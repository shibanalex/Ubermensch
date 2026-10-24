"""Failure, replay and recovery tests using only synthetic data and local servers."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer

from ubermensch import config
from ubermensch.api import create_app
from ubermensch.assistant import Assistant, Outcome
from ubermensch.brain import BrainError
from ubermensch.capabilities.registry import ToolRegistry, ToolArgumentsError
from ubermensch.core import Core, Bus
from ubermensch.runtime.context import Execution, current
from ubermensch.runtime.execution import DurableTools
from ubermensch.runtime.jobs import JobCancelled
from ubermensch.runtime.http_tools import ToolExecutionUncertain
from ubermensch.runtime.storage import fingerprint, stamp
from ubermensch.runtime import policy
from ubermensch.store import Store, MIGRATIONS


def make_core(store):
    core = Core.__new__(Core)
    core.store, core.bus = store, Bus()
    core.tool_registry = ToolRegistry()
    core._approvals = {}
    core._brain_calls = 0
    core.health = SimpleNamespace(problems=lambda: [])
    return core


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.assistant = Assistant.__new__(Assistant)
        self.assistant.store = self.store

    def test_invalid_later_operation_does_not_apply_earlier_fact(self):
        with self.assertRaises(BrainError):
            self.assistant._apply_answer({'memory': [{'op': 'add', 'content': 'test'}],
                                         'tasks': [{'op': 'add', 'title': 123}]}, Outcome(''))
        self.assertEqual(self.store.facts(10), [])

    def test_sql_failure_rolls_back_all_operations_and_outcome(self):
        outcome = Outcome('')
        with self.assertRaises(sqlite3.IntegrityError):
            self.assistant._apply_answer({'memory': [{'op': 'add', 'content': 'test'}],
                'tasks': [{'op': 'add', 'title': 'test', 'goal_id': 999}]}, outcome)
        self.assertEqual(self.store.facts(10), [])
        self.assertEqual(outcome.changes, [])

    def test_invalid_reminder_does_not_save_memory(self):
        with self.assertRaises(BrainError):
            self.assistant._apply_answer({'memory': [{'op': 'add', 'content': 'test'}],
                'actions': [{'kind': 'reminder', 'summary': 'test',
                             'params': {'at': '2000-01-01', 'text': 'test'}}]}, Outcome(''))
        self.assertEqual(self.store.facts(10), [])
        self.assertEqual(self.store.pending_actions(), [])

    def test_required_operation_fields_and_types(self):
        for data in [{'tasks': [{'op': 'add'}]}, {'memory': [{'op': 'forget'}]},
                     {'people': [{'op': 'merge', 'id': 1, 'into': 1}]},
                     {'goals': [{'op': 'add', 'title': 'x', 'deadline': '2026-02-30'}]},
                     {'memory': [{'op': 'add', 'content': 'x', 'importance': True}]}]:
            with self.subTest(data=data), self.assertRaises(BrainError):
                self.assistant._apply_answer(data, Outcome(''))

    def test_fts_finds_old_low_importance_fact_and_tracks_edits(self):
        identifier = self.store.add_fact('learning', 'Курс гармонии по четвергам', 1, 'test')
        for n in range(170):
            self.store.add_fact('profile', f'Нерелевантный факт {n}', 5, 'test')
        self.assertEqual(self.store.relevant_facts('Когда курс гармонии?', 10)[0]['id'], identifier)
        self.store.update_fact(identifier, None, 'Сольфеджио по пятницам', None)
        self.assertEqual(self.store.relevant_facts('сольфеджио', 10)[0]['id'], identifier)
        self.store.forget_fact(identifier)
        self.assertNotIn(identifier, [r['id'] for r in self.store.relevant_facts('сольфеджио', 10)])

    def test_search_treats_fts_syntax_as_plain_text(self):
        self.store.relevant_facts('" OR * () NEAR { } -', 5)
        self.assertEqual(self.store.relevant_facts('anything', 0), [])

    def test_feedback_update_is_one_rating(self):
        message = self.store.add_message('assistant', 'test', 'desktop')
        self.store.record_feedback('helpful', message_id=message)
        self.store.record_feedback('correction', message_id=message)
        self.assertEqual(self.store.quality_metrics()['ratings'], {'correction': 1})

    def test_upgrade_v6_and_reopen_preserve_data_and_build_index(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.db'
            db = sqlite3.connect(path)
            for version, script in enumerate(MIGRATIONS[:6], 1):
                db.executescript(f'{script}; PRAGMA user_version={version};')
            db.execute("INSERT INTO facts(area,content,importance,source,created_at,updated_at) "
                       "VALUES('learning','гармония',1,'test','2020','2020')")
            db.commit()
            db.close()
            for _ in range(2):
                store = Store(path)
                self.assertEqual(store.relevant_facts('гармония', 1)[0]['content'], 'гармония')
                self.assertEqual(store.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], len(MIGRATIONS))
                store.db.close()


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'test.db')
        self.addCleanup(self.store.db.close)
        self.core = make_core(self.store)
        self.addAsyncCleanup(self.core.execution.close)
        self.addAsyncCleanup(self.core.jobs.close)
        setting = patch.object(config, 'DATA_DIR', self.root)
        setting.start()
        self.addCleanup(setting.stop)
        self.client = TestClient(TestServer(create_app(self.core, 't')))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer t'}

    async def post(self, path, value):
        return await self.client.post(path, json=value, headers=self.headers)

    async def test_string_false_and_missing_approval_are_rejected(self):
        self.core.decide = Mock()
        for value in [{'approve': 'false'}, {'approve': 0}, {}, {'approve': None}]:
            self.assertEqual((await self.post('/api/actions/1', value)).status, 400)
        self.core.decide.assert_not_called()
        self.core.decide.return_value = 'rejected'
        self.assertEqual((await self.post('/api/actions/1', {'approve': False})).status, 200)
        self.core.decide.assert_called_once_with(1, False)

    async def test_task_date_validation_and_explicit_clear(self):
        for value in [{'title': None}, {'title': 12}, {'title': 'x', 'due': '2026-02-30'}]:
            self.assertEqual((await self.post('/api/tasks', value)).status, 400)
        response = await self.post('/api/tasks', {'title': 'x', 'due': '2026-10-25'})
        identifier = (await response.json())['id']
        await self.post(f'/api/tasks/{identifier}/edit', {'due': None, 'notes': ''})
        self.assertIsNone(self.store.tasks()[0]['due'])

    async def test_tool_replay_and_concurrent_delivery_execute_once(self):
        effects = []
        @self.core.tool_registry.tool()
        async def save(text: str):
            await asyncio.sleep(.01)
            effects.append(text)
            return {'saved': text}
        call_id, run_id = uuid4().hex, uuid4().hex
        first, second = await asyncio.gather(*[
            self.core.call_tool('save', {'text': 'x'}, run_id, call_id) for _ in range(2)])
        self.assertEqual(first, second)
        self.assertEqual(effects, ['x'])
        self.assertEqual(await self.core.call_tool('save', {'text': 'x'}, run_id, call_id), first)
        with self.assertRaises(ToolArgumentsError):
            await self.core.call_tool('save', {'text': 'other'}, run_id, call_id)

    async def test_unknown_effect_is_not_replayed(self):
        effects = []
        @self.core.tool_registry.tool()
        async def save():
            effects.append('effect')
            raise RuntimeError('after effect')
        call_id = uuid4().hex
        with self.assertRaises(RuntimeError):
            await self.core.call_tool('save', {}, call_id=call_id)
        await asyncio.sleep(0)
        with self.assertRaises(ToolExecutionUncertain):
            await self.core.call_tool('save', {}, call_id=call_id)
        self.assertEqual(effects, ['effect'])
        self.assertEqual(self.store.get_call(call_id)['status'], 'uncertain')

    async def test_client_disconnect_does_not_cancel_or_repeat_tool(self):
        started, finish = asyncio.Event(), asyncio.Event()
        effects = []
        @self.core.tool_registry.tool()
        async def save():
            started.set()
            await finish.wait()
            effects.append('once')
            return 'saved'
        call_id = uuid4().hex
        request = asyncio.create_task(self.core.call_tool('save', {}, call_id=call_id))
        await started.wait()
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        finish.set()
        await asyncio.gather(*list(self.core.execution.tasks.values()))
        self.assertEqual(await self.core.call_tool('save', {}, call_id=call_id), 'saved')
        self.assertEqual(effects, ['once'])

    async def test_graceful_restart_preserves_live_approval(self):
        effects = []
        @self.core.tool_registry.tool()
        async def telegram_send(text: str):
            if await self.core.request_approval('Send', text):
                effects.append(text)
            return 'finished'
        call_id = uuid4().hex
        request = asyncio.create_task(self.core.call_tool('telegram_send', {'text': 'x'}, call_id=call_id))
        for _ in range(100):
            await asyncio.sleep(0)
            if self.core._approvals:
                break
        self.assertTrue(self.core._approvals)
        action = next(iter(self.core._approvals))
        await self.core.execution.close()
        await asyncio.gather(request, return_exceptions=True)
        self.assertEqual(self.store.get_call(call_id)['status'], 'waiting_approval')
        self.core._execution = DurableTools(self.core)
        self.addAsyncCleanup(self.core.execution.close)
        self.core.execution.recover()
        self.core.decide(action, True)
        await asyncio.gather(*list(self.core.execution.tasks.values()))
        self.assertEqual(effects, ['x'])

    async def test_recovery_marks_inflight_unknown_and_preserves_completed(self):
        run_id = self.store.create_run('chat', {'text': 'x'})
        self.store.update_run(run_id, 'running')
        call_id = uuid4().hex
        self.store.create_call(call_id, run_id, 'save', {})
        self.core.execution.recover()
        self.assertEqual(self.store.get_run(run_id)['status'], 'interrupted')
        self.assertEqual(self.store.get_call(call_id)['status'], 'uncertain')

    async def test_approval_survives_reopen_and_resumes_only_after_decision(self):
        effects = []
        @self.core.tool_registry.tool()
        async def telegram_send(text: str):
            if await self.core.request_approval('Send', text):
                effects.append(text)
                return 'sent'
            return 'denied'
        run_id = self.store.create_run('tools', {})
        call_id = uuid4().hex
        self.store.create_call(call_id, run_id, 'telegram_send', {'text': 'test'})
        self.store.update_call(call_id, 'waiting_approval')
        action_id = self.store.add_action('approval', 'Send', {'details': 'test'}, 'external')
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        digest = fingerprint([1, fingerprint(['telegram_send', {'text': 'test'}]), 'Send', 'test'])
        self.store.db.execute('UPDATE actions SET call_id=?,expires_at=?,approval_hash=? WHERE id=?',
                              (call_id, expires, digest, action_id))
        self.store.db.close()
        self.store = Store(self.root / 'test.db')
        self.addCleanup(self.store.db.close)
        self.core.store = self.store
        self.core._execution.store = self.store
        self.core.execution.recover()
        self.assertEqual(effects, [])
        self.core.decide(action_id, True)
        await asyncio.gather(*list(self.core.execution.tasks.values()))
        self.assertEqual(effects, ['test'])
        self.assertEqual(self.store.get_call(call_id)['result'], 'sent')
        self.core.decide(action_id, True)
        self.assertEqual(effects, ['test'])

    async def test_approval_timeout_is_rejected_and_not_executed(self):
        run_id = self.store.create_run('tools', {})
        call_id = uuid4().hex
        self.store.create_call(call_id, run_id, 'telegram_send', {})
        self.store.update_call(call_id, 'waiting_approval')
        action = self.store.add_action('approval', 'Send', {}, 'external')
        self.store.db.execute('UPDATE actions SET call_id=?,expires_at=? WHERE id=?',
                              (call_id, '2000-01-01T00:00:00+00:00', action))
        self.core.decide(action, True)
        self.assertEqual(self.store.get_action(action)['status'], 'rejected')
        self.assertEqual(self.core.execution.tasks, {})

    async def test_async_chat_has_result_and_same_request_id_is_not_repeated(self):
        self.core.chat = AsyncMock(return_value=Outcome('answer'))
        run_id = uuid4().hex
        response = await self.post('/api/runs/chat', {'text': 'hello', 'run_id': run_id})
        self.assertEqual(response.status, 202)
        await asyncio.gather(*list(self.core.jobs.tasks.values()))
        await self.post('/api/runs/chat', {'text': 'hello', 'run_id': run_id})
        self.core.chat.assert_awaited_once()
        result = await self.client.get('/api/runs/' + run_id, headers=self.headers)
        self.assertEqual((await result.json())['result']['reply'], 'answer')
        self.assertEqual((await self.post('/api/runs/chat', {'text': 'different', 'run_id': run_id})).status, 400)

    async def test_cancel_queued_job_does_not_start_it(self):
        self.core.chat = AsyncMock(return_value=Outcome('answer'))
        run = self.core.jobs.submit('chat', {'text': 'x'})
        await self.core.jobs.cancel(run['id'])
        self.assertEqual(self.store.get_run(run['id'])['status'], 'cancelled')
        self.core.chat.assert_not_awaited()

    async def test_cancelling_inline_job_does_not_cancel_parent_service(self):
        started = asyncio.Event()
        survived = []
        async def work():
            started.set()
            await asyncio.Future()
        async def service():
            try:
                await self.core.jobs.inline('agent', {}, work)
            except JobCancelled:
                survived.append(True)
        parent = asyncio.create_task(service())
        await started.wait()
        run_id = next(iter(self.core.jobs.tasks))
        await self.core.jobs.cancel(run_id)
        await parent
        self.assertEqual(survived, [True])
        self.assertFalse(parent.cancelled())

    async def test_recover_only_queued_jobs(self):
        self.core.chat = AsyncMock(return_value=Outcome('answer'))
        queued = self.store.create_run('chat', {'text': 'queued'})
        started = self.store.create_run('chat', {'text': 'started'})
        self.store.update_run(started, 'running')
        self.core.execution.recover()
        self.core.jobs.recover()
        await asyncio.gather(*list(self.core.jobs.tasks.values()))
        self.core.chat.assert_awaited_once_with('queued', 'desktop')
        self.assertEqual(self.store.get_run(started)['status'], 'interrupted')
        self.assertEqual(self.store.get_run(queued)['status'], 'completed')

    async def test_reminder_is_sent_only_after_channel_acknowledgements(self):
        reminder = self.store.add_reminder(datetime.now() - timedelta(minutes=1), 'test')
        for _ in range(2):
            self.store.enqueue_delivery(f'reminder:{reminder}', {'type': 'reminder', 'text': 'test'},
                                         reminder_id=reminder)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0], 2)
        self.assertEqual(len(self.store.due_reminders()), 1)
        telegram = self.store.pending_deliveries('telegram')[0]
        self.store.fail_delivery(telegram['id'], 'offline')
        self.assertEqual(self.store.pending_deliveries('telegram'), [])
        self.store.acknowledge_delivery(telegram['id'], 'telegram')
        self.assertEqual(len(self.store.due_reminders()), 1)
        desktop = self.store.pending_deliveries('desktop')[0]
        self.assertFalse(self.store.acknowledge_delivery(desktop['id'], 'telegram'))
        self.store.acknowledge_delivery(desktop['id'], 'desktop')
        self.assertEqual(self.store.due_reminders(), [])

    async def test_cancel_reminder_cancels_pending_deliveries(self):
        reminder = self.store.add_reminder(datetime.now(), 'test')
        self.store.enqueue_delivery('test', {'text': 'test'}, reminder_id=reminder)
        self.store.close_reminder(reminder, 'cancelled')
        self.assertEqual(self.store.pending_deliveries('telegram'), [])

    async def test_telegram_delivery_retries_after_failure(self):
        from ubermensch.bot import forward_events
        attempted, delivered = asyncio.Event(), asyncio.Event()
        calls = 0
        async def send(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                attempted.set()
                raise RuntimeError('offline')
            delivered.set()
        self.store.enqueue_delivery('message:test', {'type': 'notify', 'text': 'synthetic'},
                                     channels=('telegram',))
        with patch('ubermensch.bot.send', side_effect=send):
            task = asyncio.create_task(forward_events(Mock(), self.core, 123))
            try:
                await asyncio.wait_for(attempted.wait(), 2)
                self.store.db.execute('UPDATE deliveries SET next_attempt=?', (stamp(),))
                self.core.bus.publish({'type': 'changed'})
                await asyncio.wait_for(delivered.wait(), 3)
                self.assertEqual(self.store.pending_deliveries('telegram'), [])
                self.assertEqual(calls, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_background_cannot_dispatch_shell(self):
        @self.core.tool_registry.tool()
        async def run_shell(command: str):
            self.fail('must not dispatch')
        run_id = self.store.create_run('agent', {}, 'background')
        with self.assertRaises(PermissionError):
            await self.core.call_tool('run_shell', {'command': 'echo test'}, run_id)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0], 0)

    async def test_background_path_policy_resolves_symlinks_and_hides_secrets(self):
        allowed = self.root / 'allowed'
        allowed.mkdir()
        outside = self.root / 'outside'
        outside.write_text('test')
        (allowed / 'link').symlink_to(outside)
        with patch.dict(os.environ, {'UBERMENSCH_READ_ROOTS': str(allowed)}):
            self.assertTrue(policy.readable(allowed / 'note.md'))
            self.assertFalse(policy.readable(allowed / 'link'))
            self.assertFalse(policy.readable(allowed / '.env'))

    async def test_feedback_endpoint_records_real_target_only(self):
        response = await self.post('/api/feedback', {'message_id': 99, 'rating': 'helpful'})
        self.assertEqual(response.status, 404)
        message = self.store.add_message('assistant', 'test', 'desktop')
        self.assertEqual((await self.post('/api/feedback', {'message_id': message, 'rating': 'helpful'})).status, 200)
        self.assertEqual(self.store.quality_metrics()['helpful_rate'], 1)
