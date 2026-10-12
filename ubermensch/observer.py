"""Взгляд на экран: изредка Освальд смотрит, чем занят пользователь, и комментирует в пузыре.

Снимок делается только когда пользователь за компьютером, днём, не в полноэкранной игре
или фильме и если на экране нет личного (банк, пароли, инкогнито — см. system/screen.py).
Картинка удаляется сразу после комментария; в дневник активности попадает лишь короткая
подпись «чем занят».
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import TYPE_CHECKING

from .brain import BrainError
from .system import screen, windows

if TYPE_CHECKING:
    from .core import Core

log = logging.getLogger(__name__)

GAP_HOURS = (2, 4)  # случайная пауза между взглядами — 3–5 раз за день
ACTIVE_HOURS = (9, 25)  # с 9 утра до часу ночи (25 = 01:00 следующего дня)
IDLE_LIMIT = 120  # пользователь не трогал мышь дольше — значит, не за компьютером


def _daytime(now: datetime) -> bool:
    hour = now.hour if now.hour >= ACTIVE_HOURS[0] else now.hour + 24
    return ACTIVE_HOURS[0] <= hour < ACTIVE_HOURS[1]


class ScreenObserver:
    def __init__(self, core: Core):
        self.core = core
        self.shot = core.assistant.workspace / "screens" / "now.jpg"

    async def run(self) -> None:
        while True:
            await asyncio.sleep(random.uniform(*GAP_HOURS) * 3600)
            await self.look()

    async def look(self) -> str | None:
        """Один взгляд. Возвращает комментарий или None, если смотреть сейчас нельзя."""
        if not _daytime(datetime.now()) or windows.idle_seconds() > IDLE_LIMIT:
            return None
        window = windows.foreground()
        if reason := screen.skip_reason(window):
            log.info("Взгляд на экран пропущен: %s", reason)
            return None
        await asyncio.to_thread(screen.capture, self.shot)
        try:
            comment, activity = await self.core.assistant.comment_screen(
                self.shot, window.app, window.title)
        except BrainError as e:
            log.info("Взгляд на экран без комментария: %s", e)
            return None
        finally:
            self.shot.unlink(missing_ok=True)  # картинку не храним
        now = datetime.now().isoformat(timespec="seconds")
        self.core.store.add_events([{"source": "screen", "external_id": now, "ts": now,
                                     "thread": window.app, "text": activity}])
        self.core.say(comment)
        return comment
