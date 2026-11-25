"""Поведение оверлея: чем он занят сам по себе. Только решения; рисует и двигает окно cat.py.

Раз в секунду оверлей «осматривается» (World) и решает:
- пользователь отошёл от ПК (5+ мин без мыши и клавиатуры): засыпает; вернулся после
  долгого отсутствия: вздрагивает и здоровается;
- на весь экран игра или фильм: отползает за край экрана, чтобы не мешать; потом возвращается;
- в остальное время изредка гуляет вдоль своего места, оглядывается, напевает, подпрыгивает,
  поздно вечером зевает, а если его долго не трогали: скучает.
Пока оверлея тащат, он говорит, думает или открыт его пузырь: он ничего не затевает.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

AWAY_S = 5 * 60  # без мыши и клавиатуры столько: пользователь отошёл
GREET_AFTER_S = 15 * 60  # здороваться, только если его не было дольше
ACTIVE_S = 60  # пользователь за ПК: трогал мышь или клавиатуру за последнюю минуту
QUIET_AFTER_TOUCH_MS = 20_000  # после касания оверлей какое-то время не отвлекается
BORED_AFTER_MS = 40 * 60_000  # столько не трогали: начинает скучать
IDLE_GAP_MS = (35_000, 110_000)  # пауза между затеями
WALK_RANGE = 260  # гуляет не дальше стольких пикселей от своего места
WALK_SPEED = 70  # пикселей в секунду

DURATIONS = {"look": 2600, "hum": 4500, "hop": 900, "yawn": 2200, "bored": 4000,
             "wake": 2600}
GREETINGS = ("вернулся! я скучала :3", "наконец-то… я ждала", "о, привет-привет)",
             "где ты был так долго? …шучу. почти :3", "ура, ты тут")


@dataclass(frozen=True)
class World:
    """Что оверлей видит в этот момент."""
    now_ms: int
    hour: int
    user_idle_s: float  # секунд без мыши и клавиатуры
    fullscreen: bool  # активное окно: игра, фильм, презентация на весь экран
    touched_ago_ms: int  # когда его последний раз трогали
    busy: bool  # тащат, говорит, думает или открыт пузырь
    offset: int  # насколько он сейчас левее (<0) или правее (>0) своего места


@dataclass(frozen=True)
class Walk:
    dx: int  # куда и насколько отойти от текущего места
    ms: int


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Tuck:
    hidden: bool  # True: спрятаться за край экрана, False; вернуться


Action = Walk | Say | Tuck


class Behavior:
    def __init__(self):
        self.activity = "idle"  # idle, walk, look, hum, hop, yawn, bored, sleep, wake, tucked
        self.started_ms = 0
        self._until = 0
        self._next_idea = random.randint(*IDLE_GAP_MS)
        self._away_since: int | None = None

    def _start(self, activity: str, now: int, ms: int = 0) -> None:
        self.activity, self.started_ms = activity, now
        self._until = now + (ms or DURATIONS.get(activity, 0))

    def progress(self, now: int) -> float:
        """0..1: какая часть текущей затеи прошла (для анимации)."""
        span = self._until - self.started_ms
        return min(1.0, max(0.0, (now - self.started_ms) / span)) if span > 0 else 0.0

    def interrupt(self) -> None:
        """Маскота тронули: всё бросает (прогулку прервёт окно)."""
        if self.activity not in ("tucked",):
            self.activity = "idle"
            self._until = 0

    def walk_finished(self, now: int) -> None:
        if self.activity == "walk":
            self._start("idle", now)
            self._next_idea = now + random.randint(*IDLE_GAP_MS)

    def step(self, w: World) -> Action | None:
        now = w.now_ms
        # 1. полноэкранное окно: уйти за край и ждать
        if w.fullscreen and not w.busy:
            if self.activity != "tucked":
                self._start("tucked", now)
                return Tuck(True)
            return None
        if self.activity == "tucked":
            self._start("idle", now)
            self._next_idea = now + random.randint(*IDLE_GAP_MS)
            return Tuck(False)

        # 2. пользователь отошёл: спать; вернулся; проснуться и поздороваться
        away = w.user_idle_s >= AWAY_S
        if away:
            if self._away_since is None:
                self._away_since = now - int(w.user_idle_s * 1000)
            if self.activity not in ("sleep", "walk") and not w.busy:
                self._start("sleep", now)
            return None
        if self._away_since is not None:
            was_away_s = (now - self._away_since) / 1000
            self._away_since = None
            if self.activity == "sleep":
                self._start("wake", now)
                if was_away_s >= GREET_AFTER_S:
                    return Say(random.choice(GREETINGS))
            return None

        if w.busy:
            if self.activity not in ("walk", "wake"):
                self.activity = "idle"
            return None
        if self.activity in ("walk",):
            return None  # закончится, когда окно доедет
        if self.activity != "idle" and now >= self._until:
            self._start("idle", now)
        if self.activity != "idle" or now < self._next_idea:
            return None

        # 3. сам по себе: только когда пользователь за ПК и оверлея давно не трогали
        if w.user_idle_s > ACTIVE_S or w.touched_ago_ms < QUIET_AFTER_TOUCH_MS:
            return None
        self._next_idea = now + random.randint(*IDLE_GAP_MS)
        ideas = {"walk": 3, "look": 3, "hum": 2, "hop": 1}
        if w.hour >= 23 or w.hour < 2:
            ideas["yawn"] = 3
        if w.touched_ago_ms > BORED_AFTER_MS:
            ideas["bored"] = 3
        idea = random.choices(list(ideas), weights=list(ideas.values()))[0]
        if idea == "walk":
            target = random.randint(-WALK_RANGE, WALK_RANGE)
            if abs(target - w.offset) < 60:  # совсем рядом: шаг побольше
                target = w.offset + (90 if target >= w.offset else -90)
            dx = max(-WALK_RANGE, min(WALK_RANGE, target)) - w.offset
            ms = int(abs(dx) / WALK_SPEED * 1000)
            self._start("walk", now, ms)
            return Walk(dx, ms)
        self._start(idea, now)
        return None
