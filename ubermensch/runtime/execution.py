"""Durable tool calls and approvals. Unknown effects are never replayed."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from ..capabilities.registry import ToolArgumentsError
from .context import Execution, current
from .http_tools import ToolExecutionUncertain
from .policy import check_tool
from .storage import fingerprint, stamp
from .tools import ToolJournal

# These implementations only read/validate before their single approval gate.
# Adding a new resumable tool requires the same invariant and a recovery test.
RESUMABLE = {'telegram_send', 'email_send', 'run_shell', 'write_file', 'append_file',
             'write_word', 'write_excel', 'move_path', 'delete_to_recycle_bin',
             'calendar_delete', 'read_file', 'read_document', 'read_excel', 'find_files', 'suspend_computer'}
APPROVAL_VERSION = 1


def identifier(value: str | None) -> str:
    return UUID(value).hex if value else uuid4().hex


class DurableTools:
    def __init__(self, core):
        self.core, self.store = core, core.store
        self.tasks: dict[str, asyncio.Task] = {}
        self.stopping = False

    async def call(self, name: str, arguments: dict, run_id=None, call_id=None):
        # Validation happens before any durable dispatch marker.
        values = self.core.tool_registry.validate(name, arguments)
        call_id = identifier(call_id)
        old = self.store.get_call(call_id)
        if old:
            if (old['fingerprint'] != fingerprint([name, values]) or
                    (run_id is not None and old['run_id'] != identifier(run_id))):
                raise ToolArgumentsError('call_id уже связан с другим запросом')
            if old['status'] == 'completed':
                return old['result']
            if call_id in self.tasks:
                return await asyncio.shield(self.tasks[call_id])
            raise ToolExecutionUncertain('Вызов уже зарегистрирован: ' + old['status'] +
                                         '. Проверь /api/tool-calls/' + call_id)
        run_id = identifier(run_id)
        run = self.store.get_run(run_id)
        if run is None:
            self.store.create_run('tools', {}, run_id=run_id)
            run = self.store.get_run(run_id)
        elif run['status'] in {'cancelled', 'cancelling', 'interrupted', 'failed', 'completed'}:
            raise ToolArgumentsError('Задание уже завершено или отменено')
        context = current.set(Execution(self.core, run_id, run['mode'], call_id))
        try:
            check_tool(name)
        finally:
            current.reset(context)
        self.store.create_call(call_id, run_id, name, values)
        task = self._launch(call_id)
        return await asyncio.shield(task)

    def _launch(self, call_id):
        task = asyncio.create_task(self._execute(call_id), name='tool:' + call_id)
        self.tasks[call_id] = task

        def finished(done):
            self.tasks.pop(call_id, None)
            if not done.cancelled():
                done.exception()  # result also remains in SQLite if the HTTP caller left
        task.add_done_callback(finished)
        return task

    async def _execute(self, call_id):
        call = self.store.get_call(call_id)
        run = self.store.get_run(call['run_id'])
        token = current.set(Execution(self.core, call['run_id'], run['mode'], call_id))
        from .. import config
        journal = ToolJournal(config.DATA_DIR / 'workspace' / 'runs' / call['run_id'] / 'tools.jsonl')
        try:
            check_tool(call['name'])
            result = await self.core.tool_registry.call(call['name'], call['arguments'], journal=journal)
            self.store.update_call(call_id, 'completed', result=result)
            return result
        except BaseException as error:
            if not (self.stopping and self.store.get_call(call_id)['status'] == 'waiting_approval'):
                self.store.update_call(call_id, 'uncertain', error=type(error).__name__ +
                                       ': выполнение прервано; проверь фактический результат.')
            raise
        finally:
            current.reset(token)
            self.core.bus.publish({'type': 'changed'})

    async def approval(self, title: str, details: str) -> bool:
        context = current.get()
        call_id = context.call_id
        call = self.store.get_call(call_id)
        digest = fingerprint([APPROVAL_VERSION, call['fingerprint'], title, details])
        row = self.store.db.execute('SELECT * FROM actions WHERE call_id=?', (call_id,)).fetchone()
        if row is None:
            with self.store.transaction():
                action_id = self.store.add_action('approval', title, {'details': details}, 'external')
                expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds')
                self.store.db.execute('UPDATE actions SET call_id=?,expires_at=?,approval_hash=? WHERE id=?',
                                      (call_id, expires, digest, action_id))
                self.store.update_call(call_id, 'waiting_approval')
                self.store.enqueue_delivery(f'approval:{action_id}',
                    {'type': 'approval', 'id': action_id, 'title': title, 'details': details})
            row = self.store.get_action(action_id)
        if row['approval_hash'] != digest:
            raise ToolArgumentsError('Содержание операции изменилось; нужно новое подтверждение')
        if row['status'] != 'pending':
            if row['status'] == 'approved' and row['expires_at'] > stamp():
                self.store.update_call(call_id, 'running')
                return True
            return False
        remaining = (datetime.fromisoformat(row['expires_at']) - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            self.decide(row['id'], False)
            return False
        waiting = asyncio.get_running_loop().create_future()
        self.core._approvals[row['id']] = waiting
        parent = self.store.get_run(context.run_id)
        if parent['status'] == 'running':
            self.store.update_run(context.run_id, 'waiting_approval')
        self.core.bus.publish({'type': 'changed'})
        try:
            approved = await asyncio.wait_for(waiting, remaining)
            self.store.update_call(call_id, 'running')
            return approved
        except TimeoutError:
            self.decide(row['id'], False)
            return False
        finally:
            self.core._approvals.pop(row['id'], None)
            if self.store.get_run(context.run_id)['status'] == 'waiting_approval':
                self.store.update_run(context.run_id, 'running')
            self.core.bus.publish({'type': 'changed'})

    def decide(self, action_id: int, approve: bool) -> str | None:
        row = self.store.get_action(action_id)
        if not row or not row['call_id']:
            return None
        call = self.store.get_call(row['call_id'])
        if row['status'] != 'pending':
            return 'Уже решено'
        if row['expires_at'] <= stamp() or call['status'] != 'waiting_approval':
            approve = False
        self.store.decide_action(action_id, 'approved' if approve else 'rejected')
        waiting = self.core._approvals.get(action_id)
        if waiting is not None and not waiting.done():
            waiting.set_result(approve)
        elif call['status'] == 'waiting_approval':
            if approve and call['name'] in RESUMABLE:
                # Restart only a known operation that has not crossed its approval gate.
                self._launch(call['id'])
            else:
                self.store.update_call(call['id'], 'completed', result='Действие отменено.')
        self.core.bus.publish({'type': 'changed'})
        return '✅ Подтверждено' if approve else '❌ Отменено или срок подтверждения истёк'

    def recover(self) -> None:
        self.store.recover_execution()
        for row in self.store.db.execute("SELECT a.id,a.status,a.expires_at,t.name,t.id AS tool_id "
                "FROM actions a JOIN tool_calls t ON t.id=a.call_id WHERE t.status='waiting_approval'"):
            if row['expires_at'] <= stamp() or row['name'] not in RESUMABLE:
                self.store.decide_action(row['id'], 'rejected')
                self.store.update_call(row['tool_id'], 'completed', result='Подтверждение устарело.')
            elif row['status'] == 'approved':
                self._launch(row['tool_id'])
            elif row['status'] == 'rejected':
                self.store.update_call(row['tool_id'], 'completed', result='Действие отменено.')

    def expire_approvals(self):
        rows = self.store.db.execute("SELECT id FROM actions WHERE status='pending' "
                                    'AND call_id IS NOT NULL AND expires_at<=?', (stamp(),)).fetchall()
        for row in rows:
            self.decide(row['id'], False)
        self.store.db.execute("UPDATE deliveries SET status='cancelled' WHERE status='pending' "
            "AND json_extract(event,'$.type')='approval' AND EXISTS(SELECT 1 FROM actions a "
            "WHERE a.id=json_extract(deliveries.event,'$.id') AND a.status!='pending')")

    async def cancel_run(self, run_id: str) -> None:
        tasks = []
        for call_id, task in list(self.tasks.items()):
            if self.store.get_call(call_id)['run_id'] == run_id:
                task.cancel()
                tasks.append(task)
        for row in self.store.db.execute("SELECT a.id FROM actions a JOIN tool_calls t ON t.id=a.call_id "
                                         "WHERE t.run_id=? AND a.status='pending'", (run_id,)):
            self.store.decide_action(row['id'], 'rejected')
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
