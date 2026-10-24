"""Gmail по IMAP: входящие (без рекламы, соцсетей и форумов) и отправленные.

Папки открываются только для чтения, письма забираются через BODY.PEEK —
отметки «прочитано» не появляются.
"""
from __future__ import annotations

import email
import email.policy
import html
import imaplib
import re
from datetime import datetime
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from . import local_iso

BODY_CHARS = 6000
BATCH = 40


def _text(message: email.message.EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        content = part.get_payload(decode=True).decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    content = re.sub(r"[ \t ]+", " ", content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


def _sent_folder(imap: imaplib.IMAP4_SSL) -> str | None:
    """Имя папки «Отправленные» (в русском Gmail оно закодировано modified UTF-7)."""
    _, folders = imap.list()
    for line in folders:
        decoded = line.decode("ascii", errors="replace")
        if "\\Sent" in decoded:
            return decoded.rsplit(' "/" ', 1)[-1]
    return None


def _fetch(imap: imaplib.IMAP4_SSL, ids: list[bytes], outgoing: bool) -> list[dict]:
    events = []
    for i in range(0, len(ids), BATCH):
        _, data = imap.fetch(b",".join(ids[i:i + BATCH]), "(X-GM-MSGID BODY.PEEK[])")
        for item in data:
            if not isinstance(item, tuple):
                continue
            msgid = re.search(rb"X-GM-MSGID (\d+)", item[0])
            message = email.message_from_bytes(item[1], policy=email.policy.default)
            try:
                sent_at = parsedate_to_datetime(message["date"])
            except (TypeError, ValueError):
                continue
            subject = str(message["subject"] or "").strip()
            sender_name, sender = parseaddr(str(message["from"] or ""))
            recipients = ", ".join(addr for _, addr in getaddresses([str(message["to"] or "")]))
            body = _text(message)[:BODY_CHARS]
            events.append({
                "source": "gmail",
                "external_id": msgid.group(1).decode() if msgid else str(message["message-id"]),
                "ts": local_iso(sent_at),
                "thread": re.sub(r"^((re|fwd?|ответ|пересл)\s*:\s*)+", "", subject, flags=re.I)[:200],
                "author": sender_name or sender,
                "outgoing": int(outgoing),
                "text": f"Тема: {subject}\n{body}",
                "meta": {"from": sender, "to": recipients[:300]},
            })
    return events


def collect(address: str, password: str, since: datetime) -> list[dict]:
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    try:
        imap.login(address, password.replace(" ", ""))
        events = []
        imap.select("INBOX", readonly=True)
        query = f'"after:{since:%Y/%m/%d} -category:promotions -category:social -category:forums"'
        _, found = imap.search(None, "X-GM-RAW", query)
        events += _fetch(imap, found[0].split(), outgoing=False)
        if sent := _sent_folder(imap):
            imap.select(sent, readonly=True)
            month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[since.month - 1]
            _, found = imap.search(None, "SINCE", f"{since.day:02d}-{month}-{since.year}")
            events += _fetch(imap, found[0].split(), outgoing=True)
        return events
    finally:
        try:
            imap.logout()
        except imaplib.IMAP4.error:
            pass


def _connect(address: str, password: str) -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    imap.login(address, password.replace(" ", ""))
    imap.select("INBOX", readonly=True)
    return imap


def _logout(imap: imaplib.IMAP4_SSL) -> None:
    try:
        imap.logout()
    except imaplib.IMAP4.error:
        pass


def _split_fetch(data: list) -> list[dict]:
    """Ответ FETCH по письмам: метаданные (номер, X-GM-MSGID, FLAGS) и тела частей HEADER/TEXT.
    Порядок частей и место FLAGS у Gmail не гарантированы — распознаём по описанию."""
    messages, current = [], None
    for item in data:
        meta = item[0] if isinstance(item, tuple) else item
        if not isinstance(meta, bytes):
            continue
        if re.match(rb"\d+ \(", meta):
            current = {"meta": b"", "header": b"", "text": b""}
            messages.append(current)
        if current is None:
            continue
        current["meta"] += meta
        if isinstance(item, tuple):
            current["header" if b"HEADER" in meta.upper() else "text"] = item[1]
    return messages


def _summary(raw: dict) -> dict:
    message = email.message_from_bytes(raw["header"] + raw["text"], policy=email.policy.default)
    msgid = re.search(rb"X-GM-MSGID (\d+)", raw["meta"])
    try:
        sent_at = local_iso(parsedate_to_datetime(message["date"]))
    except (TypeError, ValueError):
        sent_at = ""
    name, sender = parseaddr(str(message["from"] or ""))
    try:
        preview = _text(message)[:400]
    except (KeyError, LookupError, ValueError):  # начало многочастного письма без границы
        preview = ""
    return {
        "id": msgid.group(1).decode() if msgid else "",
        "date": sent_at,
        "from": f"{name} <{sender}>" if name else sender,
        "subject": str(message["subject"] or "").strip(),
        "unread": b"\\Seen" not in raw["meta"],
        "preview": preview,
    }


def inbox(address: str, password: str, days: int = 3, query: str = "",
          limit: int = 20) -> list[dict]:
    """Свежие входящие прямо из Gmail (без рекламы, соцсетей и форумов), новые первыми.
    query — поиск в синтаксисе Gmail (from:, subject:, слова). Отметки «прочитано» не ставятся."""
    imap = _connect(address, password)
    try:
        search = f"newer_than:{days}d -category:promotions -category:social -category:forums {query}"
        _, found = imap.search(None, "X-GM-RAW", f'"{search.strip().replace(chr(34), "")}"')
        ids = found[0].split()[-limit:]
        letters = []
        for i in range(0, len(ids), BATCH):
            _, data = imap.fetch(b",".join(ids[i:i + BATCH]),
                                 "(X-GM-MSGID FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4000>)")
            letters += [_summary(m) for m in _split_fetch(data)]
        return sorted(letters, key=lambda m: m["date"], reverse=True)
    finally:
        _logout(imap)


def read(address: str, password: str, message_id: str) -> dict:
    """Письмо целиком по id из inbox: отправитель, получатели, тема, текст, имена вложений."""
    if not message_id.isdigit():
        raise ValueError("id письма — число из списка входящих")
    imap = _connect(address, password)
    try:
        _, found = imap.search(None, "X-GM-MSGID", message_id)
        if not found[0]:
            raise ValueError("Письмо не найдено во входящих")
        _, data = imap.fetch(found[0].split()[0], "(BODY.PEEK[])")
        message = email.message_from_bytes(next(i for i in data if isinstance(i, tuple))[1],
                                           policy=email.policy.default)
        return {
            "id": message_id,
            "date": str(message["date"] or ""),
            "from": str(message["from"] or ""),
            "to": str(message["to"] or ""),
            "subject": str(message["subject"] or ""),
            "text": _text(message)[:BODY_CHARS],
            "attachments": [part.get_filename() for part in message.iter_attachments()
                            if part.get_filename()],
        }
    finally:
        _logout(imap)
