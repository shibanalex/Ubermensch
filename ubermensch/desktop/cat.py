"""Маскот: окошко-консоль с зелёной мордочкой-смайликом :3 (глаза-точки, рот «3» на боку).

Эмоции: ожидание (моргает, следит за курсором), речь, думает, радуется, удивлён,
перетаскивают (>.<), голова кружится после броска (@ @), спит ночью, нет связи (x x).
Погладить — поводить курсором туда-сюда над мордочкой.
Сам по себе (behavior.py): гуляет, оглядывается, напевает, подпрыгивает, зевает, скучает,
спит, пока пользователя нет, и прячется за край экрана от полноэкранных окон.
"""
from __future__ import annotations

import math
import os
import random
from datetime import datetime

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSettings, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QContextMenuEvent, QCursor, QFont, QGuiApplication, QMouseEvent,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QMenu

from ..system import windows
from . import behavior, console, face
from .bubble import ReplyBubble
from .client import Api
from .console import ACCENT, BG, BORDER, DIM, MONO, TITLE_H, plain

FACE = ACCENT
TALK = ("w", "open", "w", "o")  # рот, пока говорит

# Эмоции в духе аниме и манги: лицо (глаза, рот, брови, румянец, подмигивание), рот во время
# речи и эффект вокруг. Эмоцию ответа выбирает мозг (поле emotion), ядро присылает событие.
EMOTIONS = {
    "happy": dict(eyes="happy", mouth="w", brows=2, blush=True, talk=("w", "open", "smile", "o")),
    "excited": dict(eyes="sparkle", mouth="smile", brows=4, blush=True,
                    talk=("smile", "open", "smile", "O"), effect="sparkles"),
    "embarrassed": dict(eyes="chevron", mouth="pout", brows=-1, angle=-3, blush=True,
                        talk=("pout", "o", "pout", "w"), effect="sweat"),
    "annoyed": dict(eyes="flat", mouth="pout", brows=-2, angle=5,
                    talk=("flat", "open", "pout", "o"), effect="anger"),
    "sad": dict(eyes="teary", mouth="frown", brows=1, angle=-5,
                talk=("frown", "o", "frown", "open"), effect="gloom"),
    "confused": dict(eyes="open", mouth="o", tilt=6, talk=("o", "w", "o", "open"),
                     effect="question"),
    "smug": dict(eyes="half", mouth="w", brows=1, tilt=5, talk=("w", "open", "w", "tongue")),
    "wink": dict(eyes="open", mouth="tongue", wink=True, blush=True,
                 talk=("tongue", "open", "smile", "w"), effect="sparkle_one"),
    "thinking": dict(eyes="open", mouth="w", tilt=5, talk=TALK, effect="dots"),
}
EMOTION_HOLD_MS = 5000  # после реплики выражение держится ещё столько
POKES_TO_ANNOY = 4  # столько тычков за POKE_WINDOW_MS — раздражается
POKE_WINDOW_MS = 2500
PEEK = 30  # сколько маскота видно, когда она спряталась за край экрана
CHEERS = ("умничка :3", "минус одна, горжусь тобой", "вот это мой человек", "засчитано ♥",
          "кайф. дальше?")


