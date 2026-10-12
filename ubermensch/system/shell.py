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


def is_read_only(command: str) -> bool:
    return not MUTATING.search(command)


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
