"""Долговременная память в SQLite: диалог, факты, цели, напоминания, журнал действий."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

AREAS = {
    "profile": "Обо мне",
    "career": "Карьера и деньги",
    "health": "Здоровье и тело",
    "learning": "Обучение и навыки",
    "discipline": "Дисциплина и продуктивность",
    "personal": "Личная жизнь",
    "growth": "Самосовершенствование",
}

# Каждая миграция применяется один раз; номер последней хранится в PRAGMA user_version.
MIGRATIONS = [
    """
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL
    );
    CREATE TABLE facts (
        id INTEGER PRIMARY KEY,
        area TEXT NOT NULL,
        content TEXT NOT NULL,
        importance INTEGER NOT NULL DEFAULT 3,
        source TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE goals (
        id INTEGER PRIMARY KEY,
        area TEXT NOT NULL,
        title TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        deadline TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'dropped')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY,
        due_at TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'cancelled')),
        created_at TEXT NOT NULL
    );
    CREATE INDEX reminders_due ON reminders (status, due_at);
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        params TEXT NOT NULL,
        risk TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('pending', 'approved', 'rejected', 'done', 'failed')),
        result TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT
    );
    """,
    """
    ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL DEFAULT 'tg';
    ALTER TABLE messages ADD COLUMN notes TEXT;
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        goal_id INTEGER REFERENCES goals (id),
        due TEXT,
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dropped')),
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        done_at TEXT
    );
    CREATE INDEX tasks_status ON tasks (status, due);
    """,
    """
    CREATE TABLE imports (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('done', 'skipped', 'failed')),
        summary TEXT,
        imported_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        thread TEXT,
        author TEXT,
        outgoing INTEGER NOT NULL DEFAULT 0,
        text TEXT NOT NULL,
        meta TEXT,
        UNIQUE (source, external_id)
    );
    CREATE INDEX events_source_ts ON events (source, ts);
    CREATE TABLE cursors (
        source TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE analysis_jobs (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'done', 'failed', 'skipped')),
        chars INTEGER,
        result TEXT,
        created_at TEXT NOT NULL,
        done_at TEXT
    );
    CREATE TABLE people (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        importance INTEGER NOT NULL DEFAULT 3,
        updated_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE people ADD COLUMN merged_into INTEGER REFERENCES people (id);
    """,
    """
    -- категории программ и сайтов для метрики «полезное / отвлекающее»
    CREATE TABLE activity_categories (
        key TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self.db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")

    def _insert(self, sql: str, *args) -> int:
        return self.db.execute(sql, args).lastrowid

    def _changed(self, sql: str, *args) -> bool:
        return self.db.execute(sql, args).rowcount > 0

    # --- диалог ---

    def add_message(self, role: str, text: str, channel: str) -> int:
        return self._insert(
            "INSERT INTO messages (ts, role, text, channel) VALUES (?, ?, ?, ?)",
            now(), role, text, channel,
        )

    def set_message_notes(self, message_id: int, notes: list[str]) -> None:
        """Служебные строки под ответом: что изменилось в памяти, задачах, действиях."""
        self.db.execute("UPDATE messages SET notes = ? WHERE id = ?",
                        (json.dumps(notes, ensure_ascii=False), message_id))

    def recent_messages(self, limit: int) -> list[sqlite3.Row]:
        rows = self.db.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return rows[::-1]

    # --- факты ---

    def facts(self, limit: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM facts WHERE active = 1 "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def add_fact(self, area: str, content: str, importance: int, source: str) -> int:
        ts = now()
        return self._insert(
            "INSERT INTO facts (area, content, importance, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            area, content, importance, source, ts, ts,
        )

    def update_fact(self, fact_id: int, area: str | None, content: str | None,
                    importance: int | None) -> bool:
        return self._changed(
            "UPDATE facts SET area = COALESCE(?, area), content = COALESCE(?, content), "
            "importance = COALESCE(?, importance), updated_at = ? WHERE id = ? AND active = 1",
            area, content, importance, now(), fact_id,
        )

    def forget_fact(self, fact_id: int) -> bool:
        return self._changed(
            "UPDATE facts SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
            now(), fact_id,
        )

    # --- цели ---

    def goals(self, status: str = "active") -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM goals WHERE status = ? ORDER BY area, id", (status,)
        ).fetchall()

    def add_goal(self, area: str, title: str, details: str, deadline: str | None) -> int:
        ts = now()
        return self._insert(
            "INSERT INTO goals (area, title, details, deadline, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            area, title, details, deadline, ts, ts,
        )

    def update_goal(self, goal_id: int, area: str | None, title: str | None,
                    details: str | None, deadline: str | None) -> bool:
        return self._changed(
            "UPDATE goals SET area = COALESCE(?, area), title = COALESCE(?, title), "
            "details = COALESCE(?, details), deadline = COALESCE(?, deadline), updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            area, title, details, deadline, now(), goal_id,
        )

    def set_goal_status(self, goal_id: int, status: str) -> bool:
        return self._changed(
            "UPDATE goals SET status = ?, updated_at = ? WHERE id = ? AND status = 'active'",
            status, now(), goal_id,
        )

    # --- задачи ---

    def tasks(self) -> list[sqlite3.Row]:
        """Открытые задачи и выполненные сегодня — то, что видно в листе."""
        return self.db.execute(
            "SELECT * FROM tasks WHERE status = 'open' OR (status = 'done' AND done_at >= ?) "
            "ORDER BY status DESC, due IS NULL, due, id",
            (now()[:10],),
        ).fetchall()

    def add_task(self, title: str, notes: str, due: str | None, goal_id: int | None,
                 source: str) -> int:
        return self._insert(
            "INSERT INTO tasks (title, notes, due, goal_id, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            title, notes, due, goal_id, source, now(),
        )

    def update_task(self, task_id: int, title: str | None, notes: str | None,
                    due: str | None) -> bool:
        return self._changed(
            "UPDATE tasks SET title = COALESCE(?, title), notes = COALESCE(?, notes), "
            "due = COALESCE(?, due) WHERE id = ? AND status = 'open'",
            title, notes, due, task_id,
        )

    def set_task_status(self, task_id: int, status: str) -> bool:
        return self._changed(
            "UPDATE tasks SET status = ?, done_at = ? WHERE id = ? AND status != ?",
            status, now() if status == "done" else None, task_id, status,
        )

    # --- напоминания ---

    def add_reminder(self, due_at: datetime, text: str) -> int:
        return self._insert(
            "INSERT INTO reminders (due_at, text, created_at) VALUES (?, ?, ?)",
            due_at.isoformat(timespec="seconds"), text, now(),
        )

    def pending_reminders(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at"
        ).fetchall()

    def due_reminders(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at",
            (now(),),
        ).fetchall()

    def close_reminder(self, reminder_id: int, status: str) -> bool:
        return self._changed(
            "UPDATE reminders SET status = ? WHERE id = ? AND status = 'pending'",
            status, reminder_id,
        )

    # --- действия ---

    def add_action(self, kind: str, summary: str, params: dict, risk: str) -> int:
        return self._insert(
            "INSERT INTO actions (kind, summary, params, risk, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            kind, summary, json.dumps(params, ensure_ascii=False), risk, now(),
        )

    def get_action(self, action_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()

    def pending_actions(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM actions WHERE status = 'pending' ORDER BY id"
        ).fetchall()

    def decide_action(self, action_id: int, status: str) -> bool:
        """Переводит действие из pending в approved/rejected. False — если решение уже принято."""
        return self._changed(
            "UPDATE actions SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            status, now(), action_id,
        )

    def finish_action(self, action_id: int, status: str, result: str) -> None:
        self.db.execute(
            "UPDATE actions SET status = ?, result = ? WHERE id = ?", (status, result, action_id)
        )

    # --- сырые данные из источников ---

    def sync_events(self, events: list[dict]) -> int:
        """Как add_events, но запись с тем же source + external_id обновляется на месте, если
        изменилась (заметки, которые правят): id сохраняется — ссылки на неё остаются верными.
        Возвращает число новых и изменённых."""
        before = self.db.total_changes
        self.db.execute("BEGIN")
        try:
            self.db.executemany(
                "INSERT INTO events (source, external_id, ts, thread, author, outgoing, text, meta) "
                "VALUES (:source, :external_id, :ts, :thread, :author, :outgoing, :text, :meta) "
                "ON CONFLICT (source, external_id) DO UPDATE SET ts=excluded.ts, "
                "thread=excluded.thread, author=excluded.author, outgoing=excluded.outgoing, "
                "text=excluded.text, meta=excluded.meta "
                "WHERE events.text != excluded.text OR events.meta IS NOT excluded.meta",
                [{"thread": None, "author": None, "outgoing": 0, **e,
                  "meta": json.dumps(e.get("meta") or {}, ensure_ascii=False)} for e in events])
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return self.db.total_changes - before

    def add_events(self, events: list[dict]) -> int:
        """Добавляет события; уже собранные (тот же source + external_id) пропускает.

        Возвращает число новых. Одна транзакция — иначе десятки тысяч строк пишутся минутами.
        """
        before = self.db.total_changes
        self.db.execute("BEGIN")
        try:
            self.db.executemany(
                "INSERT OR IGNORE INTO events "
                "(source, external_id, ts, thread, author, outgoing, text, meta) "
                "VALUES (:source, :external_id, :ts, :thread, :author, :outgoing, :text, :meta)",
                [{"thread": None, "author": None, "outgoing": 0, **e,
                  "meta": json.dumps(e.get("meta") or {}, ensure_ascii=False)} for e in events],
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.db.total_changes - before

    def events(self, source: str, start: str, end: str,
               thread: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM events WHERE source = ? AND ts >= ? AND ts < ?"
        args: list = [source, start, end]
        if thread is not None:
            sql += " AND thread = ?"
            args.append(thread)
        return self.db.execute(sql + " ORDER BY ts, id", args).fetchall()

    def event_threads(self, source: str, start: str, end: str) -> list[sqlite3.Row]:
        """Собеседники/потоки за период: сколько сообщений и символов."""
        return self.db.execute(
            "SELECT thread, COUNT(*) AS n, SUM(LENGTH(text)) AS chars FROM events "
            "WHERE source = ? AND ts >= ? AND ts < ? GROUP BY thread ORDER BY chars DESC",
            (source, start, end),
        ).fetchall()

    def event_span(self, source: str) -> tuple[str | None, str | None]:
        row = self.db.execute("SELECT MIN(ts), MAX(ts) FROM events WHERE source = ?",
                              (source,)).fetchone()
        return row[0], row[1]

    def my_telegram_messages(self, limit: int, account: str = "personal") -> list[str]:
        """Свежие сообщения, которые пользователь сам писал людям (не в «Избранное»)
        с личного (у событий нет поля account) или другого аккаунта."""
        return [r[0] for r in self.db.execute(
            "SELECT text FROM events WHERE source = 'telegram' AND outgoing = 1 "
            "AND json_extract(meta, '$.kind') != 'self' "
            "AND COALESCE(json_extract(meta, '$.account'), 'personal') = ? "
            "ORDER BY ts DESC LIMIT ?", (account, limit))]

    def my_ai_messages(self, limit: int) -> list[str]:
        """Свежие реплики пользователя в разговорах с ИИ (длинные — как он рассуждает)."""
        return [r[0] for r in self.db.execute(
            "SELECT text FROM events WHERE source = 'ai_chat' AND LENGTH(text) > 80 "
            "ORDER BY ts DESC LIMIT ?", (limit,))]

    def event_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT source, COUNT(*) FROM events GROUP BY source")}

    def cursor(self, source: str) -> str | None:
        row = self.db.execute("SELECT value FROM cursors WHERE source = ?", (source,)).fetchone()
        return row["value"] if row else None

    def delete_cursor(self, source: str) -> None:
        self.db.execute("DELETE FROM cursors WHERE source = ?", (source,))

    def retry_failed_jobs(self, older_than: str) -> int:
        """Порции, упавшие из-за сети или сна (не лимита — лимит разбор пережидает), —
        снова в очередь, если упали раньше older_than."""
        return self.db.execute(
            "UPDATE analysis_jobs SET status = 'pending' WHERE status = 'failed' AND done_at < ?",
            (older_than,)).rowcount

    def set_cursor(self, source: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO cursors (source, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (source) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (source, value, now()),
        )

    # --- очередь разбора ---

    def add_job(self, key: str, source: str, title: str, period_start: str, period_end: str,
                priority: int) -> bool:
        return self._changed(
            "INSERT OR IGNORE INTO analysis_jobs "
            "(key, source, title, period_start, period_end, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            key, source, title, period_start, period_end, priority, now(),
        )

    def next_job(self, sources: set[str] | None = None) -> sqlite3.Row | None:
        """Сначала важное (priority), внутри — от старого к новому: свежие сведения
        тогда уточняют устаревшие, а не наоборот. sources — только из этих источников."""
        only = f"AND source IN ({','.join('?' * len(sources))}) " if sources else ""
        return self.db.execute(
            f"SELECT * FROM analysis_jobs WHERE status = 'pending' {only}"
            "ORDER BY priority DESC, period_start, id LIMIT 1", tuple(sources or ())
        ).fetchone()

    def jobs_done_since(self, moment: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM analysis_jobs WHERE status = 'done' AND done_at > ?",
            (moment,)).fetchone()[0]

    def telegram_contacts(self, start: str, min_messages: int) -> list[sqlite3.Row]:
        """Личные чаты с большой перепиской — для портретов отношений."""
        return self.db.execute(
            "SELECT thread, COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM events "
            "WHERE source = 'telegram' AND json_extract(meta, '$.kind') = 'user' AND ts >= ? "
            "GROUP BY thread HAVING n >= ? ORDER BY n DESC", (start, min_messages)).fetchall()

    def finish_job(self, job_id: int, status: str, result: str, chars: int) -> None:
        self.db.execute(
            "UPDATE analysis_jobs SET status = ?, result = ?, chars = ?, done_at = ? WHERE id = ?",
            (status, result, chars, now(), job_id),
        )

    def job_counts(self, min_priority: int = 0) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT status, COUNT(*) FROM analysis_jobs WHERE priority >= ? GROUP BY status",
            (min_priority,))}

    def drop_legacy_jobs(self) -> int:
        """Убирает порции старого размера (ключ без «@»), если ни одну часть их периода ещё
        не разбирали: план поставит их заново, крупнее. Начатые периоды доразбираются как есть."""
        groups: dict[str, set[str]] = {}
        for key, status in self.db.execute(
                "SELECT key, status FROM analysis_jobs WHERE instr(key, '@') = 0"):
            groups.setdefault(key.rsplit("|", 1)[0], set()).add(status)
        dropped = 0
        for group, statuses in groups.items():
            if statuses == {"pending"}:
                dropped += self.db.execute(
                    "DELETE FROM analysis_jobs WHERE status = 'pending' AND instr(key, '@') = 0 "
                    "AND substr(key, 1, ?) = ?", (len(group) + 1, group + "|")).rowcount
        return dropped

    def has_job_prefix(self, prefix: str) -> bool:
        return self.db.execute("SELECT 1 FROM analysis_jobs WHERE substr(key, 1, ?) = ? LIMIT 1",
                               (len(prefix), prefix)).fetchone() is not None

    def last_job_done_at(self) -> str | None:
        return self.db.execute(
            "SELECT MAX(done_at) FROM analysis_jobs WHERE status = 'done'").fetchone()[0]

    def job_results(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT title, result FROM analysis_jobs WHERE status = 'done' AND result != '' "
            "ORDER BY period_end DESC"
        ).fetchall()

    # --- люди ---

    # --- метрики ---

    def activity_sessions(self, start: str, end: str) -> list[tuple[str, str, str, int]]:
        """Сессии за компьютером: (начало, программа, заголовок окна, секунд)."""
        return [(r[0], r[1] or "", r[2], int(json.loads(r[3] or "{}").get("seconds", 0)))
                for r in self.db.execute(
                    "SELECT ts, thread, text, meta FROM events WHERE source = 'activity' "
                    "AND ts >= ? AND ts < ? ORDER BY ts", (start, end))]

    def activity_categories(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT key, category FROM activity_categories")}

    def set_activity_categories(self, categories: dict[str, str]) -> None:
        ts = now()
        self.db.executemany(
            "INSERT INTO activity_categories (key, category, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET category = excluded.category, "
            "updated_at = excluded.updated_at",
            [(key, category, ts) for key, category in categories.items()])

    def fact_areas(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT area, COUNT(*) FROM facts WHERE active = 1 GROUP BY area")}

    def facts_by_day(self, since: str) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT substr(created_at, 1, 10), COUNT(*) FROM facts WHERE created_at >= ? "
            "GROUP BY 1", (since,))}

    def jobs_by_source(self) -> list[tuple[str, int, int]]:
        """(источник, разобрано, всего) — пропущенные пустые порции считаются разобранными."""
        return [(r[0], r[1], r[2]) for r in self.db.execute(
            "SELECT source, SUM(status IN ('done', 'skipped')), COUNT(*) FROM analysis_jobs "
            "GROUP BY source ORDER BY COUNT(*) DESC")]

    def analyzed_chars(self) -> int:
        return self.db.execute(
            "SELECT COALESCE(SUM(chars), 0) FROM analysis_jobs WHERE status = 'done'").fetchone()[0]

    def task_stats(self, today: str, week_start: str) -> dict:
        one = lambda sql, *args: self.db.execute(sql, args).fetchone()[0]  # noqa: E731
        done_days = {r[0] for r in self.db.execute(
            "SELECT DISTINCT substr(done_at, 1, 10) FROM tasks WHERE status = 'done'")}
        return {
            "open": one("SELECT COUNT(*) FROM tasks WHERE status = 'open'"),
            "overdue": one("SELECT COUNT(*) FROM tasks WHERE status = 'open' AND due < ?", today),
            "done_today": one("SELECT COUNT(*) FROM tasks WHERE status = 'done' AND done_at >= ?",
                              today),
            "done_week": one("SELECT COUNT(*) FROM tasks WHERE status = 'done' AND done_at >= ?",
                             week_start),
            "done_days": sorted(done_days),
        }

    def goal_progress(self) -> list[tuple[str, int, int]]:
        """Активные цели: (название, задач выполнено, задач всего)."""
        return [(r[0], r[1] or 0, r[2]) for r in self.db.execute(
            "SELECT g.title, SUM(t.status = 'done'), COUNT(t.id) FROM goals g "
            "LEFT JOIN tasks t ON t.goal_id = g.id AND t.status != 'dropped' "
            "WHERE g.status = 'active' GROUP BY g.id ORDER BY g.area, g.id")]

    def people(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM people WHERE merged_into IS NULL "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def add_person(self, name: str, relation: str, notes: str, importance: int) -> int:
        return self._insert(
            "INSERT INTO people (name, relation, notes, importance, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            name, relation, notes, importance, now(),
        )

    def merge_person(self, person_id: int, into_id: int) -> bool:
        """Двойник склеивается с основной записью: не удаляется, а скрывается из выдачи."""
        if person_id == into_id:
            return False
        return self._changed(
            "UPDATE people SET merged_into = ?, updated_at = ? WHERE id = ? AND merged_into IS NULL "
            "AND EXISTS (SELECT 1 FROM people WHERE id = ? AND merged_into IS NULL)",
            into_id, now(), person_id, into_id,
        )

    def update_person(self, person_id: int, name: str | None, relation: str | None,
                      notes: str | None, importance: int | None) -> bool:
        return self._changed(
            "UPDATE people SET name = COALESCE(?, name), relation = COALESCE(?, relation), "
            "notes = COALESCE(?, notes), importance = COALESCE(?, importance), updated_at = ? "
            "WHERE id = ?",
            name, relation, notes, importance, now(), person_id,
        )

    # --- импорт документов ---

    def import_status(self, sha256: str) -> str | None:
        row = self.db.execute("SELECT status FROM imports WHERE sha256 = ?", (sha256,)).fetchone()
        return row["status"] if row else None

    def record_import(self, name: str, sha256: str, status: str, summary: str) -> None:
        self.db.execute(
            "INSERT INTO imports (name, sha256, status, summary, imported_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (sha256) DO UPDATE SET status = excluded.status, "
            "summary = excluded.summary, imported_at = excluded.imported_at",
            (name, sha256, status, summary, now()),
        )

    def counts(self) -> dict[str, int]:
        queries = {
            "messages": "SELECT COUNT(*) FROM messages",
            "facts": "SELECT COUNT(*) FROM facts WHERE active = 1",
            "goals": "SELECT COUNT(*) FROM goals WHERE status = 'active'",
            "tasks": "SELECT COUNT(*) FROM tasks WHERE status = 'open'",
            "reminders": "SELECT COUNT(*) FROM reminders WHERE status = 'pending'",
            "actions_pending": "SELECT COUNT(*) FROM actions WHERE status = 'pending'",
        }
        return {k: self.db.execute(q).fetchone()[0] for k, q in queries.items()}
