"""Заказы фриланс-бирж под его навык: парсинг, сбор данных, OCR. Источник — RSS FL.ru (лента
открыта без входа). Каждый подходящий заказ — событие `freelance`; heartbeat предлагает его с
готовым откликом. Серые заказы (визовые слоты, антифрод чужих сервисов, персональные данные,
накрутки) отсекаются: с ними риск бана и проблем с законом.
"""
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ElementTree
from email.utils import parsedate_to_datetime

FEEDS = ("https://www.fl.ru/rss/all.xml?category=5",)  # «Программирование»
MATCH = re.compile(r"(?i)парс|scrap|скрап|selenium|playwright|сбор данных|собрать данные|"
                   r"мониторинг цен|выгрузк\w* (?:с|из) сайт|ocr|распознав|краул|crawl")
EXCLUDE = re.compile(r"(?i)vfs|визов|\bвиз[ауы]\b|слот|запис\w* на прием|антифрод|anti-?fraud|персональн|"
                     r"накрут|спам|рассылк|пробив|базы? (?:номеров|телефонов)|авторегистр|"
                     r"массов\w* регистрац|регистрац\w* аккаунт")


def orders(raw: str) -> list[dict]:
    """Подходящие заказы из RSS: заголовок и описание — про его навык и без серых задач."""
    events = []
    for item in ElementTree.fromstring(raw).iter("item"):
        title = (item.findtext("title") or "").strip()
        about = " ".join((item.findtext("description") or "").split())
        text = f"{title} {about}"
        if not MATCH.search(text) or EXCLUDE.search(text):
            continue
        link = (item.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone().isoformat(
                timespec="seconds")
        except (TypeError, ValueError):
            continue
        events.append({"source": "freelance", "external_id": link, "ts": ts, "thread": "FL.ru",
                       "author": "FL.ru", "text": f"Тема: {title} — {about[:600]} {link}",
                       "meta": json.dumps({"category": item.findtext("category") or ""},
                                          ensure_ascii=False)})
    return events


def collect(timeout: int = 20) -> list[dict]:
    found = []
    for url in FEEDS:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            found += orders(response.read().decode("utf-8", "replace"))
    return found
