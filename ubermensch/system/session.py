"""Сенсоры сеанса KDE Plasma на Wayland: активное окно, простой, уведомления.

Чужие окна на Wayland видит только композитор, поэтому в KWin загружается скрипт
kwin_tracker.js: при каждой смене активного окна (и его заголовка) он звонит по D-Bus
сюда. Простой приходит от композитора по протоколу ext-idle-notify. Уведомления
программ и сайтов слышны на шине D-Bus (вызовы Notify) — они пишутся в журнал по дням,
откуда их забирает сборщик collectors/notifications.py.

Сенсоры живут в ядре (start() из __main__). Маскот и лист задач — другие процессы:
они спрашивают ядро по D-Bus (org.ubermensch.Anya, /Session). Ядро не запущено —
окно неизвестно (None), простой 0.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from jeepney import (DBusAddress, HeaderFields, MatchRule, MessageType, new_error, new_method_call,
                     new_method_return)
from jeepney.bus_messages import Monitoring, message_bus
from jeepney.io.blocking import open_dbus_connection

SERVICE, PATH, IFACE = "org.ubermensch.Anya", "/Session", "org.ubermensch.Session"
KWIN_SCRIPT = Path(__file__).with_name("kwin_tracker.js")
KWIN_PLUGIN = "ubermensch-tracker"
IDLE_STEP_MS = 5000  # композитор сообщает «простой» через столько без мыши и клавиатуры

log = logging.getLogger(__name__)


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.window: dict | None = None
        self.idle_since: float | None = None  # monotonic: с какого момента нет ввода
        self.running = False


_state = _State()


# --- в ядре ---

def start(notifications_dir: Path | None = None) -> None:
    """Поднять сенсоры в этом процессе: D-Bus-сервис, скрипт KWin, простой, уведомления."""
    if _state.running:
        return
    _state.running = True
    ready = threading.Event()
    threading.Thread(target=_serve, args=(ready,), name="session-dbus", daemon=True).start()
    ready.wait(5)
    threading.Thread(target=_watch_idle, name="session-idle", daemon=True).start()
    if notifications_dir:
        threading.Thread(target=_watch_notifications, args=(notifications_dir,),
                         name="session-notifications", daemon=True).start()
    _load_kwin_script()


def _serve(ready: threading.Event) -> None:
    try:
        connection = open_dbus_connection(bus="SESSION")
        granted = connection.send_and_get_reply(message_bus.RequestName(SERVICE, 4)).body[0]
    except Exception:
        log.exception("Сеанс: D-Bus недоступен — окна и простой не отслеживаются")
        ready.set()
        return
    ready.set()
    if granted != 1:  # 1 = мы владелец имени
        log.warning("Сеанс: имя %s уже занято другим процессом", SERVICE)
        return
    while True:
        message = connection.receive()
        if message.header.message_type != MessageType.method_call:
            continue
        try:
            reply = _handle(message)
        except Exception as error:  # чужой вызов не должен ронять сенсоры
            reply = new_error(message, f"{SERVICE}.Error", "s", (str(error),))
        connection.send(reply)


def _handle(message):
    method = message.header.fields.get(HeaderFields.member)
    if method == "Window":  # от скрипта KWin
        data = json.loads(message.body[0]) if message.body else {}
        with _state.lock:
            _state.window = data or None
        return new_method_return(message)
    if method == "ActiveWindow":
        with _state.lock:
            return new_method_return(message, "s", (json.dumps(_state.window or {}),))
    if method == "IdleSeconds":
        return new_method_return(message, "d", (_local_idle(),))
    return new_error(message, "org.freedesktop.DBus.Error.UnknownMethod", "s", (str(method),))


def _load_kwin_script() -> None:
    """Перезагрузить скрипт: после перезапуска ядра он сразу сообщит текущее окно."""
    def kwin(method: str, *args: str) -> str:
        done = subprocess.run(["qdbus6", "org.kde.KWin", "/Scripting",
                               f"org.kde.kwin.Scripting.{method}", *args],
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip()

    try:
        if kwin("isScriptLoaded", KWIN_PLUGIN) == "true":
            kwin("unloadScript", KWIN_PLUGIN)
        kwin("loadScript", str(KWIN_SCRIPT), KWIN_PLUGIN)
        kwin("start")
    except (OSError, subprocess.SubprocessError):
        log.exception("Сеанс: не удалось загрузить скрипт KWin — активное окно неизвестно")


def _watch_idle() -> None:
    try:
        from pywayland.client import Display
        from pywayland.protocol.wayland import WlSeat

        from .protocols.ext_idle_notify_v1 import ExtIdleNotifierV1

        display = Display()
        display.connect()
    except Exception:
        log.exception("Сеанс: нет подключения к Wayland — простой не отслеживается")
        return
    found = {}

    def on_global(registry, name, interface, version):
        if interface == "wl_seat" and "seat" not in found:
            found["seat"] = registry.bind(name, WlSeat, 1)
        elif interface == "ext_idle_notifier_v1":
            found["notifier"] = registry.bind(name, ExtIdleNotifierV1, min(version, 2))
            found["version"] = version

    registry = display.get_registry()
    registry.dispatcher["global"] = on_global
    display.roundtrip()
    if "notifier" not in found or "seat" not in found:
        log.warning("Сеанс: композитор не поддерживает ext-idle-notify")
        return
    notifier = found["notifier"]
    # v2: только ввод, без учёта «не засыпать» от видео — нам важно, трогают ли компьютер
    subscribe = notifier.get_input_idle_notification if found["version"] >= 2 \
        else notifier.get_idle_notification
    notification = subscribe(IDLE_STEP_MS, found["seat"])

    def idled(_):
        with _state.lock:
            _state.idle_since = time.monotonic() - IDLE_STEP_MS / 1000

    def resumed(_):
        with _state.lock:
            _state.idle_since = None

    notification.dispatcher["idled"] = idled
    notification.dispatcher["resumed"] = resumed
    while display.dispatch(block=True) != -1:
        pass
    log.warning("Сеанс: соединение с Wayland закрыто — простой больше не отслеживается")


def _watch_notifications(folder: Path) -> None:
    """Каждый вызов Notify на шине — строка в <folder>/ГГГГ-ММ-ДД.jsonl."""
    try:
        connection = open_dbus_connection(bus="SESSION")
        rule = MatchRule(type="method_call", interface="org.freedesktop.Notifications",
                         member="Notify")
        connection.send_and_get_reply(Monitoring().BecomeMonitor([rule.serialise()]))
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.exception("Сеанс: уведомления не слышны")
        return
    while True:
        message = connection.receive()
        if message.header.message_type != MessageType.method_call or len(message.body) < 7:
            continue
        app, _, _, summary, body, _, hints = message.body[:7]
        entry = hints.get("desktop-entry", ("s", ""))[1] if isinstance(hints, dict) else ""
        now = datetime.now()
        record = {"ts": now.isoformat(timespec="seconds"), "app": app, "entry": entry,
                  "summary": summary, "body": body}
        try:
            with open(folder / f"{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as journal:
                journal.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            log.warning("Сеанс: не удалось записать уведомление", exc_info=True)


def _local_idle() -> float:
    with _state.lock:
        return 0.0 if _state.idle_since is None else time.monotonic() - _state.idle_since


# --- для всех процессов ---

_ADDRESS = DBusAddress(PATH, bus_name=SERVICE, interface=IFACE)
_client_lock = threading.Lock()
_client = None


def _call(method: str):
    global _client
    with _client_lock:
        try:
            if _client is None:
                _client = open_dbus_connection(bus="SESSION")
            reply = _client.send_and_get_reply(new_method_call(_ADDRESS, method), timeout=1)
        except Exception:
            if _client is not None:
                _client.close()
            _client = None
            return None
    if reply.header.message_type != MessageType.method_return:
        return None
    return reply.body[0]


def active_window() -> dict | None:
    if _state.running:
        with _state.lock:
            return dict(_state.window) if _state.window else None
    raw = _call("ActiveWindow")
    return (json.loads(raw) or None) if raw else None


def idle_seconds() -> float:
    if _state.running:
        return _local_idle()
    return _call("IdleSeconds") or 0.0
