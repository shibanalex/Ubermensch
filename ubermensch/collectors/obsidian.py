"""Хранилище Obsidian пользователя: дневник, ориентиры, проекты, заметки; его собственные слова.

Каждая заметка: одно событие (`external_id` = путь в хранилище), которое обновляется на месте,
когда пользователь её правит: идентификатор записи не меняется, поэтому ссылки профиля
остаются верными. Холсты (.canvas): тексты их карточек. Вложения, шаблоны, служебные папки
и отчёты самой системы (08 AI/Reports) не читаются. Вырезки из веба (Clippings) и референсы
(папки References): сведения о мире, а не его слова.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .. import config

SKIP_DIRS = {".obsidian", ".claudian", ".trash", ".git"}  # остальное хранилище: целиком (его решение 26.10.2026)
NOT_OWN_WORDS = {"Clippings", "References"}  # вырезки и референсы: сведения, не его слова
MAX_CHARS = 30_000
DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
COMMENTS = re.compile(r"<!--.*?-->", re.S)


def _frontmatter(text: str) -> dict[str, str]:
    """Простые поля «ключ: значение» (mood, energy, date, type); без полного YAML."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            fields[key.strip()] = value.strip().strip('"\'')
    return fields


def _canvas_text(raw: str) -> str:
    """Тексты карточек и подписи связей холста."""
    try:
        canvas = json.loads(raw)
    except ValueError:
        return ""
    parts = [node.get("text", "") for node in canvas.get("nodes", []) if node.get("type") == "text"]
    parts += [f"[файл] {node['file']}" for node in canvas.get("nodes", []) if node.get("file")]
    parts += [f"[связь] {edge['label']}" for edge in canvas.get("edges", []) if edge.get("label")]
    return "\n\n".join(p for p in parts if p.strip())


def _moment(path: Path, fields: dict[str, str]) -> str:
    """Дата заметки: из полей, из имени (2026-10-18.md), иначе; время последней правки."""
    for candidate in (fields.get("date", ""), path.stem):
        if found := DATE_IN_NAME.search(candidate):
            return f"{found.group(1)}T21:00:00"
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def notes(vault: Path) -> list[Path]:
    reports = config.REPORTS_DIR.resolve() if config.REPORTS_DIR else None
    found = []
    for path in sorted(vault.rglob("*")):
        relative = path.relative_to(vault)
        if path.suffix not in (".md", ".canvas") or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in relative.parts) or \
                (reports and reports in path.resolve().parents):
            continue
        found.append(path)
    return found


def read(path: Path) -> tuple[str, dict[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".canvas":
        return _canvas_text(raw), {}
    return COMMENTS.sub("", raw).strip(), _frontmatter(raw)


def collect(vault: Path | None = None) -> list[dict]:
    vault = vault or config.VAULT_DIR
    if vault is None or not vault.is_dir():
        return []
    events = []
    for path in notes(vault):
        relative = path.relative_to(vault).as_posix()
        try:
            text, fields = read(path)
        except OSError:
            continue
        if not text:
            continue
        own = not NOT_OWN_WORDS & set(relative.split("/")[:-1]) \
            and not fields.get("source", "").startswith("http")  # Web Clipper пишет source
        events.append({
            "source": "obsidian", "external_id": relative, "ts": _moment(path, fields),
            "thread": relative.split("/")[0] if "/" in relative else "корень",
            "author": None if own else "вырезка из веба", "outgoing": 1 if own else 0,
            "text": f"# {relative}\n{text[:MAX_CHARS]}",
            "meta": {"path": relative, **{k: v for k, v in fields.items()
                                          if k in ("type", "mood", "energy", "tags") and v}},
        })
    return events


def context_notes(vault: Path | None = None, limit: int = 8000) -> str:
    """Что пользователь сам написал для своего ИИ и о себе сейчас: для каждого разговора:
    «08 AI/Обо мне», «08 AI/Сейчас», «06 Life/Личные ориентиры» и дневник за два дня."""
    vault = vault or config.VAULT_DIR
    if vault is None or not vault.is_dir():
        return ""
    wanted = [vault / "08 AI" / "Обо мне.md", vault / "08 AI" / "Сейчас.md",
              vault / "06 Life" / "Личные ориентиры.md"]
    daily = sorted((vault / "02 Journal" / "Daily").glob("*.md"))[-2:]
    parts, size = [], 0
    for path in wanted + daily:
        if not path.is_file():
            continue
        text, _ = read(path)
        text = FRONTMATTER.sub("", text).strip()
        block = f"### {path.relative_to(vault).as_posix()}\n{text}"
        if size + len(block) > limit:
            block = block[:max(0, limit - size)]
        if block.strip():
            parts.append(block)
            size += len(block)
        if size >= limit:
            break
    return "\n\n".join(parts)
