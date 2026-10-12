"""Поиск файлов: мгновенно через Everything (es.exe), иначе — обходом личных папок."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config

HOME = Path.home()
SEARCH_ROOTS = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads", Path("D:/")]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "$RECYCLE.BIN",
             "System Volume Information", "Docker", "WSL", "Libraries Kontakt", "AppData"}
ES = config.DATA_DIR / "bin" / "es.exe"  # консольный клиент Everything (voidtools)


def _everything(name_part: str, folder: str | None, limit: int) -> list[str] | None:
    """None — Everything недоступен (не установлен или не запущен)."""
    es = str(ES) if ES.exists() else shutil.which("es")
    if not es:
        return None
    # в консоль es пишет в старой кодировке (Ü → U, кириллица теряется) — берём UTF-8 из файла
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "found.txt"
        args = [es, "-n", str(limit), "-export-txt", str(out), "-utf8-bom"] \
            + (["-path", folder] if folder else []) + [name_part]
        done = subprocess.run(args, capture_output=True, timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if done.returncode != 0 or not out.exists():
            return None
        lines = out.read_text(encoding="utf-8-sig").splitlines()
    return [line for line in lines if line.strip() and "\\AppData\\" not in line]


def find(name_part: str, folder: str | None = None, limit: int = 30) -> list[str]:
    """Файлы и папки, в имени которых есть name_part (без учёта регистра)."""
    if (found := _everything(name_part, folder, limit)) is not None:
        return found
    needle = name_part.casefold()
    found = []
    for root in ([Path(folder)] if folder else SEARCH_ROOTS):
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in dirs + files:
                if needle in name.casefold():
                    found.append(str(Path(current) / name))
                    if len(found) >= limit:
                        return found
    return found
