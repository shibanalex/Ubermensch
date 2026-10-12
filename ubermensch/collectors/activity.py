"""Активность в Windows: какая программа и какое окно на переднем плане, сколько времени.

Истории окон Windows не хранит, поэтому трекер пишет её сам с момента запуска ядра.
Простой (больше 2 минут без мыши и клавиатуры) не считается.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..store import Store
from ..system import windows

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 5
IDLE_SECONDS = 120
MIN_SESSION_SECONDS = 10
SLEEP_GAP_SECONDS = 60  # замеры раз в 5 с; пропуск больше минуты — компьютер спал


class ActivityTracker:
    def __init__(self, store: Store):
        self.store = store
        self._current: tuple[str, str] | None = None
        self._started: datetime | None = None

    def _close_session(self, now: datetime) -> None:
        if self._current and self._started:
            seconds = int((now - self._started).total_seconds())
            if seconds >= MIN_SESSION_SECONDS:
                app, title = self._current
                started = self._started.isoformat(timespec="seconds")
                self.store.add_events([{
                    "source": "activity", "external_id": f"{started}:{app}", "ts": started,
                    "thread": app, "text": title[:300] or app, "meta": {"seconds": seconds},
                }])
        self._current, self._started = None, None

    async def run(self) -> None:
        last_sample = datetime.now()
        while True:
            now = datetime.now()
            if (now - last_sample).total_seconds() > SLEEP_GAP_SECONDS:
                # компьютер спал: сессия кончилась на последнем замере, сон — не работа
                self._close_session(last_sample)
            last_sample = now
            active = None
            if windows.idle_seconds() <= IDLE_SECONDS and (window := windows.foreground()):
                active = (window.app, window.title)
            if active != self._current:
                self._close_session(now)
                if active:
                    self._current, self._started = active, now
            await asyncio.sleep(SAMPLE_SECONDS)
