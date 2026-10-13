"""Клиент локального API ядра на QtNetwork: запросы и поток событий (SSE)."""
from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import config

Callback = Callable[[dict], None]
BRAIN_TIMEOUT_MS = 330_000  # чуть больше, чем ядро ждёт Claude


class Api(QObject):
    state_changed = Signal(dict)  # свежий снимок состояния ядра
    event = Signal(dict)  # события шины: thinking, reminder, celebrate, changed
    online_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.base = f"http://{config.API_HOST}:{config.API_PORT}"
        self.nam = QNetworkAccessManager(self)
        self.online = False
        self.state: dict = {}
        self._events: QNetworkReply | None = None
        self._buffer = b""
        # Несколько событий подряд — один запрос состояния.
        self._refresh_timer = QTimer(self, singleShot=True, interval=120)
        self._refresh_timer.timeout.connect(lambda: self._get("/api/state", self._on_state))
        QTimer.singleShot(0, self._connect_events)

    # --- запросы ---

    def _request(self, path: str, timeout_ms: int) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(self.base + path))
        request.setRawHeader(b"Authorization", f"Bearer {config.api_token() or ''}".encode())
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(timeout_ms)
        return request

    def _get(self, path: str, callback: Callback | None = None) -> None:
        reply = self.nam.get(self._request(path, 15_000))
        reply.finished.connect(lambda: self._finished(reply, callback))

    def _post(self, path: str, payload: dict, callback: Callback | None = None,
              timeout_ms: int = 15_000) -> None:
        reply = self.nam.post(self._request(path, timeout_ms), json.dumps(payload).encode())
        reply.finished.connect(lambda: self._finished(reply, callback))

    @staticmethod
    def _finished(reply: QNetworkReply, callback: Callback | None) -> None:
        reply.deleteLater()
        raw = bytes(reply.readAll())
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if reply.error() != QNetworkReply.NetworkError.NoError and "error" not in data:
            data = {"error": "ядро недоступно"}
        if callback:
            callback(data)

    def refresh(self) -> None:
        self._refresh_timer.start()

    def _on_state(self, data: dict) -> None:
        if "error" not in data:
            self.state = data
            self.state_changed.emit(data)

    def chat(self, text: str, callback: Callback) -> None:
        self._post("/api/chat", {"text": text}, callback, BRAIN_TIMEOUT_MS)

    def onboard(self, callback: Callback) -> None:
        self._post("/api/onboard", {}, callback, BRAIN_TIMEOUT_MS)

    def add_task(self, title: str) -> None:
        self._post("/api/tasks", {"title": title})

    def set_task(self, task_id: int, status: str) -> None:
        self._post(f"/api/tasks/{task_id}", {"status": status})

    def metrics(self, callback: Callback) -> None:
        self._get("/api/metrics", callback)

    def decide(self, action_id: int, approve: bool, callback: Callback) -> None:
        self._post(f"/api/actions/{action_id}", {"approve": approve}, callback)

    # --- поток событий ---

    def _connect_events(self) -> None:
        self._buffer = b""
        # Ядро шлёт ping каждые 15 с; 40 с тишины — соединение мертво.
        reply = self.nam.get(self._request("/api/events", 40_000))
        reply.readyRead.connect(self._on_events_data)
        reply.finished.connect(self._on_events_closed)
        self._events = reply

    def _on_events_data(self) -> None:
        reply = self._events
        if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            return  # например, неверный токен — обработает finished
        self._set_online(True)
        self._buffer += bytes(reply.readAll())
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            for line in block.decode("utf-8", "replace").splitlines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event.get("type") in ("changed", "thinking"):
                        self.refresh()
                    self.event.emit(event)

    def _on_events_closed(self) -> None:
        self._events.deleteLater()
        self._events = None
        self._set_online(False)
        QTimer.singleShot(3000, self._connect_events)

    def _set_online(self, online: bool) -> None:
        if online != self.online:
            self.online = online
            self.online_changed.emit(online)
            if online:
                self.refresh()
