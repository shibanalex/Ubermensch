"""Хранилище Obsidian и задачи виджета: сбор, обновление заметок на месте, контекст, правка задач."""
from __future__ import annotations

import json
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from ubermensch import config
from ubermensch.api import create_app
from ubermensch.collectors import obsidian
from ubermensch.core import Bus, Core
from ubermensch.store import Store
from ubermensch.runtime import policy
from ubermensch.runtime.context import current


def _note(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ObsidianTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name) / "vault"
        _note(self.vault, "02 Journal/Daily/2026-10-17.md",
              "---\ntype: daily\nmood: 4\n---\n# 2026-10-17\nХороший день <!-- шаблон -->")
        _note(self.vault, "02 Journal/Daily/2026-10-18.md", "Не выспался.")
        _note(self.vault, "06 Life/Личные ориентиры.md", "Музыка важнее всего.")
        _note(self.vault, "08 AI/Обо мне.md", "Композитор и программист.")
        _note(self.vault, "08 AI/Reports/Профиль.md", "отчёт самой Ани")
        _note(self.vault, "90 Templates/Daily.md", "шаблон")
        _note(self.vault, "99 Assets/x.md", "вложение")
        _note(self.vault, "Clippings/статья.md", "чужой текст")
        _note(self.vault, "03 Music/Grieg.md", "---\nsource: \"https://youtube.com/x\"\n---\nописание")
        (self.vault / "09 Canvas").mkdir()
        (self.vault / "09 Canvas/Сферы.canvas").write_text(json.dumps({
            "nodes": [{"type": "text", "text": "Здоровье"}, {"type": "file", "file": "06 Life/x.md"}],
            "edges": [{"label": "важно"}]}), encoding="utf-8")
        for name, value in (("VAULT_DIR", self.vault),
                            ("REPORTS_DIR", self.vault / "08 AI" / "Reports")):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_collect_reads_whole_vault_and_marks_clippings(self):
        events = {e["external_id"]: e for e in obsidian.collect()}
        self.assertNotIn("08 AI/Reports/Профиль.md", events)
        self.assertIn("90 Templates/Daily.md", events)  # читает всё хранилище
        self.assertIn("99 Assets/x.md", events)
        self.assertEqual(events["Clippings/статья.md"]["outgoing"], 0)
        self.assertEqual(events["03 Music/Grieg.md"]["outgoing"], 0)  # Web Clipper: source http
        daily = events["02 Journal/Daily/2026-10-17.md"]
        self.assertEqual((daily["outgoing"], daily["ts"][:10]), (1, "2026-10-17"))
        self.assertEqual(daily["meta"]["mood"], "4")
        self.assertNotIn("шаблон", daily["text"])  # HTML-комментарии шаблона выброшены
        self.assertIn("Здоровье", events["09 Canvas/Сферы.canvas"]["text"])

    def test_edited_note_updates_in_place(self):
        store = Store(":memory:")
        self.addCleanup(store.db.close)
        self.assertEqual(store.sync_events(obsidian.collect()), 9)
        row = store.db.execute("SELECT id FROM events WHERE external_id='06 Life/Личные ориентиры.md'").fetchone()
        self.assertEqual(store.sync_events(obsidian.collect()), 0)  # без правок: без изменений
        _note(self.vault, "06 Life/Личные ориентиры.md", "Музыка и здоровье.")
        self.assertEqual(store.sync_events(obsidian.collect()), 1)
        updated = store.db.execute("SELECT id, text FROM events "
                                   "WHERE external_id='06 Life/Личные ориентиры.md'").fetchone()
        self.assertEqual(updated[0], row[0])
        self.assertIn("Музыка и здоровье.", updated[1])

    def test_context_notes_for_every_chat(self):
        text = obsidian.context_notes()
        for part in ("08 AI/Обо мне.md", "06 Life/Личные ориентиры.md",
                     "02 Journal/Daily/2026-10-18.md", "Не выспался."):
            self.assertIn(part, text)
        self.assertNotIn("type: daily", text)
        self.assertLessEqual(len(obsidian.context_notes(limit=40)), 40)

    def test_whole_vault_writable_except_obsidian_settings(self):
        # «у неё должен быть свободный доступ»: и в чате, и в фоне
        with patch.object(config, "VAULT_DIR", self.vault), \
                patch.object(config, "PROTECTED", (self.vault / ".obsidian",)):
            for mode in ("interactive", "background"):
                token = current.set(SimpleNamespace(mode=mode))
                try:
                    self.assertTrue(policy.writable(self.vault / "06 Life" / "x.md"), mode)
                    self.assertTrue(policy.writable(self.vault / "02 Journal" / "Daily" / "d.md"), mode)
                    self.assertFalse(policy.writable(self.vault / ".obsidian" / "app.json"), mode)
                finally:
                    current.reset(token)


class TaskWidgetAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = Core.__new__(Core)
        self.core.store = Store(":memory:")
        self.addCleanup(self.core.store.db.close)
        self.core.bus = Bus()
        self.client = TestClient(TestServer(create_app(self.core, "t")))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer t"}

    async def post(self, path: str, body: dict):
        return await self.client.post(path, json=body, headers=self.headers)

    async def test_edit_and_return_done_task(self):
        created = await (await self.post("/api/tasks", {"title": "Купить тетрадь"})).json()
        task = created["id"]
        response = await self.post(f"/api/tasks/{task}/edit", {"title": "Купить две тетради",
                                                               "due": "2026-10-20"})
        self.assertTrue((await response.json())["ok"])
        self.assertEqual((await self.post(f"/api/tasks/{task}/edit", {"due": "завтра"})).status, 400)
        await self.post(f"/api/tasks/{task}", {"status": "done"})
        listed = await (await self.client.get("/api/tasks", headers=self.headers)).json()
        self.assertEqual([(t["title"], t["status"]) for t in listed["tasks"]],
                         [("Купить две тетради", "done")])
        await self.post(f"/api/tasks/{task}", {"status": "open"})  # мисклик: вернуть
        listed = await (await self.client.get("/api/tasks", headers=self.headers)).json()
        self.assertEqual(listed["tasks"][0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
