from __future__ import annotations

import asyncio

from .. import google_calendar
from .core_client import _ask
from .registry import registry


@registry.tool()
async def calendar_upcoming(days: int = 14) -> list[dict]:
    """События Google Календаря на ближайшие дни."""
    return await asyncio.to_thread(google_calendar.upcoming, days)



@registry.tool()
async def calendar_add(title: str, start: str, end: str | None = None, description: str = "",
                       reminder_minutes: int | None = None) -> dict:
    """Добавить событие в Google Календарь пользователя (сразу видно на телефоне).
    start/end: «ГГГГ-ММ-ДД»; на весь день, «ГГГГ-ММ-ДДTЧЧ:ММ»; точное местное время."""
    return await asyncio.to_thread(google_calendar.add, title, start, end, description,
                                   reminder_minutes)



@registry.tool()
async def calendar_delete(event_id: str) -> str:
    """Удалить событие. Свои (добавленные ассистентом): сразу, чужие; после подтверждения."""
    current = await asyncio.to_thread(google_calendar.event, event_id)
    if not google_calendar.is_own(current.get("description")) and not await _ask(
            "Удалить событие из календаря", f"{current.get('summary', '')}\n"
            f"{current['start'].get('dateTime', current['start'].get('date'))}"):
        return "Пользователь не разрешил удаление."
    await asyncio.to_thread(google_calendar.delete, event_id)
    return "Событие удалено."

