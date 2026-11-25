"""Творчество: партитуры MuseScore и проекты FL Studio; над чем и когда работает пользователь.

Каждая новая правка файла: отдельное событие, так со временем видно, как идёт работа.
"""
from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

from .. import config
from . import local_iso

ROOTS = [*config.PERSONAL_DIRS, *config.EXTRA_ROOTS]
APPS = {".mscz": "MuseScore", ".mscx": "MuseScore", ".flp": "FL Studio"}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".cache", "snap",
             "lost+found", "Docker", "Libraries Kontakt", "Ubermensch", "Backups"}


def mscz_xml(archive: zipfile.ZipFile) -> str:
    """Текст партитуры (.mscx) из файла MuseScore (.mscz: это zip)."""
    name = next(n for n in archive.namelist() if n.endswith(".mscx"))
    return archive.read(name).decode("utf-8", errors="replace")


def _score_details(path: Path) -> str:
    try:
        if path.suffix.lower() == ".mscz":
            with zipfile.ZipFile(path) as archive:
                xml = mscz_xml(archive)
        else:
            xml = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, StopIteration):
        return ""
    return score_details(xml)


def score_details(xml: str) -> str:
    """Название, инструменты и примерная длина партитуры MuseScore."""
    title = re.search(r'<metaTag name="workTitle">([^<]*)</metaTag>', xml)
    instruments = list(dict.fromkeys(re.findall(r"<trackName>([^<]+)</trackName>", xml)))
    staves = max(1, len(re.findall(r"<Staff id=", xml)) // 2)  # описание + содержимое
    measures = len(re.findall(r"<Measure[ >]", xml)) // staves
    parts = []
    if title and title.group(1).strip():
        parts.append(f"«{title.group(1).strip()}»")
    if instruments:
        parts.append("инструменты: " + ", ".join(instruments[:20]))
    if measures:
        parts.append(f"~{measures} тактов")
    return "; ".join(parts)


def collect() -> list[dict]:
    events = []
    for root in ROOTS:
        if not root.exists():
            continue
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                app = APPS.get(Path(name).suffix.lower())
                if not app:
                    continue
                path = Path(folder) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                details = _score_details(path) if app == "MuseScore" else ""
                created = datetime.fromtimestamp(stat.st_ctime)
                events.append({
                    "source": "creative",
                    "external_id": f"{path}|{int(stat.st_mtime)}",
                    "ts": local_iso(datetime.fromtimestamp(stat.st_mtime).astimezone()),
                    "thread": app,
                    "text": f"{app}: {path.name}" + (f" — {details}" if details else "")
                            + f" (создан {created:%d.%m.%Y}, {stat.st_size // 1024} КБ)",
                    "meta": {"path": str(path)},
                })
    return events
