"""Поиск файлов: мгновенно по индексу plocate, в конкретной папке — fd, иначе обходом папок."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .. import config

HOME = Path.home()
SEARCH_ROOTS = [HOME, *config.EXTRA_ROOTS]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages", "snap",
             ".cache", ".local", ".config", ".mozilla", ".npm", ".cargo", ".rustup", "Trash"}


def _visible(path: str) -> bool:
    """Без служебного: скрытые папки, кэши, системные каталоги."""
    parts = Path(path).parts
    return not any(p in SKIP_DIRS or (p.startswith(".") and p != ".") for p in parts[1:-1])


def _plocate(name_part: str, limit: int) -> list[str] | None:
    """None — индекса нет. Индекс обновляется раз в сутки: свежие файлы найдёт fd/обход."""
    if not shutil.which("plocate"):
        return None
    done = subprocess.run(["plocate", "-i", "-l", str(limit * 10), "--", name_part],
                          capture_output=True, timeout=30)
    if done.returncode not in (0, 1):  # 1 — ничего не найдено
        return None
    lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    roots = [str(r) for r in SEARCH_ROOTS]
    found = [line for line in lines if line.startswith(tuple(roots)) and _visible(line)
             and name_part.casefold() in Path(line).name.casefold()]
    return found[:limit]


def _fd(name_part: str, folder: str, limit: int) -> list[str] | None:
    fd = shutil.which("fdfind") or shutil.which("fd")
    if not fd:
        return None
    done = subprocess.run([fd, "--ignore-case", "--fixed-strings", "--absolute-path",
                           "--max-results", str(limit), "--", name_part, folder],
                          capture_output=True, timeout=60)
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace").splitlines()


def find(name_part: str, folder: str | None = None, limit: int = 30) -> list[str]:
    """Файлы и папки, в имени которых есть name_part (без учёта регистра)."""
    fast = _fd(name_part, folder, limit) if folder else _plocate(name_part, limit)
    if fast:
        return fast
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
