from __future__ import annotations

from typing import Literal

from .core_client import _core_result
from .registry import registry

Account = Literal["personal", "work"]


@registry.tool()
async def telegram_find_chats(query: str, account: Account = "personal") -> list[dict]:
    """Найти чаты Telegram пользователя по части имени. Возвращает chat_id для других
    инструментов. account: personal; личный аккаунт, work; рабочий (chat_id у них разные)."""
    return await _core_result("/api/tg/chats", {"query": query, "account": account})



@registry.tool()
async def telegram_read(chat_id: int, limit: int = 30,
                        account: Account = "personal") -> list[dict]:
    """Последние сообщения чата (свежие, прямо из Telegram). «Я»: сам пользователь."""
    return await _core_result("/api/tg/history", {"chat_id": chat_id, "limit": limit,
                                                  "account": account})



@registry.tool()
async def telegram_send(chat_id: int, text: str, account: Account = "personal") -> str:
    """Отправить сообщение от имени пользователя. Перед отправкой он увидит текст и подтвердит.
    Пиши так, как пишет он сам (см. его стиль; для work: рабочий стиль), а не как ассистент."""
    return await _core_result("/api/tg/send", {"chat_id": chat_id, "text": text,
                                               "account": account})



@registry.tool()
async def email_inbox(days: int = 3, query: str = "", limit: int = 20,
                      account: str = "all") -> list[dict]:
    """Свежие входящие Gmail прямо с сервера (без рекламы, соцсетей и форумов), новые первыми:
    id, account, дата, отправитель, тема, unread, начало текста. account: all (все ящики),
    адрес, имя до @ или метка. query: поиск Gmail: from:, subject:, слова, is:unread.
    Письма не помечаются прочитанными."""
    return await _core_result("/api/email/inbox", {"days": days, "query": query, "limit": limit,
                                                   "account": account})


@registry.tool()
async def email_read(message_id: str, account: str) -> dict:
    """Письмо целиком по id и account из email_inbox: отправитель, получатели, тема, текст,
    вложения. id у разных ящиков свои: account обязателен."""
    return await _core_result("/api/email/read", {"message_id": message_id, "account": account})


@registry.tool()
async def email_send(to: str, subject: str, body: str, account: str = "") -> str:
    """Отправить письмо с Gmail пользователя. Перед отправкой он увидит письмо и подтвердит.
    account: с какого ящика (адрес, имя до @ или метка); пусто; основной. Отвечай с того
    ящика, на который пришло письмо."""
    return await _core_result("/api/email/send", {"to": to, "subject": subject, "body": body,
                                                  "account": account})

