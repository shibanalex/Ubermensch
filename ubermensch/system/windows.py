"""Окна и ввод Windows: активное окно, простой, полноэкранность, блокировка, медиаклавиши."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetForegroundWindow.restype = wintypes.HWND
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetTickCount.restype = wintypes.DWORD
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2

MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "volume_up": 0xAF,
              "volume_down": 0xAE, "mute": 0xAD}


@dataclass(frozen=True)
class Window:
    app: str  # имя программы без .exe
    title: str
    hwnd: int


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def idle_seconds() -> float:
    """Сколько секунд не трогали мышь и клавиатуру."""
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    _user32.GetLastInputInfo(ctypes.byref(info))
    return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # счётчик 32-битный


def foreground() -> Window | None:
    """Активное окно или None, если его нет."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    title = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, title, 512)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = "?"
    process = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if process:
        path = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            app = Path(path.value).stem
        _kernel32.CloseHandle(process)
    return Window(app, title.value, int(hwnd))


def is_fullscreen(window: Window) -> bool:
    """Окно занимает весь монитор — игра, фильм, презентация."""
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(window.hwnd, ctypes.byref(rect)):
        return False
    monitor = _user32.MonitorFromWindow(window.hwnd, MONITOR_DEFAULTTONEAREST)
    info = _MonitorInfo(ctypes.sizeof(_MonitorInfo))
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    return (rect.left <= screen.left and rect.top <= screen.top
            and rect.right >= screen.right and rect.bottom >= screen.bottom)


def lock() -> None:
    _user32.LockWorkStation()


def press_media_key(action: str, times: int = 1) -> None:
    key = MEDIA_KEYS[action]
    for _ in range(max(1, min(times, 50))):
        _user32.keybd_event(key, 0, 0, 0)
        _user32.keybd_event(key, 0, 2, 0)  # KEYEVENTF_KEYUP
