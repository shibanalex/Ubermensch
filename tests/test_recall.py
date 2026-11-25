"""Память по основам слов и подбор прошлого опыта по теме запроса."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from ubermensch import recall
from ubermensch.store import Store


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)

    def test_word_forms_share_a_stem(self):
        self.assertEqual(recall.stems("что там с общежитием"), ["общежи"])
        # совпадение: по началу слова, как в поиске
        self.assertEqual(recall.overlap(recall.stems("общежитием"), "место в общежитии"), 1)
        self.assertEqual(recall.overlap(recall.stems("откликаюсь"), "откладывает отклики"), 1)
        self.assertEqual(recall.stems("и в 2026 он"), [])
        self.assertEqual(recall.stems("на hh и в cv"), ["hh", "cv"])
        self.assertEqual(recall.fts_query(""), "")

    def test_facts_are_found_across_word_forms(self):
        fact = self.store.add_fact("career", "Иван бросил курс по алгоритмам на третьей неделе", 1, "x")
        for n in range(30):
            self.store.add_fact("profile", f"Другой важный факт {n}", 5, "x")
        found = [r["id"] for r in self.store.relevant_facts("стоит ли снова проходить курсы алгоритмов", 5)]
        self.assertIn(fact, found)

    def test_related_experience_brings_past_attempts_and_outcomes(self):
        goal = self.store.add_goal("career", "Удалённая работа Python", "", None)
        self.store.record_goal_cycle(goal, "step", "blocked", action="Искала вакансии на hh",
                                     blocker="hh отказал в доступе к вакансиям (403)")
        self.store.record_goal_cycle(goal, "step", "done", action="Прочитала почту", result="ничего")
        rec = self.store.save_recommendation(goal, "Откликаться на вакансии hh вечером", "x", "x", "x", 30,
                                             "2026-12-01")
        self.store.recommendation_feedback(rec, "declined", "вечером нет сил")
        tried = recall.related_experience(self.store, "давай поищем вакансии на hh")
        kinds = [(t["kind"], t["status"]) for t in tried]
        self.assertIn(("шаг по цели", "blocked"), kinds)
        self.assertIn(("совет", "declined"), kinds)
        self.assertTrue(any("403" in t["blocker"] for t in tried if t["kind"] == "шаг по цели"))
        self.assertNotIn("Прочитала почту", [t["what"] for t in tried])
        self.assertEqual(recall.related_experience(self.store, "какая погода"), [])

    def test_memory_benchmark_meets_thresholds(self):
        path = Path(__file__).resolve().parents[1] / "scripts/evaluate_memory.py"
        spec = importlib.util.spec_from_file_location("evaluate_memory", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, result in module.evaluate()["fixtures"].items():
            self.assertGreaterEqual(result["hit_at_5"], result["threshold"], (name, result["misses"]))


if __name__ == "__main__":
    unittest.main()
