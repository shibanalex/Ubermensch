"""Лист задач: бумажный лист, приклеенный к рабочему столу.

Он живёт под окнами программ, а когда пользователь переходит на рабочий стол
(клик по обоям, Win+D), поднимается над ним, чтобы его было видно.
"""
from __future__ import annotations

import ctypes
import html
from datetime import date

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QContextMenuEvent, QFont, QFontMetrics, QGuiApplication,
                           QMouseEvent, QPainter, QPen)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..system import windows
from . import theme
from .client import Api

PAPER_W = 310
LEFT, RIGHT, TOP, BOTTOM = 44, 14, 26, 12  # отступы текста от краёв листа (слева — за полями)
BODY_W = PAPER_W - LEFT - RIGHT - 8
MAX_SCREEN_SHARE = 0.8
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
HWND_TOP, HWND_BOTTOM = None, ctypes.c_void_p(1)
SWP_NOSIZE_NOMOVE_NOACTIVATE = 0x0001 | 0x0002 | 0x0010


def _hand(size: float) -> QFont:
    return QFont(theme.HAND, size)


def _ddmm(day: str) -> str:
    return f"{day[8:10]}.{day[5:7]}"


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setFont(_hand(9.5))
    label.setStyleSheet(f"color: {theme.RED_INK}; padding-top: 4px;")
    return label


