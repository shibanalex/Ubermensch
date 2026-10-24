"""Команды и открытие: bash с классификацией «только чтение», запуск программ, Корзина."""
from __future__ import annotations

import os
import asyncio
import signal
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


HARMLESS_REDIRECTS = re.compile(r"\d*>&\d+|&?\d*>>?\s*/dev/null\b")  # 2>/dev/null, 2>&1


def is_read_only(command: str) -> bool:
    """Ничего не меняет. Перенаправление в /dev/null и между потоками — не запись."""
    return not MUTATING.search(HARMLESS_REDIRECTS.sub(" ", command))


def is_dangerous(command: str) -> bool:
    """Опасно: ломает систему или доступ — или меняет что-то в системных папках, памяти и
    коде Ани. Остальное (в том числе меняющее) Аня делает сама."""
    return bool(DANGEROUS.search(command)) or (
        not is_read_only(command) and bool(PROTECTED_WORDS.search(command)))


# Управление рабочим столом и чтение состояния: на компьютере пользователя без подтверждения.
# Только сами эти программы — без перенаправлений, подстановок и цепочек с чем-то другим.
SAFE_HOST_COMMANDS = frozenset({
    "kwriteconfig6", "kreadconfig6", "qdbus6", "qdbus", "busctl", "wpctl", "pactl", "playerctl",
    "brightnessctl", "notify-send", "plasma-apply-colorscheme", "plasma-apply-desktoptheme",
    "plasma-apply-wallpaperimage", "plasma-apply-cursortheme", "kscreen-doctor", "xdg-open",
    "spectacle", "loginctl", "nmcli", "bluetoothctl", "powerprofilesctl", "date",
    "ls", "cat", "head", "tail", "grep", "rg", "find", "fd", "wc", "du", "df", "free", "uptime",
    "ps", "pgrep", "top", "lscpu", "lsblk", "lspci", "lsusb", "sensors", "upower", "journalctl",
    "systemctl", "uname", "whoami", "id", "which", "file", "stat", "echo", "printf", "sort",
    "uniq", "cut", "tr", "jq", "xdg-mime", "fc-list", "locate", "plocate",
})
# Секреты, которые не должны попасть в вывод без вопроса.
SECRETS = re.compile(r"(?i)id_(rsa|ed25519|ecdsa)|\.session\b|secrets?\b|\.password-store|keyring"
                     r"|wallet|Cookies|Login Data|\.netrc|credentials|token")
# systemctl/loginctl/nmcli/bluetoothctl меняют систему только этими подкомандами — их спрашиваем.
RISKY_SUBCOMMANDS = re.compile(
    r"(?i)\b(systemctl|loginctl)(\s+-\S+)*\s+(?!(status|show|is-active|is-enabled|is-failed|"
    r"list-units|list-timers|list-sessions|show-session|cat)\b)[a-z]|\bnmcli\s+\S+\s+(delete|down|modify)\b"
    r"|\bbluetoothctl\s+(remove|untrust)\b|\b(logout|reboot|shutdown|halt|suspend|hibernate)\b"
    r"|\bjournalctl\b.*--(vacuum|rotate|flush|relinquish)")


def runs_on_host_freely(command: str) -> bool:
    """Безопасная системная команда: каждая часть цепочки — из SAFE_HOST_COMMANDS, без записи
    в файлы, подстановок, sudo и упоминаний секретов, системных папок и кода Ани."""
    if re.search(r"[`<>]|\$\(|\$\{|\|\||(?<!&)&(?!&)|&&\s*$", command) or "\n" in command:
        return False
    if is_dangerous(command) or PROTECTED_WORDS.search(command) or SECRETS.search(command) \
            or RISKY_SUBCOMMANDS.search(command):
        return False
    if re.search(r"\bfind\b.*\s-(delete|exec|execdir|ok|okdir|fprint|fls|fprintf)\b", command):
        return False
    for part in re.split(r"\s*(?:;|&&|\|)\s*", command.strip()):
        words = part.split()
        if not words or Path(words[0]).name not in SAFE_HOST_COMMANDS:
            return False
    return True


def _command(command: str, sandbox: bool):
    from .. import config
    folder = config.DATA_DIR / 'workspace' / 'shell'
    folder.mkdir(parents=True, exist_ok=True)
    args = ['bash', '--noprofile', '--norc', '-c', command]
    if sandbox:
        executable = shutil.which('bwrap')
        if not executable:
            raise RuntimeError('Для sandbox нужен bubblewrap (bwrap). Команда не запущена.')
        prefix = [executable, '--unshare-all', '--die-with-parent', '--new-session',
                  '--ro-bind', '/usr', '/usr']
        for directory in ('/bin', '/sbin', '/lib', '/lib64'):
            path = Path(directory)
            if path.is_symlink():
                prefix += ['--symlink', os.readlink(path), directory]
            elif path.exists():
                prefix += ['--ro-bind', directory, directory]
        args = prefix + ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                         '--bind', str(folder), '/work', '--chdir', '/work',
                         '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                         '--setenv', 'HOME', '/work', '--', *args]
    # Do not pass tokens, proxy credentials or Python/bash startup hooks to children.
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(Path.home()),
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    for key in ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY'):
        if key in os.environ:
            environment[key] = os.environ[key]
    return args, folder, environment


def run(command: str, timeout: int = 120, *, sandbox: bool = False) -> str:
    """Synchronous diagnostic entry point; tools use cancellable run_async."""
    args, folder, environment = _command(command, sandbox)
    done = subprocess.run(args, capture_output=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, cwd=folder, env=environment)
    out = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    return (out or f"(нет вывода, код {done.returncode})")[:OUTPUT_CHARS]


async def run_async(command: str, timeout: int = 120, *, sandbox: bool = False) -> str:
    args, folder, environment = _command(command, sandbox)
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=folder, env=environment, start_new_session=True)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.CancelledError, TimeoutError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    out = output.decode('utf-8', errors='replace').strip()
    return (out or f'(нет вывода, код {process.returncode})')[:OUTPUT_CHARS]


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
