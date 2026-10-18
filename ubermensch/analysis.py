"""Расследование: сбор данных из источников, разбор по частям и досье на пользователя.

1. Сбор — сборщики раз в несколько часов докладывают новое в таблицу events.
2. План — данные режутся по месяцам на порции до CHUNK_CHARS символов (задачи в очереди).
   Последние RECENT_DAYS дней идут первыми, остальной год — в фоне.
3. Разбор — по одной порции, моделью подешевле, с паузами: бережём лимиты подписки.
4. Досье — когда разобраны свежие данные и когда разобрано всё.
"""
from __future__ import annotations

import asyncio
import imaplib
import json
import logging
import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta

from . import config, ingest, metrics
from .assistant import HELPER_SOURCES, Assistant
from .brain import BrainError
from .collectors import ai_chats, chrome, creative, documents, gmail, notifications, takeout
from .collectors.telegram import TelegramAccount, TelegramLoginRequired
from .health import Health
from .store import Store
from .system import windows
from .university import lk, site

log = logging.getLogger(__name__)

HISTORY_DAYS = 365
RECENT_DAYS = 30
# Порция на один вызов. Каждый вызов несёт ещё и общий контекст (что уже известно), поэтому
# порции покрупнее — больше данных на тот же лимит подписки.
CHUNK_CHARS = 120_000
LEGACY_CHUNK = 60_000  # порции, поставленные в очередь до 13.10.2026 (ключ без «@размер»)
COLLECT_EVERY = 2 * 3600
SLOW_SOURCES_EVERY = 24 * 3600  # документы и архив чатов с ИИ меняются редко
# Лимит подписки Claude общий для разбора, бота и Claude Code: днём разбор идёт медленно
# и уступает разговорам, ночью — быстро.
DAY_PAUSE = 15 * 60  # не больше 4 порций в час
NIGHT_PAUSE = 90
NIGHT_HOURS = (1, 8)
AWAY_SECONDS = 10 * 60  # пользователь отошёл от ПК (или экран заблокирован) — днём как ночью
QUIET_AFTER_CHAT = timedelta(minutes=10)
LIMIT_PAUSE = 30 * 60  # если время сброса лимита неизвестно
AUTH_WAIT = 5 * 60  # Claude разлогинен или не найден — проверяем снова через 5 минут
TELEGRAM_WAIT = timedelta(hours=24)
STYLE_EVERY = 7 * 24 * 3600
STYLE_SAMPLE, STYLE_CHARS = 2500, 35_000  # свежие сообщения пользователя для описания стиля
THINKING_CHARS = 40_000
WORK_STYLE_MIN = 50  # рабочий стиль — когда своих сообщений в рабочем Telegram хотя бы столько
CATEGORIZE_EVERY = 20 * 3600
UNIVERSITY_EVERY = 20 * 3600  # новости вуза и личный кабинет — раз в сутки
NEWS_SEEN = 300  # сколько адресов уже просмотренных новостей помнить  # разметка программ и сайтов — не чаще раза в сутки  # его реплики в разговорах с ИИ — как он рассуждает
MAX_ATTEMPTS = 3
RETRY_PAUSE = 5 * 60
HELPER_PAUSE = 5  # после порции, разобранной вторым ИИ, — лимит Claude не тронут
RETRY_FAILED_AFTER = timedelta(hours=12)  # упавшие порции (сеть, сон) — снова в очередь
DOSSIER_DIR = config.DATA_DIR / "dossier"
SOURCES = {"telegram": "Telegram", "gmail": "Почта", "chrome": "Браузер",
           "ai_chat": "Разговоры с ИИ", "activity": "Активность за компьютером",
           "document": "Документы", "creative": "Творчество", "notification": "Уведомления",
           "youtube": "YouTube", "google_search": "Поиск Google",
           "google_activity": "Действия в сервисах Google", "lk": "Личный кабинет вуза"}
TAKEOUT_DAYS = 2 * 365  # из Google Takeout берём два года: интересы видны в динамике
GROUP_LINES = 700  # строк на сервис в месяц (просмотры, запросы, уведомления) — дальше шум
PERSON_MIN_MESSAGES, PERSON_TOP = 300, 15  # портреты отношений: личные чаты от 300 сообщений
CONSOLIDATE_EVERY = 10  # уборка памяти после каждых 10 разобранных порций
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
          "октябрь", "ноябрь", "декабрь"]


# --- нарезка данных на порции ---

def _dm(ts: str) -> str:
    return f"{ts[8:10]}.{ts[5:7]}"


