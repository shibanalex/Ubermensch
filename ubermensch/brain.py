"""Вызов Claude через Claude Code в headless-режиме (работает на подписке пользователя)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings, find_claude
from .health import Health

log = logging.getLogger(__name__)

MCP_SERVER = "ubermensch"  # имя нашего сервера инструментов в mcp-конфиге


LIMIT_WORDS = ("limit", "лимит", "rate", "overloaded", "529")
AUTH_WORDS = ("login", "log in", "authenticat", "unauthorized", "401", "oauth", "invalid api key",
              "credential", "not logged", "expired", "disabled", "subscription access",
              "can't receive replies", "verification portal")
MISSING = "Claude Code не найден"
TIMEOUT = "нет ответа за"
RESET_AT = re.compile(r"(?i)resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")
# недельный лимит: «resets Oct 20, 10am»; старый формат Claude Code: «limit reached|1760000000»
RESET_DATE = re.compile(r"(?i)resets?\s+(?:on\s+)?([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(?:at\s+)?"
                        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")
RESET_EPOCH = re.compile(r"limit reached\|(\d{10})")
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
AUTH_PAUSE = timedelta(hours=12)  # после ошибки входа не дёргаем Claude каждую секунду
LIMIT_FALLBACK = timedelta(minutes=30)  # если время сброса лимита неизвестно


class BrainError(RuntimeError):
    @property
    def kind(self) -> str:
        """limit — лимит подписки, auth — слетел вход, missing — нет программы,
        timeout — не ответил вовремя, other — прочее."""
        text = str(self).lower()
        if any(word in text for word in LIMIT_WORDS):
            return "limit"
        if MISSING.lower() in text:
            return "missing"
        if any(word in text for word in AUTH_WORDS):
            return "auth"
        if TIMEOUT in text:
            return "timeout"
        return "other"

    @property
    def is_limit(self) -> bool:
        """Упёрлись в лимит подписки (или Claude перегружен) — подождать, а не сдаваться."""
        return self.kind == "limit"

    @property
    def reset_at(self) -> datetime | None:
        """Когда лимит сбросится. Claude Code пишет «resets 10pm (Europe/Moscow)», для недельного
        лимита — «resets Oct 20, 10am», в старых версиях — «limit reached|<unix-время>»."""
        text, now = str(self), datetime.now()
        if match := RESET_EPOCH.search(text):
            return datetime.fromtimestamp(int(match[1]))
        if (match := RESET_DATE.search(text)) and match[1].lower() in MONTHS:
            hour = _hour(int(match[3]), match[5])
            if hour is None:
                return None
            try:
                moment = datetime(now.year, MONTHS[match[1].lower()], int(match[2]), hour,
                                  int(match[4] or 0))
            except ValueError:
                return None
            return moment if moment > now - timedelta(days=1) else moment.replace(year=now.year + 1)
        match = RESET_AT.search(text)
        if not match:
            return None
        hour = _hour(int(match[1]), match[3])
        if hour is None:
            return None
        moment = now.replace(hour=hour, minute=int(match[2] or 0), second=0, microsecond=0)
        return moment if moment > now else moment + timedelta(days=1)

    def friendly(self) -> str:
        """Человеческое объяснение для пользователя: что случилось и что сделать."""
        kind = self.kind
        if kind == "limit":
            when = f" до {self.reset_at:%H:%M}" if self.reset_at else ""
            return f"Упёрлась в лимит подписки Claude{when}. Как сбросится — вернусь."
        if kind == "auth":
            return ("Claude разлогинился. Открой терминал, запусти claude и выполни /login "
                    "(или войди в расширении Claude Code в VS Code) — дальше подхвачу сама.")
        if kind == "missing":
            return ("Не нахожу Claude Code. Установи его (claude.ai/code) или обнови расширение "
                    "в VS Code — я найду его сам.")
        if kind == "timeout":
            return "Claude думал слишком долго и не ответил. Попробуй ещё раз."
        return f"Не получилось достучаться до Claude: {self}"


def _hour(hour: int, half: str | None) -> int | None:
    """12-часовое время («10pm») → 24-часовое; None, если час невозможный."""
    half = (half or "").lower()
    if half == "pm" and hour != 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    return hour if hour <= 23 else None


@dataclass(frozen=True)
class Answer:
    data: dict
    duration_ms: int


class Brain:
    def __init__(self, settings: Settings, health: Health | None = None):
        self.settings = settings
        self.health = health or Health()
        self.claude_bin = settings.claude_bin
        self._blocked_until: datetime | None = None  # лимит или слетевший вход
        self._blocked_by: BrainError | None = None
        self._auth_failures = 0  # подряд; первая после пробуждения часто ложная — сеть не поднялась

    def _resolve_binary(self) -> str:
        """Путь к claude меняется при обновлении расширения VS Code — ищем заново."""
        if not self.claude_bin or not Path(self.claude_bin).exists():
            try:
                self.claude_bin = find_claude()
            except RuntimeError:
                raise BrainError(MISSING) from None
        return self.claude_bin

    async def ask(self, prompt: str, **kwargs) -> Answer:
        """Вызов Claude с учётом здоровья: пока висит лимит — сразу отказ, без лишних вызовов."""
        if self._blocked_until and datetime.now() < self._blocked_until and self._blocked_by:
            raise self._blocked_by
        try:
            answer = await self._ask(prompt, **kwargs)
        except BrainError as e:
            if e.kind in ("limit", "auth", "missing"):
                pause = (e.reset_at - datetime.now() + timedelta(minutes=1) if e.reset_at
                         else LIMIT_FALLBACK if e.kind == "limit" else AUTH_PAUSE)
                self._blocked_until, self._blocked_by = datetime.now() + pause, e
                self._auth_failures = self._auth_failures + 1 if e.kind == "auth" else 0
                if e.kind != "auth" or self._auth_failures > 1:
                    self.health.report("brain", e.friendly(), quiet=True)
            raise
        self._blocked_until = self._blocked_by = None
        self._auth_failures = 0
        self.health.clear("brain")
        return answer

    @property
    def blocked_until(self) -> datetime | None:
        """До когда подписка недоступна (лимит, вход); None — доступна."""
        if self._blocked_until and datetime.now() < self._blocked_until:
            return self._blocked_until
        return None

    async def wait_out(self, error: BrainError) -> bool:
        """Для фоновой работы: ждать, пока Claude снова доступен (лимит — до сброса, вход и
        программа — проверка раз в 5 минут). False — ошибка не из тех, что проходят сами."""
        if error.kind not in ("limit", "auth", "missing"):
            return False
        until = self._blocked_until or datetime.now() + AUTH_PAUSE
        log.info("Фоновая работа ждёт Claude до %s (%s)", f"{until:%d.%m %H:%M}", error.kind)
        await asyncio.sleep(max(30.0, (until - datetime.now()).total_seconds()))
        return True

    @staticmethod
    async def _terminate(proc) -> None:
        """Останавливаем и Claude, и его MCP-процессы до передачи запроса другому ИИ."""
        if proc.returncode is not None:
            return
        try:  # Claude запущен в своей группе процессов — гасим её целиком
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()

    async def _ask(self, prompt: str, *, system_prompt_file: Path, schema: dict,
                   tools: tuple[str, ...] = (), model: str | None = None,
                   effort: str | None = None, mcp_config: Path | None = None,
                   read_dirs: tuple[Path, ...] = (), timeout: int | None = None,
                   agents: str | None = None) -> Answer:
        """Один изолированный вызов: без сохранения сессии и без чужих MCP-серверов.

        tools — встроенные инструменты Claude Code (только безопасные), mcp_config — наш
        сервер инструментов (ubermensch.tools); всё остальное запрещено (dontAsk).
        Промпт идёт через stdin (длинный контекст не влезает в аргументы командной строки),
        ответ — JSON по схеме.
        """
        if agents:  # помощники (runtime/models.py): основная модель вызывает их инструментом Agent
            tools = (*tools, "Agent")
        args = [
            self._resolve_binary(), "-p",
            "--model", model or self.settings.claude_model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--system-prompt-file", str(system_prompt_file),
            "--tools", ",".join(tools),
            "--permission-mode", "dontAsk",
            "--permission-prompts", "none",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        allowed = list(tools)
        if mcp_config:
            args += ["--mcp-config", str(mcp_config)]
            allowed.append(f"mcp__{MCP_SERVER}")  # наши инструменты сами спрашивают «да/нет»
        if allowed:
            args += ["--allowedTools", ",".join(allowed)]
        if effort:
            args += ["--effort", effort]
        if agents:
            args += ["--agents", agents]
        for folder in read_dirs:
            args += ["--add-dir", str(folder)]
        limit = timeout or self.settings.claude_timeout

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.settings.workspace,
                start_new_session=True,  # своя группа: при отмене гасим Claude вместе с MCP
                # инструмент может ждать «да/нет» пользователя до 10 минут
                env={**os.environ, "MCP_TOOL_TIMEOUT": str(limit * 1000)},
            )
        except FileNotFoundError:
            self.claude_bin = ""  # в следующий раз поищем заново
            raise BrainError(MISSING) from None
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), limit)
        except TimeoutError:
            await self._terminate(proc)
            raise BrainError(f"нет ответа за {limit} с") from None
        except asyncio.CancelledError:  # вызов отменили (сторож чата) — процесс не оставляем
            await self._terminate(proc)
            raise

        try:
            result = json.loads(out.decode("utf-8"))
        except ValueError:
            log.error("Claude rc=%s stderr=%s", proc.returncode, err.decode("utf-8", "replace")[:2000])
            raise BrainError(f"непонятный ответ Claude Code (код {proc.returncode})") from None

        if result.get("is_error") or result.get("subtype") != "success":
            raise BrainError(str(result.get("result") or result.get("subtype"))[:500])

        data = result.get("structured_output")
        if data is None:
            try:
                data = json.loads(result.get("result") or "")
            except ValueError:
                raise BrainError("ответ не соответствует схеме") from None

        log.info("Claude: %d мс, ходов %s", result.get("duration_ms", 0), result.get("num_turns"))
        return Answer(data=data, duration_ms=int(result.get("duration_ms", 0)))
