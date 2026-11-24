"""Интервью: Аня понемногу спрашивает пользователя о том, чего не видно в данных, и проверяет
свои выводы о нём. Поправки к профилю: «это про меня неверно» сразу убирает вывод из
разговоров и учитывается при следующей перестройке профиля.

Вопросы берутся из опубликованного профиля: сначала главные открытые вопросы, затем выводы,
которые стоит подтвердить (needs_confirmation), затем вопросы разделов. Не чаще раза в день,
днём, когда пользователь недавно был на связи, и не следующий, пока прошлый без ответа
(если только не прошло двое суток). Ответ — обычная реплика в чате: Аня видит свой вопрос в
истории и сохраняет сказанное в память; профиль при перестройке читает и разговор, и поправки.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .personalization import load_profile, read_feedback

log = logging.getLogger(__name__)

CHECK_EVERY = 30 * 60
ASK_HOURS = range(11, 22)
MIN_GAP = timedelta(hours=20)  # между вопросами
UNANSWERED_WAIT = timedelta(hours=48)  # прошлый без ответа — следующий не раньше
RECENT_CONTACT = timedelta(hours=3)  # спрашиваем, когда он недавно был на связи
FEEDBACK_KINDS = {"wrong": "неверно", "outdated": "устарело", "right": "верно"}

_lock = threading.Lock()


def _path(name: str) -> Path:
    return config.DATA_DIR / "personalization" / name


def _read(name: str) -> list[dict]:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write(name: str, items: list[dict]) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


# --- поправки к профилю ---

def add_feedback(claim_id: str, verdict: str, comment: str = "") -> dict:
    """Пользователь сказал, верен ли вывод профиля. Последняя оценка вывода главнее."""
    profile = load_profile(config.DATA_DIR)
    claim = next((c for s in profile.sections for c in s.claims if c.id == claim_id),
                 None) if profile else None
    if claim is None:
        raise KeyError(claim_id)
    if verdict not in FEEDBACK_KINDS:
        raise ValueError(verdict)
    entry = {"claim_id": claim_id, "claim": claim.text, "verdict": verdict,
             "comment": comment.strip()[:2000], "profile_id": profile.profile_id,
             "at": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        items = [i for i in _read("feedback.json")
                 if not (i["claim_id"] == claim_id and i["profile_id"] == profile.profile_id)]
        _write("feedback.json", items + [entry])
    return entry


def feedback(profile_id: str | None = None) -> list[dict]:
    """Все поправки (или только к этой версии профиля)."""
    items = read_feedback(config.DATA_DIR)
    return [i for i in items if profile_id is None or i["profile_id"] == profile_id]


def feedback_evidence(_db=None) -> list[dict]:
    """Поправки как записи корпуса: его прямые слова о выводах о нём (для перестройки)."""
    return [{"ref": f"feedback:{n}", "source": "feedback", "ts": item["at"], "role": "user",
             "text": f"Пользователь о выводе профиля «{item['claim']}»: "
                     f"{FEEDBACK_KINDS[item['verdict']]}"
                     + (f". Его слова: {item['comment']}" if item["comment"] else ""),
             "metadata": {"claim_id": item["claim_id"], "verdict": item["verdict"]}}
            for n, item in enumerate(_read("feedback.json"), 1)]


# --- вопросы ---

def _candidates(profile) -> list[dict]:
    """Вопросы по порядку важности; claim_id — если вопрос проверяет вывод."""
    found = [{"text": q, "claim_id": None} for q in profile.open_questions]
    found += [{"text": f"Проверяю свой вывод о тебе: «{c.text.rstrip('.')}». Это так?",
               "claim_id": c.id}
              for s in profile.sections for c in s.claims if c.needs_confirmation]
    found += [{"text": q, "claim_id": None} for s in profile.sections for q in s.questions]
    return found


def next_question() -> dict | None:
    profile = load_profile(config.DATA_DIR)
    if profile is None:
        return None
    asked = {item["text"] for item in _read("interview.json")}
    judged = {item["claim_id"] for item in feedback(profile.profile_id)}
    return next((q for q in _candidates(profile)
                 if q["text"] not in asked and q["claim_id"] not in judged), None)


def record_answer(text: str) -> None:
    """Первая реплика пользователя после вопроса — ответ на него (для истории интервью)."""
    with _lock:
        items = _read("interview.json")
        if items and not items[-1].get("answered_at"):
            items[-1].update(answered_at=datetime.now().isoformat(timespec="seconds"),
                             answer=text[:4000])
            _write("interview.json", items)


class Interviewer:
    """Фоновая служба: изредка задаёт вопрос из профиля."""

    def __init__(self, store, notify):
        self.store, self.notify = store, notify

    def _due(self, now: datetime) -> bool:
        if now.hour not in ASK_HOURS:
            return False
        items = _read("interview.json")
        if items:
            last = items[-1]
            asked = datetime.fromisoformat(last["asked_at"])
            if now - asked < MIN_GAP:
                return False
            if not last.get("answered_at") and now - asked < UNANSWERED_WAIT:
                return False
        contact = self.store.db.execute(
            "SELECT MAX(ts) FROM messages WHERE role='user'").fetchone()[0]
        return bool(contact) and now - datetime.fromisoformat(contact[:19]) < RECENT_CONTACT

    def ask(self) -> str | None:
        question = next_question()
        if question is None:
            return None
        text = question["text"].rstrip()
        if not text.endswith("?"):
            text += "?"
        self.notify(f"🧭 Можно вопрос, чтобы лучше тебя понимать? {text}\n"
                    "(Можно не отвечать. Если я в чём-то о тебе ошибаюсь — скажи, я поправлю.)")
        with _lock:
            _write("interview.json", _read("interview.json") + [{
                "text": question["text"], "claim_id": question["claim_id"],
                "asked_at": datetime.now().isoformat(timespec="seconds")}])
        log.info("Интервью: задан вопрос")
        return question["text"]

    async def run(self) -> None:
        # Автоматические вопросы 🧭 отключены: только по явному вызову /question.
        while True:
            await asyncio.sleep(3600)
