"""Окна и ввод: активное окно, простой, полноэкранность, блокировка, музыка и громкость.

Окно и простой — от сенсоров сеанса (session.py: KWin и Wayland). Музыка — через MPRIS
(D-Bus-интерфейс любого плеера: Chrome, Spotify, VLC…), громкость — через PipeWire (wpctl).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from jeepney import DBusAddress, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

from . import session

MEDIA_ACTIONS = ("play_pause", "next", "previous", "volume_up", "volume_down", "mute")
MPRIS_METHODS = {"play_pause": "PlayPause", "next": "Next", "previous": "Previous"}
VOLUME_STEP = "2%"


@dataclass(frozen=True)
class Window:
    app: str  # класс программы: google-chrome, org.telegram.desktop, code…
    title: str
    pid: int
    fullscreen: bool = False
    desktop: bool = False  # рабочий стол (обои и значки)


def idle_seconds() -> float:
    """Сколько секунд не трогали мышь и клавиатуру."""
    return session.idle_seconds()


def foreground() -> Window | None:
    """Активное окно или None, если его нет (или сенсоры ядра не запущены)."""
    data = session.active_window()
    if not data:
        return None
    return Window(app=data.get("app", "?"), title=data.get("title", ""),
                  pid=int(data.get("pid") or 0), fullscreen=bool(data.get("fullscreen")),
                  desktop=bool(data.get("desktop")))


def is_desktop(window: Window | None) -> bool:
    return window is not None and window.desktop


def is_fullscreen(window: Window) -> bool:
    """Окно занимает весь монитор — игра, фильм, презентация."""
    return window.fullscreen


def lock() -> None:
    subprocess.run(["loginctl", "lock-session"], check=True, timeout=10)


def _players(connection) -> list[str]:
    names = connection.send_and_get_reply(message_bus.ListNames(), timeout=2).body[0]
    return [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]


def _playback_status(connection, player: str) -> str:
    address = DBusAddress("/org/mpris/MediaPlayer2", bus_name=player,
                          interface="org.freedesktop.DBus.Properties")
    reply = connection.send_and_get_reply(
        new_method_call(address, "Get", "ss", ("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
        timeout=2)
    return reply.body[0][1] if reply.body else ""


def _media(action: str) -> None:
    """Плеер, который играет сейчас; если никто не играет — первый на паузе."""
    with open_dbus_connection(bus="SESSION") as connection:
        players = _players(connection)
        if not players:
            raise RuntimeError("Не нашла запущенного плеера (музыка, видео в браузере).")
        statuses = {p: _playback_status(connection, p) for p in players}
        target = next((p for p, s in statuses.items() if s == "Playing"), None) \
            or next((p for p, s in statuses.items() if s == "Paused"), players[0])
        address = DBusAddress("/org/mpris/MediaPlayer2", bus_name=target,
                              interface="org.mpris.MediaPlayer2.Player")
        connection.send_and_get_reply(new_method_call(address, MPRIS_METHODS[action]), timeout=2)


def press_media_key(action: str, times: int = 1) -> None:
    times = max(1, min(times, 50))
    sink = "@DEFAULT_AUDIO_SINK@"
    if action in MPRIS_METHODS:
        for _ in range(times if action != "play_pause" else 1):
            _media(action)
    elif action == "mute":
        subprocess.run(["wpctl", "set-mute", sink, "toggle"], check=True, timeout=5)
    else:
        step = f"{int(VOLUME_STEP[:-1]) * times}%{'+' if action == 'volume_up' else '-'}"
        subprocess.run(["wpctl", "set-volume", "-l", "1.0", sink, step], check=True, timeout=5)
