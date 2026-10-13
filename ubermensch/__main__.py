"""Запуск ядра: python -m ubermensch (API для приложения, Telegram, напоминания, расследование)."""
from __future__ import annotations

import asyncio
import logging
import msvcrt
import os
import sys
import time
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

from . import api, bot, config
from .analysis import Investigation
from .assistant import Assistant
from .brain import Brain
from .openai_brain import OpenAIBrain
from .collectors.activity import ActivityTracker
from .core import Core
from .maintenance import Maintenance
from .observer import ScreenObserver
from .store import Store


def _setup_logging(settings: config.Settings) -> None:
    handlers: list[logging.Handler] = [RotatingFileHandler(
        settings.logs_dir / "ubermensch.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8",
    )]
    if sys.stderr:  # под pythonw консоли нет
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # предупреждения о шрифтах в PDF — шум
    logging.getLogger("telethon").setLevel(logging.WARNING)


def _single_instance(settings: config.Settings):
    """Два экземпляра бота с одним токеном мешают друг другу получать сообщения."""
    lock = open(settings.data_dir / "ubermensch.lock", "w")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.exit("Übermensch уже запущен.")
    return lock


async def _serve(settings: config.Settings) -> None:
    store = Store(settings.db_path)
    brain = Brain(settings)
    assistant = Assistant(settings, store, brain)
    core = Core(store, assistant, settings.data_dir / "raw" / "import")
    brain.health = core.health  # мозг сообщает о лимите и слетевшем входе
    if os.getenv("OPENAI_API_KEY"):  # второй ИИ: фон без личного и запасной ответ в чате
        assistant.helper = OpenAIBrain(store, core.health, core.notify)
    assistant.waiting_for_user = lambda: bool(core._approvals)
    core.health.bind(asyncio.get_running_loop())
    core.investigation = Investigation(store, assistant, core.notify, core.telegrams, core.health)

    # Фоновые сервисы: упавший перезапускается надсмотрщиком, ядро продолжает работать.
    services = core.supervisor
    services.add("напоминания", core.reminder_loop)
    services.add("импорт документов", core.import_loop)
    services.add("трекер активности", ActivityTracker(store).run)
    services.add("сбор данных", core.investigation.collect_loop)
    services.add("разбор данных", core.investigation.worker)
    services.add("взгляд на экран", ScreenObserver(core).run)
    services.add("обслуживание", Maintenance(settings.db_path, core.health).run)

    runner = await api.start(core, config.api_token(create=True), config.API_HOST, config.API_PORT)
    services.start()
    try:
        await bot.run(settings, core)
    finally:
        await services.stop()
        await runner.cleanup()


def _data_available() -> bool:
    try:
        return (config.DATA_DIR / "db").is_dir() and bool(os.listdir(config.DATA_DIR))
    except OSError:  # диск заблокирован BitLocker или ещё не подключён
        return False


def _wait_for_data() -> None:
    """После перезагрузки D: заблокирован, пока его не откроют паролем — ждём, а не падаем."""
    notified = False
    while not _data_available():
        if not notified and os.getenv("TG_BOT_TOKEN") and os.getenv("TG_CHAT_ID"):
            text = (f"🔒 Диск с данными ({config.DATA_DIR.drive}) заблокирован. Открой его в "
                    "Проводнике и введи пароль BitLocker — я запущусь сам.")
            body = urllib.parse.urlencode({"chat_id": os.environ["TG_CHAT_ID"], "text": text})
            try:
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage",
                    data=body.encode(), timeout=15)
                notified = True
            except OSError:
                pass  # сети пока нет — попробуем на следующем круге
        time.sleep(30)


def main() -> None:
    _wait_for_data()
    settings = config.load()
    _setup_logging(settings)
    lock = _single_instance(settings)  # noqa: F841 — держим файл открытым до выхода
    logging.info("Старт. Данные: %s, Claude: %s", settings.data_dir, settings.claude_bin)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
