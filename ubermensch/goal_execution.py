"""Approved, bounded goal steps. Artifact verification is not proof of a life outcome."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .planning import day, required
from .runtime.context import current
from .runtime.storage import encoded, fingerprint, stamp


GOAL_EXECUTION_MIGRATION = """
CREATE TABLE goal_steps (
    id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
    fingerprint TEXT NOT NULL UNIQUE, path TEXT NOT NULL, content TEXT NOT NULL,
    reason TEXT NOT NULL, review_on TEXT NOT NULL, next_step TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN
        ('proposed','approved','executing','verifying','verified','blocked','cancelled')),
    approval_call_id TEXT REFERENCES tool_calls(id), run_id TEXT REFERENCES runs(id),
    write_call_id TEXT NOT NULL UNIQUE, verify_call_id TEXT REFERENCES tool_calls(id),
    observation TEXT, error TEXT, recommendation_id INTEGER REFERENCES recommendations(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX goal_steps_queue ON goal_steps(status,id);
"""


class GoalExecutionStorage:
    def propose_goal_step(self, goal_id, path, content, reason, review_on, next_step):
        self._planning_goal(goal_id)
        if not self.db.execute('SELECT 1 FROM goal_plans WHERE goal_id=?', (goal_id,)).fetchone():
            raise ValueError('Сначала задайте измеримый план через goal_plan')
        path = str(Path(required(path, 'Путь', 4000)).expanduser().resolve())
        required(content, 'Материал', 12000)
        # read_file uses universal newlines and utf-8-sig; store exactly that canonical text.
        content = content.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
        reason = required(reason, 'Связь с целью и источники', 2000)
        next_step = required(next_step, 'Следующий шаг после проверки', 2000)
        review_on = day(review_on)
        digest = fingerprint([goal_id, path, content, reason, review_on, next_step])
        self.db.execute('INSERT OR IGNORE INTO goal_steps(goal_id,fingerprint,path,content,reason,'
            'review_on,next_step,write_call_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (goal_id, digest, path, content, reason, review_on, next_step, uuid4().hex, stamp(), stamp()))
        identifier = self.db.execute('SELECT id FROM goal_steps WHERE fingerprint=?', (digest,)).fetchone()[0]
        return self.goal_step(identifier)

    def goal_step(self, identifier):
        row = self.db.execute('SELECT * FROM goal_steps WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Шаг цели не найден')
        result = dict(row)
        result['observation'] = json.loads(result['observation']) if result['observation'] else None
        return result

    def goal_steps(self, limit=20):
        rows = self.db.execute("SELECT id FROM goal_steps ORDER BY status IN "
            "('approved','executing','verifying','blocked','proposed') DESC,id DESC LIMIT ?", (limit,))
        return [{k: v for k, v in self.goal_step(row[0]).items() if k not in {'content', 'fingerprint'}}
                for row in rows.fetchall()]

    def finish_goal_step(self, identifier, observation):
        """Observation, next proposal and state move together; never increase the owner's metric."""
        with self.transaction():
            step = self.goal_step(identifier)
            if step['status'] != 'verifying':
                return
            evidence = encoded({'goal_step': identifier, 'write_call_id': step['write_call_id'],
                                'verify_call_id': step['verify_call_id'], **observation})
            recommendation = self.save_recommendation(step['goal_id'], step['next_step'], step['reason'],
                evidence, 'Пользователь оценил материал и сообщил фактический результат по показателю цели',
                0, step['review_on'])
            self.db.execute("UPDATE goal_steps SET status='verified',observation=?,error=NULL,"
                'recommendation_id=?,updated_at=? WHERE id=?',
                (encoded(observation), recommendation, stamp(), identifier))
            self.enqueue_delivery(f'goal-step:{identifier}:verified', {'type': 'message',
                'text': f"Проверен материал по цели #{step['goal_id']}: {step['path']}. "
                        f"Следующий шаг (предложение): {step['next_step']}. "
                        'Результат самой цели ещё требует замера.'})


class GoalExecutor:
    """One small step per autonomy tick; no LLM, new worker or retry of a write."""
    def __init__(self, core):
        self.core, self.store = core, core.store
        self._lock = asyncio.Lock()

    def pending(self):
        return self.store.db.execute("SELECT id FROM goal_steps WHERE status IN "
            "('approved','executing','verifying') ORDER BY id LIMIT 1").fetchone()

    def _block(self, identifier, reason):
        self.store.db.execute("UPDATE goal_steps SET status='blocked',error=?,updated_at=? WHERE id=?",
                              (reason, stamp(), identifier))

    async def tick(self):
        async with self._lock:
            row = self.pending()
            if row is None:
                return None
            identifier = row[0]
            return await self.core.jobs.inline('goal_step', {'goal_step_id': identifier},
                                               lambda: self._execute(identifier), mode='background')

    async def _execute(self, identifier):
        from .runtime import policy

        step = self.store.goal_step(identifier)
        run_id = current.get().run_id
        try:
            self.store._planning_goal(step['goal_id'])
            if step['status'] == 'approved':
                path = Path(step['path'])
                if not policy.writable(path) or not policy.readable(path):
                    raise PermissionError('Путь недоступен для фоновой записи и проверки; '
                                          'предложите новый шаг в разрешённой папке')
                if path.exists():
                    raise FileExistsError('Путь уже существует; исходный файл сохранён. Выберите новый путь')
                # Persist before dispatch. A crash after this marker permits only verification.
                self.store.db.execute("UPDATE goal_steps SET status='executing',run_id=?,updated_at=? "
                    "WHERE id=? AND status='approved'", (run_id, stamp(), identifier))
                await self.core.call_tool('create_text_artifact',
                    {'path': step['path'], 'content': step['content']}, run_id, step['write_call_id'])
            else:
                old_run = self.store.get_run(step['run_id']) if step['run_id'] else None
                if old_run and old_run['status'] in {'cancelled', 'cancelling'}:
                    raise ValueError('Исполнение отменено владельцем; автоматического продолжения нет')
                call = self.store.get_call(step['write_call_id'])
                if call is None or call['status'] != 'completed':
                    raise ValueError('Результат записи неизвестен. Проверьте файл и журнал вызова; '
                                     'автоматический повтор запрещён')
            # A fresh, read-only verification is safe after restart. Never replay the write.
            self.store._planning_goal(step['goal_id'])
            verify_id = uuid4().hex
            self.store.db.execute("UPDATE goal_steps SET status='verifying',updated_at=? WHERE id=?",
                                  (stamp(), identifier))
            observed = await self.core.call_tool('read_file',
                {'path': step['path'], 'limit': 30000}, run_id, verify_id)
            self.store.db.execute('UPDATE goal_steps SET verify_call_id=? WHERE id=?', (verify_id, identifier))
            if not isinstance(observed, dict) or observed.get('text') != step['content'] \
                    or observed.get('next_offset') is not None:
                raise ValueError('Содержимое не совпало с утверждённым материалом; исправление не запускалось')
            payload = observed['text'].encode('utf-8')
            self.store.finish_goal_step(identifier, {'verified_at': stamp(), 'bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(), 'kind': 'artifact_content',
                'owner_outcome': 'unmeasured'})
        except asyncio.CancelledError:
            self._block(identifier, 'Исполнение остановлено; проверьте журнал и файл перед новым шагом')
            raise
        except Exception as error:
            # Tool calls keep their precise durable status. Do not conceal an uncertain effect.
            self._block(identifier, f'{type(error).__name__}: {error}'[:1000])
        return {k: v for k, v in self.store.goal_step(identifier).items()
                if k not in {'content', 'fingerprint'}}
