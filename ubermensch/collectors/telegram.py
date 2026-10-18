"""Telegram через официальный API (Telethon) от имени пользователя.

Берём личные чаты и «Избранное» целиком. Из групп — только важное: свои сообщения (вместе с тем,
на что ответил) и обращения к пользователю; чужая болтовня — шум. Каналы и боты не читаем.
Сессия = полный доступ к аккаунту, поэтому лежит в secrets с доступом только владельцу.

Аккаунтов два: личный (TG_API_ID/TG_API_HASH) и рабочий (TG_WORK_API_ID/TG_WORK_HASH), у каждого
своя сессия. Рабочие чаты помечены «(рабочий TG)», чтобы разбор понимал контекст.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (AuthKeyUnregisteredError, PasswordHashInvalidError,
                             PhoneCodeExpiredError, PhoneCodeInvalidError, PhoneNumberInvalidError,
                             SessionPasswordNeededError, SessionRevokedError, UserDeactivatedError)
from telethon.tl.types import (InputMessagesFilterMyMentions, MessageMediaDocument,
                               MessageMediaGeo, MessageMediaPhoto, MessageMediaPoll, User)

from .. import config
from ..store import Store
from . import local_iso

log = logging.getLogger(__name__)

SESSION = config.DATA_DIR / "secrets" / "telegram"
SERVICE_USER_ID = 777000  # служебные уведомления Telegram
MESSAGE_CHARS = 2000


@dataclass(frozen=True)
class Profile:
    key: str  # personal | work
    label: str  # для сообщений пользователю: «личный», «рабочий»
    session: Path
    id_env: str
    hash_env: str
    login_bat: str
    phone_env: str  # номер для входа; нет в .env — Аня спросит в боте

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.id_env) and os.getenv(self.hash_env))

    @property
    def personal(self) -> bool:
        return self.key == "personal"


PERSONAL = Profile("personal", "личный", SESSION, "TG_API_ID", "TG_API_HASH",
                   "telegram_login.sh", "TG_PHONE")
WORK = Profile("work", "рабочий", config.DATA_DIR / "secrets" / "telegram_work",
               "TG_WORK_API_ID", "TG_WORK_HASH", "telegram_work_login.sh", "TG_WORK_PHONE")
PROFILES = {p.key: p for p in (PERSONAL, WORK)}


def client(profile: Profile = PERSONAL) -> TelegramClient:
    tg = TelegramClient(str(profile.session), int(os.environ[profile.id_env]),
                        os.environ[profile.hash_env], device_model="Übermensch", app_version="1.0")
    tg.flood_sleep_threshold = 120  # короткие ограничения Telegram пережидаем сами
    return tg


def _media_label(message) -> str:
    media = message.media
    if media is None:
        return ""
    if isinstance(media, MessageMediaPhoto):
        return "[фото]"
    if isinstance(media, MessageMediaDocument):
        if message.voice:
            return "[голосовое]"
        if message.video_note or message.video:
            return "[видео]"
        if message.sticker:
            return "[стикер]"
        if message.gif:
            return "[gif]"
        return f"[файл: {message.file.name or 'без имени'}]"
    if isinstance(media, MessageMediaGeo):
        return "[геопозиция]"
    if isinstance(media, MessageMediaPoll):
        return "[опрос]"
    return "[вложение]"


class TelegramLoginRequired(RuntimeError):
    """Нет входа: сессия не создана, завершена в настройках Telegram или удалена."""


class LoginStepError(RuntimeError):
    """Шаг входа не прошёл (неверный номер, код или пароль) — текст для пользователя."""


def _sender_name(sender) -> str:
    name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "кто-то"
    if getattr(sender, "last_name", None):
        name += f" {sender.last_name}"
    return name


class TelegramAccount:
    """Один клиент на сессию на всё ядро: сбор переписок и действия от имени пользователя.

    Два клиента на одном файле сессии мешают друг другу, поэтому ядро держит один.
    """

    def __init__(self, profile: Profile = PERSONAL):
        self.profile = profile
        self._client: TelegramClient | None = None
        self._lock = asyncio.Lock()
        self._login: tuple[str, str] | None = None  # (номер, phone_code_hash) — идёт вход

    def _thread(self, name: str, kind: str) -> str:
        """Имя чата в данных; рабочие помечены, чтобы разбор понимал контекст."""
        tags = (["группа"] if kind == "group" else []) + ([] if self.profile.personal
                                                          else ["рабочий TG"])
        return f"{name} ({', '.join(tags)})" if tags else name

    def _id(self, dialog_id: int, message_id: int) -> str:
        """Номера сообщений у аккаунтов пересекаются — у рабочего свой префикс."""
        prefix = "" if self.profile.personal else f"{self.profile.key}:"
        return f"{prefix}{dialog_id}:{message_id}"

    def _meta(self, **meta) -> dict:
        return meta if self.profile.personal else {**meta, "account": self.profile.key}

    async def _require(self) -> TelegramClient:
        """Подключённый и авторизованный клиент, иначе TelegramLoginRequired."""
        async with self._lock:
            if self._client is None:
                self._client = client(self.profile)
            if not self._client.is_connected():
                await self._client.connect()
            try:
                authorized = await self._client.is_user_authorized()
            except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError):
                authorized = False
            if not authorized:
                raise TelegramLoginRequired(f"нет входа в {self.profile.label} Telegram — "
                                            f"запусти {self.profile.login_bat}")
            return self._client

    # --- вход (Аня входит сама, код пользователь присылает в бота) ---

    async def _connected(self) -> TelegramClient:
        if self._client is None:
            self._client = client(self.profile)
        if not self._client.is_connected():
            await self._client.connect()
        return self._client

    async def authorized(self) -> bool:
        async with self._lock:
            try:
                return await (await self._connected()).is_user_authorized()
            except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError):
                return False

    async def send_code(self, phone: str) -> None:
        """Шаг 1: Telegram присылает код в приложение (или SMS)."""
        async with self._lock:
            tg = await self._connected()
            try:
                sent = await tg.send_code_request(phone)
            except PhoneNumberInvalidError:
                raise LoginStepError("Telegram не принял номер — пришли его в формате +79991234567")
            self._login = (phone, sent.phone_code_hash)

    async def enter_code(self, code: str) -> bool:
        """Шаг 2: код. True — вошли, False — нужен облачный пароль."""
        if self._login is None:
            raise LoginStepError("Код уже не нужен — вход не начат")
        phone, code_hash = self._login
        async with self._lock:
            tg = await self._connected()
            try:
                await tg.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
            except SessionPasswordNeededError:
                return False
            except PhoneCodeInvalidError:
                raise LoginStepError("Код не подошёл — проверь и пришли ещё раз (с пробелами)")
            except PhoneCodeExpiredError:
                self._login = None
                raise LoginStepError("Код устарел — Telegram мог сбросить его, если он ушёл в "
                                     "чат целиком. Начну вход заново: /login")
        self._login = None
        return True

    async def enter_password(self, password: str) -> None:
        """Шаг 3: облачный пароль (двухэтапная проверка)."""
        async with self._lock:
            tg = await self._connected()
            try:
                await tg.sign_in(password=password)
            except PasswordHashInvalidError:
                raise LoginStepError("Пароль не подошёл — пришли ещё раз")
        self._login = None

    # --- действия ---

    async def find_chats(self, query: str, limit: int = 10) -> list[dict]:
        tg = await self._require()
        query = query.casefold()
        found = []
        async for dialog in tg.iter_dialogs(limit=500):
            if query in (dialog.name or "").casefold():
                kind = "канал" if dialog.is_channel and not dialog.is_group else                     "группа" if dialog.is_group else "личный"
                found.append({"chat_id": dialog.id, "name": dialog.name, "kind": kind,
                              "unread": dialog.unread_count})
                if len(found) >= limit:
                    break
        return found

    async def history(self, chat_id: int, limit: int = 30) -> list[dict]:
        tg = await self._require()
        messages = await tg.get_messages(chat_id, limit=min(limit, 100))
        result = []
        for message in reversed(messages):
            text = " ".join(filter(None, [_media_label(message), message.message or ""]))
            if not text.strip():
                continue
            author = "Я" if message.out else _sender_name(message.sender or await message.get_sender())
            result.append({"time": local_iso(message.date), "author": author, "text": text})
        return result

    async def chat_name(self, chat_id: int) -> str:
        tg = await self._require()
        entity = await tg.get_entity(chat_id)
        return _sender_name(entity)

    async def send(self, chat_id: int, text: str) -> None:
        tg = await self._require()
        await tg.send_message(chat_id, text)

    # --- сбор ---

    async def _group_highlights(self, tg: TelegramClient, dialog, since: datetime,
                                last_id: int) -> tuple[list[dict], int]:
        """Из группы — свои сообщения (с цитатой того, на что ответил) и обращения ко мне.

        Отбор делает сам Telegram (поиск по отправителю и по упоминаниям), поэтому даже
        в огромных группах не приходится листать чужую переписку.
        """
        since_utc = since.astimezone()
        found: dict[int, dict] = {}
        newest = last_id
        for query in ({"from_user": "me"}, {"filter": InputMessagesFilterMyMentions}):
            async for message in tg.iter_messages(dialog.entity, min_id=last_id, **query):
                if not last_id and message.date < since_utc:
                    break
                newest = max(newest, message.id)
                if message.id in found or message.action is not None:
                    continue
                text = " ".join(filter(None, [_media_label(message), message.message or ""]))
                if not text.strip():
                    continue
                if message.out and message.is_reply:
                    replied = await message.get_reply_message()
                    if replied is not None:
                        quote = (replied.message or _media_label(replied))[:200]
                        who = _sender_name(replied.sender or await replied.get_sender())
                        text = f"(в ответ {who}: «{quote}») {text}"
                author = "Я" if message.out else                     _sender_name(message.sender or await message.get_sender())
                found[message.id] = {
                    "source": "telegram",
                    "external_id": self._id(dialog.id, message.id),
                    "ts": local_iso(message.date),
                    "thread": self._thread(dialog.name, "group"),
                    "author": author,
                    "outgoing": int(bool(message.out)),
                    "text": text[:MESSAGE_CHARS],
                    "meta": self._meta(chat_id=dialog.id, kind="group", mention=not message.out),
                }
        return sorted(found.values(), key=lambda e: e["ts"]), newest

    async def collect(self, store: Store, since: datetime) -> tuple[int, int]:
        """Собирает новые сообщения. Возвращает (новых сообщений, прочитанных чатов)."""
        tg = await self._require()
        added = chats = 0
        async for dialog in tg.iter_dialogs():
            entity = dialog.entity
            if dialog.is_channel and not dialog.is_group:
                continue  # каналы — чужие публикации, не жизнь пользователя
            if isinstance(entity, User) and (entity.bot or entity.id == SERVICE_USER_ID):
                continue
            kind = "self" if isinstance(entity, User) and entity.is_self else                 "user" if dialog.is_user else "group"
            name = "Избранное" if kind == "self" else dialog.name
            cursor_key = f"telegram:{dialog.id}" if self.profile.personal else \
                f"telegram:{self.profile.key}:{dialog.id}"
            last_id = int(store.cursor(cursor_key) or 0)
            if kind == "group":
                batch, last_id = await self._group_highlights(tg, dialog, since, last_id)
                if batch:
                    added += store.add_events(batch)
                    chats += 1
                if last_id:
                    store.set_cursor(cursor_key, str(last_id))
                continue
            batch: list[dict] = []
            messages = tg.iter_messages(entity, reverse=True, min_id=last_id) if last_id else                 tg.iter_messages(entity, reverse=True, offset_date=since)
            async for message in messages:
                last_id = max(last_id, message.id)
                if message.action is not None:
                    continue  # служебные: «вступил в группу», «закрепил» и т. п.
                text = " ".join(filter(None, [_media_label(message), message.message or ""]))
                if not text.strip():
                    continue
                if message.out:
                    author = "Я"
                elif kind == "user":
                    author = name
                else:
                    author = _sender_name(message.sender or await message.get_sender())
                batch.append({
                    "source": "telegram",
                    "external_id": self._id(dialog.id, message.id),
                    "ts": local_iso(message.date),
                    "thread": self._thread(name, kind),
                    "author": author,
                    "outgoing": int(bool(message.out)),
                    "text": text[:MESSAGE_CHARS],
                    "meta": self._meta(chat_id=dialog.id, kind=kind),
                })
            if batch:
                added += store.add_events(batch)
                chats += 1
            if last_id:
                store.set_cursor(cursor_key, str(last_id))
        return added, chats
