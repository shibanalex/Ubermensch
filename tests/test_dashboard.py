"""Панель для Obsidian: снимок целей, предложений, согласий и задач без вызова модели."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import config, dashboard
from ubermensch.store import Store


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("DATA_DIR", Path(self.temp.name)), ("OUTPUT_DIR", Path(self.temp.name) / "r")):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"HHBOT_DIR": str(Path(self.temp.name) / "no-bot")})
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.core = SimpleNamespace(store=self.store, thinking=False, autonomy=None,
                                    state=lambda: {"approvals": [{"id": 1, "summary": "Письмо", "risk": "", "details": ""}]})

    def test_snapshot_shows_what_waits_for_him(self):
        planned = self.store.add_goal("career", "Работа", "", "2027-03-31")
        bare = self.store.add_goal("growth", "Трио", "", None)
        self.store.save_goal_plan(planned, "Приглашения", "шт", 0, 5, 300, "Найти вакансии", "воронка")
        self.store.record_goal_cycle(planned, "step", "blocked", action="Искала на hh", blocker="403")
        self.store.record_goal_cycle(bare, "plan", "needs_user", ask_user="Сколько частей готово?",
                                     proposal={"metric": "Части", "unit": "шт", "baseline": None, "target": 3,
                                               "weekly_minutes": 120, "next_step": "Разработка", "evidence": "x"})
        self.store.propose_experiment(planned, "owner", False, "Утро лучше", "воронка", "Откликаться утром",
                                      "ответы", "≥15%", "2 недели", 30, "нет", 2, "2027-01-01")
        self.store.add_task("Позвонить", "", None, None, "user")
        data = dashboard.build(self.core)
        goals = {g["title"]: g for g in data["goals"]}
        self.assertEqual(goals["Работа"]["plan"]["next_step"], "Найти вакансии")
        self.assertEqual(goals["Работа"]["last_cycle"]["blocker"], "403")
        self.assertIsNone(goals["Трио"]["plan"])
        self.assertEqual(data["plan_proposals"][0]["proposal"]["metric"], "Части")
        self.assertEqual(data["consent_needed"][0]["hypothesis"], "Утро лучше")
        self.assertEqual([t["title"] for t in data["tasks"]], ["Позвонить"])
        self.assertEqual(data["approvals"][0]["summary"], "Письмо")
        self.assertFalse(data["hh"]["available"])

    def test_plugin_source_is_valid_javascript(self):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("нужен node")
        plugin = Path(__file__).resolve().parents[1] / "obsidian-plugin/ubermensch-anya/main.js"
        self.assertEqual(subprocess.run(["node", "--check", str(plugin)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
