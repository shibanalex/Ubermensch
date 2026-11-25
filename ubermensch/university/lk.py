"""Личный кабинет студента (старый кабинет e.mospolytech.ru): только чтение.

Вход: Playwright через установленный Chrome, без окна, по LK_LOGIN и LK_PASSWORD из .env
(капча или ошибка: окно для ручного входа: lk_login.sh). Браузер нужен только для входа:
сессия (cookies) сохраняется в secrets, дальше страницы читаются обычными HTTP-запросами.
Обход: с главной по ссылкам внутри кабинета (без «выхода» и действий), плюс документы
(PDF, Word). Снимок страниц хранится в папке данных, изменения между снимками: событиями.
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

ROOT = "https://e.mospolytech.ru/old/"  # вход: отсюда кабинет сам ведёт на страницу входа
HOST = "e.mospolytech.ru"
# Старый кабинет жил в /old/, сейчас (новый сломан): в корне, разделы «/?p=marks». Обход
# стартует с того, где нашлось меню разделов: когда новый починят, старый вернётся в /old/.
STARTS = ("https://e.mospolytech.ru/", "https://e.mospolytech.ru/old/")
MIN_MENU_LINKS = 5
SESSION = config.DATA_DIR / "secrets" / "lk_session.json"
SNAPSHOT = config.DATA_DIR / "raw" / "lk" / "snapshot.json"
MAX_PAGES = 60
PAUSE = 0.7  # между запросами: не нагружать сервер вуза
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


CHANGE_PASSWORD = re.compile(r"(?i)name=[\"']?(old|new|confirm|repeat)_?pass")


def _is_login_page(html: str) -> bool:
    """Есть поле пароля, и это не форма смены пароля в «Персональной информации»."""
    fields = re.findall(r'<input[^>]+type=["\']?password[^>]*>', html, re.I)
    return any(not CHANGE_PASSWORD.search(field) for field in fields)


# --- вход ---

def login(interactive: bool = False) -> None:
    """Войти и сохранить сессию. interactive: окно, где пользователь входит сам."""
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
            # на странице входа несколько форм (одна скрытая: ФИО и 4 цифры): только видимые поля
            password = page.locator("input[type=password]:visible").first
            if interactive:
                # пользователь входит сам; ждём, пока поле пароля исчезнет (до 5 минут)
                page.wait_for_selector("input[type=password]", state="detached", timeout=300_000)
            elif password.count():
                user = page.locator("form:has(input[type=password]:visible) :is(input[type=text], "
                                    "input[type=email], input:not([type])):visible").first
                user.fill(auth[0])
                password.fill(auth[1])
                password.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=LOGIN_TIMEOUT_MS)
                except PlaywrightError:
                    pass
                if page.locator("input[type=password]:visible").count():
                    raise LkLoginFailed("Кабинет не принял логин и пароль (или просит капчу) — "
                                        "проверь LK_LOGIN/LK_PASSWORD в .env или войди сам: "
                                        "lk_login.sh")
            cookies = [c for c in page.context.cookies() if HOST in c.get("domain", "")]
        finally:
            browser.close()
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps(cookies), encoding="utf-8")
    SESSION.chmod(0o600)
    log.info("Вход в личный кабинет выполнен, сессия сохранена")


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    if SESSION.exists():
        for c in json.loads(SESSION.read_text(encoding="utf-8")):
            jar.set_cookie(http.cookiejar.Cookie(
                0, c["name"], c["value"], None, False, c["domain"], True,
                c["domain"].startswith("."), c.get("path", "/"), True, c.get("secure", False),
                int(c["expires"]) if c.get("expires", -1) > 0 else None, False, None, None, {}))
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                       urllib.request.HTTPSHandler(context=site.TLS))


# --- чтение ---

def _get(url: str, opener) -> dict:
    """Страница кабинета текстом; сессия кончилась: LkLoginFailed после повторного входа."""
    try:
        raw, kind, final = site.fetch(url, opener)
    except urllib.error.HTTPError as e:
        if e.code >= 500:  # кабинет лежит
            raise LkUnavailable(str(e)) from None
        raise RuntimeError(f"страница недоступна: {e.code}") from None  # одна битая ссылка
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
    """Опенер с живой сессией: если сессии нет или она кончилась; входим заново."""
    opener = _opener()
    if not SESSION.exists() or _get(ROOT, opener)["login"]:
        login()
        opener = _opener()
        if _get(ROOT, opener)["login"]:
            raise LkLoginFailed("После входа кабинет снова просит пароль")
    return opener


def read(url: str) -> dict:
    """Одна страница или документ кабинета (для инструмента ассистента)."""
    if urllib.parse.urlparse(url).hostname != HOST:
        raise ValueError("Это не личный кабинет")
    from .lk_api import PAGE_PREFIX
    if url.startswith(PAGE_PREFIX):  # запись из API кабинета: из последнего снимка
        page = load_snapshot().get(url)
        if page is None:
            raise ValueError("Такой записи в последнем снимке кабинета нет — найди её через lk_find")
        return {"url": url, "title": page["title"], "text": page["text"], "links": []}
    page = _get(url, _session_opener())
    return {"url": page["url"], "title": page["title"], "text": page["text"][:site.MAX_TEXT],
            "links": [{"url": u, "text": t} for u, t in page["links"]
                      if urllib.parse.urlparse(u).hostname == HOST and t][:150]}


def _in_cabinet(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname == HOST and (parsed.path in ("/", "/index.php")
                                        or parsed.path.startswith("/old/"))


def _start(opener) -> str:
    """Адрес, где сейчас живёт старый кабинет: главная с меню разделов."""
    for url in STARTS:
        try:
            page = _get(url, opener)
        except LkUnavailable:
            raise
        except Exception:
            continue
        links = [u for u, _ in page["links"] if _in_cabinet(u) and not UNSAFE.search(u)]
        if not page["login"] and len(set(links)) >= MIN_MENU_LINKS:
            return url
    raise LkUnavailable("не нашла меню старого кабинета ни в корне, ни в /old/")


def crawl() -> dict[str, dict]:
    """Кабинет: {адрес: {"title", "text", "hash"}}. С октября 2026 кабинет; React-приложение
    с JSON API (lk_api.py): уведомления, заявления, диалоги, договоры. Старый HTML-обход
    (crawl_html) остался на случай, если вуз вернёт прежний сайт."""
    from . import lk_api

    try:
        return lk_api.pages()
    except lk_api.LkApiError as error:
        raise LkUnavailable(str(error)) from None


def crawl_html() -> dict[str, dict]:
    """Обход старого HTML-кабинета: только ссылки внутри кабинета, без выхода, удаления,
    отправки форм и картинок."""
    opener = _session_opener()
    start = _start(opener)
    pages: dict[str, dict] = {}
    queue, seen = deque([start]), {start}
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
            if _in_cabinet(link) and link not in seen and not UNSAFE.search(link):
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
    """lk_login.sh: окно для ручного входа (если автоматический не прошёл)."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    print("Сейчас откроется окно личного кабинета — войди как обычно, окно закроется само.")
    login(interactive=True)
    print("Готово: Аня сохранила сессию кабинета.")


if __name__ == "__main__":
    main()
