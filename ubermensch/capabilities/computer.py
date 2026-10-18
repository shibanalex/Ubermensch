from __future__ import annotations

import asyncio
from typing import Literal

from .. import config
from ..system import plasma, screen, shell, windows
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



@registry.tool()
async def system_status() -> str:
    """Состояние компьютера: батарея, громкость, яркость, что играет, активное окно, простой,
    режим «не беспокоить»."""
    battery, volume, light, playing = await asyncio.gather(
        asyncio.to_thread(plasma.battery), asyncio.to_thread(plasma.volume),
        asyncio.to_thread(plasma.brightness), asyncio.to_thread(plasma.now_playing))
    window = windows.foreground()
    lines = [
        f"Батарея: {battery['percent']}%, {battery['state']}" if battery else "Батарея: нет данных",
        f"Громкость: {volume['percent']}%" + (" (выключен звук)" if volume["muted"] else "")
        if volume else "Громкость: нет данных",
        f"Яркость экрана: {light}%" if light is not None else "Яркость: не регулируется",
        "Играет: " + ("; ".join(f"{p['player']} — {p['status']}: {p['artist']} «{p['title']}»"
                                for p in playing) or "ничего"),
        f"Активное окно: «{window.title}» ({window.app})" if window else "Активное окно: нет",
        f"Без мыши и клавиатуры: {windows.idle_seconds():.0f} с",
        "«Не беспокоить»: " + ("включено" if plasma._inhibit["cookie"] else "выключено"),
    ]
    return "\n".join(lines)


@registry.tool()
async def desktop_notify(title: str, text: str, urgent: bool = False) -> str:
    """Показать уведомление на экране компьютера (заметнее пузыря маскота; urgent — важное)."""
    await asyncio.to_thread(plasma.notify, title, text, urgent)
    return "Уведомление показано"


@registry.tool()
async def windows_list() -> str:
    """Открытые окна программ: id, программа, заголовок, свёрнуто ли, активно ли."""
    found = await asyncio.to_thread(plasma.window_list)
    return "\n".join(f"{w['id']} | {w['app']} | «{w['title']}»"
                     + (" | активно" if w["active"] else "") + (" | свёрнуто" if w["minimized"] else "")
                     for w in found) or "Окон нет"


@registry.tool()
async def window_control(window_id: str,
                         action: Literal["focus", "minimize", "maximize", "restore", "close",
                                         "fullscreen"]) -> str:
    """Окно по id из windows_list: показать (focus), свернуть, развернуть, вернуть размер, закрыть
    (программа сама спросит про несохранённое), во весь экран."""
    window = await asyncio.to_thread(plasma.window_action, window_id, action)
    return f"Готово: «{window['title']}» ({window['app']})"


@registry.tool()
async def clipboard_read() -> str:
    """Текст в буфере обмена. Только когда пользователь сам просит: там бывают пароли."""
    text = await asyncio.to_thread(plasma.clipboard)
    return text[:8000] or "(буфер пуст)"


@registry.tool()
async def clipboard_write(text: str) -> str:
    """Положить текст в буфер обмена — пользователь вставит его сам (Ctrl+V)."""
    await asyncio.to_thread(plasma.set_clipboard, text)
    return "Текст в буфере обмена"


@registry.tool()
async def set_brightness(percent: int) -> str:
    """Яркость экрана, 1–100%."""
    return f"Яркость: {await asyncio.to_thread(plasma.set_brightness, percent)}%"


@registry.tool()
async def do_not_disturb(minutes: int) -> str:
    """Режим «не беспокоить» для уведомлений на minutes минут (0 — выключить)."""
    on = await asyncio.to_thread(plasma.do_not_disturb, minutes)
    return f"«Не беспокоить» на {minutes} мин" if on else "«Не беспокоить» выключено"


@registry.tool()
async def suspend_computer(reason: str) -> str:
    """Перевести компьютер в сон — только с подтверждением пользователя. reason — зачем."""
    if not await _ask("Усыпить компьютер", reason):
        return "Пользователь не разрешил."
    await asyncio.to_thread(plasma.suspend)
    return "Компьютер засыпает"
