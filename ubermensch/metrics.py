"""Метрики для окна «метрики»: насколько ассистент изучила пользователя и что пользователь делает.

Всё считается из базы на лету, без вызовов Claude. Единственное, что делает Claude, -
раз в сутки размечает новые программы и сайты по категориям (analysis.Investigation).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from .store import AREAS, Store

BROWSERS = {"chrome", "msedge", "firefox", "opera", "browser", "brave", "vivaldi"}
# категория → полезное / нейтральное / отвлекающее
CATEGORIES = {"учёба": "useful", "работа": "useful", "музыка": "useful", "развитие": "useful",
              "общение": "neutral", "быт": "neutral", "система": "neutral",
              "развлечения": "distracting"}
AREA_FULL = 60  # столько фактов в сфере: сфера «изучена»
PLAN_SHARE, AREAS_SHARE = 0.6, 0.4  # освоение = 60% разобранных данных + 40% охвата сфер
WEEK = 7
# на этих сайтах бывает и полезное, и пустое: размечается каждое видео или страница
MIXED_SITES = {"YouTube", "ВКонтакте", "VK", "Twitch", "Reddit", "Дзен", "Rutube", "TikTok"}


def activity_key(app: str, title: str) -> str:
    """Что размечать: сайт для браузера («Видео - YouTube - Google Chrome» → YouTube),
    программу для остального."""
    if app.lower() in BROWSERS:
        parts = [p.strip() for p in re.split(r"\s[-—–]\s", title) if p.strip()]
        if len(parts) >= 2:
            site = parts[-2][:60]
            if site in MIXED_SITES and len(parts) >= 3:
                return f"сайт: {site} · {' - '.join(parts[:-2])[:80]}"
            return f"сайт: {site}"
    return f"программа: {app}"


def display_key(key: str) -> str:
    """Для списков «куда ушло время»: видео YouTube; одной строкой «сайт: YouTube»."""
    return key.split(" · ")[0]


def _minutes(seconds: float) -> int:
    return round(seconds / 60)


def _spread(start: datetime, seconds: int):
    """Разложить сессию по часам: [(час, секунд)]."""
    moment, left = start, seconds
    while left > 0:
        edge = moment.replace(minute=0, second=0) + timedelta(hours=1)
        chunk = min(left, int((edge - moment).total_seconds()))
        yield moment, chunk
        moment, left = edge, left - chunk


def learning(store: Store, status: dict, labels: dict[str, str], voice_at: str | None,
             style_at: str | None) -> dict:
    """Как далеко ассистент продвинулась в изучении пользователя. labels: названия источников."""
    by_source = store.jobs_by_source()
    done = sum(d for _, d, _ in by_source)
    total = sum(t for _, _, t in by_source)
    areas = store.fact_areas()
    coverage = sum(min(1.0, areas.get(a, 0) / AREA_FULL) for a in AREAS) / len(AREAS)
    plan = done / total if total else 0.0
    since = (date.today() - timedelta(days=13)).isoformat()
    per_day = store.facts_by_day(since)
    return {
        "mastery": round(100 * (PLAN_SHARE * plan + AREAS_SHARE * coverage)),
        "jobs": {"done": done, "total": total,
                 "by_source": [{"label": labels.get(s, s), "done": d, "total": t}
                               for s, d, t in by_source]},
        "analyzed_chars": store.analyzed_chars(),
        "areas": [{"area": label, "facts": areas.get(key, 0), "full": AREA_FULL}
                  for key, label in AREAS.items()],
        "facts": sum(areas.values()),
        "people": len(store.people(10_000)),
        "goals": len(store.goals()),
        "facts_by_day": [per_day.get((date.today() - timedelta(days=i)).isoformat(), 0)
                         for i in range(13, -1, -1)],
        "events": [{"label": labels.get(s, s), "count": n}
                   for s, n in status.get("events", {}).items()],
        "dossier_at": status.get("dossier_at"),
        "voice_at": voice_at,
        "style_at": style_at,
    }


def activity(store: Store, today: date | None = None) -> dict:
    """Время за компьютером, полезное и отвлекающее, задачи и цели."""
    today = today or date.today()
    week_start = today - timedelta(days=WEEK - 1)
    sessions = store.activity_sessions(week_start.isoformat(),
                                       (today + timedelta(days=1)).isoformat())
    categories = store.activity_categories()
    days: dict[str, dict] = {
        (week_start + timedelta(days=i)).isoformat(): {
            "seconds": 0, "first": None, "last": None,
            "kinds": defaultdict(int)} for i in range(WEEK)}
    hours = [0] * 24
    top_today: dict[str, int] = defaultdict(int)
    top_week: dict[str, int] = defaultdict(int)
    for ts, app, title, seconds in sessions:
        start = datetime.fromisoformat(ts)
        key = activity_key(app, title)
        kind = CATEGORIES.get(categories.get(key, ""), "unknown")
        for moment, chunk in _spread(start, seconds):
            day = days.get(moment.date().isoformat())
            if day is None:
                continue
            day["seconds"] += chunk
            day["kinds"][kind] += chunk
            end = (moment + timedelta(seconds=chunk)).strftime("%H:%M")
            day["first"] = min(day["first"] or "99", moment.strftime("%H:%M"))
            day["last"] = max(day["last"] or "", end)
            top_week[display_key(key)] += chunk
            if moment.date() == today:
                hours[moment.hour] += chunk
                top_today[display_key(key)] += chunk

    def top(counter: dict[str, int], n: int) -> list[dict]:
        return [{"key": k.split(": ", 1)[-1], "minutes": _minutes(v),
                 "category": categories.get(k, "разное" if k.split(": ", 1)[-1] in MIXED_SITES
                                            else None)}
                for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:n] if v >= 60]

    tasks = store.task_stats(today.isoformat(), week_start.isoformat())
    streak, day = 0, today
    done_days = set(tasks.pop("done_days"))
    if today.isoformat() not in done_days:  # сегодня ещё можно успеть: серия не рвётся
        day -= timedelta(days=1)
    while day.isoformat() in done_days:
        streak, day = streak + 1, day - timedelta(days=1)
    return {
        "today": today.isoformat(),
        "days": [{"date": d, "minutes": _minutes(v["seconds"]), "first": v["first"],
                  "last": v["last"],
                  "kinds": {k: _minutes(s) for k, s in v["kinds"].items()}}
                 for d, v in days.items()],
        "hours": [_minutes(s) for s in hours],
        "top_today": top(top_today, 6),
        "top_week": top(top_week, 8),
        "tasks": {**tasks, "streak": streak},
        "goals": [{"title": t, "done": d, "total": n} for t, d, n in store.goal_progress()],
        "since": sessions[0][0][:10] if sessions else None,
    }


def uncategorized(store: Store, days: int = 14, min_seconds: int = 300,
                  limit: int = 80) -> list[dict]:
    """Программы и сайты без категории, на которые ушло хоть сколько-то времени."""
    start = (date.today() - timedelta(days=days)).isoformat()
    known = store.activity_categories()
    seconds: dict[str, int] = defaultdict(int)
    titles: dict[str, list[str]] = defaultdict(list)
    for _, app, title, secs in store.activity_sessions(start, "9999"):
        key = activity_key(app, title)
        if key in known:
            continue
        seconds[key] += secs
        if len(titles[key]) < 3 and title not in titles[key]:
            titles[key].append(title[:80])
    # видео и страницы смешанных сайтов короче: им хватает минуты
    keys = sorted((k for k, v in seconds.items()
                   if v >= (60 if " · " in k else min_seconds)), key=lambda k: -seconds[k])
    return [{"key": k, "minutes": _minutes(seconds[k]), "titles": titles[k]}
            for k in keys[:limit]]