def _sample(texts: list[str], limit: int) -> str:
    """Тексты подряд (свежие первыми), пока влезают в limit символов; длинные — обрезаются."""
    lines, size = [], 0
    for text in texts:
        text = text[:2000]
        if size + len(text) > limit:
            break
        lines.append(f"— {text}")
        size += len(text)
    return "\n".join(lines)


def _group_chatter(event) -> bool:
    """Чужое сообщение в группе, не обращённое к пользователю, — шум, в разбор не идёт.

    Так же отсекаются и групповые сообщения, собранные до перехода на «только важное».
    """
    meta = json.loads(event["meta"] or "{}")
    return meta.get("kind") == "group" and not event["outgoing"] and not meta.get("mention")


def _units(store: Store, source: str, start: str, end: str) -> list[str]:
    """Смысловые куски за период: чат, письмо, документ или сводка браузера."""
    events = store.events(source, start, end)
    if source == "telegram":
        events = [e for e in events if not _group_chatter(e)]
    if not events:
        return []
    if source in ("telegram", "ai_chat"):
        threads: dict[str, list] = defaultdict(list)
        for e in events:
            threads[e["thread"] or "?"].append(e)
        header = "### Чат: {}" if source == "telegram" else "### Разговоры с ИИ, проект {}"
        return [header.format(thread) + "\n" + "\n".join(
            f"[{_dm(e['ts'])} {e['ts'][11:16]}] {'Я' if e['outgoing'] else e['author'] or '?'}: "
            f"{e['text']}" for e in items) for thread, items in sorted(threads.items())]
    if source == "gmail":
        units = []
        for e in events:
            meta = json.loads(e["meta"] or "{}")
            mark = " — отправлено мной" if e["outgoing"] else ""
            units.append(f"### Письмо {_dm(e['ts'])}.{e['ts'][:4]}{mark}\n"
                         f"От: {e['author']} <{meta.get('from', '')}>; кому: {meta.get('to', '')}\n"
                         f"{e['text'][:3000]}")
        return units
    if source == "lk":
        return [f"### Личный кабинет вуза: {e['thread']} (снимок {_dm(e['ts'])}.{e['ts'][:4]})\n"
                f"{e['text'][:15_000]}" for e in events]
    if source == "document":
        return [f"### Документ: {json.loads(e['meta'] or '{}').get('path', e['thread'])} "
                f"(изменён {_dm(e['ts'])}.{e['ts'][:4]})\n{e['text'][:12_000]}" for e in events]
    if source == "chrome":
        searches = list(dict.fromkeys(
            f"[{_dm(e['ts'])}] {e['text']}" for e in events if e["thread"] == "поиск"))
        days: dict[str, set] = defaultdict(set)
        titles: dict[str, list] = defaultdict(list)
        for e in events:
            if e["thread"] != "поиск":
                days[e["thread"]].add(e["ts"][:10])
                if len(titles[e["thread"]]) < 8 and e["text"] not in titles[e["thread"]]:
                    titles[e["thread"]].append(e["text"])
        top = sorted(days, key=lambda host: -len(days[host]))[:80]
        sites = [f"- {host}: {len(days[host])} дн. — " + "; ".join(titles[host]) for host in top]
        return ["### Поисковые запросы\n" + "\n".join(searches[:800]),
                "### Сайты (дней с посещениями) и что там открывалось\n" + "\n".join(sites)]
    if source in ("youtube", "google_search", "google_activity", "notification"):
        groups: dict[str, dict[str, str]] = defaultdict(dict)  # сервис → текст → первая дата
        for e in events:
            groups[e["thread"] or "?"].setdefault(e["text"][:300], _dm(e["ts"]))
        prefix = "Уведомления: " if source == "notification" else ""
        return [f"### {prefix}{thread} ({len(items)})\n"
                + "\n".join(f"[{day}] {text}" for text, day in list(items.items())[:GROUP_LINES])
                for thread, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    if source == "creative":
        return [f"[{_dm(e['ts'])}.{e['ts'][:4]}] {e['text']}" for e in events]
    if source == "activity":
        seconds: Counter = Counter()
        windows: Counter = Counter()
        per_day: Counter = Counter()
        for e in events:
            spent = json.loads(e["meta"] or "{}").get("seconds", 0)
            seconds[e["thread"]] += spent
            windows[f"{e['thread']}: {e['text']}"] += spent
            per_day[e["ts"][:10]] += spent
        return ["### Время за компьютером по программам\n" + "\n".join(
                    f"- {app}: {s / 3600:.1f} ч" for app, s in seconds.most_common(40)),
                "### Окна, где прошло больше всего времени\n" + "\n".join(
                    f"- {w} — {s / 60:.0f} мин" for w, s in windows.most_common(60)),
                "### Активные часы по дням\n" + "\n".join(
                    f"- {day}: {s / 3600:.1f} ч" for day, s in sorted(per_day.items()))]
    return [e["text"] for e in events]


def _pack(units: list[str], limit: int = CHUNK_CHARS) -> list[str]:
    """Складывает куски в порции; слишком длинный кусок режет по строкам, повторяя заголовок."""
    chunks, current = [], ""
    for unit in units:
        header = unit.split("\n", 1)[0]
        while len(unit) > limit:
            cut = unit.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:cut])
            unit = f"{header} (продолжение)\n{unit[cut:].lstrip()}"
        if current and len(current) + len(unit) + 2 > limit:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks


