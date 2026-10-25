"""Долгие поручения: журнал сделанного между заходами, продолжение после перезапуска без повторов."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import agent, config
from ubermensch.assistant import Outcome
from ubermensch.runtime.jobs import JobCancelled
from ubermensch.store import Store


class FakeAssistant:
    def __init__(self, store):
        self.store, self.calls, self.answers = store, [], []
        self.brain = SimpleNamespace(wait_out=lambda e: asyncio.sleep(0, False))

    async def agent_ask(self, task, extra, **kwargs):
        """Заход: задание с тем же source, что у настоящего agent_ask, и вызовы в его журнале."""
        self.calls.append(task)
        data, tools, error = self.answers.pop(0)
        run_id = self.store.create_run("agent", {"source": kwargs.get("source")}, "background")
        for n, (name, status, arguments) in enumerate(tools):
            call_id = f"{run_id[:24]}{n:08x}"
            self.store.create_call(call_id, run_id, name, arguments)
            if status != "running":
                self.store.update_call(call_id, status, result={} if status == "completed" else None)
        if error is not None:
            raise error
        return data, Outcome(reply=data.get("reply", ""), run_id=run_id)


CONTINUE = {"status": "continue", "notes": "нашла 2 из 5", "report": "", "journal": "", "reply": ""}
DONE = {"status": "done", "notes": "", "report": "## Итог", "journal": "", "reply": "Готово"}


class TaskResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in (("DATA_DIR", root), ("OUTPUT_DIR", root / "reports"), ("VAULT_DIR", None)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.notes = []
        core = SimpleNamespace(notify=self.notes.append, thinking=False,
                               bus=SimpleNamespace(publish=lambda event: None))
        self.assistant = FakeAssistant(self.store)
        self.autonomy = agent.Autonomy(self.store, self.assistant,
                                       SimpleNamespace(_last_chat=datetime.min), core)
        self.task = agent.add_task("Сравнить вакансии", "найди 5 вакансий и сохрани в отчёт")

    def current(self):
        return next(t for t in agent.tasks() if t["id"] == self.task["id"])

    async def test_next_session_sees_confirmed_actions_and_does_not_repeat_them(self):
        self.assistant.answers = [
            (CONTINUE, [("write_file", "completed", {"path": "/tmp/r.md", "content": "две вакансии"})], None),
            (DONE, [], None)]
        await self.autonomy.run_task(self.current())
        await self.autonomy.run_task(self.current())
        self.assertIn("подтверждено журналом вызовов — не повторяй", self.assistant.calls[1])
        self.assertIn("write_file(content=две вакансии, path=/tmp/r.md)", self.assistant.calls[1])
        self.assertEqual(self.current()["status"], "done")

    async def test_restart_between_steps_resumes_by_itself(self):
        self.assistant.answers = [
            ({}, [("career_search", "completed", {"query": "python"})], asyncio.CancelledError()),
            (DONE, [], None)]
        with self.assertRaises(asyncio.CancelledError):  # ядро выключается посреди захода
            await self.autonomy.run_task(self.current())
        self.assertEqual((self.current()["status"], self.current()["interrupted_by"]), ("interrupted", "restart"))
        self.assertEqual(self.autonomy.resume_interrupted(), [self.task["id"]])
        item = self.current()
        self.assertEqual((item["status"], item["sessions"], item["resumes"]), ("pending", 0, 1))
        await self.autonomy.run_task(item)
        self.assertIn("career_search(query=python)", self.assistant.calls[1])
        self.assertTrue(self.notes[-1].startswith("📋"))

    def test_crash_inside_a_tool_call_asks_him_instead_of_resuming(self):
        run_id = self.store.create_run("agent", {"source": f"task:{self.task['id']}"}, "background")
        self.store.create_call("a" * 32, run_id, "email_send", {"to": "hr@example.com"})
        agent._update_task(self.task["id"], status="running")
        agent.recover()
        self.store.recover_execution()  # ядро при старте: оборванный вызов → uncertain
        self.assertEqual(self.autonomy.resume_interrupted(), [])
        self.assertEqual(self.current()["interrupted_by"], "uncertain")
        self.assertIn("email_send", self.notes[0])
        self.autonomy.resume_interrupted()
        self.assertEqual(len(self.notes), 1)  # не повторяет вопрос каждую минуту
        self.assertTrue(agent.resume_task(self.task["id"]))  # он проверил и сказал продолжать
        self.assertEqual(self.current()["status"], "pending")

    async def test_cancelled_by_him_is_never_resumed_by_itself(self):
        self.assistant.answers = [({}, [], JobCancelled("остановлено"))]
        with self.assertRaises(JobCancelled):
            await self.autonomy.run_task(self.current())
        self.assertEqual(self.autonomy.resume_interrupted(), [])
        self.assertEqual(self.current()["interrupted_by"], "user")

    def test_resume_limit_stops_endless_restarts(self):
        agent._update_task(self.task["id"], status="interrupted", interrupted_by="restart",
                           resumes=agent.RESUME_LIMIT)
        self.assertEqual(self.autonomy.resume_interrupted(), [])
        self.assertEqual(self.current()["interrupted_by"], "limit")
        self.assertIn("несколько раз подряд", self.notes[0])


if __name__ == "__main__":
    unittest.main()
