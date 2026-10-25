"""Manifest-based encrypted snapshots and offline restore verification.

python -m ubermensch.backups verify ARCHIVE
python -m ubermensch.backups restore ARCHIVE --to EMPTY_DIRECTORY
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

from . import config

DATA_PARTS = ('raw', 'agent', 'personalization', 'dossier', 'workspace')
EXCLUDED = {'secrets', 'backups', '.git', '__pycache__', '.venv', '.ssh', '.gnupg'}


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _excluded(path: Path) -> bool:
    return (path.name in EXCLUDED or path.name == '.env' or path.name.startswith('.env.')
            or '.session' in path.name or path.suffix in {'.sock', '.lock'})


def stage_snapshot(db_path: Path, destination: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    destination.mkdir(parents=True, exist_ok=False)
    db_target = destination / 'data' / 'db' / 'ubermensch.db'
    db_target.parent.mkdir(parents=True)
    with closing(sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(db_target)) as target:
        source.backup(target)
    manifest = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'roots': {}, 'files': {}, 'excluded': []}
    roots = {f'data/{part}': config.DATA_DIR / part for part in DATA_PARTS}
    roots['documents'] = config.DOCUMENTS_DIR
    if config.VAULT_DIR:
        roots['vault'] = config.VAULT_DIR
    elif config.OUTPUT_DIR != config.DOCUMENTS_DIR:
        roots['reports'] = config.OUTPUT_DIR

    def copy(source, target, relative):
        if _excluded(source) or source.is_symlink():
            manifest['excluded'].append(str(relative))
            return
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            for child in sorted(source.iterdir()):
                copy(child, target / child.name, relative / child.name)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Detect a file changing during the copy instead of blessing a torn snapshot.
            for _ in range(3):
                before = source.stat()
                shutil.copy2(source, target)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                    break
            else:
                raise RuntimeError(f'Файл изменяется во время копирования: {relative}')

    for name, root in roots.items():
        manifest['roots'][name] = {'source': str(root), 'present': root.exists()}
        if root.exists():
            copy(root, destination / name, Path(name))
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            manifest['files'][str(path.relative_to(destination))] = {
                'sha256': checksum(path), 'bytes': path.stat().st_size}
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def verify_snapshot(folder: Path) -> dict:
    folder = folder.resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1:
        raise ValueError('Неизвестная версия резервной копии')
    for name, expected in manifest['files'].items():
        path = folder / name
        if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                or folder not in path.resolve().parents):
            raise ValueError('Недопустимый путь в manifest')
        if not path.is_file() or path.stat().st_size != expected['bytes'] or checksum(path) != expected['sha256']:
            raise ValueError(f'Повреждён или отсутствует файл: {name}')
    actual = {str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'manifest.json'}:
        raise ValueError('Содержимое архива не совпадает с manifest')
    database = folder / 'data' / 'db' / 'ubermensch.db'
    # This is a standalone SQLite backup, never a live database with a WAL.
    connection = sqlite3.connect(f'{database.as_uri()}?mode=ro&immutable=1', uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity_check не пройден')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('В SQLite есть нарушенные внешние ссылки')
        # These are the minimum restorable application entities, not merely a readable DB.
        for table in ('messages', 'facts', 'tasks', 'goals', 'actions', 'reminders'):
            connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
    finally:
        connection.close()
    return manifest


def _seven_zip(args, password: str, *, cwd=None):
    if not password or '\n' in password or '\r' in password:
        raise ValueError('Пароль должен быть непустым и однострочным')
    executable = shutil.which('7z') or shutil.which('7za')
    if not executable:
        raise RuntimeError('Нужен 7-Zip')
    result = subprocess.run([executable, *args], input=(password + '\n' + password + '\n').encode(),
                            capture_output=True, cwd=cwd, timeout=600)
    if result.returncode:
        # хвост вывода 7-Zip: «No space left», «Wrong password»… — без него причину не найти
        tail = (result.stderr or result.stdout).decode('utf-8', 'replace').strip().splitlines()[-3:]
        raise RuntimeError(f'7-Zip вернул код {result.returncode}: ' + ' | '.join(tail)[:300])


def unpack_verified(archive: Path, destination: Path, password: str) -> dict:
    # рабочая папка — рядом с назначением: /tmp у нас в оперативной памяти, а снимок — гигабайты
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.unpack-') as work:
        temporary = Path(work)
        _seven_zip(['x', '-y', str(archive.resolve()), 'snapshot.tar', '-o' + work], password)
        payload = temporary / 'snapshot.tar'
        if payload.is_symlink() or not payload.is_file():
            raise ValueError('Нет snapshot.tar: старый или неподдерживаемый архив')
        destination.mkdir(parents=True, exist_ok=False)
        with tarfile.open(payload) as bundle:
            for member in bundle.getmembers():
                if not (member.isfile() or member.isdir()):
                    raise ValueError('Архив содержит ссылку или специальный файл')
            bundle.extractall(destination, filter='data')
        return verify_snapshot(destination)


def create_archive(db_path: Path, archive: Path, password: str) -> dict:
    archive = archive.resolve()
    if archive.exists():
        raise FileExistsError(archive)
    partial = archive.with_suffix('.partial.7z')
    if partial.exists():
        raise FileExistsError(partial)
    try:
        # не в /tmp: он в оперативной памяти, а снимок, tar и проверка вместе — больше 7 ГБ
        with tempfile.TemporaryDirectory(dir=archive.parent, prefix='.staging-') as work:
            temporary = Path(work)
            snapshot = temporary / 'snapshot'
            stage_snapshot(db_path, snapshot)
            verify_snapshot(snapshot)
            with tarfile.open(temporary / 'snapshot.tar', 'w') as bundle:
                for path in sorted(snapshot.iterdir()):
                    bundle.add(path, arcname=path.name)
            _seven_zip(['a', '-t7z', '-mhe=on', '-mx=5', '-p', str(partial), 'snapshot.tar'], password, cwd=temporary)
            manifest = unpack_verified(partial, temporary / 'verification', password)
        partial.replace(archive)
        return manifest
    finally:
        partial.unlink(missing_ok=True)


def restore_archive(archive: Path, destination: Path, password: str) -> dict:
    destination = destination.expanduser().absolute()
    if destination.exists():
        raise FileExistsError('Каталог восстановления должен быть новым')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        snapshot = Path(work) / 'snapshot'
        manifest = unpack_verified(archive, snapshot, password)
        snapshot.rename(destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'restore'])
    parser.add_argument('archive', type=Path)
    parser.add_argument('--to', type=Path)
    args = parser.parse_args()
    import getpass
    password = os.getenv('BACKUP_PASSWORD') or getpass.getpass('Пароль резервной копии: ')
    if args.command == 'restore':
        if not args.to:
            parser.error('restore требует --to NEW_DIRECTORY')
        manifest = restore_archive(args.archive, args.to, password)
    else:
        with tempfile.TemporaryDirectory(dir=args.archive.resolve().parent, prefix='.verify-') as work:
            manifest = unpack_verified(args.archive, Path(work) / 'snapshot', password)
    print(f"Проверено файлов: {len(manifest['files'])}. SQLite: OK.")


if __name__ == '__main__':
    main()
