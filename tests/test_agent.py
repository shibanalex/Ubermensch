"""Самостоятельность: расписание, heartbeat, поручения, рефлексия и память Ани."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import agent, config
from ubermensch.assistant import Outcome
from ubermensch.store import Store


class FakeAssistant:
    def __init__(self):
        self.calls, self.answers = [], []
        self._lock = asyncio.Lock()
        self.brain = SimpleNamespace(wait_out=lambda e: asyncio.sleep(0, False))

    async def agent_ask(self, task, extra, **kwargs):
        self.calls.append(task)
        data = self.answers.pop(0)
        return data, Outcome(reply=data.get("reply", ""), emotion=data.get("emotion", ""))


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in (("DATA_DIR", root), ("OUTPUT_DIR", root / "reports"),
                            ("VAULT_DIR", None)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("idle_seconds", lambda: 10.0), ("foreground", lambda: None)):
            patcher = patch(f"ubermensch.system.windows.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.notes = []
        self.core = SimpleNamespace(notify=self.notes.append, thinking=False,
                                    bus=SimpleNamespace(publish=lambda event: None))
        self.assistant = FakeAssistant()
        self.autonomy = agent.Autonomy(self.store, self.assistant,
                                       SimpleNamespace(_last_chat=datetime.min), self.core)

    def test_next_run_for_day_week_and_hours(self):
        monday = datetime(2026, 10, 19, 12, 0)  # понедельник
        self.assertEqual(agent.next_run({"every": "day", "at": "09:00"}, monday),
                         datetime(2026, 10, 20, 9, 0))
        self.assertEqual(agent.next_run({"every": "week", "at": "10:00", "weekday": 4}, monday),
                         datetime(2026, 10, 23, 10, 0))
        self.assertEqual(agent.next_run({"every": "hours", "hours": 3}, monday),
                         datetime(2026, 10, 19, 15, 0))
        schedule = agent.add_schedule("Обзор недели", "сделай обзор", "week", "10:00", 0)
        fired = agent.due_schedules(datetime.fromisoformat(schedule["next_at"]))
        self.assertEqual([f["id"] for f in fired], [schedule["id"]])
        self.assertGreater(agent.schedules()[0]["next_at"], schedule["next_at"])
        with self.assertRaises(ValueError):
            agent.add_schedule("x", "y", "day", "25:00")

    async def test_heartbeat_skips_unchanged_and_respects_budget(self):
        self.assistant.answers = [{"decision": "message", "reply": "Пора на пару", "journal": "напомнила"}]
        self.assertEqual(await self.autonomy.heartbeat(), "message")
        self.assertEqual(self.notes, ["Пора на пару"])
        self.assertTrue(agent.checklist_path().exists())
        self.assertEqual(await self.autonomy.heartbeat(), "без перемен")  # Claude не вызывался
        self.assertEqual(len(self.assistant.calls), 1)
        self.assistant.answers = [{"decision": "message", "reply": "Ещё одно", "journal": ""}]
        await self.autonomy.heartbeat(force=True)  # только что писала — 90 минут молчит
        self.assertEqual(self.notes, ["Пора на пару"])
        self.assertIn("писать сейчас нельзя", self.assistant.calls[-1])
        self.assertIn("напомнила", agent.journal_path(date.today()).read_text(encoding="utf-8"))

    async def test_task_continues_then_reports(self):
        task = agent.add_task("Сравнить ноутбуки", "найди три варианта до 80 тысяч")
        self.assistant.answers = [
            {"status": "continue", "notes": "нашла два", "reply": "", "report": "", "journal": ""},
            {"status": "done", "notes": "", "reply": "Готово, лучший — X", "report": "## Итог\nX",
             "journal": "сравнила"}]
        self.assertEqual(await self.autonomy.run_task(agent.tasks("pending")[0]), "continue")
        self.assertIn("нашла два", agent.tasks("pending")[0]["notes"])
        self.assertEqual(await self.autonomy.run_task(agent.tasks("pending")[0]), "done")
        self.assertIn("Прошлые заходы: нашла два", self.assistant.calls[-1])
        done = [t for t in agent._read_json("tasks.json") if t["id"] == task["id"]][0]
        self.assertEqual(done["status"], "done")
        self.assertIn("## Итог", Path(done["report"]).read_text(encoding="utf-8"))
        self.assertTrue(self.notes[-1].startswith("📋 Готово"))

    async def test_reflection_writes_lessons_skills_and_context(self):
        self.store.add_message("user", "не пиши мне так длинно", "tg")
        self.assistant.answers = [{"lessons": "## Как с ним говорить\n- Коротко.", "journal": "день",
                                   "skills": [{"name": "Справка из вуза", "when": "нужна справка",
                                               "steps": "1. ЛК → Справки"}], "reply": ""}]
        await self.autonomy.reflect()
        self.assertIn("не пиши мне так длинно", self.assistant.calls[0])
        self.assertEqual(self.store.cursor("reflected_on"), date.today().isoformat())
        self.assertIsNone(self.autonomy._reflection_due(datetime.now().replace(hour=23)))
        context = agent.context_block("мне нужна справка из вуза")
        self.assertIn("Коротко.", context)
        self.assertIn("ЛК → Справки", context)
        self.assertIn("[итог дня] день", context)


if __name__ == "__main__":
    unittest.main()
