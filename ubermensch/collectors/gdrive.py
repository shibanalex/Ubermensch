"""Чаты Gemini в Google AI Studio: с Google Диска, где AI Studio их хранит.

Раз в несколько часов: файлы-промпты AI Studio, изменившиеся с прошлого раза, скачиваются в
папку импорта («Gemini: <название> [id].json»). Дальше их разбирает обычный глубокий импорт
(core.deep_import): изменившийся чат; новый хеш, и в память попадает только новое.
Нужен вход Google с доступом к Диску (google_login.sh); без него сборщик молча ничего не делает.
Чаты по ссылке «всем, у кого есть ссылка» (download_shared) забираются и без входа, а ссылки
запоминаются: свежая версия каждого такого чата забирается при каждом сборе.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from .. import config, google_calendar

MIME = "application/vnd.google-makersuite.prompt"
LINKS = config.DATA_DIR / "raw" / "shared_chats.json"
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _target(import_dir: Path, item: dict) -> Path:
    title = UNSAFE.sub("_", item.get("name") or "чат").strip()[:80] or "чат"
    return import_dir / f"Gemini — {title} [{item['id'][:8]}].json"


def collect(import_dir: Path, since: str | None) -> tuple[list[str], str | None]:
    """(скачанные имена, новый курсор modifiedTime). since: RFC 3339 из прошлого раза."""
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
            continue  # не чат (или формат AI Studio поменялся): не тащим в импорт
        target = _target(import_dir, item)
        if not target.exists() or target.read_bytes() != raw:
            target.write_bytes(raw)
            saved.append(target.name)
    return saved, cursor


SHARED_ID = re.compile(r"(?:ids%22:%5B%22|ids\":\[\"|/d/|[?&]id=)([A-Za-z0-9_-]{20,})")


def file_id(url: str) -> str | None:
    """ID файла из ссылки AI Studio (…/prompts?state={"ids":["…"]}) или Google Диска."""
    found = SHARED_ID.search(url)
    return found.group(1) if found else None


def download_shared(url: str, import_dir: Path) -> str:
    """Чат AI Studio по ссылке с доступом «всем, у кого есть ссылка»: без входа Google.
    Кладёт его в папку импорта; дальше: глубокий импорт. Возвращает имя файла."""
    import urllib.request

    identifier = file_id(url)
    if identifier is None:
        raise ValueError("Не нашла в ссылке ID файла AI Studio или Диска")
    request = urllib.request.Request(
        f"https://drive.google.com/uc?export=download&id={identifier}",
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read(100 * 1024 * 1024)
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("По ссылке не чат AI Studio: нет доступа по ссылке или это другой файл") from None
    if not isinstance(data, dict) or "chunkedPrompt" not in data:
        raise ValueError("Файл по ссылке — не чат AI Studio")
    first = next((c.get("text", "") for c in data["chunkedPrompt"].get("chunks", [])
                  if c.get("role") == "user" and c.get("text")), "")
    title = " ".join(first.lstrip("#* ").split()[:6]) or "чат"
    target = _target(import_dir, {"id": identifier, "name": title})
    existing = [p for p in import_dir.glob(f"*[[]{identifier[:8]}[]].json")]
    if existing:  # тот же чат уже лежит: перезаписываем его, а не плодим копии
        target = existing[0]
    if not target.exists() or target.read_bytes() != raw:
        target.write_bytes(raw)
    links = shared_links()
    if url not in links:
        LINKS.parent.mkdir(parents=True, exist_ok=True)
        LINKS.write_text(json.dumps(links + [url], ensure_ascii=False, indent=1), encoding="utf-8")
    return target.name


def shared_links() -> list[str]:
    """Ссылки на чаты, которые пользователь дал: их свежая версия забирается регулярно."""
    try:
        return json.loads(LINKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