def _periods(source: str, first: str, today: date) -> list[tuple[str, str, str]]:
    """(начало, конец, подпись). Документы — по годам, остальное — по месяцам."""
    periods = []
    if source == "document":
        for year in range(int(first[:4]), today.year + 1):
            periods.append((f"{year}-01-01", f"{year + 1}-01-01", f"{year} год"))
        return periods
    month = date(int(first[:4]), int(first[5:7]), 1)
    while month <= today:
        following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        periods.append((month.isoformat(), following.isoformat(),
                        f"{MONTHS[month.month - 1]} {month.year}"))
        month = following
    return periods


def _stats_units(store: Store, start: str, end: str) -> list[str]:
    """Точные цифры вместо догадок: когда встаёт и ложится, когда активен, с кем и сколько пишет.

    Активность — свои сообщения, страницы браузера, работа за ПК; день считается с 5 утра,
    чтобы ночные часы относились к прошедшему дню.
    """
    days: dict[str, dict] = defaultdict(lambda: {"first": None, "last": None, "sent": 0,
                                                 "received": 0, "pages": 0, "pc": 0})
    hours: Counter = Counter()
    contacts: Counter = Counter()

    def mark(ts: str) -> dict:
        moment = datetime.fromisoformat(ts)
        day = days[(moment - timedelta(hours=5)).date().isoformat()]
        day["first"] = min(day["first"] or moment, moment)
        day["last"] = max(day["last"] or moment, moment)
        hours[moment.hour] += 1
        return day

    for e in store.events("telegram", start, end):
        meta = json.loads(e["meta"] or "{}")
        if e["outgoing"]:
            mark(e["ts"])["sent"] += 1
        elif not _group_chatter(e):
            days[(datetime.fromisoformat(e["ts"]) - timedelta(hours=5)).date().isoformat()][
                "received"] += 1
        if meta.get("kind") == "user":
            contacts[e["thread"]] += 1
    for e in store.events("chrome", start, end):
        mark(e["ts"])["pages"] += 1
    for e in store.events("activity", start, end):
        mark(e["ts"])["pc"] += json.loads(e["meta"] or "{}").get("seconds", 0)
    if not days:
        return []
    weekdays = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    lines = []
    for day, d in sorted(days.items()):
        if d["first"] is None:
            continue
        lines.append(f"{day} ({weekdays[date.fromisoformat(day).weekday()]}): активен "
                     f"{d['first']:%H:%M}–{d['last']:%H:%M}, написал {d['sent']}, получил "
                     f"{d['received']}, страниц {d['pages']}"
                     + (f", за ПК {d['pc'] / 3600:.1f} ч" if d["pc"] else ""))
    histogram = ", ".join(f"{h:02d}ч: {hours[h]}" for h in range(24) if hours[h])
    top = ", ".join(f"{name} — {n}" for name, n in contacts.most_common(15))
    return ["### Статистика по дням (день с 5 утра; «активен» — первое и последнее своё действие)\n"
            + "\n".join(lines),
            f"### Активность по часам суток\n{histogram}",
            f"### С кем больше всего переписка (сообщений в обе стороны)\n{top}"]


