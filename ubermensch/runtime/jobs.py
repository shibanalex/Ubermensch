"""Long-running application jobs independent of the lifetime of an HTTP request."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from pathlib import Path

from ..brain import BrainError
from .context import Execution, current
from .execution import identifier
from .storage import encoded

TERMINAL = {'completed', 'failed', 'interrupted', 'cancelled'}


class JobCancelled(BrainError):
    def friendly(self):
        return str(self)


def result_data(value):
    if is_dataclass(value):
        value = asdict(value)
        if 'pending' in value:
            value['pending'] = [{**p, 'id': p['action_id']} for p in value['pending']]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [result_data(v) for v in value]
    return value


class Jobs:
    def __init__(self, core):
        self.core, self.store = core, core.store
        self.tasks: dict[str, asyncio.Task] = {}
        self.stopping = False

    def submit(self, kind: str, payload: dict, *, run_id: str | None = None) -> dict:
        run_id = identifier(run_id)
        existing = self.store.get_run(run_id)
        if existing:
            if existing['kind'] != kind or encoded(existing['payload']) != encoded(payload):
                raise ValueError('ID задания уже связан с другим запросом')
            return self.store.public_run(run_id)
        self.store.create_run(kind, payload, 'interactive' if kind in {'chat', 'onboard'} else 'background', run_id)
        self._launch(run_id)
        return self.store.public_run(run_id)

    def _launch(self, run_id):
        task = asyncio.create_task(self._execute(run_id, lambda: self._dispatch(run_id)), name='run:' + run_id)
        self.tasks[run_id] = task

        def done(t):
            self.tasks.pop(run_id, None)
            if not t.cancelled():
                t.exception()
        task.add_done_callback(done)

    async def inline(self, kind, payload, action, *, mode='background'):
        run_id = self.store.create_run(kind, payload, mode)
        task = asyncio.create_task(self._execute(run_id, action), name='run:' + run_id)
        self.tasks[run_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            if not asyncio.current_task().cancelling():
                # Cancelling one job must not cancel its supervisor or Telegram polling handler.
                raise JobCancelled('Задание остановлено пользователем; проверь выполненные действия.') from None
            raise
        finally:
            self.tasks.pop(run_id, None)

    async def _execute(self, run_id, action):
        run = self.store.get_run(run_id)
        token = current.set(Execution(self.core, run_id, run['mode']))
        self.store.update_run(run_id, 'running')
        self.core.bus.publish({'type': 'changed'})
        try:
            result = await action()
            self.store.update_run(run_id, 'completed', result=result_data(result))
            return result
        except asyncio.CancelledError:
            if not self.stopping:
                await self.core.execution.cancel_run(run_id)
            self.store.update_run(run_id, 'interrupted' if self.stopping else 'cancelled', error=
                'Работа остановлена. Уже выполненные действия сохранены; '
                'вызовы с неизвестным результатом требуют проверки.')
            raise
        except Exception as error:
            message = error.friendly() if isinstance(error, BrainError) else (
                f'Работа завершилась с ошибкой ({type(error).__name__}). Проверь журнал действий.')
            self.store.update_run(run_id, 'failed', error=message)
            raise
        finally:
            current.reset(token)
            self.core.bus.publish({'type': 'changed'})

    async def _dispatch(self, run_id):
        run = self.store.get_run(run_id)
        kind, payload = run['kind'], run['payload']
        if kind == 'chat':
            return await self.core.chat(payload['text'], 'desktop')
        if kind == 'onboard':
            if not self.core.needs_onboarding() or self.store.recent_messages(1):
                return {'skipped': True}
            return await self.core.onboard('desktop')
        if kind == 'collect':
            await self.core.investigation.collect()
        elif kind == 'voice':
            await self.core.investigation.refresh_voice()
        elif kind == 'import':
            return await self.core.deep_import(self.core.import_dir / payload['name'])
        elif kind == 'heartbeat':
            return await self.core.autonomy.heartbeat(force=True)
        elif kind == 'profile':
            return await self.core.profiler.build()
        else:
            raise ValueError('Неизвестный тип задания')
        return {'ok': True}

    def recover(self):
        # Only queued requests are safe: the durable 'running' marker precedes effects.
        for row in self.store.db.execute("SELECT id,kind FROM runs WHERE status='queued'"):
            if row['kind'] in {'chat', 'onboard', 'collect', 'voice', 'import', 'heartbeat', 'profile'}:
                self._launch(row['id'])

    async def cancel(self, run_id):
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run['status'] in TERMINAL:
            return self.store.public_run(run_id)
        self.store.update_run(run_id, 'cancelling')
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.core.execution.cancel_run(run_id)
        # A task cancelled before its coroutine starts cannot run its finally block.
        if self.store.get_run(run_id)['status'] == 'cancelling':
            self.store.update_run(run_id, 'cancelled', error='Задание отменено; проверь журнал действий.')
        self.core.bus.publish({'type': 'changed'})
        return self.store.public_run(run_id)

    async def close(self):
        self.stopping = True
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
