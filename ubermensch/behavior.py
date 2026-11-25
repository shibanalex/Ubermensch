"""Поведение в цифрах: ритм дня и сна, экранное время, общение, творчество; по месяцам.

Считается кодом из собранных событий, без ИИ: точно, дёшево и проверяемо. Каждая метрика -
отдельная запись-доказательство (`metric:<вид>:<ГГГГ-ММ>`), на которую профиль может
сослаться так же, как на сообщение или документ. Сон здесь: оценка по цифровым следам
(последнее действие вечером, первое утром), а не измерение: телефон без синхронизации,
книга или бессонница в темноте не видны. Считаются только действия, требующие человека:
история YouTube пишется и при автовоспроизведении, поэтому ночные просмотры: отдельно.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

# Источники, где событие требует действия самого человека (плюс любые исходящие сообщения).
# YouTube и «Мои действия» сюда не входят: автовоспроизведение пишет историю и во сне.
ACTIVE_SOURCES = ("activity", "chrome", "google_search", "ai_chat", "creative")
MIN_ACTIONS_PER_DAY = 15  # реже: ночные перерывы ничего не говорят о сне
MAX_SLEEP = timedelta(hours=14)
NIGHT_START, NIGHT_END = 18, 14  # ночной промежуток ищем с 18:00 до 14:00 следующего дня
MIN_SLEEP = timedelta(hours=3)
MIN_DAYS = 5  # месяц с меньшим числом ночей: без оценки сна
TOP = 8


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts[:19])
    except (TypeError, ValueError):
        return None


def _hm(minutes: float) -> str:
    minutes = round(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock(moment: datetime, base_hour: int = 0) -> float:
    """Минуты от base_hour (чтобы 01:30 ночи было «позже» 23:00)."""
    return ((moment.hour - base_hour) % 24) * 60 + moment.minute


def _moments(db: sqlite3.Connection) -> list[datetime]:
    """Все моменты собственных действий: активные источники и исходящие сообщения."""
    marks = ",".join("?" * len(ACTIVE_SOURCES))
    rows = db.execute(f"SELECT ts FROM events WHERE source IN ({marks}) OR outgoing=1",
                      ACTIVE_SOURCES)
    return sorted(m for (ts,) in rows if (m := _parse(ts)))


def _nights(moments: list[datetime]) -> dict[str, tuple[datetime, datetime]]:
    """Ночь → (отбой, подъём): самый длинный перерыв в действиях между 18:00 и 14:00.

    Ключ: дата вечера. Перерыв короче трёх часов сном не считается."""
    best: dict[str, tuple[datetime, datetime]] = {}
    for before, after in zip(moments, moments[1:]):
        gap = after - before
        if gap < MIN_SLEEP or gap > MAX_SLEEP:
            continue
        middle = before + gap / 2
        evening = (middle - timedelta(hours=NIGHT_END)).date()
        window_start = datetime.combine(evening, datetime.min.time()) + timedelta(hours=NIGHT_START)
        if not window_start <= middle <= window_start + timedelta(hours=24 - NIGHT_START + NIGHT_END):
            continue
        key = evening.isoformat()
        if key not in best or gap > best[key][1] - best[key][0]:
            best[key] = (before, after)
    return best


def _youtube_nights(db: sqlite3.Connection) -> Counter:
    return Counter(dict(db.execute(
        "SELECT substr(ts,1,7), COUNT(*) FROM events WHERE source='youtube' AND outgoing=0 "
        "AND CAST(substr(ts,12,2) AS INTEGER) BETWEEN 1 AND 5 GROUP BY 1")))


def _rhythm(moments: list[datetime], youtube_nights: Counter) -> dict[str, str]:
    by_month: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for evening, (sleep, wake) in _nights(moments).items():
        by_month[evening[:7]].append((sleep, wake))
    hours: dict[str, Counter] = defaultdict(Counter)
    days: dict[str, set] = defaultdict(set)
    for moment in moments:
        hours[moment.strftime("%Y-%m")][moment.hour] += 1
        days[moment.strftime("%Y-%m")].add(moment.date())
    result = {}
    for month in sorted(set(by_month) | set(hours) | set(youtube_nights)):
        lines = []
        nights = by_month.get(month, [])
        dense = sum(hours[month].values()) >= MIN_ACTIONS_PER_DAY * max(1, len(days[month]))
        if len(nights) >= MIN_DAYS and dense:
            beds = [_clock(s, 12) for s, _ in nights]  # от полудня: полночь = 720
            wakes = [_clock(w) for _, w in nights]
            lengths = [(w - s).total_seconds() / 3600 for s, w in nights]
            late = sum(1 for s, _ in nights if 2 <= s.hour < 12)
            weekend = [(w - s).total_seconds() / 3600 for s, w in nights if s.weekday() >= 4]
            weekday = [(w - s).total_seconds() / 3600 for s, w in nights if s.weekday() < 4]
            lines.append(
                f"Оценка сна по цифровым следам ({len(nights)} ночей): отбой — медиана "
                f"{_hm(median(beds) + 720)}, подъём — {_hm(median(wakes))}, ночной перерыв в действиях "
                f"(сон не дольше него) — медиана {median(lengths):.1f} ч (от {min(lengths):.1f} "
                f"до {max(lengths):.1f}). "
                f"Отбой после 02:00 — {late} из {len(nights)} ночей.")
            if weekday and weekend:
                lines.append(f"Перерыв в будни — {median(weekday):.1f} ч, перед выходными — "
                             f"{median(weekend):.1f} ч.")
        counter = hours.get(month)
        if counter:
            total = sum(counter.values())
            night = sum(counter[h] for h in range(0, 5))
            peak = ", ".join(f"{h:02d}:00" for h, _ in counter.most_common(3))
            lines.append(f"Активных действий (поиск, браузер, сообщения, трекер окон, чаты с ИИ, "
                         f"творчество): {total}; самые активные часы — {peak}; с 00:00 до 05:00 — "
                         f"{night * 100 // total}%.")
            if not dense:
                lines.append("Действий мало — сон по ним не оценивается.")
        if youtube_nights.get(month):
            lines.append(f"Просмотров YouTube с 01:00 до 06:00 — {youtube_nights[month]} "
                         "(часть может быть автовоспроизведением во сне).")
        if lines:
            result[month] = " ".join(lines)
    return result


def _screen(db: sqlite3.Connection) -> dict[str, str]:
    """Время в программах за месяц (трекер активности; только пока работает ядро)."""
    categories = dict(db.execute("SELECT key, category FROM activity_categories")) \
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_categories'").fetchone() \
        else {}
    apps: dict[str, Counter] = defaultdict(Counter)
    days: dict[str, set] = defaultdict(set)
    for ts, app, meta in db.execute("SELECT ts, thread, meta FROM events WHERE source='activity'"):
        try:
            seconds = int(json.loads(meta or "{}").get("seconds", 0))
        except (ValueError, TypeError, AttributeError):
            continue
        apps[ts[:7]][app or "?"] += seconds
        days[ts[:7]].add(ts[:10])
    result = {}
    for month, counter in sorted(apps.items()):
        total = sum(counter.values())
        if total < 3600:
            continue
        top = ", ".join(f"{app} {sec / 3600:.1f} ч" + (f" ({categories[app]})" if app in categories else "")
                        for app, sec in counter.most_common(TOP))
        result[month] = (f"Время за компьютером (трекер, {len(days[month])} дн.): всего "
                         f"{total / 3600:.1f} ч, в среднем {total / 3600 / len(days[month]):.1f} ч "
                         f"в день. Больше всего: {top}.")
    return result


def _social(db: sqlite3.Connection) -> dict[str, str]:
    """Переписка: сколько, с кем, кто начинает разговор, ночные сообщения."""
    sent: dict[str, Counter] = defaultdict(Counter)
    received: dict[str, Counter] = defaultdict(Counter)
    starts: dict[str, Counter] = defaultdict(Counter)
    late: Counter = Counter()
    last: dict[str, datetime] = {}
    rows = db.execute("SELECT ts, thread, outgoing FROM events WHERE source='telegram' "
                      "ORDER BY thread, ts")
    for ts, thread, outgoing in rows:
        moment = _parse(ts)
        if moment is None or not thread:
            continue
        month = ts[:7]
        (sent if outgoing else received)[month][thread] += 1
        previous = last.get(thread)
        if previous is None or moment - previous > timedelta(hours=6):
            starts[month]["я" if outgoing else "мне"] += 1
        last[thread] = moment
        if outgoing and moment.hour < 5:
            late[month] += 1
    result = {}
    for month in sorted(set(sent) | set(received)):
        mine, theirs = sent[month], received[month]
        if not mine and not theirs:
            continue
        top = ", ".join(f"{t} ({n})" for t, n in mine.most_common(TOP))
        begun = starts[month]
        result[month] = (
            f"Telegram: отправлено {sum(mine.values())}, получено {sum(theirs.values())} "
            f"сообщений; свои сообщения — в {len(mine)} чатах. Больше всего своих сообщений: "
            f"{top or 'нет'}. Разговоры (после паузы 6 ч) начаты пользователем — {begun['я']}, "
            f"собеседниками — {begun['мне']}. Своих сообщений с 00:00 до 05:00 — {late[month]}.")
    return result


def _volume(db: sqlite3.Connection) -> dict[str, str]:
    """Сколько смотрел, искал и создавал: следы внимания и творчества."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for source, month, n in db.execute(
            "SELECT source, substr(ts,1,7), COUNT(*) FROM events WHERE source IN "
            "('youtube','google_search','chrome','creative','document','ai_chat','gmail') "
            "GROUP BY 1, 2"):
        counts[month][source] = n
    labels = {"youtube": "просмотров YouTube", "google_search": "поисковых запросов",
              "chrome": "страниц в Chrome", "creative": "правок творческих файлов",
              "document": "документов", "ai_chat": "реплик в чатах с ИИ", "gmail": "писем"}
    hosts: dict[str, Counter] = defaultdict(Counter)
    for month, host, meta in db.execute("SELECT substr(ts,1,7), thread, meta FROM events "
                                        "WHERE source='chrome'"):
        try:
            hosts[month][host] += int(json.loads(meta or "{}").get("visits", 1))
        except (ValueError, TypeError, AttributeError):
            hosts[month][host] += 1
    result = {}
    for month, counter in sorted(counts.items()):
        parts = [f"{labels[s]} — {n}" for s, n in counter.most_common() if s in labels]
        line = "Объём за месяц: " + "; ".join(parts) + "."
        if hosts.get(month):
            line += " Частые сайты: " + ", ".join(f"{h} ({n})" for h, n in
                                                  hosts[month].most_common(TOP)) + "."
        result[month] = line
    return result


