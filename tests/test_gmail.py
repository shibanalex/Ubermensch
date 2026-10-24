"""Чтение Gmail: разбор ответа FETCH без сети (части и FLAGS у Gmail идут в разном порядке)."""
from __future__ import annotations

import imaplib
import os
import unittest
from unittest.mock import patch

from ubermensch import config, contracts
from ubermensch.collectors import gmail
from ubermensch.core import Core

ACCOUNTS = {"GMAIL_ADDRESS": "main@gmail.com", "GMAIL_PASSWORD": "a",
            "GMAIL_ADDRESS_2": "second@gmail.com", "GMAIL_PASSWORD_2": "b",
            "GMAIL_ADDRESS_3": "work@gmail.com", "GMAIL_PASSWORD_3": "c", "GMAIL_LABEL_3": "работа"}

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


class MailAccountsTests(unittest.TestCase):
    def test_accounts_resolve_by_number_address_name_or_label(self):
        with patch.dict(os.environ, ACCOUNTS):
            self.assertEqual([a.address for a in config.mail_accounts()],
                             ["main@gmail.com", "second@gmail.com", "work@gmail.com"])
            self.assertEqual(config.mail_account().address, "main@gmail.com")
            for name in ("2", "second", "Second@gmail.com"):
                self.assertEqual(config.mail_account(name).address, "second@gmail.com")
            self.assertEqual(config.mail_account("Работа").address, "work@gmail.com")
            with self.assertRaises(ValueError):
                config.mail_account("other")

    def test_inbox_merges_boxes_and_reports_a_broken_one(self):
        def inbox(address, password, days, query, limit):
            if address == "second@gmail.com":
                raise imaplib.IMAP4.error("auth")
            return [{"id": "1", "date": "2026-10-2" + ("4" if address == "work@gmail.com" else "3")}]

        with patch.dict(os.environ, ACCOUNTS), patch.object(gmail, "inbox", inbox):
            letters = Core.mail_inbox(3, "", 10, "all")
            self.assertEqual([m["account"] for m in letters],
                             ["work@gmail.com", "main@gmail.com", "second@gmail.com"])
            self.assertIn("error", letters[-1])
            self.assertEqual(len(Core.mail_inbox(3, "", 10, "работа")), 1)


if __name__ == "__main__":
    unittest.main()
