"""Контролируемые эксперименты по цели: гипотеза → согласие → действие → замеры → вывод.

Правила в коде, а не только в инструкции модели:
- subject='anya': меняется только способ работы ассистента (источник поиска, формат отчёта):
  такой эксперимент она начинает сама; subject='owner': меняются его действия или привычки:
  начинается только после его согласия (experiment_decide: только в разговоре).
- sensitive (сон, питание, тренировки, здоровье) всегда subject='owner'; без медицинских
  вмешательств и резких нагрузок: это проверяет и инструкция модели.
- вывод keep / adjust / drop: только при min_observations замерах; раньше; inconclusive
  или остановка. Скрытых экспериментов нет: всё видно в контексте разговора.
"""
from __future__ import annotations

from datetime import date

from .planning import day, encoded, minutes, required, stamp

EXPERIMENT_MIGRATION = """
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    subject TEXT NOT NULL CHECK(subject IN ('anya','owner')),
    sensitive INTEGER NOT NULL DEFAULT 0 CHECK(sensitive IN (0,1)),
    hypothesis TEXT NOT NULL, basis TEXT NOT NULL, change TEXT NOT NULL, metric TEXT NOT NULL,
    success TEXT NOT NULL, stop TEXT NOT NULL, cost_minutes INTEGER NOT NULL, risk TEXT NOT NULL,
    min_observations INTEGER NOT NULL CHECK(min_observations BETWEEN 1 AND 100),
    review_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed','active','declined','stopped','concluded')),
    consent TEXT NOT NULL DEFAULT '', verdict TEXT CHECK(verdict IN ('keep','adjust','drop','inconclusive')),
    result TEXT NOT NULL DEFAULT '', next_step TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE experiment_observations (
    id INTEGER PRIMARY KEY, experiment_id INTEGER NOT NULL REFERENCES experiments(id),
    observed_on TEXT NOT NULL, value TEXT NOT NULL, evidence TEXT NOT NULL, recorded_at TEXT NOT NULL,
    UNIQUE(experiment_id, observed_on, evidence)
);
"""

OPEN = ("proposed", "active")


