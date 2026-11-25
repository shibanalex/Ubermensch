"""Действия, которые система выполняет по решению мозга.

Уровень риска задаёт реестр, а не модель: Claude не может «понизить» риск действия.
Всё, кроме local, выполняется только после подтверждения пользователем в Telegram.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .store import Store

log = logging.getLogger(__name__)

RISK_LABELS = {"local": "локальное", "external": "внешнее", "finance": "финансы"}


@dataclass(frozen=True)
class ActionType:
    kind: str
    risk: str
    help: str  # попадает в системный промпт: что делает и какие params нужны
    run: Callable[[Store, dict], str]


def _reminder(store: Store, params: dict) -> str:
    due = datetime.fromisoformat(str(params["at"]))
    if due.tzinfo:
        due = due.astimezone().replace(tzinfo=None)
    if due <= datetime.now():
        raise ValueError("это время уже прошло")
    text = str(params.get("text", "")).strip()
    if not text:
        raise ValueError("пустой текст напоминания")
    reminder_id = store.add_reminder(due, text)
    return f"⏰ Напоминание #{reminder_id}: {due:%d.%m %H:%M} — {text}"


REGISTRY: dict[str, ActionType] = {
    a.kind: a
    for a in [
        ActionType(
            kind="reminder",
            risk="local",
            help='Поставить напоминание в Telegram. params: {"at": "YYYY-MM-DDTHH:MM" '
                 '(местное время), "text": "что напомнить"}',
            run=_reminder,
        ),
    ]
}


@dataclass(frozen=True)
class Submitted:
    action_id: int
    summary: str
    risk: str
    result: str | None  # None: ждёт подтверждения


def submit(store: Store, kind: str, summary: str, params: dict) -> Submitted | None:
    action = REGISTRY.get(kind)
    if action is None:
        log.warning("Неизвестное действие %r пропущено", kind)
        return None
    action_id = store.add_action(kind, summary, params, action.risk)
    if action.risk != "local":
        return Submitted(action_id, summary, action.risk, None)
    store.decide_action(action_id, "approved")
    return Submitted(action_id, summary, action.risk, execute(store, action_id))


def execute(store: Store, action_id: int) -> str:
    """Выполняет одобренное действие и пишет результат в журнал."""
    row = store.get_action(action_id)
    try:
        result = REGISTRY[row["kind"]].run(store, json.loads(row["params"]))
    except Exception as e:
        log.exception("Действие #%s (%s) не выполнено", action_id, row["kind"])
        store.finish_action(action_id, "failed", str(e))
        return f"⚠️ Не получилось: {row['summary']} — {e}"
    store.finish_action(action_id, "done", result)
    return result
