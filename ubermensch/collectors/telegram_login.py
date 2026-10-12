"""Первый вход в Telegram: python -m ubermensch.collectors.telegram_login

Telegram пришлёт код в приложение; если включён облачный пароль — спросит и его.
После входа ядро сразу начинает собирать переписки.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import urllib.request

from .. import config
from .telegram import SESSION, client


def _trigger_collection() -> None:
    """Просим ядро собрать данные сейчас, не дожидаясь планового запуска."""
    token = config.api_token()
    request = urllib.request.Request(
        f"http://{config.API_HOST}:{config.API_PORT}/api/collect", method="POST", data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10)
        print("Ядро начало собирать переписки.")
    except OSError:
        print("Ядро сейчас не запущено — сбор начнётся при следующем старте.")


async def main() -> None:
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    tg = client()
    await tg.start(
        phone=lambda: input("Номер телефона в формате +7...: ").strip(),
        code_callback=lambda: input("Код из Telegram: ").strip(),
        password=lambda: getpass.getpass("Облачный пароль Telegram (ввод скрыт): "),
    )
    me = await tg.get_me()
    print(f"\nГотово: вход выполнен как {me.first_name} (@{me.username}).")
    print(f"Сессия сохранена в {SESSION}.session — это ключ к аккаунту, никому его не передавай.")
    await tg.disconnect()
    _trigger_collection()


if __name__ == "__main__":
    asyncio.run(main())
    input("\nНажми Enter, чтобы закрыть окно.")
