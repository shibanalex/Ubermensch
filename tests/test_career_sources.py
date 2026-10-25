"""Зарубежные вакансии: Arbeitnow (Германия/ЕС), визовая поддержка по тексту, общий снимок."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from ubermensch import career
from ubermensch.store import Store

PAGE = {"data": [
    {"slug": "python-backend-berlin", "company_name": "Acme", "title": "Python Backend Engineer",
     "description": "<p>FastAPI, PostgreSQL. We offer <b>visa sponsorship</b> and relocation support.</p>",
     "remote": False, "url": "https://www.arbeitnow.com/jobs/acme/python-backend-berlin",
     "tags": ["Python", "Backend"], "job_types": ["full time"], "location": "Berlin", "created_at": 1},
    {"slug": "django-dev-munich", "company_name": "Beta", "title": "Django Developer",
     "description": "Django and Python. German C1 required.", "remote": True,
     "url": "https://www.arbeitnow.com/jobs/beta/django-dev-munich", "tags": [], "job_types": [],
     "location": "Munich", "created_at": 2},
], "links": {"next": None}}


class CareerSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)

    def test_visa_is_found_in_text_and_silence_is_not_a_no(self):
        self.assertEqual(career.visa_note("We provide relocation package and Blue Card help"), "найдено в тексте")
        self.assertEqual(career.visa_note("Wir bieten Visumsunterstützung"), "найдено в тексте")
        self.assertEqual(career.visa_note("Great team, flexible hours"), "не сказано")

    async def test_search_filters_words_and_visa_and_reuses_snapshot(self):
        fetch = AsyncMock(return_value=PAGE)
        with patch.object(career, "request_json", fetch):
            every = await career.search(self.store, "arbeitnow", "python", limit=10)
            visa = await career.search(self.store, "arbeitnow", "python", limit=10, visa_only=True)
        self.assertEqual(every["found"], 2)
        self.assertEqual([i["title"] for i in visa["items"]], ["Python Backend Engineer"])
        self.assertEqual(visa["items"][0]["visa_or_relocation"], "найдено в тексте")
        self.assertEqual(fetch.await_count, 1)  # один снимок на все запросы
        saved = self.store.opportunity(visa["items"][0]["id"])
        self.assertEqual((saved["source"], saved["details"]["location"]), ("arbeitnow", "Berlin"))
        self.assertNotIn("<b>", saved["details"]["description"])


if __name__ == "__main__":
    unittest.main()
