"""Работа с системой и кодом: снимок экрана модели, точечные правки с откатом, проект в песочнице."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import config
from ubermensch.capabilities import computer, files
from ubermensch.claude_api import ClaudeApiBrain
from ubermensch.runtime import images
from ubermensch.runtime.context import current
from ubermensch.system import versions


class Cursors(dict):
    def cursor(self, key):
        return self.get(key)

    def set_cursor(self, key, value):
        self[key] = value


class CodeToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        patcher = patch.object(config, "DATA_DIR", self.root / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        work = self.root / "work"
        work.mkdir()
        self.work = work
        env = patch.dict(os.environ, {"UBERMENSCH_READ_ROOTS": str(work), "UBERMENSCH_WRITE_ROOTS": str(work)})
        env.start()
        self.addCleanup(env.stop)
        token = current.set(SimpleNamespace(mode="interactive", run_id="r", call_id="c",
                                            core=SimpleNamespace(request_approval=self._deny)))
        self.addCleanup(current.reset, token)

    async def _deny(self, *args):
        raise AssertionError("в разрешённой папке подтверждение не нужно")

    def test_only_screens_are_attached_as_images(self):
        screens = images.screens_dir()
        screens.mkdir(parents=True)
        (screens / "request.jpg").write_bytes(b"\xff\xd8jpeg")
        self.assertEqual(images.attached({"image_path": str(screens / "request.jpg")}),
                         ("image/jpeg", "/9hqcGVn"))
        elsewhere = self.work / "photo.jpg"
        elsewhere.write_bytes(b"\xff\xd8jpeg")
        self.assertIsNone(images.attached({"image_path": str(elsewhere)}))
        self.assertIsNone(images.attached({"image_path": str(screens / "../../work/photo.jpg")}))
        self.assertIsNone(images.attached("Снимок: /x.jpg"))
        with patch.object(images, "MAX_BYTES", 3):
            self.assertIsNone(images.attached({"image_path": str(screens / "request.jpg")}))

    async def test_claude_api_sees_the_screenshot_itself(self):
        screens = images.screens_dir()
        screens.mkdir(parents=True)
        (screens / "request.jpg").write_bytes(b"\xff\xd8jpeg")
        brain = ClaudeApiBrain(Cursors(), SimpleNamespace(clear=lambda *a: None, report=lambda *a: None),
                               lambda text: None)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        replies = [SimpleNamespace(stop_reason="tool_use", model="claude-haiku-5-5", usage=usage, content=[
                       SimpleNamespace(type="tool_use", id="t1", name="screenshot", input={})]),
                   SimpleNamespace(stop_reason="end_turn", model="claude-haiku-5-5", usage=usage,
                                   content=[SimpleNamespace(type="text", text='{"reply": "вижу"}')])]
        requests = []

        async def create(**request):
            requests.append(json.loads(json.dumps(request, default=lambda o: o.__dict__)))
            return replies.pop(0)
        brain.client = SimpleNamespace(messages=SimpleNamespace(create=create),
                                       beta=SimpleNamespace(messages=SimpleNamespace(create=create)))

        class Gateway:
            def definitions(self):
                return [SimpleNamespace(name="screenshot", description="d", parameters={"type": "object"})]

            async def call(self, name, arguments):
                return {"text": "Окно: Konsole", "image_path": str(screens / "request.jpg")}

        system = self.root / "system.md"
        system.write_text("s", encoding="utf-8")
        await brain.ask("что на экране?", system_prompt_file=system, role="background", tool_gateway=Gateway(),
                        schema={"type": "object", "required": ["reply"], "properties": {"reply": {"type": "string"}}})
        content = requests[1]["messages"][-1]["content"][0]["content"]
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["data"], "/9hqcGVn")

    async def test_edit_file_shows_diff_and_can_be_rolled_back(self):
        code = self.work / "app.py"
        code.write_text("def total(a, b):\n    return a - b\n\nprint(total(2, 3))\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            await files.edit_file(str(code), "nothing", "x")
        code.write_text(code.read_text() + "# return a - b\n", encoding="utf-8")
        with self.assertRaises(ValueError):  # фрагмент дважды: не угадываем
            await files.edit_file(str(code), "return a - b", "return a + b")
        result = await files.edit_file(str(code), "    return a - b\n", "    return a + b\n")
        self.assertIn("-    return a - b", result["diff"])
        self.assertIn("+    return a + b", result["diff"])
        self.assertIn("return a + b", code.read_text())
        listed = await files.file_versions(str(code))
        self.assertEqual(listed[0]["id"], result["version"])
        restored = await files.restore_file_version(result["version"])
        self.assertIn("return a - b\n\nprint", code.read_text())
        self.assertIsNotNone(restored["previous_saved_as"])  # откат тоже откатывается
        self.assertIn("return a + b", (versions.root() / restored["previous_saved_as"] / "app.py").read_text())

    async def test_write_file_keeps_previous_version(self):
        note = self.work / "note.md"
        note.write_text("старое", encoding="utf-8")
        self.assertIn("прежняя версия", await files.write_file(str(note), "новое"))
        self.assertEqual((versions.root() / versions.history(str(note))[0]["id"] / "note.md").read_text(), "старое")

    @unittest.skipUnless(shutil.which("bwrap"), "нужен bubblewrap")
    async def test_project_runs_read_only_without_secrets(self):
        project = self.work / "repo"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname='x'\n")
        (project / ".env").write_text("API_KEY=very-secret\n")
        (project / ".env.example").write_text("API_KEY=\n")
        (project / "secrets").mkdir()
        (project / "secrets" / "token").write_text("very-secret")
        out = await computer.project_run(str(project), "cat .env; cat .env.example; ls secrets | wc -l; "
                                         "touch new 2>&1 | tail -1", timeout=60)
        self.assertNotIn("very-secret", out)
        self.assertIn("API_KEY=", out)
        self.assertIn("Read-only", out)
        self.assertFalse((project / "new").exists())
        with self.assertRaises(ValueError):
            await computer.project_run(str(self.work), "ls")  # не проект
        with self.assertRaises(ValueError):
            await computer.project_run(str(Path.home()), "ls")


if __name__ == "__main__":
    unittest.main()
