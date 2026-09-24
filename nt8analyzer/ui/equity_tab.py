"""Scheda "Equity curve": curve per strategia, curva combinata (nera) e drawdown."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..metrics import daily_correlation, drawdown_info, equity_times
from ..portfolio import Entity, Portfolio
from . import theme
from .widgets import Card, HoverTip, add_legend, make_plot, span, swatch_html, zero_line


def _epoch(times: np.ndarray) -> np.ndarray:
    return times.astype("datetime64[s]").astype(np.int64).astype(float)


class _Series:
    def __init__(self, entity: Entity):
        self.entity = entity
        self.x = _epoch(equity_times(entity.trades))
        self.dd = drawdown_info(entity.trades.profit)
        if entity.is_combined:
            self.color, self.style, self.width = theme.COMBINED, Qt.SolidLine, 3.0
        else:
            self.color = theme.strategy_color(entity.color_index)
            self.style = theme.strategy_line_style(entity.color_index)
            self.width = 1.8
        self.eq_item = None
        self.dd_item = None
        self.visible = True

    def value_at(self, x: float, values: np.ndarray) -> float | None:
        i = int(np.searchsorted(self.x, x, side="right")) - 1
        if i < 0:
            return None
        return float(values[min(i, len(values) - 1)])


class EquityTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.series: list[_Series] = []
        self._has_combined = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("sectionTitle")
        top.addWidget(self.info)
        top.addStretch(1)
        self.show_single_dd = QCheckBox("Mostra anche il drawdown delle singole strategie")
        self.show_single_dd.setChecked(False)
        self.show_single_dd.toggled.connect(self._apply_visibility)
        top.addWidget(self.show_single_dd)
        layout.addLayout(top)

        self.eq_plot = make_plot("Equity curve (profitto netto cumulativo)", date_axis=True, min_height=260)
        self.eq_legend = add_legend(self.eq_plot)
        self.dd_plot = make_plot("Drawdown (dal massimo precedente)", date_axis=True, min_height=170)
        self.dd_plot.setXLink(self.eq_plot)
        HoverTip(self.eq_plot, self._hover_equity)
        HoverTip(self.dd_plot, self._hover_drawdown)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._item_changed)

        self.corr = QTableWidget()
        self.corr.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.corr.setSelectionMode(QAbstractItemView.NoSelection)
        self.corr_card = Card(self.corr, "Correlazione del P&L giornaliero")

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(Card(self.table, "Max drawdown e risultati (spunta per mostrare/nascondere la curva)"))
        bottom.addWidget(self.corr_card)
        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 2)
        bottom.setSizes([900, 520])

        self.table.setMinimumHeight(110)
        self.corr.setMinimumHeight(110)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(Card(self.eq_plot))
        splitter.addWidget(Card(self.dd_plot))
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([430, 220, 230])
        layout.addWidget(splitter, 1)

    # ------------------------------------------------------------------ dati
    def update_portfolio(self, portfolio: Portfolio) -> None:
        previous_hidden = {s.entity.key for s in self.series if not s.visible}
        self.series = [_Series(e) for e in portfolio.strategies]
        if portfolio.combined is not None:
            self.series.append(_Series(portfolio.combined))
        for s in self.series:
            s.visible = s.entity.key not in previous_hidden
        self._has_combined = portfolio.combined is not None
        self.show_single_dd.setVisible(self._has_combined)
        self._draw(portfolio)
        self._fill_table()
        self._fill_correlation(portfolio)
        n = len(portfolio.strategies)
        if portfolio.combined is not None:
            dd = portfolio.combined.stats["max_dd"]
            self.info.setText(f"{n} strategie  ·  Max drawdown combinato: {theme.fmt_money(-dd)}")
        else:
            self.info.setText("Carica almeno 2 strategie per vedere la curva combinata (nera)")

    def _draw(self, portfolio: Portfolio) -> None:
        self.eq_plot.getPlotItem().clear()
        self.dd_plot.getPlotItem().clear()
        self.eq_legend.clear()
        self.eq_legend.setColumnCount(1 if len(self.series) <= 6 else 2 if len(self.series) <= 14 else 3)
        zero_line(self.eq_plot)
        zero_line(self.dd_plot)
        primary_key = portfolio.primary.key if portfolio.primary else None
        for s in self.series:
            pen = pg.mkPen(s.color, width=s.width, style=s.style)
            s.eq_item = self.eq_plot.plot(s.x, s.dd.equity, pen=pen, name=s.entity.name)
            if s.entity.key == primary_key:
                fill = QColor(s.color)
                fill.setAlpha(38 if s.entity.is_combined else 55)
                s.eq_item.setZValue(10)
                s.dd_item = self.dd_plot.plot(s.x, s.dd.drawdown, pen=pen, fillLevel=0, brush=pg.mkBrush(fill))
                s.dd_item.setZValue(10)
            else:
                s.dd_item = self.dd_plot.plot(s.x, s.dd.drawdown, pen=pg.mkPen(s.color, width=1.3, style=s.style))
        self._mark_max_dd(portfolio)
        self._apply_visibility()
        self.eq_plot.getPlotItem().enableAutoRange()
        self.dd_plot.getPlotItem().enableAutoRange()

    def _mark_max_dd(self, portfolio: Portfolio) -> None:
        primary = portfolio.primary
        target = next((s for s in self.series if s.entity.key == primary.key), None)
        if target is None or target.dd.max_dd <= 0:
            return
        info = target.dd
        xp, xt = target.x[info.peak_index], target.x[info.trough_index]
        yp, yt = info.equity[info.peak_index], info.equity[info.trough_index]
        markers = pg.ScatterPlotItem(
            [xp, xt],
            [yp, yt],
            symbol=["t", "t1"],
            size=12,
            brush=[pg.mkBrush(theme.MC_BEST), pg.mkBrush(theme.MC_WORST)],
            pen=pg.mkPen("#ffffff", width=1.5),
        )
        markers.setZValue(20)
        self.eq_plot.addItem(markers)
        who = "combinato" if primary.is_combined else primary.name
        label = pg.TextItem(
            html=(
                f"<div style='font-size:9pt'><b>Max DD {who}</b><br>"
                f"{span(theme.NEGATIVE_TEXT, theme.fmt_money(-info.max_dd), True)}</div>"
            ),
            anchor=(0.5, -0.15),
            fill=pg.mkBrush(255, 255, 255, 220),
            border=pg.mkPen(theme.BORDER),
        )
        label.setPos(xt, yt)
        label.setZValue(21)
        self.eq_plot.addItem(label, ignoreBounds=True)

        line = pg.InfiniteLine(
            pos=-info.max_dd,
            angle=0,
            movable=False,
            pen=pg.mkPen(theme.MC_WORST, width=1.2, style=Qt.DashLine),
            label=f"Max DD {who}: {theme.fmt_money(-info.max_dd)}",
            labelOpts={"position": 0.12, "color": theme.NEGATIVE_TEXT, "fill": pg.mkBrush(255, 255, 255, 220)},
        )
        self.dd_plot.addItem(line)
        trough = pg.ScatterPlotItem([xt], [-info.max_dd], symbol="o", size=9, brush=pg.mkBrush(theme.MC_WORST), pen=pg.mkPen("#ffffff"))
        trough.setZValue(20)
        self.dd_plot.addItem(trough)

    def _apply_visibility(self) -> None:
        for s in self.series:
            if s.eq_item is None:
                continue
            s.eq_item.setVisible(s.visible)
            show_dd = s.visible and (
                s.entity.is_combined or self.show_single_dd.isChecked() or not self._has_combined
            )
            s.dd_item.setVisible(show_dd)

    # ------------------------------------------------------------------ tabelle
    COLUMNS = [
        "Serie",
        "Trade",
        "Profitto netto",
        "Max DD ($)",
        "Max DD (%)",
        "Inizio DD",
        "Minimo DD",
        "Durata DD (gg)",
        "Recovery factor",
        "Profit factor",
        "Peggior giorno",
        "Max perdite consec.",
    ]

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.clear()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setRowCount(len(self.series))
        for r, s in enumerate(self.series):
            st = s.entity.stats
            name = QTableWidgetItem(s.entity.name)
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked if s.visible else Qt.Unchecked)
            name.setIcon(theme.swatch_icon(s.color, s.style))
            name.setData(Qt.UserRole, r)
            values = [
                ("int", st["n_trades"]),
                ("money", st["net_profit"]),
                ("money", -st["max_dd"] if st["max_dd"] else 0.0),
                ("pct", st["max_dd_pct"]),
                ("datetime", st["max_dd_start"]),
                ("datetime", st["max_dd_trough"]),
                ("num1", st["max_dd_days"]),
                ("ratio", st["recovery_factor"]),
                ("ratio", st["profit_factor"]),
                ("money_date", st["worst_day"]),
                ("int", st["max_consec_losses"]),
            ]
            self.table.setItem(r, 0, name)
            for c, (kind, value) in enumerate(values, start=1):
                item = QTableWidgetItem(theme.fmt_value(kind, value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setForeground(theme.tone_color(theme.value_tone(kind, value)))
                self.table.setItem(r, c, item)
            if s.entity.is_combined:
                for c in range(len(self.COLUMNS)):
                    item = self.table.item(r, c)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setBackground(QBrush(QColor("#efeee9")))
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setStretchLastSection(True)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self.series):
            return
        self.series[idx].visible = item.checkState() == Qt.Checked
        self._apply_visibility()

    def _fill_correlation(self, portfolio: Portfolio) -> None:
        entities = portfolio.strategies
        self.corr_card.setVisible(len(entities) >= 2)
        if len(entities) < 2:
            return
        matrix = daily_correlation([e.trades for e in entities])
        names = [e.name for e in entities]
        self.corr.clear()
        self.corr.setRowCount(len(names))
        self.corr.setColumnCount(len(names))
        self.corr.setHorizontalHeaderLabels([str(i + 1) for i in range(len(names))])
        self.corr.setVerticalHeaderLabels([f"{i + 1}. {n}" for i, n in enumerate(names)])
        for i, n in enumerate(names):
            self.corr.horizontalHeaderItem(i).setToolTip(n)
        for i in range(len(names)):
            for j in range(len(names)):
                v = float(matrix[i, j])
                item = QTableWidgetItem(f"{v:+.2f}")
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QBrush(_diverging(v)))
                self.corr.setItem(i, j, item)
        head = self.corr.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Stretch if len(names) <= 5 else QHeaderView.ResizeToContents)
        head.setMinimumSectionSize(52)
        self.corr.setToolTip(
            "Correlazione del P&L giornaliero: valori bassi o negativi indicano strategie che si diversificano."
        )

    # ------------------------------------------------------------------ hover
    def _visible_series(self):
        return [s for s in self.series if s.visible and len(s.x)]

    @staticmethod
    def _date_header(x: float) -> str:
        return theme.fmt_datetime(np.datetime64(int(x), "s"))

    def _hover_equity(self, x: float, y: float):
        rows = []
        for s in self._visible_series():
            v = s.value_at(x, s.dd.equity)
            if v is not None:
                rows.append(f"{swatch_html(s.color)} {s.entity.name}: {span(theme.INK, theme.fmt_money(v), True)}")
        if not rows:
            return None
        return f"<b>{self._date_header(x)}</b><br>" + "<br>".join(rows), x

    def _hover_drawdown(self, x: float, y: float):
        rows = []
        for s in self._visible_series():
            if not s.dd_item.isVisible():
                continue
            v = s.value_at(x, s.dd.drawdown)
            if v is not None:
                rows.append(f"{swatch_html(s.color)} {s.entity.name}: {span(theme.NEGATIVE_TEXT if v < 0 else theme.INK, theme.fmt_money(v), True)}")
        if not rows:
            return None
        return f"<b>{self._date_header(x)}</b><br>" + "<br>".join(rows), x


def _diverging(value: float) -> QColor:
    """Blu per correlazione negativa, rosso per positiva, grigio neutro a 0."""
    value = max(-1.0, min(1.0, value))
    neutral = np.array([240, 239, 236])
    pole = np.array([224, 104, 103]) if value > 0 else np.array([109, 167, 236])
    rgb = neutral + (pole - neutral) * abs(value) * 0.8
    return QColor(*[int(c) for c in rgb])
