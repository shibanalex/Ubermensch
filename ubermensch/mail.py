"""Send email via Gmail SMTP with explicit user confirmation gating."""
from __future__ import annotations

import mimetypes
import smtplib
from collections.abc import Sequence
from email.message import EmailMessage
from pathlib import Path


def send(
    address: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    attachments: Sequence[Path] | None = None,
) -> None:
    message = EmailMessage()
    message["From"] = address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    for path in attachments or ():
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype.split("/", 1) if ctype and "/" in ctype else ("application", "octet-stream"))
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(address, password.replace(" ", ""))
        smtp.send_message(message)
