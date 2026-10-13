"""Команды и открытие: PowerShell с классификацией «только чтение», запуск, Корзина."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

OUTPUT_CHARS = 8000

# Команды PowerShell, которые что-то меняют. Остальные (Get-*, Test-*, Select-* …) — только чтение.
MUTATING = re.compile(
    r"(?i)\b(remove|set|new|stop|start|restart|rename|move|copy|clear|install|uninstall|"
    r"invoke|add|disable|enable|out|export|import|register|unregister|update|reset|suspend|"
    r"resume|mount|dismount|push|pop|enter|send|write|save|lock|unlock|grant|revoke)-\w+"
    r"|\b(rm|del|erase|rd|rmdir|mv|move|cp|copy|ren|kill|taskkill|shutdown|reg|sc|net|"
    r"format|diskpart|bcdedit|iex|curl|wget|start|attrib|icacls|takeown|cipher|powercfg)\b"
    r"|>|\|\s*(sh|bash|cmd)\b")


# Команды, которые могут сломать систему, данные или доступ: их Аня выполняет только с «да».
DANGEROUS = re.compile(
    r"(?i)\b(format|diskpart|bcdedit|vssadmin|wbadmin|cipher\s+/w|bootrec|sfc|dism|"
    r"shutdown|logoff)\b"
    r"|\b(Format-Volume|Clear-Disk|Initialize-Disk|Remove-Partition|Resize-Partition|"
    r"Stop-Computer|Restart-Computer|Remove-LocalUser|Set-LocalUser|New-LocalUser|"
    r"Add-LocalGroupMember|Set-MpPreference|Add-MpPreference|Set-ExecutionPolicy|"
    r"Disable-ComputerRestore|Set-Service|Remove-Service)\b"
    r"|\bnet\s+(user|localgroup)\b|\breg\s+(delete|import)\b|Remove-Item\s+.*HKLM:"
    r"|\bTurnOff|DisableAntiSpyware|DisableRealtimeMonitoring")
PROTECTED_WORDS = re.compile(r"(?i)\\Windows\b|Program Files|Ubermensch|Übermensch|\.env\b")


def is_read_only(command: str) -> bool:
    return not MUTATING.search(command)


def is_dangerous(command: str) -> bool:
    """Опасно: ломает систему или доступ — или меняет что-то в системных папках, памяти и
    коде Ани. Остальное (в том числе меняющее) Аня делает сама."""
    return bool(DANGEROUS.search(command)) or (
        not is_read_only(command) and bool(PROTECTED_WORDS.search(command)))


def powershell(command: str, timeout: int = 120) -> str:
    """Выполняет команду и возвращает вывод (UTF-8, обрезанный)."""
    script = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {command}"
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    out = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    return (out or f"(нет вывода, код {done.returncode})")[:OUTPUT_CHARS]


def open_target(target: str) -> None:
    """Файл, папка, URL или программа по имени («notepad», «chrome»)."""
    if re.match(r"(?i)^https?://", target) or Path(target).exists():
        os.startfile(target)
    else:
        subprocess.Popen(["cmd", "/c", "start", "", target],
                         creationflags=subprocess.CREATE_NO_WINDOW)


def to_recycle_bin(path: str) -> None:
    """Удаление в Корзину — его можно отменить."""
    kind = "DeleteDirectory" if Path(path).is_dir() else "DeleteFile"
    literal = path.replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName Microsoft.VisualBasic; "
                    f"[Microsoft.VisualBasic.FileIO.FileSystem]::{kind}('{literal}', "
                    "'OnlyErrorDialogs', 'SendToRecycleBin')"],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
