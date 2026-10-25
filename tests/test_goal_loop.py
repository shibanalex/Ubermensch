"""Цикл цели: план → шаг инструментами → проверка по журналу вызовов → следующий шаг."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import agent, config, goal_loop
from ubermensch.assistant import Outcome
from ubermensch.brain import BrainError
from ubermensch.store import Store

# журнал ставит отметки по настоящим часам — «сейчас» в тестах того же дня
NOON = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)


class FakeAssistant:
    """Отвечает заготовками; инструменты «вызывает», записывая их в журнал запуска."""

    def __init__(self, store):
        self.store, self.calls, self.answers = store, [], []
        self.brain = SimpleNamespace(wait_out=lambda e: asyncio.sleep(0, False))

    async def agent_ask(self, task, extra, **kwargs):
        self.calls.append(task)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        data, tools = answer
        run_id = self.store.create_run("agent", {"source": kwargs.get("source")}, "background")
        for n, (name, status) in enumerate(tools):
            call_id = f"{run_id[:24]}{n:08x}"
            self.store.create_call(call_id, run_id, name, {})
            if status != "running":
                self.store.update_call(call_id, status, result={} if status == "completed" else None)
        return data, Outcome(reply=data.get("reply", ""), run_id=run_id)


class GoalLoopTests(unittest.IsolatedAsyncioTestCase):
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
        self.goal = self.store.add_goal("career", "Удалённая работа Python backend", "120–140 тыс.", "2027-03-31")

    def plan(self, next_step="Найти 5 вакансий Middle Python на hh и оценить соответствие"):
        self.store.save_goal_plan(self.goal, "Приглашения на собеседование", "шт", 0, 5, 300,
                                  next_step, "Воронка откликов бота: 8 приглашений на 79 откликов")

    def last(self):
        return self.store.goal_cycles(self.goal, 1)[0]

    async def test_goal_without_plan_gets_a_proposal_and_waits_for_owner(self):
        proposal = {"metric": "Приглашения", "unit": "шт", "baseline": None, "target": 5,
                    "weekly_minutes": 300, "next_step": "Подобрать вакансии",
                    "evidence": "Воронка откликов"}
        self.assistant.answers = [({"proposal": proposal, "ask_user": "Сколько приглашений сейчас?",
                                    "reply": "Предлагаю план…"}, [])]
        self.assertEqual(await self.autonomy.goal_cycle(NOON), "needs_user")
        self.assertIn("нет измеримого плана", self.assistant.calls[0])
        self.assertEqual(self.notes, ["🎯 Удалённая работа Python backend: Предлагаю план…"])
        # предложение видно в разговоре, чтобы Аня сохранила план после его «да»
        self.assertIn("Сколько приглашений сейчас?", goal_loop.proposals_context(self.store))
        # сегодня и следующие дни без ответа — не переспрашивать
        self.assertIsNone(await self.autonomy.goal_cycle(NOON + timedelta(hours=3)))
        self.assertIsNone(self.store.due_goal(NOON + timedelta(days=2)))
        # он утвердил план — цель снова в работе, предложение из контекста ушло
        self.plan()
        self.assertIsNotNone(self.store.due_goal(NOON + timedelta(days=1)))
        self.assertEqual(goal_loop.proposals_context(self.store), "")

    async def test_step_counts_as_done_only_with_completed_tool_calls(self):
        self.plan()
        self.assistant.answers = [({"outcome": "done", "action": "Нашла и оценила 5 вакансий",
                                    "result": "career_assess ×5", "next_step": "Подготовить черновики для трёх лучших",
                                    "blocker": "", "ask_user": "", "reply": "Оценила 5 вакансий"},
                                   [("career_search", "completed"), ("career_assess", "completed")])]
        self.assertEqual(await self.autonomy.goal_cycle(NOON), "done")
        self.assertIn("Найти 5 вакансий", self.assistant.calls[0])
        cycle = self.last()
        self.assertEqual(cycle["status"], "done")
        self.assertIn("career_assess", cycle["evidence"])
        plan = self.store.measured_goals()[0]
        self.assertEqual(plan["next_step"], "Подготовить черновики для трёх лучших")
        self.assertEqual((plan["metric"], plan["baseline"], plan["target"]), ("Приглашения на собеседование", 0, 5))
        self.assertEqual(self.notes, ["🎯 Удалённая работа Python backend: Оценила 5 вакансий"])

    async def test_claimed_done_without_tools_is_unverified_and_step_stays(self):
        self.plan()
        self.assistant.answers = [({"outcome": "done", "action": "Всё сделала", "result": "",
                                    "next_step": "Дальше", "blocker": "", "ask_user": "", "reply": "Готово!"}, [])]
        self.assertEqual(await self.autonomy.goal_cycle(NOON), "unverified")
        self.assertEqual(self.store.measured_goals()[0]["next_step"],
                         "Найти 5 вакансий Middle Python на hh и оценить соответствие")
        self.assertEqual(self.notes, [])  # неподтверждённым не хвастается

    async def test_blocked_step_is_shown_next_time_instead_of_repeating_blindly(self):
        self.plan()
        self.assistant.answers = [
            ({"outcome": "blocked", "action": "Искала на hh", "result": "", "next_step": "Remotive",
              "blocker": "hh отказал в доступе (403)", "ask_user": "", "reply": ""},
             [("career_search", "failed")]),
            ({"outcome": "done", "action": "Искала на Remotive", "result": "3 вакансии",
              "next_step": "Оценить", "blocker": "", "ask_user": "", "reply": ""},
             [("career_search", "completed")])]
        self.assertEqual(await self.autonomy.goal_cycle(NOON), "blocked")
        self.assertEqual(self.store.measured_goals()[0]["next_step"][:11], "Найти 5 вак")
        await self.autonomy.goal_cycle(NOON + timedelta(days=1))
        self.assertIn("hh отказал в доступе (403)", self.assistant.calls[1])  # <tried>
        self.assertEqual(self.store.measured_goals()[0]["next_step"], "Оценить")

    async def test_uncertain_tool_effect_blocks_instead_of_moving_on(self):
        self.plan()
        self.assistant.answers = [({"outcome": "done", "action": "Записала файл", "result": "",
                                    "next_step": "Дальше", "blocker": "", "ask_user": "", "reply": "Готово"},
                                   [("write_file", "uncertain")])]
        self.assertEqual(await self.autonomy.goal_cycle(NOON), "blocked")
        self.assertNotEqual(self.store.measured_goals()[0]["next_step"], "Дальше")

    async def test_model_failure_is_recorded_and_goal_waits_until_tomorrow(self):
        self.plan()
        self.assistant.answers = [BrainError("лимит")]
        with self.assertRaises(BrainError):
            await self.autonomy.goal_cycle(NOON)
        self.assertEqual(self.last()["status"], "failed")
        self.assertIsNone(self.store.due_goal(NOON + timedelta(hours=4)))
        self.assertIsNotNone(self.store.due_goal(NOON + timedelta(days=1)))

    async def test_goals_with_plans_go_first_then_by_deadline(self):
        later = self.store.add_goal("growth", "Трио ре минор", "", "2027-10-31")
        sooner = self.store.add_goal("personal", "К бабушке", "", "2026-11-30")
        self.store.save_goal_plan(later, "Готовые части", "шт", 1, 3, 120, "Разработка", "Экспозиция готова")
        self.assertEqual(self.store.due_goal(NOON)[0]["id"], later)  # с планом — первой
        self.store.record_goal_cycle(later, "step", "done")
        self.assertEqual(self.store.due_goal(NOON)[0]["id"], sooner)  # без плана — по сроку


if __name__ == "__main__":
    unittest.main()
