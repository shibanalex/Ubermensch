"""Окна в стиле консоли Windows: чёрное тело, тёмный заголовок «>_», кнопки ─ ☐ ✕."""
from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme

MONO = "Consolas"
BG = QColor("#0c0c0c")
FG = QColor("#cccccc")
BRIGHT = QColor("#f2f2f2")
DIM = QColor("#767676")
GREEN = QColor("#5af78e")
RED = QColor("#ff6b6b")
TITLE_BG = QColor("#1f1f1f")
BORDER = QColor("#3c3c3c")
BUTTON_HOVER = QColor("#3a3a3a")
CLOSE_HOVER = QColor("#c42b1c")

TITLE_H, BUTTON_W = 28, 34
BUTTONS = ("hide", "maximize", "close")  # слева направо
GLYPHS = {"hide": "─", "maximize": "☐", "close": "✕"}

SCROLLBAR = f"""
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a3a3a; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


def plain(markdown: str) -> str:
    """Консоль показывает простой текст: убираем разметку, списки — точками."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", markdown, flags=re.S)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^(\s*)[-*] ", r"\1• ", s, flags=re.M)
    return s.strip()


class ConsoleWindow(QWidget):
    """Безрамочное окно-консоль: перетаскивается за заголовок, кнопки шлют button_clicked."""
    button_clicked = Signal(str)  # hide / maximize / close
    moved = Signal()

    def __init__(self, title: str):
        super().__init__(None, theme.TOP_WINDOW)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.title = title
        self._hover: str | None = None
        self._drag: QPoint | None = None

    def buttons(self) -> dict[str, QRect]:
        right = self.width() - 1
        return {name: QRect(right - (len(BUTTONS) - i) * BUTTON_W, 1, BUTTON_W, TITLE_H - 1)
                for i, name in enumerate(BUTTONS)}

    def button_at(self, pos: QPoint) -> str | None:
        return next((name for name, rect in self.buttons().items() if rect.contains(pos)), None)

    def set_hover(self, name: str | None) -> None:
        if name != self._hover:
            self._hover = name
            self.update()

    # --- отрисовка ---

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_chrome(p)
        self.paint_body(p)

    def paint_body(self, p: QPainter) -> None:
        """Переопределяют окна, которые рисуют тело сами (маскот)."""

    def frame_rect(self) -> QRect:
        """Прямоугольник самого окна; пузырь оставляет под ним место для хвостика."""
        return self.rect()

    def tail_path(self) -> QPainterPath | None:
        """Хвостик, как у реплики в комиксе; у обычных окон его нет."""
        return None

    def paint_chrome(self, p: QPainter) -> None:
        frame = QRectF(self.frame_rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        window = QPainterPath()
        window.addRoundedRect(frame, 8, 8)  # скругление как в Windows 11
        if (tail := self.tail_path()) is not None:
            window = window.united(tail)
        p.fillPath(window, BG)
        p.save()
        p.setClipPath(window)
        p.fillRect(QRectF(0, 0, self.width(), TITLE_H), TITLE_BG)
        for name, rect in self.buttons().items():
            if name == self._hover:
                p.fillRect(rect, CLOSE_HOVER if name == "close" else BUTTON_HOVER)
        p.restore()
        p.setPen(QPen(BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(window)

        p.setPen(FG)
        p.setFont(QFont(MONO, 8, QFont.Weight.Bold))
        p.drawText(QRect(10, 0, 24, TITLE_H), Qt.AlignmentFlag.AlignVCenter, ">_")
        title_font = QFont("Segoe UI", 9)
        p.setFont(title_font)
        title_w = self.width() - 32 - len(BUTTONS) * BUTTON_W - 6
        p.drawText(QRect(32, 0, title_w, TITLE_H), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(title_font).elidedText(self.title, Qt.TextElideMode.ElideRight, title_w))
        p.setFont(QFont("Segoe UI Symbol", 9))
        for name, rect in self.buttons().items():
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, GLYPHS[name])

    # --- мышь: заголовок перетаскивает окно, кнопки нажимаются ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and pos.y() < TITLE_H \
                and self.button_at(pos) is None:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.set_hover(self.button_at(event.position().toPoint()))
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self.moved.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            button = self.button_at(event.position().toPoint())
            if button:
                self.button_clicked.emit(button)

    def leaveEvent(self, event) -> None:
        self.set_hover(None)
