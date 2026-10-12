"""Поиск файлов в личных папках пользователя."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
SEARCH_ROOTS = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads", Path("D:/")]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "$RECYCLE.BIN",
             "System Volume Information", "Docker", "WSL", "Libraries Kontakt", "AppData"}


def find(name_part: str, folder: str | None = None, limit: int = 30) -> list[str]:
    """Файлы и папки, в имени которых есть name_part (без учёта регистра)."""
    needle = name_part.casefold()
    found: list[str] = []
    for root in ([Path(folder)] if folder else SEARCH_ROOTS):
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in dirs + files:
                if needle in name.casefold():
                    found.append(str(Path(current) / name))
                    if len(found) >= limit:
                        return found
    return found
