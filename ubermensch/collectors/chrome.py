"""История Chrome: страницы (одна запись на страницу в день) и поисковые запросы."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import local_iso

USER_DATA = Path.home() / ".config" / "google-chrome"
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
SKIP_HOSTS = {"localhost", "127.0.0.1", "newtab", ""}
# поисковик → параметр с запросом
SEARCH_PARAMS = {"google.": "q", "yandex.": "text", "ya.ru": "text", "bing.com": "q",
                 "duckduckgo.com": "q", "youtube.com": "search_query", "perplexity.ai": "q"}


def _chrome_time(microseconds: int) -> datetime:
    return CHROME_EPOCH + timedelta(microseconds=microseconds)


def _search_query(host: str, url: str) -> str | None:
    for engine, param in SEARCH_PARAMS.items():
        if engine in host:
            values = parse_qs(urlparse(url).query).get(param)
            return values[0].strip() if values and values[0].strip() else None
    return None


def collect(since: datetime, cursor: str | None) -> tuple[list[dict], str | None]:
    """Возвращает (события, новый курсор). Курсор — последний обработанный visit_time."""
    start_us = int(cursor) if cursor else int((since.astimezone(timezone.utc) - CHROME_EPOCH)
                                              / timedelta(microseconds=1))
    events: list[dict] = []
    last_us = start_us
    for history in USER_DATA.glob("*/History"):
        profile = history.parent.name
        with tempfile.TemporaryDirectory() as tmp:
            # Chrome держит файл открытым — читаем копию
            copy = Path(tmp) / "History"
            shutil.copy2(history, copy)
            db = sqlite3.connect(copy)
            rows = db.execute(
                "SELECT v.id, u.id, u.url, u.title, v.visit_time FROM visits v "
                "JOIN urls u ON u.id = v.url WHERE v.visit_time > ? ORDER BY v.visit_time",
                (start_us,),
            ).fetchall()
            db.close()

        pages: dict[tuple[str, int], dict] = {}
        visits: defaultdict[tuple[str, int], int] = defaultdict(int)
        for visit_id, url_id, url, title, visit_time in rows:
            last_us = max(last_us, visit_time)
            parsed = urlparse(url)
            host = (parsed.hostname or "").removeprefix("www.")
            if parsed.scheme not in ("http", "https") or host in SKIP_HOSTS:
                continue
            ts = local_iso(_chrome_time(visit_time))
            if query := _search_query(host, url):
                events.append({"source": "chrome", "external_id": f"{profile}:q:{visit_id}",
                               "ts": ts, "thread": "поиск", "text": query,
                               "meta": {"engine": host}})
                continue
            key = (ts[:10], url_id)
            visits[key] += 1
            if key not in pages:
                pages[key] = {"source": "chrome", "external_id": f"{profile}:{ts[:10]}:{url_id}",
                              "ts": ts, "thread": host, "text": (title or url)[:300],
                              "meta": {"url": url[:500]}}
        for key, event in pages.items():
            event["meta"]["visits"] = visits[key]
            events.append(event)
    return events, str(last_us)
