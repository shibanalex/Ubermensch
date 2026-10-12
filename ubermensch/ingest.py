"""Чтение документов для импорта в память: экспорт Google AI Studio, .txt и .md."""
from __future__ import annotations

import json
from pathlib import Path

MAX_CHARS = 400_000  # больше — нужен отдельный импортёр (например, для выгрузки Telegram)
PART_CHARS = 60_000  # столько текста уходит мозгу за один вызов


def _aistudio(data: dict) -> str:
    """Переписка из AI Studio: реплики пользователя и модели, без «мыслей» модели."""
    lines = []
    for chunk in data.get("chunkedPrompt", {}).get("chunks", []):
        text = (chunk.get("text") or "").strip()
        if not text or chunk.get("isThought"):
            continue
        who = "Пользователь" if chunk.get("role") == "user" else "ИИ (Gemini)"
        day = (chunk.get("createTime") or "")[:10]
        lines.append(f"[{day}] {who}:\n{text}" if day else f"{who}:\n{text}")
    return "\n\n".join(lines)


def read_document(path: Path) -> str | None:
    """Текст документа или None, если формат не поддерживается."""
    raw = path.read_bytes().decode("utf-8", errors="replace").lstrip("﻿")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and "chunkedPrompt" in data:
        text = _aistudio(data)
    elif data is None and path.suffix.lower() in ("", ".txt", ".md"):
        text = raw
    else:
        return None
    return text if text.strip() and len(text) <= MAX_CHARS else None


def split(text: str, limit: int = PART_CHARS) -> list[str]:
    """Режет по абзацам на части не длиннее limit."""
    parts, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > limit:
            parts.append(current)
            current = ""
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts
