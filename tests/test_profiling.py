"""Профиль: окна, проверка опор и цитат, сборка разделов и полный прогон с подставным мозгом."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubermensch import behavior, config, profiling
from ubermensch.brain import Answer, BrainError
from ubermensch.personalization import context_for, load_profile
from ubermensch.profile_corpus import prepare_corpus
from ubermensch.store import Store


def _seed(store: Store) -> None:
    start = datetime(2026, 9, 1, 9, 0)
    events = []
    for day in range(10):
        morning, night = start + timedelta(days=day), start + timedelta(days=day, hours=15)
        events += [
            {"source": "telegram", "external_id": f"o{day}", "ts": morning.isoformat(),
             "thread": "Иван", "outgoing": 1, "text": f"Сегодня опять не выспался, день {day}"},
            {"source": "telegram", "external_id": f"i{day}", "ts": (morning + timedelta(minutes=5)).isoformat(),
             "thread": "Иван", "author": "Иван", "text": "Ложись раньше"},
            {"source": "google_search", "external_id": f"s{day}", "ts": night.isoformat(),
             "text": "как перестать откладывать"},
            {"source": "youtube", "external_id": f"y{day}", "ts": night.isoformat(),
             "text": "видео без смысла"},
        ]
    store.add_events(events)
    store.add_fact("profile", "Учится в Московском Политехе", 4, "test")
    store.add_goal("learning", "Сдать сессию без долгов", "", None)


class FakeBrain:
    """Отвечает по виду задачи: наблюдения ссылаются на записи 1–2, раздел — на o1."""

    def __init__(self):
        self.calls = []

    async def ask(self, prompt: str, *, schema, **kwargs) -> Answer:
        self.calls.append(prompt)
        if schema is profiling.MAP_SCHEMA:
            data = {"observations": [
                {"section": "body", "text": "Жалуется, что не высыпается", "kind": "explicit_statement",
                 "confidence": "high", "refs": [1, 99], "quote": "опять не выспался",
                 "quote_ref": 1},
                {"section": "psyche", "text": "Без опоры", "kind": "hypothesis",
                 "confidence": "low", "refs": [0, 1000], "quote": "", "quote_ref": 0},
            ]} if "Telegram" in prompt else {"observations": []}
        elif schema is profiling.SECTION_SCHEMA:
            data = {"summary": "Мало спит.", "claims": [
                {"text": "Регулярно не высыпается", "kind": "explicit_statement",
                 "confidence": "high", "as_of": "2026-09", "basis": ["o1", "o404"],
                 "contradictions": [], "implication": "Не ставить дела на раннее утро",
                 "needs_confirmation": False},
                {"text": "Без оснований", "kind": "hypothesis", "confidence": "low", "as_of": "",
                 "basis": ["o999"], "contradictions": [], "implication": "",
                 "needs_confirmation": True}],
                "unknowns": ["Нет данных о тренировках"], "questions": ["Как ты спишь?"]}
        else:
            data = {"rules": [
                {"scope": "планирование", "when": "Планирует утро", "action": "Начинать не раньше 10",
                 "avoid": "Будить в 7", "basis": ["body.01"]},
                {"scope": "чат", "when": "x", "action": "y", "avoid": "", "basis": ["nope.01"]}],
                "open_questions": ["Что для тебя главное?"], "recent_changes": [],
                "summary": "Студент, которому не хватает сна."}
        return Answer(data=data, duration_ms=1)

    async def wait_out(self, error: BrainError) -> bool:
        return False


class ProfilingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "db").mkdir()
        (self.root / "workspace").mkdir()
        self.db_path = self.root / "db" / "ubermensch.db"
        self.store = Store(self.db_path)
        self.addCleanup(self.store.db.close)
        _seed(self.store)
        for name, value in (("DATA_DIR", self.root), ("OUTPUT_DIR", self.root / "docs")):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def corpus(self) -> Path:
        target = self.root / "corpus"
        prepare_corpus(self.db_path, target, derive=behavior.derive)
        return target

    def test_windows_skip_bulk_sources_but_keep_metrics(self):
        windows = profiling.build_windows(self.corpus())
        sources = {w.source for w in windows}
        self.assertIn("telegram", sources)
        self.assertIn("metrics", sources)
        self.assertNotIn("youtube", sources)  # просмотры — только через метрики
        self.assertNotIn("google_search", sources)
        telegram = next(w for w in windows if w.source == "telegram")
        self.assertIn("Я: Сегодня опять не выспался", telegram.text)
        self.assertTrue(all(ref.startswith("event:") for ref in telegram.refs))

    def test_observations_keep_only_real_refs_and_verbatim_quotes(self):
        window = next(w for w in profiling.build_windows(self.corpus()) if w.source == "telegram")
        cleaned = profiling.clean_observations(window, [
            {"section": "body", "text": "a", "kind": "explicit_statement", "confidence": "high",
             "refs": [1, 99], "quote": "опять  не выспался", "quote_ref": 1},
            {"section": "body", "text": "b", "kind": "observed_behavior", "confidence": "high",
             "refs": [2], "quote": "придуманная цитата", "quote_ref": 2},
            {"section": "values", "text": "c", "kind": "hypothesis", "confidence": "low",
             "refs": [500], "quote": "", "quote_ref": 0},
            {"section": "unknown", "text": "d", "kind": "hypothesis", "confidence": "low",
             "refs": [1], "quote": "", "quote_ref": 0},
        ])
        self.assertEqual([o["text"] for o in cleaned], ["a", "b"])
        self.assertEqual(cleaned[0]["refs"], [window.refs[0]])
        self.assertEqual(cleaned[0]["quote"]["ref"], window.refs[0])
        self.assertNotIn("quote", cleaned[1])

    def test_metrics_estimate_sleep_without_youtube(self):
        db = sqlite3.connect(self.db_path)
        self.addCleanup(db.close)
        rhythm = [i for i in behavior.derive(db) if i["ref"] == "metric:rhythm:2026-09"]
        self.assertEqual(len(rhythm), 1)
        self.assertIn("Активных действий", rhythm[0]["text"])

    async def test_full_build_publishes_grounded_profile(self):
        brain = FakeBrain()
        settings = SimpleNamespace(workspace=self.root / "workspace", db_path=self.db_path)
        assistant = SimpleNamespace(brain=brain, _analysis_lock=asyncio.Lock())
        investigation = SimpleNamespace(_last_chat=datetime.min, _pause=lambda: 0)
        notes = []
        profiler = profiling.Profiler(settings, self.store, assistant, investigation, notes.append)
        target = await profiler.build()

        profile = load_profile(self.root)
        self.assertIsNotNone(profile)
        self.assertTrue(target.is_dir())
        body = next(s for s in profile.sections if s.id == "body")
        self.assertEqual([c.text for c in body.claims], ["Регулярно не высыпается"])
        claim = body.claims[0]
        self.assertTrue(claim.evidence_refs and all(r.startswith("event:") for r in claim.evidence_refs))
        self.assertEqual(claim.evidence_quotes[0].quote, "опять не выспался")
        self.assertEqual([r.basis_claim_ids for r in profile.support_rules], [["body.01"]])
        self.assertIn("Не ставить дела на раннее утро", context_for(self.root, "утро"))
        self.assertTrue((self.root / "docs" / "Профиль.md").exists())
        self.assertTrue(notes and "Профиль обновлён" in notes[0])

        # Перестройка: наблюдения берутся из кеша, новых вызовов наблюдений нет.
        mapped = sum(1 for p in brain.calls if "наблюдения для профиля" in p)
        brain.calls.clear()
        await profiler.build()
        self.assertEqual(sum(1 for p in brain.calls if "наблюдения для профиля" in p), 0)
        self.assertGreater(mapped, 0)
        self.assertEqual(len(list((self.root / "personalization" / "corpus").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
