"""Процесс маскота (oswald.exe): мордочка, реплики-пузыри, окно запроса, история, трей.

Запускается присмотром (python -m ubermensch.desktop). «Выход» закрывает приложение целиком:
код 0 говорит присмотру, что это решение пользователя, а не падение.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import link, theme
from .answer import AnswerConsole
from .cat import Mascot, icon_pixmap
from .client import Api
from .prompt import PromptWindow

PLAN_DAY = ("Спланируй мой сегодняшний день: предложи 3–5 задач под мои цели "
            "с учётом уже открытых задач и напоминаний.")


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("oswald.exe")
    app.setFont(QFont(theme.FONT, 10))
    if link.is_running(link.MASCOT):
        link.send("show")  # второй запуск просто показывает уже живого маскота
        return link.ALREADY_RUNNING

    api = Api()
    answer = AnswerConsole(api)
    prompt = PromptWindow()
    mascot = Mascot(api)
    mascot.answer_visible = answer.isVisible
    mascot.engaged = lambda: prompt.isVisible() or answer.isVisible()

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

    commands = {"show": show_mascot, "prompt": lambda: prompt.popup(anchor()),
                "history": lambda: answer.open(anchor()),
                "plan": lambda: ask(PLAN_DAY)}
    link.serve(link.MASCOT, app, lambda command: commands[command]())

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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
