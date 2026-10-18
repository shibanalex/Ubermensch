"""Возможности помощника, общие для всех ИИ и клиентских интерфейсов."""
from .registry import ToolRegistry, registry


def load_registry() -> ToolRegistry:
    # Регистрация явная: произвольные модули и функции модели недоступны.
    from . import calendar, communication, files, university, windows  # noqa: F401

    return registry
