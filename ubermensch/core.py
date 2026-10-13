"""Ядро: единая точка для всех клиентов (Telegram, приложение на ПК, позже телефон).

Клиенты вызывают методы Core, а об изменениях узнают из шины событий:
  {"type": "thinking", "on": bool}   — мозг думает (маскот показывает это)
  {"type": "changed"}                — данные изменились, перечитать состояние
  {"type": "reminder", "text": str}  — сработало напоминание
  {"type": "celebrate"}              — выполнена задача
  {"type": "notify", "text": str}    — сообщение от системы (например, досье готово)
  {"type": "approval", "id", "title", "details"} — действие ждёт «да/нет» пользователя
  {"type": "say", "text": str}       — реплика маскота (например, комментарий к экрану)
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import actions, config, ingest, mail, metrics
from .analysis import SOURCES
from .assistant import ONBOARDING_EVENT, Assistant, Outcome
from .brain import BrainError
from .collectors.telegram import PROFILES, TelegramAccount
from .health import Health
from .services import Supervisor
from .store import Store

if TYPE_CHECKING:
    from .analysis import Investigation

log = logging.getLogger(__name__)

APPROVAL_TIMEOUT = 10 * 60  # секунд ждём «да/нет», потом считаем отказом


class Bus:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Подписчик не успевает читать события, событие пропущено")


class Core:
    def __init__(self, store: Store, assistant: Assistant, import_dir: Path):
        self.store = store
        self.assistant = assistant
        self.import_dir = import_dir
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self.bus = Bus()
        self.investigation: Investigation  # подключается при запуске, до старта API
        self.health = Health(self.notify)  # известные проблемы и что с ними делать
        self.supervisor = Supervisor(self.health)  # фоновые сервисы с перезапуском
        # один клиент на сессию: и для сбора, и для действий; личный и рабочий аккаунты
        self.telegrams = {key: TelegramAccount(profile) for key, profile in PROFILES.items()}
        self.telegram = self.telegrams["personal"]
        self._brain_calls = 0  # счётчик, а не флаг: вызовы могут стоять в очереди друг за другом
        self._approvals: dict[int, asyncio.Future] = {}  # действия, ждущие «да/нет»

    @property
    def thinking(self) -> bool:
        return self._brain_calls > 0

    @contextlib.contextmanager
    def _brain_busy(self):
        self._brain_calls += 1
        self.bus.publish({"type": "thinking", "on": True})
        try:
            yield
        finally:
            self._brain_calls -= 1
            self.bus.publish({"type": "thinking", "on": self.thinking})
            self.bus.publish({"type": "changed"})

    async def chat(self, text: str, channel: str, *, from_user: bool = True) -> Outcome:
        self.investigation.touch()  # фоновый разбор притихнет и не съест лимит разговора
        with self._brain_busy():
            return await self.assistant.handle(text, channel=channel, from_user=from_user)

    def notify(self, text: str) -> None:
        """Сообщение от системы: появится у маскота и придёт в Telegram."""
        self.store.add_message("assistant", text, "system")
        self.bus.publish({"type": "notify", "text": text})
        self.bus.publish({"type": "changed"})

    def say(self, text: str) -> None:
        """Реплика маскота на ПК (пузырь), без Telegram и истории чата."""
        self.bus.publish({"type": "say", "text": text})

    def needs_onboarding(self) -> bool:
        return not self.store.goals() and not self.store.facts(1)

    async def onboard(self, channel: str) -> Outcome:
        return await self.chat(ONBOARDING_EVENT, channel, from_user=False)

    def decide(self, action_id: int, approve: bool) -> str | None:
        """None — решение по действию уже принято раньше (например, в другом клиенте)."""
        if not self.store.decide_action(action_id, "approved" if approve else "rejected"):
            return None
        if (waiting := self._approvals.get(action_id)) is not None:
            # действие ждёт внутри инструмента — отдаём решение, выполнит оно само
            if not waiting.done():
                waiting.set_result(approve)
            self.store.finish_action(action_id, "approved" if approve else "rejected", "")
            result = "✅ Подтверждено" if approve else "❌ Отменено"
        else:
            result = actions.execute(self.store, action_id) if approve else "❌ Отменено"
        self.bus.publish({"type": "changed"})
        return result

    async def request_approval(self, title: str, details: str = "") -> bool:
        """Спрашивает пользователя (Telegram + приложение) и ждёт ответа. Молчание — это «нет»."""
        action_id = self.store.add_action("approval", title, {"details": details}, "external")
        waiting = asyncio.get_running_loop().create_future()
        self._approvals[action_id] = waiting
        self.bus.publish({"type": "approval", "id": action_id, "title": title, "details": details})
        self.bus.publish({"type": "changed"})
        try:
            return await asyncio.wait_for(waiting, APPROVAL_TIMEOUT)
        except TimeoutError:
            self.store.decide_action(action_id, "rejected")
            self.store.finish_action(action_id, "rejected", "нет ответа")
            self.bus.publish({"type": "changed"})
            return False
        finally:
            self._approvals.pop(action_id, None)

    # --- действия от имени пользователя (всё внешнее — только после «да») ---

    def tg(self, account: str = "personal") -> TelegramAccount:
        if account not in self.telegrams:
            raise ValueError(f"Нет такого аккаунта Telegram: {account} (есть: personal, work)")
        if not self.telegrams[account].profile.configured:
            raise ValueError(f"{account}: аккаунт не настроен в .env")
        return self.telegrams[account]

    async def send_telegram(self, chat_id: int, text: str, account: str = "personal") -> str:
        tg = self.tg(account)
        name = await tg.chat_name(chat_id)
        where = "Telegram" if tg.profile.personal else f"{tg.profile.label} Telegram"
        if not await self.request_approval(f"Отправить в {where} → {name}", text):
            return "Пользователь не подтвердил отправку."
        await tg.send(chat_id, text)
        return f"Отправлено в «{name}» ({tg.profile.label} аккаунт)."

    async def send_email(self, to: str, subject: str, body: str) -> str:
        if not await self.request_approval(f"Отправить письмо → {to}",
                                           f"Тема: {subject}\n\n{body}"):
            return "Пользователь не подтвердил отправку."
        await asyncio.to_thread(mail.send, os.environ["GMAIL_ADDRESS"],
                                os.environ["GMAIL_PASSWORD"], to, subject, body)
        return f"Письмо для {to} отправлено."

    def add_task(self, title: str, due: str | None) -> int:
        task_id = self.store.add_task(title, "", due, None, "user")
        self.bus.publish({"type": "changed"})
        return task_id

    def set_task_status(self, task_id: int, status: str) -> bool:
        changed = self.store.set_task_status(task_id, status)
        if changed:
            if status == "done":
                self.bus.publish({"type": "celebrate"})
            self.bus.publish({"type": "changed"})
        return changed

    def state(self) -> dict:
        """Снимок для клиентов: всё, что нужно нарисовать на экране."""
        return {
            "thinking": self.thinking,
            "problems": self.health.problems(),
            "messages": [{**dict(m), "notes": json.loads(m["notes"] or "[]")}
                         for m in self.store.recent_messages(60)],
            "tasks": [dict(t) for t in self.store.tasks()],
            "goals": [dict(g) for g in self.store.goals()],
            "reminders": [dict(r) for r in self.store.pending_reminders()],
            "approvals": [
                {"id": a["id"], "summary": a["summary"], "risk": a["risk"],
                 "details": json.loads(a["params"]).get("details", "")}
                for a in self.store.pending_actions()
            ],
        }

    async def import_pending(self, *, retry_failed: bool = False) -> list[str]:
        """Импортирует новые файлы из папки импорта. Каждый файл — один раз (по хэшу)."""
        results = []
        # архивы Google Takeout разбирает расследование (collectors/takeout.py), не этот импорт
        for path in sorted(p for p in self.import_dir.iterdir()
                           if p.is_file() and p.suffix.lower() != ".zip"):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            status = self.store.import_status(digest)
            # пропущенное (вдруг формат стал поддерживаться) и упавшее (лимит прошёл) — пробуем снова
            if status == "done":
                continue
            if ingest.is_aistudio(path):  # чаты с ИИ — глубоко: сведения живут и в ответах ИИ
                results.append(await self.deep_import(path, digest))
                continue
            text = ingest.read_document(path)
            if text is None:
                self.store.record_import(path.name, digest, "skipped",
                                         "неподдерживаемый формат или слишком большой файл")
                results.append(f"⏭ {path.name}: формат не поддерживается")
                continue
            log.info("Импорт %s (%d символов)", path.name, len(text))
            try:
                with self._brain_busy():
                    outcome = await self.assistant.ingest(path.name, text)
            except BrainError as e:
                log.error("Импорт %s не удался: %s", path.name, e)
                self.store.record_import(path.name, digest, "failed", str(e))
                results.append(f"⚠️ {path.name}: {e}")
                continue
            self.store.record_import(path.name, digest, "done", outcome.reply)
            results.append(f"📥 {path.name}: фактов и целей — {len(outcome.changes)}")
        return results

    def metrics(self) -> dict:
        """Окно метрик: как далеко Освальд изучил пользователя и что пользователь делает."""
        def mtime(name: str) -> str | None:
            path = self.assistant.workspace / name
            return (datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes")
                    if path.exists() else None)

        labels = {**SOURCES, "person": "Портреты отношений", "stats": "Статистика по месяцам"}
        learning = metrics.learning(self.store, self.investigation.status(), labels,
                                    mtime("oswald_persona.md"), mtime("tg_style.md"))
        if self.assistant.helper is not None:
            learning["openai"] = self.assistant.helper.status()
        return {"learning": learning,
                "activity": metrics.activity(self.store)}

    async def deep_import(self, path: Path, digest: str | None = None) -> str:
        """Чат с ИИ целиком: без повторов, только новое относительно памяти; сжатый конспект
        новых сведений — в Obsidian (Конспекты). Можно запускать повторно на уже импортированном."""
        digest = digest or hashlib.sha256(path.read_bytes()).hexdigest()
        paragraphs, dropped = ingest.dedup(ingest.aistudio_full(path))
        text = "\n\n".join(paragraphs)
        log.info("Глубокий импорт %s: %d символов, повторов выброшено %d", path.name, len(text),
                 dropped)
        try:
            with self._brain_busy():
                outcome, summary = await self.assistant.distill(path.name, text,
                                                                progress=f"distill:{digest}")
        except BrainError as e:
            log.error("Глубокий импорт %s не удался: %s", path.name, e)
            self.store.record_import(path.name, digest, "failed", str(e))
            return f"⚠️ {path.name}: {e.friendly()}"
        note = ""
        if summary:
            try:
                folder = config.OBSIDIAN_DIR / "Конспекты"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{path.stem}.md"
                if not summary.startswith("#"):  # заголовок модель обычно пишет сама
                    summary = f"# {path.stem}\n\n{summary}"
                target.write_text(summary + "\n", encoding="utf-8")
                note = f"\nКонспект: {target}"
            except OSError:
                log.warning("Не удалось положить конспект в Obsidian (%s)", config.OBSIDIAN_DIR)
        self.store.record_import(path.name, digest, "done", outcome.reply)
        self.store.delete_cursor(f"distill:{digest}")
        self.notify(f"📥 {outcome.reply}\nНового в памяти: {len(outcome.changes)}.{note}")
        return f"📥 {path.name}: изменений в памяти — {len(outcome.changes)}"

    async def import_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                await self.import_pending()
            except Exception:
                log.exception("Ошибка при импорте документов")
            await asyncio.sleep(300)

    async def reminder_loop(self) -> None:
        while True:
            try:
                for r in self.store.due_reminders():
                    self.store.close_reminder(r["id"], "sent")
                    self.bus.publish({"type": "reminder", "text": r["text"]})
                    self.bus.publish({"type": "changed"})
            except Exception:
                log.exception("Ошибка в цикле напоминаний")
            await asyncio.sleep(20)
