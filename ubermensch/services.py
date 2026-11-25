"""Фоновые сервисы ядра и надсмотрщик над ними.

Сервис: долгоживущая корутина (цикл): напоминания, сбор данных, разбор, трекер, наблюдатель
экрана, резервные копии… Надсмотрщик запускает их, а если сервис упал: пишет в лог,
отмечает проблему в Health и перезапускает с нарастающей паузой (5 с → 10 с → … → 5 мин).
Один упавший сервис не роняет ядро.

Добавить сервис: supervisor.add("имя", фабрика_корутины). Когда проект перерастёт один процесс,
сервис с тем же интерфейсом можно вынести в отдельный процесс-воркер (очередь: Redis и т. п.).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from .health import Health

log = logging.getLogger(__name__)

FIRST_BACKOFF, MAX_BACKOFF = 5, 300
HEALTHY_AFTER = 600  # проработал 10 минут без падений: пауза перезапуска сбрасывается


@dataclass
class ServiceState:
    name: str
    factory: Callable[[], Awaitable[None]]
    state: str = "ожидает"
    restarts: int = 0
    last_error: str = ""
    started_at: datetime | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class Supervisor:
    def __init__(self, health: Health):
        self.health = health
        self._services: dict[str, ServiceState] = {}

    def add(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        self._services[name] = ServiceState(name, factory)

    def start(self) -> None:
        for service in self._services.values():
            service.task = asyncio.create_task(self._guard(service), name=f"service:{service.name}")

    async def stop(self) -> None:
        tasks = [s.task for s in self._services.values() if s.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _guard(self, service: ServiceState) -> None:
        backoff = FIRST_BACKOFF
        while True:
            service.state, service.started_at = "работает", datetime.now()
            try:
                await service.factory()
                service.last_error = "завершился сам"  # сервисы: бесконечные циклы
            except asyncio.CancelledError:
                service.state = "остановлен"
                raise
            except Exception as e:
                service.last_error = f"{type(e).__name__}: {e}"[:300]
                log.exception("Сервис «%s» упал", service.name)
            worked = (datetime.now() - service.started_at).total_seconds()
            if worked > HEALTHY_AFTER:
                backoff = FIRST_BACKOFF
            service.restarts += 1
            service.state = f"перезапуск через {backoff} с"
            if service.restarts >= 3:
                self.health.report(f"service:{service.name}",
                                   f"Фоновая служба «{service.name}» падает: {service.last_error}",
                                   quiet=service.restarts < 10)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    def status(self) -> list[dict]:
        return [{"name": s.name, "state": s.state, "restarts": s.restarts,
                 "last_error": s.last_error} for s in self._services.values()]
