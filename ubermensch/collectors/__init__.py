"""Сборщики данных о пользователе. Каждый возвращает список событий для store.add_events.

Событие: source, external_id (уникален в источнике), ts (местное время ISO), thread
(чат, тема письма, сайт, программа), author, outgoing (написал сам пользователь), text, meta.
Тяжёлая работа сборщиков идёт в отдельных потоках; в базу пишет только ядро.
"""
from __future__ import annotations

from datetime import datetime, timezone


def local_iso(moment: datetime) -> str:
    """Время в местном часовом поясе без смещения: так хранится всё в базе."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
