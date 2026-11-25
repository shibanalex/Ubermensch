"""Снимок экрана с фильтром приватности.

Снимок не делается, если на экране может быть то, что нельзя видеть даже ассистенту:
банк, пароли, приватный режим браузера, госуслуги; и если идёт полноэкранная игра или фильм.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from . import windows

# заголовки и программы, при которых экран не снимаем
SENSITIVE = re.compile(
    r"(?i)банк|bank|сбер|sber|т-банк|тинькофф|tinkoff|альфа|alfa|втб|vtb|озон банк|"
    r"госуслуги|gosuslugi|налог|nalog|парол|password|1password|bitwarden|keepass|lastpass|"
    r"инкогнито|incognito|inprivate|private brows|приватн|вход в аккаунт|sign in|войти|"
    r"оплат|payment|checkout|карт[аы] |cvv|двухфактор|2fa|код подтверждения")
# класс окна в нижнем регистре: часть имени достаточна («org.keepassxc.keepassxc» → keepassxc)
SENSITIVE_APPS = ("keepass", "1password", "bitwarden", "seahorse", "kwalletmanager", "remmina",
                  "krdc", "reddy")


def skip_reason(window: windows.Window | None) -> str | None:
    """Почему сейчас снимать нельзя (None: можно)."""
    if window is None:
        return "нет активного окна"
    app = window.app.casefold()
    if any(name in app for name in SENSITIVE_APPS) or SENSITIVE.search(window.title):
        return "на экране может быть личное"
    if windows.is_fullscreen(window) and not windows.is_desktop(window):
        return "полноэкранный режим"
    return None


def capture(path: Path, max_width: int = 1600) -> Path:
    """Снимок экрана в JPEG (уменьшенный: для Claude хватает и бережёт лимит).

    Снимает Spectacle: на Wayland экран отдаёт только композитор, а Spectacle KWin
    доверяет: без диалога «разрешить запись экрана»."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "screen.png"
        subprocess.run(["spectacle", "--background", "--nonotify", "--fullscreen",
                        "--output", str(raw)], capture_output=True, timeout=30, check=True)
        image = Image.open(raw)
        image.load()
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=80)
    return path
