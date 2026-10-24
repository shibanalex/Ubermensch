"""Telegram: уведомления, подтверждения и чат с телефона.

Отвечает только владельцу (TG_CHAT_ID), остальных молча игнорирует.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiogram.utils.chat_action import ChatActionSender

from . import actions
from .assistant import Outcome
from .brain import BrainError
from .config import Settings
from .core import Core
from .store import AREAS

log = logging.getLogger(__name__)

TG_LIMIT = 4000
COMMANDS = [
    BotCommand(command="tasks", description="Задачи на сегодня"),
    BotCommand(command="goals", description="Мои цели"),
    BotCommand(command="memory", description="Что ты обо мне знаешь"),
    BotCommand(command="forget", description="Забыть факт: /forget 12"),
    BotCommand(command="reminders", description="Напоминания"),
    BotCommand(command="cancel", description="Отменить напоминание: /cancel 3"),
    BotCommand(command="status", description="Состояние системы"),
    BotCommand(command="profile", description="Мой профиль: /profile или /profile обновить"),
    BotCommand(command="question", description="Задай мне вопрос обо мне"),
    BotCommand(command="agent", description="Твои поручения и расписание"),
    BotCommand(command="login", description="Войти в Telegram: /login или /login work"),
]


def to_html(text: str) -> str:
    """Минимальный Markdown → Telegram HTML: **жирный**, `код`, заголовки → жирный."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    return re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", s, flags=re.M)


def split(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режет по абзацам, чтобы не превысить лимит сообщения Telegram."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        if current and len(current) + len(para) + 2 > limit:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


async def send(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    for chunk in split(text):
        try:
            await bot.send_message(chat_id, to_html(chunk), **kwargs)
        except TelegramBadRequest:
            # Модель выдала разметку, которую Telegram не принял — шлём как есть.
            await bot.send_message(chat_id, chunk, parse_mode=None, **kwargs)


def _approval_keyboard(action_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнить", callback_data=f"act:{action_id}:y"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"act:{action_id}:n"),
    ]])


def _health_lines(core: Core) -> list[str]:
    lines = ["", "**Проблемы**"] + [f"⚠️ {p['message']}" for p in core.health.problems()]
    if len(lines) == 2:
        lines = ["", "Проблем нет ✅"]
    broken = [s for s in core.supervisor.status() if s["state"] != "работает"]
    lines += [f"🔧 {s['name']}: {s['state']} ({s['last_error']})" for s in broken]
    return lines


def _investigation_lines(core: Core) -> list[str]:
    status = core.investigation.status()
    jobs = status["jobs"]
    total = sum(jobs.values())
    events = ", ".join(f"{k}: {v}" for k, v in sorted(status["events"].items())) or "пока нет"
    lines = ["", "**Расследование**", f"Собрано: {events}"]
    if total:
        lines.append(f"Разобрано порций: {jobs.get('done', 0)} из {total}"
                     + (f", ошибок: {jobs['failed']}" if jobs.get("failed") else ""))
    if status["dossier_at"]:
        lines.append(f"Досье: {status['dossier_at'][:16].replace('T', ' ')}")
    return lines


