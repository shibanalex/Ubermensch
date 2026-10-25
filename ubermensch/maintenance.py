"""Обслуживание: ночные резервные копии и проверка места на диске.

Бэкап — зашифрованный архив 7-Zip с manifest, SQLite snapshot, данными агента, raw,
профилем, workspace, Obsidian и документами Ани. Проверяется пробной распаковкой.
Пароль — BACKUP_PASSWORD в .env; хранить отдельно от ноутбука.

Архивы лежат в <данные>/backups (последние 7) и копируются на внешний диск или флешку,
если в корне носителя есть папка AnyaBackup (последние 14). Сессия Telegram и ключи из .env
в архив не входят: их проще пересоздать, чем рисковать утечкой.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .health import Health
from .backups import create_archive

log = logging.getLogger(__name__)

SEVEN_ZIP = shutil.which("7z") or shutil.which("7za")
# BACKUP_DIR — куда класть локальные архивы (по умолчанию папка данных; архив — гигабайты)
LOCAL_DIR = Path(os.getenv("BACKUP_DIR", "") or config.DATA_DIR / "backups").expanduser()
MARKER = "AnyaBackup"  # папка в корне внешнего носителя: сюда копировать архивы
MARKERS = (MARKER, "OswaldBackup")  # старое имя тоже понимаем
KEEP_LOCAL, KEEP_EXTERNAL = 7, 14
CHECK_EVERY = 10 * 60
RETRY_AFTER_FAILURE = timedelta(hours=6)  # сбой бэкапа не повторять каждые 10 минут
NIGHT = range(2, 6)  # бэкап ночью, когда компьютер свободен…
OVERDUE = timedelta(hours=36)  # …но если ночью был выключен — днём
EXTERNAL_STALE = timedelta(days=7)
MIN_FREE_GB = 5


def _archives(folder: Path) -> list[Path]:
    return sorted([*folder.glob("anya-*.7z"), *folder.glob("oswald-*.7z")],
                  key=lambda p: p.name.split("-", 1)[1])  # по дате, независимо от имени


def _mount_points() -> list[Path]:
    """Подключённые носители: KDE монтирует их в /media/<пользователь>, иногда в /run/media."""
    user = os.environ.get("USER", "")
    points = []
    for base in (Path("/media") / user, Path("/run/media") / user, Path("/mnt")):
        try:
            points += [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            continue
    return points


def _external_folders() -> list[Path]:
    """Подключённые носители с папкой AnyaBackup в корне (кроме диска с самими данными)."""
    folders = []
    for point in _mount_points():
        try:
            if point.stat().st_dev == config.DATA_DIR.stat().st_dev:
                continue
            folder = next((point / m for m in MARKERS if (point / m).is_dir()), None)
        except OSError:
            continue
        if folder:
            folders.append(folder)
    return folders


class Maintenance:
    def __init__(self, db_path: Path, health: Health):
        self.db_path = db_path
        self.health = health
        self._failed_at: datetime | None = None

    async def run(self) -> None:
        while True:
            self._check_disk()
            if self._backup_due():
                await asyncio.to_thread(self.backup)
            await asyncio.to_thread(self._copy_external)
            await asyncio.sleep(CHECK_EVERY)

    def _check_disk(self) -> None:
        free_gb = shutil.disk_usage(config.DATA_DIR).free / 1024 ** 3
        if free_gb < MIN_FREE_GB:
            self.health.report("disk", f"На диске с данными осталось {free_gb:.1f} ГБ. "
                               "Освободи место, иначе сбор данных и бэкапы остановятся.")
        else:
            self.health.clear("disk", "Место на диске снова есть.")

    def _backup_due(self) -> bool:
        if self._failed_at and datetime.now() - self._failed_at < RETRY_AFTER_FAILURE:
            return False
        archives = _archives(LOCAL_DIR)
        if not archives:
            return True
        age = datetime.now() - datetime.fromtimestamp(archives[-1].stat().st_mtime)
        return age > OVERDUE or (age > timedelta(hours=20) and datetime.now().hour in NIGHT)

    def backup(self) -> Path | None:
        password = os.getenv("BACKUP_PASSWORD")
        if not password:
            self.health.report("backup", "Резервные копии не делаются: задай пароль BACKUP_PASSWORD "
                               "в .env и запиши его там, где он переживёт ноутбук.")
            return None
        if not SEVEN_ZIP:
            self.health.report("backup", "Резервные копии не делаются: нужен 7-Zip "
                               "(sudo apt install 7zip).")
            return None
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        archive = LOCAL_DIR / f"anya-{datetime.now():%Y-%m-%d_%H-%M-%S}.7z"
        try:
            manifest = create_archive(self.db_path, archive, password)
        except Exception as error:
            self._failed_at = datetime.now()
            self.health.report("backup", "Резервная копия не прошла создание или восстановление: "
                               f"{type(error).__name__}: {str(error)[:200]}. Повторю через 6 часов.")
            log.warning("Backup failed: %s: %s", type(error).__name__, error)
            return None
        self._failed_at = None
        log.info("Восстановление проверено: %d файлов", len(manifest['files']))
        for old in _archives(LOCAL_DIR)[:-KEEP_LOCAL]:
            old.unlink(missing_ok=True)
        self.health.clear("backup", "Резервные копии снова делаются.")
        log.info("Резервная копия: %s (%.1f МБ)", archive.name, archive.stat().st_size / 1024 ** 2)
        return archive

    def _copy_external(self) -> None:
        latest = _archives(LOCAL_DIR)[-1:] if LOCAL_DIR.exists() else []
        for folder in _external_folders():
            if latest and not (folder / latest[0].name).exists():
                target = folder / latest[0].name
                temporary = target.with_suffix('.partial')
                shutil.copy2(latest[0], temporary)
                from .backups import checksum
                if checksum(temporary) != checksum(latest[0]):
                    temporary.unlink(missing_ok=True)
                    raise OSError('Внешняя копия не совпадает с архивом')
                temporary.replace(target)
                log.info("Копия на внешний носитель: %s", folder)
            for old in _archives(folder)[:-KEEP_EXTERNAL]:
                old.unlink(missing_ok=True)
            self.health.clear("backup_external", "Копия на внешний диск сделана.")
        # давно не было копии вне ноутбука — напомнить подключить носитель
        newest_external = max((a.stat().st_mtime for f in _external_folders()
                               for a in _archives(f)), default=None)
        stamp = config.DATA_DIR / "backups" / ".last_external"
        if newest_external:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(newest_external))
        last = float(stamp.read_text()) if stamp.exists() else None
        if latest and (last is None or
                       datetime.now() - datetime.fromtimestamp(last) > EXTERNAL_STALE):
            self.health.report("backup_external", "Давно не было копии вне ноутбука: подключи "
                               f"внешний диск или флешку с папкой {MARKER} в корне.")
