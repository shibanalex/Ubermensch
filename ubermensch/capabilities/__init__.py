"""Возможности помощника, общие для всех ИИ и клиентских интерфейсов."""
from .registry import ToolRegistry, registry


def load_registry() -> ToolRegistry:
    # Регистрация явная: произвольные модули и функции модели недоступны.
    from . import agent, calendar, communication, computer, files, profile, university  # noqa: F401

    return registry
