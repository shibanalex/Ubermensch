"""Claude API: ответ по схеме, цикл инструментов, учёт денег и стоп по бюджету."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ubermensch.claude_api import ClaudeApiBrain, ClaudeApiError


class Cursors:
    def __init__(self):
        self.values = {}

    def cursor(self, key):
        return self.values.get(key)

    def set_cursor(self, key, value):
        self.values[key] = value


def _usage(inp=1000, out=200):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.requests = replies, []

    async def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=lambda o: o.__dict__)))
        return self.replies.pop(0)


class ClaudeApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.system = Path(self.temp.name) / "system.md"
        self.system.write_text("system", encoding="utf-8")
        self.notes = []
        self.brain = ClaudeApiBrain(Cursors(), SimpleNamespace(clear=lambda *a: None,
                                                               report=lambda *a: None),
                                    self.notes.append)
        self.schema = {"type": "object", "required": ["reply"], "properties": {"reply": {"type": "string"}}}

    def _client(self, replies):
        messages = FakeMessages(replies)
        self.brain.client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
        return messages

    async def test_tool_loop_and_charge(self):
        tool_use = SimpleNamespace(type="tool_use", id="t1", name="system_status", input={})
        messages = self._client([
            SimpleNamespace(stop_reason="tool_use", content=[tool_use], usage=_usage(), model="claude-haiku-5-5"),
            SimpleNamespace(stop_reason="end_turn", model="claude-haiku-5-5", usage=_usage(),
                            content=[SimpleNamespace(type="text", text='{"reply": "100%"}')])])
        calls = []

        class Gateway:
            def definitions(self):
                return [SimpleNamespace(name="system_status", description="d", parameters={"type": "object"})]

            async def call(self, name, arguments):
                calls.append(name)
                return "Батарея: 100%"

        answer = await self.brain.ask("?", system_prompt_file=self.system, schema=self.schema,
                                      role="background", tool_gateway=Gateway())
        self.assertEqual(answer.data, {"reply": "100%"})
        self.assertEqual(calls, ["system_status"])
        self.assertEqual(messages.requests[0]["model"], "claude-haiku-5-5")
        self.assertEqual(messages.requests[0]["output_config"]["effort"], "low")
        self.assertEqual(messages.requests[1]["messages"][-1]["content"][0]["tool_use_id"], "t1")
        self.assertAlmostEqual(self.brain.spent, 2 * (1000 * 0.10 + 200 * 0.50) / 1e6)

    async def test_budget_stops_and_warns(self):
        self.brain.store.set_cursor("claude_api:spent", "7.99")
        self._client([SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5-5",
                                      usage=_usage(10_000, 2_000),
                                      content=[SimpleNamespace(type="text", text='{"reply": "ok"}')])])
        await self.brain.ask("?", system_prompt_file=self.system, schema=self.schema, role="chat")
        self.assertTrue(self.notes and "из $10" in self.notes[0])
        self.brain.store.set_cursor("claude_api:spent", "10.01")
        self.assertFalse(self.brain.usable)
        with self.assertRaises(ClaudeApiError):
            await self.brain.ask("?", system_prompt_file=self.system, schema=self.schema)


if __name__ == "__main__":
    unittest.main()
