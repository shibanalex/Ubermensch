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
from ubermensch.collectors import gdrive, listening, power
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
        self.assertEqual(len(events), 1)  # второй сон: через перезагрузку, часы ненадёжны
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


    def test_gemini_chats_from_drive_go_to_import_once(self):
        chat = json.dumps({"chunkedPrompt": {"chunks": [{"role": "user", "text": "привет"}]}}).encode()
        listing = {"files": [{"id": "abc123456789", "name": "План/трио", "modifiedTime": "2026-10-19T10:00:00Z"},
                             {"id": "zzz", "name": "битый", "modifiedTime": "2026-10-19T11:00:00Z"}]}
        contents = {"abc123456789": chat, "zzz": b"not json"}

        class Download:
            def __init__(self, buffer, request):
                self.buffer, self.request = buffer, request

            def next_chunk(self):
                self.buffer.write(contents[self.request])
                return None, True

        files = SimpleNamespace(
            list=lambda **kw: SimpleNamespace(execute=lambda: listing),
            get_media=lambda fileId: fileId)
        drive = SimpleNamespace(files=lambda: files)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(gdrive.google_calendar, "service", return_value=drive), \
                patch("googleapiclient.http.MediaIoBaseDownload", Download):
            saved, cursor = gdrive.collect(Path(temp), None)
            again, _ = gdrive.collect(Path(temp), cursor)
            self.assertEqual(saved, ["Gemini — План_трио [abc12345].json"])
            self.assertEqual(again, [])  # тот же файл: без повторного импорта
            self.assertEqual(Path(temp, saved[0]).read_bytes(), chat)
        self.assertEqual(cursor, "2026-10-19T11:00:00Z")

    def test_shared_chat_link_downloads_once_and_is_remembered(self):
        chat = json.dumps({"chunkedPrompt": {"chunks": [{"role": "user", "text": "# План на осень"}]}})
        url = ("https://aistudio.google.com/app/prompts?state=%7B%22ids%22:%5B%22"
               "1am8XVHw0cG8PcJFesthoSPwQvZdANUoU%22%5D%7D&usp=sharing")
        self.assertEqual(gdrive.file_id(url), "1am8XVHw0cG8PcJFesthoSPwQvZdANUoU")

        class Opened:
            def __enter__(self):
                return SimpleNamespace(read=lambda size: chat.encode())

            def __exit__(self, *args):
                return False

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(gdrive, "LINKS", Path(temp) / "links.json"), \
                patch("urllib.request.urlopen", return_value=Opened()):
            first = gdrive.download_shared(url, Path(temp))
            again = gdrive.download_shared(url, Path(temp))
            self.assertEqual(first, again)
            self.assertEqual(len(list(Path(temp).glob("Gemini*.json"))), 1)
            self.assertEqual(gdrive.shared_links(), [url])


if __name__ == "__main__":
    unittest.main()

