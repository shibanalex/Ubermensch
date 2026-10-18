"""Команды и открытие: bash с классификацией «только чтение», запуск программ, Корзина."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

OUTPUT_CHARS = 8000

# Команды, которые что-то меняют. Остальные (ls, cat, grep, ps, df, journalctl …) — только чтение.
MUTATING = re.compile(
    r"(?i)(^|[;&|(`]|\$\()\s*(sudo\s+)?(rm|rmdir|mv|cp|mkdir|touch|ln|chmod|chown|chgrp|dd|"
    r"truncate|shred|kill|pkill|killall|xkill|systemctl|loginctl|apt|apt-get|dpkg|snap|flatpak|"
    r"pip|pipx|npm|git|tee|install|rsync|scp|curl|wget|crontab|kwriteconfig6|gsettings|dconf|"
    r"nmcli|rfkill|xdg-settings|update-alternatives|ufw|mount|umount|swapoff|swapon|tar|unzip|"
    r"7z|gio|kioclient6|trash|reboot|poweroff|shutdown|halt)\b"
    r"|\bsed\s+(-\w*\s+)*-i|>|\|\s*(sh|bash|zsh|python3?)\b")

# Команды, которые могут сломать систему, данные или доступ: их Аня выполняет только с «да».
DANGEROUS = re.compile(
    r"(?i)\b(sudo|su|pkexec|doas|mkfs(\.\w+)?|fdisk|sfdisk|gdisk|parted|wipefs|cryptsetup|"
    r"dd|shred|shutdown|reboot|poweroff|halt|useradd|userdel|usermod|passwd|chpasswd|visudo|"
    r"grub-install|update-grub|efibootmgr)\b"
    r"|\bsystemctl\s+(--user\s+)?(poweroff|reboot|halt|disable|mask|stop|kill|isolate|"
    r"rescue|emergency)\b"
    r"|\b(apt|apt-get|dpkg|snap|flatpak)\s+(remove|purge|autoremove|-r|-P|--purge)\b"
    r"|\bufw\s+disable\b|\bcrontab\s+-r\b|\brm\s+(-\w+\s+)*/\s*($|[;&|])"
    r"|>\s*/dev/(sd|nvme|mmcblk)|:\(\)\s*\{")
PROTECTED_WORDS = re.compile(
    r"(?i)(^|[\s'\"=])/(etc|boot|usr|bin|sbin|lib|lib64|opt|var|root|sys|proc)\b"
    r"|Ubermensch|Übermensch|\.env\b|\.ssh\b|\.gnupg\b")


def is_read_only(command: str) -> bool:
    return not MUTATING.search(command)


def is_dangerous(command: str) -> bool:
    """Опасно: ломает систему или доступ — или меняет что-то в системных папках, памяти и
    коде Ани. Остальное (в том числе меняющее) Аня делает сама."""
    return bool(DANGEROUS.search(command)) or (
        not is_read_only(command) and bool(PROTECTED_WORDS.search(command)))


def run(command: str, timeout: int = 120) -> str:
    """Выполняет команду в bash и возвращает вывод (обрезанный)."""
    done = subprocess.run(["bash", "-c", command], capture_output=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, cwd=Path.home(),
                          env={**os.environ, "LC_ALL": os.environ.get("LANG", "C.UTF-8")})
    out = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    return (out or f"(нет вывода, код {done.returncode})")[:OUTPUT_CHARS]


def _detached(args: list[str]) -> None:
    """Программа живёт сама по себе: не дочерний процесс ядра и без его вывода."""
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _desktop_entries() -> list[Path]:
    dirs = [Path.home() / ".local/share"] + [
        Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    dirs += [Path("/var/lib/snapd/desktop"), Path("/var/lib/flatpak/exports/share"),
             Path.home() / ".local/share/flatpak/exports/share"]
    return [f for d in dirs if (d / "applications").is_dir()
            for f in (d / "applications").rglob("*.desktop")]


def _find_app(name: str) -> Path | None:
    """Ярлык программы: по имени файла («org.telegram.desktop»), затем по Name= («Telegram»)."""
    needle = name.casefold().removesuffix(".desktop")
    entries = _desktop_entries()
    for entry in entries:
        if needle == entry.stem.casefold() or needle == entry.stem.casefold().rsplit(".", 1)[-1]:
            return entry
    for entry in entries:
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "NoDisplay=true" in text:
            continue
        names = re.findall(r"(?m)^(?:Name|Name\[ru\]|GenericName\[ru\])=(.+)$", text)
        if any(needle == n.strip().casefold() for n in names):
            return entry
    for entry in entries:
        if needle in entry.stem.casefold():
            return entry
    return None


def open_target(target: str) -> None:
    """Файл, папка, URL или программа по имени («chrome», «telegram», «kate»)."""
    path = Path(target).expanduser()
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", target) or path.exists():
        _detached(["xdg-open", str(path) if path.exists() else target])
    elif entry := _find_app(target):
        _detached(["gio", "launch", str(entry)])
    elif shutil.which(target):
        _detached([target])
    else:
        raise FileNotFoundError(f"Не нашла программу или файл «{target}».")


def to_recycle_bin(path: str) -> None:
    """Удаление в Корзину — его можно отменить."""
    subprocess.run(["gio", "trash", "--", path], check=True, capture_output=True, timeout=60)
