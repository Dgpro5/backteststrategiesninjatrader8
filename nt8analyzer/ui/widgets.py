"""Widget riutilizzabili: grafici, tooltip al passaggio del mouse, riquadri KPI."""

from __future__ import annotations

from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme


class MoneyAxis(pg.AxisItem):
    """Asse con etichette in dollari."""

    def __init__(self, orientation: str = "left", **kwargs):
        super().__init__(orientation, **kwargs)
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing):
        decimals = 0 if spacing * scale >= 1 else 2
        out = []
        for v in values:
            v *= scale
            sign = "-" if v < 0 else ""
            out.append(f"{sign}${abs(v):,.{decimals}f}")
        return out


class PercentAxis(pg.AxisItem):
    def __init__(self, orientation: str = "left", **kwargs):
        super().__init__(orientation, **kwargs)
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing):
        decimals = 0 if spacing * scale * 100 >= 1 else 1
        return [f"{v * scale * 100:.{decimals}f}%" for v in values]


def make_plot(
    title: str | None = None,
    date_axis: bool = False,
    money_y: bool = True,
    percent_y: bool = False,
    money_x: bool = False,
    x_label: str | None = None,
    y_label: str | None = None,
    min_height: int = 240,
) -> pg.PlotWidget:
    axes = {}
    if date_axis:
        axes["bottom"] = pg.DateAxisItem(orientation="bottom", utcOffset=0)
    elif money_x:
        axes["bottom"] = MoneyAxis("bottom")
    if percent_y:
        axes["left"] = PercentAxis("left")
    elif money_y:
        axes["left"] = MoneyAxis("left")
    widget = pg.PlotWidget(axisItems=axes)
    item = widget.getPlotItem()
    item.showGrid(x=True, y=True, alpha=0.12)
    if title:
        item.setTitle(title, color=theme.INK, size="10.5pt", bold=True)
    for name in ("left", "bottom"):
        axis = item.getAxis(name)
        axis.setPen(pg.mkPen(theme.BORDER))
        axis.setTextPen(pg.mkPen(theme.INK_2))
    if x_label:
        item.setLabel("bottom", x_label, color=theme.INK_2)
    if y_label:
        item.setLabel("left", y_label, color=theme.INK_2)
    widget.setMinimumHeight(min_height)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return widget


def add_legend(widget: pg.PlotWidget, columns: int = 1) -> pg.LegendItem:
    legend = widget.getPlotItem().addLegend(
        offset=(10, 10),
        brush=pg.mkBrush(255, 255, 255, 225),
        pen=pg.mkPen(theme.BORDER),
        labelTextColor=theme.INK,
        colCount=columns,
    )
    return legend


def zero_line(widget: pg.PlotWidget, y: float = 0.0) -> pg.InfiniteLine:
    line = pg.InfiniteLine(pos=y, angle=0, movable=False, pen=pg.mkPen(theme.MUTED, width=1))
    widget.getPlotItem().addItem(line, ignoreBounds=True)
    return line


def bar_brushes(values: np.ndarray) -> list:
    pos, neg = pg.mkBrush(theme.POSITIVE), pg.mkBrush(theme.NEGATIVE)
    return [pos if v >= 0 else neg for v in values]


def set_category_ticks(widget: pg.PlotWidget, labels: list[str], max_labels: int = 24) -> None:
    step = max(1, int(np.ceil(len(labels) / max_labels)))
    ticks = [(i, lab) for i, lab in enumerate(labels) if i % step == 0]
    widget.getPlotItem().getAxis("bottom").setTicks([ticks, []])


class HoverTip(QObject):
    """Mostra un riquadro informativo (e una linea verticale) seguendo il mouse.

    ``callback(x, y)`` restituisce ``None`` (nascondi) oppure ``(html, x_linea)``
    dove ``x_linea`` può essere ``None`` per non disegnare la linea.
    """

    def __init__(self, widget: pg.PlotWidget, callback: Callable[[float, float], tuple | None]):
        super().__init__(widget)
        self.widget = widget
        self.plot = widget.getPlotItem()
        self.callback = callback
        self.text = pg.TextItem(
            anchor=(0, 1),
            fill=pg.mkBrush(255, 255, 255, 240),
            border=pg.mkPen(theme.BORDER),
            color=theme.INK,
        )
        self.text.setZValue(1000)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(theme.MUTED, width=1, style=Qt.DashLine))
        self.vline.setZValue(999)
        self._attach()
        self._hide()
        self.proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=45, slot=self._moved)
        widget.viewport().installEventFilter(self)

    def _attach(self) -> None:
        if self.text.scene() is None:
            self.plot.addItem(self.text, ignoreBounds=True)
        if self.vline.scene() is None:
            self.plot.addItem(self.vline, ignoreBounds=True)

    def _hide(self) -> None:
        self.text.hide()
        self.vline.hide()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Leave:
            self._hide()
        return False

    def _moved(self, event) -> None:
        pos = event[0]
        vb = self.plot.vb
        if not vb.sceneBoundingRect().contains(pos):
            self._hide()
            return
        point = vb.mapSceneToView(pos)
        try:
            result = self.callback(point.x(), point.y())
        except Exception:  # un errore nel tooltip non deve bloccare l'interfaccia
            result = None
        if not result:
            self._hide()
            return
        self._attach()
        html, x_line = result
        self.text.setHtml(f"<div style='font-size:9pt'>{html}</div>")
        (x0, x1), (y0, y1) = vb.viewRange()
        ax = 1.0 if point.x() > (x0 + x1) / 2 else 0.0
        ay = 0.0 if point.y() > (y0 + y1) / 2 else 1.0
        self.text.setAnchor((ax, ay))
        self.text.setPos(point.x(), point.y())
        self.text.show()
        if x_line is not None:
            self.vline.setPos(x_line)
            self.vline.show()
        else:
            self.vline.hide()


def span(color: str, text: str, bold: bool = False) -> str:
    weight = "font-weight:600;" if bold else ""
    return f"<span style='color:{color};{weight}'>{text}</span>"


def swatch_html(color: str) -> str:
    return f"<span style='color:{color};font-size:11pt'>&#9632;</span>"


class Card(QFrame):
    """Riquadro con titolo opzionale che contiene un widget."""

    def __init__(self, widget: QWidget, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        if title:
            label = QLabel(title)
            label.setObjectName("cardTitle")
            layout.addWidget(label)
        layout.addWidget(widget)


class KpiTile(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("kpiTitle")
        self.value = QLabel("—")
        self.value.setObjectName("kpiValue")
        self.sub = QLabel("")
        self.sub.setObjectName("kpiSub")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.sub)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, text: str, tone: int = 0, sub: str = "") -> None:
        self.value.setText(text)
        color = theme.POSITIVE_TEXT if tone > 0 else theme.NEGATIVE_TEXT if tone < 0 else theme.INK
        self.value.setStyleSheet(f"color: {color};")
        self.sub.setText(sub)
