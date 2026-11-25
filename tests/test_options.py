"""Карта путей к цели: оценки без придуманных вероятностей, порядок, отказ владельца; его решение."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from ubermensch import goal_loop, options
from ubermensch.api import create_app
from ubermensch.capabilities import planning
from ubermensch.core import Bus, Core
from ubermensch.runtime.context import current
from ubermensch.store import Store


class OptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.goal = self.store.add_goal("career", "Удалённая работа", "", None)

    def add(self, title, payoff="medium", effort="medium"):
        return self.store.save_option(self.goal, title, "что это", "доступно: есть резюме",
                                      "подходит: Python", payoff, effort, "время", True, "воронка hh")

    def context(self, mode):
        token = current.set(SimpleNamespace(core=SimpleNamespace(store=self.store), mode=mode,
                                            run_id="r", call_id="c"))
        self.addCleanup(current.reset, token)

    def test_same_title_updates_estimate_keeps_status(self):
        first = self.add("Отклики на hh", payoff="low")
        self.store.set_option_status(first, "trying", "идут отклики", "журнал")
        again = self.add("Отклики на hh", payoff="high")
        self.assertEqual(first, again)
        [row] = self.store.goal_options(self.goal)
        self.assertEqual((row["payoff"], row["status"], row["outcome"]), ("high", "trying", "идут отклики"))
        with self.assertRaises(ValueError):  # только качественные уровни
            self.add("Фриланс", payoff="70%")
        with self.assertRaises(ValueError):
            self.store.save_option(self.goal, "Пусто", "", "a", "b", "low", "low", "r", False, "e")

    def test_order_trying_then_cheap_high_payoff(self):
        hard = self.add("Своя студия", payoff="high", effort="high")
        cheap = self.add("Рекомендации знакомых", payoff="high", effort="low")
        low = self.add("Биржи фриланса", payoff="low", effort="low")
        going = self.add("Отклики на hh")
        failed = self.add("Холодные письма")
        self.store.set_option_status(going, "trying", "идёт", "журнал")
        self.store.set_option_status(failed, "failed", "0 ответов из 20", "почта")
        self.assertEqual([o["id"] for o in self.store.goal_options(self.goal)],
                         [going, cheap, hard, low, failed])

    def test_rejected_returns_only_by_owner(self):
        item = self.add("Переезд в Москву")
        self.store.set_option_status(item, "rejected", "не хочу переезжать", "его ответ", owner=True)
        with self.assertRaises(ValueError):
            self.store.set_option_status(item, "trying", "попробуем", "мысль Ани")
        self.store.set_option_status(item, "untried", "передумал", "его ответ", owner=True)
        self.assertEqual(self.store.goal_options(self.goal)[0]["status"], "untried")

    async def test_background_tool_cannot_undo_his_rejection(self):
        item = self.add("Переезд в Москву")
        self.store.set_option_status(item, "rejected", "нет", "его ответ", owner=True)
        self.context("background")
        with self.assertRaises(Exception):
            await planning.option_status(item, "trying", "попробуем", "мысль")
        created = await planning.option_upsert(self.goal, "Стажировка", "что", "да", "да", "medium",
                                               "low", "мало денег", True, "вакансии")
        self.assertEqual(self.store.goal_options(self.goal)[0]["id"], created["id"])

    def test_step_prompt_carries_map_and_asks_to_build_empty_one(self):
        goal = {"id": self.goal, "area": "career", "title": "Работа", "details": "", "deadline": None}
        plan = {"metric": "Приглашения", "next_step": "Найти вакансии"}
        empty = goal_loop.step_task(goal, plan, [], [], options=[])
        self.assertIn("Пуста — этим шагом составь её", empty)
        self.add("Отклики на hh")
        text = goal_loop.step_task(goal, plan, [], [], options=self.store.goal_options(self.goal))
        self.assertIn("<options>", text)
        self.assertIn("Отклики на hh", text)
        self.assertNotIn("evidence", options.map_text(self.store.goal_options(self.goal)))

    def test_chat_sees_maps_of_active_goals(self):
        self.assertEqual(options.options_context(self.store), "")
        self.add("Отклики на hh")
        text = options.options_context(self.store)
        self.assertIn(f"#{self.goal} ", text)
        self.assertIn("option_status", text)


class OptionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = Core.__new__(Core)
        self.core.store = Store(":memory:")
        self.addCleanup(self.core.store.db.close)
        self.core.bus = Bus()
        goal = self.core.store.add_goal("career", "Работа", "", None)
        self.option = self.core.store.save_option(goal, "Переезд", "что", "да", "да", "high", "high",
                                                  "дорого", False, "вакансии")
        self.client = TestClient(TestServer(create_app(self.core, "t")))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_owner_rejects_and_restores_from_obsidian(self):
        headers = {"Authorization": "Bearer t"}
        url = f"/api/options/{self.option}/status"
        for status in ("rejected", "untried"):
            response = await self.client.post(url, json={"status": status, "outcome": "кнопка"}, headers=headers)
            self.assertEqual(response.status, 200)
        self.assertEqual(self.core.store.goal_options(1)[0]["status"], "untried")
        bad = await self.client.post(url, json={"status": "maybe", "outcome": "x"}, headers=headers)
        self.assertEqual(bad.status, 400)


if __name__ == "__main__":
    unittest.main()
