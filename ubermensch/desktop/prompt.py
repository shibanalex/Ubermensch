"""Окно запроса: маленькая консоль, куда пишешь просьбу, вопрос или просто болтаешь."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout

from . import console
from .console import ACCENT, BRIGHT, DIM, MONO, TITLE_H

WIDTH, HEIGHT = 500, 150


class PromptEdit(QPlainTextEdit):
    """Enter: отправить, Shift+Enter; новая строка, Esc; закрыть."""
    submitted = Signal()
    cancelled = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class PromptWindow(console.ConsoleWindow):
    submitted = Signal(str)
    open_history = Signal()

    def __init__(self):
        super().__init__("anya.exe — запрос")
        self.resize(WIDTH, HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, TITLE_H + 8, 12, 8)
        layout.setSpacing(4)

        row = QHBoxLayout()
        prompt = QLabel(r"C:\anya>")
        prompt.setFont(QFont(MONO, 10.5))
        prompt.setStyleSheet(f"color: {ACCENT.name()};")
        self.edit = PromptEdit()
        self.edit.setFont(QFont(MONO, 10.5))
        self.edit.setCursorWidth(8)  # блочный курсор, как в консоли
        self.edit.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {BRIGHT.name()};"
            f"selection-background-color: {console.SELECTION}; }}" + console.SCROLLBAR)
        self.edit.submitted.connect(self._submit)
        self.edit.cancelled.connect(self.hide)
        row.addWidget(prompt, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.edit, 1)
        layout.addLayout(row, 1)

        hint = QLabel("Enter — отправить · Shift+Enter — новая строка · Esc — закрыть")
        hint.setFont(QFont(MONO, 8))
        hint.setStyleSheet(f"color: {DIM.name()};")
        layout.addWidget(hint)

        self.button_clicked.connect(
            lambda name: self.open_history.emit() if name == "maximize" else self.hide())

    def toggle(self, anchor: QRect) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.popup(anchor)

    def popup(self, anchor: QRect) -> None:
        """Появляется над оверлеем (или под ним, если сверху нет места)."""
        area = self.screen().availableGeometry()
        x = max(area.left() + 4, min(anchor.center().x() - WIDTH // 2, area.right() - WIDTH - 4))
        y = anchor.top() - HEIGHT - 8
        if y < area.top():
            y = anchor.bottom() + 8
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def _submit(self) -> None:
        text = self.edit.toPlainText().strip()
        if text:
            self.edit.clear()
            self.hide()
            self.submitted.emit(text)
