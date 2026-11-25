"""Оценки 👍/👎 под ответами в Telegram: привязаны к ответу, 👎 спрашивает «что не так», рефлексия их видит."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ubermensch import bot
from ubermensch.assistant import Outcome
from ubermensch.store import Store


def handler(dispatcher, kind: str, name: str):
    for router in dispatcher.sub_routers:
        for item in getattr(router, kind).handlers:
            if item.callback.__name__ == name:
                return item.callback
    raise LookupError(name)


class RatingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.store.add_message("user", "Мне отказали в общаге", "tg")
        self.reply = self.store.add_message("assistant", "Сочувствую, передай врачу", "tg")
        self.core = SimpleNamespace(store=self.store, login=None,
                                    chat=AsyncMock(return_value=Outcome(reply="Вот рычаги", message_id=99)))
        self.dispatcher = bot.create_dispatcher(SimpleNamespace(tg_chat_id=1), self.core)
        self.bot = MagicMock(send_message=AsyncMock(), send_chat_action=AsyncMock())

    def query(self, sign):
        return SimpleNamespace(data=f"fb:{self.reply}:{sign}", bot=self.bot, answer=AsyncMock(),
                               message=SimpleNamespace(edit_reply_markup=AsyncMock()))

    async def test_thumbs_down_records_and_next_message_is_the_reason(self):
        await handler(self.dispatcher, "callback_query", "on_rating")(self.query("-"))
        self.assertIn("Что было не так", self.bot.send_message.await_args.args[1])
        message = SimpleNamespace(text="Нет рычагов: письменный отказ, жалоба", bot=self.bot,
                                  chat=SimpleNamespace(id=1))
        with patch.object(bot, "ChatActionSender", MagicMock()):
            await handler(self.dispatcher, "message", "on_text")(message)
        [item] = self.store.rated_replies(self.store.db.execute(
            "SELECT substr(ts,1,10) FROM messages WHERE id=?", (self.reply,)).fetchone()[0])
        self.assertEqual((item["rating"], item["why"]), ("unhelpful", "Нет рычагов: письменный отказ, жалоба"))
        self.assertEqual(item["asked"], "Мне отказали в общаге")
        self.assertIn("👎", self.core.chat.await_args.args[0])  # Ассистент понимает, что это отзыв
        # new reply includes rating buttons
        markup = self.bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, "fb:99:-")

    async def test_thumbs_up_and_unknown_reply(self):
        await handler(self.dispatcher, "callback_query", "on_rating")(self.query("+"))
        self.assertEqual(self.store.quality_metrics()["helpful_rate"], 1)
        missing = self.query("+")
        missing.data = "fb:12345:+"
        await handler(self.dispatcher, "callback_query", "on_rating")(missing)
        self.assertEqual(self.store.quality_metrics()["rated"], 1)

    async def test_long_reply_gets_buttons_once(self):
        await bot.send(self.bot, 1, "а" * 3000 + "\n\n" + "б" * 3000, reply_markup="KB")
        calls = self.bot.send_message.await_args_list
        self.assertEqual([c.kwargs.get("reply_markup") for c in calls], [None, "KB"])


if __name__ == "__main__":
    unittest.main()
