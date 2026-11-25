"""Google Takeout: история YouTube, поиска Google и других действий в сервисах Google.

Архивы (takeout-*.zip, хоть по частям) кладутся в папку импорта. Из них берём:
- «Мои действия» и историю YouTube: события по времени;
- свои комментарии и сообщения в чатах трансляций YouTube: его собственные слова;
- партитуры MuseScore с Диска: что он сочиняет (как сборщик творчества);
- чаты Google AI Studio с Диска и подписки/плейлисты YouTube: в папку импорта как документы:
  их разберёт импорт (чаты: глубоко).
Остальное (видео, музыка, картинки, книги, Фото) не трогаем. Понимаем оба формата выгрузки:
JSON и HTML (по умолчанию Google отдаёт HTML), русские и английские названия.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from . import creative, local_iso

UNSAFE_NAME = re.compile(r'[<>:"/\\|?*]')  # нельзя в имени файла Windows
YOUTUBE_WORDS = {"комментарии": "YouTube: мои комментарии", "comments": "YouTube: мои комментарии",
                 "live chats": "YouTube: мои сообщения в чатах трансляций"}

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
    """(сервис, действие, дополнение: канал и т. п., время) из HTML-страницы «Мои действия»."""
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


def _youtube_thread(name: str) -> str | None:
    lower = name.lower().replace("\\", "/")
    if "youtube" not in lower or not lower.endswith(".csv"):
        return None
    folder = lower.rsplit("/", 2)[-2] if lower.count("/") >= 1 else ""
    return YOUTUBE_WORDS.get(folder)


def _comment_text(cell: str) -> str:
    """Текст комментария в выгрузке: JSON-фрагменты {"text": ...} через запятую."""
    try:
        parts = json.loads(f"[{cell}]")
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    except ValueError:
        return cell.strip()


def _youtube_words(thread: str, raw: bytes, since: datetime) -> list[dict]:
    """Свои комментарии и сообщения в чатах трансляций: его слова, как исходящие сообщения."""
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
    if len(rows) < 2:
        return []
    header = [h.lower() for h in rows[0]]
    col = lambda *words: next((i for i, h in enumerate(header)  # noqa: E731
                               if any(w in h for w in words)), None)
    ts_col, text_col = col("дата", "временная метка", "timestamp", "created"), col("текст", "text")
    id_col, video_col = col("идентификатор комментария", "идентификатор сообщения", "id"), \
        col("видео", "video")
    if ts_col is None or text_col is None:
        return []
    events = []
    for row in rows[1:]:
        if len(row) <= max(ts_col, text_col):
            continue
        try:
            moment = datetime.fromisoformat(row[ts_col].replace("Z", "+00:00"))
        except ValueError:
            continue
        text = _comment_text(row[text_col])
        ts = local_iso(moment)
        if not text or ts < since.isoformat():
            continue
        video = row[video_col] if video_col is not None and video_col < len(row) else ""
        events.append({
            "source": "youtube", "outgoing": 1, "thread": thread, "ts": ts,
            "external_id": row[id_col] if id_col is not None else hashlib.sha1(
                f"{thread}|{ts}|{text}".encode()).hexdigest(),
            "text": text, "meta": {"video": video} if video else {},
        })
    return events


def _drive_score(info: zipfile.ZipInfo, raw: bytes) -> dict | None:
    """Партитура MuseScore с Google Диска: событие творчества на дату последней правки."""
    name = info.filename.replace("\\", "/")
    if not name.lower().endswith(".mscz") or "/.mscbackup/" in name or "/Корзина/" in name:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as score:
            details = creative.score_details(creative.mscz_xml(score))
    except (zipfile.BadZipFile, StopIteration):
        details = ""
    moment = datetime(*info.date_time)
    file = name.rsplit("/", 1)[-1]
    return {
        "source": "creative", "thread": "MuseScore",
        "external_id": f"drive|{name}|{info.date_time}",
        "ts": moment.isoformat(timespec="seconds"),
        "text": f"MuseScore (Google Диск): {file}" + (f" — {details}" if details else "")
                + f" ({info.file_size // 1024} КБ)",
        "meta": {"path": name},
    }


def _entries(path: Path) -> Iterator[tuple[zipfile.ZipInfo | None, str, bytes]]:
    """(сведения о файле в архиве или None, имя, содержимое): из архива или папки Takeout."""
    if path.is_dir():
        for file in path.rglob("*"):
            if file.is_file():
                yield None, str(file), file.read_bytes()
        return
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                yield info, info.filename, archive.read(info)


def _wanted(name: str) -> bool:
    lower = name.lower()
    return (_relevant(name) or _youtube_thread(name) is not None
            or (lower.endswith(".mscz") and ("/диск/" in lower or "/drive/" in lower)))


def collect(paths: list[Path], since: datetime) -> list[dict]:
    """События из выбранных архивов Takeout (и из уже распакованных папок Takeout)."""
    events: list[dict] = []
    for path in paths:
        for info, name, raw in _filtered(path, _wanted):
            if _relevant(name):
                events += _events(name, raw, since)
            elif thread := _youtube_thread(name):
                events += _youtube_words(thread, raw, since)
            elif info is not None and (score := _drive_score(info, raw)):
                events.append(score)
    return events


def _filtered(path: Path, wanted) -> Iterator[tuple[zipfile.ZipInfo | None, str, bytes]]:
    """Только нужные файлы: гигабайтные видео и музыку из архива не читаем."""
    if path.is_dir():
        for info, name, raw in _entries(path):
            if wanted(name):
                yield info, name, raw
        return
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.is_dir() and wanted(info.filename):
                yield info, info.filename, archive.read(info)


def _subscriptions(name: str) -> bool:
    lower = name.lower()
    return "youtube" in lower and lower.endswith(".csv") and (
        "подписки" in lower or "subscriptions" in lower or "плейлисты.csv" in lower
        or "playlists.csv" in lower)


def _ai_studio(name: str) -> bool:
    lower = name.lower().replace("\\", "/")
    return "/google ai studio/" in lower and lower.rsplit(".", 1)[-1] not in (
        "png", "jpg", "jpeg", "webp", "gif", "pdf", "mp3", "mp4", "wav", "docx", "xlsx", "pptx",
        "py", "html", "zip")


def extract_documents(paths: list[Path], import_dir: Path) -> list[str]:
    """Чаты AI Studio с Диска и подписки/плейлисты YouTube: в папку импорта как документы
    (импорт сам разберёт их, чаты: глубоко). Возвращает имена созданных файлов."""
    created, seen = [], set()
    channels: list[str] = []
    playlists: list[str] = []
    for path in paths:
        for _, name, raw in _filtered(path, lambda n: _ai_studio(n) or _subscriptions(n)):
            if _subscriptions(name):
                rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
                if "плейлист" in name.lower() or "playlist" in name.lower():
                    playlists += [r[2] for r in rows[1:] if len(r) > 2 and r[2]]
                else:
                    channels += [r[2] for r in rows[1:] if len(r) > 2 and r[2]]
                continue
            try:
                data = json.loads(raw.decode("utf-8", errors="replace").lstrip("\ufeff"))
            except ValueError:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if not isinstance(data, dict) or "chunkedPrompt" not in data or digest in seen:
                continue
            seen.add(digest)
            stem = re.sub(r"\.(txt|json)$", "", name.replace("\\", "/").rsplit("/", 1)[-1])
            target = import_dir / f"AI Studio — {UNSAFE_NAME.sub('_', stem)}"
            if not target.exists():
                target.write_bytes(raw)
                created.append(target.name)
    if channels or playlists:
        target = import_dir / "YouTube — подписки и плейлисты.txt"
        text = ("Подписки пользователя на YouTube-каналы:\n" + "\n".join(f"- {c}" for c in channels)
                + "\n\nЕго плейлисты на YouTube:\n" + "\n".join(f"- {p}" for p in playlists))
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
            created.append(target.name)
    return created


def unpacked(import_dir: Path) -> list[Path]:
    """Если архив распаковали сами: папки Takeout тоже подходят."""
    return [p for p in import_dir.rglob("Takeout") if p.is_dir()] if os.path.isdir(import_dir) else []
