"""Цикл цели: утверждённый план → следующий шаг → действие инструментами → проверка → новый шаг.

Без плана Аня предлагает измеримый план и ждёт решения пользователя: показатель, исходную
точку и цель меняет только он (goal_plan в разговоре). С планом — делает следующий шаг сама
в пределах фоновых прав. «Сделано» засчитывается, только если в журнале этого запуска есть
успешные вызовы инструментов; иначе заход «не подтверждён» и следующий шаг не сдвигается.

Обучение на результатах: вечерняя рефлексия видит счёт эффективности (effectiveness) и
заходы дня и записывает по каждой цели стратегию — что работает, что нет и какую гипотезу
проверить следующим шагом (goal_strategy); следующий заход получает её в <strategy>.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from .experiments import compact
from .planning import encoded, required, stamp

GOAL_LOOP_MIGRATION = """
CREATE TABLE goal_cycles (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    run_id TEXT REFERENCES runs(id),
    kind TEXT NOT NULL CHECK(kind IN ('plan','step')),
    status TEXT NOT NULL CHECK(status IN ('done','unverified','blocked','needs_user','failed')),
    action TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
    next_step TEXT NOT NULL DEFAULT '', blocker TEXT NOT NULL DEFAULT '',
    ask_user TEXT NOT NULL DEFAULT '', proposal TEXT, evidence TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX goal_cycles_goal ON goal_cycles(goal_id, id);
"""
GOAL_STRATEGY_MIGRATION = """
CREATE TABLE goal_strategy (
    goal_id INTEGER PRIMARY KEY REFERENCES goals(id),
    works TEXT NOT NULL, fails TEXT NOT NULL, hypothesis TEXT NOT NULL, evidence TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

ASK_AGAIN_AFTER = timedelta(days=3)  # не переспрашивать о том же чаще
HISTORY = 5  # столько прошлых заходов по цели видит следующий

STEP_EXTRA = {
    "outcome": {"type": "string", "enum": ["done", "blocked", "needs_user"],
                "description": "done — шаг сделан и проверен; blocked — не вышло, причина в "
                               "blocker; needs_user — нужно его решение или данные (ask_user)"},
    "action": {"type": "string", "description": "что ты сделала, по шагам"},
    "result": {"type": "string", "description": "что получилось: факты, ссылки, пути к файлам"},
    "next_step": {"type": "string", "description": "следующий конкретный шаг к цели"},
    "blocker": {"type": "string", "description": "что мешает (для blocked), иначе пусто"},
    "ask_user": {"type": "string", "description": "вопрос или решение, нужное от него, иначе пусто"},
}
PLAN_EXTRA = {
    "proposal": {"type": "object", "additionalProperties": False,
                 "required": ["metric", "unit", "baseline", "target", "weekly_minutes",
                              "next_step", "evidence"],
                 "properties": {
                     "metric": {"type": "string"}, "unit": {"type": "string"},
                     "baseline": {"type": ["number", "null"],
                                  "description": "текущее значение из его данных; null — неизвестно"},
                     "target": {"type": "number"}, "weekly_minutes": {"type": "integer"},
                     "next_step": {"type": "string"},
                     "evidence": {"type": "string", "description": "на чём основан план: его данные"}}},
    "ask_user": {"type": "string", "description": "что уточнить у него, чтобы утвердить план"},
}


class GoalLoopStorage:
    def goal_strategy(self, goal_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM goal_strategy WHERE goal_id=?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def set_goal_strategy(self, goal_id: int, works: str, fails: str, hypothesis: str,
                          evidence: str) -> None:
        """Стратегия цели из рефлексии. Без основания (evidence) не сохраняется."""
        self._planning_goal(goal_id)
        evidence = required(evidence, "Основание стратегии", 3000)
        self.db.execute(
            "INSERT INTO goal_strategy VALUES (?,?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET "
            "works=excluded.works, fails=excluded.fails, hypothesis=excluded.hypothesis, "
            "evidence=excluded.evidence, updated_at=excluded.updated_at",
            (goal_id, works.strip()[:3000], fails.strip()[:3000], hypothesis.strip()[:2000],
             evidence, stamp()))

    def effectiveness(self, days: int = 7) -> dict:
        """Счёт самой Ани: что из её работы подтвердилось результатом. Только наблюдения."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        steps = Counter(dict(self.db.execute(
            "SELECT status, COUNT(*) FROM goal_cycles WHERE kind='step' AND created_at>=? "
            "GROUP BY status", (since,)).fetchall()))
        plans = Counter(dict(self.db.execute(
            "SELECT status, COUNT(*) FROM goal_cycles WHERE kind='plan' AND created_at>=? "
            "GROUP BY status", (since,)).fetchall()))
        blockers = Counter(" ".join(r[0].casefold().split())[:100] for r in self.db.execute(
            "SELECT blocker FROM goal_cycles WHERE blocker!='' AND created_at>=?", (since,)))
        recommendations = dict(self.db.execute(
            "SELECT status, COUNT(*) FROM recommendations WHERE updated_at>=? GROUP BY status",
            (since,)).fetchall())
        quality = dict(self.db.execute(
            "SELECT rating, COUNT(*) FROM quality_feedback WHERE created_at>=? GROUP BY rating",
            (since,)).fetchall())
        attempted = sum(steps.values())
        return {
            "days": days,
            "goal_steps": dict(steps), "goal_plans": dict(plans),
            "verified_step_share": round(steps["done"] / attempted, 2) if attempted else None,
            "repeated_blockers": [b for b, n in blockers.most_common(5) if n > 1],
            "recommendations": recommendations, "answer_ratings": quality,
            "note": "Доля подтверждённых шагов — по журналу вызовов, не по словам модели. "
                    "Малые числа не доказывают, что подход работает.",
        }

    def goal_cycles(self, goal_id: int | None = None, limit: int = HISTORY) -> list[dict]:
        where, args = ("WHERE goal_id=?", (goal_id,)) if goal_id is not None else ("", ())
        rows = self.db.execute(f"SELECT * FROM goal_cycles {where} ORDER BY id DESC LIMIT ?",
                               (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def record_goal_cycle(self, goal_id: int, kind: str, status: str, *, run_id: str | None = None,
                          action: str = "", result: str = "", next_step: str = "", blocker: str = "",
                          ask_user: str = "", proposal: dict | None = None,
                          evidence: list | None = None) -> int:
        self._planning_goal(goal_id)
        cursor = self.db.execute(
            "INSERT INTO goal_cycles(goal_id,run_id,kind,status,action,result,next_step,blocker,"
            "ask_user,proposal,evidence,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (goal_id, run_id, kind, status, action[:4000], result[:6000], next_step[:2000],
             blocker[:2000], ask_user[:2000], encoded(proposal) if proposal else None,
             encoded(evidence or []), stamp()))
        return cursor.lastrowid

    def advance_goal_step(self, goal_id: int, next_step: str) -> None:
        """Следующий шаг — рабочая часть плана в утверждённом направлении: его двигает и фон.
        Показатель, единица, исходная точка и цель не трогаются."""
        next_step = required(next_step, "Следующий шаг", 2000)
        self.db.execute("UPDATE goal_plans SET next_step=?, updated_at=? WHERE goal_id=?",
                        (next_step, stamp(), goal_id))

    def run_evidence(self, run_id: str | None) -> list[dict]:
        if not run_id:
            return []
        return [dict(r) for r in self.db.execute(
            "SELECT id, name, status FROM tool_calls WHERE run_id=? ORDER BY rowid", (run_id,))]

    def due_goal(self, now: datetime) -> tuple[dict, dict | None] | None:
        """Цель для следующего захода: не трогали сегодня; после вопроса к пользователю —
        не раньше чем через ASK_AGAIN_AFTER, если он не ответил планом. Сначала с планом,
        затем без плана; внутри — по сроку."""
        candidates = []
        for goal in self.db.execute("SELECT * FROM goals WHERE status='active' "
                                    "ORDER BY deadline IS NULL, deadline, id").fetchall():
            plan = self.db.execute("SELECT * FROM goal_plans WHERE goal_id=?", (goal["id"],)).fetchone()
            last = self.db.execute("SELECT * FROM goal_cycles WHERE goal_id=? ORDER BY id DESC LIMIT 1",
                                   (goal["id"],)).fetchone()
            if last is not None:
                when = datetime.fromisoformat(last["created_at"]).astimezone()  # в базе UTC
                if when.date() == now.astimezone().date():
                    continue
                answered = plan is not None and plan["updated_at"] >= last["created_at"]
                if last["status"] == "needs_user" and not answered \
                        and now.astimezone() - when < ASK_AGAIN_AFTER:
                    continue
            candidates.append((plan is None, dict(goal), dict(plan) if plan else None))
        candidates.sort(key=lambda c: c[0])  # стабильно: порядок по сроку сохраняется
        return (candidates[0][1], candidates[0][2]) if candidates else None

    def plan_proposals(self) -> list[dict]:
        """Предложенные Аней планы для целей, у которых плана всё ещё нет."""
        rows = self.db.execute(
            "SELECT c.goal_id, g.title, c.proposal, c.ask_user, c.created_at FROM goal_cycles c "
            "JOIN goals g ON g.id=c.goal_id WHERE c.kind='plan' AND c.proposal IS NOT NULL "
            "AND g.status='active' AND NOT EXISTS (SELECT 1 FROM goal_plans p WHERE p.goal_id=c.goal_id) "
            "AND c.id=(SELECT MAX(id) FROM goal_cycles WHERE goal_id=c.goal_id AND kind='plan')")
        return [dict(r) for r in rows.fetchall()]


def verify(outcome: str, evidence: list[dict]) -> str:
    """Статус захода по фактам, а не по словам модели."""
    if any(call["status"] == "uncertain" for call in evidence):
        return "blocked"  # эффект неизвестен — не двигаемся дальше без сверки
    if outcome == "done":
        return "done" if any(call["status"] == "completed" for call in evidence) else "unverified"
    return outcome if outcome in {"blocked", "needs_user"} else "failed"


def step_task(goal: dict, plan: dict, history: list[dict], recommendations: list[dict],
              strategy: dict | None = None, experiments: list[dict] | None = None) -> str:
    return (
        f"[Системная задача: работа над целью #{goal['id']} «{goal['title']}».] Это его "
        "утверждённая цель с измеримым планом. Сделай следующий шаг сама — сейчас, "
        "инструментами, в пределах фоновых прав: поиск в сети, чтение его почты, ЛК, файлов и "
        "заметок, career_* (поиск, оценка, черновики), отчёты и материалы в папке Ани. "
        "Не отправляй ничего людям от его имени, не трать деньги, не меняй показатель и цель. "
        "Проверь результат: перечитай созданное, сверь найденное с источником. Если шаг "
        "слишком большой — сделай первую проверяемую часть. Не повторяй то, что в <tried> уже "
        "не сработало, и советы, которые он отклонил (declined), без новых оснований. Если в "
        "<strategy> есть гипотеза — проверь её этим шагом, когда это возможно: если меняется "
        "только твой способ работы — experiment_propose subject=anya, если его действия или "
        "привычки — subject=owner (начнётся после его «да»). По идущим из <experiments> запиши "
        "замер из данных (experiment_observe); наступил review_on и замеров хватает — вывод "
        "(experiment_conclude для anya, для owner — предложи вывод ему в reply). outcome: done — сделано и проверено; blocked — "
        "не вышло (blocker — почему, next_step — обход); needs_user — нужно его решение "
        "(ask_user). next_step — следующий конкретный шаг. reply — 1–3 предложения ему "
        "по сути, пусто, если писать незачем.\n\n"
        f"<goal>{encoded({k: goal.get(k) for k in ('area', 'title', 'details', 'deadline')})}</goal>\n"
        f"<plan>{encoded({k: plan.get(k) for k in ('metric', 'unit', 'baseline', 'target', 'weekly_minutes', 'next_step', 'evidence')})}</plan>\n"
        "<tried>\n" + "\n".join(
            f"- {c['created_at'][:10]} {c['status']}: {c['action'][:300]} → {c['result'][:300]}"
            + (f" | мешало: {c['blocker'][:200]}" if c['blocker'] else "") for c in history)
        + "\n</tried>\n<recommendations>\n" + "\n".join(
            f"- [{r['status']}] {r['action'][:200]}" + (f" → {r['outcome'][:200]}" if r.get('outcome') else "")
            for r in recommendations) + "\n</recommendations>\n<strategy>"
        + (encoded({k: strategy[k] for k in ('works', 'fails', 'hypothesis', 'evidence')})
           if strategy else "") + "</strategy>\n<experiments>\n"
        + "\n".join(encoded(compact(e)) for e in experiments or []) + "\n</experiments>")


def plan_task(goal: dict, history: list[dict]) -> str:
    return (
        f"[Системная задача: план для цели #{goal['id']} «{goal['title']}».] У цели нет "
        "измеримого плана, поэтому ты не можешь над ней работать. Предложи план по его данным: "
        "один показатель результата (не активности), единица, текущее значение — только если "
        "оно видно из его данных (иначе baseline null и спроси), цель, сколько минут в неделю "
        "он реально может выделять, первый конкретный шаг, который ты сможешь сделать сама, и "
        "основание (evidence: на какие его данные опираешься). Ничего не сохраняй — это "
        "предложение: утверждает он. reply — коротко предложи план и спроси «утверждаешь?»; "
        "ask_user — что уточнить.\n\n"
        f"<goal>{encoded({k: goal.get(k) for k in ('area', 'title', 'details', 'deadline')})}</goal>\n"
        "<previous>\n" + "\n".join(f"- {c['created_at'][:10]} {c['status']}: {c['ask_user'][:300]}"
                                   for c in history) + "\n</previous>")


def proposals_context(store) -> str:
    items = store.plan_proposals()
    if not items:
        return ""
    lines = [f"- цель #{p['goal_id']} «{p['title']}»: {p['proposal']}"
             + (f" (уточнить: {p['ask_user']})" if p['ask_user'] else "") for p in items]
    return ("<goal_plan_proposals>\nТы предложила ему эти планы. Если он соглашается (можно с "
            "поправками) — сохрани через goal_plan с его числами; не придумывай исходную точку.\n"
            + "\n".join(lines) + "\n</goal_plan_proposals>")
