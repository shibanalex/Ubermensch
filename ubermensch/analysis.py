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
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta

from . import config
from .assistant import Assistant
from .brain import BrainError
from .collectors import ai_chats, chrome, creative, documents, gmail, notifications, takeout
from .collectors.telegram import TelegramAccount, TelegramLoginRequired
from .health import Health
from .store import Store

log = logging.getLogger(__name__)

HISTORY_DAYS = 365
RECENT_DAYS = 30
CHUNK_CHARS = 60_000
COLLECT_EVERY = 2 * 3600
SLOW_SOURCES_EVERY = 24 * 3600  # документы и архив чатов с ИИ меняются редко
# Лимит подписки Claude общий для разбора, бота и Claude Code: днём разбор идёт медленно
# и уступает разговорам, ночью — быстро.
DAY_PAUSE = 15 * 60  # не больше 4 порций в час
NIGHT_PAUSE = 90
NIGHT_HOURS = (1, 8)
QUIET_AFTER_CHAT = timedelta(minutes=10)
LIMIT_PAUSE = 30 * 60  # если время сброса лимита неизвестно
AUTH_WAIT = 5 * 60  # Claude разлогинен или не найден — проверяем снова через 5 минут
TELEGRAM_WAIT = timedelta(hours=24)
STYLE_EVERY = 7 * 24 * 3600
STYLE_SAMPLE, STYLE_CHARS = 2500, 35_000  # свежие сообщения пользователя для описания стиля
MAX_ATTEMPTS = 3
RETRY_PAUSE = 5 * 60
DOSSIER_DIR = config.DATA_DIR / "dossier"
SOURCES = {"telegram": "Telegram", "gmail": "Почта", "chrome": "Браузер",
           "ai_chat": "Разговоры с ИИ", "activity": "Активность за компьютером",
           "document": "Документы", "creative": "Творчество", "notification": "Уведомления",
           "youtube": "YouTube", "google_search": "Поиск Google",
           "google_activity": "Действия в сервисах Google"}
TAKEOUT_DAYS = 2 * 365  # из Google Takeout берём два года: интересы видны в динамике
GROUP_LINES = 700  # строк на сервис в месяц (просмотры, запросы, уведомления) — дальше шум
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
          "октябрь", "ноябрь", "декабрь"]


# --- нарезка данных на порции ---

def _dm(ts: str) -> str:
    return f"{ts[8:10]}.{ts[5:7]}"


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


def plan(store: Store) -> int:
    """Ставит в очередь новые порции. Возвращает, сколько добавлено."""
    today = date.today()
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    added = 0
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
                added += store.add_job(f"{prefix}{tag}|{number}/{len(chunks)}", source,
                                       f"{label}, {period}{part}", start, end, priority)
    return added


def digest(store: Store, job) -> str:
    """Текст порции для разбора — собирается заново из сырых данных."""
    number, total = map(int, job["key"].rsplit("|", 1)[1].split("/"))
    chunks = _pack(_units(store, job["source"], job["period_start"], job["period_end"]))
    return chunks[number - 1] if number <= len(chunks) else ""


class Investigation:
    def __init__(self, store: Store, assistant: Assistant, notify: Callable[[str], None],
                 telegram: TelegramAccount, health: Health):
        self.store = store
        self.assistant = assistant
        self.telegram = telegram
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

            if os.getenv("TG_API_ID") and os.getenv("TG_API_HASH"):
                try:
                    found["telegram"], _ = await self.telegram.collect(self.store, since)
                    self.health.clear("telegram", "Telegram снова на связи.")
                except TelegramLoginRequired:
                    self.health.report("telegram", "Нет входа в Telegram (сессия завершена или "
                                       "удалена). Запусти telegram_login.bat в папке проекта.")
                except Exception:
                    log.exception("Сбор Telegram не удался")
                await self._update_style()

            try:
                found["notification"] = self.store.add_events(
                    await asyncio.to_thread(notifications.collect))
            except Exception:
                log.exception("Сбор уведомлений Windows не удался")

            found["takeout"] = await self._collect_takeout()

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
        except Exception:
            log.exception("Импорт Google Takeout не удался")
            return 0
        for path in fresh:
            key = takeout.archive_key(path) if path.is_file() else f"takeout-dir:{path}"
            self.store.record_import(path.name, key, "done", f"Takeout: событий {added}")
        log.info("Google Takeout: %d архив(ов), новых событий %d", len(fresh), added)
        return added

    async def _update_style(self) -> None:
        """Раз в неделю — как пользователь пишет в Telegram (для бота и сообщений от его имени)."""
        if not self._due("style", STYLE_EVERY):
            return
        messages = self.store.my_telegram_messages(STYLE_SAMPLE)
        if len(messages) < 100:
            return
        sample, size = [], 0
        for text in messages:
            if size + len(text) > STYLE_CHARS:
                break
            sample.append(f"— {text}")
            size += len(text)
        try:
            await self.assistant.describe_style("\n".join(sample))
            self._mark("style")
            log.info("Стиль переписки обновлён по %d сообщениям", len(sample))
        except BrainError as e:
            log.warning("Стиль переписки не обновлён: %s", e)

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
        """Сколько ждать перед следующей порцией: лимит подписки общий с чатом и Claude Code."""
        now = datetime.now()
        if NIGHT_HOURS[0] <= now.hour < NIGHT_HOURS[1]:
            return NIGHT_PAUSE
        return DAY_PAUSE

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
            text = digest(self.store, job)
            if not text.strip():
                self.store.finish_job(job["id"], "skipped", "", 0)
                continue
            try:
                outcome = await self.assistant.analyze(job["title"], text)
            except BrainError as e:
                if e.kind in ("limit", "auth", "missing"):
                    # не вина порции: ждём, пока лимит сбросится или пользователь починит вход
                    wait = (e.reset_at - datetime.now()).total_seconds() + 120 if e.reset_at \
                        else LIMIT_PAUSE if e.is_limit else AUTH_WAIT
                    log.warning("Разбор на паузе до %s (%s): %s",
                                (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M"),
                                e.kind, e)
                    await asyncio.sleep(max(60, wait))
                    continue
                # обрыв сети, сон компьютера — пробуем ещё, прежде чем сдаться
                self._attempts[job["id"]] = self._attempts.get(job["id"], 0) + 1
                if self._attempts[job["id"]] < MAX_ATTEMPTS:
                    log.warning("Порция «%s»: попытка %d не удалась (%s), повторю позже",
                                job["title"], self._attempts[job["id"]], e)
                    await asyncio.sleep(RETRY_PAUSE)
                else:
                    log.error("Порция «%s» не разобрана: %s", job["title"], e)
                    self.store.finish_job(job["id"], "failed", str(e), len(text))
                continue
            self.store.finish_job(job["id"], "done", outcome.reply, len(text))
            log.info("Разобрано: %s (%d симв., изменений %d)", job["title"], len(text),
                     len(outcome.changes))
            await self._maybe_dossier()
            await asyncio.sleep(self._pause())

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
