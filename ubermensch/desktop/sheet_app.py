"""Процесс листа задач: бумажный лист на рабочем столе. Просьбы к маскоту — через link."""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import link, theme
from .client import Api
from .sheet import TaskSheet


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("oswald.exe — задачи")
    app.setFont(QFont(theme.FONT, 10))
    if link.is_running(link.SHEET):
        return link.ALREADY_RUNNING
    link.serve(link.SHEET, app)  # только чтобы второй лист не запустился

    sheet = TaskSheet(Api())
    sheet.plan_day.connect(lambda: link.send("plan"))
    sheet.show_mascot.connect(lambda: link.send("show"))
    sheet.open_prompt.connect(lambda: link.send("prompt"))
    sheet.open_history.connect(lambda: link.send("history"))
    sheet.open_metrics.connect(lambda: link.send("metrics"))
    sheet.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
