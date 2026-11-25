"""Новый личный кабинет (React): через его JSON API, без браузера.

Приложение e.mospolytech.ru ходит в old-e.mospolytech.ru/lk_api.php: вход POST ulogin/upassword →
токен, дальше GET ?<запрос>&token=…. Здесь: только чтение: профиль, уведомления, заявления
(со статусами и ответами), диалоги и договоры. Запросы, которые что-то меняют (sign*, set*,
clear*, create*, save*, newMessage, changeADPass, mailCode), не вызываются.

Кабинет отдаётся «страницами» в формате lk.crawl ({адрес: {title, text, hash}}): адреса -
PAGE_PREFIX/<раздел>/<id>, так работают снимок, поиск lk_find и события об изменениях.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from . import site

API = "https://old-e.mospolytech.ru/lk_api.php"
PAGE_PREFIX = "https://e.mospolytech.ru/api/"
TOKEN = config.DATA_DIR / "secrets" / "lk_token"
READ_ONLY = ("getUser", "getNotifications", "getAppRequests", "getMsgDialogues", "getPayments")
TIMEOUT = 40


class LkApiError(Exception):
    pass


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=site.TLS))


def _json(request) -> object:
    try:
        with _opener().open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as error:
        raise LkApiError(f"API кабинета недоступно: {error}") from None


def _login() -> str:
    from .lk import credentials

    pair = credentials()
    if not pair:
        raise LkApiError("Нет логина и пароля кабинета в .env")
    body = urllib.parse.urlencode({"ulogin": pair[0], "upassword": pair[1]}).encode()
    data = _json(urllib.request.Request(API, data=body,
                                        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise LkApiError("Кабинет не принял логин и пароль")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(token, encoding="utf-8")
    TOKEN.chmod(0o600)
    return token


def _get(query: str, token: str):
    if query not in READ_ONLY:
        raise ValueError(f"{query} не из разрешённых запросов чтения")
    return _json(f"{API}?{query}&token={urllib.parse.quote(token)}")


def session_token() -> str:
    """Сохранённый токен, если он ещё действует; иначе: новый вход."""
    if TOKEN.exists():
        token = TOKEN.read_text(encoding="utf-8").strip()
        user = _get("getUser", token)
        if isinstance(user, dict) and (user.get("user") or {}).get("is_token_valid"):
            return token
    return _login()


def _plain(value) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", str(value or ""))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t ]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _page(title: str, lines: list[str]) -> dict:
    text = "\n".join(line for line in lines if line and not line.endswith(": ")).strip()
    return {"title": title, "text": text[:30_000], "hash": hashlib.sha1(text.encode()).hexdigest()}


def _flatten(value, prefix="") -> list[str]:
    """Договоры и прочие вложенные данные: строками «ключ: значение»."""
    if isinstance(value, dict):
        return [line for k, v in value.items() for line in _flatten(v, f"{prefix}{k}.")]
    if isinstance(value, list):
        return [line for n, v in enumerate(value, 1) for line in _flatten(v, f"{prefix}{n}.")]
    return [f"{prefix.rstrip('.')}: {_plain(value)}"] if str(value or "").strip() else []


def pages(token: str | None = None) -> dict[str, dict]:
    token = token or session_token()
    result: dict[str, dict] = {}
    user = (_get("getUser", token) or {}).get("user") or {}
    result[PAGE_PREFIX + "profile"] = _page("Профиль студента", [
        f"{k}: {_plain(user.get(k))}" for k in ("status", "user_status", "course", "faculty", "group",
                                               "specialty", "degreeLength", "educationForm", "finance")])
    for item in _get("getNotifications", token) or []:
        result[f"{PAGE_PREFIX}notifications/{item.get('id')}"] = _page(
            "Уведомление: " + _plain(item.get("title"))[:200],
            [_plain(item.get("title")), _plain(item.get("text")), f"тип: {item.get('type', '')}"])
    for item in _get("getAppRequests", token) or []:
        files = [f.get("name") or f.get("title") or "" for f in (item.get("files_output") or [])
                 if isinstance(f, dict)]
        result[f"{PAGE_PREFIX}requests/{item.get('id')}"] = _page(
            f"Заявление №{item.get('num')}: {_plain(item.get('subject'))[:150]}", [
                f"Статус: {_plain(item.get('status'))}", f"Статус обновлён: {item.get('status_update', '')}",
                f"Подано: {item.get('created', '')}", f"Описание: {_plain(item.get('description'))}",
                f"Ответственное подразделение: {_plain(item.get('response_div'))}",
                f"Контакт: {_plain(item.get('response_contact'))}", f"Комментарий: {_plain(item.get('comment'))}",
                f"Ответные файлы: {', '.join(filter(None, files))}"])
    for item in _get("getMsgDialogues", token) or []:
        opponent = item.get("opponent") or {}
        last = item.get("lastmessage") or {}
        name = " ".join(str(opponent.get(k, "")) for k in ("surname", "name", "patronymic")).strip() \
            or _plain(opponent.get("fio"))
        result[f"{PAGE_PREFIX}dialogues/{item.get('id')}"] = _page(
            f"Диалог: {(_plain(item.get('subject')) or name or 'без темы')[:150]}", [
                f"Собеседник: {name}", f"Последнее сообщение ({last.get('date', '')}): {_plain(last.get('message') or last.get('text'))}"])
    payments = _get("getPayments", token) or {}
    result[PAGE_PREFIX + "payments"] = _page("Договоры и оплата", _flatten(payments.get("contracts") or payments))
    return result
