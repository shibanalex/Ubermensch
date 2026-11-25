"""Запуск ядра: python -m ubermensch (API для приложения, Telegram, напоминания, расследование)."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

from . import agent, api, bot, config
from .analysis import Investigation
from .assistant import Assistant
from .brain import Brain
from .claude_api import ClaudeApiBrain
from .openai_brain import OpenAIBrain
from .collectors import listening, notifications
from .collectors.activity import ActivityTracker
from .core import Core
from .maintenance import Maintenance
from .observer import ScreenObserver
from .agent import Autonomy
from .interview import Interviewer
from .profiling import Profiler
from .store import Store
from .system import session


def _setup_logging(settings: config.Settings) -> None:
    handlers: list[logging.Handler] = [RotatingFileHandler(
        settings.logs_dir / "ubermensch.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8",
    )]
    if sys.stderr:  # под pythonw консоли нет
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # предупреждения о шрифтах в PDF: шум
    logging.getLogger("telethon").setLevel(logging.WARNING)


def _single_instance(settings: config.Settings):
    """Два экземпляра бота с одним токеном мешают друг другу получать сообщения."""
    lock = open(settings.data_dir / "ubermensch.lock", "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("Übermensch уже запущен.")
    return lock


async def _serve(settings: config.Settings) -> None:
    store = Store(settings.db_path)
    brain = Brain(settings)
    assistant = Assistant(settings, store, brain)
    core = Core(store, assistant, settings.data_dir / "raw" / "import")
    brain.health = core.health  # мозг сообщает о лимите и слетевшем входе
    if os.getenv("CLAUDE_AI_API_KEY"):  # Claude API: heartbeat (Haiku) и запасной ответ (Sonnet)
        assistant.claude_api = ClaudeApiBrain(store, core.health, core.notify)
    if os.getenv("OPENAI_API_KEY"):  # второй ИИ: фон без личного и запасной ответ в чате
        assistant.helper = OpenAIBrain(store, core.health, core.notify)
    assistant.waiting_for_user = lambda: bool(core._approvals)
    core.health.bind(asyncio.get_running_loop())
    core.investigation = Investigation(store, assistant, core.notify, core.telegrams, core.health)

    async def login_required(account: str) -> None:  # сессии нет: ассистент сама просит код
        if core.login is None:
            core.notify(await core.start_tg_login(account))

    core.investigation.on_login_required = login_required

    # Фоновые сервисы: упавший перезапускается надсмотрщиком, ядро продолжает работать.
    services = core.supervisor
    services.add("напоминания", core.reminder_loop)
    services.add("окно в мир", core.world_loop)
    services.add("импорт документов", core.import_loop)
    services.add("трекер активности", ActivityTracker(store).run)
    services.add("сбор данных", core.investigation.collect_loop)
    services.add("почта", core.investigation.mail_loop)
    services.add("разбор данных", core.investigation.worker)
    services.add("взгляд на экран", ScreenObserver(core).run)
    services.add("обслуживание", Maintenance(settings.db_path, core.health).run)
    core.profiler = Profiler(settings, store, assistant, core.investigation, core.notify)
    services.add("профиль", core.profiler.run)
    core.interviewer = Interviewer(store, core.notify)
    services.add("интервью", core.interviewer.run)
    agent.recover()
    core.autonomy = Autonomy(store, assistant, core.investigation, core)
    services.add("самостоятельность", core.autonomy.run)

    runner = await api.start(core, config.api_token(create=True), config.API_HOST, config.API_PORT)
    core.execution.recover()
    core.jobs.recover()
    services.start()
    try:
        await bot.run(settings, core)
    finally:
        core.jobs.stopping = True
        core.execution.stopping = True
        await services.stop()
        await core.jobs.close()
        await core.execution.close()
        await runner.cleanup()


def _data_available() -> bool:
    try:
        return (config.DATA_DIR / "db").is_dir() and bool(os.listdir(config.DATA_DIR))
    except OSError:  # диск с данными ещё не подключён или не расшифрован
        return False


def _wait_for_data() -> None:
    """Папки с данными нет (диск не подключён): ждём, а не падаем."""
    notified = False
    while not _data_available():
        if not notified and os.getenv("TG_BOT_TOKEN") and os.getenv("TG_CHAT_ID"):
            text = (f"🔒 Не вижу папку с данными ({config.DATA_DIR}). Подключи диск — "
                    "я запущусь сама.")
            body = urllib.parse.urlencode({"chat_id": os.environ["TG_CHAT_ID"], "text": text})
            try:
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage",
                    data=body.encode(), timeout=15)
                notified = True
            except OSError:
                pass  # сети пока нет: попробуем на следующем круге
        time.sleep(30)


def main() -> None:
    _wait_for_data()
    settings = config.load()
    _setup_logging(settings)
    lock = _single_instance(settings)  # noqa: F841; держим файл открытым до выхода
    session.start(notifications.FOLDER, listening.FOLDER)  # окно, простой, уведомления, музыка
    logging.info("Старт. Данные: %s, Claude: %s", settings.data_dir, settings.claude_bin)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
