"""Scheda "Analisi grafica": grafici di dettaglio per una strategia o il portafoglio."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..metrics import drawdown_info, equity_times, group_sum, hour_index, monthly_pnl, weekday_index
from ..portfolio import Entity, Portfolio
from . import theme
from .widgets import Card, HoverTip, bar_brushes, make_plot, set_category_ticks, span, zero_line


def _epoch(times: np.ndarray) -> np.ndarray:
    return times.astype("datetime64[s]").astype(np.int64).astype(float)


class AnalysisTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.portfolio: Portfolio | None = None
        self.entity: Entity | None = None
        self._data: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 0)
        bar = QHBoxLayout()
        label = QLabel("Analizza:")
        label.setObjectName("sectionTitle")
        bar.addWidget(label)
        self.selector = QComboBox()
        self.selector.setMinimumWidth(280)
        self.selector.currentIndexChanged.connect(self._selection_changed)
        bar.addWidget(self.selector)
        bar.addStretch(1)
        hint = QLabel("Rotella: zoom · trascina: sposta · tasto destro: esporta immagine")
        hint.setObjectName("hint")
        bar.addWidget(hint)
        outer.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 8, 0, 14)
        grid.setSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.eq_plot = make_plot("Equity curve", date_axis=True, min_height=280)
        self.dd_plot = make_plot("Drawdown", date_axis=True, min_height=280)
        self.dd_plot.setXLink(self.eq_plot)
        self.trade_plot = make_plot("P&L per trade", x_label="Trade n.", min_height=280)
        self.hist_plot = make_plot("Distribuzione del P&L per trade", money_y=False, money_x=True, y_label="N. trade", min_height=280)
        self.month_plot = make_plot("P&L mensile", min_height=280)
        self.hour_plot = make_plot("P&L per ora di entrata", x_label="Ora", min_height=280)
        self.weekday_plot = make_plot("P&L per giorno della settimana", min_height=280)
        self.mae_plot = make_plot("MAE vs risultato del trade", money_x=True, x_label="MAE ($)", min_height=280)
        self.exit_plot = make_plot("P&L per tipo di uscita", min_height=280)
        self.month_table = QTableWidget()
        self.month_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.month_table.verticalHeader().setVisible(False)
        self.month_table.setMinimumHeight(220)

        cards = [
            (self.eq_plot, 0, 0),
            (self.dd_plot, 0, 1),
            (self.trade_plot, 1, 0),
            (self.hist_plot, 1, 1),
            (self.month_plot, 2, 0),
            (self.hour_plot, 2, 1),
            (self.weekday_plot, 3, 0),
            (self.exit_plot, 3, 1),
            (self.mae_plot, 4, 0),
        ]
        for widget, row, col in cards:
            grid.addWidget(Card(widget), row, col)
        grid.addWidget(Card(self.month_table, "P&L mensile per anno"), 4, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        HoverTip(self.eq_plot, self._hover_equity)
        HoverTip(self.dd_plot, self._hover_dd)
        HoverTip(self.trade_plot, self._hover_trade)
        HoverTip(self.hist_plot, self._hover_hist)
        HoverTip(self.month_plot, lambda x, y: self._hover_bars("month", x))
        HoverTip(self.hour_plot, lambda x, y: self._hover_bars("hour", x))
        HoverTip(self.weekday_plot, lambda x, y: self._hover_bars("weekday", x))
        HoverTip(self.exit_plot, lambda x, y: self._hover_bars("exit", x))

    # ------------------------------------------------------------------ selezione
    def update_portfolio(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        current = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        for e in portfolio.entities():
            if e.is_combined:
                icon = theme.swatch_icon(theme.COMBINED)
            else:
                icon = theme.swatch_icon(theme.strategy_color(e.color_index), theme.strategy_line_style(e.color_index))
            self.selector.addItem(icon, e.name, e.key)
        idx = self.selector.findData(current)
        self.selector.setCurrentIndex(idx if idx >= 0 else 0)
        self.selector.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        if self.portfolio is None:
            return
        entity = self.portfolio.entity(self.selector.currentData())
        if entity is not None:
            self.entity = entity
            self._draw(entity)

    def _color(self, entity: Entity) -> str:
        return theme.COMBINED if entity.is_combined else theme.strategy_color(entity.color_index)

    # ------------------------------------------------------------------ disegno
    def _draw(self, entity: Entity) -> None:
        ts = entity.trades
        for plot in (
            self.eq_plot,
            self.dd_plot,
            self.trade_plot,
            self.hist_plot,
            self.month_plot,
            self.hour_plot,
            self.weekday_plot,
            self.mae_plot,
            self.exit_plot,
        ):
            plot.getPlotItem().clear()
        self._data = {}
        if ts.empty:
            return
        color = self._color(entity)
        x = _epoch(equity_times(ts))
        dd = drawdown_info(ts.profit)
        self._data["eq"] = (x, dd)

        # Equity + drawdown
        zero_line(self.eq_plot)
        self.eq_plot.plot(x, dd.equity, pen=pg.mkPen(color, width=2.2))
        if dd.max_dd > 0:
            self.eq_plot.addItem(
                pg.ScatterPlotItem(
                    [x[dd.peak_index], x[dd.trough_index]],
                    [dd.equity[dd.peak_index], dd.equity[dd.trough_index]],
                    symbol=["t", "t1"],
                    size=11,
                    brush=[pg.mkBrush(theme.MC_BEST), pg.mkBrush(theme.MC_WORST)],
                    pen=theme.ring_pen(1.5),
                )
            )
        zero_line(self.dd_plot)
        self.dd_plot.plot(x, dd.drawdown, pen=pg.mkPen(theme.NEGATIVE, width=1.6), fillLevel=0, brush=pg.mkBrush(theme.rgba(theme.NEGATIVE, 60)))
        if dd.max_dd > 0:
            self.dd_plot.addItem(
                pg.InfiniteLine(
                    pos=-dd.max_dd,
                    angle=0,
                    pen=pg.mkPen(theme.NEGATIVE, width=1, style=Qt.DashLine),
                    label=f"Max DD {theme.fmt_money(-dd.max_dd)}",
                    labelOpts={"position": 0.12, "color": theme.NEGATIVE_TEXT, "fill": theme.label_brush(220)},
                )
            )

        # P&L per trade
        n = len(ts)
        idx = np.arange(1, n + 1)
        self._data["trade"] = ts
        self.trade_plot.addItem(pg.BarGraphItem(x=idx, height=ts.profit, width=0.8, brushes=bar_brushes(ts.profit), pen=None))
        zero_line(self.trade_plot)

        # Istogramma
        bins = int(np.clip(np.sqrt(n) * 1.5, 10, 60))
        counts, edges = np.histogram(ts.profit, bins=bins)
        centers = (edges[:-1] + edges[1:]) / 2
        self._data["hist"] = (counts, edges)
        self.hist_plot.addItem(
            pg.BarGraphItem(
                x0=edges[:-1],
                x1=edges[1:],
                height=counts,
                brushes=bar_brushes(centers),
                pen=pg.mkPen(theme.SURFACE, width=1),
            )
        )
        mean_line = pg.InfiniteLine(
            pos=float(ts.profit.mean()),
            angle=90,
            pen=pg.mkPen(theme.MC_MEAN, width=1.5, style=Qt.DashLine),
            label=f"media {theme.fmt_money(ts.profit.mean())}",
            labelOpts={"position": 0.92, "color": theme.MC_MEAN, "fill": theme.label_brush(220)},
        )
        self.hist_plot.addItem(mean_line)

        # Mensile
        months, mpnl = monthly_pnl(ts)
        labels = [theme.fmt_month(m) for m in months]
        short = [f"{theme.MONTHS_IT[int(str(m)[5:7]) - 1]} {str(m)[2:4]}" for m in months]
        month_counts = group_sum(ts.exit_time.astype("datetime64[M]"), np.ones(n))[1]
        self._bars(self.month_plot, "month", labels, mpnl, counts=month_counts, axis_labels=short)

        # Ora di entrata
        hours = hour_index(ts.entry_time)
        h_keys, h_pnl = group_sum(hours, ts.profit)
        _, h_cnt = group_sum(hours, np.ones(n))
        self._bars(self.hour_plot, "hour", [f"{int(h):02d}:00" for h in h_keys], h_pnl, counts=h_cnt)

        # Giorno della settimana
        wd = weekday_index(ts.exit_time)
        w_keys, w_pnl = group_sum(wd, ts.profit)
        _, w_cnt = group_sum(wd, np.ones(n))
        self._bars(self.weekday_plot, "weekday", [theme.WEEKDAYS_IT[int(k)] for k in w_keys], w_pnl, counts=w_cnt)

        # Tipo di uscita
        exit_names = np.array([e or "(n/d)" for e in ts.exit_name], dtype=object)
        e_keys, e_pnl = group_sum(exit_names, ts.profit)
        _, e_cnt = group_sum(exit_names, np.ones(n))
        self._bars(self.exit_plot, "exit", [str(k) for k in e_keys], e_pnl, counts=e_cnt)

        # MAE vs P&L
        scatter = pg.ScatterPlotItem(
            x=ts.mae,
            y=ts.profit,
            data=np.arange(n),
            brush=bar_brushes(ts.profit),
            size=7,
            pen=theme.ring_pen(0.8),
            hoverable=True,
            hoverSize=11,
        )
        scatter.setToolTip(None)
        scatter.sigHovered.connect(self._scatter_hover)
        self.mae_plot.addItem(scatter)
        zero_line(self.mae_plot)

        self._fill_month_table(months, mpnl)
        for plot in (self.eq_plot, self.dd_plot, self.trade_plot, self.hist_plot, self.mae_plot):
            plot.getPlotItem().enableAutoRange()

    def _bars(
        self,
        plot: pg.PlotWidget,
        key: str,
        labels: list[str],
        values: np.ndarray,
        counts: np.ndarray,
        axis_labels: list[str] | None = None,
    ) -> None:
        xs = np.arange(len(values))
        plot.addItem(pg.BarGraphItem(x=xs, height=values, width=0.7, brushes=bar_brushes(values), pen=None))
        zero_line(plot)
        set_category_ticks(plot, axis_labels or labels, max_labels=10 if key == "month" else 24)
        self._data[key] = (labels, values, counts)
        plot.getPlotItem().enableAutoRange()

    def _fill_month_table(self, months: np.ndarray, pnl: np.ndarray) -> None:
        years = sorted({int(str(m)[:4]) for m in months})
        headers = ["Anno"] + [m.capitalize() for m in theme.MONTHS_IT] + ["Totale"]
        self.month_table.clear()
        self.month_table.setColumnCount(len(headers))
        self.month_table.setHorizontalHeaderLabels(headers)
        self.month_table.setRowCount(len(years))
        lookup = {str(m): v for m, v in zip(months, pnl)}
        for r, year in enumerate(years):
            year_item = QTableWidgetItem(str(year))
            font = year_item.font()
            font.setBold(True)
            year_item.setFont(font)
            self.month_table.setItem(r, 0, year_item)
            total = 0.0
            for mi in range(12):
                key = f"{year}-{mi + 1:02d}"
                if key in lookup:
                    v = float(lookup[key])
                    total += v
                    item = QTableWidgetItem(theme.fmt_money(v, 0))
                    item.setForeground(theme.tone_color(theme.value_tone("money", v)))
                else:
                    item = QTableWidgetItem("")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.month_table.setItem(r, mi + 1, item)
            tot = QTableWidgetItem(theme.fmt_money(total, 0))
            tot.setFont(font)
            tot.setForeground(theme.tone_color(theme.value_tone("money", total)))
            tot.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.month_table.setItem(r, 13, tot)
        head = self.month_table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Stretch)
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(13, QHeaderView.ResizeToContents)

    # ------------------------------------------------------------------ hover
    def _hover_equity(self, x: float, y: float):
        if "eq" not in self._data:
            return None
        xs, dd = self._data["eq"]
        i = int(np.searchsorted(xs, x, side="right")) - 1
        if i < 0:
            return None
        date = theme.fmt_datetime(np.datetime64(int(xs[i]), "s"))
        return (
            f"<b>{date}</b><br>Equity: {span(theme.INK, theme.fmt_money(dd.equity[i]), True)}"
            f"<br>Drawdown: {span(theme.NEGATIVE_TEXT, theme.fmt_money(dd.drawdown[i]))}"
        ), xs[i]

    _hover_dd = _hover_equity

    def _hover_trade(self, x: float, y: float):
        ts = self._data.get("trade")
        if ts is None:
            return None
        i = int(round(x)) - 1
        if i < 0 or i >= len(ts) or abs(x - (i + 1)) > 0.5:
            return None
        p = ts.profit[i]
        tone = theme.POSITIVE_TEXT if p >= 0 else theme.NEGATIVE_TEXT
        strategy = f"<br>{ts.strategy[i]}" if ts.strategy[i] else ""
        return (
            f"<b>Trade {i + 1}</b>{strategy}<br>{theme.fmt_datetime(ts.exit_time[i])}"
            f"<br>{ts.market_pos[i]} · {ts.exit_name[i]}<br>P&L: {span(tone, theme.fmt_money(p), True)}"
        ), None

    def _hover_hist(self, x: float, y: float):
        if "hist" not in self._data:
            return None
        counts, edges = self._data["hist"]
        i = int(np.searchsorted(edges, x, side="right")) - 1
        if i < 0 or i >= len(counts):
            return None
        return f"{theme.fmt_money(edges[i])} … {theme.fmt_money(edges[i + 1])}<br><b>{int(counts[i])} trade</b>", None

    def _hover_bars(self, key: str, x: float):
        if key not in self._data:
            return None
        labels, values, counts = self._data[key]
        i = int(round(x))
        if i < 0 or i >= len(values) or abs(x - i) > 0.45:
            return None
        v = float(values[i])
        tone = theme.POSITIVE_TEXT if v >= 0 else theme.NEGATIVE_TEXT
        return f"<b>{labels[i]}</b><br>P&L: {span(tone, theme.fmt_money(v), True)}<br>{int(counts[i])} trade", None

    def _scatter_hover(self, item, points, ev=None) -> None:
        if not len(points) or self.entity is None:
            item.setToolTip(None)
            return
        i = int(points[0].data())
        ts = self.entity.trades
        item.setToolTip(
            f"Trade {i + 1} · {theme.fmt_datetime(ts.exit_time[i])}\n"
            f"MAE {theme.fmt_money(ts.mae[i])} · MFE {theme.fmt_money(ts.mfe[i])}\n"
            f"P&L {theme.fmt_money(ts.profit[i])}"
        )
