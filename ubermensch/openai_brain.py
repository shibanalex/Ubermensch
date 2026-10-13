"""Второй ИИ — OpenAI API (платно, по токенам): фоновый разбор без личного и запасной ответ
в чате, когда Claude в лимите или завис.

Тот же интерфейс, что у Brain (ask → Answer по JSON-схеме), но без инструментов и действий.
Две модели: дешёвая для фона (много данных) и посильнее для запасного ответа в чате (ответов
мало, качество важнее). Флагман (GPT-6 Astra) на бюджет в $10 не рассчитан: весь Takeout
обошёлся бы в ~$58.
Каждый вызов считается в долларах по токенам; на OPENAI_WARN_USD — предупреждение, на
OPENAI_BUDGET_USD — стоп, всё возвращается на Claude. Прокси Windows учитывается (trust_env).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from .brain import Answer, BrainError
from .health import Health
from .store import Store

log = logging.getLogger(__name__)

API = "https://api.openai.com/v1"
# Первая доступная по порядку (список моделей аккаунта — GET /models).
PREFERRED = {
    "background": ("gpt-6-luna", "gpt-6.1-luna", "gpt-5.6-luna", "gpt-5-mini", "gpt-4.1-mini",
                   "gpt-4o-mini"),
    "chat": ("gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-terra", "gpt-5", "gpt-4.1", "gpt-5-mini",
             "gpt-4o-mini"),
}
MODEL_ENV = {"background": "OPENAI_MODEL", "chat": "OPENAI_CHAT_MODEL"}
EFFORT = {"background": "low", "chat": "medium"}
# $ за миллион токенов (вход, выход); по самому длинному совпадению начала имени.
# Неизвестная модель считается по цене флагмана — бюджет не уйдёт в минус незаметно.
PRICES = {"gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6-sol": (2.0, 10.0),
          "gpt-6-luna": (0.1, 0.5), "gpt-6.1-luna": (0.1, 0.5), "gpt-5.6-terra": (2.0, 12.0),
          "gpt-5.6-luna": (0.2, 1.2), "gpt-5-mini": (0.25, 2.0), "gpt-5-nano": (0.05, 0.4),
          "gpt-5": (1.25, 10.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4),
          "gpt-4.1": (2.0, 8.0), "gpt-4o-mini": (0.15, 0.6)}
UNKNOWN_PRICE = (10.0, 50.0)
TIMEOUT = 240
PAUSE_AFTER_FAILURE = timedelta(hours=1)  # ключ, баланс или регион — не долбить каждую минуту


class OpenAIBrain:
    def __init__(self, store: Store, health: Health, notify):
        self.store, self.health, self.notify = store, health, notify
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.budget = float(os.getenv("OPENAI_BUDGET_USD", "9"))
        self.warn_at = float(os.getenv("OPENAI_WARN_USD", "7"))
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
            raise BrainError(f"OpenAI недоступен: {e.__class__.__name__}") from None

    def _fail(self, status: int, error: dict) -> None:
        code, message = error.get("code") or "", error.get("message") or ""
        if status == 401:
            text = "OpenAI не принял ключ — проверь OPENAI_API_KEY в .env."
        elif code == "insufficient_quota":
            text = "На балансе OpenAI кончились деньги — пополни баланс, пока работаю на Claude."
        elif code == "unsupported_country_region_territory" or status == 403:
            text = f"OpenAI отказал в доступе ({status}): {message[:150]}"
        elif status == 429:
            raise BrainError("OpenAI: слишком много запросов, попробую позже")
        else:
            raise BrainError(f"OpenAI: ошибка {status} {code} {message[:200]}")
        self._blocked_until = datetime.now() + PAUSE_AFTER_FAILURE
        self.health.report("openai", text)
        raise BrainError(text)

    async def _pick_model(self, session: aiohttp.ClientSession, role: str) -> str:
        """Модель для роли: из .env или первая доступная из PREFERRED; запоминается."""
        if self._models[role]:
            return self._models[role]
        available = {m["id"] for m in (await self._request(session, "GET", "/models"))["data"]}
        model = next((m for m in PREFERRED[role] if m in available), None)
        if not model:
            raise BrainError("OpenAI: нет ни одной из моделей " + ", ".join(PREFERRED[role]))
        self._models[role] = model
        self.store.set_cursor(f"openai:model:{role}", model)
        log.info("OpenAI: для «%s» — модель %s", role, model)
        return model

    async def ask(self, prompt: str, *, system_prompt_file: Path, schema: dict,
                  role: str = "background", **_ignored) -> Answer:
        """Как Brain.ask: ответ JSON по схеме. role — background (дешёвая модель) или chat
        (посильнее). Инструментов нет — лишние параметры игнорируются."""
        if not self.key:
            raise BrainError("OpenAI не подключён")
        if self.spent >= self.budget:
            raise BrainError(f"Бюджет OpenAI (${self.budget:g}) исчерпан")
        if self._blocked_until and datetime.now() < self._blocked_until:
            raise BrainError("OpenAI временно недоступен")
        started = datetime.now()
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            model = await self._pick_model(session, role)
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt_file.read_text(encoding="utf-8")},
                    {"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "answer", "schema": schema, "strict": False}},
            }
            if model.startswith(("gpt-5", "gpt-6", "o")):  # модели с рассуждением
                body["reasoning_effort"] = EFFORT[role]  # фон — «low»: дешевле и быстрее
            data = await self._request(session, "POST", "/chat/completions", body)
        self._charge(model, data.get("usage") or {})
        try:
            answer = json.loads(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, ValueError):
            raise BrainError("OpenAI: ответ не по схеме") from None
        self.health.clear("openai", "OpenAI снова работает.")
        ms = int((datetime.now() - started).total_seconds() * 1000)
        log.info("OpenAI %s: %d мс, потрачено всего $%.3f", model, ms, self.spent)
        return Answer(data=answer, duration_ms=ms)

    def _charge(self, model: str, usage: dict) -> None:
        matches = [name for name in PRICES if model.startswith(name)]
        price_in, price_out = PRICES[max(matches, key=len)] if matches else UNKNOWN_PRICE
        cost = (usage.get("prompt_tokens", 0) * price_in
                + usage.get("completion_tokens", 0) * price_out) / 1_000_000
        before = self.spent
        self.store.set_cursor("openai:spent", f"{before + cost:.6f}")
        if before < self.warn_at <= before + cost:
            self.notify(f"💸 На OpenAI потрачено ${before + cost:.2f} из ${self.budget:g}. "
                        f"На ${self.budget:g} остановлюсь и всё верну на Claude.")
        if before < self.budget <= before + cost:
            self.notify(f"💸 Бюджет OpenAI ${self.budget:g} исчерпан — дальше всё на Claude. "
                        "Пополнишь — подними OPENAI_BUDGET_USD в .env.")
