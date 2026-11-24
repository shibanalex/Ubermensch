"""Здоровье системы: известные проблемы и что с ними делать пользователю.

Компоненты сообщают о проблеме (report) и о том, что она ушла (clear). Пользователь узнаёт
о проблеме один раз, напоминание — не чаще REMIND_EVERY, и получает «починилось», когда
она исчезает. Список проблем виден в /status, в API и у маскота.

Ключи проблем: brain (Claude: лимит, вход, нет программы), telegram, gmail, disk, backup,
service:<имя> (упавший фоновый сервис).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

REMIND_EVERY = timedelta(hours=6)


@dataclass
class Problem:
    key: str
    message: str  # что случилось и что сделать — по-человечески
    since: datetime = field(default_factory=datetime.now)
    notified_at: datetime | None = None


class Health:
    def __init__(self, notify: Callable[[str], None] | None = None):
        self._notify = notify or (lambda text: None)
        self._problems: dict[str, Problem] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Уведомления уходят через цикл ядра: о проблеме можно сообщать и из рабочих потоков."""
        self._loop, self._loop_thread = loop, threading.get_ident()

    def notify(self, text: str) -> None:
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self._notify, text)
        else:
            self._notify(text)

    def report(self, key: str, message: str, *, quiet: bool = True) -> None:
        """По умолчанию только в списке проблем (/status, API, маскот), без спама в Telegram."""
        problem = self._problems.get(key)
        if problem is None or problem.message != message:
            log.warning("Проблема [%s]: %s", key, message)
            problem = self._problems[key] = Problem(key, message,
                                                    since=problem.since if problem else datetime.now())
        if quiet:
            return
        if problem.notified_at is None or datetime.now() - problem.notified_at > REMIND_EVERY:
            problem.notified_at = datetime.now()
            self.notify(f"⚠️ {message}")

    def clear(self, key: str, recovered: str | None = None) -> None:
        problem = self._problems.pop(key, None)
        if problem is not None:
            log.info("Проблема [%s] решена", key)

    def has(self, key: str) -> bool:
        return key in self._problems

    def problems(self) -> list[dict]:
        return [{"key": p.key, "message": p.message,
                 "since": p.since.isoformat(timespec="seconds")}
                for p in self._problems.values()]
