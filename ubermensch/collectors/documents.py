"""Документы пользователя: docx, pdf, txt, md из личных папок.

Папки проектов (где есть .git, package.json, pyproject.toml) пропускаются целиком:
там README и код, а не жизнь пользователя.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote  # имена скачанных файлов бывают в %D0%90-кодировке

from . import local_iso

HOME = Path.home()
ROOTS = [Path(r"D:\доки"), HOME / "Documents", HOME / "Downloads", HOME / "Desktop"]
EXTENSIONS = {".docx", ".pdf", ".txt", ".md"}
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "site-packages", "AppData", "dist",
             "build", ".idea", ".vscode", "Übermensch_Project", "Messenger", "gallery-admin"}
PROJECT_MARKERS = {".git", "package.json", "pyproject.toml", "requirements.txt"}
MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 30_000
PDF_PAGES = 30


def _read(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        import docx
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            parts += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
        return "\n".join(parts)
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:PDF_PAGES])
    raw = path.read_bytes()
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def collect() -> tuple[list[dict], int]:
    """Возвращает (события, число файлов, которые не удалось прочитать)."""
    events, failed = [], 0
    for root in ROOTS:
        if not root.exists():
            continue
        for folder, dirs, files in os.walk(root):
            if PROJECT_MARKERS & (set(dirs) | set(files)):
                dirs.clear()  # папка проекта — не заходим ни в неё, ни глубже
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                path = Path(folder) / name
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                    if stat.st_size > MAX_BYTES:
                        continue
                    text = _read(path).strip()
                except Exception:
                    failed += 1
                    continue
                if len(text) < 50:
                    continue
                modified = local_iso(datetime.fromtimestamp(stat.st_mtime).astimezone())
                events.append({
                    "source": "document",
                    "external_id": f"{path}|{int(stat.st_mtime)}",
                    "ts": modified,
                    "thread": str(path.parent),
                    "text": f"Файл: {unquote(path.name)}\n{text[:MAX_CHARS]}",
                    "meta": {"path": str(path), "chars": len(text)},
                })
    return events, failed
