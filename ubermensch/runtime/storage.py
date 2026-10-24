"""Durable execution records, delivery outbox and user quality feedback."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


RUNTIME_MIGRATION = """
CREATE TABLE runs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('interactive','background')),
    status TEXT NOT NULL, result TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX runs_status ON runs(status, created_at);
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL, arguments TEXT NOT NULL, fingerprint TEXT NOT NULL,
    status TEXT NOT NULL, result TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX calls_run ON tool_calls(run_id, created_at);
ALTER TABLE actions ADD COLUMN call_id TEXT REFERENCES tool_calls(id);
ALTER TABLE actions ADD COLUMN expires_at TEXT;
ALTER TABLE actions ADD COLUMN approval_hash TEXT;
CREATE UNIQUE INDEX approval_call ON actions(call_id) WHERE call_id IS NOT NULL;
CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL, channel TEXT NOT NULL,
    event TEXT NOT NULL, reminder_id INTEGER REFERENCES reminders(id),
    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL,
    delivered_at TEXT, UNIQUE(dedup_key, channel)
);
CREATE INDEX deliveries_pending ON deliveries(channel, status, next_attempt);
CREATE TABLE quality_feedback (
    id INTEGER PRIMARY KEY, target TEXT NOT NULL UNIQUE,
    message_id INTEGER REFERENCES messages(id), run_id TEXT REFERENCES runs(id),
    rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful','correction')),
    note TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class RuntimeStorage:
    def create_run(self, kind: str, payload: dict, mode: str = 'interactive',
                   run_id: str | None = None) -> str:
        identifier = run_id or uuid4().hex
        self.db.execute('INSERT INTO runs(id,kind,payload,mode,status,created_at,updated_at) '
                        "VALUES(?,?,?,?,'queued',?,?)",
                        (identifier, kind, encoded(payload), mode, stamp(), stamp()))
        return identifier

    def get_run(self, identifier: str) -> dict | None:
        row = self.db.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def update_run(self, identifier: str, status: str, *, result=None, error=None) -> None:
        self.db.execute('UPDATE runs SET status=?,result=?,error=?,updated_at=? WHERE id=?',
                        (status, encoded(result) if result is not None else None, error,
                         stamp(), identifier))

    def list_runs(self, limit: int = 30) -> list[dict]:
        return [self.public_run(r[0], include_result=False) for r in self.db.execute(
            "SELECT id FROM runs WHERE kind!='tools' ORDER BY "
            "status IN ('queued','running','waiting_approval','cancelling') DESC, created_at DESC,rowid DESC LIMIT ?",
            (limit,))]

    def public_run(self, identifier: str, *, include_result: bool = True) -> dict | None:
        columns = 'id,kind,mode,status,error,created_at,updated_at' + (',result' if include_result else '')
        row = self.db.execute(f'SELECT {columns} FROM runs WHERE id=?', (identifier,)).fetchone()
        run = dict(row) if row is not None else None
        if run is not None:
            if include_result:
                run['result'] = json.loads(run['result']) if run['result'] else None
            run['calls'] = [dict(r) for r in self.db.execute(
                'SELECT id,name,status,error,created_at,updated_at FROM tool_calls '
                'WHERE run_id=? ORDER BY rowid', (identifier,))]
        return run

    def get_call(self, identifier: str) -> dict | None:
        row = self.db.execute('SELECT * FROM tool_calls WHERE id=?', (identifier,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['arguments'] = json.loads(value['arguments'])
        value['result'] = json.loads(value['result']) if value['result'] is not None else None
        return value

    def create_call(self, identifier: str, run_id: str, name: str, arguments: dict) -> None:
        self.db.execute('INSERT INTO tool_calls '
                        '(id,run_id,name,arguments,fingerprint,status,created_at,updated_at) '
                        "VALUES(?,?,?,?,?,'running',?,?)",
                        (identifier, run_id, name, encoded(arguments),
                         fingerprint([name, arguments]), stamp(), stamp()))

    def update_call(self, identifier: str, status: str, *, result=None, error=None) -> None:
        self.db.execute('UPDATE tool_calls SET status=?,result=?,error=?,updated_at=? WHERE id=?',
                        (status, encoded(result) if status == 'completed' else None,
                         error, stamp(), identifier))

    def recover_execution(self) -> None:
        with self.transaction():
            self.db.execute("UPDATE tool_calls SET status='uncertain', error=?, updated_at=? "
                            "WHERE status='running'",
                            ('Процесс прерван; проверь результат перед новым действием.', stamp()))
            self.db.execute("UPDATE runs SET status='interrupted',error=?,updated_at=? "
                            "WHERE status IN ('running','waiting_approval','cancelling')",
                            ('Работа прервана перезапуском. Выполненные действия не повторяются.',
                             stamp()))
            # Legacy approvals have no recoverable, immutable operation attached.
            self.db.execute("UPDATE actions SET status='rejected',result=? WHERE kind='approval' "
                            "AND call_id IS NULL AND status='pending'",
                            ('Подтверждение устарело после перезапуска.',))

    def enqueue_delivery(self, key: str, event: dict, *, reminder_id: int | None = None,
                         channels=('telegram', 'desktop')) -> None:
        for channel in channels:
            self.db.execute('INSERT OR IGNORE INTO deliveries '
                            '(dedup_key,channel,event,reminder_id,next_attempt,created_at) '
                            'VALUES(?,?,?,?,?,?)',
                            (key, channel, encoded(event), reminder_id, stamp(), stamp()))

    def pending_deliveries(self, channel: str, limit: int = 50) -> list[dict]:
        return [{**dict(r), 'event': json.loads(r['event'])} for r in self.db.execute(
            "SELECT * FROM deliveries WHERE channel=? AND status='pending' AND next_attempt<=? "
            'ORDER BY id LIMIT ?', (channel, stamp(), limit))]

    def acknowledge_delivery(self, identifier: int, channel: str) -> bool:
        with self.transaction():
            changed = self.db.execute("UPDATE deliveries SET status='delivered',delivered_at=? "
                                      "WHERE id=? AND channel=? AND status='pending'",
                                      (stamp(), identifier, channel)).rowcount > 0
            row = self.db.execute('SELECT reminder_id FROM deliveries WHERE id=? AND channel=?',
                                  (identifier, channel)).fetchone()
            if row and row[0] is not None:
                self.db.execute("UPDATE reminders SET status='sent' WHERE id=? AND status='pending' "
                                "AND NOT EXISTS(SELECT 1 FROM deliveries WHERE reminder_id=? "
                                "AND status='pending')", (row[0], row[0]))
        return changed

    def fail_delivery(self, identifier: int, error: str) -> None:
        row = self.db.execute('SELECT attempts FROM deliveries WHERE id=?', (identifier,)).fetchone()
        delay = min(3600, 5 * 2 ** min(row[0], 10))
        next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec='seconds')
        self.db.execute('UPDATE deliveries SET attempts=attempts+1,last_error=?,next_attempt=? '
                        'WHERE id=?', (error[:500], next_attempt, identifier))

    def record_feedback(self, rating: str, note: str = '', *, message_id=None, run_id=None) -> None:
        target = f'message:{message_id}' if message_id is not None else f'run:{run_id}'
        self.db.execute('INSERT INTO quality_feedback '
                        '(target,message_id,run_id,rating,note,created_at) VALUES(?,?,?,?,?,?) '
                        'ON CONFLICT(target) DO UPDATE SET rating=excluded.rating,note=excluded.note,'
                        'created_at=excluded.created_at',
                        (target, message_id, run_id, rating, note, stamp()))

    def quality_metrics(self) -> dict:
        ratings = {r[0]: r[1] for r in self.db.execute(
            'SELECT rating,COUNT(*) FROM quality_feedback GROUP BY rating')}
        runs = {r[0]: r[1] for r in self.db.execute(
            "SELECT status,COUNT(*) FROM runs WHERE kind!='tools' GROUP BY status")}
        total = sum(ratings.values())
        return {'ratings': ratings, 'rated': total,
                'helpful_rate': ratings.get('helpful', 0) / total if total else None,
                'runs': runs,
                'deliveries_pending': self.db.execute(
                    "SELECT COUNT(*) FROM deliveries WHERE status='pending'").fetchone()[0],
                'tool_calls_uncertain': self.db.execute(
                    "SELECT COUNT(*) FROM tool_calls WHERE status='uncertain'").fetchone()[0]}
