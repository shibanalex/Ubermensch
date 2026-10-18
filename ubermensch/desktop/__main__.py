"""Приложение на ПК: python -m ubermensch.desktop (ядро запускается отдельно).

Это присмотр за процессом маскота (mascot_app — мордочка, пузыри, запрос, история, трей):
упал — поднимается снова с растущей паузой; «Выход» у маскота (код 0) закрывает всё.
Список задач — отдельный виджет Plasma (plasmoid/, ставится scripts/install_widget.sh).
"""
from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

from .. import config
from .link import ALREADY_RUNNING

CHILDREN = {"маскот": "ubermensch.desktop.mascot_app"}  # задачи — виджет Plasma (plasmoid/)
MAIN = "маскот"  # его «Выход» закрывает приложение
BACKOFF = (2, 5, 15, 60)  # пауза перед перезапуском после 1-го, 2-го, ... падения подряд
STABLE_S = 300  # проработал столько — счётчик падений сбрасывается
# Окна Ани ходят по экрану и сами выбирают место — на Wayland так нельзя, поэтому они
# работают через XWayland. Остальное (активное окно, простой) — от сенсоров ядра.
CHILD_ENV = {**os.environ, "QT_QPA_PLATFORM": "xcb"}

log = logging.getLogger("desktop")


def _single_instance():
    """Блокировка файла в каталоге сеанса: второй присмотр не стартует."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    handle = open(os.path.join(runtime, "ubermensch-desktop.lock"), "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _log_file():
    """Лог приложения в папке данных; если её пока нет (диск не подключён) — без файла."""
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
            env=CHILD_ENV)
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
