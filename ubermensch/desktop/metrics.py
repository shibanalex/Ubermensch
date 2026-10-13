"""Окно метрик: «Освальд обо мне» — насколько он меня изучил, «Мои действия» — время за ПК,
полезное и отвлекающее, задачи и цели. Псевдографика в консоли, данные — GET /api/metrics."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeyEvent, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizeGrip,
                               QVBoxLayout)

from . import console
from .client import Api
from .console import BRIGHT, DIM, FG, GREEN, MONO, RED, TITLE_H

AMBER = QColor("#e5c07b")
GREY = QColor("#9a9a9a")
BAR_W = 22
LABEL_W = 30
SPARK = "▁▂▃▄▅▆▇█"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KINDS = (("useful", "полезное", GREEN), ("neutral", "нейтральное", GREY),
         ("distracting", "отвлекающее", RED), ("unknown", "не размечено", DIM))
REFRESH_MS = 60_000


def _fmt(color: QColor, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(color)
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    return f


def _bar(value: float, total: float, width: int = BAR_W) -> tuple[str, str]:
    filled = round(width * min(1.0, value / total)) if total else 0
    return "█" * filled, "░" * (width - filled)


def _spark(values: list[int]) -> str:
    top = max(values) or 1
    return "".join(" " if v == 0 else SPARK[min(7, int(v / top * 7.999))] for v in values)


def _hm(minutes: int) -> str:
    return f"{minutes // 60} ч {minutes % 60:02d} мин" if minutes >= 60 else f"{minutes} мин"


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _when(iso: str | None) -> str | None:
    return f"{iso[8:10]}.{iso[5:7]} {iso[11:16]}" if iso else None


class MetricsWindow(console.ConsoleWindow):
    TABS = (("learning", "Освальд обо мне"), ("activity", "Мои действия"))

    def __init__(self, api: Api):
        super().__init__("oswald.exe — метрики")
        self.resize(640, 600)
        self.setMinimumSize(460, 300)
        self.api = api
        self._settings = QSettings("Ubermensch", "desktop")
        self._tab = str(self._settings.value("metrics_tab", "learning"))
        self._data: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, TITLE_H + 6, 4, 4)
        layout.setSpacing(6)
        tabs = QHBoxLayout()
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, label in self.TABS:
            button = QPushButton(label)
            button.setFont(QFont(MONO, 10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, key=key: self._switch(key))
            self.tab_buttons[key] = button
            tabs.addWidget(button)
        tabs.addStretch()
        layout.addLayout(tabs)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont(MONO, 10))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {FG.name()};"
            f"selection-background-color: #2e5f3e; }}" + console.SCROLLBAR)
        layout.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setFont(QFont(MONO, 8))
        self.status.setStyleSheet(f"color: {DIM.name()};")
        bottom.addWidget(self.status, 1)
        bottom.addWidget(QSizeGrip(self), alignment=Qt.AlignmentFlag.AlignBottom
                         | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom)

        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(self.reload)
        self.button_clicked.connect(lambda name: self.hide())
        self.moved.connect(lambda: self._settings.setValue("metrics_pos", self.pos()))
        self._style_tabs()
        # Tab иначе уходит на переход фокуса между виджетами
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self, lambda: self._switch(
            "activity" if self._tab == "learning" else "learning"))

    # --- показ ---

    def open(self, anchor: QRect) -> None:
        if not self.isVisible():
            saved = self._settings.value("metrics_pos")
            area = self.screen().availableGeometry()
            if isinstance(saved, QPoint) and area.contains(saved):
                self.move(saved)
            else:  # левее маскота, низом на его уровне
                self.move(max(area.left() + 4, anchor.left() - self.width() - 12),
                          max(area.top() + 4, anchor.bottom() - self.height()))
        self.show()
        self.raise_()
        self.activateWindow()
        self.reload()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.key() == Qt.Key.Key_F5:
            self.reload()
        else:
            super().keyPressEvent(event)

    def _switch(self, tab: str) -> None:
        self._tab = tab
        self._settings.setValue("metrics_tab", tab)
        self._style_tabs()
        self._render()

    def _style_tabs(self) -> None:
        for key, button in self.tab_buttons.items():
            active = key == self._tab
            label = dict(self.TABS)[key]
            button.setText(f"[ {label} ]" if active else f"  {label}  ")
            color = GREEN if active else DIM
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {color.name()}; }}"
                f"QPushButton:hover {{ color: {BRIGHT.name()}; }}")

    def reload(self) -> None:
        self.status.setText("обновляю…")
        self.api.metrics(self._loaded)

    def _loaded(self, data: dict) -> None:
        if "error" in data:
            self.status.setText(f"нет данных: {data['error']}")
            return
        self._data = data
        self.status.setText(f"обновлено в {datetime.now():%H:%M} · F5 — обновить · "
                            "Tab — вкладка · Esc — закрыть")
        self._render()

    # --- вывод ---

    def _render(self) -> None:
        bar = self.view.verticalScrollBar()
        scroll = bar.value()
        self.view.clear()
        self._cursor = self.view.textCursor()
        self._cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self._data:
            self._w("загружаю…", DIM)
        elif self._tab == "learning":
            self._learning(self._data["learning"])
        else:
            self._activity(self._data["activity"])
        bar.setValue(scroll)

    def _w(self, text: str, color: QColor = FG, bold: bool = False) -> None:
        self._cursor.insertText(text, _fmt(color, bold))

    def _ln(self, text: str = "", color: QColor = FG, bold: bool = False) -> None:
        self._w(text + "\n", color, bold)

    def _head(self, title: str, note: str = "") -> None:
        self._ln()
        self._w(title.upper(), GREEN, bold=True)
        self._ln(f"   {note}" if note else "", DIM)

    def _row(self, label: str, value: float, total: float, tail: str,
             color: QColor = GREEN, width: int = BAR_W) -> None:
        filled, empty = _bar(value, total, width)
        self._w(f"  {label[:LABEL_W - 2]:<{LABEL_W - 2}}", FG)
        self._w(filled, color)
        self._w(empty, DIM)
        self._ln(f"  {tail}", FG)

    # --- «Освальд обо мне» ---

    def _learning(self, d: dict) -> None:
        filled, empty = _bar(d["mastery"], 100, 30)
        self._w("ОСВОЕНИЕ  ", GREEN, bold=True)
        self._w(filled, GREEN)
        self._w(empty, DIM)
        self._ln(f"  {d['mastery']}%", BRIGHT, bold=True)
        self._ln("60% — сколько данных разобрано, 40% — насколько полно известна каждая сфера",
                 DIM)

        jobs = d["jobs"]
        self._head("Разбор данных", f"{jobs['done']} из {jobs['total']} порций · "
                   f"{_num(d['analyzed_chars'])} символов прочитано")
        for item in jobs["by_source"]:
            self._row(item["label"], item["done"], item["total"],
                      f"{item['done']}/{item['total']}")

        self._head("Память", f"{d['facts']} фактов · {d['people']} людей · {d['goals']} целей")
        for area in d["areas"]:
            self._row(area["area"], area["facts"], area["full"], f"{area['facts']}/{area['full']}")
        self._w("  новые факты за 2 недели  ", DIM)
        self._spark(d["facts_by_day"])
        self._ln(f"  {sum(d['facts_by_day'])}", FG)

        self._head("Собрано")
        events = sorted(d["events"], key=lambda e: -e["count"])
        for item in events:
            self._w(f"  {item['label'][:LABEL_W - 2]:<{LABEL_W - 2}}", FG)
            self._ln(f"{_num(item['count']):>10}", BRIGHT)

        self._head("Вехи")
        for label, value, missing in (
                ("Досье", _when(d["dossier_at"]), "ещё нет — соберётся после свежих 30 дней"),
                ("Голос Освальда", _when(d["voice_at"]), "ещё нет"),
                ("Твой стиль письма", _when(d["style_at"]), "ещё нет")):
            self._w(f"  {label:<{LABEL_W - 2}}", FG)
            self._ln(value or missing, BRIGHT if value else DIM)

    # --- «Мои действия» ---

    def _activity(self, d: dict) -> None:
        days = d["days"]
        today = days[-1]
        since = d.get("since")
        if since:
            self._ln(f"время за компьютером считается с {since[8:10]}.{since[5:7]}", DIM)
        self._head("Сегодня", f"{_hm(today['minutes'])} за ПК" + (
            f" · с {today['first']} до {today['last']}" if today["first"] else ""))
        self._w("  по часам  ", DIM)
        self._spark(d["hours"])
        self._ln()
        self._ln("            0     6     12    18   23", DIM)
        self._kinds(today["kinds"], today["minutes"])
        if d["top_today"]:
            self._ln("  куда ушло время", DIM)
            for item in d["top_today"]:
                self._top(item)

        self._head("Неделя", f"{_hm(sum(x['minutes'] for x in days))} за ПК")
        longest = max((x["minutes"] for x in days), default=0) or 1
        for day in days:
            when = date.fromisoformat(day["date"])
            label = f"{WEEKDAYS[when.weekday()]} {when:%d.%m}"
            useful = day["kinds"].get("useful", 0)
            tail = (f"{_hm(day['minutes'])}  {day['first']}–{day['last']}" if day["minutes"]
                    else "—")
            if day["minutes"]:
                tail += f"  ✓{round(100 * useful / day['minutes'])}%"
            self._row(label, day["minutes"], longest, tail, width=14)
        week_kinds: dict[str, int] = {}
        for day in days:
            for kind, minutes in day["kinds"].items():
                week_kinds[kind] = week_kinds.get(kind, 0) + minutes
        self._kinds(week_kinds, sum(x["minutes"] for x in days))
        self._ln("  ✓ — доля полезного за день", DIM)
        if d["top_week"]:
            self._ln("  куда ушло время", DIM)
            for item in d["top_week"]:
                self._top(item)

        tasks = d["tasks"]
        self._head("Задачи")
        self._w("  открыто ", DIM)
        self._w(str(tasks["open"]), BRIGHT)
        self._w(" · просрочено ", DIM)
        self._w(str(tasks["overdue"]), RED if tasks["overdue"] else BRIGHT)
        self._w(" · закрыто сегодня ", DIM)
        self._w(str(tasks["done_today"]), GREEN)
        self._w(" · за неделю ", DIM)
        self._ln(str(tasks["done_week"]), GREEN)
        self._w("  серия: ", DIM)
        self._ln(f"{tasks['streak']} дн. подряд с закрытыми задачами", AMBER if tasks["streak"]
                 else DIM)

        if d["goals"]:
            self._head("Цели", "выполнено задач по цели")
            for goal in d["goals"]:
                if goal["total"]:
                    self._row(goal["title"], goal["done"], goal["total"],
                              f"{goal['done']}/{goal['total']}")
                else:
                    self._w(f"  {goal['title'][:LABEL_W - 2]:<{LABEL_W - 2}}", FG)
                    self._ln("задач пока нет", DIM)

    def _spark(self, values: list[int]) -> None:
        """Столбики по значениям; пустые — точками, чтобы было видно шкалу."""
        line = _spark(values)
        for char in line:
            self._w("·" if char == " " else char, DIM if char == " " else GREEN)

    def _kinds(self, kinds: dict[str, int], total: int) -> None:
        """Полоса «полезное / нейтральное / отвлекающее / не размечено»."""
        if not total:
            return
        self._w("  ", FG)
        width = BAR_W + 16
        for kind, _, color in KINDS:
            self._w(("█" if kind != "unknown" else "░") * round(width * kinds.get(kind, 0) / total),
                    color)
        self._ln()
        self._w("  ", FG)
        for kind, label, color in KINDS:
            minutes = kinds.get(kind, 0)
            if minutes:
                self._w("■ ", color)
                self._w(f"{label} {round(100 * minutes / total)}%   ", FG)
        self._ln()

    def _top(self, item: dict) -> None:
        category = item.get("category")
        self._w(f"    {item['key'][:LABEL_W + 4]:<{LABEL_W + 4}}", FG)
        self._w(f"{_hm(item['minutes']):>12}  ", BRIGHT)
        self._ln(category or "размечу позже", DIM if not category else FG)
