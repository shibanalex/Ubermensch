"""Новый ЛК через JSON API: только чтение, разделы — «страницы» снимка, переход со старого обхода."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ubermensch import config
from ubermensch.university import lk, lk_api

ANSWERS = {
    "getUser": {"user": {"is_token_valid": True, "status": "Учится", "course": "2", "faculty": "ФИТ"}},
    "getNotifications": [{"id": "7", "type": "doc", "title": "Приказ", "text": "<p>О предоставлении<br>отпуска</p>"}],
    "getAppRequests": [{"id": "3", "num": "SC1", "subject": "Сохранение места в общежитии", "status": "В работе",
                        "status_update": "2026-10-22", "created": "2026-10-20", "description": "Прошу сохранить",
                        "response_div": "Н-503", "response_contact": "", "comment": "", "files_output": []}],
    "getMsgDialogues": [{"id": "9", "subject": "", "opponent": {"surname": "Иванова", "name": "Мария"},
                         "lastmessage": {"message": "Заявление в работе"}}],
    "getPayments": {"contracts": {"dormitory": [{"number": "1001-01", "name": "Договор на проживание"}]}},
}


class LkApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in ((lk_api, "TOKEN"), (lk, "SNAPSHOT")):
            patcher = patch.object(target, value, Path(self.temp.name) / value.lower())
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(config, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, request):
        url = request if isinstance(request, str) else request.full_url
        if not isinstance(request, str):  # вход
            return {"token": "t0k"}
        return ANSWERS[url.split("?")[1].split("&")[0]]

    def test_sections_become_snapshot_pages(self):
        with patch.object(lk_api, "_json", self.fake), patch("ubermensch.university.lk.credentials",
                                                           return_value=("login", "pass")):
            pages = lk_api.pages()
        request = pages[lk_api.PAGE_PREFIX + "requests/3"]
        self.assertEqual(request["title"], "Заявление №SC1: Сохранение места в общежитии")
        self.assertIn("Статус: В работе", request["text"])
        self.assertIn("Ответственное подразделение: Н-503", request["text"])
        self.assertNotIn("Контакт:", request["text"])  # пустые поля не засоряют текст
        self.assertIn("О предоставлении\nотпуска", pages[lk_api.PAGE_PREFIX + "notifications/7"]["text"])
        self.assertEqual(pages[lk_api.PAGE_PREFIX + "dialogues/9"]["title"], "Диалог: Иванова Мария")
        self.assertIn("Договор на проживание", pages[lk_api.PAGE_PREFIX + "payments"]["text"])
        self.assertEqual((Path(self.temp.name) / "token").read_text(), "t0k")

    def test_only_read_queries_can_be_sent(self):
        for query in ("signDormitoryAct", "clearAllNotifications", "newMessage", "changeADPass"):
            with self.assertRaises(ValueError):
                lk_api._get(query, "t")

    def test_status_change_is_a_change_and_api_errors_mean_unavailable(self):
        old = {lk_api.PAGE_PREFIX + "requests/3": lk_api._page("Заявление", ["Статус: Новое"])}
        new = {lk_api.PAGE_PREFIX + "requests/3": lk_api._page("Заявление", ["Статус: Готово"])}
        self.assertEqual([c["url"] for c in lk.changes(old, new)], [lk_api.PAGE_PREFIX + "requests/3"])
        with patch.object(lk_api, "pages", side_effect=lk_api.LkApiError("нет сети")):
            with self.assertRaises(lk.LkUnavailable):
                lk.crawl()

    def test_api_records_are_read_from_snapshot(self):
        url = lk_api.PAGE_PREFIX + "requests/3"
        lk.save_snapshot({url: lk_api._page("Заявление №SC1", ["Статус: Готово"])})
        self.assertEqual(lk.read(url)["text"], "Статус: Готово")
        with self.assertRaises(ValueError):
            lk.read(lk_api.PAGE_PREFIX + "requests/404")


if __name__ == "__main__":
    unittest.main()
