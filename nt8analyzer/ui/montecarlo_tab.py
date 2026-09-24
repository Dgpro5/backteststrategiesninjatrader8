"""Scheda "Monte Carlo": shuffler/bootstrap dei trade con caso migliore, medio e peggiore."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..montecarlo import (
    BOOTSTRAP,
    RANK_FINAL,
    RANK_MAX_DD,
    RANK_RATIO,
    SHUFFLE,
    MCConfig,
    MCResult,
    run_monte_carlo,
)
from ..portfolio import Portfolio
from . import theme
from .widgets import Card, HoverTip, KpiTile, add_legend, make_plot, span, swatch_html, zero_line

AUTO_RUN_LIMIT = 6_000_000  # simulazioni x trade oltre cui non si ricalcola in automatico
MAX_POINTS_PER_LINE = 700

HIST_METRICS = [
    ("max_dd", "Max drawdown ($)", "money"),
    ("max_dd_pct", "Max drawdown (%)", "pct"),
    ("max_consec_loss_amount", "Max perdita consecutiva ($)", "money"),
    ("max_consec_losses", "Max perdite consecutive (n. trade)", "int"),
    ("dd_length", "Durata max drawdown (n. trade)", "int"),
    ("final", "Profitto netto finale", "money"),
]


class MonteCarloTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.portfolio: Portfolio | None = None
        self.result: MCResult | None = None
        self.settings = QSettings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # --- controlli
        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        self.series_box = QComboBox()
        self.series_box.setMinimumWidth(170)
        self.series_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.series_box.setMinimumContentsLength(18)
        self.sims = QSpinBox()
        self.sims.setRange(10, 100_000)
        self.sims.setSingleStep(500)
        self.sims.setGroupSeparatorShown(True)
        self.sims.setValue(int(self.settings.value("mc/sims", 1000)))
        self.method = QComboBox()
        self.method.addItem("Shuffle (rimescola l'ordine)", SHUFFLE)
        self.method.addItem("Bootstrap (con reinserimento)", BOOTSTRAP)
        self.method.setCurrentIndex(max(0, self.method.findData(self.settings.value("mc/method", SHUFFLE))))
        self.n_trades = QSpinBox()
        self.n_trades.setRange(0, 1_000_000)
        self.n_trades.setSpecialValueText("come l'originale")
        self.n_trades.setToolTip("Numero di trade per simulazione (solo bootstrap). 0 = stesso numero dell'originale")
        self.rank = QComboBox()
        self.rank.addItem("Max drawdown", RANK_MAX_DD)
        self.rank.addItem("Profitto finale", RANK_FINAL)
        self.rank.addItem("Profitto / max drawdown", RANK_RATIO)
        self.rank.setCurrentIndex(max(0, self.rank.findData(self.settings.value("mc/rank", RANK_MAX_DD))))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 100_000_000)
        self.threshold.setDecimals(0)
        self.threshold.setSingleStep(250)
        self.threshold.setPrefix("$ ")
        self.threshold.setGroupSeparatorShown(True)
        self.threshold.setValue(float(self.settings.value("mc/threshold", 2000)))
        self.threshold.setToolTip("Calcola la probabilità che il max drawdown raggiunga questa soglia (es. limite prop firm)")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_000_000_000)
        self.seed.setSpecialValueText("casuale")
        self.seed.setToolTip("Seed del generatore casuale: 0 = diverso ad ogni esecuzione, altro = risultati ripetibili")
        self.show_original = QCheckBox("Mostra originale")
        self.show_original.setToolTip("Mostra la sequenza reale dei trade (arancione tratteggiata)")
        self.run_btn = QPushButton("Esegui simulazione")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run)

        def lab(text: str) -> QLabel:
            label = QLabel(text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return label

        controls.addWidget(lab("Serie:"), 0, 0)
        controls.addWidget(self.series_box, 0, 1)
        controls.addWidget(lab("Simulazioni:"), 0, 2)
        controls.addWidget(self.sims, 0, 3)
        controls.addWidget(lab("Metodo:"), 0, 4)
        controls.addWidget(self.method, 0, 5)
        controls.addWidget(lab("N. trade:"), 0, 6)
        controls.addWidget(self.n_trades, 0, 7)
        rank_label = lab("Criterio:")
        rank_label.setToolTip("Criterio con cui scegliere la curva migliore (verde) e peggiore (rossa)")
        self.rank.setToolTip(rank_label.toolTip())
        controls.addWidget(rank_label, 1, 0)
        controls.addWidget(self.rank, 1, 1)
        controls.addWidget(lab("Soglia DD:"), 1, 2)
        controls.addWidget(self.threshold, 1, 3)
        controls.addWidget(lab("Seed:"), 1, 4)
        controls.addWidget(self.seed, 1, 5)
        controls.addWidget(self.show_original, 1, 6)
        controls.addWidget(self.run_btn, 1, 7)
        controls.setColumnStretch(8, 1)
        layout.addLayout(controls)

        self.method.currentIndexChanged.connect(self._method_changed)
        self.show_original.toggled.connect(self._toggle_original)
        self.threshold.valueChanged.connect(self._update_kpis)
        self._method_changed()

        # --- KPI
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)
        self.kpi_worst = KpiTile("Max DD · peggiore")
        self.kpi_mean = KpiTile("Max DD · medio")
        self.kpi_best = KpiTile("Max DD · migliore")
        self.kpi_95 = KpiTile("Max DD · conf. 95%")
        self.kpi_streak = KpiTile("Perdita consec. · peggiore")
        self.kpi_prob = KpiTile("Prob. DD ≥ soglia")
        self.kpi_loss = KpiTile("Prob. profitto < 0")
        for tile in (self.kpi_worst, self.kpi_mean, self.kpi_best, self.kpi_95, self.kpi_streak, self.kpi_prob, self.kpi_loss):
            kpi_row.addWidget(tile)
        layout.addLayout(kpi_row)

        # --- grafico principale
        self.plot = make_plot("Simulazioni Monte Carlo", x_label="Numero trade", y_label="Profitto cumulativo", min_height=300)
        self.legend = add_legend(self.plot)
        HoverTip(self.plot, self._hover)
        self.original_item = None
        self._gray_sample = None

        # --- tabella e istogramma
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        hist_box = QWidget()
        hist_layout = QVBoxLayout(hist_box)
        hist_layout.setContentsMargins(0, 0, 0, 0)
        hist_top = QHBoxLayout()
        hist_top.addWidget(QLabel("Distribuzione:"))
        self.hist_metric = QComboBox()
        for key, label, _ in HIST_METRICS:
            self.hist_metric.addItem(label, key)
        self.hist_metric.currentIndexChanged.connect(self._draw_histogram)
        hist_top.addWidget(self.hist_metric, 1)
        hist_layout.addLayout(hist_top)
        self.hist_plot = make_plot(money_y=False, y_label="N. simulazioni", min_height=200)
        HoverTip(self.hist_plot, self._hover_hist)
        hist_layout.addWidget(self.hist_plot, 1)
        self._hist_data = None

        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)

        table_box = QWidget()
        tb_layout = QVBoxLayout(table_box)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.addWidget(self.table, 1)
        tb_layout.addWidget(self.note)

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(Card(table_box, "Statistiche Monte Carlo: caso migliore, medio e peggiore"))
        bottom.addWidget(Card(hist_box))
        bottom.setStretchFactor(0, 5)
        bottom.setStretchFactor(1, 3)
        bottom.setSizes([1000, 560])

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(Card(self.plot))
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 400])
        layout.addWidget(splitter, 1)

    # ------------------------------------------------------------------ dati
    def _method_changed(self) -> None:
        self.n_trades.setEnabled(self.method.currentData() == BOOTSTRAP)

    def update_portfolio(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        current = self.series_box.currentData()
        self.series_box.blockSignals(True)
        self.series_box.clear()
        for e in portfolio.entities():
            if e.is_combined:
                icon = theme.swatch_icon(theme.COMBINED)
            else:
                icon = theme.swatch_icon(theme.strategy_color(e.color_index), theme.strategy_line_style(e.color_index))
            self.series_box.addItem(icon, f"{e.name} ({len(e.trades)} trade)", e.key)
        idx = self.series_box.findData(current)
        self.series_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.series_box.blockSignals(False)
        entity = portfolio.entity(self.series_box.currentData())
        if entity is not None and self.sims.value() * len(entity.trades) <= AUTO_RUN_LIMIT:
            self.run()
        else:
            self._clear("Dati cambiati: premi «Esegui simulazione» per ricalcolare.")

    def _clear(self, message: str) -> None:
        self.result = None
        self.plot.getPlotItem().clear()
        self.legend.clear()
        self.hist_plot.getPlotItem().clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.note.setText(message)
        for tile in (self.kpi_worst, self.kpi_mean, self.kpi_best, self.kpi_95, self.kpi_streak, self.kpi_prob, self.kpi_loss):
            tile.set("—")

    def config(self) -> MCConfig:
        return MCConfig(
            n_sims=self.sims.value(),
            method=self.method.currentData(),
            n_trades=self.n_trades.value() or None,
            capital=self.portfolio.capital if self.portfolio else 0.0,
            seed=self.seed.value() or None,
            rank_by=self.rank.currentData(),
            dd_threshold=self.threshold.value(),
            max_plot=1000,
        )

    def run(self) -> None:
        if self.portfolio is None or self.portfolio.empty:
            return
        entity = self.portfolio.entity(self.series_box.currentData())
        if entity is None or entity.trades.empty:
            return
        cfg = self.config()
        self.settings.setValue("mc/sims", cfg.n_sims)
        self.settings.setValue("mc/method", cfg.method)
        self.settings.setValue("mc/rank", cfg.rank_by)
        self.settings.setValue("mc/threshold", cfg.dd_threshold)

        n = cfg.n_trades if (cfg.method == BOOTSTRAP and cfg.n_trades) else len(entity.trades)
        dialog = None
        if cfg.n_sims * n > 3_000_000:
            dialog = QProgressDialog("Simulazione Monte Carlo in corso…", "Annulla", 0, cfg.n_sims, self)
            dialog.setWindowModality(Qt.WindowModal)
            dialog.setMinimumDuration(300)

        def progress(done: int, total: int) -> bool:
            if dialog is None:
                return True
            dialog.setValue(done)
            QApplication.processEvents()
            return not dialog.wasCanceled()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = run_monte_carlo(entity.trades.profit, cfg, progress)
        finally:
            QApplication.restoreOverrideCursor()
            if dialog is not None:
                dialog.close()
        self._entity_name = entity.name
        self._draw()

    # ------------------------------------------------------------------ disegno
    def _draw(self) -> None:
        r = self.result
        if r is None:
            return
        item = self.plot.getPlotItem()
        item.clear()
        self.legend.clear()
        zero_line(self.plot)
        n = r.n_trades
        k = len(r.plot_curves)
        if k:
            step_idx = np.unique(np.linspace(0, n, min(n + 1, MAX_POINTS_PER_LINE)).round().astype(int))
            m = len(step_idx)
            xs = np.tile(step_idx.astype(float), k)
            ys = r.plot_curves[:, step_idx].ravel()
            connect = np.ones(k * m, dtype=bool)
            connect[m - 1 :: m] = False
            alpha = 70 if k <= 100 else 40 if k <= 400 else 26 if k <= 1000 else 18
            gray = pg.PlotCurveItem(
                xs,
                ys,
                connect=connect,
                pen=pg.mkPen(QColor(140, 140, 140, alpha), width=1),
                antialias=False,
                skipFiniteCheck=True,
            )
            item.addItem(gray)
            shown = f"{k:,} di {r.n_sims:,}" if k < r.n_sims else f"{r.n_sims:,}"
            # Campione di legenda opaco: le linee vere sono quasi trasparenti.
            self._gray_sample = pg.PlotDataItem([], [], pen=pg.mkPen(theme.MC_GRAY, width=2))
            self.legend.addItem(self._gray_sample, f"Simulazioni ({shown})")

        x = np.arange(n + 1, dtype=float)
        rank_label = {RANK_MAX_DD: "drawdown", RANK_FINAL: "profitto", RANK_RATIO: "profitto/DD"}[r.config.rank_by]
        best = item.plot(x, r.best_curve, pen=pg.mkPen(theme.MC_BEST, width=2.4))
        worst = item.plot(x, r.worst_curve, pen=pg.mkPen(theme.MC_WORST, width=2.4))
        mean = item.plot(x, r.mean_curve, pen=pg.mkPen(theme.MC_MEAN, width=2.6))
        for curve, z in ((best, 10), (worst, 11), (mean, 12)):
            curve.setZValue(z)
        self.legend.addItem(best, f"Migliore (per {rank_label})")
        self.legend.addItem(worst, f"Peggiore (per {rank_label})")
        self.legend.addItem(mean, "Media")
        self.original_item = item.plot(
            np.arange(len(r.original_curve), dtype=float),
            r.original_curve,
            pen=pg.mkPen(theme.MC_ORIGINAL, width=1.8, style=Qt.DashLine),
        )
        self.original_item.setZValue(13)
        self._toggle_original(self.show_original.isChecked())
        item.enableAutoRange()

        method = "Shuffle" if r.config.method == SHUFFLE else "Bootstrap"
        item.setTitle(
            f"Monte Carlo · {self._entity_name} · {r.n_sims:,} simulazioni · {method} · {n:,} trade",
            color=theme.INK,
            size="10.5pt",
            bold=True,
        )
        self._fill_table()
        self._update_kpis()
        self._draw_histogram()
        if r.config.method == SHUFFLE:
            self.note.setText(
                "Shuffle: gli stessi trade in ordine casuale, quindi profitto finale, expectancy e profit factor "
                "non cambiano; cambiano drawdown e serie di perdite. «Confidenza 95%» = valore non superato nel "
                "95% delle simulazioni. Migliore/peggiore sono il valore più favorevole/sfavorevole di ogni riga."
            )
        else:
            self.note.setText(
                "Bootstrap: i trade vengono estratti a caso con reinserimento, quindi variano anche profitto finale "
                "e percentuale vincenti. «Confidenza 95%» = valore non superato nel 95% delle simulazioni."
            )

    def _toggle_original(self, checked: bool) -> None:
        if self.original_item is None or self.original_item.scene() is None:
            return
        self.original_item.setVisible(checked)
        self.legend.removeItem(self.original_item)
        if checked:
            self.legend.addItem(self.original_item, "Sequenza originale")

    def _fill_table(self) -> None:
        r = self.result
        headers = ["Metrica", "Migliore", "Media", "Mediana", "Conf. 95%", "Conf. 99%", "Peggiore", "Originale"]
        header_colors = {1: theme.POSITIVE_TEXT, 2: theme.MC_MEAN, 6: theme.NEGATIVE_TEXT}
        self.table.clear()
        self.table.setColumnCount(len(headers))
        for c, h in enumerate(headers):
            item = QTableWidgetItem(h)
            if c in header_colors:
                item.setForeground(QBrush(QColor(header_colors[c])))
            self.table.setHorizontalHeaderItem(c, item)
        self.table.setRowCount(len(r.summary))
        bold = QFont(self.table.font())
        bold.setBold(True)
        for row, s in enumerate(r.summary):
            self.table.setItem(row, 0, QTableWidgetItem(s.label))
            values = [s.best, s.mean, s.median, s.conf95, s.conf99, s.worst, s.original]
            for c, v in enumerate(values, start=1):
                kind = "num" if (s.kind == "int" and c == 2) else s.kind
                item = QTableWidgetItem(theme.fmt_value(kind, v))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c in header_colors:
                    item.setForeground(QBrush(QColor(header_colors[c])))
                    if s.key in ("max_dd", "max_consec_loss_amount", "largest_loss"):
                        item.setFont(bold)
                self.table.setItem(row, c, item)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setStretchLastSection(True)
        self.table.resizeRowsToContents()

    def _row(self, key: str):
        if self.result is None:
            return None
        return next((s for s in self.result.summary if s.key == key), None)

    def _update_kpis(self) -> None:
        r = self.result
        if r is None:
            return
        dd = self._row("max_dd")
        dd_pct = self._row("max_dd_pct")

        def pct_sub(attr: str) -> str:
            return f"{theme.fmt_pct(getattr(dd_pct, attr))} dal picco" if dd_pct else ""

        self.kpi_worst.set(theme.fmt_money(-dd.worst), -1, pct_sub("worst"))
        self.kpi_mean.set(theme.fmt_money(-dd.mean), -1, pct_sub("mean"))
        self.kpi_best.set(theme.fmt_money(-dd.best), -1 if dd.best else 0, pct_sub("best"))
        self.kpi_95.set(theme.fmt_money(-dd.conf95), -1, f"99%: {theme.fmt_money(-dd.conf99)}")
        streak = self._row("max_consec_loss_amount")
        losses = self._row("max_consec_losses")
        self.kpi_streak.set(
            theme.fmt_money(streak.worst),
            -1 if streak.worst < 0 else 0,
            f"{int(losses.worst)} perdite di fila (media {losses.mean:.1f})",
        )
        threshold = self.threshold.value()
        prob = r.prob_dd_exceeds(threshold)
        if prob is None:
            self.kpi_prob.set("—", sub="imposta una soglia")
        else:
            self.kpi_prob.set(theme.fmt_pct(prob, 1), -1 if prob > 0 else 0, f"soglia {theme.fmt_money(threshold, 0)}")
        loss_prob = r.prob_loss()
        self.kpi_loss.set(theme.fmt_pct(loss_prob, 1), -1 if loss_prob > 0 else 0, f"{r.n_sims:,} simulazioni")
        self._draw_histogram()

    def _draw_histogram(self) -> None:
        r = self.result
        plot = self.hist_plot.getPlotItem()
        plot.clear()
        self._hist_data = None
        if r is None:
            return
        key = self.hist_metric.currentData()
        kind = next(k for mk, _, k in HIST_METRICS if mk == key)
        values = r.metrics[key]
        values = values[np.isfinite(values)]
        if key in ("max_dd", "final", "max_consec_loss_amount"):
            values = -values if key == "max_dd" else values
        if len(values) == 0:
            return
        if kind == "int":
            lo, hi = int(values.min()), int(values.max())
            edges = np.arange(lo - 0.5, hi + 1.5, 1.0)
        else:
            edges = np.histogram_bin_edges(values, bins=min(60, max(10, int(np.sqrt(len(values))))))
        counts, edges = np.histogram(values, bins=edges)
        bars = pg.BarGraphItem(
            x0=edges[:-1], x1=edges[1:], height=counts, brush=pg.mkBrush(42, 120, 214, 150), pen=pg.mkPen(theme.SURFACE, width=1)
        )
        plot.addItem(bars)
        self._hist_data = (counts, edges, kind)
        axis = plot.getAxis("bottom")
        axis.enableAutoSIPrefix(False)
        if kind == "money":
            axis.tickStrings = lambda vals, scale, spacing: [theme.fmt_money(v, 0) for v in vals]
        elif kind == "pct":
            axis.tickStrings = lambda vals, scale, spacing: [f"{v * 100:.0f}%" for v in vals]
        else:
            axis.tickStrings = lambda vals, scale, spacing: [f"{v:g}" for v in vals]
        axis.picture = None
        axis.update()

        def vline(pos, color, label, style=Qt.DashLine, position=0.9):
            line = pg.InfiniteLine(
                pos=pos,
                angle=90,
                pen=pg.mkPen(color, width=1.8, style=style),
                label=label,
                labelOpts={"position": position, "color": color, "fill": pg.mkBrush(255, 255, 255, 220)},
            )
            plot.addItem(line)

        sign = -1 if key == "max_dd" else 1
        row = self._row(key)
        if row is not None:
            vline(sign * row.mean, theme.MC_MEAN, "media", position=0.92)
            vline(sign * row.conf95, "#b55d00", "95%", position=0.78)
            vline(sign * row.worst, theme.MC_WORST, "peggiore", Qt.SolidLine, position=0.64)
            vline(sign * row.best, theme.MC_BEST, "migliore", Qt.SolidLine, position=0.5)
        if key == "max_dd" and self.threshold.value() > 0:
            vline(-self.threshold.value(), theme.INK, "soglia", Qt.DotLine, position=0.36)
        plot.enableAutoRange()

    # ------------------------------------------------------------------ hover
    def _hover(self, x: float, y: float):
        r = self.result
        if r is None:
            return None
        i = int(round(x))
        if i < 0 or i > r.n_trades:
            return None
        rows = [
            f"{swatch_html(theme.MC_BEST)} Migliore: {span(theme.INK, theme.fmt_money(r.best_curve[i]), True)}",
            f"{swatch_html(theme.MC_MEAN)} Media: {span(theme.INK, theme.fmt_money(r.mean_curve[i]), True)}",
            f"{swatch_html(theme.MC_WORST)} Peggiore: {span(theme.INK, theme.fmt_money(r.worst_curve[i]), True)}",
        ]
        if self.show_original.isChecked() and i < len(r.original_curve):
            rows.append(f"{swatch_html(theme.MC_ORIGINAL)} Originale: {span(theme.INK, theme.fmt_money(r.original_curve[i]), True)}")
        if len(r.plot_curves):
            col = r.plot_curves[:, i]
            rows.append(
                f"{swatch_html(theme.MC_GRAY)} Range: {theme.fmt_money(col.min())} … {theme.fmt_money(col.max())}"
            )
        return f"<b>Trade {i}</b><br>" + "<br>".join(rows), float(i)

    def _hover_hist(self, x: float, y: float):
        if self._hist_data is None:
            return None
        counts, edges, kind = self._hist_data
        i = int(np.searchsorted(edges, x, side="right")) - 1
        if i < 0 or i >= len(counts):
            return None
        total = counts.sum()
        if kind == "int":
            label = f"{edges[i] + 0.5:g}"
        else:
            label = f"{theme.fmt_value(kind, edges[i])} … {theme.fmt_value(kind, edges[i + 1])}"
        return f"{label}<br><b>{int(counts[i])} simulazioni</b> ({counts[i] / total * 100:.1f}%)", None
