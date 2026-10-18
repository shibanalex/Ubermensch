from __future__ import annotations

import asyncio
import json
import urllib.request

from .. import config


def _core(path: str, payload: dict, timeout: float = 700) -> dict:
    request = urllib.request.Request(
        f"http://{config.API_HOST}:{config.API_PORT}{path}", method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {config.api_token()}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())



async def _ask(title: str, details: str = "") -> bool:
    answer = await asyncio.to_thread(_core, "/api/approval", {"title": title, "details": details})
    return bool(answer.get("approved"))



async def _core_result(path: str, payload: dict):
    answer = await asyncio.to_thread(_core, path, payload)
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]

