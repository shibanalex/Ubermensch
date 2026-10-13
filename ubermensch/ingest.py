"""Чтение документов для импорта в память: чаты Google AI Studio, PDF, Word, .txt и .md.

Большие выгрузки чатов с ИИ (ChatGPT, Claude.ai) и архивы Google Takeout идут другим путём —
через сборщики (collectors/ai_chats.py, collectors/takeout.py), по месяцам.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MAX_CHARS = 2_000_000  # ~35 порций разбора; больше — значит, нужен отдельный сборщик
PART_CHARS = 60_000  # столько текста уходит мозгу за один вызов
MODEL_REPLY_CHARS = 1500  # ответы ИИ в чатах — лишь контекст: факты берутся из слов пользователя
# Повторы при глубоком импорте: абзац выбрасывается, если DUP_SHARE его фраз по 5 слов
# уже встречались в одном из других абзацев.
SHINGLE, DUP_SHARE, MIN_DUP_CHARS = 5, 0.7, 80


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


def user_replicas(path: Path) -> list[str]:
    """Только реплики пользователя из чата AI Studio — для анализа того, как он рассуждает."""
    data = _load_aistudio(path)
    if data is None:
        return []
    return [c["text"].strip() for c in data["chunkedPrompt"].get("chunks", [])
            if c.get("role") == "user" and not c.get("isThought") and (c.get("text") or "").strip()]


def _load_aistudio(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_bytes().decode("utf-8", errors="replace").lstrip("﻿"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and "chunkedPrompt" in data else None


def is_aistudio(path: Path) -> bool:
    return path.suffix.lower() in ("", ".json") and _load_aistudio(path) is not None


def aistudio_full(path: Path) -> list[str]:
    """Глубокий импорт: реплики пользователя и ответы ИИ целиком, по абзацам, без «мыслей».
    Нужен, когда сведения о пользователе живут в ответах ИИ (досье, которое ИИ вёл с его слов)."""
    data = _load_aistudio(path) or {}
    paragraphs = []
    for chunk in data.get("chunkedPrompt", {}).get("chunks", []):
        text = (chunk.get("text") or "").strip()
        if not text or chunk.get("isThought"):
            continue
        who = "Пользователь" if chunk.get("role") == "user" else "ИИ (Gemini)"
        day = (chunk.get("createTime") or "")[:10]
        paragraphs.append(f"[{day}] {who}:" if day else f"{who}:")
        paragraphs += [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs


def _shingles(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + SHINGLE]) for i in range(max(1, len(words) - SHINGLE + 1))}


def dedup(paragraphs: list[str]) -> tuple[list[str], int]:
    """Убирает абзацы, почти целиком повторяющие уже встреченные (ИИ любит пересказывать
    досье заново). Оставляется самая поздняя версия: она точнее. Возвращает (абзацы, сколько
    символов выброшено)."""
    index: dict[str, list[int]] = {}
    kept, dropped = [], 0
    for number, para in enumerate(reversed(paragraphs)):
        shingles = _shingles(para)
        if len(para) >= MIN_DUP_CHARS:
            overlap: dict[int, int] = {}
            for s in shingles:
                for other in index.get(s, ()):
                    overlap[other] = overlap.get(other, 0) + 1
            if overlap and max(overlap.values()) >= DUP_SHARE * len(shingles):
                dropped += len(para)
                continue
        for s in shingles:
            index.setdefault(s, []).append(number)
        kept.append(para)
    return kept[::-1], dropped


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
