"""Окно в мир: погода, курсы валют и главные новости; чтобы ассистент знала, что происходит сегодня.

Обновляется раз в несколько часов (core.world_loop) и лежит в workspace/world.json; в контекст
чата попадает коротким блоком <world>. Источники доступны и из России, и через VPN:
Open-Meteo (погода), open.er-api.com (курсы), Google Новости (RSS, русская выдача).
Всё: без ключей и без данных пользователя (запросы; только город и язык).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
TIMEOUT = 20
CITY = os.getenv("WORLD_CITY", "Москва")
LAT, LON = float(os.getenv("WORLD_LAT", "55.75")), float(os.getenv("WORLD_LON", "37.62"))
NEWS_RSS = "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru"
NEWS_COUNT = 10
MAX_AGE = timedelta(hours=24)  # старше: в контекст не идёт (лучше ничего, чем вчерашнее)

WEATHER = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
           45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "сильная морось",
           61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
           67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
           77: "снежная крупа", 80: "ливень", 81: "ливень", 82: "сильный ливень",
           85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом",
           99: "гроза с градом"}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def weather() -> str:
    data = json.loads(_get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}&timezone=Europe%2FMoscow&forecast_days=2"
        "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"))
    now, daily = data["current"], data["daily"]
    days = [f"{label}: {WEATHER.get(daily['weather_code'][i], '—')}, "
            f"{daily['temperature_2m_min'][i]:+.0f}…{daily['temperature_2m_max'][i]:+.0f}°, "
            f"осадки {daily['precipitation_probability_max'][i]}%"
            for i, label in enumerate(("сегодня", "завтра"))]
    return (f"{CITY}: сейчас {now['temperature_2m']:+.0f}° (ощущается "
            f"{now['apparent_temperature']:+.0f}°), {WEATHER.get(now['weather_code'], '—')}, "
            f"ветер {now['wind_speed_10m']:.0f} км/ч. " + "; ".join(days))


def rates() -> str:
    data = json.loads(_get("https://open.er-api.com/v6/latest/USD"))["rates"]
    rub = data["RUB"]
    return (f"USD {rub:.2f} ₽, EUR {rub / data['EUR']:.2f} ₽, CNY {rub / data['CNY']:.2f} ₽ "
            "(рыночный курс, не ЦБ)")


def news() -> list[str]:
    raw = _get(NEWS_RSS).decode("utf-8", errors="replace")
    titles = [unescape(re.sub(r"<[^>]+>", "", t)).strip()
              for t in re.findall(r"<item>.*?<title>(.*?)</title>", raw, re.S)]
    return [t for t in titles if t][:NEWS_COUNT]


def refresh(path: Path) -> dict:
    """Собрать свежее; что не получилось: взять из прошлого снимка."""
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    snapshot = {"updated": datetime.now().isoformat(timespec="minutes")}
    for key, fetch in (("weather", weather), ("rates", rates), ("news", news)):
        try:
            snapshot[key] = fetch()
        except Exception as e:  # сеть, формат: не страшно, есть прошлое значение
            log.warning("Окно в мир: %s не обновилось (%s)", key, e)
            if key in old:
                snapshot[key] = old[key]
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    return snapshot


def render(path: Path) -> str:
    """Блок для контекста чата; пустая строка: снимка нет или он устарел."""
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if datetime.now() - datetime.fromisoformat(data["updated"]) > MAX_AGE:
        return ""
    lines = [f"Обновлено {data['updated'][11:16]}."]
    if data.get("weather"):
        lines.append(f"Погода — {data['weather']}")
    if data.get("rates"):
        lines.append(f"Курсы — {data['rates']}")
    if data.get("news"):
        lines.append("Главные новости (заголовки Google Новостей):\n"
                     + "\n".join(f"- {t}" for t in data["news"]))
    return "<world>\n" + "\n".join(lines) + "\n</world>"
