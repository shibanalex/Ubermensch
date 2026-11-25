"""Виджет оверлея по эскизу пользователя: брови-дуги, большие аниме-глаза (веко с ресничкой,
радужка со штрихами и бликами), нос-чёрточка, рот «ω». Зелёные линии на чёрном.

Манга-приёмы для эмоций: глаза-звёздочки, слёзы, плоский взгляд, подмигивание; брови
«злые» и «обеспокоенные»; эффекты вокруг лица: капля пота, знак злости, искры, тучка грусти.

Эмоция: это состояние лица (FaceState): какие глаза, куда смотрят, какой рот, брови, румянец.
Рисуется векторно, поэтому одинаково чётко и в окне оверлея, и в значке трея.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen

BLUSH = QColor(236, 140, 170, 170)
WATER = QColor(136, 192, 208, 220)  # слёзы и капля пота
ANGER = QColor(191, 97, 106, 235)  # знак злости
PUPIL = QColor("#2e3440")  # детали глаз: цветом фона окна
EYE_DX = 40  # глаза от центра
EYE_W, EYE_H = 23, 22  # полуширина глаза и высота века над центром


@dataclass
class FaceState:
    eyes: str = "open"  # open, closed, happy, half, wide, x, chevron, dizzy, sparkle, teary, flat
    mouth: str = "w"  # w (ω), o, O, open, wave, smile, frown, pout, tongue, flat
    look: QPointF = field(default_factory=lambda: QPointF(0, 0))  # куда смотрит радужка
    brows: float = 0.0  # подъём бровей (+ выше, − ниже)
    brow_tilt: float = 0.0  # одна бровь выше другой (думает)
    brow_angle: float = 0.0  # + внутренние концы вниз (злится), − вверх (переживает)
    wink: bool = False  # правый глаз подмигивает «^»
    blush: bool = False
    t: int = 0  # время, мс: для вращения «@ @»


def _pen(color: QColor, width: float, alpha: int = 255) -> QPen:
    c = QColor(color)
    c.setAlpha(alpha)
    pen = QPen(c, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _lid(ex: float, ey: float, m: int, lift: float = 0.0) -> tuple[QPainterPath, QPainterPath]:
    """(верхнее веко с ресничкой, контур глаза для обрезки радужки). m: −1 левый, +1 правый."""
    inner, outer = ex - m * (EYE_W - 1), ex + m * EYE_W
    top = ey - EYE_H - lift
    upper = QPainterPath(QPointF(inner, ey - 4))
    upper.quadTo(QPointF(ex, top), QPointF(outer, ey - 8))
    upper.lineTo(QPointF(outer + m * 6, ey - 13))  # ресничка у внешнего уголка
    shape = QPainterPath(QPointF(inner, ey - 4))
    shape.quadTo(QPointF(ex, top), QPointF(outer, ey - 8))
    shape.quadTo(QPointF(ex + m * 4, ey + 34), QPointF(inner, ey - 4))
    return upper, shape


def _star(p: QPainter, x: float, y: float, r: float, color: QColor) -> None:
    """Четырёхлучевая звёздочка-блик."""
    path = QPainterPath(QPointF(x, y - r))
    for px, py in ((x + r * 0.28, y - r * 0.28), (x + r, y), (x + r * 0.28, y + r * 0.28),
                   (x, y + r), (x - r * 0.28, y + r * 0.28), (x - r, y),
                   (x - r * 0.28, y - r * 0.28), (x, y - r)):
        path.lineTo(QPointF(px, py))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _open_eye(p: QPainter, ex: float, ey: float, m: int, s: FaceState, color: QColor,
              half: bool = False, wide: bool = False, sparkle: bool = False,
              teary: bool = False) -> None:
    lift = 6 if wide else 0
    upper, shape = _lid(ex, ey, m, lift)
    ix, iy = ex + s.look.x() * 1.6, ey + 4 + s.look.y() * 1.2
    rx, ry = (8.5, 11) if wide else (12, 17) if sparkle or teary else (11, 16)
    p.save()
    p.setClipPath(shape)
    p.setPen(_pen(color, 1.8))
    p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 70)))
    p.drawEllipse(QPointF(ix, iy), rx, ry)
    p.setPen(Qt.PenStyle.NoPen)  # зрачок
    p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 200)))
    p.drawEllipse(QPointF(ix + m * 1, iy + 3), rx * 0.45, ry * 0.5)
    hatch = _pen(color, 1.3, 150)  # штрихи радужки, как на эскизе
    p.setPen(hatch)
    for k in (-0.55, 0.0, 0.55):
        x = ix + k * rx
        p.drawLine(QPointF(x, iy + ry * 0.15), QPointF(x - 1.5, iy + ry * 0.8))
    if sparkle:  # глаза-звёздочки: блики мерцают
        twinkle = 0.85 + 0.25 * math.sin(s.t / 160 + m)
        _star(p, ix - m * 3.5, iy - ry * 0.4, 6.5 * twinkle, PUPIL)
        _star(p, ix + m * 3.5, iy + ry * 0.3, 3.2 * twinkle, PUPIL)
    else:
        p.setPen(_pen(PUPIL, 2.6))  # блики: вертикальные чёрточки
        p.drawLine(QPointF(ix - m * 3.5, iy - ry * 0.65), QPointF(ix - m * 3.5, iy - ry * 0.15))
        p.drawLine(QPointF(ix - m * 0.5, iy - ry * 0.55), QPointF(ix - m * 0.5, iy - ry * 0.35))
        if teary:  # влажный блеск: ещё два блика
            p.setPen(_pen(PUPIL, 2.0))
            p.drawEllipse(QPointF(ix + m * 4, iy + ry * 0.35), 1.6, 1.6)
            p.drawEllipse(QPointF(ix - m * 5, iy + ry * 0.1), 1.0, 1.0)
    if half:  # сонное веко закрывает верх
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(PUPIL)
        p.drawRect(QRectF(ex - EYE_W - 8, ey - EYE_H - 10, 2 * EYE_W + 16, EYE_H + 9))
    p.restore()
    p.setPen(_pen(color, 3.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if half:
        lid = QPainterPath(QPointF(ex - m * (EYE_W - 1), ey - 2))
        lid.quadTo(QPointF(ex, ey - 6), QPointF(ex + m * EYE_W, ey - 3))
        lid.lineTo(QPointF(ex + m * (EYE_W + 5), ey - 7))
        p.drawPath(lid)
    else:
        p.drawPath(upper)
    lower = QPainterPath(QPointF(ex - m * 8, ey + 19))  # тонкое нижнее веко
    lower.quadTo(QPointF(ex + m * 2, ey + 23), QPointF(ex + m * 12, ey + 16))
    p.setPen(_pen(color, 1.4, 170))
    p.drawPath(lower)
    if teary:  # слеза копится у нижнего века и скатывается
        phase = (s.t % 1800) / 1800
        tx, ty = ex + m * 11, ey + 20 + phase * 18
        drop = QPainterPath(QPointF(tx, ty - 6))
        drop.quadTo(QPointF(tx + 4.5, ty + 1), QPointF(tx, ty + 3.5))
        drop.quadTo(QPointF(tx - 4.5, ty + 1), QPointF(tx, ty - 6))
        p.setOpacity(1 - phase * 0.7)
        p.setPen(_pen(WATER, 1.3))
        p.setBrush(QColor(WATER.red(), WATER.green(), WATER.blue(), 90))
        p.drawPath(drop)
        p.setOpacity(1)


def _eye(p: QPainter, ex: float, ey: float, m: int, s: FaceState, color: QColor) -> None:
    pen = _pen(color, 3.2)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inner, outer = ex - m * (EYE_W - 1), ex + m * EYE_W
    if s.wink and m > 0:  # подмигивает правым: «^»
        path = QPainterPath(QPointF(inner, ey + 3))
        path.quadTo(QPointF(ex, ey - 12), QPointF(outer, ey + 3))
        path.lineTo(QPointF(outer + m * 5, ey))
        p.drawPath(path)
    elif s.eyes in ("open", "half", "wide", "sparkle", "teary"):
        _open_eye(p, ex, ey, m, s, color, half=s.eyes == "half", wide=s.eyes == "wide",
                  sparkle=s.eyes == "sparkle", teary=s.eyes == "teary")
    elif s.eyes == "flat":  # «¬ ¬»: плоский взгляд: веко прямой чертой, под ним край радужки
        p.drawLine(QPointF(inner, ey - 1), QPointF(outer + m * 4, ey - 3))
        p.setPen(_pen(color, 2.0))
        iris = QPainterPath(QPointF(ex - 8 + s.look.x(), ey + 1))
        iris.quadTo(QPointF(ex + s.look.x(), ey + 13), QPointF(ex + 8 + s.look.x(), ey + 1))
        p.drawPath(iris)
    elif s.eyes == "closed":  # моргнула или спит: дуга вниз с ресничкой
        path = QPainterPath(QPointF(inner, ey - 1))
        path.quadTo(QPointF(ex, ey + 8), QPointF(outer, ey - 3))
        path.lineTo(QPointF(outer + m * 6, ey - 7))
        p.drawPath(path)
    elif s.eyes == "happy":  # «^ ^»
        path = QPainterPath(QPointF(inner, ey + 5))
        path.quadTo(QPointF(ex, ey - 14), QPointF(outer, ey + 5))
        p.drawPath(path)
    elif s.eyes == "x":
        for a, b in (((-9, -9), (9, 9)), ((-9, 9), (9, -9))):
            p.drawLine(QPointF(ex + a[0], ey + a[1]), QPointF(ex + b[0], ey + b[1]))
    elif s.eyes == "chevron":  # «> <»: зажмурилась
        tip = ex - m * 6  # остриё смотрит к носу: «> <»
        p.drawPolyline([QPointF(tip + m * 13, ey - 10), QPointF(tip, ey), QPointF(tip + m * 13, ey + 10)])
    elif s.eyes == "dizzy":  # спираль «@»
        path = QPainterPath()
        spin = s.t * 0.012
        for i in range(60):
            a = spin + i * 0.33
            r = 1.5 + i * 0.2
            point = QPointF(ex + math.cos(a) * r, ey + math.sin(a) * r)
            path.lineTo(point) if i else path.moveTo(point)
        p.setPen(_pen(color, 2.2))
        p.drawPath(path)


def _brow(p: QPainter, ex: float, by: float, m: int, color: QColor, angle: float = 0.0) -> None:
    """angle > 0: внутренний конец (у носа) ниже: злится; < 0; выше: переживает."""
    path = QPainterPath(QPointF(ex - m * 19, by + 4 + angle))
    path.quadTo(QPointF(ex + m * 2, by - 7), QPointF(ex + m * 21, by - 1 - angle * 0.5))
    p.setPen(_pen(color, 2.8))
    p.drawPath(path)
    p.setPen(_pen(color, 1.4, 160))  # второй штрих: как набросок от руки
    thin = QPainterPath(QPointF(ex - m * 14, by + 1 + angle * 0.8))
    thin.quadTo(QPointF(ex + m * 3, by - 8), QPointF(ex + m * 17, by - 4 - angle * 0.4))
    p.drawPath(thin)


def _mouth(p: QPainter, cx: float, y: float, kind: str, color: QColor) -> None:
    p.setPen(_pen(color, 2.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "w":  # «ω»: как на эскизе
        path = QPainterPath(QPointF(cx - 24, y - 4))
        path.cubicTo(QPointF(cx - 20, y + 5), QPointF(cx - 6, y + 5), QPointF(cx, y - 1))
        path.cubicTo(QPointF(cx + 6, y + 5), QPointF(cx + 20, y + 5), QPointF(cx + 24, y - 4))
        p.drawPath(path)
    elif kind in ("o", "O"):
        r = (4.5, 5.5) if kind == "o" else (7, 9)
        p.drawEllipse(QPointF(cx, y + 1), *r)
    elif kind == "open":  # говорит
        p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 60)))
        path = QPainterPath(QPointF(cx - 12, y - 2))
        path.quadTo(QPointF(cx, y - 4), QPointF(cx + 12, y - 2))
        path.quadTo(QPointF(cx, y + 12), QPointF(cx - 12, y - 2))
        p.drawPath(path)
    elif kind == "wave":  # «~»: скучает
        path = QPainterPath(QPointF(cx - 16, y))
        path.cubicTo(QPointF(cx - 8, y - 6), QPointF(cx, y + 6), QPointF(cx + 8, y))
        path.quadTo(QPointF(cx + 12, y - 3), QPointF(cx + 16, y))
        p.drawPath(path)
    elif kind == "smile":  # широкая улыбка «D»
        p.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 70)))
        path = QPainterPath(QPointF(cx - 17, y - 4))
        path.quadTo(QPointF(cx, y - 6), QPointF(cx + 17, y - 4))
        path.quadTo(QPointF(cx, y + 18), QPointF(cx - 17, y - 4))
        p.drawPath(path)
    elif kind == "frown":  # «︵»
        path = QPainterPath(QPointF(cx - 13, y + 6))
        path.quadTo(QPointF(cx, y - 6), QPointF(cx + 13, y + 6))
        p.drawPath(path)
    elif kind == "pout":  # надулась: маленькое «ε»
        path = QPainterPath(QPointF(cx - 8, y - 2))
        path.cubicTo(QPointF(cx - 6, y + 4), QPointF(cx - 1, y + 4), QPointF(cx, y))
        path.cubicTo(QPointF(cx + 1, y + 4), QPointF(cx + 6, y + 4), QPointF(cx + 8, y - 2))
        p.drawPath(path)
    elif kind == "tongue":  # дразнится: улыбка и язычок
        path = QPainterPath(QPointF(cx - 15, y - 3))
        path.quadTo(QPointF(cx, y + 9), QPointF(cx + 15, y - 3))
        p.drawPath(path)
        tongue = QPainterPath(QPointF(cx + 1, y + 3))
        tongue.quadTo(QPointF(cx + 5, y + 14), QPointF(cx + 10, y + 1))
        p.setBrush(QBrush(QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(), 120)))
        p.setPen(_pen(color, 2.0))
        p.drawPath(tongue)
    elif kind == "flat":  # «-»
        p.drawLine(QPointF(cx - 10, y + 1), QPointF(cx + 10, y))


def draw(p: QPainter, cx: float, eyes_y: float, mouth_y: float, s: FaceState,
         color: QColor) -> None:
    """Лицо целиком: брови, глаза, нос, рот, румянец."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    brow_y = eyes_y - EYE_H - 12 - s.brows
    for m in (-1, 1):
        ex = cx + m * EYE_DX
        if s.eyes not in ("x", "dizzy"):
            _brow(p, ex, brow_y - (s.brow_tilt if m > 0 else 0), m, color, s.brow_angle)
        _eye(p, ex, eyes_y, m, s, color)
        if s.blush:  # румянец «///»
            p.setPen(_pen(BLUSH, 1.7))
            for k in range(3):
                bx = ex + m * 10 + k * 5 - 5
                p.drawLine(QPointF(bx, eyes_y + 30), QPointF(bx + 4, eyes_y + 24))
    p.setPen(_pen(color, 1.6, 200))  # нос
    p.drawLine(QPointF(cx, eyes_y + 16), QPointF(cx - 1, eyes_y + 22))
    _mouth(p, cx, mouth_y, s.mouth, color)


