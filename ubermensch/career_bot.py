"""Отклики на hh через бота пользователя (HHruBot, Playwright от его аккаунта) — по расписанию.

Аня запускает его CLI: `responses` обновляет статусы, `run --dry-run|--live --limit N` готовит
письма или откликается. Письма — из шаблонов Ани (DATA_DIR/career/cover_letters.json), не от
нейросети. Результаты переносятся в её воронку (career_opportunities / career_events) с
отметкой шаблона — так видно, какое письмо работает. По умолчанию — только подготовка писем;
настоящие отклики — после HHBOT_LIVE=1 в .env (это его решение, не её).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from . import config

TIMEOUT = 3 * 60 * 60
STAGES = {"invitation": "interview", "discard": "rejected"}  # состояния hh → этапы воронки


def folder() -> Path:
    return Path(os.getenv("HHBOT_DIR", str(Path.home() / "Projects" / "HHruBot"))).expanduser()


def live() -> bool:
    return os.getenv("HHBOT_LIVE", "0").strip() == "1"


def daily_limit() -> int:
    try:
        return max(1, min(200, int(os.getenv("HHBOT_LIMIT", "30"))))
    except ValueError:
        return 30


def available() -> str | None:
    """None — можно запускать; иначе причина, почему нельзя."""
    base = folder()
    if not (base / ".venv" / "bin" / "hhbot").exists():
        return f"Бот не найден или не установлен: {base}/.venv/bin/hhbot"
    if not (base / "data" / "browser_profile").is_dir():
        return "Нет профиля браузера бота: нужен вход — `.venv/bin/hhbot login`"
    return None


async def command(*args: str, timeout: int = TIMEOUT) -> tuple[int, str]:
    """Запустить CLI бота; вывод — последние строки (подробности у бота в data/hhbot.log)."""
    base = folder()
    process = await asyncio.create_subprocess_exec(
        str(base / ".venv" / "bin" / "hhbot"), *args, cwd=base, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.CancelledError, TimeoutError):
        process.kill()
        await process.wait()
        raise
    return process.returncode, output.decode("utf-8", errors="replace")[-4000:]


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{folder() / 'data' / 'hhbot.sqlite3'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _templates() -> list[tuple[str, re.Pattern]]:
    """Шаблоны писем как регулярки: подстановки {position}/{company} — любой текст."""
    path = config.DATA_DIR / "career" / "cover_letters.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    result = []
    for item in data.get("ru", []) + data.get("en", []):
        parts = re.split(r"\{position\}|\{company\}", item["text"])
        result.append((item["id"], re.compile("^" + "(.+?)".join(re.escape(p) for p in parts) + "$", re.S)))
    return result


def template_of(letter: str, templates=None) -> str | None:
    letter = (letter or "").strip()
    for identifier, pattern in templates if templates is not None else _templates():
        if pattern.match(letter):
            return identifier
    return None


def sync(store) -> dict:
    """Перенести отклики и статусы бота в воронку Ани. Повторный перенос ничего не дублирует.
    Возвращает новые приглашения — о них стоит сказать сразу."""
    templates = _templates()
    invited, added = [], Counter()
    with _database() as db:
        rows = db.execute(
            "SELECT a.vacancy_id, a.vacancy_name, a.employer_name, a.url, a.letter, a.created_at, "
            "n.state, n.checked_at FROM applications a LEFT JOIN negotiations n USING(vacancy_id) "
            "WHERE a.status='sent'").fetchall()
    for row in rows:
        url = row["url"] if str(row["url"]).startswith("https://") else f"https://hh.ru/vacancy/{row['vacancy_id']}"
        template = template_of(row["letter"], templates)
        with store.transaction():
            store.save_opportunity("hh", str(row["vacancy_id"]), row["vacancy_name"] or "Вакансия",
                                   row["employer_name"] or "Работодатель", url, {"via": "hhbot"})
            opportunity = store.db.execute(
                "SELECT id FROM career_opportunities WHERE source='hh' AND external_id=?",
                (str(row["vacancy_id"]),)).fetchone()[0]
            stages = {r[0] for r in store.db.execute(
                "SELECT stage FROM career_events WHERE opportunity_id=?", (opportunity,))}
            applied_on = str(row["created_at"])[:10]
            if "applied" not in stages:
                store.record_career_event(opportunity, "applied",
                                          f"hhbot: отклик отправлен, письмо {template or 'не из шаблонов'}",
                                          min(applied_on, date.today().isoformat()))
                added["applied"] += 1
            stage = STAGES.get(row["state"] or "")
            if stage and stage not in stages:
                seen_on = max(applied_on, str(row["checked_at"] or applied_on)[:10])
                store.record_career_event(opportunity, stage, f"hhbot: статус hh «{row['state']}»",
                                          min(seen_on, date.today().isoformat()))
                added[stage] += 1
                if stage == "interview":
                    invited.append({"vacancy": row["vacancy_name"], "company": row["employer_name"],
                                    "url": url})
    return {"added": dict(added), "invitations": invited}


def template_stats() -> list[dict]:
    """Конверсия по шаблонам писем: отправлено, просмотрено, приглашений, отказов.
    Малые числа — наблюдение, не доказательство."""
    templates = _templates()
    stats = {}
    with _database() as db:
        for row in db.execute("SELECT a.letter, n.state FROM applications a LEFT JOIN negotiations n "
                              "USING(vacancy_id) WHERE a.status='sent'"):
            key = template_of(row["letter"], templates) or "без шаблона"
            item = stats.setdefault(key, {"template": key, "sent": 0, "viewed": 0, "invited": 0, "rejected": 0})
            item["sent"] += 1
            item["viewed"] += row["state"] in {"viewed", "invitation", "discard"}
            item["invited"] += row["state"] == "invitation"
            item["rejected"] += row["state"] == "discard"
    return sorted(stats.values(), key=lambda s: -s["sent"])
