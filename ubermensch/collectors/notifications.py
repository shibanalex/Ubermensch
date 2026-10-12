"""Уведомления Windows: кто и что пишет пользователю (Discord, почта, сайты и т. д.).

Windows хранит недавние уведомления в локальной базе; собирая её регулярно, копим историю.
Telegram отсюда не берём — его переписки собираются напрямую и полнее.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import local_iso

DATABASE = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Notifications" \
    / "wpndatabase.db"
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
SKIP_APPS = re.compile(r"(?i)telegram|python|ubermensch")


def _app_name(primary_id: str) -> str:
    """«Microsoft.WindowsStore_8wekyb3d8bbwe!App» → «WindowsStore», «…\\chrome.exe» → «chrome»."""
    name = re.split(r"[!\\/]", primary_id)
    candidate = next((part for part in reversed(name) if part and not part.startswith("{")),
                     primary_id)
    candidate = re.sub(r"_[a-z0-9]{13}$", "", candidate.removesuffix(".exe"))
    return candidate.split(".")[-1] if candidate.count(".") >= 1 else candidate


def collect() -> list[dict]:
    if not DATABASE.exists():
        return []
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm"):  # база открыта Windows — читаем копию целиком
            source = Path(f"{DATABASE}{suffix}")
            if source.exists():
                shutil.copy2(source, Path(tmp) / f"wpndatabase.db{suffix}")
        db = sqlite3.connect(Path(tmp) / "wpndatabase.db")
        rows = db.execute(
            "SELECT n.Id, h.PrimaryId, n.ArrivalTime, n.Payload FROM Notification n "
            "JOIN NotificationHandler h ON h.RecordId = n.HandlerId WHERE n.Type = 'toast'"
        ).fetchall()
        db.close()
    for notification_id, primary_id, arrival, payload in rows:
        if not payload or SKIP_APPS.search(primary_id or ""):
            continue
        xml = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        texts = [html.unescape(t).strip() for t in re.findall(r"<text[^>]*>([^<]*)</text>", xml)]
        text = " — ".join(t for t in texts if t)
        if not text:
            continue
        moment = FILETIME_EPOCH + timedelta(microseconds=int(arrival) // 10)
        events.append({
            "source": "notification",
            "external_id": f"{primary_id}:{notification_id}:{arrival}",
            "ts": local_iso(moment),
            "thread": _app_name(primary_id or "?"),
            "text": text[:1000],
            "meta": {},
        })
    return events
