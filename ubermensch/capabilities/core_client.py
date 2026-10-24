from __future__ import annotations

import asyncio
import json
import urllib.request

from .. import config
from ..runtime.context import current


def _core(path: str, payload: dict, timeout: float = 700) -> dict:
    request = urllib.request.Request(
        f"http://{config.API_HOST}:{config.API_PORT}{path}", method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {config.api_token()}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())



async def _ask(title: str, details: str = "") -> bool:
    if context := current.get():
        return await context.core.request_approval(title, details)
    answer = await asyncio.to_thread(_core, "/api/approval", {"title": title, "details": details})
    return bool(answer.get("approved"))



async def _core_result(path: str, payload: dict):
    if context := current.get():
        core = context.core
        if path == '/api/tg/send':
            return await core.send_telegram(**payload)
        if path == '/api/email/send':
            return await core.send_email(**payload)
        if path == '/api/tg/chats':
            return await core.tg(payload.get('account', 'personal')).find_chats(payload['query'])
        if path == '/api/tg/history':
            return await core.tg(payload.get('account', 'personal')).history(
                payload['chat_id'], payload.get('limit', 30))
    answer = await asyncio.to_thread(_core, path, payload)
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]

