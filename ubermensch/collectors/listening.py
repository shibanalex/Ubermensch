"""Что пользователь слушает: треки из любых плееров (Spotify, браузер, VLC…), которые ядро
замечает по MPRIS (system/session.py) и пишет журналом по дням. Видео YouTube в браузере
сюда тоже попадает: как и в историю просмотров, но только то, что реально играло."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .. import config
from . import local_iso

FOLDER = config.DATA_DIR / "raw" / "listening"
FRESH_DAYS = 3
KEEP_DAYS = 60


def collect() -> list[dict]:
    if not FOLDER.is_dir():
        return []
    today, events = date.today(), []
    for journal in sorted(FOLDER.glob("*.jsonl")):
        try:
            day = date.fromisoformat(journal.stem)
        except ValueError:
            continue
        if today - day > timedelta(days=KEEP_DAYS):
            journal.unlink(missing_ok=True)
            continue
        if today - day > timedelta(days=FRESH_DAYS):
            continue
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
                moment = datetime.fromisoformat(item["ts"])
            except (ValueError, KeyError):
                continue
            track = f"{item.get('artist')} — {item['title']}" if item.get("artist") else item["title"]
            events.append({"source": "music", "external_id": f"{item['ts']}:{item.get('player')}",
                           "ts": local_iso(moment.astimezone()), "thread": item.get("player") or "?",
                           "text": track[:500], "meta": {}})
    return events
