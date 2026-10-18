from __future__ import annotations

import asyncio
from typing import Literal

from .. import config
from ..system import screen, shell, windows
from .core_client import _ask
from .registry import registry

WORKSPACE = config.DATA_DIR / "workspace"


@registry.tool()
async def open_item(target: str) -> str:
    """Открыть файл, папку, сайт (URL) или программу (например «kate», «chrome», «telegram»)."""
    try:
        await asyncio.to_thread(shell.open_target, target)
    except FileNotFoundError as error:
        return str(error)
    return f"Открыто: {target}"



@registry.tool()
async def run_shell(command: str, reason: str) -> str:
    """Выполнить команду bash (Ubuntu, KDE Plasma) — сразу, в том числе меняющую настройки.
    Подтверждение нужно только опасным: sudo, диски, загрузчик, выключение, учётные записи,
    удаление пакетов, системные папки, память и код Ани. reason — зачем это нужно."""
    if shell.is_dangerous(command) and \
            not await _ask("Опасная команда", f"{reason}\n\n{command}"):
        return "Пользователь не разрешил выполнить команду."
    return await asyncio.to_thread(shell.run, command)



@registry.tool()
async def media_key(action: Literal["play_pause", "next", "previous", "volume_up", "volume_down",
                                    "mute"], times: int = 1) -> str:
    """Управление музыкой и громкостью: пауза/воспроизведение, треки, громкость (один шаг ≈ 2%)."""
    try:
        await asyncio.to_thread(windows.press_media_key, action, times)
    except (RuntimeError, OSError) as error:
        return f"Не получилось: {error}"
    return "Готово"



@registry.tool()
async def screenshot() -> str:
    """Снимок экрана пользователя — посмотреть, чем он занят (затем открой файл инструментом Read).
    Не сработает, если на экране может быть личное (банк, пароли, инкогнито)."""
    window = windows.foreground()
    if reason := screen.skip_reason(window):
        return f"Снимок не сделан: {reason}."
    path = WORKSPACE / "screens" / "request.jpg"
    await asyncio.to_thread(screen.capture, path)
    return f"Снимок: {path}. Активное окно: «{window.title}» ({window.app})."



@registry.tool()
async def lock_screen() -> str:
    """Заблокировать компьютер (экран блокировки)."""
    await asyncio.to_thread(windows.lock)
    return "Компьютер заблокирован"

