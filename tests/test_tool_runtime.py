"""Проверки исполнения и переключения без внешних ИИ и реальных сообщений."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import TestClient, TestServer

from ubermensch import config
from ubermensch.api import create_app
from ubermensch.assistant import Assistant, _schema
from ubermensch.brain import Answer, BrainError
from ubermensch.capabilities import load_registry
from ubermensch.capabilities.registry import ToolArgumentsError, ToolRegistry
from ubermensch.core import Core
from ubermensch.openai_brain import MAX_TOOL_ROUNDS, OpenAIBrain
from ubermensch.runtime.http_tools import HttpToolGateway, ToolExecutionUncertain
from ubermensch.runtime.router import ChatRouter, InterruptedToolRun
from ubermensch.runtime.tools import LocalToolGateway
from ubermensch.store import Store
from ubermensch.tools import create_server


class MemoryCursors:
    def __init__(self):
        self.values = {}

    def cursor(self, key):
        return self.values.get(key)

    def set_cursor(self, key, value):
        self.values[key] = value


def response(content=None, calls=None):
    return {"choices": [{"message": {"content": json.dumps(content) if content else None,
                                      "tool_calls": calls}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}


def call(name, arguments, identifier="call_1"):
    return {"id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


class OpenAIToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prompt = Path(self.temp.name) / "prompt.md"
        self.prompt.write_text("Test", encoding="utf-8")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_BUDGET_USD": "9",
                                    "OPENAI_WARN_USD": "7"}):
            self.brain = OpenAIBrain(MemoryCursors(), Mock(), Mock())
        self.brain._models = {"chat": "gpt-4.1", "background": "gpt-4.1"}
        self.registry = ToolRegistry()
        self.effects = []

        @self.registry.tool()
        async def save_note(text: str) -> str:
            self.effects.append(text)
            return "saved"

        self.gateway = LocalToolGateway(self.registry)
        self.requests = []

    def arrange(self, responses):
        async def request(session, method, path, body=None):
            self.requests.append(copy.deepcopy(body))
            return responses.pop(0)
        self.brain._request = request

    async def ask(self, **kwargs):
        return await self.brain.ask("Save", system_prompt_file=self.prompt,
                                    schema={"type": "object", "required": ["reply"]}, **kwargs)

    async def test_tool_result_is_returned_to_model_and_each_turn_is_charged(self):
        self.arrange([response(calls=[call("save_note", {"text": "hello"})]),
                      response({"reply": "done"})])
        answer = await self.ask(role="chat", tool_gateway=self.gateway)
        self.assertEqual(answer.data, {"reply": "done"})
        self.assertEqual(self.effects, ["hello"])
        self.assertEqual(self.requests[1]["messages"][-1],
                         {"role": "tool", "tool_call_id": "call_1", "content": '"saved"'})
        self.assertAlmostEqual(self.brain.spent, 0.0056)
        self.assertFalse(self.requests[0]["store"])

    async def test_unknown_tool_and_invalid_arguments_do_not_execute(self):
        self.arrange([response(calls=[call("not_registered", {})]),
                      response(calls=[call("save_note", {"extra": "wrong"})]),
                      response({"reply": "could not save"})])
        await self.ask(role="chat", tool_gateway=self.gateway)
        self.assertEqual(self.effects, [])
        self.assertIn("error", json.loads(self.requests[1]["messages"][-1]["content"]))
        self.assertIn("error", json.loads(self.requests[2]["messages"][-1]["content"]))

    async def test_background_has_no_tools(self):
        self.arrange([response({"reply": "analysis"})])
        await self.ask()
        self.assertNotIn("tools", self.requests[0])
        with self.assertRaises(BrainError):
            await self.ask(tool_gateway=self.gateway)

    async def test_budget_stops_before_executing_tool(self):
        self.brain.budget = 0.001
        self.arrange([response(calls=[call("save_note", {"text": "stop"})])])
        with self.assertRaises(BrainError):
            await self.ask(role="chat", tool_gateway=self.gateway)
        self.assertEqual(self.effects, [])
        self.assertGreater(self.brain.spent, self.brain.budget)

    async def test_round_limit_forces_final_answer(self):
        self.arrange([response(calls=[call("save_note", {"text": "step"})])
                      for _ in range(MAX_TOOL_ROUNDS)] + [response({"reply": "finished"})])
        await self.ask(role="chat", tool_gateway=self.gateway)
        self.assertEqual(len(self.effects), MAX_TOOL_ROUNDS)
        self.assertEqual(self.requests[-1]["tool_choice"], "none")

    async def test_transport_failure_after_dispatch_is_not_sent_back_for_retry(self):
        self.arrange([response(calls=[call("save_note", {"text": "hello"})])])
        self.gateway.call = AsyncMock(side_effect=ToolExecutionUncertain("uncertain"))
        with self.assertRaises(ToolExecutionUncertain):
            await self.ask(role="chat", tool_gateway=self.gateway)
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_final_shape_is_rejected(self):
        self.arrange([response({"other": "missing reply"})])
        with self.assertRaises(BrainError):
            await self.ask()


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "mcp.json"
        self.config.write_text(json.dumps({"mcpServers": {"ubermensch": {"env": {}}}}))
        self.fallback = AsyncMock(return_value=Answer({"reply": "fallback"}, 1))
        self.primary = SimpleNamespace(ask=AsyncMock(return_value=Answer({"reply": "primary"}, 1)))

    async def ask(self, watchdog=10, waiting=lambda: False):
        return await ChatRouter().ask(
            self.primary, "test", options={"mcp_config": self.config}, fallback=self.fallback,
            fallback_available=lambda: True, waiting_for_user=waiting, watchdog=watchdog)

    async def test_limit_switches_with_no_started_tools(self):
        self.primary.ask.side_effect = BrainError("limit")
        answer, engine = await self.ask()
        self.assertEqual(engine, "openai")
        self.assertEqual(answer.data["reply"], "fallback")
        self.fallback.assert_awaited_once()

    async def test_started_tool_prevents_replay_after_limit(self):
        async def primary(prompt, **options):
            data = json.loads(Path(options["mcp_config"]).read_text())
            path = Path(data["mcpServers"]["ubermensch"]["env"]["UBERMENSCH_TOOL_JOURNAL"])
            path.write_text('{"tool": "send", "status": "started"}\n')
            raise BrainError("limit")
        self.primary.ask.side_effect = primary
        with self.assertRaises(InterruptedToolRun):
            await self.ask()
        self.fallback.assert_not_awaited()

    async def test_watchdog_waits_for_primary_to_stop(self):
        stopped = asyncio.Event()

        async def primary(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def fallback(journal):
            self.assertTrue(stopped.is_set())
            return Answer({"reply": "safe"}, 1)

        self.primary.ask.side_effect = primary
        self.fallback.side_effect = fallback
        _, engine = await self.ask(watchdog=0)
        self.assertEqual(engine, "openai")

    async def test_outer_cancellation_cleans_up_primary(self):
        stopped = asyncio.Event()

        async def primary(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        self.primary.ask.side_effect = primary
        task = asyncio.create_task(self.ask())
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())
        self.fallback.assert_not_awaited()

    async def test_tool_dispatched_during_cancellation_prevents_replay(self):
        async def primary(prompt, **options):
            data = json.loads(Path(options["mcp_config"]).read_text())
            path = Path(data["mcpServers"]["ubermensch"]["env"]["UBERMENSCH_TOOL_JOURNAL"])
            try:
                await asyncio.Future()
            finally:
                path.write_text('{"tool": "send", "status": "dispatched"}\n')

        self.primary.ask.side_effect = primary
        with self.assertRaises(InterruptedToolRun):
            await self.ask(watchdog=0)
        self.fallback.assert_not_awaited()

    async def test_waiting_for_approval_suspends_watchdog(self):
        async def primary(*args, **kwargs):
            await asyncio.sleep(1.1)
            return Answer({"reply": "approved"}, 1)
        self.primary.ask.side_effect = primary
        _, engine = await self.ask(watchdog=0, waiting=lambda: True)
        self.assertEqual(engine, "claude")
        self.fallback.assert_not_awaited()


class ToolAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = Core.__new__(Core)
        self.core.tool_registry = load_registry()
        self.telegram = SimpleNamespace(
            chat_name=AsyncMock(return_value="Test person"), send=AsyncMock(),
            profile=SimpleNamespace(personal=True, label="personal"))
        self.core.tg = Mock(return_value=self.telegram)
        self.core.request_approval = AsyncMock(return_value=False)
        self.server = TestServer(create_app(self.core, "test-token"))
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        for setting, value in [("API_PORT", self.server.port), ("API_HOST", "127.0.0.1"),
                                ("DATA_DIR", Path(self.temp.name))]:
            patcher = patch.object(config, setting, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        token = patch.object(config, "api_token", return_value="test-token")
        token.start()
        self.addCleanup(token.stop)

    async def test_auth_and_same_catalog_for_mcp_and_http(self):
        unauthorized = await self.client.get("/api/tools")
        self.assertEqual(unauthorized.status, 401)
        gateway = await HttpToolGateway.connect()
        mcp_tools = await create_server().list_tools()
        self.assertEqual({t.name for t in gateway.definitions()}, {t.name for t in mcp_tools})
        self.assertIn("run_shell", {t.name for t in gateway.definitions()})

    async def test_read_text_through_real_api_and_journal(self):
        path = Path(self.temp.name) / "note.txt"
        path.write_text("hello world", encoding="utf-8")
        run_id = uuid4().hex
        gateway = await HttpToolGateway.connect(run_id=run_id)
        result = await gateway.call("read_file", {"path": str(path), "limit": 5})
        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["next_offset"], 5)
        records = (Path(self.temp.name) / "workspace" / "runs" / run_id / "tools.jsonl").read_text()
        self.assertIn('"status": "completed"', records)
        self.assertNotIn("hello", records)

    async def test_denied_send_never_reaches_telegram(self):
        gateway = await HttpToolGateway.connect()
        result = await gateway.call("telegram_send", {"chat_id": 42, "text": "test"})
        self.core.request_approval.assert_awaited_once()
        self.telegram.send.assert_not_awaited()
        self.assertIn("не подтвердил", result)

    async def test_mcp_adapter_uses_same_execution_api(self):
        path = Path(self.temp.name) / "note.txt"
        path.write_text("from MCP", encoding="utf-8")
        result = await create_server().call_tool("read_file", {"path": str(path)})
        self.assertFalse(result.is_error)
        self.assertIn("from MCP", str(result))

    async def test_bad_arguments_and_run_id_are_rejected_before_dispatch(self):
        gateway = await HttpToolGateway.connect(run_id="../../outside")
        with self.assertRaises(BrainError):
            await gateway.call("read_file", {"path": "unused"})
        gateway = await HttpToolGateway.connect()
        with self.assertRaises(BrainError):
            await gateway.call("run_shell", {"command": "must not run", "unexpected": True})
        self.assertFalse((Path(self.temp.name) / "workspace").exists())

    async def test_server_failure_after_effect_is_uncertain(self):
        effects = []
        registry = ToolRegistry()

        @registry.tool()
        async def partially_done(text: str):
            effects.append(text)
            raise ValueError("failed after changing state")

        self.core.tool_registry = registry
        gateway = await HttpToolGateway.connect()
        with self.assertRaises(ToolExecutionUncertain):
            await gateway.call("partially_done", {"text": "once"})
        self.assertEqual(effects, ["once"])

    async def test_limit_fallback_reads_via_api_and_applies_task_and_reminder(self):
        path = Path(self.temp.name) / "requested.txt"
        path.write_text("requested contents", encoding="utf-8")
        assistant = Assistant.__new__(Assistant)
        assistant.workspace = Path(self.temp.name) / "workspace"
        assistant.workspace.mkdir()
        assistant.mcp_config = assistant.workspace / "mcp.json"
        assistant.mcp_config.write_text(json.dumps({"mcpServers": {"ubermensch": {"env": {}}}}))
        assistant.schema = _schema()
        assistant._lock = asyncio.Lock()
        assistant.store = Store(":memory:")
        self.addCleanup(assistant.store.db.close)
        assistant.router = ChatRouter()
        assistant.waiting_for_user = lambda: False
        assistant.brain = SimpleNamespace(ask=AsyncMock(side_effect=BrainError("limit")))
        assistant._context = Mock(return_value="context")
        assistant._render_system_prompt = Mock(return_value="system")
        assistant._prompt_file = Mock(return_value=path)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_BUDGET_USD": "9"}):
            assistant.helper = OpenAIBrain(MemoryCursors(), Mock(), Mock())
        assistant.helper._models["chat"] = "gpt-4.1"
        final = {"reply": "requested contents", "emotion": "happy", "memory": [], "goals": [],
                 "people": [],
                 "tasks": [{"op": "add", "title": "test task"}],
                 "actions": [{"kind": "reminder", "summary": "test", "params": {
                     "at": (datetime.now() + timedelta(hours=1)).isoformat(), "text": "test"}}]}
        replies = [response(calls=[call("read_file", {"path": str(path)})]), response(final)]
        requests = []

        async def fake_api(session, method, endpoint, body=None):
            requests.append(copy.deepcopy(body))
            return replies.pop(0)

        assistant.helper._request = fake_api
        outcome = await assistant.handle("read, plan and remind", channel="desktop")
        self.assertEqual(outcome.engine, "openai")
        self.assertEqual(outcome.reply, "requested contents")
        self.assertEqual(outcome.emotion, "happy")
        self.assertIn("requested contents", requests[1]["messages"][-1]["content"])
        self.assertEqual(len(assistant.store.tasks()), 1)
        self.assertEqual(len(assistant.store.pending_reminders()), 1)


class FallbackOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_can_apply_reminder_actions(self):
        assistant = Assistant.__new__(Assistant)
        assistant._lock = asyncio.Lock()
        assistant.store = Store(":memory:")
        self.addCleanup(assistant.store.db.close)
        assistant._context = Mock(return_value="context")
        at = (datetime.now() + timedelta(hours=1)).isoformat()
        assistant._chat_ask = AsyncMock(return_value=(Answer({
            "reply": "reminder created", "actions": [{"kind": "reminder", "summary": "test",
                                                        "params": {"at": at, "text": "test"}}],
        }, 1), "openai"))
        outcome = await assistant.handle("remind me", channel="desktop")
        self.assertEqual(len(assistant.store.pending_reminders()), 1)
        self.assertEqual(outcome.engine, "openai")


if __name__ == "__main__":
    unittest.main()
