from __future__ import annotations

import asyncio

from .. import config
from ..collectors import gdrive
from .registry import registry


@registry.tool()
async def import_shared_chat(url: str) -> str:
    """Забрать чат Gemini (AI Studio) по его ссылке — с доступом «всем, у кого есть ссылка» —
    и отдать в глубокий импорт: важное из чата попадёт в память в течение нескольких минут.
    Повторная загрузка того же чата добавит только новое."""
    try:
        name = await asyncio.to_thread(gdrive.download_shared, url,
                                       config.DATA_DIR / "raw" / "import")
    except (ValueError, OSError) as error:
        return f"Не получилось: {error}"
    return f"Чат забран: «{name}». Разберу его в фоне."
