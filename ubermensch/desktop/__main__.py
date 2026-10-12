"""Приложение на ПК: python -m ubermensch.desktop (ядро запускается отдельно)."""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import theme
from .answer import AnswerConsole
from .cat import Mascot, icon_pixmap
from .client import Api
from .prompt import PromptWindow
from .sheet import TaskSheet

INSTANCE_KEY = "ubermensch-desktop"
PLAN_DAY = ("Спланируй мой сегодняшний день: предложи 3–5 задач под мои цели "
            "с учётом уже открытых задач и напоминаний.")


def _already_running() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_KEY)
    return socket.waitForConnected(300)


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("oswald.exe")
    app.setFont(QFont(theme.FONT, 10))
    if _already_running():
        return
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    api = Api()
    sheet = TaskSheet(api)
    answer = AnswerConsole(api)
    prompt = PromptWindow()
    mascot = Mascot(api)
    mascot.answer_visible = answer.isVisible

    def anchor():
        return mascot.frameGeometry()

    def ask(text: str) -> None:
        """Запрос уходит в ядро; ответ маскот скажет пузырём, а история пишется в консоль."""
        mascot.bubble.hide()
        answer.show_request(text)
        answer.begin_request()

        def done(data: dict) -> None:
            answer.end_request(data)
            if "error" in data:
                mascot.say(f"Не получилось: {data['error']}", title="Ошибка")

        api.chat(text, done)

    def show_mascot() -> None:
        mascot.show()
        mascot.raise_()

    mascot.clicked.connect(lambda: prompt.toggle(anchor()))
    mascot.open_history.connect(lambda: answer.open(anchor()))
    mascot.quit_requested.connect(app.quit)
    prompt.submitted.connect(ask)
    prompt.open_history.connect(lambda: answer.open(anchor()))
    answer.typing.connect(mascot.set_typing)
    sheet.plan_day.connect(lambda: ask(PLAN_DAY))
    sheet.show_mascot.connect(show_mascot)
    sheet.open_prompt.connect(lambda: prompt.popup(anchor()))
    sheet.open_history.connect(lambda: answer.open(anchor()))

    def on_event(event: dict) -> None:
        if event.get("type") == "approval":  # действие ждёт «да/нет» — показываем сразу
            answer.open(anchor())
            mascot.say(f"Нужно твоё «да»: {event.get('title', '')}", seconds=30,
                       title="Подтверждение")

    api.event.connect(on_event)

    icon = QIcon(icon_pixmap(64))
    app.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon)
    tray.setToolTip("oswald.exe")
    menu = QMenu()
    menu.addAction("Спросить", lambda: prompt.popup(anchor()))
    menu.addAction("История ответов", lambda: answer.open(anchor()))
    menu.addAction("Показать маскота", show_mascot)
    menu.addSeparator()
    menu.addAction("Выход", app.quit)
    tray.setContextMenu(menu)

    def on_tray_click(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return
        if mascot.isVisible():
            prompt.popup(anchor())
        else:
            show_mascot()

    tray.activated.connect(on_tray_click)
    tray.show()
    mascot.hidden_to_tray.connect(lambda: tray.showMessage(
        "oswald.exe", "Я спрятался в трей — значок :3 у часов (возможно, под стрелкой ^). "
        "Кликни по нему, чтобы вернуть меня.", icon, 8000))

    mascot.show()
    sheet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
