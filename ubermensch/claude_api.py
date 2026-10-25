"""Claude API (платно, по токенам, CLAUDE_AI_API_KEY): дешёвый фон и запасной ответ в чате.

Подписка Claude Code — главный мозг; её лимит общий для чата, разбора и профиля. Сюда уходит
то, что может идти мимо подписки:
- background — heartbeat и мелкие фоновые решения: Claude Haiku 5.5, effort low;
- chat — запасной ответ, когда подписка в лимите: Claude Sonnet 5.5, effort medium
  (с серверным запасным вариантом при отказе классификатора — fallbacks="default").
Тот же интерфейс, что у Brain/OpenAIBrain: ask → Answer по JSON-схеме (output_config.format),
инструменты — общие, через ToolGateway (те же подтверждения). Каждый вызов считается в долларах:
на CLAUDE_API_WARN_USD — предупреждение, на CLAUDE_API_BUDGET_USD — стоп.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

from .brain import Answer, BrainError
from .health import Health
from .runtime import images
from .runtime.http_tools import ToolExecutionUncertain
from .runtime.tools import ToolGateway
from .store import Store

log = logging.getLogger(__name__)

MODELS = {"background": "claude-haiku-5-5", "chat": "claude-sonnet-5-5"}
EFFORT = {"background": "low", "chat": "medium"}
# $ за 1 млн токенов: вход, выход, чтение кеша, запись кеша (запись — 1.25× входа)
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
          "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
          "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00)}
UNKNOWN_PRICE = (4.00, 20.00, 0.40, 5.00)  # запасной ответ сервера — по худшей из цен
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 12
MAX_TOOL_RESULT = 60000
TIMEOUT = 240
PAUSE_AFTER_FAILURE = timedelta(hours=1)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# Структурированный вывод API не принимает числовые и строковые границы в схеме — снимаем их
# (значения и так проверяются при применении ответа).
UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength"}


# Свободных объектов API тоже не принимает: каждый объект — с явным списком полей. Такой в схеме
# ответа один — params действия (напоминание: at, text); новое действие — добавить его поля сюда.
ACTION_PARAMS = {"type": "object", "additionalProperties": False,
                 "properties": {"at": {"type": "string"}, "text": {"type": "string"}}}


def api_schema(schema):
    """Копия JSON Schema в виде, который принимает output_config.format: без числовых и
    строковых границ, у каждого объекта — additionalProperties: false, свободный объект
    (params действия) — с перечисленными полями."""
    if isinstance(schema, list):
        return [api_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    if schema.get("type") == "object" and not schema.get("properties"):
        return dict(ACTION_PARAMS)
    result = {k: api_schema(v) for k, v in schema.items() if k not in UNSUPPORTED}
    if result.get("type") == "object":
        result["additionalProperties"] = False
    return result


class ClaudeApiError(BrainError):
    def friendly(self) -> str:
        return str(self)


class ClaudeApiBrain:
    def __init__(self, store: Store, health: Health, notify):
        self.store, self.health, self.notify = store, health, notify
        key = os.getenv("CLAUDE_AI_API_KEY", "").strip()
        self.client = anthropic.AsyncAnthropic(api_key=key, timeout=TIMEOUT) if key else None
        self.budget = float(os.getenv("CLAUDE_API_BUDGET_USD", "10"))
        self.warn_at = float(os.getenv("CLAUDE_API_WARN_USD", "8"))
        self._blocked_until: datetime | None = None

    # --- состояние ---

    @property
    def spent(self) -> float:
        return float(self.store.cursor("claude_api:spent") or 0)

    @property
    def usable(self) -> bool:
        return self.client is not None and self.spent < self.budget and not (
            self._blocked_until and datetime.now() < self._blocked_until)

    def status(self) -> dict | None:
        if self.client is None:
            return None
        return {"spent": round(self.spent, 3), "budget": self.budget,
                "model": " / ".join(MODELS.values())}

    # --- вызов ---

    async def ask(self, prompt: str, *, system_prompt_file: Path, schema: dict,
                  role: str = "background", tool_gateway: ToolGateway | None = None,
                  timeout: int | None = None, **_ignored) -> Answer:
        """Как Brain.ask: ответ JSON по схеме. Инструменты — только явно выданные."""
        if role not in MODELS:
            raise ClaudeApiError(f"Claude API: неизвестная роль {role}")
        if not self.usable:
            raise ClaudeApiError("Claude API недоступен: нет ключа, бюджет исчерпан или пауза")
        model = MODELS[role]
        request = {
            "model": model, "max_tokens": MAX_TOKENS,
            # системный промпт стабилен — кешируется; инструменты идут раньше и кешируются с ним
            "system": [{"type": "text", "text": system_prompt_file.read_text(encoding="utf-8"),
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": EFFORT[role],
                              "format": {"type": "json_schema", "schema": api_schema(schema)}},
        }
        if tool_gateway is not None and (definitions := tool_gateway.definitions()):
            request["tools"] = [{"name": t.name, "description": t.description,
                                 "input_schema": t.parameters} for t in definitions]
        started = datetime.now()
        try:
            async with asyncio.timeout(timeout or (900 if tool_gateway else TIMEOUT)):
                answer = await self._conversation(request, tool_gateway)
        except TimeoutError:
            raise ClaudeApiError("Claude API: истекло время; начатые действия не повторяются") from None
        self.health.clear("claude_api", "Claude API снова работает.")
        ms = int((datetime.now() - started).total_seconds() * 1000)
        log.info("Claude API %s: %d мс, потрачено всего $%.3f", model, ms, self.spent)
        return Answer(data=answer, duration_ms=ms)

    async def _create(self, request: dict):
        try:
            if request["model"] == MODELS["chat"]:  # отказ классификатора — серверный запасной
                return await self.client.beta.messages.create(
                    **request, betas=[FALLBACK_BETA], fallbacks="default")
            return await self.client.messages.create(**request)
        except anthropic.AuthenticationError:
            self._block("Claude API не принял ключ — проверь CLAUDE_AI_API_KEY в .env.")
        except anthropic.PermissionDeniedError as e:
            self._block(f"Claude API отказал в доступе: {e.message[:150]}")
        except anthropic.BadRequestError as e:
            if "credit" in e.message.lower() or "balance" in e.message.lower():
                self._block("На балансе Claude API кончились деньги — пополни, пока работаю на подписке.")
            raise ClaudeApiError(f"Claude API: неверный запрос — {e.message[:200]}") from None
        except anthropic.RateLimitError:
            raise ClaudeApiError("Claude API: слишком много запросов, попробую позже") from None
        except anthropic.APIStatusError as e:
            raise ClaudeApiError(f"Claude API: ошибка {e.status_code}") from None
        except anthropic.APIConnectionError:
            raise ClaudeApiError("Claude API недоступен: нет связи") from None

    def _block(self, text: str):
        self._blocked_until = datetime.now() + PAUSE_AFTER_FAILURE
        self.health.report("claude_api", text)
        raise ClaudeApiError(text)

    async def _conversation(self, request: dict, gateway: ToolGateway | None) -> dict:
        allowed = {tool["name"] for tool in request.get("tools", [])}
        for turn in range(MAX_TOOL_ROUNDS + 1):
            if not self.usable:
                raise ClaudeApiError("Claude API: бюджет исчерпан, следующие шаги остановлены")
            response = await self._create(request)
            self._charge(getattr(response, "model", None) or request["model"], response.usage)
            if response.stop_reason == "refusal":
                raise ClaudeApiError("Claude API отказался отвечать на этот запрос")
            if response.stop_reason == "max_tokens":
                raise ClaudeApiError("Claude API: ответ не уместился")
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                text = next((b.text for b in response.content if b.type == "text"), "")
                try:
                    answer = json.loads(text)
                except ValueError:
                    raise ClaudeApiError("Claude API: ответ не по схеме") from None
                if not isinstance(answer, dict):
                    raise ClaudeApiError("Claude API: ответ не по схеме")
                return answer
            if gateway is None or turn == MAX_TOOL_ROUNDS:
                raise ClaudeApiError("Claude API: превышен лимит вызовов инструментов")
            # весь ответ целиком (с блоками рассуждений) — так модель продолжит с того же места
            request["messages"].append({"role": "assistant", "content": response.content})
            results = []
            for call in calls:
                try:
                    if call.name not in allowed:
                        raise ValueError("Инструмент не выдан этому запросу")
                    result = await gateway.call(call.name, dict(call.input))
                    content = json.dumps(result, ensure_ascii=False)
                    if len(content) > MAX_TOOL_RESULT:
                        content = content[:MAX_TOOL_RESULT] + " …(обрезано)"
                    if image := images.attached(result):  # снимок экрана — модели самой картинкой
                        content = [{"type": "text", "text": content},
                                   {"type": "image", "source": {"type": "base64", "media_type": image[0],
                                                                "data": image[1]}}]
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
                except ToolExecutionUncertain:
                    raise
                except Exception as error:
                    log.warning("Ошибка инструмента Claude API: %s", type(error).__name__)
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": str(error)[:1500], "is_error": True})
            request["messages"].append({"role": "user", "content": results})
        raise ClaudeApiError("Claude API: превышен лимит шагов")

    def _charge(self, model: str, usage) -> None:
        price_in, price_out, price_read, price_write = next(
            (price for name, price in PRICES.items() if model.startswith(name)), UNKNOWN_PRICE)
        cost = ((usage.input_tokens or 0) * price_in + (usage.output_tokens or 0) * price_out
                + (getattr(usage, "cache_read_input_tokens", 0) or 0) * price_read
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * price_write) / 1_000_000
        before = self.spent
        self.store.set_cursor("claude_api:spent", f"{before + cost:.6f}")
        if before < self.warn_at <= before + cost:
            self.notify(f"💸 На Claude API потрачено ${before + cost:.2f} из ${self.budget:g}. "
                        f"На ${self.budget:g} остановлюсь — дальше всё на подписке.")
        if before < self.budget <= before + cost:
            self.notify(f"💸 Бюджет Claude API ${self.budget:g} исчерпан — дальше всё на подписке. "
                        "Пополнишь — подними CLAUDE_API_BUDGET_USD в .env.")
