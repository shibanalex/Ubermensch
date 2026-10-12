"""Снимок экрана с фильтром приватности.

Снимок не делается, если на экране может быть то, что нельзя видеть даже Освальду:
банк, пароли, приватный режим браузера, госуслуги; и если идёт полноэкранная игра или фильм.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import ImageGrab

from . import windows

# заголовки и программы, при которых экран не снимаем
SENSITIVE = re.compile(
    r"(?i)банк|bank|сбер|sber|т-банк|тинькофф|tinkoff|альфа|alfa|втб|vtb|озон банк|"
    r"госуслуги|gosuslugi|налог|nalog|парол|password|1password|bitwarden|keepass|lastpass|"
    r"инкогнито|incognito|inprivate|private brows|приватн|вход в аккаунт|sign in|войти|"
    r"оплат|payment|checkout|карт[аы] |cvv|двухфактор|2fa|код подтверждения")
SENSITIVE_APPS = {"KeePass", "KeePassXC", "1Password", "Bitwarden", "Reddy", "mstsc"}


def skip_reason(window: windows.Window | None) -> str | None:
    """Почему сейчас снимать нельзя (None — можно)."""
    if window is None:
        return "нет активного окна"
    if window.app in SENSITIVE_APPS or SENSITIVE.search(window.title):
        return "на экране может быть личное"
    if windows.is_fullscreen(window) and window.app not in ("explorer",):
        return "полноэкранный режим"
    return None


def capture(path: Path, max_width: int = 1600) -> Path:
    """Снимок основного монитора в JPEG (уменьшенный — для Claude хватает и бережёт лимит)."""
    image = ImageGrab.grab()
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=80)
    return path
