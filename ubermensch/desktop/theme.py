"""Оформление «лист бумаги»: кремовая бумага, чернила, карандаш, линовка как в тетради."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

PAPER = "#fbf6e9"
PAPER_LINE = "#e9e0cc"  # линовка
MARGIN_LINE = "#e3aaa1"  # красная линия полей
INK = "#2d2a26"  # чернила / графит
PENCIL = "#8f877a"  # карандаш — второстепенное
RED_INK = "#a33a2c"  # красные чернила — акценты и твои реплики
GREEN_INK = "#4f7a3a"
TAPE = QColor(232, 219, 180, 200)

FONT = "Noto Sans"  # «напечатанный» текст
HAND = "Caveat"  # «от руки»: шрифт в desktop/fonts, регистрирует mascot_app

SHADOW = 10  # поле под тень вокруг листа

FLAT_BUTTON = f"""
QPushButton {{ background: transparent; color: {INK}; border: none; padding: 2px 6px; }}
QPushButton:hover {{ color: {RED_INK}; }}
QPushButton:disabled {{ color: {PENCIL}; }}
"""

SCROLLBAR = f"""
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {PAPER_LINE}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""

# Поле ввода — просто строка на бумаге с линией снизу.
LINE_INPUT = (f"background: transparent; color: {INK}; border: none;"
              f"border-bottom: 1px solid {PENCIL}; selection-background-color: {MARGIN_LINE};")

TOP_WINDOW = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.Tool)


def paper_rect(widget: QWidget) -> QRectF:
    return QRectF(widget.rect()).adjusted(SHADOW, SHADOW - 2, -SHADOW, -SHADOW - 2)


def paint_paper(widget: QWidget, *, ruled_from: float | None = None, step: float = 27,
                margin_x: float | None = 34, tape: bool = False) -> None:
    """Лист с мягкой тенью; по желанию — линовка, поля и полоска скотча сверху."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paper = paper_rect(widget)

    p.setPen(Qt.PenStyle.NoPen)
    for i in range(SHADOW):  # кольца с затуханием складываются в мягкую тень
        p.setBrush(QColor(60, 40, 20, int(9 * (1 - i / SHADOW) ** 2)))
        p.drawRoundedRect(paper.adjusted(-i, -i + 3, i, i + 3), 2 + i, 2 + i)
    p.setBrush(QColor(PAPER))
    p.drawRect(paper)

    if ruled_from is not None:
        p.setPen(QPen(QColor(PAPER_LINE), 1))
        y = paper.top() + ruled_from
        while y < paper.bottom() - 4:
            p.drawLine(QPointF(paper.left(), y), QPointF(paper.right(), y))
            y += step
    if margin_x is not None:
        p.setPen(QPen(QColor(MARGIN_LINE), 1.2))
        x = paper.left() + margin_x
        p.drawLine(QPointF(x, paper.top()), QPointF(x, paper.bottom()))
    if tape:
        p.save()
        p.translate(paper.center().x(), paper.top() + 2)
        p.rotate(-3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TAPE)
        p.drawRect(QRectF(-38, -11, 76, 22))
        p.restore()


class DragHandle(QWidget):
    """Область, за которую можно перетаскивать безрамочное окно."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._offset: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._offset is not None:
            self.window().move(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._offset = None


def ink_path(points: list[tuple[float, float]]) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    return path
