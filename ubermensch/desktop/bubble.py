"""Реплика маскота: мини-окно консоли с хвостиком, как пузырь в комиксе.

Появляется над маскотом, текст печатается по буквам. Висит, пока его читают:
наведённая мышь не даёт ему исчезнуть. ☐ открывает полную историю.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from . import console
from .console import DIM, FG, MONO, TITLE_H

TAIL_H = 16
MIN_W, MAX_W = 230, 420
PAD = 10
CHARS_PER_TICK = 4  # ~250 символов в секунду


def _fmt(color: QColor) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(color)
    return f


class ReplyBubble(console.ConsoleWindow):
    typing = Signal(bool)
    open_history = Signal()

    def __init__(self):
        super().__init__("anya.exe")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._tail_x = 40
        self._queue: list[tuple[str, QTextCharFormat]] = []
        self._hold_ms = 10_000

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setFont(QFont(MONO, 10))
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {FG.name()};"
            f"selection-background-color: #2e5f3e; }}" + console.SCROLLBAR)

        self._type_timer = QTimer(self, interval=16)
        self._type_timer.timeout.connect(self._type_tick)
        self._hide_timer = QTimer(self, singleShot=True)
        self._hide_timer.timeout.connect(self.hide)
        self.button_clicked.connect(
            lambda name: self.open_history.emit() if name == "maximize" else self.hide())

    # --- форма: окно + хвостик к маскоту ---

    def frame_rect(self) -> QRect:
        return self.rect().adjusted(0, 0, 0, -TAIL_H)

    def tail_path(self) -> QPainterPath:
        bottom, x = self.frame_rect().bottom(), self._tail_x
        path = QPainterPath(QPointF(x - 14, bottom - 2))
        path.lineTo(QPointF(x + 4, bottom + TAIL_H - 1))
        path.lineTo(QPointF(x + 6, bottom - 2))
        path.closeSubpath()
        return path

    # --- показ ---

    def show_text(self, text: str, anchor: QWidget, *, notes: list[str] = (),
                  title: str = "anya.exe", color: QColor = FG, typewriter: bool = True,
                  hold_seconds: float | None = None) -> None:
        self.title = title
        metrics = QFontMetrics(self.view.font())
        lines = text.splitlines() + [f"+ {n}" for n in notes]
        widest = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        width = max(MIN_W, min(MAX_W, widest + 2 * PAD + 20))
        text_w = width - 2 * PAD - 16  # поля документа и место под полосу прокрутки
        full = "\n".join(lines)
        text_h = metrics.boundingRect(QRect(0, 0, text_w, 100_000),
                                      Qt.TextFlag.TextWordWrap, full).height()

        area = anchor.screen().availableGeometry()
        room = anchor.y() - area.top() - TITLE_H - TAIL_H - 2 * PAD - 8
        body_h = max(60, min(text_h + metrics.height(), max(room, area.height() // 3)))
        height = TITLE_H + body_h + 2 * PAD + TAIL_H
        self.resize(width, height)
        self.view.setGeometry(PAD, TITLE_H + PAD // 2, width - PAD - 4, body_h + PAD)

        # над маскотом, хвостик смотрит на его середину
        x = anchor.x() + anchor.width() // 2 - width + 70
        x = max(area.left() + 4, min(x, area.right() - width - 4))
        y = max(area.top() + 4, anchor.y() - height + 8)
        self.move(x, y)
        self._tail_x = max(24, min(anchor.x() + anchor.width() // 2 - x, width - 24))

        self.view.clear()
        self._queue.clear()
        self._write(text, _fmt(color))
        for note in notes:
            self._write(f"\n+ {note}", _fmt(DIM))
        self._hold_ms = int((hold_seconds if hold_seconds is not None
                             else min(120.0, max(8.0, 6 + len(full) / 18))) * 1000)
        if not typewriter:
            while self._queue:
                self._insert(*self._queue.pop(0))
        self._hide_timer.stop()
        self.show()
        self.raise_()
        self.update()
        if self._queue:
            self._type_timer.start()
            self.typing.emit(True)
        else:
            self._hide_timer.start(self._hold_ms)

    def _write(self, text: str, fmt: QTextCharFormat) -> None:
        self._queue.append((text, fmt))

    def _insert(self, text: str, fmt: QTextCharFormat) -> None:
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, fmt)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _type_tick(self) -> None:
        if not self._queue:
            self._type_timer.stop()
            self.typing.emit(False)
            if not self.underMouse():
                self._hide_timer.start(self._hold_ms)
            return
        text, fmt = self._queue[0]
        self._insert(text[:CHARS_PER_TICK], fmt)
        if len(text) > CHARS_PER_TICK:
            self._queue[0] = (text[CHARS_PER_TICK:], fmt)
        else:
            self._queue.pop(0)

    def hideEvent(self, event) -> None:
        if self._type_timer.isActive():
            self._type_timer.stop()
            self._queue.clear()
            self.typing.emit(False)
        super().hideEvent(event)

    # --- пока читают, не исчезает ---

    def enterEvent(self, event) -> None:
        self._hide_timer.stop()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if not self._type_timer.isActive():
            self._hide_timer.start(5000)
