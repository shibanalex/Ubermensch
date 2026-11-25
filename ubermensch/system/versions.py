"""Прежние версии файлов перед изменением инструментами: чтобы любую правку можно было откатить.

Копия кладётся в workspace/versions/<id>/ до записи; журнал: versions.jsonl. Храним
MAX_VERSIONS последних и не старше KEEP_DAYS. Файлы больше MAX_BYTES не копируются
(запись идёт, но отката нет: об этом сказано в ответе инструмента).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .. import config

MAX_VERSIONS = 500
KEEP_DAYS = 30
MAX_BYTES = 20 * 1024 * 1024
_lock = threading.Lock()


def root() -> Path:
    return config.DATA_DIR / "workspace" / "versions"


def _index() -> Path:
    return root() / "versions.jsonl"


def _entries() -> list[dict]:
    try:
        lines = _index().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def _write(entries: list[dict]) -> None:
    _index().parent.mkdir(parents=True, exist_ok=True)
    tmp = _index().with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    tmp.replace(_index())


def save(path: Path, operation: str) -> str | None:
    """Сохранить текущий файл перед изменением. None: файла ещё нет или он слишком большой."""
    path = path.resolve()
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return None
    identifier = uuid4().hex[:12]
    folder = root() / identifier
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, folder / path.name)
    entry = {"id": identifier, "path": str(path), "operation": operation, "size": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _lock:
        entries = _entries() + [entry]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
        keep = [e for e in entries if e["saved_at"] >= cutoff][-MAX_VERSIONS:]
        for old in entries:
            if old not in keep:
                shutil.rmtree(root() / old["id"], ignore_errors=True)
        _write(keep)
    return identifier


def history(path: str | None = None, limit: int = 20) -> list[dict]:
    wanted = str(Path(path).expanduser().resolve()) if path else None
    return [e for e in reversed(_entries()) if wanted is None or e["path"] == wanted][:limit]


def restore(identifier: str) -> dict:
    """Вернуть версию на место. Текущее содержимое тоже сохраняется версией: откат откатываем."""
    entry = next((e for e in _entries() if e["id"] == identifier), None)
    if entry is None:
        raise ValueError("Версия не найдена (или уже удалена по сроку хранения)")
    source = root() / identifier / Path(entry["path"]).name
    if not source.is_file():
        raise ValueError("Файл версии пропал из хранилища")
    target = Path(entry["path"])
    current = save(target, f"перед откатом к {identifier}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"restored": str(target), "from_version": identifier, "previous_saved_as": current}