def icon_pixmap(size: int) -> QPixmap:
    """Иконка для трея: маленькое чёрное окошко с мордочкой Ани."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(BORDER, max(1, size // 32)))
    p.setBrush(BG)
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size / 6, size / 6)
    scale = size / 120  # лицо рассчитано на ширину ~120
    p.scale(scale, scale)
    face.draw(p, 60, 58, 92, face.FaceState(), FACE)
    p.end()
    return pixmap


class Mascot(console.ConsoleWindow):
    clicked = Signal()  # открыть окно запроса
    open_history = Signal()  # открыть консоль ответов
    open_metrics = Signal()  # окно метрик
    quit_requested = Signal()
    hidden_to_tray = Signal()

    W, H = 220, 160
    TICK_MS = 50

    def __init__(self, api: Api):
        super().__init__("anya.exe")
        self.resize(self.W, self.H)
        self.setToolTip("Клик — спросить. Перетащи меня. Поводи курсором — погладишь :3")
        self.api = api
        self.bubble = ReplyBubble()  # реплики маскота — мини-окна с хвостиком
        self.bubble.open_history.connect(self.open_history)
        self.bubble.typing.connect(self.set_typing)
        self.answer_visible = lambda: False  # подменяет приложение
        self.engaged = lambda: False  # открыто окно запроса или история — не отвлекаться
        self.behavior = behavior.Behavior()
        self._tucked_from: QPoint | None = None  # откуда ушёл за край экрана

        self._ms = 0
        self._blink_at, self._blink_until = 2500, 0
        self._talk_until = self._happy_until = self._surprise_until = self._dizzy_until = 0
        self._typing = False
        self._last_touch = 0  # последнее взаимодействие — ночью котик засыпает без него
        self._press: QPoint | None = None
        self._dragging = False
        self._drag_distance = 0
        self._pet_moves: list[tuple[int, int]] = []  # (время, пройденный курсором путь)
        self._last_cursor: QPoint | None = None
        self._hearts: list[tuple[int, float]] = []  # (время появления, x)
        self._emotion, self._emotion_until = "", 0  # эмоция текущего ответа
        self._pokes: list[int] = []  # когда тыкали в мордочку
        self._last_reply_id: int | None = None

        self._settings = QSettings("Ubermensch", "desktop")
        self.home = self._place()  # его место; гуляет вокруг него, после перетаскивания — новое
        api.event.connect(self._on_event)
        api.state_changed.connect(self._on_state)

        timer = QTimer(self, interval=self.TICK_MS)
        timer.timeout.connect(self._tick)
        timer.start()
        observe = QTimer(self, interval=1000)
        observe.timeout.connect(self._observe)
        observe.start()

    def _place(self) -> QPoint:
        saved = self._settings.value("mascot_pos")
        area = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(saved, QPoint) and any(
                s.availableGeometry().contains(saved) for s in QGuiApplication.screens()):
            self.move(saved)
        else:
            self.move(area.right() - self.W - 24, area.bottom() - self.H - 12)
        return self.pos()

    # --- сам по себе ---

    def _fullscreen_app(self) -> bool:
        """На весь экран чужое окно (игра, фильм), а не рабочий стол и не наши окна."""
        window = windows.foreground()
        if window is None or windows.is_desktop(window):
            return False
        return window.pid != os.getpid() and windows.is_fullscreen(window)

    def _observe(self) -> None:
        if not self.isVisible():
            return
        busy = (self._dragging or self._press is not None or self._typing
                or self._ms < self._talk_until or bool(self.api.state.get("thinking"))
                or self.bubble.isVisible() or self.engaged())
        world = behavior.World(
            now_ms=self._ms, hour=datetime.now().hour, user_idle_s=windows.idle_seconds(),
            fullscreen=self._fullscreen_app(), touched_ago_ms=self._ms - self._last_touch,
            busy=busy, offset=self.x() - self.home.x())
        action = self.behavior.step(world)
        area = self.screen().availableGeometry()
        if isinstance(action, behavior.Walk):
            x = max(area.left(), min(self.x() + action.dx, area.right() - self.W))
            ms = max(300, int(action.ms * abs(x - self.x()) / max(1, abs(action.dx))))
            walk = self.glide(QPoint(x, self.y()), ms, QEasingCurve.Type.Linear)
            walk.finished.connect(lambda: self.behavior.walk_finished(self._ms))
            self._walk_dir = 1 if action.dx > 0 else -1
        elif isinstance(action, behavior.Say):
            self.say(action.text, seconds=6)
        elif isinstance(action, behavior.Tuck) and action.hidden:
            self.bubble.hide()
            self._tucked_from = self.pos()
            self.glide(QPoint(area.right() - PEEK, self.y()), 600, QEasingCurve.Type.InOutCubic)
        elif isinstance(action, behavior.Tuck) and self._tucked_from is not None:
            self.glide(self._tucked_from, 600, QEasingCurve.Type.OutCubic)
            self._tucked_from = None

    # --- реакции на ядро и приложение ---

    def say(self, text: str, seconds: float | None = None, title: str = "anya.exe",
            notes: list[str] = ()) -> None:
        self.bubble.show_text(text, self, notes=notes, title=title, hold_seconds=seconds)

    def _talk(self, seconds: float) -> None:
        self._talk_until = max(self._talk_until, self._ms + int(min(seconds, 4.0) * 1000))

    def set_typing(self, typing: bool) -> None:
        """Пока консоль ответа печатает текст, маскот «говорит»."""
        self._typing = typing
        if not typing:
            self._talk(0.3)

    def happy(self, seconds: float = 1.5) -> None:
        self._happy_until = self._ms + int(seconds * 1000)

    def feel(self, emotion: str, seconds: float = 6) -> None:
        """Показать эмоцию: на время реплики и ещё EMOTION_HOLD_MS после неё."""
        if emotion in EMOTIONS:
            self._emotion = emotion
            self._emotion_until = self._ms + int(seconds * 1000)

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "reminder":
            self._last_touch = self._ms
            self._surprise_until = self._ms + 2500
            self.say(f"⏰ {event.get('text', '')}", seconds=60, title="Напоминание")
        elif kind == "say":  # реплика от ядра — например, комментарий к экрану
            self.say(event.get("text", ""), seconds=20)
        elif kind == "celebrate":
            self.feel("excited", 3.5)
            self.say(random.choice(CHEERS), seconds=4)
        elif kind == "emotion":  # эмоция ответа мозга: держится, пока печатается реплика
            self.feel(str(event.get("emotion", "")), 8)

    def _on_state(self, state: dict) -> None:
        replies = [m for m in state.get("messages", []) if m["role"] == "assistant"]
        if not replies:
            return
        latest = replies[-1]
        if self._last_reply_id is not None and latest["id"] > self._last_reply_id \
                and not self.answer_visible():
            self.say(plain(latest["text"]), notes=latest.get("notes") or [])
        self._last_reply_id = latest["id"]

    # --- анимация ---

    def _tick(self) -> None:
        self._ms += self.TICK_MS
        if self._ms >= self._blink_at:
            self._blink_until = self._ms + 140
            self._blink_at = self._ms + random.randint(2500, 6000)
        self._hearts = [(t, x) for t, x in self._hearts if self._ms - t < 1500]
        self.update()

    def _mood(self) -> str:
        t = self._ms
        if self._dragging:
            return "drag"
        if t < self._dizzy_until:
            return "dizzy"
        if not self.api.online:
            return "offline"
        if t < self._surprise_until:
            return "surprised"
        if t < self._happy_until:
            return "happy"
        if self.api.state.get("thinking"):
            return "thinking"
        if self._typing or t < self._talk_until:
            if self._emotion and t < self._emotion_until:  # говорит, пока не допечатает
                self._emotion_until = max(self._emotion_until, t + EMOTION_HOLD_MS)
            return "talk"
        if self._emotion and t < self._emotion_until:
            return "emotion"
        activity = self.behavior.activity
        if activity == "wake":  # вздрогнул, потом обрадовался
            return "surprised" if self.behavior.progress(t) < 0.35 else "happy"
        if activity != "idle":
            return activity
        if datetime.now().hour < 7 and t - self._last_touch > 120_000:
            return "sleep"
        return "idle"

    def _active_emotion(self) -> dict | None:
        return EMOTIONS.get(self._emotion) if self._ms < self._emotion_until else None

    def _face(self, mood: str) -> face.FaceState:
        """Эмоция → состояние лица."""
        t = self._ms
        blink = t < self._blink_until
        state = face.FaceState(eyes="closed" if blink else "open", t=t)
        emotion = self._active_emotion()
        if emotion and mood in ("talk", "emotion"):
            if not blink or emotion["eyes"] in ("happy", "chevron", "flat"):
                state.eyes = emotion["eyes"]
            state.brows, state.brow_angle = emotion.get("brows", 0), emotion.get("angle", 0)
            state.brow_tilt, state.blush = emotion.get("tilt", 0), emotion.get("blush", False)
            state.wink = emotion.get("wink", False)
            talk = emotion.get("talk", TALK)
            state.mouth = talk[(t // 140) % len(talk)] if mood == "talk" else emotion["mouth"]
            return state
        if mood == "drag":
            state.eyes, state.mouth, state.brows = "chevron", "o", -3
        elif mood == "dizzy":
            state.eyes, state.mouth = "dizzy", "wave"
        elif mood == "offline":
            state.eyes, state.mouth = "x", "wave"
        elif mood == "surprised":
            state.eyes, state.mouth, state.brows = "wide", "o", 6
        elif mood == "happy":  # радуется, гладят — «^ ^» и румянец
            state.eyes, state.blush, state.brows = "happy", True, 2
        elif mood == "thinking":
            state.brow_tilt = 5
        elif mood == "talk":
            state.mouth = TALK[(t // 140) % len(TALK)]
        elif mood == "sleep":
            state.eyes, state.brows = "closed", -2
        elif mood == "hum":
            state.eyes, state.blush = "happy", True
            state.mouth = "o" if (t // 450) % 2 else "w"
        elif mood == "yawn":
            state.eyes = "closed"
            state.mouth = "O" if 0.2 < self.behavior.progress(t) < 0.8 else "o"
        elif mood == "bored":
            state.eyes, state.mouth, state.brows = "half", "wave", -3
        return state

    def _look(self, mood: str) -> QPointF:
        if mood == "thinking":
            return QPointF(3, -4)  # смотрит вверх, вспоминает
        if mood == "walk":  # смотрит, куда идёт
            return QPointF(4 * getattr(self, "_walk_dir", 1), 1)
        if mood == "look":  # налево, потом направо
            return QPointF(-4 if self.behavior.progress(self._ms) < 0.5 else 4, 0)
        if mood == "tucked":  # выглядывает из-за края
            return QPointF(-4, 0)
        if mood not in ("idle", "talk"):
            return QPointF(0, 0)
        center = self.mapToGlobal(QPoint(self.W // 2, TITLE_H + 45))
        dx, dy = QCursor.pos().x() - center.x(), QCursor.pos().y() - center.y()
        dist = math.hypot(dx, dy) or 1
        reach = min(1.0, dist / 250) * 3.5
        return QPointF(dx / dist * reach, dy / dist * reach)

    def paint_body(self, p: QPainter) -> None:
        mood = self._mood()
        state = self._face(mood)
        state.look = self._look(mood)
        color = DIM if mood == "offline" else FACE
        cx, body_h = self.W / 2, self.H - TITLE_H
        eyes_y, mouth_y = TITLE_H + body_h * 0.42, TITLE_H + body_h * 0.78

        p.save()
        if mood == "drag":  # покачивается в руке
            p.translate(cx, TITLE_H + body_h / 2)
            p.rotate(math.sin(self._ms / 90) * 7)
            p.translate(-cx, -(TITLE_H + body_h / 2))
        elif mood == "walk":  # шагает — мордочка подпрыгивает в такт
            p.translate(0, -abs(math.sin(self._ms / 130)) * 4)
        elif mood == "hop":
            p.translate(0, -math.sin(self.behavior.progress(self._ms) * math.pi) * 14)
        elif mood == "hum":  # покачивается в такт
            p.translate(cx, TITLE_H + body_h / 2)
            p.rotate(math.sin(self._ms / 300) * 5)
            p.translate(-cx, -(TITLE_H + body_h / 2))
        elif mood == "talk":  # говорит — голова чуть кивает в такт словам
            p.translate(0, math.sin(self._ms / 110) * 1.5)
        elif mood == "emotion" and self._emotion == "excited":  # подпрыгивает от радости
            p.translate(0, -abs(math.sin(self._ms / 160)) * 5)
        elif mood == "emotion" and self._emotion == "confused":  # склонила голову
            p.translate(cx, TITLE_H + body_h / 2)
            p.rotate(-8)
            p.translate(-cx, -(TITLE_H + body_h / 2))
        face.draw(p, cx, eyes_y, mouth_y, state, color)
        p.restore()

        self._paint_extras(p, mood, color)

        status = {"offline": "нет связи с ядром", "thinking": "думаю..."}.get(mood)
        if not status and (problems := self.api.state.get("problems")):
            status = "⚠ " + problems[0]["message"][:34] + "…"  # подробности — /status и история
        if status:
            p.setFont(QFont(MONO, 8))
            p.setPen(DIM)
            p.drawText(QRect(10, self.H - 20, self.W - 16, 16), Qt.AlignmentFlag.AlignVCenter,
                       rf"C:\anya> {status}")

    def _paint_effect(self, p: QPainter, effect: str, color) -> None:
        """Манга-эффект эмоции вокруг мордочки."""
        t, w = self._ms, self.W
        if effect == "sparkles":
            face.sparkles(p, [(18, TITLE_H + 20), (w - 20, TITLE_H + 26), (24, self.H - 38),
                              (w - 26, self.H - 44)], t, color)
        elif effect == "sparkle_one":
            face.sparkles(p, [(w / 2 + 70, TITLE_H + 22)], t, color)
        elif effect == "sweat":
            face.sweat_drop(p, w / 2 + 68, TITLE_H + 26, t)
        elif effect == "anger":
            face.anger_mark(p, w / 2 + 64, TITLE_H + 18, t)
        elif effect == "gloom":
            face.gloom(p, w / 2 - 34, TITLE_H + 4, 68, t, color)
        elif effect == "question":
            p.setPen(color)
            p.setFont(QFont(MONO, 18, QFont.Weight.Bold))
            p.drawText(QPointF(w - 34, TITLE_H + 26 - abs(math.sin(t / 300)) * 4), "?")
        elif effect == "dots":
            p.setPen(color)
            p.setFont(QFont(MONO, 14, QFont.Weight.Bold))
            p.drawText(QPointF(w - 46, TITLE_H + 22), "." * (1 + (t // 400) % 3))

    def _paint_extras(self, p: QPainter, mood: str, color) -> None:
        t = self._ms
        emotion = self._active_emotion()
        if emotion and mood in ("talk", "emotion") and emotion.get("effect"):
            self._paint_effect(p, emotion["effect"], color)
        p.setPen(color)
        if mood == "thinking":
            p.setFont(QFont(MONO, 14, QFont.Weight.Bold))
            p.drawText(QPointF(self.W - 46, TITLE_H + 22), "." * (1 + (t // 400) % 3))
        elif mood == "surprised":
            p.setFont(QFont(MONO, 20, QFont.Weight.Bold))
            p.drawText(QPointF(self.W - 34, TITLE_H + 28), "!")
        elif mood == "hum":  # ноты всплывают и тают
            for i, note in enumerate("♪♫♪"):
                phase = ((t / 1000) * 0.45 + i / 3) % 1
                p.setOpacity(1 - phase)
                p.setFont(QFont("Noto Sans Symbols2", int(10 + phase * 5)))
                p.drawText(QPointF(self.W - 52 + math.sin(phase * 6 + i) * 8,
                                   TITLE_H + 46 - phase * 34), note)
            p.setOpacity(1)
        elif mood == "bored":
            p.setOpacity(0.6)
            p.setFont(QFont(MONO, 9))
            p.drawText(QPointF(self.W - 44, TITLE_H + 24), "эх")
            p.setOpacity(1)
        elif mood in ("sleep", "offline"):
            for i in range(3):
                phase = ((t / 1000) * 0.35 + i / 3) % 1
                p.setOpacity(1 - phase)
                p.setFont(QFont(MONO, int(9 + phase * 6)))
                p.drawText(QPointF(self.W - 40 + phase * 14, TITLE_H + 40 - phase * 30), "z")
            p.setOpacity(1)
        for born, x in self._hearts:  # сердечки, когда гладят
            phase = (t - born) / 1500
            p.setOpacity(1 - phase)
            p.setFont(QFont("Noto Sans Symbols2", 13))
            p.drawText(QPointF(x, TITLE_H + 40 - phase * 34), "♥")
        p.setOpacity(1)

    # --- мышь ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._last_touch = self._ms
        self.stop_glide()  # поймали на прогулке
        self.behavior.interrupt()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragging = False
            self._drag_distance = 0

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._last_touch = self._ms
        self.set_hover(self.button_at(event.position().toPoint()))
        if self._press is None:
            self._detect_petting(event.globalPosition().toPoint())
            return
        delta = event.globalPosition().toPoint() - self._press
        if self._dragging or delta.manhattanLength() > 5:
            if not self._dragging:
                self.bubble.hide()
            self._dragging = True
            self._drag_distance = max(self._drag_distance, delta.manhattanLength())
            self.move(self._origin + delta)

    def _detect_petting(self, cursor: QPoint) -> None:
        """Курсор водят туда-сюда над мордочкой — это поглаживание."""
        if self._last_cursor is not None:
            self._pet_moves.append((self._ms, (cursor - self._last_cursor).manhattanLength()))
        self._last_cursor = cursor
        self._pet_moves = [(t, d) for t, d in self._pet_moves if self._ms - t < 1200]
        if sum(d for _, d in self._pet_moves) > 450:
            self._pet_moves.clear()
            self.happy(1.8)
            self._hearts.append((self._ms, random.uniform(30, self.W - 50)))

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._last_cursor = None
        self._pet_moves.clear()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.home = self.pos()  # куда поставили — там теперь его место
            self._tucked_from = None
            self._settings.setValue("mascot_pos", self.pos())
            if self._drag_distance > 120:  # швырнули — голова кружится
                self._dizzy_until = self._ms + 1600
            return
        button = self.button_at(event.position().toPoint())
        if button in ("hide", "close"):
            self.hide_to_tray()
        elif button == "maximize":
            self.open_history.emit()
        else:
            self._pokes = [t for t in self._pokes if self._ms - t < POKE_WINDOW_MS] + [self._ms]
            if len(self._pokes) >= POKES_TO_ANNOY:  # затыкали — дуется
                self._pokes.clear()
                self.feel("annoyed", 3)
                self.say(random.choice(("эй, щекотно!", "ну хватит тыкать >_<", "я тебе не кнопка ¬_¬")),
                         seconds=3)
                return
            self.happy(0.5)
            self.clicked.emit()

    def hide_to_tray(self) -> None:
        """Прячет маскота; приложение подскажет, как его вернуть."""
        self.hide()
        self.bubble.hide()
        self.hidden_to_tray.emit()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction("Спросить", self.clicked.emit)
        menu.addAction("История ответов", self.open_history.emit)
        menu.addAction("Метрики", self.open_metrics.emit)
        menu.addAction("Спрятать в трей", self.hide_to_tray)
        menu.addSeparator()
        menu.addAction("Выход", self.quit_requested.emit)
        menu.exec(event.globalPos())
