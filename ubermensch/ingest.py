"""Чтение документов для импорта в память: чаты Google AI Studio, PDF, Word, .txt и .md.

Большие выгрузки чатов с ИИ (ChatGPT, Claude.ai) и архивы Google Takeout идут другим путём —
через сборщики (collectors/ai_chats.py, collectors/takeout.py), по месяцам.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_CHARS = 2_000_000  # ~35 порций разбора; больше — значит, нужен отдельный сборщик
PART_CHARS = 60_000  # столько текста уходит мозгу за один вызов
MODEL_REPLY_CHARS = 1500  # ответы ИИ в чатах — лишь контекст: факты берутся из слов пользователя


def _aistudio(data: dict) -> str:
    """Переписка из AI Studio: реплики пользователя целиком, ответы модели — сокращённо,
    без «мыслей» модели."""
    lines = []
    for chunk in data.get("chunkedPrompt", {}).get("chunks", []):
        text = (chunk.get("text") or "").strip()
        if not text or chunk.get("isThought"):
            continue
        user = chunk.get("role") == "user"
        if not user and len(text) > MODEL_REPLY_CHARS:
            text = text[:MODEL_REPLY_CHARS].rstrip() + " […]"
        who = "Пользователь" if user else "ИИ (Gemini)"
        day = (chunk.get("createTime") or "")[:10]
        lines.append(f"[{day}] {who}:\n{text}" if day else f"{who}:\n{text}")
    return "\n\n".join(lines)


def _pdf(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def _docx(path: Path) -> str:
    import docx
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        parts += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
    return "\n".join(parts)


def read_document(path: Path) -> str | None:
    """Текст документа или None, если формат не поддерживается."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf(path)
    elif suffix == ".docx":
        text = _docx(path)
    else:
        raw = path.read_bytes().decode("utf-8", errors="replace").lstrip("﻿")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and "chunkedPrompt" in data:
            text = _aistudio(data)
        elif data is None and suffix in ("", ".txt", ".md"):
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
