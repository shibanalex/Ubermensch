"""Граница между провайдерами ИИ и возможностями помощника."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..capabilities.registry import ToolDefinition, ToolRegistry


class ToolGateway(Protocol):
    def definitions(self) -> list[ToolDefinition]: ...

    async def call(self, name: str, arguments: dict): ...


class ToolJournal:
    """Только имена и статусы; аргументы, содержимое файлов и секреты не пишутся."""
    def __init__(self, path: Path):
        self.path = path

    @property
    def started(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def record(self, name: str, status: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"tool": name, "status": status}) + "\n")
            stream.flush()


class LocalToolGateway:
    def __init__(self, registry: ToolRegistry, journal: ToolJournal | None = None):
        self.registry, self.journal = registry, journal

    def definitions(self) -> list[ToolDefinition]:
        return self.registry.definitions()

    async def call(self, name: str, arguments: dict):
        return await self.registry.call(name, arguments, journal=self.journal)
