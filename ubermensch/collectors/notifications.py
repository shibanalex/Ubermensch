"""Уведомления: кто и что пишет пользователю (Discord, почта, сайты в Chrome и т. д.).

Система истории уведомлений не хранит, поэтому ядро само слушает их на шине D-Bus
(system/session.py) и пишет журнал по дням; сборщик забирает свежие дни, база
отбрасывает уже собранное. Telegram отсюда не берём — его переписки собираются напрямую
и полнее.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timedelta

from .. import config
from . import local_iso

FOLDER = config.DATA_DIR / "raw" / "notifications"
FRESH_DAYS = 3  # сбор идёт чаще раза в день — этого хватает с запасом
KEEP_DAYS = 30
SKIP_APPS = re.compile(r"(?i)telegram|python|ubermensch|anya|plasma_waitforname|kded|"
                       r"org\.kde\.(powerdevil|kdeconnect\.daemon)|spectacle")
TAGS = re.compile(r"<[^>]+>")


def _app_name(app: str, entry: str) -> str:
    """«Google Chrome» → «Google Chrome», пустое имя — по ярлыку «org.kde.discover» → «discover»."""
    return app.strip() or entry.rsplit(".", 1)[-1] or "?"


def collect() -> list[dict]:
    if not FOLDER.is_dir():
        return []
    today = date.today()
    events = []
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
                record = json.loads(line)
            except ValueError:
                continue
            app = _app_name(record.get("app", ""), record.get("entry", ""))
            if SKIP_APPS.search(f"{app} {record.get('entry', '')}"):
                continue
            parts = [html.unescape(TAGS.sub("", record.get(k) or "")).strip()
                     for k in ("summary", "body")]
            text = " — ".join(p for p in parts if p)
            if not text:
                continue
            digest = hashlib.sha1(line.encode("utf-8")).hexdigest()[:16]
            events.append({
                "source": "notification",
                "external_id": f"{app}:{record.get('ts')}:{digest}",
                "ts": local_iso(datetime.fromisoformat(record["ts"]).astimezone()),
                "thread": app,
                "text": text[:1000],
                "meta": {},
            })
    return events
