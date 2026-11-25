"""Рабочий стол KDE Plasma: окна, уведомления, «не беспокоить», буфер обмена, яркость, батарея,
громкость, что играет. Всё: через D-Bus и разовые скрипты KWin (session.kwin_eval)."""
from __future__ import annotations

import re
import subprocess
import threading

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from . import session, windows

NOTIFY = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                     interface="org.freedesktop.Notifications")
KLIPPER = DBusAddress("/klipper", bus_name="org.kde.klipper", interface="org.kde.klipper.klipper")
BRIGHTNESS = DBusAddress("/org/kde/ScreenBrightness/display0", bus_name="org.kde.ScreenBrightness",
                         interface="org.kde.ScreenBrightness.Display")
BATTERY = DBusAddress("/org/freedesktop/UPower/devices/DisplayDevice",
                      bus_name="org.freedesktop.UPower", interface="org.freedesktop.DBus.Properties")
BATTERY_STATES = {1: "заряжается", 2: "разряжается", 3: "пустая", 4: "заряжена",
                  5: "ждёт зарядки", 6: "разряжается медленно"}
WINDOW_ACTIONS = ("focus", "minimize", "maximize", "restore", "close", "fullscreen")


def _call(address: DBusAddress, method: str, signature: str | None = None, body: tuple = (),
          bus: str = "SESSION"):
    with open_dbus_connection(bus=bus) as connection:
        reply = connection.send_and_get_reply(new_method_call(address, method, signature, body),
                                              timeout=5)
    if reply.header.message_type.name == "error":
        raise RuntimeError(str(reply.body[0]) if reply.body else "ошибка D-Bus")
    return reply.body


def _property(address: DBusAddress, interface: str, name: str, bus: str = "SESSION"):
    props = DBusAddress(address.object_path, bus_name=address.bus_name,
                        interface="org.freedesktop.DBus.Properties")
    return _call(props, "Get", "ss", (interface, name), bus)[0][1]


# --- окна ---

_WINDOW_FIELDS = """{id: String(w.internalId), app: String(w.resourceClass), title: String(w.caption),
    active: w === workspace.activeWindow, minimized: w.minimized, fullscreen: w.fullScreen}"""


def window_list() -> list[dict]:
    return session.kwin_eval(
        "reply(workspace.windowList().filter(w => w.normalWindow && !w.skipTaskbar)"
        f".map(w => ({_WINDOW_FIELDS})));")


def window_action(window_id: str, action: str) -> dict:
    """Сделать с окном (id из window_list) одно из WINDOW_ACTIONS."""
    if action not in WINDOW_ACTIONS:
        raise ValueError(f"действие: {', '.join(WINDOW_ACTIONS)}")
    steps = {
        "focus": "w.minimized = false; workspace.activeWindow = w;",
        "minimize": "w.minimized = true;",
        "maximize": "w.minimized = false; w.setMaximize(true, true);",
        "restore": "w.minimized = false; w.setMaximize(false, false); w.fullScreen = false;",
        "close": "w.closeWindow();",  # программа сама спросит про несохранённое
        "fullscreen": "w.fullScreen = !w.fullScreen;",
    }[action]
    return session.kwin_eval(
        f"const w = workspace.windowList().find(x => String(x.internalId) === {window_id!r});"
        f"if (!w) reply({{error: 'окна нет'}}); else {{ {steps} reply({_WINDOW_FIELDS}); }}")


# --- уведомления и «не беспокоить» ---

def notify(title: str, text: str, urgent: bool = False) -> int:
    hints = {"urgency": ("y", 2 if urgent else 1), "desktop-entry": ("s", "anya")}
    return _call(NOTIFY, "Notify", "susssasa{sv}i",
                 ("Аня", 0, "face-smile", title, text, [], hints, -1))[0]


_inhibit = {"connection": None, "cookie": None, "timer": None}
_inhibit_lock = threading.Lock()


def do_not_disturb(minutes: int) -> bool:
    """Тихий режим уведомлений на minutes минут (0: выключить). Держится, пока жив процесс."""
    with _inhibit_lock:
        if _inhibit["timer"]:
            _inhibit["timer"].cancel()
        if _inhibit["connection"] is not None:  # закрытое соединение снимает запрет само
            _inhibit["connection"].close()
            _inhibit.update(connection=None, cookie=None, timer=None)
        if minutes <= 0:
            return False
        connection = open_dbus_connection(bus="SESSION")
        reply = connection.send_and_get_reply(new_method_call(
            NOTIFY, "Inhibit", "ssa{sv}", ("anya", "Аня: не беспокоить", {})), timeout=5)
        timer = threading.Timer(minutes * 60, do_not_disturb, args=(0,))
        timer.daemon = True
        timer.start()
        _inhibit.update(connection=connection, cookie=reply.body[0], timer=timer)
        return True


# --- буфер, яркость, батарея, звук ---

def clipboard() -> str:
    return _call(KLIPPER, "getClipboardContents")[0]


def set_clipboard(text: str) -> None:
    _call(KLIPPER, "setClipboardContents", "s", (text,))


def brightness() -> int | None:
    try:
        value = _property(BRIGHTNESS, BRIGHTNESS.interface, "Brightness")
        top = _property(BRIGHTNESS, BRIGHTNESS.interface, "MaxBrightness")
    except (RuntimeError, OSError):
        return None
    return round(value * 100 / top) if top else None


def set_brightness(percent: int) -> int:
    top = _property(BRIGHTNESS, BRIGHTNESS.interface, "MaxBrightness")
    percent = max(1, min(100, percent))
    _call(BRIGHTNESS, "SetBrightness", "iu", (round(top * percent / 100), 0))
    return percent


def battery() -> dict | None:
    try:
        percent = _call(BATTERY, "Get", "ss", ("org.freedesktop.UPower.Device", "Percentage"),
                        "SYSTEM")[0][1]
        state = _call(BATTERY, "Get", "ss", ("org.freedesktop.UPower.Device", "State"),
                      "SYSTEM")[0][1]
    except (RuntimeError, OSError):
        return None
    return {"percent": round(percent), "state": BATTERY_STATES.get(state, "?")}


def volume() -> dict | None:
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"([\d.]+)", out)
    return {"percent": round(float(found.group(1)) * 100), "muted": "MUTED" in out} if found else None


def now_playing() -> list[dict]:
    """Плееры MPRIS: что играет или стоит на паузе."""
    result = []
    with open_dbus_connection(bus="SESSION") as connection:
        for player in windows._players(connection):
            props = DBusAddress("/org/mpris/MediaPlayer2", bus_name=player,
                                interface="org.freedesktop.DBus.Properties")
            try:
                reply = connection.send_and_get_reply(new_method_call(
                    props, "GetAll", "s", ("org.mpris.MediaPlayer2.Player",)), timeout=2)
                values = {k: v[1] for k, v in reply.body[0].items()}
            except Exception:
                continue
            meta = {k: v[1] for k, v in values.get("Metadata", {}).items()}
            artist = meta.get("xesam:artist") or []
            result.append({"player": player.rsplit(".", 1)[-1].split(".instance")[0],
                           "status": values.get("PlaybackStatus", "?"),
                           "title": meta.get("xesam:title", ""),
                           "artist": ", ".join(artist) if isinstance(artist, list) else str(artist)})
    return result


def suspend() -> None:
    subprocess.run(["systemctl", "suspend"], check=True, timeout=10)
