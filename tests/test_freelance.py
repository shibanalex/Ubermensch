"""Заказы FL.ru под его навык; серые отсекаются. Heartbeat видит новые письма и заказы без рассылок."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from ubermensch import agent
from ubermensch.collectors import freelance
from ubermensch.store import Store

RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<item><title>Парсинг ВБ Каспи</title><link>https://www.fl.ru/projects/1/a.html</link>
<description>Перенос карточек товаров</description><pubDate>Tue, 27 Oct 2026 08:42:26 GMT</pubDate></item>
<item><title>Отчёт 1С</title><link>https://www.fl.ru/projects/2/b.html</link>
<description>Бухгалтерия</description><pubDate>Tue, 27 Oct 2026 08:40:00 GMT</pubDate></item>
<item><title>Python: обход Cloudflare для VFS Global</title><link>https://www.fl.ru/projects/3/c.html</link>
<description>слоты на визовую запись, scraping</description><pubDate>Tue, 27 Oct 2026 08:30:00 GMT</pubDate></item>
<item><title>Авторегистратор почт outlook</title><link>https://www.fl.ru/projects/4/d.html</link>
<description>скрипт на selenium</description><pubDate>Tue, 27 Oct 2026 08:20:00 GMT</pubDate></item>
</channel></rss>"""


class FreelanceTests(unittest.TestCase):
    def test_only_his_skill_and_no_grey_orders(self):
        found = freelance.orders(RSS)
        self.assertEqual([e["external_id"] for e in found], ["https://www.fl.ru/projects/1/a.html"])
        self.assertEqual(found[0]["source"], "freelance")

    def test_heartbeat_sees_new_mail_and_orders_without_noise(self):
        store = Store(":memory:")
        self.addCleanup(store.db.close)
        store.add_events(freelance.orders(RSS))
        mails = [("TechCertify", "Тема: Айгуль приглашает вас на собеседование"),
                 ("Работа.ру", "Тема: 10 вакансий с ежедневной оплатой"),
                 ("hh.ru", "Тема: Работодатель не готов пригласить вас"),
                 ("hh.ru", "Тема: Работодатель не готов пригласить вас")]
        store.add_events([{"source": "gmail_2", "external_id": str(n), "ts": "2026-10-27T12:31:00",
                           "author": a, "text": t} for n, (a, t) in enumerate(mails)])
        autonomy = agent.Autonomy.__new__(agent.Autonomy)
        autonomy.store = store
        text, last = autonomy._new_events()
        self.assertIn("TechCertify", text)
        self.assertIn("Парсинг ВБ Каспи", text)
        self.assertNotIn("ежедневной оплатой", text)
        self.assertIn("отказов «не готов пригласить» — 2", text)
        store.set_cursor("heartbeat_events", str(last))
        self.assertEqual(autonomy._new_events()[0], "")  # уже показанное не повторяется


if __name__ == "__main__":
    unittest.main()
