"""Самостоятельность Ани: heartbeat, поручения, расписание, вечерняя рефлексия, навыки.

Устроено по мотивам OpenClaw: агент сам просыпается, сверяется со списком, решает, нужно
ли что-то сделать, ведёт свой дневник и долгую память в обычных файлах и учится на опыте.

- Heartbeat — раз в HEARTBEAT_EVERY: время, состояние пользователя, задачи, его заметки,
  свежие события и список «08 AI/Reports/Heartbeat.md» (его можно править в Obsidian).
  Если с прошлого раза ничего не поменялось — Claude даже не вызывается. Аня решает:
  промолчать, написать (не чаще MAX_MESSAGES в день и MIN_MESSAGE_GAP между) или сделать
  что-то своими инструментами (те же подтверждения, что в чате).
- Поручения — «разберись и доложи»: фоновая работа в несколько шагов с инструментами и
  поиском; не уложилась — продолжает в следующий заход; отчёт — в «Reports/Поручения».
- Расписание — регулярные поручения: каждый день / по дню недели / каждые N часов.
- Рефлексия — раз в сутки вечером: что было за день, что он поправлял, на что отвечал и
  что пропускал → обновлённые «Уроки Ани» (как ему помогать) и запись в её дневнике.
- Навыки — удачные способы решения задач, которые Аня записывает себе сама.
Уроки, дневник за два дня, подходящие навыки и открытые поручения — в каждом разговоре.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from . import config
from .brain import BrainError
from .runtime.http_tools import ToolExecutionUncertain
from .runtime.router import InterruptedToolRun
from .runtime.jobs import JobCancelled
from .collectors import obsidian

log = logging.getLogger(__name__)

HEARTBEAT_EVERY = 30 * 60
HEARTBEAT_HOURS = range(9, 24)  # ночью не будить
FORCE_HEARTBEAT = timedelta(hours=3)  # даже без перемен — заглянуть раз в столько
MAX_MESSAGES = 4  # своих сообщений в день
MIN_MESSAGE_GAP = timedelta(minutes=90)
QUIET_AFTER_CHAT = timedelta(minutes=10)
REFLECT_FROM = 23  # рефлексия — после этого часа (или ночью, до 5)
REVIEW_WEEKDAY, REVIEW_FROM = 6, 19  # обзор недели — в воскресенье с 19:00
TASK_SESSIONS = 3  # поручение: столько заходов, потом — «не вышло»
REST_AFTER_TASK = 15 * 60
LESSONS_CHARS, JOURNAL_CHARS, SKILLS_CHARS = 6000, 3000, 4000
HEARTBEAT_MODEL, HEARTBEAT_EFFORT = "sonnet", "medium"
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

DEFAULT_CHECKLIST = """# Heartbeat — за чем Аня следит сама

Аня перечитывает этот список раз в полчаса днём и решает, нужно ли что-то сделать или
написать. Правь его как хочешь: добавляй, вычёркивай, меняй тон.