class ExperimentStorage:
    def propose_experiment(self, goal_id, subject, sensitive, hypothesis, basis, change, metric,
                           success, stop, cost_minutes, risk, min_observations, review_on):
        self._planning_goal(goal_id)
        if subject not in {"anya", "owner"}:
            raise ValueError("subject: anya (её способ работы) или owner (его действия)")
        if sensitive and subject != "owner":
            raise ValueError("Сон, питание, тренировки и здоровье — эксперимент над ним: subject=owner")
        texts = [required(v, name, 2000) for v, name in (
            (hypothesis, "Гипотеза"), (basis, "Основание"), (change, "Что меняем"),
            (metric, "Как меряем"), (success, "Критерий успеха"), (stop, "Критерий остановки"),
            (risk, "Риски"))]
        cost_minutes = minutes(cost_minutes)
        if isinstance(min_observations, bool) or not isinstance(min_observations, int) \
                or not 1 <= min_observations <= 100:
            raise ValueError("Минимум замеров — целое от 1 до 100")
        review_on = day(review_on)
        # Свой способ работы ассистент меняет сама; его жизнь: только после подтверждения.
        status = "active" if subject == "anya" else "proposed"
        consent = "своё изменение Ани" if status == "active" else ""
        cursor = self.db.execute(
            "INSERT INTO experiments(goal_id,subject,sensitive,hypothesis,basis,change,metric,success,"
            "stop,cost_minutes,risk,min_observations,review_on,status,consent,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (goal_id, subject, int(bool(sensitive)), *texts, cost_minutes, min_observations, review_on,
             status, consent, stamp(), stamp()))
        return self.experiment(cursor.lastrowid)

    def experiment(self, identifier):
        row = self.db.execute("SELECT * FROM experiments WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Эксперимент не найден")
        result = dict(row)
        result["observations"] = [dict(r) for r in self.db.execute(
            "SELECT observed_on, value, evidence FROM experiment_observations WHERE experiment_id=? "
            "ORDER BY observed_on, id", (identifier,))]
        return result

    def experiments(self, goal_id=None, statuses=OPEN):
        marks = ",".join("?" * len(statuses))
        where = f"status IN ({marks})" + (" AND goal_id=?" if goal_id is not None else "")
        args = (*statuses, *((goal_id,) if goal_id is not None else ()))
        return [self.experiment(r[0]) for r in self.db.execute(
            f"SELECT id FROM experiments WHERE {where} ORDER BY id", args)]

    def decide_experiment(self, identifier, accept, consent):
        """Его решение по эксперименту над ним. Только из разговора."""
        item = self.experiment(identifier)
        if item["status"] != "proposed":
            raise ValueError("Решение принимается только по предложенному эксперименту")
        consent = required(consent, "Его слова о согласии или отказе", 1000)
        self.db.execute("UPDATE experiments SET status=?, consent=?, updated_at=? WHERE id=?",
                        ("active" if accept else "declined", consent, stamp(), identifier))
        return self.experiment(identifier)

    def observe_experiment(self, identifier, value, evidence, observed_on=None):
        item = self.experiment(identifier)
        if item["status"] != "active":
            raise ValueError("Замеры пишутся только в идущий эксперимент")
        observed_on = day(observed_on or date.today().isoformat(), past=True)
        self.db.execute(
            "INSERT INTO experiment_observations(experiment_id,observed_on,value,evidence,recorded_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(experiment_id,observed_on,evidence) DO UPDATE SET "
            "value=excluded.value", (identifier, observed_on, required(value, "Значение", 500),
                                     required(evidence, "Источник замера", 2000), stamp()))
        return self.experiment(identifier)

    def stop_experiment(self, identifier, reason):
        item = self.experiment(identifier)
        if item["status"] not in OPEN:
            raise ValueError("Эксперимент уже завершён")
        self.db.execute("UPDATE experiments SET status='stopped', result=?, updated_at=? WHERE id=?",
                        (required(reason, "Причина остановки", 2000), stamp(), identifier))
        return self.experiment(identifier)

    def conclude_experiment(self, identifier, verdict, result, next_step):
        """Вывод по данным: keep / adjust / drop; не раньше min_observations замеров."""
        item = self.experiment(identifier)
        if item["status"] != "active":
            raise ValueError("Вывод делается по идущему эксперименту")
        if verdict not in {"keep", "adjust", "drop", "inconclusive"}:
            raise ValueError("Вывод: keep, adjust, drop или inconclusive")
        if verdict != "inconclusive" and len(item["observations"]) < item["min_observations"]:
            raise ValueError(f"Замеров {len(item['observations'])} из {item['min_observations']}: "
                             "вывод пока только inconclusive")
        self.db.execute("UPDATE experiments SET status='concluded', verdict=?, result=?, next_step=?, "
                        "updated_at=? WHERE id=?",
                        (verdict, required(result, "Что показали замеры", 3000),
                         required(next_step, "Следующий шаг", 2000), stamp(), identifier))
        return self.experiment(identifier)


def compact(item: dict) -> dict:
    keys = ("id", "goal_id", "subject", "sensitive", "status", "hypothesis", "change", "metric",
            "success", "stop", "min_observations", "review_on", "verdict", "result")
    return {**{k: item[k] for k in keys}, "observations": len(item["observations"]),
            "last": item["observations"][-1] if item["observations"] else None}


def experiments_context(store) -> str:
    items = store.experiments()
    if not items:
        return ""
    return ("<experiments>\nИдущие и предложенные эксперименты. Предложенные над ним (owner) "
            "начинаются только после его явного согласия — тогда experiment_decide; сам он "
            "может и отказаться. Вывод — только по замерам.\n"
            + "\n".join(encoded(compact(i)) for i in items) + "\n</experiments>")
