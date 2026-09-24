"""Disegna l'icona dell'app (PNG + ICO) con Qt.

Uso: python tools/genera_icona.py
"""

from __future__ import annotations

import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "nt8analyzer", "assets")


def _walk(rng: random.Random, n: int, drift: float, vol: float) -> list[float]:
    y, out = 0.0, [0.0]
    for _ in range(n):
        y += drift + rng.gauss(0, vol)
        out.append(y)
    return out


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw(size: int = 256) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#fcfcfb"))
    p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 44, 44)
    p.setPen(QPen(QColor("#d6d5ce"), 4))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 44, 44)

    left, right, top, bottom = 40.0, size - 36.0, 44.0, size - 44.0
    rng = random.Random(4)
    paths = [_walk(rng, 24, 1.0, 1.8) for _ in range(14)]
    lo = min(min(w) for w in paths)
    hi = max(max(w) for w in paths)

    def to_path(values: list[float]) -> QPainterPath:
        path = QPainterPath()
        for i, v in enumerate(values):
            x = left + (right - left) * i / (len(values) - 1)
            y = bottom - (bottom - top) * (v - lo) / (hi - lo)
            path.moveTo(QPointF(x, y)) if i == 0 else path.lineTo(QPointF(x, y))
        return path

    finals = [w[-1] for w in paths]
    best, worst = finals.index(max(finals)), finals.index(min(finals))
    for i, w in enumerate(paths):
        if i not in (best, worst):
            p.setPen(_pen(QColor(140, 140, 140, 110), 4))
            p.drawPath(to_path(w))
    mean = [sum(w[i] for w in paths) / len(paths) for i in range(len(paths[0]))]
    for values, color, width in ((paths[worst], "#d03b3b", 9), (paths[best], "#0a8f0a", 9), (mean, "#2a78d6", 9)):
        p.setPen(_pen(QColor(color), width))
        p.drawPath(to_path(values))
    p.end()
    return img


def main() -> None:
    app = QGuiApplication(sys.argv[:1])  # necessario per QPainter
    os.makedirs(OUT_DIR, exist_ok=True)
    image = draw(256)
    png = os.path.join(OUT_DIR, "icon.png")
    ico = os.path.join(OUT_DIR, "icon.ico")
    assert image.save(png), png
    assert image.save(ico, "ICO"), ico
    print(png)
    print(ico)
    app.quit()


if __name__ == "__main__":
    main()