- Сроки: задачи на сегодня и просроченные, дедлайны в вузе, напоминания.
- Если я долго сижу за компьютером без перерыва или засиделся ночью — мягко скажи.
- Если в личном кабинете вуза, почте или переписке появилось важное — коротко сообщи.
- Если в дневнике или «Сейчас» что-то новое — учти, помоги с этим.
- Не пиши просто так: только когда есть польза. Лучше промолчать, чем отвлечь.
"""

_lock = threading.Lock()


# --- файлы ---

def _agent_dir() -> Path:
    return config.DATA_DIR / "agent"


def _read_json(name: str) -> list[dict]:
    try:
        return json.loads((_agent_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_json(name: str, items: list[dict]) -> None:
    folder = _agent_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{name}.tmp"
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(folder / name)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checklist_path() -> Path:
    return config.OUTPUT_DIR / "Heartbeat.md"


def lessons_path() -> Path:
    return config.OUTPUT_DIR / "Уроки Ани.md"


def journal_path(day: date) -> Path:
    return config.OUTPUT_DIR / "Дневник Ани" / f"{day.isoformat()}.md"


def skills_dir() -> Path:
    return config.OUTPUT_DIR / "Навыки"


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit].strip()
    except OSError:
        return ""


def journal(text: str, when: datetime | None = None) -> None:
    """Строка в дневник Ани за сегодня."""
    when = when or datetime.now()
    path = journal_path(when.date())
    with _lock:
        head = "" if path.exists() else f"# Дневник Ани — {when:%d.%m.%Y}\n\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{head}- {when:%H:%M} {text.strip()}\n")


# --- поручения и расписание ---

def add_task(title: str, instructions: str, *, due: str | None = None,
             source: str = "user", schedule_id: str | None = None) -> dict:
    task = {"id": uuid4().hex[:8], "title": title.strip()[:200],
            "instructions": instructions.strip()[:6000], "due": due, "status": "pending",
            "sessions": 0, "notes": "", "source": source, "schedule_id": schedule_id,
            "created_at": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        _write_json("tasks.json", _read_json("tasks.json") + [task])
    return task


def tasks(status: str | None = None) -> list[dict]:
    return [t for t in _read_json("tasks.json") if status is None or t["status"] == status]


def _update_task(task_id: str, **changes) -> None:
    with _lock:
        items = _read_json("tasks.json")
        for item in items:
            if item["id"] == task_id:
                item.update(changes)
        _write_json("tasks.json", items)


def cancel_task(task_id: str) -> bool:
    found = any(t["id"] == task_id and t["status"] in {'pending', 'interrupted'} for t in tasks())
    if found:
        _update_task(task_id, status="cancelled")
    return found


def next_run(schedule: dict, after: datetime) -> datetime:
    """Следующий запуск после after: every = day | week | hours."""
    if schedule["every"] == "hours":
        return after + timedelta(hours=max(1, int(schedule.get("hours") or 1)))
    hour, minute = (int(x) for x in (schedule.get("at") or "09:00").split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule["every"] == "day":
        return candidate if candidate > after else candidate + timedelta(days=1)
    weekday = int(schedule.get("weekday") or 0)
    candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    return candidate if candidate > after else candidate + timedelta(days=7)


def add_schedule(title: str, instructions: str, every: str, at: str = "09:00",
                 weekday: int | None = None, hours: int | None = None) -> dict:
    if every not in ("day", "week", "hours"):
        raise ValueError("every: day, week или hours")
    if every != "hours" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
        raise ValueError("at: ЧЧ:ММ")
    schedule = {"id": uuid4().hex[:8], "title": title.strip()[:200],
                "instructions": instructions.strip()[:6000], "every": every, "at": at,
                "weekday": weekday, "hours": hours, "active": True}
    schedule["next_at"] = next_run(schedule, datetime.now()).isoformat(timespec="minutes")
    with _lock:
        _write_json("schedules.json", _read_json("schedules.json") + [schedule])
    return schedule


def schedules() -> list[dict]:
    return [s for s in _read_json("schedules.json") if s.get("active")]


def cancel_schedule(schedule_id: str) -> bool:
    with _lock:
        items = _read_json("schedules.json")
        found = False
        for item in items:
            if item["id"] == schedule_id and item.get("active"):
                item["active"], found = False, True
        _write_json("schedules.json", items)
    return found


def describe_schedule(s: dict) -> str:
    when = {"day": f"каждый день в {s['at']}", "hours": f"каждые {s.get('hours') or 1} ч",
            "week": f"каждый {WEEKDAYS[int(s.get('weekday') or 0)]} в {s['at']}"}[s["every"]]
    return f"{s['id']} «{s['title']}» — {when}, следующий: {s['next_at'].replace('T', ' ')}"


def due_schedules(now: datetime) -> list[dict]:
    """Расписания, которым пора: создают поручения, сдвигают next_at."""
    fired = []
    with _lock:
        items = _read_json("schedules.json")
        for item in items:
            if item.get("active") and datetime.fromisoformat(item["next_at"]) <= now:
                fired.append(dict(item))
                item["next_at"] = next_run(item, now).isoformat(timespec="minutes")
        if fired:
            _write_json("schedules.json", items)
    return fired


# --- навыки ---

def _slug(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip()[:80] or "навык"


def save_skill(name: str, when: str, steps: str) -> Path:
    path = skills_dir() / f"{_slug(name)}.md"
    _write_text(path, f"# {name.strip()}\n\nКогда пригодится: {when.strip()}\n\n{steps.strip()}\n")
    return path


def relevant_skills(query: str, limit: int = SKILLS_CHARS) -> str:
    """Навыки, чьё название или «когда пригодится» совпадает со словами запроса."""
    folder = skills_dir()
    if not folder.is_dir():
        return ""
    words = {w[:5] for w in re.findall(r"\w+", query.lower()) if len(w) > 3}
    scored = []
    for path in folder.glob("*.md"):
        text = _read(path, 3000)
        head = " ".join(text.splitlines()[:3]).lower()
        score = len(words & {w[:5] for w in re.findall(r"\w+", head) if len(w) > 3})
        if score:
            scored.append((score, path.stem, text))
    names = sorted(p.stem for p in folder.glob("*.md"))
    parts, size = [], 0
    for _, _, text in sorted(scored, reverse=True):
        if size + len(text) > limit:
            break
        parts.append(text)
        size += len(text)
    listing = "Все навыки: " + ", ".join(names) if names else ""
    return "\n\n".join([listing, *parts]).strip()


# --- контекст для каждого разговора ---

def context_block(query: str = "") -> str:
    """Уроки, дневник Ани за два дня, подходящие навыки, открытые поручения и расписание."""
    parts = []
    if lessons := _read(lessons_path(), LESSONS_CHARS):
        parts.append(f"<anya_lessons note=\"твои уроки о том, как ему помогать — ты пишешь их "
                     f"сама по опыту\">\n{lessons}\n</anya_lessons>")
    today = date.today()
    days = [_read(journal_path(today - timedelta(days=n)), JOURNAL_CHARS // 2) for n in (1, 0)]
    if any(days):
        parts.append("<anya_journal note=\"твой дневник: что ты делала и замечала\">\n"
                     + "\n\n".join(d for d in days if d) + "\n</anya_journal>")
    if skills := relevant_skills(query):
        parts.append(f"<anya_skills>\n{skills}\n</anya_skills>")
    open_tasks = tasks("pending")
    active = schedules()
    if open_tasks or active:
        lines = [f"- поручение {t['id']}: {t['title']}" + (f" (срок {t['due']})" if t["due"] else "")
                 for t in open_tasks] + [f"- расписание {describe_schedule(s)}" for s in active]
        parts.append("<anya_tasks>\n" + "\n".join(lines) + "\n</anya_tasks>")
    return "\n\n".join(parts)


# --- служба ---

HEARTBEAT_EXTRA = {
    "decision": {"type": "string", "enum": ["silent", "message", "act"],
                 "description": "silent — промолчать; message — написать ему reply; "
                                "act — сделала что-то инструментами (reply — коротко что)"},
    "journal": {"type": "string", "description": "одна строка в твой дневник: что заметила и решила"},
}
TASK_EXTRA = {
    "status": {"type": "string", "enum": ["done", "continue", "failed"]},
    "report": {"type": "string", "description": "отчёт Markdown (для done), иначе пусто"},
    "notes": {"type": "string", "description": "что сделано и что осталось — для следующего захода"},
    "journal": {"type": "string"},
}
REFLECT_EXTRA = {
    "lessons": {"type": "string", "description": "полный новый текст «Уроков Ани», Markdown"},
    "journal": {"type": "string", "description": "итог дня в твой дневник, 3–8 предложений"},
    "skills": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "when", "steps"],
        "properties": {"name": {"type": "string"}, "when": {"type": "string"},
                       "steps": {"type": "string"}}}},
}


class Autonomy:
    def __init__(self, store, assistant, investigation, core):
        self.store, self.assistant, self.investigation, self.core = \
            store, assistant, investigation, core
        self._last_fingerprint = ""
        self._last_heartbeat = datetime.min
        self.state = {"heartbeat": None, "task": None}

    # --- heartbeat ---

    def _messages_today(self) -> list[datetime]:
        sent = [datetime.fromisoformat(m["at"]) for m in _read_json("messages.json")]
        return [m for m in sent if m.date() == date.today()]

    def _fingerprint(self, now: datetime) -> str:
        """Что могло измениться: задачи, напоминания, заметки «Сейчас» и дневник, новые
        события, состояние пользователя, трёхчасовой отрезок дня."""
        from .system import windows

        idle = windows.idle_seconds()
        presence = "нет" if idle > 1800 else "отошёл" if idle > 300 else "за ПК"
        events = self.store.db.execute("SELECT COUNT(*), MAX(id) FROM events WHERE source IN "
                                       "('lk', 'gmail', 'obsidian', 'notification')").fetchone()
        parts = [now.strftime("%Y-%m-%d"), str(now.hour // 3), presence, str(tuple(events)),
                 json.dumps([dict(t) for t in self.store.tasks()], default=str),
                 json.dumps([dict(r) for r in self.store.pending_reminders()], default=str),
                 obsidian.context_notes(limit=4000), _read(checklist_path(), 4000)]
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _busy(self, now: datetime) -> bool:
        return now - self.investigation._last_chat < QUIET_AFTER_CHAT or self.core.thinking

    async def heartbeat(self, force: bool = False) -> str:
        now = datetime.now()
        if not checklist_path().exists():
            _write_text(checklist_path(), DEFAULT_CHECKLIST)
        fingerprint = self._fingerprint(now)
        if not force and fingerprint == self._last_fingerprint \
                and now - self._last_heartbeat < FORCE_HEARTBEAT:
            return "без перемен"
        self._last_fingerprint, self._last_heartbeat = fingerprint, now
        sent = self._messages_today()
        can_write = len(sent) < MAX_MESSAGES and (not sent or now - max(sent) >= MIN_MESSAGE_GAP)
        from .system import windows

        window = windows.foreground()
        situation = (f"Он сейчас: {'за компьютером' if windows.idle_seconds() < 300 else 'не трогает компьютер'}"
                     f" {int(windows.idle_seconds() // 60)} мин"
                     + (f", активное окно «{window.title}» ({window.app})" if window else "") + ".")
        task = (
            "[Системная задача: heartbeat — ты сама проснулась и проверяешь, нужна ли ему сейчас "
            "помощь.] Сверься со списком из <heartbeat> и со всем контекстом. "
            f"{situation} Своих сообщений сегодня: {len(sent)} из {MAX_MESSAGES}"
            + ("" if can_write else " — писать сейчас нельзя, только silent или act без сообщения")
            + ". Реши: silent (по умолчанию, если нет явной пользы), message (коротко, по-человечески, "
            "с конкретной пользой — не здоровайся и не повторяй уже сказанное в истории и дневнике) "
            "или act (сделай сама нужное инструментами и коротко скажи что). Задачи из его списка, "
            "которые можно сделать твоими инструментами (найти, прочитать, разобрать, подготовить, "
            "поставить в календарь), — делай сама: короткие сразу, большие — поручением. Задачи, "
            "которые по его словам, дневнику, почте или кабинету уже сделаны, закрой (done), а "
            "потерявшие смысл — drop, и коротко скажи, что закрыла. Сделанное "
            "отмечай done в поле tasks. Поручения и навыки — "
            "инструментами delegate_task и save_skill, если это уместно. Ничего не отправляй людям "
            "от его имени без его просьбы.\n\n<heartbeat>\n" + _read(checklist_path(), 4000)
            + "\n</heartbeat>")
        data, outcome = await self.assistant.agent_ask(
            task, HEARTBEAT_EXTRA, focus="heartbeat", model=HEARTBEAT_MODEL,
            effort=HEARTBEAT_EFFORT, timeout=600, source="heartbeat", api=True)
        decision = data.get("decision", "silent")
        if decision != "silent" and outcome.reply and (can_write or decision == "act"):
            self.core.notify(outcome.reply)
            if outcome.emotion and outcome.emotion != "neutral":
                self.core.bus.publish({"type": "emotion", "emotion": outcome.emotion})
            with _lock:
                _write_json("messages.json", [m for m in _read_json("messages.json")
                                              if m["at"] >= date.today().isoformat()]
                            + [{"at": now.isoformat(timespec="seconds")}])
        if data.get("journal"):
            journal(f"[heartbeat · {decision}] {data['journal']}")
        self.state["heartbeat"] = {"at": now.isoformat(timespec="minutes"), "decision": decision}
        log.info("Heartbeat: %s", decision)
        return decision

    # --- поручения ---

    async def run_task(self, item: dict) -> str:
        self.state["task"] = item["title"]
        _update_task(item["id"], status="running", sessions=item["sessions"] + 1)
        task = (
            f"[Системная задача: поручение «{item['title']}».] {item['instructions']}\n\n"
            + (f"Срок: {item['due']}.\n" if item.get("due") else "")
            + (f"Прошлые заходы: {item['notes']}\n" if item.get("notes") else "")
            + f"Это заход {item['sessions'] + 1} из {TASK_SESSIONS}. Работай сама, в несколько шагов: "
            "ищи в сети, читай его файлы и заметки, пользуйся инструментами. Готово — status done, "
            "report — полный отчёт Markdown с источниками, reply — 2–4 предложения ему в Telegram. "
            "Не успела — continue и в notes, что сделано и что дальше. Невозможно — failed, reply — "
            "почему и что нужно от него. Сообщения и письма людям — только если он это поручил.")
        try:
            data, outcome = await self.assistant.agent_ask(task, TASK_EXTRA, focus=item["instructions"],
                                                           source=f"task:{item['id']}")
        except (ToolExecutionUncertain, InterruptedToolRun, JobCancelled) as e:
            _update_task(item['id'], status='interrupted', notes=str(e))
            raise
        except asyncio.CancelledError:
            _update_task(item['id'], status='interrupted', notes='Остановлено; проверь выполненные действия.')
            raise
        except BrainError as e:
            _update_task(item["id"], status="pending" if item["sessions"] + 1 < TASK_SESSIONS
                         else "failed", notes=f"{item.get('notes', '')}\nОшибка: {e}".strip())
            raise
        finally:
            self.state["task"] = None
        status = data.get("status", "failed")
        if status == "continue" and item["sessions"] + 1 >= TASK_SESSIONS:
            status = "failed"
        if status == "continue":
            _update_task(item["id"], status="pending", notes=str(data.get("notes", ""))[:4000])
        else:
            changes = {"status": status, "finished_at": datetime.now().isoformat(timespec="seconds")}
            if status == "done" and data.get("report"):
                path = config.OUTPUT_DIR / "Поручения" / f"{date.today()} {_slug(item['title'])}.md"
                _write_text(path, f"# {item['title']}\n\n{data['report']}\n")
                changes["report"] = str(path)
                outcome.reply += f"\nОтчёт: {path}"
            _update_task(item["id"], **changes)
            if outcome.reply:
                self.core.notify(("📋 " if status == "done" else "⚠️ ") + outcome.reply)
        if data.get("journal"):
            journal(f"[поручение «{item['title']}» · {status}] {data['journal']}")
        log.info("Поручение «%s»: %s", item["title"], status)
        return status

    # --- рефлексия ---

    async def reflect(self, day: date | None = None) -> None:
        day = day or date.today()
        rows = self.store.db.execute(
            "SELECT ts, role, channel, text FROM messages WHERE substr(ts,1,10)=? ORDER BY id",
            (day.isoformat(),)).fetchall()
        if not rows and not journal_path(day).exists():
            return
        talk = "\n".join(f"[{r['ts'][11:16]} {r['channel']}] {'Он' if r['role'] == 'user' else 'Аня'}: "
                         f"{r['text'][:1500]}" for r in rows)[-60_000:]
        feedback = [i for i in _read_feedback() if i.get("at", "").startswith(day.isoformat())]
        task = (
            "[Системная задача: вечерняя рефлексия — ты учишься на прошедшем дне.] В <day> — "
            "разговоры с ним за день, твой дневник и его поправки к твоим выводам. Разбери честно: "
            "что помогло, что он поправлял или повторял, на что не ответил, где ты ошиблась или "
            "была лишней, что нового о том, как с ним лучше. Обнови «Уроки Ани»: lessons — полный "
            "новый текст (до 5000 символов, разделы «Как с ним говорить», «Что ему помогает», "
            "«Чего избегать», «Мои ошибки», «Открытые вопросы»), сохрани старое верное, убери "
            "устаревшее, обобщай, а не пересказывай. journal — итог дня. skills — 0–3 навыка, если "
            "сегодня нашёлся удачный способ решать повторяющуюся задачу. reply — пусто, если "
            "писать ему незачем.\n\n<day date=\"" + day.isoformat() + "\">\n<talk>\n" + talk
            + "\n</talk>\n<anya_day>\n" + _read(journal_path(day), 8000) + "\n</anya_day>\n"
            "<corrections>\n" + "\n".join(f"- «{i['claim']}»: {i['verdict']} {i.get('comment', '')}"
                                          for i in feedback) + "\n</corrections>\n</day>")
        data, outcome = await self.assistant.agent_ask(task, REFLECT_EXTRA, focus="рефлексия",
                                                       effort="medium", timeout=900,
                                                       source="reflection")
        if lessons := str(data.get("lessons") or "").strip():
            _write_text(lessons_path(), lessons[:LESSONS_CHARS] + "\n")
        for skill in data.get("skills") or []:
            if skill.get("name") and skill.get("steps"):
                save_skill(skill["name"], skill.get("when", ""), skill["steps"])
        if data.get("journal"):
            journal(f"[итог дня] {data['journal']}", datetime.combine(day, datetime.now().time()))
        self.store.set_cursor("reflected_on", day.isoformat())
        log.info("Рефлексия за %s: уроки обновлены, навыков %d", day, len(data.get("skills") or []))

    # --- обзор недели ---

    async def weekly_review(self, today: date | None = None) -> Path:
        """Итоги недели в его Obsidian (по его шаблону Weekly): сделанное, понятое, что менять."""
        today = today or date.today()
        start = today - timedelta(days=6)
        year, week, _ = today.isocalendar()
        done = self.store.db.execute(
            "SELECT title, substr(done_at,1,10) FROM tasks WHERE status='done' AND done_at >= ? "
            "ORDER BY done_at", (start.isoformat(),)).fetchall()
        overdue = self.store.db.execute(
            "SELECT title, due FROM tasks WHERE status='open' AND due < ? ORDER BY due",
            (today.isoformat(),)).fetchall()
        diary = self.store.db.execute(
            "SELECT external_id, text FROM events WHERE source='obsidian' AND outgoing=1 "
            "AND external_id LIKE '02 Journal/%' AND ts >= ? ORDER BY ts", (start.isoformat(),)).fetchall()
        anya_days = "\n\n".join(t for n in range(7)
                                  if (t := _read(journal_path(start + timedelta(days=n)), 3000)))
        template = _read(config.VAULT_DIR / "90 Templates" / "Weekly.md", 2000) if config.VAULT_DIR else ""
        task = (
            f"[Системная задача: итоги недели {year}-W{week:02d} ({start:%d.%m}–{today:%d.%m}).] "
            "Напиши ему обзор недели в Markdown — поле review. Структура — его шаблон из <template> "
            "(«Главное, что произошло», «Что понял или заметил», «Что изменить на следующей неделе»), "
            "плюс разделы «Цели: что сдвинулось» (по его целям и ориентирам), «Сон и энергия» (по "
            "дневнику и метрикам, если видно), «Три шага на неделю» (конкретно, под его цели). "
            "Пиши его языком, тепло и по делу, без морализаторства; ставь [[ссылки]] на его заметки "
            "дневника. Не выдумывай: чего не видно — так и скажи. reply — 2–3 предложения ему.\n\n"
            f"<template>\n{template}\n</template>\n<done_tasks>\n"
            + "\n".join(f"- {t} ({d})" for t, d in done) + "\n</done_tasks>\n<overdue>\n"
            + "\n".join(f"- {t} (срок {d})" for t, d in overdue) + "\n</overdue>\n<his_diary>\n"
            + "\n\n".join(f"### {p}\n{text[:4000]}" for p, text in diary)[:30_000]
            + f"\n</his_diary>\n<anya_journal>\n{anya_days[:20_000]}\n</anya_journal>")
        task += ('\n\n<measured_week>\n' + json.dumps(self.store.planning_review(today), ensure_ascii=False)
                 + '\n</measured_week>\nСопоставь реальные замеры, этапы откликов и результаты советов '
                 'с personal_plan. Покажи, где данных не хватает. Предложи до трёх изменений '
                 'на следующую неделю с обоснованием и затратами времени; проверь общий бюджет '
                 'времени. Изменения целей не считай принятыми без решения пользователя. '
                 'Сохрани полезные предложения через recommendation_save с датой проверки. '
                 'Не выводи причинность из нескольких откликов и не подменяй результат числом действий.')
        data, outcome = await self.assistant.agent_ask(
            task, {"review": {"type": "string"}, "journal": {"type": "string"}},
            focus="итоги недели цели", effort="medium", timeout=900, source="weekly_review")
        path = config.OUTPUT_DIR / "Обзоры недели" / f"{year}-W{week:02d}.md"
        _write_text(path, f"---\ntype: review\nperiod: weekly\nweek: {year}-W{week:02d}\n---\n"
                    f"{str(data.get('review') or '').strip()}\n")
        self.store.set_cursor("reviewed_week", f"{year}-W{week:02d}")
        self.core.notify(f"🗓 Итоги недели: {outcome.reply}\nЗаметка: {path}")
        if data.get("journal"):
            journal(f"[обзор недели] {data['journal']}")
        log.info("Обзор недели: %s", path)
        return path

    def _review_due(self, now: datetime) -> bool:
        year, week, _ = now.date().isocalendar()
        return now.weekday() == REVIEW_WEEKDAY and now.hour >= REVIEW_FROM \
            and self.store.cursor("reviewed_week") != f"{year}-W{week:02d}"

    def _reflection_due(self, now: datetime) -> date | None:
        day = now.date() if now.hour >= REFLECT_FROM else (now - timedelta(days=1)).date() \
            if now.hour < 5 else None
        return day if day and self.store.cursor("reflected_on") != day.isoformat() else None

    # --- цикл ---

    async def run(self) -> None:
        await asyncio.sleep(300)
        last_heartbeat_try = datetime.min
        while True:
            now = datetime.now()
            try:
                for schedule in due_schedules(now):
                    add_task(schedule["title"], schedule["instructions"], source="schedule",
                             schedule_id=schedule["id"])
                if self._busy(now):
                    pass
                elif self._review_due(now):
                    await self.weekly_review()
                elif day := self._reflection_due(now):
                    await self.reflect(day)
                elif pending := [t for t in tasks("pending")]:
                    await self.run_task(pending[0])
                    await asyncio.sleep(REST_AFTER_TASK if not 1 <= now.hour < 8 else 120)
                elif now.hour in HEARTBEAT_HOURS and \
                        (now - last_heartbeat_try).total_seconds() >= HEARTBEAT_EVERY:
                    last_heartbeat_try = now
                    await self.heartbeat()
            except BrainError as e:
                log.warning("Самостоятельная работа отложена: %s", e)
                if not await self.assistant.brain.wait_out(e):
                    await asyncio.sleep(600)
            await asyncio.sleep(60)

    def status(self) -> dict:
        return {**self.state, "tasks": tasks("pending") + tasks("running") + tasks('interrupted'),
                "schedules": [describe_schedule(s) for s in schedules()],
                "messages_today": len(self._messages_today())}


def _read_feedback() -> list[dict]:
    from .personalization import read_feedback

    return read_feedback(config.DATA_DIR)


def recover() -> None:
    """Do not replay a whole task after a crash: some tools may have taken effect."""
    for item in tasks("running"):
        _update_task(item["id"], status="interrupted", notes=item.get('notes', '') +
                     '\nПрервано перезапуском. Проверь результат и дай новое поручение для оставшейся работы.')
