"""Окна-консоли в палитре Nord: полупрозрачное тело Polar Night, тёмный заголовок «>_»,
кнопки ─ ☐ ✕. Фон за окном размывает KWin."""
from __future__ import annotations

import re

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QRect, QRectF, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme

MONO = "Hack"  # моноширинный, похож на Consolas; запасной — любой моно из системы
# палитра Nord (nordtheme.com)
BG = QColor(46, 52, 64, 236)  # nord0, чуть прозрачный
FG = QColor("#d8dee9")
BRIGHT = QColor("#eceff4")
DIM = QColor("#7b88a1")
ACCENT = QColor("#88c0d0")  # Frost — лицо Ани, приглашение ввода
GREEN = QColor("#a3be8c")
RED = QColor("#bf616a")
TITLE_BG = QColor(36, 41, 51, 245)
BORDER = QColor("#4c566a")
BUTTON_HOVER = QColor("#3b4252")
CLOSE_HOVER = QColor("#bf616a")
SELECTION = "#5e81ac"

TITLE_H, BUTTON_W = 28, 34
SHOW_MS, HIDE_MS, SLIDE = 180, 130, 12  # появление: проявляется и всплывает на SLIDE пикселей
BUTTONS = ("hide", "maximize", "close")  # слева направо
GLYPHS = {"hide": "─", "maximize": "☐", "close": "✕"}

SCROLLBAR = f"""
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #4c566a; border-radius: 4px; min-height: 30px; }}
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
        self._showing: QParallelAnimationGroup | None = None
        self._fading: QPropertyAnimation | None = None
        self._gliding: QPropertyAnimation | None = None

    # --- плавность: окна проявляются, тают и переезжают, а не прыгают ---

    def _animation(self, prop: bytes, start, end, ms: int,
                   curve=QEasingCurve.Type.OutCubic) -> QPropertyAnimation:
        animation = QPropertyAnimation(self, prop, self)
        animation.setDuration(ms)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(curve)
        return animation

    def _stop_animations(self) -> None:
        for name in ("_showing", "_fading"):
            if (animation := getattr(self, name)) is not None:
                animation.stop()
                setattr(self, name, None)
        self.setWindowOpacity(1.0)

    def show(self) -> None:
        if self.isVisible() and self._fading is None:
            if self._showing is not None:  # окно переставили, пока оно всплывало
                self._stop_animations()
            super().show()
            return
        self._stop_animations()
        target = self.pos()
        self.setWindowOpacity(0.0)
        self.move(target + QPoint(0, SLIDE))
        super().show()
        group = QParallelAnimationGroup(self)
        group.addAnimation(self._animation(b"windowOpacity", 0.0, 1.0, SHOW_MS))
        group.addAnimation(self._animation(b"pos", target + QPoint(0, SLIDE), target, SHOW_MS))
        group.finished.connect(lambda: setattr(self, "_showing", None))
        self._showing = group
        group.start()

    def hide(self) -> None:
        if not self.isVisible() or self._fading is not None:
            return
        if self._showing is not None:
            self._showing.stop()
            self._showing = None
        fading = self._animation(b"windowOpacity", self.windowOpacity(), 0.0, HIDE_MS,
                                 QEasingCurve.Type.InQuad)

        def done() -> None:
            self._fading = None
            super(ConsoleWindow, self).hide()
            self.setWindowOpacity(1.0)

        fading.finished.connect(done)
        self._fading = fading
        fading.start()

    def glide(self, target: QPoint, ms: int = 260,
              curve=QEasingCurve.Type.InOutSine) -> QPropertyAnimation:
        """Плавно переехать в target (прогулка маскота, перестановка окон)."""
        if self._gliding is not None:
            self._gliding.stop()
        self._gliding = self._animation(b"pos", self.pos(), target, ms, curve)
        self._gliding.start()
        return self._gliding

    def stop_glide(self) -> None:
        if self._gliding is not None:
            self._gliding.stop()
            self._gliding = None

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
        window.addRoundedRect(frame, 12, 12)
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
