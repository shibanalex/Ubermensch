"""Сенсоры Linux: сон ноутбука из журнала, журнал прослушиваний, таблицы Excel и их метрики."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import behavior
from ubermensch.collectors import listening, power
from ubermensch.store import Store
from ubermensch.system.office import excel_text, write_excel


def _journal(*entries: tuple[datetime, str, str]) -> str:
    return "\n".join(json.dumps({"__REALTIME_TIMESTAMP": str(int(moment.timestamp() * 1e6)),
                                 "MESSAGE": message, "_BOOT_ID": boot})
                     for moment, message, boot in entries)


class SensorTests(unittest.TestCase):
    def test_laptop_sleep_pairs_entry_and_exit_within_one_boot(self):
        output = _journal(
            (datetime(2026, 10, 17, 1, 30), "Performing sleep operation 'suspend'...", "a"),
            (datetime(2026, 10, 17, 9, 10), "System returned from sleep operation 'suspend'.", "a"),
            (datetime(2026, 10, 17, 14, 0), "Performing sleep operation 'suspend'...", "a"),
            (datetime(2026, 10, 17, 15, 0), "System returned from sleep operation 'suspend'.", "b"))
        with patch.object(power.subprocess, "run", return_value=SimpleNamespace(stdout=output)):
            events = power.collect(datetime(2026, 10, 1))
        self.assertEqual(len(events), 1)  # второй сон — через перезагрузку, часы ненадёжны
        self.assertEqual(events[0]["meta"]["minutes"], 460)

        store = Store(":memory:")
        self.addCleanup(store.db.close)
        store.add_events(events)
        metric = [i for i in behavior.derive(store.db) if i["ref"] == "metric:laptop:2026-10"]
        self.assertIn("уснул — медиана 01:30, проснулся — 09:10", metric[0]["text"])

    def test_listening_journal_and_music_metric(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(listening, "FOLDER", Path(temp)):
            day = date.today().isoformat()
            Path(temp, f"{day}.jsonl").write_text("\n".join(json.dumps(
                {"ts": f"{day}T2{i}:00:00", "player": "spotify", "status": "Playing",
                 "title": "Trio op. 3", "artist": "Zemlinsky"}) for i in range(2)), encoding="utf-8")
            events = listening.collect()
        self.assertEqual([e["text"] for e in events], ["Zemlinsky — Trio op. 3"] * 2)
        store = Store(":memory:")
        self.addCleanup(store.db.close)
        store.add_events(events)
        music = [i for i in behavior.derive(store.db) if i["metadata"]["kind"] == "music"]
        self.assertIn("Zemlinsky (2)", music[0]["text"])

    def test_excel_text_keeps_sheets_dates_and_skips_empty_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "diary.xlsx"
            write_excel(path, "Замеры", [["Дата", "Вес, кг", "Сон, ч"],
                                         [datetime(2026, 10, 14), 72.5, 7], [None, None, None]])
            text = excel_text(path)
        self.assertIn("## Лист «Замеры»", text)
        self.assertIn("2026-10-14 | 72.5 | 7", text)
        self.assertEqual(text.count("\n"), 2)  # заголовок листа, шапка, одна строка данных


if __name__ == "__main__":
    unittest.main()

