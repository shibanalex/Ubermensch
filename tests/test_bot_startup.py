"""Ядро не падает, если Telegram недоступен при старте (после сна сеть поднимается не сразу)."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ubermensch import bot


class BotStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_network_at_start_does_not_stop_polling(self):
        fake = MagicMock()
        fake.set_my_commands = AsyncMock(side_effect=[ConnectionError("no DNS"), None])
        fake.session.close = AsyncMock()
        polled = asyncio.Event()

        async def poll(*args, **kwargs):
            await asyncio.sleep(0.2)  # тем временем меню команд пробуется повторно
            polled.set()

        dispatcher = SimpleNamespace(start_polling=poll, resolve_used_update_types=lambda: [])
        with patch.object(bot, "Bot", return_value=fake), \
                patch.object(bot, "create_dispatcher", return_value=dispatcher), \
                patch.object(bot, "forward_events", AsyncMock()), \
                patch.object(bot, "COMMANDS_RETRY", 0.01):
            await bot.run(SimpleNamespace(tg_token="1:x", tg_chat_id=1), SimpleNamespace())
        self.assertTrue(polled.is_set())
        self.assertEqual(fake.set_my_commands.await_count, 2)
        fake.session.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
