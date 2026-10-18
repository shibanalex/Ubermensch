from __future__ import annotations

import asyncio

from ..university import site
from .core_client import _core_result
from .registry import registry


@registry.tool()
async def university_search(query: str, limit: int = 10) -> list[dict]:
    """Поиск по сайту вуза пользователя (mospolytech.ru): положения, приказы, услуги МФЦ,
    общежития, расписания, новости. Возвращает заголовки, адреса и выдержки."""
    return await asyncio.to_thread(site.search, query, limit)



@registry.tool()
async def university_read(url: str) -> dict:
    """Прочитать страницу или документ (PDF, Word) с сайта вуза или из личного кабинета
    (e.mospolytech.ru) — текстом, со ссылками на вложенные документы."""
    if "e.mospolytech.ru" in url:
        return await _core_result("/api/lk/read", {"url": url})
    return await asyncio.to_thread(site.read, url)



@registry.tool()
async def lk_find(query: str, limit: int = 5) -> list[dict]:
    """Найти в личном кабинете студента (последний снимок, обновляется раз в день): заявления
    и их статусы, приказы, оценки, долги, справки, сообщения. Пустой результат — уточни слова
    или прочитай страницу кабинета через university_read."""
    return await _core_result("/api/lk/find", {"query": query, "limit": limit})