class TaskRow(QWidget):
    """Строка задачи: квадратик и текст. Клик по строке отмечает задачу."""

    BOX = 13

    def __init__(self, task: dict, today: str, on_toggle):
        super().__init__()
        self.done = task["status"] == "done"
        self._toggle = lambda: on_toggle(task["id"], "open" if self.done else "done")
        text = html.escape(task["title"])
        if self.done:
            text = f"<s>{text}</s>"
        elif task["due"] and task["due"] != today:
            color = theme.RED_INK if task["due"] < today else theme.PENCIL
            text += f" <span style='color:{color}; font-size:8pt'>· {_ddmm(task['due'])}</span>"
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setFont(_hand(9.5))
        self.label.setStyleSheet(f"color: {theme.PENCIL if self.done else theme.INK};")
        if task.get("notes"):
            self.setToolTip(task["notes"])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.BOX + 10, 0, 0, 5)
        layout.addWidget(self.label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        line_h = QFontMetrics(self.label.font()).height()
        top = (line_h - self.BOX) / 2 + 1
        p.setPen(QPen(QColor(theme.PAPER_LINE), 1))  # своя строка тетради под задачей
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.setPen(QPen(QColor(theme.INK), 1.3))
        p.drawRect(1, top, self.BOX, self.BOX)
        if self.done:  # галочка красными чернилами, чуть выходит за квадратик
            p.setPen(QPen(QColor(theme.RED_INK), 2.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(theme.ink_path([(3, top + 6), (6.5, top + 11), (16, top - 3)]))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()  # не даём листу начать перетаскивание

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self._toggle()


class TaskSheet(QWidget):
    plan_day = Signal()
    show_mascot = Signal()
    open_prompt = Signal()
    open_history = Signal()
    open_metrics = Signal()

    def __init__(self, api: Api):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.api = api
        self._settings = QSettings("Ubermensch", "desktop")
        self._drag: QPoint | None = None
        self._raised: bool | None = None

        self.content = QWidget(self)
        self.content.setStyleSheet(theme.SCROLLBAR + theme.FLAT_BUTTON)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(LEFT, TOP, RIGHT, BOTTOM)
        layout.setSpacing(6)

        header = QHBoxLayout()
        today = date.today()
        self.title = QLabel(f"{WEEKDAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}")
        self.title.setFont(_hand(13))
        self.title.setStyleSheet(f"color: {theme.INK};")
        plan = QPushButton("✦")
        plan.setFont(QFont(theme.FONT, 13))
        plan.setToolTip("Спланировать день с ИИ")
        plan.setCursor(Qt.CursorShape.PointingHandCursor)
        plan.clicked.connect(self.plan_day)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(plan)
        layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list = QVBoxLayout(self.body)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(4)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll)

        self.new_task = QLineEdit()
        self.new_task.setPlaceholderText("+ добавить задачу")
        self.new_task.setFont(_hand(10))
        self.new_task.setStyleSheet(theme.LINE_INPUT + "padding: 2px 0;")
        self.new_task.returnPressed.connect(self._add_task)
        layout.addWidget(self.new_task)

        api.state_changed.connect(self._render)
        self._render({})
        self._fit()
        self._place()

        layer = QTimer(self, interval=300)
        layer.timeout.connect(self._sync_layer)
        layer.start()

    # --- положение и слой рабочего стола ---

    def _place(self) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()
        saved = self._settings.value("sheet_pos")
        if isinstance(saved, QPoint) and any(
                s.availableGeometry().contains(saved) for s in QGuiApplication.screens()):
            self.move(saved)
        else:
            self.move(area.right() - self.width() - 270, area.top() + 30)  # левее маскота

    def _sync_layer(self) -> None:
        """Под окнами программ, но над обоями, когда пользователь смотрит на рабочий стол."""
        if not self.isVisible():
            return
        foreground = _user32.GetForegroundWindow()
        if foreground == int(self.winId()):
            self._raised = True  # с листом работают — он сверху сам
            return
        raise_it = windows.is_desktop(foreground)
        if raise_it != self._raised:
            self._raised = raise_it
            _user32.SetWindowPos(int(self.winId()), HWND_TOP if raise_it else HWND_BOTTOM,
                                 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_NOACTIVATE)

    def resizeEvent(self, event) -> None:
        self.content.setGeometry(theme.paper_rect(self).toRect())

    def paintEvent(self, event) -> None:
        theme.paint_paper(self, margin_x=34, tape=True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self._settings.setValue("sheet_pos", self.pos())

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction("✦ Спланировать день", self.plan_day.emit)
        menu.addAction("Спросить ИИ", self.open_prompt.emit)
        menu.addAction("История ответов", self.open_history.emit)
        menu.addAction("Метрики", self.open_metrics.emit)
        menu.addAction("Показать маскота", self.show_mascot.emit)
        menu.exec(event.globalPos())

    # --- содержимое ---

    def _add_task(self) -> None:
        title = self.new_task.text().strip()
        if title:
            self.api.add_task(title)
            self.new_task.clear()

    def _render(self, state: dict) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        today = date.today().isoformat()
        tasks = state.get("tasks", [])
        now_tasks = [t for t in tasks if t["status"] == "open" and (t["due"] or today) <= today]
        later = [t for t in tasks if t["status"] == "open" and (t["due"] or today) > today]
        done = [t for t in tasks if t["status"] == "done"]
        reminders = [r for r in state.get("reminders", []) if r["due_at"][:10] == today]

        def add_tasks(items: list[dict]) -> None:
            for task in items:
                self.list.addWidget(TaskRow(task, today, self.api.set_task))

        if not tasks and not reminders:
            hint = QLabel("Пока пусто. Нажми ✦ — спланируем день.")
            hint.setWordWrap(True)
            hint.setFont(_hand(10))
            hint.setStyleSheet(f"color: {theme.PENCIL};")
            self.list.addWidget(hint)
        add_tasks(now_tasks)
        if reminders:
            self.list.addWidget(_section("Напоминания"))
            for r in reminders:
                label = QLabel(f"◷ {r['due_at'][11:16]}  {r['text']}")
                label.setWordWrap(True)
                label.setFont(_hand(9.5))
                label.setStyleSheet(f"color: {theme.INK};")
                self.list.addWidget(label)
        if later:
            self.list.addWidget(_section("Позже"))
            add_tasks(later)
        if done:
            self.list.addWidget(_section("Сделано сегодня"))
            add_tasks(done)
        goals = state.get("goals", [])
        if goals:
            self.list.addWidget(_section("Цели"))
            for g in goals:
                label = QLabel(f"→ {g['title']}"
                               + (f" · до {_ddmm(g['deadline'])}" if g["deadline"] else ""))
                label.setWordWrap(True)
                label.setFont(_hand(9))
                label.setStyleSheet(f"color: {theme.PENCIL};")
                self.list.addWidget(label)
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """Лист растёт вместе со списком, но не выше 80% экрана — дальше прокрутка."""
        area = self.screen().availableGeometry()
        layout = self.content.layout()
        other_h = (TOP + BOTTOM + 2 * layout.spacing() + layout.itemAt(0).sizeHint().height()
                   + self.new_task.sizeHint().height())
        body_h = self.list.totalHeightForWidth(BODY_W)
        scroll_h = max(40, min(body_h + 4, int(area.height() * MAX_SCREEN_SHARE) - other_h))
        self.scroll.setFixedHeight(scroll_h)
        self.setFixedSize(PAPER_W + 2 * theme.SHADOW, other_h + scroll_h + 2 * theme.SHADOW)
        self.update()
