"""Google Календарь пользователя: Освальд сам ставит события и дедлайны (они сразу на телефоне).

Доступ — только к событиям (scope calendar.events), не ко всему аккаунту. Ключи OAuth и токен
лежат в D:\\Ubermensch\\secrets. Первый вход: google_login.bat (см. docs/OPERATIONS.md).
События, созданные Освальдом, помечаются в описании — их он может удалять сам, чужие только с «да».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_FILE = config.DATA_DIR / "secrets" / "google_oauth_client.json"
TOKEN_FILE = config.DATA_DIR / "secrets" / "google_token.json"
MARK = "— добавил Освальд"


class CalendarNotConnected(RuntimeError):
    pass


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        raise CalendarNotConnected("Google Календарь не подключён: запусти google_login.bat "
                                   "(инструкция — docs/OPERATIONS.md).")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise CalendarNotConnected(f"Доступ к календарю истёк или отозван ({e}): "
                                           "запусти google_login.bat заново.") from None
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise CalendarNotConnected("Доступ к календарю недействителен: запусти google_login.bat.")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _when(value: str) -> dict:
    """«2026-10-14» — на весь день, «2026-10-14T10:00» — точное время (местное)."""
    if len(value) == 10:
        return {"date": value}
    return {"dateTime": datetime.fromisoformat(value).astimezone().isoformat()}


def upcoming(days: int = 14) -> list[dict]:
    now = datetime.now().astimezone()
    items = _service().events().list(
        calendarId="primary", timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=days)).isoformat(), singleEvents=True,
        orderBy="startTime", maxResults=100).execute().get("items", [])
    return [{"id": e["id"], "title": e.get("summary", ""),
             "start": e["start"].get("dateTime", e["start"].get("date")),
             "end": e["end"].get("dateTime", e["end"].get("date")),
             "by_oswald": MARK in (e.get("description") or "")} for e in items]


def add(title: str, start: str, end: str | None = None, description: str = "",
        reminder_minutes: int | None = None) -> dict:
    if end is None:
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat() if len(start) == 10 \
            else (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="minutes")
    body = {"summary": title, "start": _when(start), "end": _when(end),
            "description": f"{description}\n\n{MARK}".strip()}
    if reminder_minutes is not None:
        body["reminders"] = {"useDefault": False,
                             "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
    event = _service().events().insert(calendarId="primary", body=body).execute()
    return {"id": event["id"], "link": event.get("htmlLink", "")}


def event(event_id: str) -> dict:
    return _service().events().get(calendarId="primary", eventId=event_id).execute()


def delete(event_id: str) -> None:
    _service().events().delete(calendarId="primary", eventId=event_id).execute()


def login() -> None:
    """Однократный вход в браузере; токен сохраняется в secrets."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_FILE.exists():
        raise SystemExit(f"Нет файла {CLIENT_FILE}. Создай OAuth-клиент (тип Desktop) в Google Cloud "
                         "и сохрани его JSON под этим именем — см. docs/OPERATIONS.md.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print("Готово: Освальд подключён к Google Календарю.")


if __name__ == "__main__":
    login()
    input("Нажми Enter, чтобы закрыть окно.")
