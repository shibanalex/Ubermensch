"""Бот откликов hh: перенос в воронку без дублей, шаблон письма, приглашения, дневная сессия."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ubermensch import agent, career_bot, config
from ubermensch.store import Store

TEMPLATE = "Здравствуйте!\n\nОткликаюсь на «{position}» в {company}. Опыт — три года.\n\nАлексей"


class CareerBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bot = root / "bot"
        (bot / "data" / "browser_profile").mkdir(parents=True)
        (bot / ".venv" / "bin").mkdir(parents=True)
        (bot / ".venv" / "bin" / "hhbot").write_text("#!/bin/sh\n")
        self.bot = sqlite3.connect(bot / "data" / "hhbot.sqlite3")
        self.addCleanup(self.bot.close)
        self.bot.executescript(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, vacancy_id TEXT, status TEXT, vacancy_name TEXT,"
            " employer_name TEXT, url TEXT, letter TEXT, answers TEXT, reason TEXT, created_at TEXT);"
            "CREATE TABLE negotiations (vacancy_id TEXT PRIMARY KEY, state TEXT, status_text TEXT, checked_at TEXT);")
        for name, value in (("DATA_DIR", root / "data"), ("OUTPUT_DIR", root / "reports"), ("VAULT_DIR", None)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (root / "data" / "career").mkdir(parents=True)
        (root / "data" / "career" / "cover_letters.json").write_text(json.dumps(
            {"ru": [{"id": "ru1", "text": TEMPLATE}], "en": []}, ensure_ascii=False))
        env = patch.dict(os.environ, {"HHBOT_DIR": str(bot), "HHBOT_LIVE": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)

    def sent(self, vacancy, letter, state=None):
        self.bot.execute("INSERT INTO applications(vacancy_id,status,vacancy_name,employer_name,url,letter,created_at)"
                         " VALUES (?,?,?,?,?,?,?)", (vacancy, "sent", f"Python {vacancy}", "ООО Ромашка",
                                                     f"https://hh.ru/vacancy/{vacancy}", letter, "2026-10-20T10:00:00"))
        if state:
            self.bot.execute("INSERT OR REPLACE INTO negotiations VALUES (?,?,?,?)",
                             (vacancy, state, "", "2026-10-22T10:00:00"))
        self.bot.commit()

    def test_letters_are_attributed_to_templates(self):
        letter = TEMPLATE.replace("{position}", "Middle Python").replace("{company}", "ООО «Ромашка»")
        self.assertEqual(career_bot.template_of(letter), "ru1")
        self.assertIsNone(career_bot.template_of("Здравствуйте! Я ИИ-письмо про ваш продукт."))

    def test_sync_fills_the_funnel_once_and_reports_new_invitations(self):
        letter = TEMPLATE.replace("{position}", "Python 1").replace("{company}", "ООО Ромашка")
        self.sent("1", letter, "invitation")
        self.sent("2", "письмо нейросети", "discard")
        self.sent("3", letter)
        first = career_bot.sync(self.store)
        self.assertEqual(first["added"], {"applied": 3, "interview": 1, "rejected": 1})
        self.assertEqual([i["vacancy"] for i in first["invitations"]], ["Python 1"])
        again = career_bot.sync(self.store)
        self.assertEqual((again["added"], again["invitations"]), ({}, []))  # без дублей
        evidence = [r[0] for r in self.store.db.execute(
            "SELECT evidence FROM career_events WHERE stage='applied' ORDER BY id")]
        self.assertIn("письмо ru1", evidence[0])
        stats = {s["template"]: s for s in career_bot.template_stats()}
        self.assertEqual((stats["ru1"]["sent"], stats["ru1"]["invited"]), (2, 1))
        self.assertEqual(stats["без шаблона"]["rejected"], 1)

    async def test_daily_session_is_dry_by_default_and_reports_to_him(self):
        notes = []
        core = SimpleNamespace(notify=notes.append, thinking=False, bus=SimpleNamespace(publish=lambda e: None))
        autonomy = agent.Autonomy(self.store, SimpleNamespace(brain=None), SimpleNamespace(_last_chat=datetime.min), core)
        self.sent("1", "x", "invitation")
        run = AsyncMock(return_value=(0, "…\nИтог: просмотрено 40, отправлено 0, dry-run 30\n"))
        with patch.object(career_bot, "command", run), \
                patch("ubermensch.system.windows.idle_seconds", lambda: 3600.0):
            now = datetime.now().replace(hour=12)
            self.assertTrue(autonomy._hh_due(now))
            result = await autonomy.hh_session(now)
            self.assertFalse(autonomy._hh_due(now))  # раз в день
        self.assertIn(("run", "--dry-run", "--limit", "30"), [c.args for c in run.await_args_list])
        self.assertFalse(result["live"])
        self.assertIn("без отправки", notes[0])
        self.assertIn("🎉 Приглашения: Python 1", notes[0])

    def test_not_due_when_he_is_at_the_computer_or_bot_missing(self):
        autonomy = agent.Autonomy(self.store, SimpleNamespace(brain=None), SimpleNamespace(_last_chat=datetime.min),
                                  SimpleNamespace(notify=lambda t: None))
        now = datetime.now().replace(hour=12)
        with patch("ubermensch.system.windows.idle_seconds", lambda: 30.0):
            self.assertFalse(autonomy._hh_due(now))
        with patch.dict(os.environ, {"HHBOT_DIR": str(Path(self.temp.name) / "none")}), \
                patch("ubermensch.system.windows.idle_seconds", lambda: 3600.0):
            self.assertIsNotNone(career_bot.available())
            self.assertFalse(autonomy._hh_due(now))


if __name__ == "__main__":
    unittest.main()
