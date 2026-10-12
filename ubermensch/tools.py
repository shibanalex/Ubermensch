"""MCP-сервер инструментов: Claude действует на компьютере и от имени пользователя.

Запускается самим Claude Code на время одного вызова (stdio). Всё, что меняет систему или
уходит другим людям, проходит через «да/нет» пользователя: ядро спрашивает в Telegram и
у маскота. Решает код здесь и в ядре, а не модель.

Это тонкий адаптер: работа с Windows — в ubermensch.system, Telegram и почта — в ядре (API).
Новый инструмент = функция с @mcp.tool() и понятным docstring (его читает Claude).
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer

from . import config
from .system import files, screen, shell, windows

WORKSPACE = config.DATA_DIR / "workspace"

mcp = MCPServer(
    "ubermensch",
    instructions="Инструменты для действий на компьютере пользователя и от его имени. "
                 "Отправка сообщений и писем, изменение файлов и системы требуют подтверждения "
                 "пользователя — инструмент сам спросит его и вернёт результат.",
)


def _core(path: str, payload: dict, timeout: float = 700) -> dict:
    request = urllib.request.Request(
        f"http://{config.API_HOST}:{config.API_PORT}{path}", method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {config.api_token()}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


async def _ask(title: str, details: str = "") -> bool:
    answer = await asyncio.to_thread(_core, "/api/approval", {"title": title, "details": details})
    return bool(answer.get("approved"))


async def _core_result(path: str, payload: dict):
    answer = await asyncio.to_thread(_core, path, payload)
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]


# --- Telegram и почта (выполняет ядро: у него сессия Telegram и пароль почты) ---

@mcp.tool()
async def telegram_find_chats(query: str) -> list[dict]:
    """Найти чаты Telegram пользователя по части имени. Возвращает chat_id для других инструментов."""
    return await _core_result("/api/tg/chats", {"query": query})


@mcp.tool()
async def telegram_read(chat_id: int, limit: int = 30) -> list[dict]:
    """Последние сообщения чата (свежие, прямо из Telegram). «Я» — сам пользователь."""
    return await _core_result("/api/tg/history", {"chat_id": chat_id, "limit": limit})


@mcp.tool()
async def telegram_send(chat_id: int, text: str) -> str:
    """Отправить сообщение от имени пользователя. Перед отправкой он увидит текст и подтвердит.
    Пиши так, как пишет он сам (см. его стиль), а не как ассистент."""
    return await _core_result("/api/tg/send", {"chat_id": chat_id, "text": text})


@mcp.tool()
async def email_send(to: str, subject: str, body: str) -> str:
    """Отправить письмо с Gmail пользователя. Перед отправкой он увидит письмо и подтвердит."""
    return await _core_result("/api/email/send", {"to": to, "subject": subject, "body": body})


# --- Windows ---

@mcp.tool()
async def open_item(target: str) -> str:
    """Открыть файл, папку, сайт (URL) или программу (например «notepad», «chrome», «telegram»)."""
    shell.open_target(target)
    return f"Открыто: {target}"


@mcp.tool()
async def find_files(name_part: str, folder: str | None = None, limit: int = 30) -> list[str]:
    """Найти файлы и папки по части имени (рабочий стол, документы, загрузки, диск D: или folder)."""
    return await asyncio.to_thread(files.find, name_part, folder, limit)


@mcp.tool()
async def run_powershell(command: str, reason: str) -> str:
    """Выполнить команду PowerShell. Команды только на чтение (Get-*, Test-Path …) выполняются сразу,
    всё, что меняет систему, — после подтверждения пользователя. reason — зачем это нужно."""
    if not shell.is_read_only(command) and \
            not await _ask("Выполнить команду PowerShell", f"{reason}\n\n{command}"):
        return "Пользователь не разрешил выполнить команду."
    return await asyncio.to_thread(shell.powershell, command)


@mcp.tool()
async def write_file(path: str, content: str) -> str:
    """Создать или перезаписать текстовый файл. В рабочей папке системы — сразу, в остальных местах —
    после подтверждения пользователя."""
    target = Path(path).expanduser().resolve()
    if WORKSPACE.resolve() not in target.parents:
        verb = "Перезаписать файл" if target.exists() else "Создать файл"
        if not await _ask(f"{verb} {target}", content[:1500]):
            return "Пользователь не разрешил запись."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Записано: {target}"


@mcp.tool()
async def move_path(source: str, destination: str) -> str:
    """Переместить или переименовать файл/папку — после подтверждения пользователя."""
    if not await _ask("Переместить", f"{source}\n→ {destination}"):
        return "Пользователь не разрешил перемещение."
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(source).rename(destination)
    return f"Перемещено в {destination}"


@mcp.tool()
async def delete_to_recycle_bin(path: str) -> str:
    """Удалить файл или папку в Корзину (восстановимо) — после подтверждения пользователя."""
    if not await _ask("Удалить в Корзину", path):
        return "Пользователь не разрешил удаление."
    await asyncio.to_thread(shell.to_recycle_bin, path)
    return f"В Корзине: {path}"


@mcp.tool()
async def media_key(action: Literal["play_pause", "next", "previous", "volume_up", "volume_down",
                                    "mute"], times: int = 1) -> str:
    """Управление музыкой и громкостью: пауза/воспроизведение, треки, громкость (один шаг ≈ 2%)."""
    windows.press_media_key(action, times)
    return "Готово"


@mcp.tool()
async def screenshot() -> str:
    """Снимок экрана пользователя — посмотреть, чем он занят (затем открой файл инструментом Read).
    Не сработает, если на экране может быть личное (банк, пароли, инкогнито)."""
    window = windows.foreground()
    if reason := screen.skip_reason(window):
        return f"Снимок не сделан: {reason}."
    path = WORKSPACE / "screens" / "request.jpg"
    await asyncio.to_thread(screen.capture, path)
    return f"Снимок: {path}. Активное окно: «{window.title}» ({window.app})."


@mcp.tool()
async def lock_screen() -> str:
    """Заблокировать компьютер (экран входа Windows)."""
    windows.lock()
    return "Компьютер заблокирован"


if __name__ == "__main__":
    mcp.run("stdio")