def _person_units(store: Store, thread: str, start: str, end: str,
                  limit: int = CHUNK_CHARS) -> list[str]:
    """Вся переписка с одним человеком за период; если не влезает — равномерная выборка."""
    events = [e for e in store.events("telegram", start, end, thread=thread)]
    lines = [f"[{_dm(e['ts'])}.{e['ts'][2:4]}] {'Я' if e['outgoing'] else thread}: "
             f"{e['text'][:400]}" for e in events]
    budget, size = limit - 500, sum(len(line) + 1 for line in lines)
    if size > budget:  # равномерно прорежаем, сохраняя ход отношений во времени
        step = size / budget
        lines = [line for i, line in enumerate(lines) if int(i / step) != int((i - 1) / step)]
    return [f"### Переписка с {thread}: {len(events)} сообщений, "
            f"{_dm(events[0]['ts'])}.{events[0]['ts'][:4]} – {_dm(events[-1]['ts'])}.{events[-1]['ts'][:4]}"
            + (" (равномерная выборка)" if size > budget else "") + "\n" + "\n".join(lines)] \
        if events else []


def plan(store: Store) -> int:
    """Ставит в очередь новые порции. Возвращает, сколько добавлено."""
    today = date.today()
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    added = 0
    store.drop_legacy_jobs()  # не начатые порции старого размера — перепланировать крупнее
    retried = store.retry_failed_jobs((datetime.now() - RETRY_FAILED_AFTER).isoformat())
    if retried:
        log.info("Снова в очереди упавших порций: %d", retried)
    size = f"@{CHUNK_CHARS}"

    # статистика по месяцам — с первого месяца, где есть переписка или браузер
    firsts = [f for f in (store.event_span(s)[0] for s in ("telegram", "chrome")) if f]
    if firsts:
        for start, end, period in _periods("stats", min(firsts), today):
            if end > today.isoformat() and store.has_job_prefix(f"stats|{start}"):
                continue
            tag = "" if end <= today.isoformat() else f"|по {today:%d.%m}"
            added += store.add_job(f"stats|{start}{tag}|1/1{size}", "stats",
                                   f"Статистика активности, {period}", start, end,
                                   2 if end > recent_from else 1)

    # портреты отношений с близкими — раз в квартал по всей переписке за год
    year_ago = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    quarter = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    for contact in store.telegram_contacts(year_ago, PERSON_MIN_MESSAGES)[:PERSON_TOP]:
        added += store.add_job(f"person|{contact['thread']}|{quarter}|1/1{size}", "person",
                               f"Отношения: переписка с {contact['thread']} за год "
                               f"({contact['n']} сообщений)", year_ago,
                               (today + timedelta(days=1)).isoformat(), 0)

    for source, label in SOURCES.items():
        first, _ = store.event_span(source)
        if not first:
            continue
        for start, end, period in _periods(source, first, today):
            complete = end <= today.isoformat()
            prefix = f"{source}|{start}"
            # незаконченный месяц разбираем один раз сразу, целиком — когда он закончится;
            # активность копится с запуска трекера — её разбираем только за полный месяц
            if not complete and (source == "activity" or store.has_job_prefix(prefix)):
                continue
            chunks = _pack(_units(store, source, start, end))
            tag = "" if complete else f"|по {today:%d.%m}"
            priority = 2 if end > recent_from else 1
            for number, _chunk in enumerate(chunks, start=1):
                part = f", часть {number} из {len(chunks)}" if len(chunks) > 1 else ""
                added += store.add_job(f"{prefix}{tag}|{number}/{len(chunks)}{size}", source,
                                       f"{label}, {period}{part}", start, end, priority)
    return added


def digest(store: Store, job) -> str:
    """Текст порции для разбора — собирается заново из сырых данных."""
    part, _, size = job["key"].rsplit("|", 1)[1].partition("@")
    number, total = map(int, part.split("/"))
    limit = int(size) if size else LEGACY_CHUNK  # порцию режем так же, как при планировании
    if job["source"] == "person":
        units = _person_units(store, job["key"].split("|")[1], job["period_start"],
                              job["period_end"], limit)
    elif job["source"] == "stats":
        units = _stats_units(store, job["period_start"], job["period_end"])
    else:
        units = _units(store, job["source"], job["period_start"], job["period_end"])
    chunks = _pack(units, limit)
    return chunks[number - 1] if number <= len(chunks) else ""


