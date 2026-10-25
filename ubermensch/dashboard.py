"""Панель для клиентов (плагин Obsidian): цели с планом и последним заходом, предложения,
эксперименты, задачи, отклики hh, счёт Ани — одним снимком, без вызова модели."""
from __future__ import annotations

import json

from . import agent, career_bot, experiments, options


def _goal(store, goal: dict, plans: dict) -> dict:
    plan = plans.get(goal["id"])
    last = store.goal_cycles(goal["id"], 1)
    strategy = store.goal_strategy(goal["id"])
    return {
        "id": goal["id"], "area": goal["area"], "title": goal["title"], "deadline": goal["deadline"],
        "plan": {k: plan[k] for k in ("metric", "unit", "baseline", "target", "current",
                                      "progress_percent", "weekly_minutes", "next_step")} if plan else None,
        "last_cycle": {k: last[0][k] for k in ("status", "kind", "action", "result", "blocker",
                                               "ask_user", "created_at")} if last else None,
        "hypothesis": strategy["hypothesis"] if strategy else "",
        "experiments": [experiments.compact(e) for e in store.experiments(goal["id"])],
        "options": [options.compact(o) for o in store.goal_options(goal["id"])],
    }


def hh() -> dict:
    blocked = career_bot.available()
    return {"available": blocked is None, "problem": blocked, "live": career_bot.live(),
            "daily_limit": career_bot.daily_limit(),
            "templates": career_bot.template_stats() if blocked is None else []}


def build(core) -> dict:
    store = core.store
    plans = {p["goal_id"]: p for p in store.measured_goals()}
    goals = [_goal(store, dict(g), plans) for g in store.db.execute(
        "SELECT * FROM goals WHERE status='active' ORDER BY deadline IS NULL, deadline, id")]
    proposals = [{**p, "proposal": json.loads(p["proposal"])} for p in store.plan_proposals()]
    autonomy = core.autonomy
    return {
        "goals": goals,
        "plan_proposals": proposals,
        "consent_needed": [experiments.compact(e) for e in store.experiments(statuses=("proposed",))],
        "tasks": [dict(t) for t in store.tasks()],
        "agent_tasks": agent.tasks("pending") + agent.tasks("running") + agent.tasks("interrupted"),
        "approvals": core.state()["approvals"],
        "funnel": store.career_funnel()["groups"],
        "hh": {**hh(), "last_session_day": store.cursor("hh_day")},
        "effectiveness": store.effectiveness(7),
        "anya": {"thinking": core.thinking, **(autonomy.state if autonomy else {})},
    }
