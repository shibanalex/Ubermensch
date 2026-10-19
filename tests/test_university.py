"""Личный кабинет вуза: где кабинет и что считать страницей входа."""
from __future__ import annotations

import unittest

from ubermensch.university import lk


class CabinetTests(unittest.TestCase):
    def test_login_page_vs_change_password_form(self):
        self.assertTrue(lk._is_login_page('<input type="password" name="upassword" id="upassword">'))
        self.assertFalse(lk._is_login_page('<input name="old_pass" type="password">'
                                           '<input name="new_pass" type="password">'))
        self.assertFalse(lk._is_login_page("<p>Успеваемость</p>"))

    def test_cabinet_links_at_root_and_old(self):
        for url in ("https://e.mospolytech.ru/?p=marks", "https://e.mospolytech.ru/index.php",
                    "https://e.mospolytech.ru/old/?p=rasp"):
            self.assertTrue(lk._in_cabinet(url), url)
        for url in ("https://online.mospolytech.ru/course/view.php", "https://e.mospolytech.ru/api/x"):
            self.assertFalse(lk._in_cabinet(url), url)
        self.assertTrue(lk.UNSAFE.search("https://e.mospolytech.ru/?logout"))


if __name__ == "__main__":
    unittest.main()
