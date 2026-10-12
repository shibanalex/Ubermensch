"""Локальный HTTP API ядра для приложения на ПК (и позже телефона через приватную сеть).

Доступ только по токену в заголовке Authorization: веб-страницы в браузере не могут
ни прочитать токен, ни отправить такой заголовок без CORS, которого здесь нет.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from datetime import date

from aiohttp import web

from .brain import BrainError
from .core import Core

log = logging.getLogger(__name__)
TASK_STATUSES = {"open", "done", "dropped"}


def _outcome(outcome) -> dict:
    return {
        "reply": outcome.reply,
        "changes": outcome.changes,
        "done": outcome.done,
        "pending": [{"id": p.action_id, "summary": p.summary, "risk": p.risk}
                    for p in outcome.pending],
    }


def create_app(core: Core, token: str) -> web.Application:
    expected = f"Bearer {token}".encode()

    @web.middleware
    async def auth(request: web.Request, handler):
        given = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            raise web.HTTPUnauthorized()
        return await handler(request)

    async def body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="ожидался JSON") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="ожидался JSON-объект")
        return data

    async def get_state(request: web.Request) -> web.Response:
        return web.json_response(core.state())

    async def post_chat(request: web.Request) -> web.Response:
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            raise web.HTTPBadRequest(text="пустое сообщение")
        try:
            return web.json_response(_outcome(await core.chat(text, "desktop")))
        except BrainError as e:
            return web.json_response({"error": e.friendly()}, status=503)

    async def post_onboard(request: web.Request) -> web.Response:
        if not core.needs_onboarding() or core.store.recent_messages(1):
            return web.json_response({"skipped": True})
        try:
            return web.json_response(_outcome(await core.onboard("desktop")))
        except BrainError as e:
            return web.json_response({"error": e.friendly()}, status=503)

    async def post_task(request: web.Request) -> web.Response:
        data = await body(request)
        title = str(data.get("title", "")).strip()
        if not title:
            raise web.HTTPBadRequest(text="пустая задача")
        due = data.get("due") or date.today().isoformat()
        return web.json_response({"id": core.add_task(title, due)})

    async def post_task_status(request: web.Request) -> web.Response:
        status = (await body(request)).get("status")
        if status not in TASK_STATUSES:
            raise web.HTTPBadRequest(text="неизвестный статус")
        return web.json_response({"ok": core.set_task_status(int(request.match_info["id"]), status)})

    async def post_decision(request: web.Request) -> web.Response:
        approve = bool((await body(request)).get("approve"))
        result = core.decide(int(request.match_info["id"]), approve)
        return web.json_response({"result": result if result is not None else "Уже решено"})

    async def post_collect(request: web.Request) -> web.Response:
        """Собрать данные сейчас (например, сразу после входа в Telegram)."""
        asyncio.create_task(core.investigation.collect())
        return web.json_response({"started": True})

    async def get_investigation(request: web.Request) -> web.Response:
        return web.json_response(core.investigation.status())

    async def get_health(request: web.Request) -> web.Response:
        return web.json_response({"problems": core.health.problems(),
                                  "services": core.supervisor.status()})

    # --- для сервера инструментов (ubermensch.tools): действия и подтверждения ---

    async def post_approval(request: web.Request) -> web.Response:
        """Ждёт «да/нет» пользователя (до 10 минут) — запрос висит всё это время."""
        data = await body(request)
        approved = await core.request_approval(str(data.get("title", "Действие")),
                                               str(data.get("details", "")))
        return web.json_response({"approved": approved})

    async def tool_call(request: web.Request, action) -> web.Response:
        try:
            return web.json_response({"result": await action(await body(request))})
        except Exception as e:  # ошибку видит Claude и может поправиться
            log.warning("Инструмент %s: %s", request.path, e)
            return web.json_response({"error": str(e)})

    async def post_tg_chats(request: web.Request) -> web.Response:
        return await tool_call(request, lambda d: core.telegram.find_chats(str(d["query"])))

    async def post_tg_history(request: web.Request) -> web.Response:
        return await tool_call(request, lambda d: core.telegram.history(
            int(d["chat_id"]), int(d.get("limit", 30))))

    async def post_tg_send(request: web.Request) -> web.Response:
        return await tool_call(request, lambda d: core.send_telegram(
            int(d["chat_id"]), str(d["text"])))

    async def post_email_send(request: web.Request) -> web.Response:
        return await tool_call(request, lambda d: core.send_email(
            str(d["to"]), str(d["subject"]), str(d["body"])))

    async def get_events(request: web.Request) -> web.StreamResponse:
        """Server-Sent Events: клиент держит соединение и получает события шины."""
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        })
        await response.prepare(request)
        await response.write(b": connected\n\n")  # клиент сразу понимает, что ядро на связи
        queue = core.bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    line = ": ping\n\n"  # держим соединение живым и замечаем обрыв
                await response.write(line.encode("utf-8"))
        except ConnectionResetError:
            pass  # клиент закрыл соединение
        finally:
            core.bus.unsubscribe(queue)
        return response

    app = web.Application(middlewares=[auth])
    app.add_routes([
        web.get("/api/state", get_state),
        web.post("/api/chat", post_chat),
        web.post("/api/onboard", post_onboard),
        web.post("/api/tasks", post_task),
        web.post(r"/api/tasks/{id:\d+}", post_task_status),
        web.post(r"/api/actions/{id:\d+}", post_decision),
        web.get("/api/events", get_events),
        web.post("/api/collect", post_collect),
        web.get("/api/investigation", get_investigation),
        web.get("/api/health", get_health),
        web.post("/api/approval", post_approval),
        web.post("/api/tg/chats", post_tg_chats),
        web.post("/api/tg/history", post_tg_history),
        web.post("/api/tg/send", post_tg_send),
        web.post("/api/email/send", post_email_send),
    ])
    return app


async def start(core: Core, token: str, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_app(core, token), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("API слушает http://%s:%s", host, port)
    return runner
