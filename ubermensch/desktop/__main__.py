"""Приложение на ПК: python -m ubermensch.desktop (ядро запускается отдельно).

Это присмотр за двумя процессами: маскот (mascot_app — мордочка, пузыри, запрос, история,
трей) и лист задач (sheet_app). Упал один — второй живёт, а упавший поднимается снова
с растущей паузой. «Выход» у маскота (код 0) закрывает всё.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

from .. import config
from .link import ALREADY_RUNNING

CHILDREN = {"маскот": "ubermensch.desktop.mascot_app",
            "лист задач": "ubermensch.desktop.sheet_app"}
MAIN = "маскот"  # его «Выход» закрывает приложение
BACKOFF = (2, 5, 15, 60)  # пауза перед перезапуском после 1-го, 2-го, ... падения подряд
STABLE_S = 300  # проработал столько — счётчик падений сбрасывается
ERROR_ALREADY_EXISTS = 183

log = logging.getLogger("desktop")


def _single_instance():
    """Именованный мьютекс Windows: второй присмотр не стартует."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ubermensch-desktop")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def _log_file():
    """Лог приложения на D:; если диск ещё заблокирован — без файла."""
    try:
        folder = config.DATA_DIR / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "desktop.log", maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        return open(folder / "desktop-stderr.log", "a", encoding="utf-8")
    except OSError:
        return subprocess.DEVNULL


class Child:
    def __init__(self, name: str, module: str, stderr):
        self.name, self.module, self.stderr = name, module, stderr
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.failures = 0
        self.restart_at = 0.0

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", self.module], stdout=subprocess.DEVNULL, stderr=self.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.started = time.monotonic()
        log.info("Запущен %s (pid %d)", self.name, self.proc.pid)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def check(self) -> int | None:
        """None — работает (или ждёт перезапуска), иначе код выхода."""
        if self.proc is None:
            if time.monotonic() >= self.restart_at:
                self.start()
            return None
        code = self.proc.poll()
        if code is None:
            return None
        self.proc = None
        if code == ALREADY_RUNNING:  # жив экземпляр от прошлого присмотра — подождать его
            self.restart_at = time.monotonic() + 30
            return None
        if code == 0:
            return 0
        lived = time.monotonic() - self.started
        self.failures = 1 if lived > STABLE_S else self.failures + 1
        pause = BACKOFF[min(self.failures, len(BACKOFF)) - 1]
        log.error("%s упал (код %s, проработал %d с) — перезапуск через %d с",
                  self.name, code, lived, pause)
        self.restart_at = time.monotonic() + pause
        return None


def main() -> None:
    if _single_instance() is None:
        return
    stderr = _log_file()
    children = [Child(name, module, stderr) for name, module in CHILDREN.items()]
    for child in children:
        child.start()
    try:
        while True:
            time.sleep(1)
            for child in children:
                if child.check() == 0:
                    if child.name == MAIN:  # пользователь нажал «Выход»
                        log.info("Выход по просьбе пользователя")
                        return
                    child.restart_at = time.monotonic() + BACKOFF[0]
    finally:
        for child in children:
            child.stop()


if __name__ == "__main__":
    main()
