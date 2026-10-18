"""Интервью и поправки к профилю: порядок вопросов, расписание, ответы, неверные выводы."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ubermensch import config, interview
from ubermensch.personalization import (AnalysisClaim, AnalysisSection, SupportRule, UserProfile,
                                        context_for, save_profile)
from ubermensch.store import Store


def _claim(claim_id: str, text: str, confirm: bool = False) -> AnalysisClaim:
    return AnalysisClaim(id=claim_id, text=text, kind="hypothesis", confidence="medium",
                         evidence_refs=["event:1"], needs_confirmation=confirm,
                         assistant_implication=f"следствие {claim_id}")


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(config, "DATA_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        profile = UserProfile(
            profile_id="p1", generated_at="2026-10-18", data_cutoff_at="2026-10",
            status="complete_for_snapshot", coverage={},
            sections=[AnalysisSection(id="values", title="Ценности", claims=[
                _claim("values.01", "Музыка для него важнее учёбы", confirm=True),
                _claim("values.02", "Ценит свободу распорядка")],
                questions=["Что для тебя значит успех?"])],
            support_rules=[SupportRule(id="rule.01", when="планирует день", action="не давить",
                                       basis_claim_ids=["values.02"], scope="планирование")],
            open_questions=["Чего ты хочешь через год?"])
        save_profile(self.root, profile, available_refs=["event:1"])
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.notes = []
        self.interviewer = interview.Interviewer(self.store, self.notes.append)

    def test_questions_in_priority_order_without_repeats(self):
        asked = [self.interviewer.ask() for _ in range(4)]
        self.assertEqual(asked[:3], ["Чего ты хочешь через год?",
                                     "Проверяю свой вывод о тебе: «Музыка для него важнее "
                                     "учёбы». Это так?",
                                     "Что для тебя значит успех?"])
        self.assertIsNone(asked[3])
        self.assertTrue(self.notes[0].startswith("🧭"))

    def test_schedule_respects_hours_gap_and_contact(self):
        day = datetime(2026, 10, 18, 15, 0)
        self.assertFalse(self.interviewer._due(day))  # он давно не писал
        self.store.add_message("user", "привет", "tg")
        now = datetime.now().replace(hour=15)
        self.assertTrue(self.interviewer._due(now))
        self.assertFalse(self.interviewer._due(now.replace(hour=23)))
        self.interviewer.ask()
        self.assertFalse(self.interviewer._due(now + timedelta(hours=21)))  # прошлый без ответа
        interview.record_answer("Хочу записать альбом")
        self.assertEqual(interview._read("interview.json")[-1]["answer"], "Хочу записать альбом")

    def test_wrong_claim_leaves_context_and_its_rules(self):
        before = context_for(self.root)
        self.assertIn("Ценит свободу распорядка", before)
        self.assertIn("не давить", before)
        interview.add_feedback("values.02", "wrong", "мне нужен чёткий режим")
        interview.add_feedback("values.01", "right")
        after = context_for(self.root)
        self.assertNotIn("Ценит свободу распорядка", after.split("recent_changes")[0])
        self.assertNotIn("не давить", after)
        self.assertIn("мне нужен чёткий режим", after)
        evidence = interview.feedback_evidence()
        self.assertEqual([e["ref"] for e in evidence], ["feedback:1", "feedback:2"])
        self.assertIn("неверно", evidence[0]["text"])
        remaining = iter(self.interviewer.ask, None)  # проверенный вывод больше не спрашивает
        self.assertEqual([q for q in remaining if q.startswith("Проверяю")], [])
        with self.assertRaises(KeyError):
            interview.add_feedback("values.99", "wrong")


if __name__ == "__main__":
    unittest.main()