# --- манга-эффекты вокруг лица ---

def sweat_drop(p: QPainter, x: float, y: float, t: int) -> None:
    """Капля пота у виска: смущение, неловкость."""
    y += (t % 2400) / 2400 * 6
    drop = QPainterPath(QPointF(x, y - 11))
    drop.quadTo(QPointF(x + 8, y + 2), QPointF(x, y + 6))
    drop.quadTo(QPointF(x - 8, y + 2), QPointF(x, y - 11))
    p.setPen(_pen(WATER, 1.8))
    p.setBrush(QColor(WATER.red(), WATER.green(), WATER.blue(), 70))
    p.drawPath(drop)
    p.setPen(_pen(WATER, 1.4, 200))
    p.drawLine(QPointF(x - 2.5, y - 1), QPointF(x - 3, y + 2))


def anger_mark(p: QPainter, x: float, y: float, t: int) -> None:
    """Знак злости «💢»: четыре изогнутых уголка, пульсирует."""
    k = 1 + 0.15 * math.sin(t / 110)
    p.setPen(_pen(ANGER, 2.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        corner = QPainterPath(QPointF(x + sx * 3 * k, y + sy * 10 * k))
        corner.quadTo(QPointF(x + sx * 3 * k, y + sy * 3 * k), QPointF(x + sx * 10 * k, y + sy * 3 * k))
        p.drawPath(corner)


def sparkles(p: QPainter, points: list[tuple[float, float]], t: int, color: QColor) -> None:
    """Искорки вокруг: восторг. Каждая мерцает со своим сдвигом."""
    for i, (x, y) in enumerate(points):
        r = 3 + 3.5 * max(0.0, math.sin(t / 220 + i * 1.9))
        if r > 3.3:
            _star(p, x, y, r, color)


def gloom(p: QPainter, x: float, y: float, width: float, t: int, color: QColor) -> None:
    """Тучка грусти: вертикальные штрихи над головой медленно ползут вниз."""
    for i in range(6):
        lx = x + i * width / 5
        drift = ((t / 900) + i * 0.37) % 1
        p.setPen(_pen(color, 1.5, int(160 * (1 - drift))))
        p.drawLine(QPointF(lx, y + drift * 8), QPointF(lx, y + 12 + drift * 8))
