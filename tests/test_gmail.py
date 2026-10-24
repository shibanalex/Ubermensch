"""Чтение Gmail: разбор ответа FETCH без сети (части и FLAGS у Gmail идут в разном порядке)."""
from __future__ import annotations

import unittest

from ubermensch import contracts
from ubermensch.collectors import gmail

HEADER = (b"From: =?utf-8?b?0JvQmg==?= <lk@mospolytech.ru>\r\nSubject: Prikaz\r\n"
          b"Date: Thu, 22 Oct 2026 15:25:00 +0300\r\n"
          b"Content-Type: text/plain; charset=utf-8\r\n\r\n")


class GmailFetchTests(unittest.TestCase):
    def test_parts_in_any_order_and_flags_after_literals(self):
        data = [
            (b"1 (X-GM-MSGID 111 BODY[TEXT]<0> {5}", b"Hello"),
            (b" BODY[HEADER] {%d}" % len(HEADER), HEADER),
            b" FLAGS (\\Seen))",
            (b"2 (X-GM-MSGID 222 FLAGS () BODY[HEADER] {%d}" % len(HEADER), HEADER),
            (b" BODY[TEXT]<0> {5}", b"World"),
            b")",
        ]
        letters = [gmail._summary(m) for m in gmail._split_fetch(data)]
        self.assertEqual([m["id"] for m in letters], ["111", "222"])
        self.assertEqual([m["unread"] for m in letters], [False, True])
        self.assertEqual(letters[0]["from"], "ЛК <lk@mospolytech.ru>")
        self.assertEqual(letters[0]["subject"], "Prikaz")
        self.assertEqual([m["preview"] for m in letters], ["Hello", "World"])
        self.assertTrue(letters[1]["date"].startswith("2026-10-22T"))

    def test_read_accepts_only_numeric_ids(self):
        with self.assertRaises(ValueError):
            gmail.read("a@b.c", "x", "1 OR ALL")
        with self.assertRaises(ValueError):
            contracts.EmailRead.model_validate({"message_id": "1 OR ALL"})
        self.assertEqual(contracts.EmailInbox.model_validate({}).days, 3)


if __name__ == "__main__":
    unittest.main()
