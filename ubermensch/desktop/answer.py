"""Консоль ответов: чёрное окно, куда бот «печатает» ответы. Выше — вся история, как в терминале."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizeGrip,
                               QVBoxLayout, QWidget)

from . import console
from .client import Api
from .console import ACCENT, BRIGHT, DIM, FG, GREEN, MONO, RED, TITLE_H, plain

PROMPT = r"C:\anya> "
CHARS_PER_TICK = 6  # скорость «печати»: ~375 символов в секунду
SPINNER = "|/-\\"


def _fmt(color: QColor) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(color)
    return f


class AnswerConsole(console.ConsoleWindow):
    typing = Signal(bool)  # маскот «говорит», пока идёт печать

    def __init__(self, api: Api):
        super().__init__("anya.exe — история")
        self.resize(560, 420)
        self.setMinimumSize(380, 220)
        self.api = api
        self._settings = QSettings("Ubermensch", "desktop")
        self._last_id = 0
        self._loaded = False  # историю при первой загрузке не «печатаем»
        self._shown_requests: list[str] = []  # запросы, выведенные до того, как ядро их записало
        self._queue: list[tuple[str, QTextCharFormat]] = []
        self._busy = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, TITLE_H + 6, 4, 4)
        layout.setSpacing(4)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont(MONO, 10.5))
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {FG.name()};"
            f"selection-background-color: {console.SELECTION}; }}" + console.SCROLLBAR)
        layout.addWidget(self.view, 1)

        self.approvals = QVBoxLayout()
        layout.addLayout(self.approvals)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setFont(QFont(MONO, 9))
        self.status.setStyleSheet(f"color: {DIM.name()};")
        bottom.addWidget(self.status, 1)
        bottom.addWidget(QSizeGrip(self), alignment=Qt.AlignmentFlag.AlignBottom
                         | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom)

        self._type_timer = QTimer(self, interval=16)
        self._type_timer.timeout.connect(self._type_tick)
        self._spin = 0
        spinner = QTimer(self, interval=120)
        spinner.timeout.connect(self._update_status)
        spinner.start()

        self.button_clicked.connect(lambda name: self.hide())
        self.moved.connect(lambda: self._settings.setValue("answer_pos", self.pos()))
        api.state_changed.connect(self._render)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # --- показ ---

    def open(self, anchor: QRect) -> None:
        if not self.isVisible():
            saved = self._settings.value("answer_pos")
            area = self.screen().availableGeometry()
            if isinstance(saved, QPoint) and area.contains(saved):
                self.move(saved)
            else:  # слева от маскота, низом на его уровне
                x = max(area.left() + 4, anchor.left() - self.width() - 12)
                y = max(area.top() + 4, anchor.bottom() - self.height())
                self.move(x, y)
        self.show()
        self.raise_()
        self._scroll_to_end()
        if self.api.online and self._loaded and not self.api.state.get("messages") \
                and not self._busy:
            self.begin_request()
            self.api.onboard(self.end_request)

    def show_request(self, text: str) -> None:
        """Сразу выводим запрос — ответ допечатается, когда придёт."""
        self._write("\n" + PROMPT, _fmt(ACCENT))
        self._write(text + "\n", _fmt(BRIGHT))
        self._shown_requests.append(text)

    def begin_request(self) -> None:
        self._busy += 1

    def end_request(self, data: dict) -> None:
        self._busy = max(0, self._busy - 1)
        if "error" in data:
            self._write(f"ошибка: {data['error']}\n", _fmt(RED))

    # --- вывод текста ---

    def _write(self, text: str, fmt: QTextCharFormat, animate: bool = False) -> None:
        if animate or self._queue:  # пока идёт печать, всё встаёт в очередь по порядку
            self._queue.append((text, fmt))
            if not self._type_timer.isActive():
                self._type_timer.start()
                self.typing.emit(True)
        else:
            self._insert(text, fmt)

    def _insert(self, text: str, fmt: QTextCharFormat) -> None:
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, fmt)
        if at_end:
            bar.setValue(bar.maximum())

    def _type_tick(self) -> None:
        if not self._queue:
            self._type_timer.stop()
            self.typing.emit(False)
            return
        text, fmt = self._queue[0]
        self._insert(text[:CHARS_PER_TICK], fmt)
        if len(text) > CHARS_PER_TICK:
            self._queue[0] = (text[CHARS_PER_TICK:], fmt)
        else:
            self._queue.pop(0)

    def _scroll_to_end(self) -> None:
        bar = self.view.verticalScrollBar()
        QTimer.singleShot(30, lambda: bar.setValue(bar.maximum()))

    # --- состояние ядра ---

    def _render(self, state: dict) -> None:
        new = [m for m in state.get("messages", []) if m["id"] > self._last_id]
        animate = self._loaded and self.isVisible()
        for m in new:
            self._write_message(m, animate)
            self._last_id = m["id"]
        self._loaded = True
        self._render_approvals(state.get("approvals", []))

    def _write_message(self, m: dict, animate: bool) -> None:
        if m["role"] == "user":
            if m["text"] in self._shown_requests:  # уже выведен в момент отправки
                self._shown_requests.remove(m["text"])
                return
            self._write("\n" + PROMPT, _fmt(ACCENT))
            self._write(m["text"], _fmt(BRIGHT))
            self._write("  [telegram]\n" if m.get("channel") == "tg" else "\n", _fmt(DIM))
            return
        self._write(plain(m["text"]) + "\n", _fmt(FG), animate)
        for note in m.get("notes") or []:
            self._write(f"  + {note}\n", _fmt(DIM))

    def _render_approvals(self, approvals: list[dict]) -> None:
        while self.approvals.count():
            item = self.approvals.takeAt(0)
            if item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        for a in approvals:
            row = QHBoxLayout()
            details = (a.get("details") or "")[:600]
            label = QLabel(f"[!] Подтвердить: {a['summary']}"
                           + (f"\n{details}" if details else ""))
            label.setWordWrap(True)
            label.setFont(QFont(MONO, 9.5))
            label.setStyleSheet(f"color: {BRIGHT.name()};")
            row.addWidget(label, 1)
            for text, approve, color in (("[ да ]", True, GREEN), ("[ нет ]", False, RED)):
                button = QPushButton(text)
                button.setFont(QFont(MONO, 9.5))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: none; color: {color.name()}; }}"
                    f"QPushButton:hover {{ text-decoration: underline; }}")
                button.clicked.connect(lambda _=False, i=a["id"], ok=approve: self.api.decide(
                    i, ok, lambda data: self._write(
                        f"{data.get('result') or data.get('error', '')}\n", _fmt(DIM))))
                row.addWidget(button)
            self.approvals.addLayout(row)

    def _update_status(self) -> None:
        if not self.api.online:
            text = PROMPT + "нет связи с ядром"
        elif self.api.state.get("thinking") or self._busy:
            self._spin = (self._spin + 1) % len(SPINNER)
            text = PROMPT + "думаю " + SPINNER[self._spin]
        else:
            text = PROMPT + "_"
        if self.status.text() != text:
            self.status.setText(text)
