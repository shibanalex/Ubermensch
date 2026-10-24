"""Trusted execution context, set by the core, never by model tool arguments."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Execution:
    core: object
    run_id: str
    mode: str = 'interactive'
    call_id: str | None = None


current: ContextVar[Execution | None] = ContextVar('execution', default=None)