class Investigation:
    def __init__(self, store: Store, assistant: Assistant, notify: Callable[[str], None],
                 telegrams: dict[str, TelegramAccount], health: Health):
        self.store = store
        self.assistant = assistant
        self.telegrams = telegrams  # личный и рабочий
        self.on_login_required = None  # ядро: начать вход в аккаунт (код — через бота)
        self.health = health
        self.import_dir = config.DATA_DIR / "raw" / "import"
        self.notify = notify  # сообщение пользователю (маскот + Telegram)
        self.collecting = False
        self._attempts: dict[int, int] = {}
        self._last_chat = datetime.min

    # --- сбор ---

    def _due(self, source: str, every: int) -> bool:
        last = self.store.cursor(f"collected:{source}")
        return last is None or datetime.now() - datetime.fromisoformat(last) > timedelta(seconds=every)

    def _mark(self, source: str) -> None:
        self.store.set_cursor(f"collected:{source}", datetime.now().isoformat(timespec="seconds"))

    async def collect(self) -> dict[str, int]:
        if self.collecting:
            return {}
        self.collecting = True
        if self.store.cursor("investigation_started") is None:
            self.store.set_cursor("investigation_started",
                                  datetime.now().isoformat(timespec="seconds"))
        since = datetime.now() - timedelta(days=HISTORY_DAYS)
        found: dict[str, int] = {}
        try:
            try:
                events, cursor = await asyncio.to_thread(chrome.collect, since,
                                                         self.store.cursor("chrome"))
                found["chrome"] = self.store.add_events(events)
                self.store.set_cursor("chrome", cursor)
            except Exception:
                log.exception("Сбор Chrome не удался")

            if os.getenv("GMAIL_ADDRESS") and os.getenv("GMAIL_PASSWORD"):
                try:
                    last = self.store.cursor("gmail")
                    start = datetime.fromisoformat(last) - timedelta(days=2) if last else since
                    found["gmail"] = self.store.add_events(await asyncio.to_thread(
                        gmail.collect, os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_PASSWORD"],
                        start))
                    self.store.set_cursor("gmail", datetime.now().isoformat(timespec="seconds"))
                    self.health.clear("gmail", "Почта снова собирается.")
                except imaplib.IMAP4.error as e:
                    log.error("Gmail не пускает: %s", e)
                    self.health.report("gmail", "Gmail не пускает: пароль приложения отозван или "
                                       "неверен. Создай новый на myaccount.google.com/apppasswords "
                                       "и замени GMAIL_PASSWORD в .env.")
                except Exception:
                    log.exception("Сбор Gmail не удался")  # обычно сеть — повторим в следующий раз

            for account in self.telegrams.values():
                profile = account.profile
                if not profile.configured:
                    continue
                key = "telegram" if profile.personal else f"telegram_{profile.key}"
                try:
                    found[key], _ = await account.collect(self.store, since)
                    self.health.clear(key, f"{profile.label.capitalize()} Telegram снова на связи.")
                except TelegramLoginRequired:
                    self.health.report(key, f"Нет входа в {profile.label} Telegram — вхожу сам, "
                                       "жду код в боте (или /login).")
                    if self.on_login_required:
                        await self.on_login_required(profile.key)
                except Exception:
                    log.exception("Сбор Telegram (%s) не удался", profile.label)
            if any(a.profile.configured for a in self.telegrams.values()):
                await self._update_style()

            try:
                found["notification"] = self.store.add_events(
                    await asyncio.to_thread(notifications.collect))
            except Exception:
                log.exception("Сбор уведомлений не удался")
            await self._update_categories()
            found["lk"] = await self._collect_university()

            found["takeout"] = await self._collect_takeout()
            found["ai_export"] = await self._collect_ai_exports()

            for source, module in (("document", documents), ("ai_chat", ai_chats),
                                   ("creative", creative)):
                if not self._due(source, SLOW_SOURCES_EVERY):
                    continue
                try:
                    result = await asyncio.to_thread(module.collect)
                    events = result[0] if isinstance(result, tuple) else result
                    found[source] = self.store.add_events(events)
                    self._mark(source)
                except Exception:
                    log.exception("Сбор %s не удался", source)

            jobs = plan(self.store)
            log.info("Сбор: %s; новых порций для разбора: %d", found, jobs)
            return found
        finally:
            self.collecting = False

    async def _collect_takeout(self) -> int:
        """Новые архивы Google Takeout в папке импорта — каждый читается один раз."""
        fresh = [p for p in takeout.archives(self.import_dir)
                 if self.store.import_status(takeout.archive_key(p)) != "done"]
        fresh += [p for p in takeout.unpacked(self.import_dir)
                  if self.store.import_status(f"takeout-dir:{p}") != "done"]
        if not fresh:
            return 0
        since = datetime.now() - timedelta(days=TAKEOUT_DAYS)
        try:
            added = self.store.add_events(await asyncio.to_thread(takeout.collect, fresh, since))
            documents_out = await asyncio.to_thread(takeout.extract_documents, fresh,
                                                    self.import_dir)
        except Exception:
            log.exception("Импорт Google Takeout не удался")
            return 0
        if documents_out:
            log.info("Из Takeout в папку импорта: %s", ", ".join(documents_out))
        for path in fresh:
            key = takeout.archive_key(path) if path.is_file() else f"takeout-dir:{path}"
            self.store.record_import(path.name, key, "done", f"Takeout: событий {added}")
        log.info("Google Takeout: %d архив(ов), новых событий %d", len(fresh), added)
        return added

    async def _collect_ai_exports(self) -> int:
        """Выгрузки ChatGPT и Claude.ai из папки импорта — каждая читается один раз."""
        def key(path):
            return f"aiexport:{path.name}:{path.stat().st_size}"
        fresh = [p for p in ai_chats.export_files(self.import_dir)
                 if self.store.import_status(key(p)) != "done"]
        if not fresh:
            return 0
        try:
            added = self.store.add_events(await asyncio.to_thread(ai_chats.collect_exports, fresh))
        except Exception:
            log.exception("Импорт выгрузки чатов с ИИ не удался")
            return 0
        for path in fresh:
            self.store.record_import(path.name, key(path), "done", f"Чаты с ИИ: реплик {added}")
        log.info("Выгрузки чатов с ИИ: %d файл(ов), новых реплик %d", len(fresh), added)
        return added

    async def _collect_university(self) -> int:
        """Раз в сутки: новости вуза и личный кабинет. Важное — сообщением пользователю,
        страницы кабинета (новые и изменившиеся) — событиями для разбора."""
        if not self._due("university", UNIVERSITY_EVERY):
            return 0
        added, ok = 0, True
        try:
            await self._university_news()
        except BrainError as e:
            ok = False
            log.warning("Новости вуза не разобраны: %s", e)
        except Exception as e:
            ok = False
            log.warning("Новости вуза не прочитаны: %s", e)
        if lk.credentials() or lk.SESSION.exists():
            try:
                added = await self._university_lk()
                self.health.clear("lk", "Личный кабинет вуза снова читается.")
            except lk.LkUnavailable as e:
                ok = False  # сбой у вуза — попробуем при следующем сборе, без тревоги
                log.info("Личный кабинет недоступен: %s", e)
            except lk.LkLoginFailed as e:
                ok = False
                self.health.report("lk", f"Не могу войти в личный кабинет вуза: {e}")
            except BrainError as e:
                ok = False
                log.warning("Изменения в ЛК не разобраны: %s", e)
        if ok:
            self._mark("university")
        return added

    async def _university_news(self) -> None:
        items = await asyncio.to_thread(site.news, 2)
        seen = json.loads(self.store.cursor("university:news") or "null")
        fresh = [i for i in items if seen is not None and i["url"] not in seen]
        if fresh:  # в первый раз только запоминаем: старые новости — не новость
            data = "\n\n".join(f"### {i['title']} ({i['date']})\n{i['text']}\n{i['url']}"
                                for i in fresh)
            message = await self.assistant.university_digest("свежие новости сайта вуза", data)
            if message:
                self.notify(f"🎓 {message}")
        urls = [i["url"] for i in items] + [u for u in (seen or []) if u not in
                                            {i["url"] for i in items}]
        self.store.set_cursor("university:news", json.dumps(urls[:NEWS_SEEN]))

    async def _university_lk(self) -> int:
        old = lk.load_snapshot()
        new = await asyncio.to_thread(lk.crawl)
        diff = lk.changes(old, new)
        now = datetime.now().isoformat(timespec="seconds")
        added = self.store.add_events([{
            "source": "lk", "external_id": f"{d['url']}|{new[d['url']]['hash']}", "ts": now,
            "thread": d["title"] or d["url"], "text": d["text"], "meta": {"url": d["url"]},
        } for d in diff])
        if old and diff:
            data = "\n\n".join(f"### {'Новая страница' if d['new'] else 'Изменилась'}: "
                                f"{d['title']}\n{d['url']}\n{d['text'][:4000]}" for d in diff)
            message = await self.assistant.university_digest(
                "изменения в его личном кабинете студента", data)
            if message:
                self.notify(f"🎓 {message}")
        elif not old:
            self.notify(f"🎓 Прочитала твой личный кабинет вуза: {len(new)} страниц. Дальше буду "
                        "смотреть его раз в день и писать, если что-то важное изменится.")
        lk.save_snapshot(new)
        log.info("Личный кабинет: страниц %d, новых и изменившихся %d", len(new), len(diff))
        return added

    async def _update_categories(self) -> None:
        """Новые программы и сайты, на которые ушло время, — по категориям (для метрик)."""
        if not self._due("categories", CATEGORIZE_EVERY):
            return
        items = metrics.uncategorized(self.store)
        if not items:
            return
        try:
            categories = await self.assistant.categorize(items)
        except BrainError as e:
            log.warning("Разметка программ и сайтов не удалась: %s", e)
            return
        self.store.set_activity_categories(categories)
        self._mark("categories")
        log.info("Размечено программ и сайтов: %d из %d", len(categories), len(items))

    async def refresh_voice(self) -> None:
        """По просьбе: стиль и голос сейчас, а не по расписанию."""
        summary = await self._update_style(force=True)
        if summary:
            self.notify(f"🗣 Обновила свой голос: {summary}")

    async def _update_style(self, force: bool = False) -> str:
        """Раз в неделю: стиль переписки пользователя, затем голос Ани — «он, но лучше»."""
        if not force and not self._due("style", STYLE_EVERY):
            return ""
        messages = self.store.my_telegram_messages(STYLE_SAMPLE)
        if len(messages) < 100:
            return ""
        try:
            await self.assistant.describe_style(_sample(messages, STYLE_CHARS))
            log.info("Стиль переписки обновлён")
            work = self.store.my_telegram_messages(STYLE_SAMPLE, "work")
            if len(work) >= WORK_STYLE_MIN:
                await self.assistant.describe_style(_sample(work, STYLE_CHARS), work=True)
                log.info("Рабочий стиль переписки обновлён по %d сообщениям", len(work))
            # как он рассуждает: реплики в чатах AI Studio из папки импорта и в Claude Code
            replicas = [r for p in sorted(self.import_dir.glob("*"))
                        if p.is_file() and p.suffix.lower() in ("", ".json")
                        and p.stat().st_size < 50_000_000
                        for r in ingest.user_replicas(p)]
            # поровну из обоих источников, свежие первыми
            thinking = (_sample(replicas[::-1], THINKING_CHARS // 2) + "\n"
                        + _sample(self.store.my_ai_messages(400), THINKING_CHARS // 2)).strip()
            summary = await self.assistant.describe_persona(thinking)
            self._mark("style")
            log.info("Голос Ани обновлён: %s", summary)
            return summary
        except BrainError as e:
            log.warning("Стиль и голос не обновлены: %s", e)
            return ""

    async def collect_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            await self.collect()
            await asyncio.sleep(COLLECT_EVERY)

    # --- разбор ---

    def touch(self) -> None:
        """Пользователь общается с ботом — разбор уступает ему лимит подписки."""
        self._last_chat = datetime.now()

    def _pause(self) -> float:
        """Сколько ждать перед следующей порцией: лимит подписки общий с чатом и Claude Code.
        Ночью или пока пользователя нет за ПК — быстро, иначе медленно."""
        now = datetime.now()
        if NIGHT_HOURS[0] <= now.hour < NIGHT_HOURS[1] or windows.idle_seconds() >= AWAY_SECONDS:
            return NIGHT_PAUSE
        return DAY_PAUSE

    async def _rest(self) -> None:
        """Пауза между порциями Claude; отошёл пользователь посреди паузы — она сокращается.
        Пока Claude отдыхает, второй ИИ разбирает свои порции: лимит Claude они не тратят."""
        started = time.monotonic()
        while time.monotonic() - started < self._pause():
            helper = self.assistant.helper
            job = self.store.next_job(HELPER_SOURCES) if helper and helper.usable else None
            if job is None:
                await asyncio.sleep(30)
            elif await self._process(job):
                await asyncio.sleep(HELPER_PAUSE)

    async def worker(self) -> None:
        await asyncio.sleep(90)
        while True:
            quiet_left = (self._last_chat + QUIET_AFTER_CHAT - datetime.now()).total_seconds()
            if quiet_left > 0:
                await asyncio.sleep(quiet_left)
                continue
            job = self.store.next_job()
            if job is None:
                await self._maybe_dossier()
                await asyncio.sleep(300)
                continue
            engine = await self._process(job)
            if engine is None:
                continue
            await self._maybe_consolidate()
            await self._maybe_dossier()
            if engine == "claude":  # лимит Claude общий с чатом — бережём
                await self._rest()
            else:
                await asyncio.sleep(HELPER_PAUSE)

    async def _process(self, job) -> str | None:
        """Разобрать одну порцию. Возвращает, кто разбирал (claude/openai), или None, если
        порция пустая или не вышло (тогда она повторится позже)."""
        text = digest(self.store, job)
        if not text.strip():
            self.store.finish_job(job["id"], "skipped", "", 0)
            return None
        try:
            outcome = await self.assistant.analyze(job["title"], text, job["source"])
        except BrainError as e:
            if e.kind in ("limit", "auth", "missing"):
                # не вина порции: ждём, пока лимит сбросится или пользователь починит вход
                wait = (e.reset_at - datetime.now()).total_seconds() + 120 if e.reset_at \
                    else LIMIT_PAUSE if e.is_limit else AUTH_WAIT
                log.warning("Разбор на паузе до %s (%s): %s",
                            (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M"),
                            e.kind, e)
                await asyncio.sleep(max(60, wait))
                return None
            # обрыв сети, сон компьютера — пробуем ещё, прежде чем сдаться
            self._attempts[job["id"]] = self._attempts.get(job["id"], 0) + 1
            if self._attempts[job["id"]] < MAX_ATTEMPTS:
                log.warning("Порция «%s»: попытка %d не удалась (%s), повторю позже",
                            job["title"], self._attempts[job["id"]], e)
                await asyncio.sleep(RETRY_PAUSE)
            else:
                log.error("Порция «%s» не разобрана: %s", job["title"], e)
                self.store.finish_job(job["id"], "failed", str(e), len(text))
            return None
        self.store.finish_job(job["id"], "done", outcome.reply, len(text))
        log.info("Разобрано (%s): %s (%d симв., изменений %d)", outcome.engine, job["title"],
                 len(text), len(outcome.changes))
        return outcome.engine

    async def _maybe_consolidate(self) -> None:
        """Каждые CONSOLIDATE_EVERY порций — уборка памяти: дубли, устаревшее, двойники людей."""
        last = self.store.cursor("consolidated_at") or ""
        if self.store.jobs_done_since(last) < CONSOLIDATE_EVERY:
            return
        try:
            outcome = await self.assistant.consolidate()
        except BrainError as e:
            log.warning("Уборка памяти отложена: %s", e)
            return
        self.store.set_cursor("consolidated_at", datetime.now().isoformat(timespec="seconds"))
        log.info("Уборка памяти: %s (изменений %d)", outcome.reply, len(outcome.changes))

    # --- досье ---

    async def _maybe_dossier(self) -> None:
        """Первое досье — после свежих данных, обновлённое — когда разобрано всё."""
        made = self.store.cursor("dossier_at")
        if made is None:
            ready = self.store.job_counts(min_priority=2).get("pending", 0) == 0 \
                and self.store.job_counts().get("done", 0) > 0 and not self._waiting_for_telegram()
        else:
            done_at = self.store.last_job_done_at() or ""
            ready = self.store.job_counts().get("pending", 0) == 0 and done_at > made
        if ready:
            await self.make_dossier()

    def _waiting_for_telegram(self) -> bool:
        """Переписки — главный источник: первое досье ждёт их до TELEGRAM_WAIT после начала."""
        if not os.getenv("TG_API_ID") or self.store.event_counts().get("telegram"):
            return False
        started = self.store.cursor("investigation_started")
        return started is not None and \
            datetime.now() - datetime.fromisoformat(started) < TELEGRAM_WAIT

    async def make_dossier(self) -> str | None:
        results = self.store.job_results()
        observations = "\n\n".join(f"### {r['title']}\n{r['result']}" for r in results)[:150_000]
        try:
            summary, text = await self.assistant.dossier(observations)
        except BrainError as e:
            log.error("Досье не составлено: %s", e)
            return None
        DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
        path = DOSSIER_DIR / f"dossier-{datetime.now():%Y-%m-%d_%H-%M}.md"
        path.write_text(text, encoding="utf-8")
        (DOSSIER_DIR / "latest.md").write_text(text, encoding="utf-8")
        try:  # копия в папку Ани — пользователю под рукой
            config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
            (config.DOCUMENTS_DIR / "Досье.md").write_text(text, encoding="utf-8")
        except OSError:
            log.warning("Не удалось положить досье в %s", config.DOCUMENTS_DIR)
        self.store.set_cursor("dossier_at", datetime.now().isoformat(timespec="seconds"))
        pending = self.store.job_counts().get("pending", 0)
        tail = f" Остальной год ещё разбираю в фоне (осталось порций: {pending})." if pending else ""
        self.notify(f"🕵 Досье готово: {summary}{tail}\nФайл: {path}")
        log.info("Досье сохранено: %s", path)
        return str(path)

    def status(self) -> dict:
        return {
            "events": self.store.event_counts(),
            "jobs": self.store.job_counts(),
            "collecting": self.collecting,
            "dossier_at": self.store.cursor("dossier_at"),
        }
