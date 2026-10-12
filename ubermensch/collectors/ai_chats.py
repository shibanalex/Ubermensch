"""Разговоры пользователя с ИИ: история Claude Code (Windows и архив из WSL).

Берём только реплики самого пользователя — это его собственные слова о себе, делах и планах.
Чаты AI Studio приходят отдельно, через папку импорта.
"""
from __future__ import annotations

import json
import re
import tarfile
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

from . import local_iso

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
BACKUPS = Path(r"D:\Backups")
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
        if isinstance(content, list):  # результаты инструментов — не слова пользователя
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
