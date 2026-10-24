"""Переключение ИИ до начала инструментов; после начала повтор запрещён."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from ..brain import Answer, BrainError, MCP_SERVER
from .tools import ToolJournal
from .context import current

log = logging.getLogger(__name__)


class InterruptedToolRun(BrainError):
    def friendly(self) -> str:
        return str(self)


class ChatRouter:
    async def ask(self, primary, prompt: str, *, options: dict, fallback,
                  fallback_available, waiting_for_user, watchdog: float) -> tuple[Answer, str]:
        source = Path(options["mcp_config"])
        context = current.get()
        run_dir = source.parent / "runs" / (context.run_id if context else uuid4().hex)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "mcp.json"
        journal = ToolJournal(run_dir / "tools.jsonl")
        config = json.loads(source.read_text(encoding="utf-8"))
        server = config["mcpServers"][MCP_SERVER]
        server.setdefault("env", {})["UBERMENSCH_TOOL_JOURNAL"] = str(journal.path)
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        task = asyncio.create_task(primary.ask(prompt, **{**options, "mcp_config": config_path}))
        started = asyncio.get_running_loop().time()

        async def use_fallback(error: BrainError | None = None):
            if journal.started:
                raise InterruptedToolRun(
                    "ИИ прервался после запуска инструментов. Часть запроса могла выполниться; "
                    "автоматически повторять его другим ИИ не буду. Проверь результат и уточни "
                    "оставшиеся действия.") from error
            result = await fallback(journal)
            # Keep support for simple single-provider callbacks.
            return result if isinstance(result, tuple) else (result, "openai")

        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=1)
                if done:
                    try:
                        return task.result(), "claude"
                    except BrainError as error:
                        if not fallback_available():
                            raise
                        log.warning("Основной ИИ недоступен, проверяю возможность переключения")
                        return await use_fallback(error)
                if (fallback_available() and asyncio.get_running_loop().time() - started > watchdog
                        and not waiting_for_user() and not journal.started):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, BrainError):
                        await task
                    # Повторная проверка журнала после остановки всего дерева процессов.
                    return await use_fallback()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, BrainError):
                    await task
            config_path.unlink(missing_ok=True)
            if not journal.started:
                run_dir.rmdir()
