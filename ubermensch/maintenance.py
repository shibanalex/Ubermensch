"""Обслуживание: ночные резервные копии и проверка места на диске.

Бэкап — зашифрованный архив 7-Zip (AES-256, имена файлов тоже скрыты) с базой (память, цели,
задачи, история, собранные данные), стилем переписки и досье. Пароль — BACKUP_PASSWORD в .env;
без него архив не открыть, поэтому хранить его нужно отдельно от ноутбука.

Архивы лежат в D:\\Ubermensch\\backups (последние 7) и копируются на внешний диск или флешку,
если в корне носителя есть папка AnyaBackup (последние 14). Сессия Telegram и ключи из .env
в архив не входят: их проще пересоздать, чем рисковать утечкой.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import string
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .health import Health

log = logging.getLogger(__name__)

SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
LOCAL_DIR = config.DATA_DIR / "backups"
MARKER = "AnyaBackup"  # папка в корне внешнего носителя: сюда копировать архивы
MARKERS = (MARKER, "OswaldBackup")  # старое имя тоже понимаем
KEEP_LOCAL, KEEP_EXTERNAL = 7, 14
CHECK_EVERY = 10 * 60
NIGHT = range(2, 6)  # бэкап ночью, когда компьютер свободен…
OVERDUE = timedelta(hours=36)  # …но если ночью был выключен — днём
EXTERNAL_STALE = timedelta(days=7)
MIN_FREE_GB = 5


def _archives(folder: Path) -> list[Path]:
    return sorted([*folder.glob("anya-*.7z"), *folder.glob("oswald-*.7z")],
                  key=lambda p: p.name.split("-", 1)[1])  # по дате, независимо от имени


def _external_folders() -> list[Path]:
    """Подключённые носители с папкой AnyaBackup (кроме системного C: и D: с данными)."""
    folders = []
    for letter in string.ascii_uppercase:
        if letter in "CD":
            continue
        folder = next((Path(f"{letter}:/{m}") for m in MARKERS
                       if Path(f"{letter}:/{m}").is_dir()), Path(f"{letter}:/{MARKER}"))
        try:
            if folder.is_dir():
                folders.append(folder)
        except OSError:
            continue
    return folders


class Maintenance:
    def __init__(self, db_path: Path, health: Health):
        self.db_path = db_path
        self.health = health

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
            self.health.report("disk", f"На диске {config.DATA_DIR.drive} осталось {free_gb:.1f} ГБ. "
                               "Освободи место, иначе сбор данных и бэкапы остановятся.")
        else:
            self.health.clear("disk", "Место на диске снова есть.")

    def _backup_due(self) -> bool:
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
        if not SEVEN_ZIP.exists():
            self.health.report("backup", "Резервные копии не делаются: нужен 7-Zip "
                               r"(C:\Program Files\7-Zip).")
            return None
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        archive = LOCAL_DIR / f"anya-{datetime.now():%Y-%m-%d_%H-%M}.7z"
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "ubermensch.db"
            source = sqlite3.connect(self.db_path)
            target = sqlite3.connect(snapshot)
            source.backup(target)  # целостная копия, даже пока ядро пишет в базу
            source.close()
            target.close()
            parts = [str(snapshot)]
            workspace = config.DATA_DIR / "workspace"
            for extra in (workspace / "tg_style.md", workspace / "anya_persona.md",
                          config.DATA_DIR / "dossier"):
                if extra.exists():
                    parts.append(str(extra))
            done = subprocess.run(
                [str(SEVEN_ZIP), "a", "-t7z", "-mhe=on", "-mx=5", f"-p{password}", str(archive),
                 *parts], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if done.returncode != 0:
            archive.unlink(missing_ok=True)
            self.health.report("backup", "Резервная копия не создалась: 7-Zip вернул ошибку "
                               f"{done.returncode}. Подробности в логе.")
            log.error("7-Zip: %s", done.stderr.decode("cp866", errors="replace")[:2000])
            return None
        for old in _archives(LOCAL_DIR)[:-KEEP_LOCAL]:
            old.unlink(missing_ok=True)
        self.health.clear("backup", "Резервные копии снова делаются.")
        log.info("Резервная копия: %s (%.1f МБ)", archive.name, archive.stat().st_size / 1024 ** 2)
        return archive

    def _copy_external(self) -> None:
        latest = _archives(LOCAL_DIR)[-1:] if LOCAL_DIR.exists() else []
        for folder in _external_folders():
            if latest and not (folder / latest[0].name).exists():
                shutil.copy2(latest[0], folder / latest[0].name)
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
