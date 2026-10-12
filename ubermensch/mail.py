"""Отправка писем через Gmail (SMTP, пароль приложения). Вызывается только после «да» пользователя."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage


def send(address: str, password: str, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(address, password.replace(" ", ""))
        smtp.send_message(message)
