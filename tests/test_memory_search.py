"""Поиск по всей памяти: факты вне контекста, люди и разговоры, кириллица без учёта регистра."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from ubermensch.capabilities import memory
from ubermensch.capabilities.registry import ToolArgumentsError
from ubermensch.runtime import policy
from ubermensch.runtime.context import current
from ubermensch.store import Store


class MemorySearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        token = current.set(SimpleNamespace(core=SimpleNamespace(store=self.store), mode="background",
                                            run_id="r", call_id="c"))
        self.addCleanup(current.reset, token)
        self.store.add_fact("career", "Клиент платит 5 000 ₽ за один сервис парсинга", 3, "chat")
        self.store.add_fact("health", "Спит плохо перед собеседованиями", 2, "chat")
        self.store.db.execute("INSERT INTO people (name, relation, notes, importance, updated_at) "
                              "VALUES ('Клиент', 'заказчик', 'Парсеры ритейла', 3, '2026-10-01')")
        self.store.add_message("user", "Клиент опять заплатил только за один парсер", "tg")
        self.store.add_message("assistant", "📥 импорт про клиента", "import")  # не разговор

    async def test_finds_facts_people_and_talk_by_stems(self):
        found = await memory.memory_search("клиент парсинг")
        self.assertEqual([f["area"] for f in found["facts"]], ["career"])
        self.assertEqual([p["name"] for p in found["people"]], ["Клиент"])
        self.assertEqual([m["who"] for m in found["messages"]], ["он"])
        only = await memory.memory_search("собеседование", where="facts", area="health")
        self.assertEqual(len(only["facts"]), 1)
        self.assertNotIn("people", only)

    async def test_bad_arguments_and_background_rights(self):
        policy.check_tool("memory_search")  # чтение памяти доступно фону
        with self.assertRaises(ToolArgumentsError):
            await memory.memory_search("и на по")
        with self.assertRaises(ToolArgumentsError):
            await memory.memory_search("клиент", area="money")


if __name__ == "__main__":
    unittest.main()
