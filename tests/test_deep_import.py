"""Живой чат с ИИ (он продолжает писать в Gemini): новая версия разбирается только по новым абзацам."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ubermensch import config, core as core_module
from ubermensch.assistant import Outcome
from ubermensch.core import Bus, Core
from ubermensch.store import Store


class DeepImportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, "OUTPUT_DIR", Path(self.temp.name) / "out")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.core = Core.__new__(Core)
        self.core.store = Store(":memory:")
        self.addCleanup(self.core.store.db.close)
        self.core.bus, self.core._brain_calls, self.core.notify = Bus(), 0, lambda text: None
        self.core.assistant = SimpleNamespace(distill=AsyncMock(return_value=(Outcome(reply="ok"), "")))
        self.path = Path(self.temp.name) / "Gemini — мастер-контекст.json"
        self.path.write_text("{}")

    async def run_version(self, paragraphs, digest):
        with patch.object(core_module.ingest, "aistudio_full", return_value=paragraphs):
            return await self.core.deep_import(self.path, digest, only_new=True)

    async def test_only_new_paragraphs_are_distilled(self):
        first = ["Он учится в Мосполитехе", "Академ оформлен 21.10"]
        await self.run_version(first, "v1")
        self.assertIn("Академ оформлен", self.core.assistant.distill.await_args.args[1])
        await self.run_version(first + ["Скрининг Higgsfield 26.10"], "v2")
        text = self.core.assistant.distill.await_args.args[1]
        self.assertEqual(text, "Скрининг Higgsfield 26.10")
        self.assertEqual(await self.run_version(first + ["скрининг  higgsfield 26.10"], "v3"),
                         f"📥 {self.path.name}: нового в чате нет")
        self.assertEqual(self.core.assistant.distill.await_count, 2)
        self.assertEqual(self.core.store.import_status("v3"), "done")

    async def test_failed_distill_does_not_mark_paragraphs_seen(self):
        from ubermensch.brain import BrainError
        self.core.assistant.distill.side_effect = BrainError("limit")
        await self.run_version(["Новое"], "v1")
        self.assertIsNone(self.core.store.cursor(f"distill_seen:{self.path.name}"))


if __name__ == "__main__":
    unittest.main()
