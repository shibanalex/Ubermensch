"""Сон ноутбука из системного журнала: когда уснул (крышка, кнопка, простой) и когда проснулся.

Это точные границы «не за компьютером»: надёжнее перерывов в действиях. Пишет systemd-sleep:
«Performing sleep operation …» и «System returned from sleep operation …». Журнал доступен
группе adm/systemd-journal; без неё сборщик молча ничего не находит. Включения и выключения
не берём: при двойной загрузке с Windows часы между загрузками сдвигаются.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

from . import local_iso


def collect(since: datetime) -> list[dict]:
    try:
        done = subprocess.run(
            ["journalctl", "-o", "json", "-t", "systemd-sleep", "--no-pager",
             "--since", since.strftime("%Y-%m-%d %H:%M:%S")],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    events, asleep = [], None
    for line in done.stdout.splitlines():
        try:
            entry = json.loads(line)
            moment = datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6)
        except (ValueError, KeyError):
            continue
        message = str(entry.get("MESSAGE", ""))
        if message.startswith("Performing sleep operation"):
            asleep = (moment, entry.get("_BOOT_ID"), message.split("'")[1] if "'" in message else "")
        elif message.startswith("System returned from sleep") and asleep:
            start, boot, kind = asleep
            asleep = None
            minutes = (moment - start).total_seconds() / 60
            if boot != entry.get("_BOOT_ID") or not 0 <= minutes < 7 * 24 * 60:
                continue
            events.append({
                "source": "power", "external_id": f"sleep:{local_iso(start.astimezone())}",
                "ts": local_iso(start.astimezone()), "thread": "сон ноутбука",
                "text": f"Ноутбук уснул в {start:%H:%M} ({kind or 'suspend'}), проснулся в "
                        f"{moment:%H:%M %d.%m} — {minutes:.0f} мин",
                "meta": {"until": local_iso(moment.astimezone()), "minutes": round(minutes)},
            })
    return events
