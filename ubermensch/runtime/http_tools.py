"""Клиент общего API инструментов. Провайдеры ИИ не получают доступ к ОС напрямую."""
from __future__ import annotations

import aiohttp
from uuid import uuid4

from .. import config
from ..brain import BrainError
from ..capabilities.registry import ToolDefinition


class ToolExecutionUncertain(BrainError):
    def friendly(self) -> str:
        return str(self)


class HttpToolGateway:
    def __init__(self, definitions: list[ToolDefinition], *, run_id: str | None = None):
        self._definitions = definitions
        self.run_id = run_id

    @staticmethod
    async def _request(method: str, path: str, payload: dict | None = None):
        token = config.api_token()
        if not token:
            raise BrainError("API инструментов: отсутствует токен ядра")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900),
                                             trust_env=False) as session:
                async with session.request(
                    method, f"http://{config.API_HOST}:{config.API_PORT}{path}", json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    if response.status >= 500 and method == "POST":
                        raise ToolExecutionUncertain(
                            "Инструмент завершился с ошибкой; часть действия могла выполниться. "
                            "Автоматический повтор остановлен. Проверь результат.")
                    data = await response.json()
                    if response.status >= 400 or "error" in data:
                        raise BrainError(f"API инструментов: {data.get('error', response.status)}")
                    return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            if method == "POST":
                raise ToolExecutionUncertain(
                    "Потеряна связь с инструментом; действие могло выполниться. "
                    "Автоматический повтор остановлен. Проверь результат.") from None
            raise BrainError(f"API инструментов недоступен: {type(error).__name__}") from None

    @classmethod
    async def connect(cls, *, run_id: str | None = None):
        data = await cls._request("GET", "/api/tools")
        return cls([ToolDefinition(**item) for item in data["tools"]], run_id=run_id)

    def definitions(self) -> list[ToolDefinition]:
        return self._definitions

    async def call(self, name: str, arguments: dict, *, call_id: str | None = None):
        call_id = call_id or uuid4().hex
        try:
            data = await self._request("POST", "/api/tools/call", {
                "name": name, "arguments": arguments, "run_id": self.run_id, 'call_id': call_id,
            })
        except ToolExecutionUncertain as error:
            raise ToolExecutionUncertain(f'{error} Вызов: {call_id}.') from error
        return data["result"]
