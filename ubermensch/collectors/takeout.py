"""Google Takeout: история YouTube, поиска Google и других действий в сервисах Google.

Архивы (takeout-*.zip, хоть по частям) кладутся в папку импорта. Читаем из них только
«Мои действия» и историю YouTube — Диск, Фото и прочее не трогаем. Понимаем оба формата
выгрузки: JSON и HTML (по умолчанию Google отдаёт HTML), русские и английские названия.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from . import local_iso

RU_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
             "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
EN_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
RU_DATE = re.compile(r"(\d{1,2})\s+([а-я]{3})[а-я]*\.?\s+(\d{4})\s*г\.,\s*(\d{1,2}):(\d{2}):(\d{2})")
EN_DATE = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)")
CELL = re.compile(r'class="outer-cell')
HEADER = re.compile(r'mdl-typography--title">(.*?)<br', re.S)
CONTENT = re.compile(r'mdl-cell--6-col mdl-typography--body-1">(.*?)</div>', re.S)


def _relevant(name: str) -> bool:
    lower = name.lower().replace("\\", "/")
    if not lower.endswith((".json", ".html")):
        return False
    return ("my activity" in lower or "мои действия" in lower
            or ("youtube" in lower and ("history" in lower or "истори" in lower)))


def _parse_date(text: str) -> datetime | None:
    if match := RU_DATE.search(text):
        day, month, year, hh, mm, ss = match.groups()
        if month in RU_MONTHS:
            return datetime(int(year), RU_MONTHS[month], int(day), int(hh), int(mm), int(ss))
    if match := EN_DATE.search(text):
        month, day, year, hh, mm, ss, half = match.groups()
        hour = int(hh) % 12 + (12 if half == "PM" else 0)
        return datetime(int(year), EN_MONTHS[month], int(day), hour, int(mm), int(ss))
    return None


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _items_html(text: str) -> Iterator[tuple[str, str, str, datetime]]:
    """(сервис, действие, дополнение — канал и т. п., время) из HTML-страницы «Мои действия»."""
    for cell in CELL.split(text)[1:]:
        header, content = HEADER.search(cell), CONTENT.search(cell)
        if not header or not content:
            continue
        lines = [_clean(part) for part in re.split(r"<br\s*/?>", content.group(1))]
        lines = [line for line in lines if line]
        if not lines:
            continue
        moment = _parse_date(lines[-1])
        if moment is None:
            continue
        extra = " · ".join(lines[1:-1])
        yield _clean(header.group(1)), lines[0], extra, moment


def _items_json(data: list) -> Iterator[tuple[str, str, str, datetime]]:
    for item in data:
        if not isinstance(item, dict) or "time" not in item or "title" not in item:
            continue
        try:
            moment = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        except ValueError:
            continue
        extra = " · ".join(s.get("name", "") for s in item.get("subtitles", []) if s.get("name"))
        yield item.get("header", "Google"), item["title"], extra, moment


def _source(header: str) -> str:
    if header.startswith("YouTube"):
        return "youtube"
    if header in ("Поиск", "Search", "Google Поиск", "Поиск картинок", "Image Search"):
        return "google_search"
    return "google_activity"


def _events(name: str, raw: bytes, since: datetime) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    if name.lower().endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return []
        items = _items_json(data) if isinstance(data, list) else iter(())
    else:
        items = _items_html(text)
    events = []
    for header, action, extra, moment in items:
        ts = local_iso(moment) if moment.tzinfo else moment.isoformat(timespec="seconds")
        if ts < since.isoformat():
            continue
        key = hashlib.sha1(f"{header}|{action}|{ts}".encode()).hexdigest()
        events.append({
            "source": _source(header),
            "external_id": key,
            "ts": ts,
            "thread": header,
            "text": action + (f" — {extra}" if extra else ""),
            "meta": {},
        })
    return events


def archives(import_dir: Path) -> list[Path]:
    return sorted(import_dir.rglob("*.zip"))


def archive_key(path: Path) -> str:
    stat = path.stat()
    return f"takeout:{path.name}:{stat.st_size}"


def collect(paths: list[Path], since: datetime) -> list[dict]:
    """События из выбранных архивов Takeout (и из уже распакованных папок Takeout)."""
    events: list[dict] = []
    for path in paths:
        if path.is_dir():
            for file in path.rglob("*"):
                if file.is_file() and _relevant(str(file)):
                    events += _events(file.name, file.read_bytes(), since)
            continue
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir() and _relevant(info.filename):
                    events += _events(info.filename, archive.read(info), since)
    return events


def unpacked(import_dir: Path) -> list[Path]:
    """Если архив распаковали сами — папки Takeout тоже подходят."""
    return [p for p in import_dir.rglob("Takeout") if p.is_dir()] if os.path.isdir(import_dir) else []
