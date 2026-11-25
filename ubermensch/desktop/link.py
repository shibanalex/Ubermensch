"""Связь с оверлеем (desktop HUD): виджет задач и другие программы просят его что-то сделать.

Команды идут строкой по локальному каналу Qt (сокет в каталоге сеанса, только этот
пользователь этого компьютера). Канал оверлея заодно не даёт запустить второго оверлея.
Из командной строки (так делает виджет Plasma): python -m ubermensch.desktop.link plan
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MASCOT = "ubermensch-mascot"
# показать оверлея, спросить, история, план дня, метрики
COMMANDS = ("show", "prompt", "history", "plan", "metrics")
ALREADY_RUNNING = 3  # код выхода второго экземпляра: присмотр не считает его падением


def is_running(key: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(key)
    running = socket.waitForConnected(300)
    socket.abort()
    return running


def serve(key: str, parent: QObject, handler: Callable[[str], None] | None = None) -> QLocalServer:
    """Слушать канал key; каждая полученная команда уходит в handler."""
    QLocalServer.removeServer(key)  # хвост от упавшего процесса
    server = QLocalServer(parent)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    server.listen(key)

    def accept() -> None:
        while (socket := server.nextPendingConnection()) is not None:
            def read(socket=socket) -> None:
                command = bytes(socket.readAll()).decode("utf-8", "replace").strip()
                if handler and command in COMMANDS:
                    handler(command)
                socket.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)

    server.newConnection.connect(accept)
    return server


def send(command: str, key: str = MASCOT) -> bool:
    """Отправить команду; False: адресат не запущен (присмотр его скоро поднимет)."""
    socket = QLocalSocket()
    socket.connectToServer(key)
    if not socket.waitForConnected(500):
        return False
    socket.write(command.encode())
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


if __name__ == "__main__":
    import sys

    from PySide6.QtCore import QCoreApplication

    QCoreApplication(sys.argv)
    command = sys.argv[1] if len(sys.argv) > 1 else "show"
    if command not in COMMANDS:
        sys.exit(f"Команды: {', '.join(COMMANDS)}")
    sys.exit(0 if send(command) else 1)
