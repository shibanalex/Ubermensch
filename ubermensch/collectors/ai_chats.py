"""Разговоры пользователя с ИИ: история Claude Code (на этом компьютере и в архивах).

Берём только реплики самого пользователя: это его собственные слова о себе, делах и планах.
Чаты AI Studio приходят отдельно, через папку импорта.
"""
from __future__ import annotations

import json
import os
import re
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

from . import local_iso

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
# архивы старой истории (например, с прежней Windows и WSL)
BACKUPS = Path(os.getenv("AI_CHATS_BACKUPS", str(Path.home() / "Backups"))).expanduser()
MAX_CHARS = 3000
# служебные вставки Claude Code, а не слова пользователя
NOISE = re.compile(r"<(system-reminder|ide_[a-z_]+|command-[a-z-]+|local-command-[a-z]+)>.*?</\1>",
                   re.S)


def _user_texts(lines: Iterable[bytes], project: str) -> Iterator[dict]:
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") != "user" or entry.get("isMeta") or not entry.get("timestamp"):
            continue
        content = entry.get("message", {}).get("content")
        if isinstance(content, list):  # результаты инструментов: не слова пользователя
            content = "\n".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text")
        text = NOISE.sub("", content or "").strip()
        if len(text) < 2 or text.startswith("<"):
            continue
        yield {
            "source": "ai_chat",
            "external_id": entry.get("uuid") or f"{project}:{entry['timestamp']}",
            "ts": local_iso(datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))),
            "thread": project,
            "outgoing": 1,
            "text": text[:MAX_CHARS],
            "meta": {"tool": "Claude Code"},
        }


def _export_messages(data: list) -> Iterator[dict]:
    """Выгрузки ChatGPT и Claude.ai (conversations.json): только реплики пользователя."""
    for conversation in data:
        if not isinstance(conversation, dict):
            continue
        if "mapping" in conversation:  # ChatGPT
            tool, title = "ChatGPT", conversation.get("title") or "без названия"
            for node_id, node in (conversation.get("mapping") or {}).items():
                message = (node or {}).get("message") or {}
                if (message.get("author") or {}).get("role") != "user" or not message.get("create_time"):
                    continue
                parts = (message.get("content") or {}).get("parts") or []
                text = "\n".join(p for p in parts if isinstance(p, str)).strip()
                if text:
                    yield {"source": "ai_chat", "external_id": f"chatgpt:{node_id}",
                           "ts": local_iso(datetime.fromtimestamp(message["create_time"]).astimezone()),
                           "thread": f"{tool}: {title}"[:200], "outgoing": 1,
                           "text": text[:MAX_CHARS], "meta": {"tool": tool}}
        elif "chat_messages" in conversation:  # Claude.ai
            tool, title = "Claude.ai", conversation.get("name") or "без названия"
            for message in conversation["chat_messages"]:
                if message.get("sender") != "human" or not message.get("created_at"):
                    continue
                text = (message.get("text") or "").strip() or "\n".join(
                    c.get("text", "") for c in message.get("content") or [] if c.get("type") == "text")
                if text.strip():
                    yield {"source": "ai_chat", "external_id": f"claude:{message.get('uuid')}",
                           "ts": local_iso(datetime.fromisoformat(
                               message["created_at"].replace("Z", "+00:00"))),
                           "thread": f"{tool}: {title}"[:200], "outgoing": 1,
                           "text": text.strip()[:MAX_CHARS], "meta": {"tool": tool}}


def export_files(import_dir: Path) -> list[Path]:
    """Кандидаты на выгрузку чатов: conversations*.json и zip-архивы с ними."""
    found = sorted(import_dir.rglob("conversations*.json"))
    for archive in sorted(import_dir.rglob("*.zip")):
        try:
            with zipfile.ZipFile(archive) as z:
                if any(Path(n).name.startswith("conversations") and n.endswith(".json")
                       for n in z.namelist()):
                    found.append(archive)
        except zipfile.BadZipFile:
            continue
    return found


def collect_exports(paths: list[Path]) -> list[dict]:
    events: list[dict] = []
    for path in paths:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                blobs = [z.read(n) for n in z.namelist()
                         if Path(n).name.startswith("conversations") and n.endswith(".json")]
        else:
            blobs = [path.read_bytes()]
        for blob in blobs:
            try:
                data = json.loads(blob)
            except ValueError:
                continue
            if isinstance(data, list):
                events += _export_messages(data)
    return events


def collect() -> list[dict]:
    events: list[dict] = []
    for transcript in CLAUDE_PROJECTS.glob("*/*.jsonl"):
        with transcript.open("rb") as f:
            events += _user_texts(f, transcript.parent.name)
    # история Claude Code, сохранённая из удалённой Ubuntu
    for archive in BACKUPS.glob("wsl-*-home-*.tar.gz"):
        with tarfile.open(archive) as tar:
            for member in tar:
                parts = Path(member.name).parts
                if member.isfile() and member.name.endswith(".jsonl") and ".claude" in parts \
                        and "projects" in parts:
                    project = Path(member.name).parent.name
                    events += _user_texts(tar.extractfile(member), f"WSL {project}")
    return events
