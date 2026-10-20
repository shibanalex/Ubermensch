"""Чаты Gemini в Google AI Studio — с Google Диска, где AI Studio их хранит.

Раз в несколько часов: файлы-промпты AI Studio, изменившиеся с прошлого раза, скачиваются в
папку импорта («Gemini — <название> [id].json»). Дальше их разбирает обычный глубокий импорт
(core.deep_import): изменившийся чат — новый хеш, и в память попадает только новое.
Нужен вход Google с доступом к Диску (google_login.sh); без него сборщик молча ничего не делает.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from .. import google_calendar

MIME = "application/vnd.google-makersuite.prompt"
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _target(import_dir: Path, item: dict) -> Path:
    title = UNSAFE.sub("_", item.get("name") or "чат").strip()[:80] or "чат"
    return import_dir / f"Gemini — {title} [{item['id'][:8]}].json"


def collect(import_dir: Path, since: str | None) -> tuple[list[str], str | None]:
    """(скачанные имена, новый курсор modifiedTime). since — RFC 3339 из прошлого раза."""
    from googleapiclient.http import MediaIoBaseDownload

    drive = google_calendar.service("drive", "v3")
    query = f"mimeType='{MIME}' and trashed=false" + (f" and modifiedTime > '{since}'" if since else "")
    files, token = [], None
    while True:
        page = drive.files().list(q=query, fields="nextPageToken, files(id, name, modifiedTime)",
                                  orderBy="modifiedTime", pageSize=100, pageToken=token).execute()
        files += page.get("files", [])
        token = page.get("nextPageToken")
        if not token:
            break
    saved, cursor = [], since
    for item in files:
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, drive.files().get_media(fileId=item["id"]))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw = buffer.getvalue()
        cursor = max(cursor or "", item["modifiedTime"])  # и битый файл больше не качать
        try:
            json.loads(raw)
        except ValueError:
            continue  # не чат (или формат AI Studio поменялся) — не тащим в импорт
        target = _target(import_dir, item)
        if not target.exists() or target.read_bytes() != raw:
            target.write_bytes(raw)
            saved.append(target.name)
    return saved, cursor
