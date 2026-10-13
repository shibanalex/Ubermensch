"""Личный кабинет студента (e.mospolytech.ru/old): только чтение.

Вход — Playwright через установленный Chrome, без окна, по LK_LOGIN и LK_PASSWORD из .env
(капча или ошибка — окно для ручного входа: lk_login.bat). Браузер нужен только для входа:
сессия (cookies) сохраняется в secrets, дальше страницы читаются обычными HTTP-запросами.
Обход: с главной по ссылкам внутри кабинета (без «выхода» и действий), плюс документы
(PDF, Word). Снимок страниц хранится на D:, изменения между снимками — событиями.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from .. import config
from . import site

log = logging.getLogger(__name__)

ROOT = "https://e.mospolytech.ru/old/"
HOST = "e.mospolytech.ru"
SESSION = config.DATA_DIR / "secrets" / "lk_session.json"
SNAPSHOT = config.DATA_DIR / "raw" / "lk" / "snapshot.json"
MAX_PAGES = 60
PAUSE = 0.7  # между запросами — не нагружать сервер вуза
UNSAFE = re.compile(r"(?i)logout|exit|выход|delete|remove|udal|cancel|otmen|submit|send|"
                    r"save|edit|\.(?:jpg|jpeg|png|gif|webp|zip|rar|mp4|mp3|css|js)(?:\?|$)")
LOGIN_TIMEOUT_MS = 45_000


class LkUnavailable(Exception):
    """Кабинет не отвечает (сбой на стороне вуза или нет сети)."""


class LkLoginFailed(Exception):
    """Не пускает: неверный логин/пароль, капча или нет данных для входа."""


def credentials() -> tuple[str, str] | None:
    login, password = os.getenv("LK_LOGIN"), os.getenv("LK_PASSWORD")
    return (login, password) if login and password else None


def _is_login_page(html: str) -> bool:
    return bool(re.search(r'<input[^>]+type=["\']?password', html, re.I))


# --- вход ---

def login(interactive: bool = False) -> None:
    """Войти и сохранить сессию. interactive — окно, где пользователь входит сам."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    auth = credentials()
    if not interactive and auth is None:
        raise LkLoginFailed("Нет LK_LOGIN и LK_PASSWORD в .env")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not interactive)
        try:
            page = browser.new_context(locale="ru-RU").new_page()
            try:
                page.goto(ROOT, timeout=LOGIN_TIMEOUT_MS)
            except PlaywrightError as e:
                raise LkUnavailable(str(e).splitlines()[0]) from None
            password = page.locator("input[type=password]").first
            if interactive:
                # пользователь входит сам; ждём, пока поле пароля исчезнет (до 5 минут)
                page.wait_for_selector("input[type=password]", state="detached", timeout=300_000)
            elif password.count():
                user = page.locator("input[type=text], input[type=email], input:not([type])").first
                user.fill(auth[0])
                password.fill(auth[1])
                password.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=LOGIN_TIMEOUT_MS)
                except PlaywrightError:
                    pass
                if page.locator("input[type=password]").count():
                    raise LkLoginFailed("Кабинет не принял логин и пароль (или просит капчу) — "
                                        "проверь LK_LOGIN/LK_PASSWORD в .env или войди сам: "
                                        "lk_login.bat")
            cookies = [c for c in page.context.cookies() if HOST in c.get("domain", "")]
        finally:
            browser.close()
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps(cookies), encoding="utf-8")
    log.info("Вход в личный кабинет выполнен, сессия сохранена")


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    if SESSION.exists():
        for c in json.loads(SESSION.read_text(encoding="utf-8")):
            jar.set_cookie(http.cookiejar.Cookie(
                0, c["name"], c["value"], None, False, c["domain"], True,
                c["domain"].startswith("."), c.get("path", "/"), True, c.get("secure", False),
                int(c["expires"]) if c.get("expires", -1) > 0 else None, False, None, None, {}))
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


# --- чтение ---

