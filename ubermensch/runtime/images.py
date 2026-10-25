"""Картинки из результатов инструментов — модели, а не только путь к файлу.

Инструмент кладёт в результат image_path; Claude API получает картинку в tool_result,
OpenAI — следующим сообщением (в сообщении инструмента там только текст), Claude Code
открывает файл сам (Read). Прикладываются только снимки из workspace/screens: результат
инструмента не должен превращаться в способ отправить модели любой файл с диска.
"""
from __future__ import annotations

import base64
from pathlib import Path

from .. import config

IMAGE_KEY = "image_path"
MAX_BYTES = 4_000_000
TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def screens_dir() -> Path:
    return (config.DATA_DIR / "workspace" / "screens").resolve()


def attached(result) -> tuple[str, str] | None:
    """(media_type, base64) снимка из результата инструмента или None."""
    if not isinstance(result, dict) or not isinstance(result.get(IMAGE_KEY), str):
        return None
    path = Path(result[IMAGE_KEY]).expanduser().resolve()
    media_type = TYPES.get(path.suffix.lower())
    if media_type is None or screens_dir() not in path.parents or not path.is_file() \
            or path.stat().st_size > MAX_BYTES:
        return None
    return media_type, base64.b64encode(path.read_bytes()).decode("ascii")
