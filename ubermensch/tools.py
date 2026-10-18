"""Тонкий MCP-адаптер общего реестра возможностей для Claude Code (stdio)."""
from __future__ import annotations

import os
from functools import wraps
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .capabilities import load_registry
from .runtime.tools import ToolJournal
from .runtime.http_tools import HttpToolGateway


def create_server() -> MCPServer:
    registry = load_registry()
    server = MCPServer(
        "ubermensch",
        instructions="Общие инструменты помощника. Сообщения и письма людям и опасные "
                     "системные операции запрашивают подтверждение сами.",
    )
    marker = os.getenv("UBERMENSCH_TOOL_JOURNAL")
    journal = ToolJournal(Path(marker)) if marker else None
    gateway = HttpToolGateway(registry.definitions(),
                              run_id=journal.path.parent.name if journal else None)

    def adapter(function):
        @wraps(function)
        async def invoke(**arguments):
            if journal is not None:
                journal.record(function.__name__, "dispatched")
            return await gateway.call(function.__name__, arguments)
        return invoke

    for function in registry.functions():
        server.add_tool(adapter(function))
    return server


if __name__ == "__main__":
    create_server().run("stdio")
