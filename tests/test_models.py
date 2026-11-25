"""Уровни моделей по значимости задачи: кто на чём работает и что получает помощник-сканер."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ubermensch import brain as brain_module
from ubermensch.assistant import Assistant
from ubermensch.brain import Answer, Brain, MCP_SERVER
from ubermensch.capabilities import load_registry
from ubermensch.runtime import models
from ubermensch.runtime.policy import BACKGROUND_TOOLS


class TierTests(unittest.TestCase):
    def test_significance_decides_model(self):
        self.assertEqual(models.cli("goal_step"), (None, None))  # основная (opus) на подписке
        self.assertEqual(models.cli("heartbeat")[0], "haiku")
        self.assertEqual(models.cli("analysis")[0], "sonnet")
        self.assertEqual(models.api_role("goal_step"), "chat")  # запасной: Sonnet, а не Haiku
        self.assertEqual(models.api_role("heartbeat"), "background")
        self.assertEqual(models.tier("неизвестное"), models.STRONG)

    def test_scanner_only_for_strong_and_only_reads(self):
        self.assertIsNone(models.helpers("heartbeat"))
        scanner = json.loads(models.helpers("chat"))["scanner"]
        self.assertEqual(scanner["model"], "haiku")
        names = {d.name for d in load_registry().definitions()}
        prefix = f"mcp__{MCP_SERVER}__"
        own = [t[len(prefix):] for t in scanner["tools"] if t.startswith(prefix)]
        self.assertTrue(set(own) <= names, set(own) - names)
        self.assertTrue(set(own) <= BACKGROUND_TOOLS)  # ничего сверх фоновых прав
        for forbidden in ("write_file", "email_send", "telegram_send", "run_shell", "delete_to_recycle_bin"):
            self.assertNotIn(forbidden, own)


class DeepModeTests(unittest.TestCase):
    def test_important_topics_go_deep_small_talk_does_not(self):
        for text in ("Все плохо ань. Мне отказали, нужно заключение ВКК",
                     "Сколько просить на оффере?", "Подумай как следует, куда вложить 4 млн",
                     "банк заблокировал карту"):
            self.assertTrue(models.deep(text), text)
        for text in ("спок нок котя", "наглая рожа утютю~", "как дела?", "включи Баха"):
            self.assertFalse(models.deep(text), text)
        self.assertEqual(models.cli("chat_deep"), (None, "high"))


class CliArgsTests(unittest.IsolatedAsyncioTestCase):
    async def test_agents_flag_and_agent_tool(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = SimpleNamespace(claude_bin="/bin/true", claude_model="opus", claude_timeout=60,
                                   workspace=Path(temp.name))
        captured = {}

        async def spawn(*args, **kwargs):
            captured["args"] = args
            raise FileNotFoundError

        brain = Brain(settings)
        with patch.object(brain_module.asyncio, "create_subprocess_exec", spawn), \
                patch.object(Path, "exists", return_value=True):
            with self.assertRaises(Exception):
                await brain._ask("x", system_prompt_file=Path("p"), schema={}, tools=("WebSearch",),
                                 agents=models.helpers("chat"))
        args = list(captured["args"])
        self.assertIn("--agents", args)
        self.assertIn("Agent", args[args.index("--tools") + 1].split(","))


class FallbackRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_goal_step_fallback_uses_chat_role(self):
        assistant = Assistant.__new__(Assistant)
        assistant.workspace = Path(tempfile.mkdtemp())
        assistant._render_system_prompt = lambda telegram=False: "system"
        api = SimpleNamespace(ask=AsyncMock(return_value=Answer({"reply": "ok"}, 1)))
        gateway = SimpleNamespace(definitions=lambda: [])
        with patch("ubermensch.assistant.HttpToolGateway.connect", AsyncMock(return_value=gateway)), \
                patch("ubermensch.assistant.JournalToolGateway", lambda inner, journal, allowed=None: inner), \
                patch("ubermensch.assistant.PROMPTS", Path(assistant.workspace)):
            (Path(assistant.workspace) / "autonomy.md").write_text("a")
            journal = SimpleNamespace(path=Path(assistant.workspace) / "r" / "tools.jsonl", started=False)
            for kind, role in (("goal_step", "chat"), ("heartbeat", "background")):
                await assistant._api_ask("p", False, journal, schema={}, timeout=5, background=True,
                                         candidates=[("claude_api", api)], kind=kind)
                self.assertEqual(api.ask.await_args.kwargs["role"], role)


if __name__ == "__main__":
    unittest.main()