def create_dispatcher(settings: Settings, core: Core) -> Dispatcher:
    store = core.store
    owner = Router(name="owner")
    owner.message.filter(F.chat.id == settings.tg_chat_id)
    owner.callback_query.filter(F.from_user.id == settings.tg_chat_id)
    strangers = Router(name="strangers")
    started_at = time.monotonic()
    stats = {"calls": 0, "last_ms": 0}

    async def deliver(bot: Bot, outcome: Outcome) -> None:
        # Как человек в мессенджере: несколько коротких сообщений с паузой «печатает…».
        # Служебные строки («запомнил…») тут не нужны — они видны в консоли на ПК.
        parts = [p.strip() for p in outcome.reply.split("\n\n") if p.strip()] or ["…"]
        for number, part in enumerate(parts):
            if number:
                await bot.send_chat_action(settings.tg_chat_id, "typing")
                await asyncio.sleep(min(2.5, 0.6 + len(part) / 120))
            await send(bot, settings.tg_chat_id, part)
        for p in outcome.pending:
            await send(
                bot, settings.tg_chat_id,
                f"🔐 **Нужно подтверждение** ({actions.RISK_LABELS[p.risk]})\n{p.summary}",
                reply_markup=_approval_keyboard(p.action_id),
            )

    async def think(bot: Bot, text: str | None) -> None:
        """text=None — знакомство при первом запуске."""
        started = time.monotonic()
        try:
            async with ChatActionSender.typing(bot=bot, chat_id=settings.tg_chat_id):
                outcome = await (core.onboard("tg") if text is None else core.chat(text, "tg"))
        except BrainError as e:
            log.error("Мозг недоступен: %s", e)
            await send(bot, settings.tg_chat_id, e.friendly())
            return
        stats["calls"] += 1
        stats["last_ms"] = int((time.monotonic() - started) * 1000)
        await deliver(bot, outcome)

    @owner.message(CommandStart())
    async def on_start(message: Message) -> None:
        if core.needs_onboarding():
            await think(message.bot, None)
        else:
            await message.answer("Я на связи. Пиши, что на уме, или смотри /tasks.")

    @owner.message(Command("tasks"))
    async def on_tasks(message: Message) -> None:
        tasks = store.tasks()
        if not tasks:
            await message.answer("Задач нет. Попроси меня спланировать день.")
            return
        await send(message.bot, message.chat.id, "\n".join(
            ("✅ " if t["status"] == "done" else "▫️ ") + f"#{t['id']} {t['title']}"
            + (f" — до {t['due']}" if t["due"] and t["status"] != "done" else "")
            for t in tasks
        ))

    @owner.message(Command("goals"))
    async def on_goals(message: Message) -> None:
        goals = store.goals()
        if not goals:
            await message.answer("Целей пока нет. Расскажи, чего хочешь добиться.")
            return
        lines = []
        for area, title in AREAS.items():
            items = [g for g in goals if g["area"] == area]
            if items:
                lines.append(f"**{title}**")
                lines += [f"#{g['id']} {g['title']}"
                          + (f" — до {g['deadline']}" if g["deadline"] else "") for g in items]
        await send(message.bot, message.chat.id, "\n".join(lines))

    @owner.message(Command("memory"))
    async def on_memory(message: Message) -> None:
        facts = store.facts(200)
        if not facts:
            await message.answer("Пока я ничего о тебе не знаю.")
            return
        lines = [f"Запомнено фактов: {len(facts)}. Удалить: /forget номер\n"]
        for area, title in AREAS.items():
            items = [f for f in facts if f["area"] == area]
            if items:
                lines.append(f"**{title}**")
                lines += [f"#{f['id']} {f['content']}" for f in items]
        await send(message.bot, message.chat.id, "\n".join(lines))

    @owner.message(Command("forget"))
    async def on_forget(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lstrip("#")
        if not arg.isdigit():
            await message.answer("Формат: /forget 12 (номер из /memory)")
            return
        ok = store.forget_fact(int(arg))
        await message.answer(f"Факт #{arg} забыт." if ok else f"Факта #{arg} нет.")

    @owner.message(Command("reminders"))
    async def on_reminders(message: Message) -> None:
        items = store.pending_reminders()
        if not items:
            await message.answer("Активных напоминаний нет.")
            return
        await send(message.bot, message.chat.id, "\n".join(
            f"#{r['id']} {r['due_at'][:16].replace('T', ' ')} — {r['text']}" for r in items
        ) + "\n\nОтменить: /cancel номер")

    @owner.message(Command("cancel"))
    async def on_cancel(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lstrip("#")
        if not arg.isdigit():
            await message.answer("Формат: /cancel 3 (номер из /reminders)")
            return
        ok = store.close_reminder(int(arg), "cancelled")
        await message.answer(f"Напоминание #{arg} отменено." if ok else f"Напоминания #{arg} нет.")

    @owner.message(Command("status"))
    async def on_status(message: Message) -> None:
        c = store.counts()
        uptime = int(time.monotonic() - started_at)
        await send(message.bot, message.chat.id, "\n".join([
            "**Аня (anya.exe)**",
            f"Работает: {uptime // 3600} ч {uptime % 3600 // 60} мин",
            f"Модель: {settings.claude_model}; вызовов из Telegram: {stats['calls']}, "
            f"последний: {stats['last_ms'] / 1000:.1f} с",
            f"Фактов: {c['facts']}, целей: {c['goals']}, задач: {c['tasks']}, "
            f"напоминаний: {c['reminders']}",
            f"Сообщений в истории: {c['messages']}, ждут подтверждения: {c['actions_pending']}",
            *_investigation_lines(core),
            *_health_lines(core),
        ]))

    @owner.message(Command("profile"))
    async def on_profile(message: Message, command: CommandObject) -> None:
        if core.profiler is None:
            await send(message.bot, message.chat.id, "Профиль ещё не подключён.")
            return
        if (command.args or "").strip().lower() in ("обновить", "rebuild", "update"):
            core.profiler.request()
            await send(message.bot, message.chat.id,
                       "Начинаю обновлять профиль: уже разобранное беру из кеша, новое разбираю "
                       "в фоне, когда ты не занят. Напишу, когда будет готово.")
            return
        s = core.profiler.status()
        lines = ["**Профиль**",
                 f"Сейчас: {s['stage']}" + (f" ({s['done']} из {s['total']})" if s["total"] else "")]
        if s["profile_id"]:
            lines += [f"Последний: {s['built_at'] or '—'}, утверждений {s['claims']}, "
                      f"правил поддержки {s['rules']}", f"Файл: {s['report']}"]
        else:
            lines.append("Готового профиля пока нет.")
        await send(message.bot, message.chat.id, "\n".join(lines))

    @owner.message(Command("question"))
    async def on_question(message: Message) -> None:
        if core.interviewer is None or core.interviewer.ask() is None:
            await send(message.bot, message.chat.id, "Вопросов пока нет: профиль ещё строится "
                       "или я уже обо всём спросила. Загляну в данные — появятся новые.")

    @owner.message(Command("agent"))
    async def on_agent(message: Message) -> None:
        s = core.autonomy.status() if core.autonomy else {}
        beat = s.get("heartbeat") or {}
        lines = ["**Сама**", f"Последний heartbeat: {beat.get('at', '—')} — {beat.get('decision', '—')}; "
                 f"своих сообщений сегодня: {s.get('messages_today', 0)}",
                 *(f"• {t['title']} [{t['status']}]" for t in s.get("tasks", [])),
                 *(f"⏰ {line}" for line in s.get("schedules", []))]
        if s.get("task"):
            lines.append(f"Сейчас работаю над: {s['task']}")
        await send(message.bot, message.chat.id, "\n".join(lines))

    @owner.message(Command("login"))
    async def on_login(message: Message, command: CommandObject) -> None:
        account = (command.args or "personal").strip().lower()
        account = {"личный": "personal", "рабочий": "work"}.get(account, account)
        try:
            await send(message.bot, message.chat.id, await core.start_tg_login(account))
        except ValueError as e:
            await message.answer(str(e))

    @owner.message(Command("login_cancel"))
    async def on_login_cancel(message: Message) -> None:
        core.login = None
        await message.answer("Вход отменён.")

    @owner.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        if core.login is not None:  # идёт вход в Telegram: это номер, код или пароль
            secret = core.login["step"] in ("code", "password")
            reply = await core.login_input(message.text)
            if secret:  # код и пароль не оставляем в чате
                with contextlib.suppress(Exception):
                    await message.delete()
            await send(message.bot, message.chat.id, reply)
            return
        await think(message.bot, message.text)

    @owner.message()
    async def on_other(message: Message) -> None:
        await message.answer("Пока я понимаю только текст и команды из меню.")

    @owner.callback_query(F.data.startswith("act:"))
    async def on_decision(query: CallbackQuery) -> None:
        _, raw_id, choice = query.data.split(":")
        result = core.decide(int(raw_id), choice == "y")
        if result is None:
            await query.answer("Уже решено")
            return
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=None)
        await send(query.bot, settings.tg_chat_id, result)

    @strangers.message()
    async def on_stranger(message: Message) -> None:
        log.warning("Сообщение от чужого: user=%s chat=%s",
                    message.from_user and message.from_user.id, message.chat.id)

    dp = Dispatcher()
    dp.include_routers(owner, strangers)
    return dp


async def forward_events(bot: Bot, core: Core, chat_id: int) -> None:
    """Напоминания, уведомления и запросы подтверждения из ядра — в Telegram, чтобы дойти до телефона."""
    queue = core.bus.subscribe()
    try:
        while True:
            # Durable deliveries survive offline Telegram and a core restart.
            for delivery in core.store.pending_deliveries('telegram'):
                event = delivery['event']
                try:
                    if event['type'] == 'approval':
                        action = core.store.get_action(event['id'])
                        if action and action['status'] == 'pending':
                            await bot.send_message(chat_id,
                                f"🔐 <b>{html.escape(event['title'])}</b>\n\n"
                                + html.escape(event.get('details', '')[:3000]),
                                reply_markup=_approval_keyboard(event['id']))
                    else:
                        prefix = '⏰ ' if event['type'] == 'reminder' else ''
                        await send(bot, chat_id, prefix + event['text'])
                    core.store.acknowledge_delivery(delivery['id'], 'telegram')
                except Exception as error:
                    core.store.fail_delivery(delivery['id'], type(error).__name__)
                    log.warning('Доставка #%s отложена: %s', delivery['id'], type(error).__name__)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
            except TimeoutError:
                continue
            try:
                if event["type"] in ("reminder", "notify"):
                    prefix = "⏰ " if event["type"] == "reminder" else ""
                    await send(bot, chat_id, f"{prefix}{event['text']}")
                elif event["type"] == "approval":  # действие ждёт «да/нет»
                    details = event["details"][:3000]
                    await bot.send_message(
                        chat_id, f"🔐 <b>{html.escape(event['title'])}</b>"
                        + (f"\n\n{html.escape(details)}" if details else ""),
                        reply_markup=_approval_keyboard(event["id"]))
            except Exception:
                log.exception("Не удалось отправить уведомление в Telegram")
    finally:
        core.bus.unsubscribe(queue)


async def run(settings: Settings, core: Core) -> None:
    bot = Bot(settings.tg_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(settings, core)
    await bot.set_my_commands(COMMANDS)
    forwarder = asyncio.create_task(forward_events(bot, core, settings.tg_chat_id))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        forwarder.cancel()
        await asyncio.gather(forwarder, return_exceptions=True)
        await bot.session.close()
