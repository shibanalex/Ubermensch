"""Контракты и проверка аргументов инструментов без зависимости от провайдера ИИ."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model
from pydantic import ValidationError


class ToolArgumentsError(ValueError):
    """Отклонено до выполнения: модель может исправить аргументы без повторения действия."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict


class ToolRegistry:
    def __init__(self):
        self._functions: dict[str, Callable] = {}
        self._arguments: dict[str, type[BaseModel]] = {}

    def tool(self):
        def register(function: Callable) -> Callable:
            name = function.__name__
            if name in self._functions:
                raise ValueError(f"Инструмент {name} уже зарегистрирован")
            hints = get_type_hints(function)
            fields = {
                parameter.name: (
                    hints.get(parameter.name, Any),
                    ... if parameter.default is inspect.Parameter.empty else parameter.default,
                )
                for parameter in inspect.signature(function).parameters.values()
            }
            self._arguments[name] = create_model(
                f"{name}_arguments", __config__=ConfigDict(extra="forbid", strict=True), **fields)
            self._functions[name] = function
            return function
        return register

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name, inspect.getdoc(function) or name,
                               self._arguments[name].model_json_schema())
                for name, function in self._functions.items()]

    def functions(self) -> tuple[Callable, ...]:
        return tuple(self._functions.values())

    def has(self, name: str) -> bool:
        return name in self._functions

    def validate(self, name: str, arguments: dict) -> dict:
        if name not in self._functions:
            raise ToolArgumentsError(f"Неизвестный инструмент: {name}")
        try:
            values = self._arguments[name].model_validate(arguments).model_dump()
        except ValidationError as error:
            raise ToolArgumentsError('Некорректные аргументы: ' + ', '.join(
                '.'.join(map(str, e['loc'])) for e in error.errors())) from None
        return values

    async def call(self, name: str, arguments: dict, *, journal=None):
        values = self.validate(name, arguments)
        # Запись делается до действия: после сбоя нельзя автоматически повторять запрос.
        if journal is not None:
            journal.record(name, "started")
        try:
            result = await self._functions[name](**values)
        except BaseException:
            if journal is not None:
                journal.record(name, "interrupted")
            raise
        if journal is not None:
            journal.record(name, "completed")
        return result


registry = ToolRegistry()
