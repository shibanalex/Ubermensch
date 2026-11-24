"""Второй ИИ — OpenAI API (платно, по токенам): фоновый разбор без личного и запасной ответ
в чате, когда Claude в лимите или завис.

Тот же интерфейс, что у Brain (ask → Answer по JSON-схеме). В чате доступны общие
инструменты через ToolGateway; фоновый разбор всегда без инструментов.
Две модели: дешёвая для фона (много данных) и посильнее для запасного ответа в чате (ответов
мало, качество важнее). Флагман (GPT-6 Astra) на бюджет в $10 не рассчитан: весь Takeout
обошёлся бы в ~$58.
Каждый вызов считается в долларах по токенам; на OPENAI_WARN_USD — предупреждение, на
OPENAI_BUDGET_USD — стоп, всё возвращается на Claude. Системный прокси учитывается (trust_env).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from .brain import Answer, BrainError
from .health import Health
from .store import Store
from .runtime import images
from .runtime.tools import ToolGateway
from .runtime.http_tools import ToolExecutionUncertain

log = logging.getLogger(__name__)

API = "https://api.openai.com/v1"
# Первая доступная по порядку (список моделей аккаунта — GET /models).
PREFERRED = {
    "background": ("gpt-6-luna", "gpt-6.1-luna", "gpt-5.6-luna", "gpt-5-mini", "gpt-4.1-mini",
                   "gpt-4o-mini"),
    "chat": ("gpt-6-sol", "gpt-5.6-terra", "gpt-5", "gpt-4.1", "gpt-5-mini",
             "gpt-4o-mini"),
}
MODEL_ENV = {"background": "OPENAI_MODEL", "chat": "OPENAI_CHAT_MODEL"}
EFFORT = {"background": "low", "chat": "medium"}
# gpt-6.1-sol is advertised for Chat Completions, but its current API rejects function tools
# with every supported reasoning_effort value.  gpt-6-sol accepts tools with effort="none".
TOOL_INCOMPATIBLE = ("gpt-6.1-sol",)
TOOL_EFFORT_NONE = ("gpt-6-sol",)
# $ за миллион токенов (вход, выход); по самому длинному совпадению начала имени.
# Неизвестная модель считается по цене флагмана — бюджет не уйдёт в минус незаметно.
PRICES = {"gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6-sol": (2.0, 10.0),
          "gpt-6-luna": (0.1, 0.5), "gpt-6.1-luna": (0.1, 0.5), "gpt-5.6-terra": (2.0, 12.0),
          "gpt-5.6-luna": (0.2, 1.2), "gpt-5-mini": (0.25, 2.0), "gpt-5-nano": (0.05, 0.4),
          "gpt-5": (1.25, 10.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4),
          "gpt-4.1": (2.0, 8.0), "gpt-4o-mini": (0.15, 0.6)}
UNKNOWN_PRICE = (10.0, 50.0)
TIMEOUT = 240
MAX_TOOL_ROUNDS = 12
MAX_TOOL_RESULT = 60000
PAUSE_AFTER_FAILURE = timedelta(hours=1)  # ключ, баланс или регион — не долбить каждую минуту


class OpenAIError(BrainError):
    def friendly(self) -> str:
        return str(self)


class OpenAIBrain:
    def __init__(self, store: Store, health: Health, notify):
        self.store, self.health, self.notify = store, health, notify
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.budget = float(os.getenv("OPENAI_BUDGET_USD", "50"))
        self.warn_at = float(os.getenv("OPENAI_WARN_USD", "45"))
        self._models = {role: os.getenv(env, "").strip() or self.store.cursor(f"openai:model:{role}")
                        for role, env in MODEL_ENV.items()}
        self._blocked_until: datetime | None = None

    # --- состояние ---

    @property
    def spent(self) -> float:
        return float(self.store.cursor("openai:spent") or 0)

    @property
    def usable(self) -> bool:
        """Есть ключ, бюджет не исчерпан и недавно не было отказа."""
        return bool(self.key) and self.spent < self.budget and not (
            self._blocked_until and datetime.now() < self._blocked_until)

    def status(self) -> dict | None:
        if not self.key:
            return None
        return {"spent": round(self.spent, 3), "budget": self.budget,
                "model": " / ".join(m for m in self._models.values() if m) or None}

    # --- вызов ---

    async def _request(self, session: aiohttp.ClientSession, method: str, path: str,
                       body: dict | None = None) -> dict:
        try:
            async with session.request(method, API + path, json=body,
                                       headers={"Authorization": f"Bearer {self.key}"}) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    self._fail(resp.status, (data or {}).get("error") or {})
                return data
        except (aiohttp.ClientError, TimeoutError) as e:
            raise OpenAIError(f"OpenAI недоступен: {e.__class__.__name__}") from None

    def _fail(self, status: int, error: dict) -> None:
        code, message = error.get("code") or "", error.get("message") or ""
        if status == 401:
            text = "OpenAI не принял ключ — проверь OPENAI_API_KEY в .env."
        elif code == "insufficient_quota":
            text = "На балансе OpenAI кончились деньги — пополни баланс, пока работаю на Claude."
        elif code == "unsupported_country_region_territory" or status == 403:
            text = f"OpenAI отказал в доступе ({status}): {message[:150]}"
        elif status == 429:
            raise OpenAIError("OpenAI: слишком много запросов, попробую позже")
        else:
            raise OpenAIError(f"OpenAI: ошибка {status} {code} {message[:200]}")
        self._blocked_until = datetime.now() + PAUSE_AFTER_FAILURE
        self.health.report("openai", text, quiet=True)
        raise OpenAIError(text)

    async def _pick_model(self, session: aiohttp.ClientSession, role: str) -> str:
        """Модель для роли: из .env или первая доступная из PREFERRED; запоминается."""
        if self._models[role]:
            return self._models[role]
        available = {m["id"] for m in (await self._request(session, "GET", "/models"))["data"]}
        model = next((m for m in PREFERRED[role] if m in available), None)
        if not model:
            raise OpenAIError("OpenAI: нет ни одной из моделей " + ", ".join(PREFERRED[role]))
        self._models[role] = model
        self.store.set_cursor(f"openai:model:{role}", model)
        log.info("OpenAI: для «%s» — модель %s", role, model)
        return model

    async def _pick_tool_model(self, session: aiohttp.ClientSession, model: str) -> str:
        """Replace a cached/explicit chat model that cannot use tools on this endpoint."""
        if not model.startswith(TOOL_INCOMPATIBLE):
            return model
        available = {m["id"] for m in (await self._request(session, "GET", "/models"))["data"]}
        fallback = next((m for m in PREFERRED["chat"] if m in available), None)
        if not fallback:
            raise OpenAIError("OpenAI: нет совместимой с инструментами резервной модели")
        self._models["chat"] = fallback
        self.store.set_cursor("openai:model:chat", fallback)
        log.warning("OpenAI: %s не поддерживает инструменты в Chat Completions; "
                    "переключаю чат на %s", model, fallback)
        return fallback

    async def ask(self, prompt: str, *, system_prompt_file: Path, schema: dict,
                  role: str = "background", tool_gateway: ToolGateway | None = None,
                  timeout: int | None = None, **_ignored) -> Answer:
        """Как Brain.ask: ответ JSON по схеме. role — background (дешёвая модель) или chat
        (посильнее). Инструменты разрешены только для chat и только явно выданные."""
        if role not in MODEL_ENV:
            raise OpenAIError(f"OpenAI: неизвестная роль {role}")
        if role != "chat" and tool_gateway is not None:
            raise OpenAIError("Инструменты запрещены для фонового разбора")
        if not self.key:
            raise OpenAIError("OpenAI не подключён")
        if self.spent >= self.budget:
            raise OpenAIError(f"Бюджет OpenAI (${self.budget:g}) исчерпан")
        if self._blocked_until and datetime.now() < self._blocked_until:
            raise OpenAIError("OpenAI временно недоступен")
        started = datetime.now()
        request_timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with aiohttp.ClientSession(timeout=request_timeout, trust_env=True) as session:
            model = await self._pick_model(session, role)
            definitions = tool_gateway.definitions() if tool_gateway is not None else []
            if definitions:
                model = await self._pick_tool_model(session, model)
            body = {
                "model": model,
                "store": False,
                "messages": [
                    {"role": "system", "content": system_prompt_file.read_text(encoding="utf-8")},
                    {"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "answer", "schema": schema, "strict": False}},
            }
            if model.startswith(("gpt-5", "gpt-6", "o")):  # модели с рассуждением
                body["reasoning_effort"] = EFFORT[role]  # фон — «low»: дешевле и быстрее
            if definitions:
                body["tools"] = [{"type": "function", "function": {
                    "name": tool.name, "description": tool.description,
                    "parameters": tool.parameters, "strict": False,
                }} for tool in definitions]
                body["parallel_tool_calls"] = False
                if model.startswith(TOOL_EFFORT_NONE):
                    body["reasoning_effort"] = "none"
            try:
                async with asyncio.timeout(timeout or (900 if tool_gateway else TIMEOUT)):
                    answer = await self._conversation(session, model, body, tool_gateway)
            except TimeoutError:
                raise OpenAIError("OpenAI: истекло время запроса; начатые действия не повторяются") from None
        self.health.clear("openai")
        ms = int((datetime.now() - started).total_seconds() * 1000)
        log.info("OpenAI %s: %d мс, потрачено всего $%.3f", model, ms, self.spent)
        return Answer(data=answer, duration_ms=ms)

    async def _conversation(self, session, model: str, body: dict,
                            gateway: ToolGateway | None) -> dict:
        for turn in range(MAX_TOOL_ROUNDS + 1):
            # Считаются все ответы, включая промежуточные вызовы функций.
            if not self.usable:
                raise OpenAIError("OpenAI: бюджет исчерпан или провайдер временно недоступен")
            if turn == MAX_TOOL_ROUNDS and body.get("tools"):
                body["tool_choice"] = "none"
            data = await self._request(session, "POST", "/chat/completions", body)
            self._charge(model, data.get("usage") or {})
            try:
                choice = data["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError):
                raise OpenAIError("OpenAI: пустой ответ") from None
            calls = message.get("tool_calls") or []
            if not calls:
                try:
                    answer = json.loads(message.get("content") or "")
                except (TypeError, ValueError):
                    raise OpenAIError("OpenAI: ответ не по схеме") from None
                required = body["response_format"]["json_schema"]["schema"].get("required", [])
                if not isinstance(answer, dict) or any(key not in answer for key in required):
                    raise OpenAIError("OpenAI: ответ не по схеме")
                return answer
            if gateway is None or not body.get("tools"):
                raise OpenAIError("OpenAI попытался вызвать невыданный инструмент")
            if turn == MAX_TOOL_ROUNDS or len(calls) > 16:
                raise OpenAIError("OpenAI: превышен лимит вызовов инструментов")
            body["messages"].append({"role": "assistant", "content": message.get("content"),
                                     "tool_calls": calls})
            allowed = {tool["function"]["name"] for tool in body["tools"]}
            pictures = []  # в сообщении инструмента у OpenAI только текст — картинки следом
            for call in calls:
                if not self.usable:
                    raise OpenAIError("OpenAI: бюджет исчерпан, следующие действия остановлены")
                try:
                    name = call["function"]["name"]
                    if name not in allowed:
                        raise ValueError("Инструмент не выдан этому запросу")
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("Аргументы должны быть JSON-объектом")
                    result = await gateway.call(name, arguments)
                    if image := images.attached(result):
                        pictures.append((name, image))
                    content = json.dumps(result, ensure_ascii=False)
                    if len(content) > MAX_TOOL_RESULT:
                        content = json.dumps({"truncated": True, "text": content[:MAX_TOOL_RESULT]},
                                             ensure_ascii=False)
                except ToolExecutionUncertain:
                    raise
                except Exception as error:
                    log.warning("Ошибка инструмента OpenAI: %s", type(error).__name__)
                    content = json.dumps({"error": str(error)[:1500]}, ensure_ascii=False)
                body["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                         "content": content})
            for name, (media_type, data) in pictures:
                body["messages"].append({"role": "user", "content": [
                    {"type": "text", "text": f"Изображение из результата инструмента {name}."},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}]})
        raise OpenAIError("OpenAI: превышен лимит шагов")

    def _charge(self, model: str, usage: dict) -> None:
        matches = [name for name in PRICES if model.startswith(name)]
        price_in, price_out = PRICES[max(matches, key=len)] if matches else UNKNOWN_PRICE
        cost = (usage.get("prompt_tokens", 0) * price_in
                + usage.get("completion_tokens", 0) * price_out) / 1_000_000
        before = self.spent
        self.store.set_cursor("openai:spent", f"{before + cost:.6f}")
        if before < self.warn_at <= before + cost:
            log.info("На OpenAI потрачено $%.2f из $%g", before + cost, self.budget)
        if before < self.budget <= before + cost:
            log.warning("Бюджет OpenAI $%g исчерпан — дальше на Claude", self.budget)