def _music(db: sqlite3.Connection) -> dict[str, str]:
    """Что слушал: самые частые исполнители и треки за месяц (по журналу плееров)."""
    artists: dict[str, Counter] = defaultdict(Counter)
    tracks: dict[str, Counter] = defaultdict(Counter)
    for month, text in db.execute("SELECT substr(ts,1,7), text FROM events WHERE source='music'"):
        tracks[month][text] += 1
        if " — " in text:
            artists[month][text.split(" — ", 1)[0]] += 1
    result = {}
    for month, counter in sorted(tracks.items()):
        line = f"Прослушиваний (треков и видео в плеерах): {sum(counter.values())}."
        if artists[month]:
            line += " Чаще всего: " + ", ".join(f"{a} ({n})" for a, n in artists[month].most_common(TOP)) + "."
        line += " Треки: " + "; ".join(f"{t} ({n})" for t, n in counter.most_common(TOP)) + "."
        result[month] = line
    return result


def _laptop_sleep(db: sqlite3.Connection) -> dict[str, str]:
    """Сон ноутбука по системному журналу: точные «не за компьютером»."""
    nights: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    naps: Counter = Counter()
    for ts, meta in db.execute("SELECT ts, meta FROM events WHERE source='power'"):
        start = _parse(ts)
        try:
            until = _parse(json.loads(meta or "{}").get("until", ""))
        except ValueError:
            until = None
        if not start or not until:
            continue
        if until - start >= timedelta(hours=3) and (start.hour >= 18 or start.hour < 9):
            nights[ts[:7]].append((start, until))
        else:
            naps[ts[:7]] += 1
    result = {}
    for month in sorted(set(nights) | set(naps)):
        line = f"Ноутбук засыпал днём ненадолго {naps[month]} раз."
        if nights.get(month):
            starts = [_clock(s, 12) for s, _ in nights[month]]
            ends = [_clock(u) for _, u in nights[month]]
            line = (f"Ночной сон ноутбука (журнал): {len(nights[month])} ночей, уснул — медиана "
                    f"{_hm(median(starts) + 720)}, проснулся — {_hm(median(ends))}. " + line)
        result[month] = line
    return result


def derive(db: sqlite3.Connection) -> list[dict]:
    """Записи-метрики для корпуса профиля."""
    moments = _moments(db)
    kinds = (("rhythm", "Ритм дня и сна", _rhythm(moments, _youtube_nights(db))), ("screen", "Экранное время", _screen(db)),
             ("social", "Общение", _social(db)), ("volume", "Внимание и творчество", _volume(db)),
             ("music", "Музыка", _music(db)), ("laptop", "Сон ноутбука", _laptop_sleep(db)))
    items = []
    for kind, title, months in kinds:
        for month, text in months.items():
            items.append({"ref": f"metric:{kind}:{month}", "source": "metrics", "ts": f"{month}-28",
                          "role": "derived", "text": f"{title}, {month}. {text}",
                          "metadata": {"kind": kind, "month": month, "title": title}})
    return items
