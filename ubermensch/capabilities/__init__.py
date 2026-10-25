"""Возможности помощника, общие для всех ИИ и клиентских интерфейсов."""
from .registry import ToolRegistry, registry


def load_registry() -> ToolRegistry:
    # Регистрация явная: произвольные модули и функции модели недоступны.
    from . import (agent, calendar, communication, computer, experiments, files, imports,  # noqa: F401
                   planning, profile, university)

    return registry
