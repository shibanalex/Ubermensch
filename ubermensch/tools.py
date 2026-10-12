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

from . import config, google_calendar
from .system import files, office, screen, shell, windows

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


async def _may_write(target: Path, preview: str) -> bool:
    """В своих зонах Освальд пишет сам, в остальных местах — после «да» пользователя."""
    if config.in_free_zone(target):
        return True
    verb = "Перезаписать файл" if target.exists() else "Создать файл"
    return await _ask(f"{verb} {target}", preview[:1500])


@mcp.tool()
async def write_file(path: str, content: str) -> str:
    """Создать или перезаписать текстовый файл (.md, .txt …). В своих зонах (заметки Obsidian,
    «Документы\\Oswald», рабочая папка) — сразу, в остальных местах — после подтверждения."""
    target = Path(path).expanduser().resolve()
    if not await _may_write(target, content):
        return "Пользователь не разрешил запись."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Записано: {target}"


@mcp.tool()
async def append_file(path: str, text: str) -> str:
    """Дописать текст в конец файла (например, в дневную заметку Obsidian). Правила зон — как у write_file."""
    target = Path(path).expanduser().resolve()
    if not await _may_write(target, text):
        return "Пользователь не разрешил запись."
    target.parent.mkdir(parents=True, exist_ok=True)
    separator = "\n" if target.exists() and target.stat().st_size else ""
    with target.open("a", encoding="utf-8") as f:
        f.write(separator + text)
    return f"Дописано: {target}"


@mcp.tool()
async def write_word(path: str, title: str, markdown: str) -> str:
    """Создать документ Word (.docx) из простого Markdown (# заголовки, - списки, **жирный**).
    В «Документы\\Oswald» — сразу, в остальных местах — после подтверждения."""
    target = Path(path).expanduser().resolve().with_suffix(".docx")
    if not await _may_write(target, markdown):
        return "Пользователь не разрешил запись."
    await asyncio.to_thread(office.write_word, target, title, markdown)
    return f"Документ: {target}"


@mcp.tool()
async def write_excel(path: str, sheet: str, rows: list[list], append: bool = False) -> str:
    """Записать лист таблицы Excel (.xlsx): rows — строки, первая — заголовки. append=true — дописать
    строки в конец листа (например, расходы). В «Документы\\Oswald» — сразу, иначе — с подтверждением."""
    target = Path(path).expanduser().resolve().with_suffix(".xlsx")
    if not await _may_write(target, "\n".join(map(str, rows[:20]))):
        return "Пользователь не разрешил запись."
    await asyncio.to_thread(office.write_excel, target, sheet, rows, append)
    return f"Таблица: {target}, лист «{sheet}»"


@mcp.tool()
async def read_excel(path: str) -> dict:
    """Прочитать таблицу Excel: листы и их строки (до 200 на лист)."""
    return await asyncio.to_thread(office.read_excel, Path(path))


# --- Google Календарь (свой календарь пользователя: добавлять можно сразу) ---

@mcp.tool()
async def calendar_upcoming(days: int = 14) -> list[dict]:
    """События Google Календаря на ближайшие дни."""
    return await asyncio.to_thread(google_calendar.upcoming, days)


@mcp.tool()
async def calendar_add(title: str, start: str, end: str | None = None, description: str = "",
                       reminder_minutes: int | None = None) -> dict:
    """Добавить событие в Google Календарь пользователя (сразу видно на телефоне).
    start/end: «ГГГГ-ММ-ДД» — на весь день, «ГГГГ-ММ-ДДTЧЧ:ММ» — точное местное время."""
    return await asyncio.to_thread(google_calendar.add, title, start, end, description,
                                   reminder_minutes)


@mcp.tool()
async def calendar_delete(event_id: str) -> str:
    """Удалить событие. Свои (добавленные Освальдом) — сразу, чужие — после подтверждения."""
    current = await asyncio.to_thread(google_calendar.event, event_id)
    if google_calendar.MARK not in (current.get("description") or "") and not await _ask(
            "Удалить событие из календаря", f"{current.get('summary', '')}\n"
            f"{current['start'].get('dateTime', current['start'].get('date'))}"):
        return "Пользователь не разрешил удаление."
    await asyncio.to_thread(google_calendar.delete, event_id)
    return "Событие удалено."


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
