"""Карта вариантов цели: какими путями к ней можно идти, что из этого ему доступно и подходит,
что уже пробовали и чем кончилось. Основа выбора шага — не случайный совет, а лучший из
доступных путей с учётом его истории.

Оценки — качественные (high / medium / low) с основанием: придуманные вероятности хуже
честного «средне, потому что…». Статус меняется только с основанием (evidence); отказ
владельца (rejected) не возвращается в работу без нового основания.
"""
from __future__ import annotations

from .planning import encoded, required, stamp

OPTIONS_MIGRATION = """
CREATE TABLE goal_options (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    title TEXT NOT NULL, description TEXT NOT NULL,
    availability TEXT NOT NULL, fit TEXT NOT NULL,
    payoff TEXT NOT NULL CHECK(payoff IN ('high','medium','low')),
    effort TEXT NOT NULL CHECK(effort IN ('high','medium','low')),
    risk TEXT NOT NULL, reversible INTEGER NOT NULL CHECK(reversible IN (0,1)),
    status TEXT NOT NULL DEFAULT 'untried'
        CHECK(status IN ('untried','trying','worked','failed','rejected')),
    evidence TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(goal_id, title)
);
"""

LEVELS = {"high", "medium", "low"}
STATUSES = {"untried", "trying", "worked", "failed", "rejected"}
RANK = {"high": 0, "medium": 1, "low": 2}


class OptionStorage:
    def save_option(self, goal_id, title, description, availability, fit, payoff, effort, risk,
                    reversible, evidence):
        """Добавить путь или уточнить его оценку. Статус и итог не трогаются."""
        self._planning_goal(goal_id)
        if payoff not in LEVELS or effort not in LEVELS:
            raise ValueError("payoff и effort — high, medium или low")
        texts = [required(v, name, 3000) for v, name in (
            (title, "Путь"), (description, "Что это"), (availability, "Доступно ли ему"),
            (fit, "Подходит ли ему"), (risk, "Риски"), (evidence, "Основание"))]
        self.db.execute(
            "INSERT INTO goal_options(goal_id,title,description,availability,fit,payoff,effort,risk,"
            "reversible,evidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(goal_id,title) DO UPDATE SET description=excluded.description,"
            "availability=excluded.availability, fit=excluded.fit, payoff=excluded.payoff,"
            "effort=excluded.effort, risk=excluded.risk, reversible=excluded.reversible,"
            "evidence=excluded.evidence, updated_at=excluded.updated_at",
            (goal_id, texts[0][:200], *texts[1:4], payoff, effort, texts[4], int(bool(reversible)),
             texts[5], stamp(), stamp()))
        return self.db.execute("SELECT id FROM goal_options WHERE goal_id=? AND title=?",
                               (goal_id, texts[0][:200])).fetchone()[0]

    def set_option_status(self, option_id, status, outcome, evidence, *, owner=False):
        """Пробуем / сработало / не сработало / отказ — с основанием. Отказ владельца
        отменяет только он сам (owner=True)."""
        if status not in STATUSES:
            raise ValueError("Статус: untried, trying, worked, failed, rejected")
        row = self.db.execute("SELECT * FROM goal_options WHERE id=?", (option_id,)).fetchone()
        if row is None:
            raise ValueError("Путь не найден")
        if row["status"] == "rejected" and status != "rejected" and not owner:
            raise ValueError("От этого пути он отказался — вернуть его может только он сам")
        self.db.execute("UPDATE goal_options SET status=?, outcome=?, evidence=?, updated_at=? WHERE id=?",
                        (status, required(outcome, "Итог / что видно", 3000),
                         required(evidence, "Основание", 3000), stamp(), option_id))

    def goal_options(self, goal_id):
        """Пути цели: сначала идущие и непробованные с высокой отдачей и малым усилием."""
        rows = [dict(r) for r in self.db.execute("SELECT * FROM goal_options WHERE goal_id=?", (goal_id,))]
        order = {"trying": 0, "untried": 1, "worked": 2, "failed": 3, "rejected": 4}
        return sorted(rows, key=lambda r: (order[r["status"]], RANK[r["payoff"]], -RANK[r["effort"]], r["id"]))


def compact(option: dict) -> dict:
    return {k: option[k] for k in ("id", "title", "status", "payoff", "effort", "availability", "fit",
                                   "risk", "reversible", "outcome")}


def map_text(options: list[dict]) -> str:
    return "\n".join(encoded(compact(o)) for o in options)


def options_context(store) -> str:
    """Карты путей активных целей для разговора: он спрашивает о вариантах — ответ по карте,
    его «не моё» или «сработало» — сразу в option_status."""
    lines = [f"#{goal['id']} " + encoded(compact(o)) for goal in store.goals()
             for o in store.goal_options(goal["id"])]
    if not lines:
        return ""
    return ("<options>\nКарта путей к его целям (#цель путь). Отвечай о вариантах по ней; его "
            "решение о пути (отказ, итог) запиши option_status с его словами в outcome. Новые пути — "
            "option_upsert с основанием.\n" + "\n".join(lines) + "\n</options>")
