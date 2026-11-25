"""Эксперименты: согласие владельца, замеры, вывод только по данным, границы фона."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from ubermensch import experiments, goal_loop
from ubermensch.capabilities import experiments as tools
from ubermensch.capabilities.registry import ToolArgumentsError
from ubermensch.runtime import policy
from ubermensch.runtime.context import current
from ubermensch.store import Store

TODAY = date.today().isoformat()


class ExperimentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.goal = self.store.add_goal("career", "Удалённая работа", "", None)

    def propose(self, subject="owner", sensitive=False, minimum=3):
        return self.store.propose_experiment(
            self.goal, subject, sensitive, "Отклики утром дают больше ответов",
            "Ответы чаще приходили на отклики до 12:00 (воронка)", "Откликаться до 12:00",
            "Доля ответов через 7 дней", "≥ 15% ответов", "2 недели без ответов", 60,
            "Нет: меняется только время", minimum, (date.today() + timedelta(days=14)).isoformat())

    def context(self, mode):
        token = current.set(SimpleNamespace(core=SimpleNamespace(store=self.store), mode=mode,
                                            run_id="r", call_id="c"))
        self.addCleanup(current.reset, token)

    def test_own_method_starts_at_once_owner_change_waits_for_consent(self):
        mine = self.propose(subject="anya")
        self.assertEqual((mine["status"], mine["consent"]), ("active", "своё изменение Ани"))
        his = self.propose()
        self.assertEqual(his["status"], "proposed")
        with self.assertRaises(ValueError):  # без согласия замеров нет
            self.store.observe_experiment(his["id"], "10%", "воронка")
        with self.assertRaises(ValueError):  # сон, питание, тренировки: только над ним
            self.propose(subject="anya", sensitive=True)
        declined = self.store.decide_experiment(self.propose()["id"], False, "не хочу менять график")
        self.assertEqual(declined["status"], "declined")
        with self.assertRaises(ValueError):  # решение принимается один раз
            self.store.decide_experiment(declined["id"], True, "передумал")

    def test_background_cannot_consent_for_him(self):
        self.context("background")
        with self.assertRaises(PermissionError):
            policy.check_tool("experiment_decide")
        policy.check_tool("experiment_propose")  # предлагать можно

    async def test_verdict_only_after_enough_observations(self):
        item = self.store.decide_experiment(self.propose(minimum=2)["id"], True, "давай, две недели")
        self.store.observe_experiment(item["id"], "12%", "воронка hh, 7 дней", TODAY)
        with self.assertRaises(ValueError):
            self.store.conclude_experiment(item["id"], "keep", "лучше", "оставить")
        with self.assertRaises(ValueError):  # замер не из будущего
            self.store.observe_experiment(item["id"], "20%", "прогноз",
                                          (date.today() + timedelta(days=1)).isoformat())
        self.store.observe_experiment(item["id"], "18%", "воронка hh, 14 дней", TODAY)
        done = self.store.conclude_experiment(item["id"], "keep", "12% → 18% на 40 откликах", "Оставить утро")
        self.assertEqual((done["status"], done["verdict"]), ("concluded", "keep"))
        early = self.store.decide_experiment(self.propose()["id"], True, "ок")
        self.assertEqual(self.store.conclude_experiment(early["id"], "inconclusive", "данных нет",
                                                        "Повторить позже")["verdict"], "inconclusive")

    async def test_background_measures_but_does_not_close_his_experiment(self):
        his = self.store.decide_experiment(self.propose()["id"], True, "согласен")
        mine = self.propose(subject="anya", minimum=1)
        self.context("background")
        await tools.experiment_observe(his["id"], "11%", "воронка", TODAY)
        with self.assertRaises(ToolArgumentsError):
            await tools.experiment_stop(his["id"], "надоело")
        with self.assertRaises(ToolArgumentsError):
            await tools.experiment_conclude(his["id"], "inconclusive", "мало", "ждать")
        await tools.experiment_observe(mine["id"], "3 вакансии", "career_search", TODAY)
        self.assertEqual((await tools.experiment_conclude(mine["id"], "keep", "работает", "дальше"))["status"],
                         "concluded")

    def test_experiments_are_visible_in_chat_and_in_the_goal_step(self):
        self.propose()
        context = experiments.experiments_context(self.store)
        self.assertIn("Отклики утром", context)
        self.assertIn("только после его явного согласия", context)
        self.store.save_goal_plan(self.goal, "Ответы", "шт", 0, 5, 60, "Откликнуться", "воронка")
        plan = self.store.measured_goals()[0]
        task = goal_loop.step_task(dict(self.store._planning_goal(self.goal)), plan, [], [], None,
                                   self.store.experiments(self.goal))
        self.assertIn("Откликаться до 12:00", task)
        self.assertIn("subject=owner (начнётся после его «да»)", task)


if __name__ == "__main__":
    unittest.main()