def _get(url: str, opener) -> dict:
    """Страница кабинета текстом; сессия кончилась — LkLoginFailed после повторного входа."""
    try:
        raw, kind, final = site.fetch(url, opener)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise LkUnavailable(str(e)) from None
    if "html" not in kind:
        text = site.document_text(raw, final, kind)
        return {"url": final, "title": final.rsplit("/", 1)[-1], "text": text or "",
                "links": [], "login": False}
    html = site._decode(raw, kind)
    title, text, links = site.parse_html(html, final)
    return {"url": final, "title": title, "text": text, "links": links,
            "login": _is_login_page(html)}


def _session_opener():
    """Опенер с живой сессией: если сессии нет или она кончилась — входим заново."""
    opener = _opener()
    if not SESSION.exists() or _get(ROOT, opener)["login"]:
        login()
        opener = _opener()
        if _get(ROOT, opener)["login"]:
            raise LkLoginFailed("После входа кабинет снова просит пароль")
    return opener


def read(url: str) -> dict:
    """Одна страница или документ кабинета (для инструмента Ани)."""
    if urllib.parse.urlparse(url).hostname != HOST:
        raise ValueError("Это не личный кабинет")
    page = _get(url, _session_opener())
    return {"url": page["url"], "title": page["title"], "text": page["text"][:site.MAX_TEXT],
            "links": [{"url": u, "text": t} for u, t in page["links"]
                      if urllib.parse.urlparse(u).hostname == HOST and t][:150]}


def crawl() -> dict[str, dict]:
    """Обход кабинета: {адрес: {"title", "text", "hash"}}. Только ссылки внутри /old/,
    без выхода, удаления, отправки форм и картинок."""
    opener = _session_opener()
    pages: dict[str, dict] = {}
    queue, seen = deque([ROOT]), {ROOT}
    while queue and len(pages) < MAX_PAGES:
        url = queue.popleft()
        try:
            page = _get(url, opener)
        except LkUnavailable:
            raise
        except Exception as e:  # одна битая страница не должна ломать обход
            log.warning("ЛК: не прочитал %s: %s", url, e)
            continue
        if page["login"]:
            raise LkLoginFailed("Сессия кончилась посреди обхода")
        text = page["text"].strip()
        if text:
            pages[url] = {"title": page["title"], "text": text[:30_000],
                          "hash": hashlib.sha1(text.encode()).hexdigest()}
        for link, _ in page["links"]:
            link = link.split("#")[0]
            parsed = urllib.parse.urlparse(link)
            if (parsed.hostname == HOST and parsed.path.startswith("/old/") and link not in seen
                    and not UNSAFE.search(link)):
                seen.add(link)
                queue.append(link)
        time.sleep(PAUSE)
    return pages


def load_snapshot() -> dict[str, dict]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}


def save_snapshot(pages: dict[str, dict]) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")


def changes(old: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    """Новые и изменившиеся страницы: [{"url", "title", "text", "new"}]."""
    return [{"url": url, "title": page["title"], "text": page["text"], "new": url not in old}
            for url, page in new.items()
            if url not in old or old[url]["hash"] != page["hash"]]


def find(query: str, limit: int = 5) -> list[dict]:
    """Поиск по последнему снимку кабинета (без похода в сеть): страницы, где есть все слова."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    found = []
    for url, page in load_snapshot().items():
        haystack = f"{page['title']}\n{page['text']}".lower()
        if all(w[:6] in haystack for w in words):  # грубая основа слова: «заявлен», «приказ»
            first = min((haystack.find(w[:6]) for w in words), default=0)
            found.append({"url": url, "title": page["title"],
                          "excerpt": page["text"][max(0, first - 300):first + 1500]})
    return found[:limit]


def main() -> None:
    """lk_login.bat: окно для ручного входа (если автоматический не прошёл)."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    print("Сейчас откроется окно личного кабинета — войди как обычно, окно закроется само.")
    login(interactive=True)
    print("Готово: Аня сохранила сессию кабинета.")


if __name__ == "__main__":
    main()
