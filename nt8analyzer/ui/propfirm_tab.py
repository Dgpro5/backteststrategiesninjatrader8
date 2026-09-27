"""Scheda "Prop Firm": probabilità di passare la valutazione e di arrivare al primo payout."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt, QTimer
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

from ..portfolio import Portfolio
from ..propfirm import (
    DAILY_ACTIONS,
    DAILY_FAIL,
    DD_STATIC,
    DD_TRAILING_EOD,
    DD_DESCRIPTIONS,
    DD_TYPES,
    FAIL_DAILY,
    FAIL_DD,
    INCOMPLETE,
    PASSED,
    PropFirmConfig,
    PropFirmResult,
    describe,
    run_prop_firm,
)
from . import theme
from .widgets import Card, HoverTip, KpiTile, add_legend, make_plot, span, swatch_html

AUTO_RUN_LIMIT = 12_000_000  # simulazioni x trade oltre cui non si ricalcola in automatico

KPI_LABELS = [
    "Tasso di passaggio",
    "Giornate per passare",
    "Primo payout (dall'inizio)",
    "Giornate al primo payout",
    "Payout medio",
    "Bocciati · DD massimo",
    "Bocciati · DD giornaliero",
]

OUTCOME_HEADERS = ["Esito", "Valutazione", "Conto finanziato (dopo il passaggio)"]
TIMING_HEADERS = ["Metrica", "Media", "Mediana", "10° perc.", "90° perc.", "Minimo", "Massimo"]


# --------------------------------------------------------------------------- valori condivisi con la stampa


def _days(value: float) -> str:
    return "—" if not np.isfinite(value) else f"{value:,.1f} gg"


def _count_pct(mask_value: int, outcomes: np.ndarray, base: np.ndarray | None = None) -> str:
    sel = outcomes if base is None else outcomes[base]
    if len(sel) == 0:
        return "—"
    n = int(np.count_nonzero(sel == mask_value))
    return f"{n / len(sel) * 100:.1f}%  ({n:,})"


def kpi_values(r: PropFirmResult) -> list[tuple[str, str, int, str]]:
    """(etichetta, valore, tono, sottotitolo) dei riquadri in alto, usati anche nella stampa."""
    cfg = r.config
    passed = r.passed
    eval_days = describe(r.eval_days[passed])
    funded = describe(r.funded_days[r.paid])
    total = describe(r.total_days)
    amount = describe(r.payout)
    cal = r.calendar_per_day
    fail_dd = r.rate(r.eval_outcome, FAIL_DD)
    fail_daily = r.rate(r.eval_outcome, FAIL_DAILY)
    values = [
        (f"{r.pass_rate * 100:.1f}%", 1 if r.pass_rate >= 0.5 else (-1 if r.pass_rate < 0.2 else 0),
         f"{int(passed.sum()):,} di {r.n_sims:,} simulazioni"),
        (_days(eval_days["mean"]), 0,
         "—" if not np.isfinite(eval_days["mean"]) else
         f"mediana {eval_days['median']:.0f} · ≈ {eval_days['mean'] * cal:.0f} gg cal."),
        (f"{r.payout_rate * 100:.1f}%", 1 if r.payout_rate >= 0.5 else (-1 if r.payout_rate < 0.2 else 0),
         "—" if not passed.any() else f"{r.payout_rate_funded * 100:.1f}% dei finanziati"),
        (_days(total["mean"]), 0,
         "—" if not np.isfinite(total["mean"]) else
         f"{funded['mean']:.1f} dopo il passaggio"),
        (theme.fmt_money(amount["mean"]) if np.isfinite(amount["mean"]) else "—",
         1 if np.isfinite(amount["mean"]) else 0,
         f"mediana {theme.fmt_money(amount['median'])}" if np.isfinite(amount["median"]) else "nessun payout"),
        (f"{fail_dd * 100:.1f}%", -1 if fail_dd > 0 else 0, f"limite {theme.fmt_money(cfg.max_drawdown, 0)}"),
        ("—", 0, "nessun limite giornaliero") if cfg.daily_loss <= 0 else
        (f"{fail_daily * 100:.1f}%", -1 if fail_daily > 0 else 0,
         f"limite {theme.fmt_money(cfg.daily_loss, 0)}" + ("" if cfg.daily_action == DAILY_FAIL else " (stop)")),
    ]
    return [(label, *v) for label, v in zip(KPI_LABELS, values)]


def outcome_rows(r: PropFirmResult) -> list[tuple[str, str, str]]:
    cfg = r.config
    passed = r.passed
    rows = [
        ("Passata / primo payout", _count_pct(PASSED, r.eval_outcome), _count_pct(PASSED, r.funded_outcome, passed)),
        ("Bocciato: drawdown massimo", _count_pct(FAIL_DD, r.eval_outcome), _count_pct(FAIL_DD, r.funded_outcome, passed)),
        ("Bocciato: limite giornaliero",
         "—" if cfg.daily_loss <= 0 or cfg.daily_action != DAILY_FAIL else _count_pct(FAIL_DAILY, r.eval_outcome),
         "—" if cfg.daily_loss <= 0 or cfg.daily_action != DAILY_FAIL else _count_pct(FAIL_DAILY, r.funded_outcome, passed)),
        (f"Non concluso entro {r.max_days:,} giornate", _count_pct(INCOMPLETE, r.eval_outcome),
         _count_pct(INCOMPLETE, r.funded_outcome, passed)),
    ]
    return rows


def timing_rows(r: PropFirmResult) -> list[tuple[str, str, list[float]]]:
    """(metrica, tipo, [media, mediana, p10, p90, minimo, massimo])."""
    keys = ("mean", "median", "p10", "p90", "min", "max")

    def row(label, kind, values):
        d = describe(values)
        return (label, kind, [d[k] for k in keys])

    return [
        row("Giornate per passare la valutazione", "days", r.eval_days[r.passed]),
        row("Giornate al payout, dopo il passaggio", "days", r.funded_days[r.paid]),
        row("Giornate totali al primo payout", "days", r.total_days),
        row("Giorni di calendario al primo payout (stima)", "days", r.total_days * r.calendar_per_day),
        row("Importo del primo payout", "money", r.payout[r.paid]),
    ]


def fmt_timing(kind: str, value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return theme.fmt_money(value) if kind == "money" else f"{value:,.1f}"


def rules_text(cfg: PropFirmConfig) -> list[tuple[str, str]]:
    """Riepilogo delle regole (etichetta, valore) per la stampa."""
    return [
        ("Account", theme.fmt_money(cfg.account_size, 0)),
        ("Profit target", theme.fmt_money(cfg.profit_target, 0)),
        ("Drawdown massimo", f"{theme.fmt_money(cfg.max_drawdown, 0)} · {DD_TYPES[cfg.drawdown_type]}"
         + (", fermo al saldo iniziale" if cfg.trailing_lock and cfg.drawdown_type != DD_STATIC else "")),
        ("Drawdown giornaliero", "nessun limite" if cfg.daily_loss <= 0 else
         f"{theme.fmt_money(cfg.daily_loss, 0)} · {DAILY_ACTIONS[cfg.daily_action].lower()}"),
        ("Giornate minime", str(cfg.min_days)),
        ("Payout", f"{cfg.payout_days} giornate ≥ {theme.fmt_money(cfg.payout_min_profit, 0)}"),
        ("Simulazioni", f"{cfg.n_sims:,}"),
    ]


def method_note(r: PropFirmResult, short: bool = False) -> str:
    cfg = r.config
    if short:
        return (
            f"Ogni simulazione rimescola a caso le {r.n_days:,} giornate di trading del backtest, ognuna con i suoi "
            "trade. Il conto finanziato riparte dal saldo iniziale con le stesse regole; il primo payout arriva dopo "
            f"{cfg.payout_days} giornate con almeno {theme.fmt_money(cfg.payout_min_profit, 0)} di profitto (le giornate "
            "con meno profitto non contano come valide, ma il loro profitto resta nel payout). Tempi in giornate di "
            f"trading; ≈ {r.calendar_per_day:.1f} giorni di calendario per giornata al ritmo del backtest."
        )
    parts = [
        f"Ogni simulazione rimescola a caso le {r.n_days:,} giornate di trading del backtest (ogni giornata tiene i "
        "suoi trade, nell'ordine in cui sono stati chiusi) e, finite le giornate, continua con un nuovo ordine casuale.",
        "Valutazione passata quando a fine giornata il profitto raggiunge il target"
        + (f" dopo almeno {cfg.min_days} giornate di trading" if cfg.min_days > 1 else "") + ".",
        {DD_TRAILING_EOD: "Drawdown trailing aggiornato con il saldo di fine giornata",
         DD_STATIC: "Drawdown statico misurato dal saldo iniziale"}.get(
            cfg.drawdown_type, "Drawdown trailing aggiornato dopo ogni trade chiuso")
        + (", fermo al saldo iniziale una volta raggiunto" if cfg.trailing_lock and cfg.drawdown_type != DD_STATIC else "")
        + ("; perdite durante il trade stimate con il MAE" if cfg.use_mae else "; controllato sui trade chiusi") + ".",
        "Primo payout sul conto finanziato (che riparte dal saldo iniziale con le stesse regole) dopo "
        f"{cfg.payout_days} giornate con almeno {theme.fmt_money(cfg.payout_min_profit, 0)} di profitto: le giornate "
        "con meno profitto non contano come giornate valide ma il loro profitto resta nel payout.",
        f"Le giornate sono giornate con almeno un trade; i giorni di calendario sono stimati con il ritmo del "
        f"backtest (≈ {r.calendar_per_day:.1f} giorni di calendario per giornata di trading).",
    ]
    if cfg.multiplier != 1:
        parts.append(f"P&L dei trade moltiplicato per {cfg.multiplier:g}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- scheda


class PropFirmTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.portfolio: Portfolio | None = None
        self.result: PropFirmResult | None = None
        self._entity_name = ""
        self.settings = QSettings()
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(450)
        self._auto_timer.timeout.connect(self._auto_run)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        def money_box(key: str, default: float, maximum: float = 1_000_000, step: float = 250) -> QDoubleSpinBox:
            box = QDoubleSpinBox()
            box.setRange(0, maximum)
            box.setDecimals(0)
            box.setSingleStep(step)
            box.setPrefix("$ ")
            box.setGroupSeparatorShown(True)
            box.setValue(float(self.settings.value(key, default)))
            box.setMinimumWidth(100)
            return box

        def int_box(key: str, default: int, minimum: int, maximum: int) -> QSpinBox:
            box = QSpinBox()
            box.setRange(minimum, maximum)
            box.setValue(int(self.settings.value(key, default)))
            box.setGroupSeparatorShown(True)
            return box

        # --- account
        self.series_box = QComboBox()
        self.series_box.setMinimumWidth(150)
        self.series_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.series_box.setMinimumContentsLength(14)
        self.account_size = money_box("prop/size", 50_000, maximum=10_000_000, step=5_000)
        self.target = money_box("prop/target", 3_000)
        self.min_days = int_box("prop/min_days", 1, 1, 365)
        self.min_days.setToolTip("Giornate di trading minime prima di poter passare la valutazione")
        # --- drawdown
        self.max_dd = money_box("prop/dd", 2_500)
        self.dd_type = QComboBox()
        for key, label in DD_TYPES.items():
            self.dd_type.addItem(label, key)
            self.dd_type.setItemData(self.dd_type.count() - 1, DD_DESCRIPTIONS[key].capitalize(), Qt.ToolTipRole)
        self.dd_type.setToolTip("Trailing EOD: il limite sale con il saldo di fine giornata. Trailing intraday: sale "
                                "dopo ogni trade chiuso. Statico: resta fisso sotto il saldo iniziale.")
        self.dd_type.setCurrentIndex(max(0, self.dd_type.findData(self.settings.value("prop/dd_type", DD_TRAILING_EOD))))
        self.trailing_lock = QCheckBox("Trailing fermo al saldo iniziale")
        self.trailing_lock.setChecked(str(self.settings.value("prop/lock", "true")).lower() == "true")
        self.trailing_lock.setToolTip("Quando il limite trailing raggiunge il saldo iniziale non sale più (regola comune)")
        self.daily = money_box("prop/daily", 0)
        self.daily.setSpecialValueText("nessun limite")
        self.daily.setToolTip("Perdita massima in una giornata. 0 = nessun limite giornaliero")
        self.daily_action = QComboBox()
        for key, label in DAILY_ACTIONS.items():
            self.daily_action.addItem(label, key)
        self.daily_action.setCurrentIndex(max(0, self.daily_action.findData(self.settings.value("prop/daily_action", DAILY_FAIL))))
        # --- payout
        self.payout_days = int_box("prop/payout_days", 5, 1, 365)
        self.payout_days.setToolTip("Giornate valide (con almeno il profitto minimo) necessarie per il primo payout")
        self.payout_min = money_box("prop/payout_min", 100, step=25)
        self.payout_min.setToolTip("Profitto minimo perché una giornata sia valida per il payout. "
                                   "Le giornate con meno profitto non contano, ma il loro profitto resta nel payout.")
        # --- simulazione
        self.sims = int_box("prop/sims", 2000, 100, 100_000)
        self.sims.setSingleStep(500)
        self.multiplier = QDoubleSpinBox()
        self.multiplier.setRange(0.01, 100)
        self.multiplier.setDecimals(2)
        self.multiplier.setSingleStep(0.1)
        self.multiplier.setPrefix("× ")
        self.multiplier.setValue(float(self.settings.value("prop/mult", 1.0)))
        self.multiplier.setToolTip("Moltiplicatore del P&L dei trade: es. 0,1 da mini a micro, 2 per il doppio dei contratti")
        self.use_mae = QCheckBox("Usa il MAE dei trade")
        self.use_mae.setChecked(str(self.settings.value("prop/mae", "false")).lower() == "true")
        self.use_mae.setToolTip("Controlla i limiti anche sulla perdita massima toccata durante il trade, non solo alla chiusura")
        self.seed = QSpinBox()
        self.seed.setRange(0, 999_999)
        self.seed.setSpecialValueText("casuale")
        self.seed.setToolTip("Seed del generatore casuale: 0 = diverso ad ogni esecuzione, altro = risultati ripetibili")
        self.run_btn = QPushButton("Esegui simulazione")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run)
        def lab(text: str, tip: str = "") -> QLabel:
            label = QLabel(text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setToolTip(tip)
            return label

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        grid = [
            [("Serie:", self.series_box), ("Account:", self.account_size), ("Profit target:", self.target),
             ("Giornate minime:", self.min_days)],
            [("DD massimo:", self.max_dd), ("Tipo drawdown:", self.dd_type), ("DD giornaliero:", self.daily),
             ("Se superato:", self.daily_action)],
            [("Giornate payout:", self.payout_days), ("Minimo al giorno:", self.payout_min),
             ("Moltiplicatore:", self.multiplier), ("Simulazioni:", self.sims)],
        ]
        for r, row in enumerate(grid):
            for c, (text, widget) in enumerate(row):
                controls.addWidget(lab(text, widget.toolTip()), r, 2 * c)
                controls.addWidget(widget, r, 2 * c + 1)
        controls.addWidget(lab("Seed:", self.seed.toolTip()), 3, 0)
        controls.addWidget(self.seed, 3, 1)
        controls.addWidget(self.trailing_lock, 3, 2, 1, 2, Qt.AlignRight)
        controls.addWidget(self.use_mae, 3, 4, 1, 2, Qt.AlignRight)
        controls.addWidget(self.run_btn, 3, 6, 1, 2)
        controls.setColumnStretch(8, 1)
        layout.addLayout(controls)

        for widget in (self.account_size, self.target, self.min_days, self.max_dd, self.daily, self.payout_days,
                       self.payout_min, self.sims, self.multiplier, self.seed):
            widget.valueChanged.connect(self._params_changed)
        for combo in (self.dd_type, self.daily_action):
            combo.currentIndexChanged.connect(self._params_changed)
        for check in (self.trailing_lock, self.use_mae):
            check.toggled.connect(self._params_changed)
        self.series_box.currentIndexChanged.connect(self._params_changed)
        self.dd_type.currentIndexChanged.connect(self._dd_type_changed)
        self.daily.valueChanged.connect(self._dd_type_changed)
        self._dd_type_changed()

        # --- KPI
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)
        self.kpis = [KpiTile(label) for label in KPI_LABELS]
        for tile in self.kpis:
            kpi_row.addWidget(tile)
        layout.addLayout(kpi_row)

        # --- grafici
        self.plot = make_plot("Simulazioni della valutazione", x_label="Giornata di trading", y_label="Saldo del conto",
                              min_height=300)
        self.legend = add_legend(self.plot)
        HoverTip(self.plot, self._hover_paths)
        self.pass_plot = make_plot("Giornate per passare", money_y=False, y_label="Simulazioni",
                                   x_label="Giornate di trading", min_height=150)
        self.payout_plot = make_plot("Giornate al primo payout", money_y=False,
                                     y_label="Simulazioni", x_label="Giornate di trading dopo il passaggio", min_height=150)
        HoverTip(self.pass_plot, lambda x, y: self._hover_hist("pass", x))
        HoverTip(self.payout_plot, lambda x, y: self._hover_hist("payout", x))
        self._hists: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        hists = QWidget()
        hist_layout = QVBoxLayout(hists)
        hist_layout.setContentsMargins(0, 0, 0, 0)
        hist_layout.setSpacing(8)
        hist_layout.addWidget(Card(self.pass_plot), 1)
        hist_layout.addWidget(Card(self.payout_plot), 1)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(Card(self.plot))
        top.addWidget(hists)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        top.setSizes([900, 560])

        # --- tabelle
        self.outcomes = self._table(OUTCOME_HEADERS)
        self.timing = self._table(TIMING_HEADERS)
        tables = QSplitter(Qt.Horizontal)
        tables.addWidget(Card(self.outcomes, "Esiti delle simulazioni"))
        tables.addWidget(Card(self.timing, "Tempi (giornate di trading) e importo del primo payout"))
        tables.setStretchFactor(0, 2)
        tables.setStretchFactor(1, 3)
        tables.setSizes([600, 860])
        self.outcomes.setMinimumHeight(150)
        self.timing.setMinimumHeight(170)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(tables)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([480, 230])
        layout.addWidget(splitter, 1)

        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        return table

    # ------------------------------------------------------------------ parametri
    def _dd_type_changed(self) -> None:
        self.trailing_lock.setEnabled(self.dd_type.currentData() != DD_STATIC)
        self.daily_action.setEnabled(self.daily.value() > 0)

    def config(self) -> PropFirmConfig:
        return PropFirmConfig(
            account_size=self.account_size.value(),
            profit_target=self.target.value(),
            max_drawdown=self.max_dd.value(),
            drawdown_type=self.dd_type.currentData(),
            trailing_lock=self.trailing_lock.isChecked(),
            daily_loss=self.daily.value(),
            daily_action=self.daily_action.currentData(),
            min_days=self.min_days.value(),
            payout_days=self.payout_days.value(),
            payout_min_profit=self.payout_min.value(),
            multiplier=self.multiplier.value(),
            use_mae=self.use_mae.isChecked(),
            n_sims=self.sims.value(),
            seed=self.seed.value() or None,
        )

    def _save_settings(self, cfg: PropFirmConfig) -> None:
        values = {
            "size": cfg.account_size, "target": cfg.profit_target, "dd": cfg.max_drawdown, "dd_type": cfg.drawdown_type,
            "lock": str(cfg.trailing_lock).lower(), "daily": cfg.daily_loss, "daily_action": cfg.daily_action,
            "min_days": cfg.min_days, "payout_days": cfg.payout_days, "payout_min": cfg.payout_min_profit,
            "mult": cfg.multiplier, "mae": str(cfg.use_mae).lower(), "sims": cfg.n_sims,
        }
        for key, value in values.items():
            self.settings.setValue(f"prop/{key}", value)

    def _params_changed(self, *_args) -> None:
        if self.portfolio is None or self.portfolio.empty:
            return
        entity = self.portfolio.entity(self.series_box.currentData())
        if entity is not None and self.sims.value() * len(entity.trades) <= AUTO_RUN_LIMIT:
            self._auto_timer.start()
        else:
            self.note.setText("Parametri cambiati: premi «Esegui simulazione» per ricalcolare.")

    def _auto_run(self) -> None:
        self.run()

    # ------------------------------------------------------------------ dati
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
        for plot in (self.plot, self.pass_plot, self.payout_plot):
            plot.getPlotItem().clear()
        self.legend.clear()
        for table in (self.outcomes, self.timing):
            table.setRowCount(0)
        for tile in self.kpis:
            tile.set("—")
        self.note.setText(message)

    def run(self) -> None:
        self._auto_timer.stop()
        if self.portfolio is None or self.portfolio.empty:
            return
        entity = self.portfolio.entity(self.series_box.currentData())
        if entity is None or entity.trades.empty:
            return
        cfg = self.config()
        self._save_settings(cfg)
        dialog = None
        if cfg.n_sims * len(entity.trades) > 4_000_000:
            dialog = QProgressDialog("Simulazione prop firm in corso…", "Annulla", 0, cfg.n_sims, self)
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
            self.result = run_prop_firm(entity.trades, cfg, progress)
        finally:
            QApplication.restoreOverrideCursor()
            if dialog is not None:
                dialog.close()
        self._entity_name = entity.name
        self._draw()

    # ------------------------------------------------------------------ disegno
    def redraw(self) -> None:
        """Ridisegna l'ultima simulazione (es. dopo un cambio di tema) senza ricalcolarla."""
        if self.result is not None:
            self._draw()

    def _draw(self) -> None:
        r = self.result
        if r is None:
            return
        cfg = r.config
        size = cfg.account_size
        item = self.plot.getPlotItem()
        item.clear()
        self.legend.clear()
        paths = r.paths + size
        groups = (
            (PASSED, theme.MC_BEST, "Passate"),
            (FAIL_DD, theme.MC_WORST, "Bocciate"),
            (INCOMPLETE, theme.MC_GRAY, "Non concluse"),
        )
        alpha = 90 if len(paths) <= 100 else 60
        for outcome, color, label in groups:
            rows = np.flatnonzero((r.path_outcome == outcome) | ((outcome == FAIL_DD) & (r.path_outcome == FAIL_DAILY)))
            if len(rows) == 0:
                continue
            xs, ys, connect = [], [], []
            for i in rows:
                valid = np.flatnonzero(np.isfinite(paths[i]))
                xs.append(valid.astype(float))
                ys.append(paths[i][valid])
                c = np.ones(len(valid), bool)
                c[-1] = False
                connect.append(c)
            curve = pg.PlotCurveItem(np.concatenate(xs), np.concatenate(ys), connect=np.concatenate(connect),
                                     pen=pg.mkPen(theme.rgba(color, alpha), width=1.2), antialias=True)
            item.addItem(curve)
            sample = pg.PlotDataItem([], [], pen=pg.mkPen(color, width=2.2))
            self.legend.addItem(sample, f"{label} ({len(rows)})")

        def hline(y, color, text, style=Qt.DashLine, position=0.97):
            line = pg.InfiniteLine(
                pos=y, angle=0, movable=False, pen=pg.mkPen(color, width=1.6, style=style), label=text,
                labelOpts={"position": position, "color": color, "fill": theme.label_brush(220),
                           "anchors": [(1, 1), (1, 1)]},
            )
            item.addItem(line)

        hline(size, theme.MUTED, f"Saldo iniziale {theme.fmt_money(size, 0)}", Qt.DotLine, 0.5)
        hline(size + cfg.profit_target, theme.MC_BEST, f"Target {theme.fmt_money(size + cfg.profit_target, 0)}")
        dd_label = "Drawdown massimo" if cfg.drawdown_type == DD_STATIC else "Limite trailing iniziale"
        hline(size - cfg.max_drawdown, theme.MC_WORST, f"{dd_label} {theme.fmt_money(size - cfg.max_drawdown, 0)}")
        item.enableAutoRange()
        shown = len(paths)
        item.setTitle(f"Valutazione · {self._entity_name} · {shown:,} di {r.n_sims:,} simulazioni",
                      color=theme.INK, size="10.5pt", bold=True)

        self._draw_hist("pass", self.pass_plot, r.eval_days[r.passed])
        self._draw_hist("payout", self.payout_plot, r.funded_days[r.paid])
        self._fill_tables()
        for tile, (_label, value, tone, sub) in zip(self.kpis, kpi_values(r)):
            tile.set(value, tone, sub)
        self.note.setText(method_note(r))

    def _draw_hist(self, key: str, plot: pg.PlotWidget, values: np.ndarray) -> None:
        item = plot.getPlotItem()
        item.clear()
        self._hists.pop(key, None)
        if len(values) == 0:
            return
        lo, hi = int(values.min()), int(values.max())
        width = max(1, int(np.ceil((hi - lo + 1) / 40)))
        edges = np.arange(lo - 0.5, hi + width + 0.5, width, dtype=float)
        counts, edges = np.histogram(values, bins=edges)
        item.addItem(pg.BarGraphItem(x0=edges[:-1], x1=edges[1:], height=counts, brush=pg.mkBrush(*theme.HIST_BAR),
                                     pen=pg.mkPen(theme.SURFACE, width=1)))
        mean = float(values.mean())
        item.addItem(pg.InfiniteLine(
            pos=mean, angle=90, pen=pg.mkPen(theme.MC_MEAN, width=1.6, style=Qt.DashLine),
            label=f"media {mean:.1f}", labelOpts={"position": 0.9, "color": theme.MC_MEAN, "fill": theme.label_brush(220)},
        ))
        self._hists[key] = (counts, edges)
        item.enableAutoRange()

    def _fill_tables(self) -> None:
        r = self.result
        bold = QFont(self.outcomes.font())
        bold.setBold(True)
        rows = outcome_rows(r)
        self.outcomes.setRowCount(len(rows))
        colors = [theme.POSITIVE_TEXT, theme.NEGATIVE_TEXT, theme.NEGATIVE_TEXT, theme.INK]
        for i, (label, ev, fu) in enumerate(rows):
            self.outcomes.setItem(i, 0, QTableWidgetItem(label))
            for c, text in enumerate((ev, fu), start=1):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setForeground(QBrush(QColor(colors[i])))
                if i == 0:
                    cell.setFont(bold)
                self.outcomes.setItem(i, c, cell)
        rows = timing_rows(r)
        self.timing.setRowCount(len(rows))
        for i, (label, kind, values) in enumerate(rows):
            self.timing.setItem(i, 0, QTableWidgetItem(label))
            for c, v in enumerate(values, start=1):
                cell = QTableWidgetItem(fmt_timing(kind, v))
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if kind == "money" and np.isfinite(v):
                    cell.setForeground(theme.tone_color(theme.value_tone("money", v)))
                self.timing.setItem(i, c, cell)
        for table in (self.outcomes, self.timing):
            head = table.horizontalHeader()
            head.setSectionResizeMode(QHeaderView.ResizeToContents)
            head.setStretchLastSection(True)
            table.resizeRowsToContents()

    # ------------------------------------------------------------------ hover
    def _hover_paths(self, x: float, y: float):
        r = self.result
        if r is None:
            return None
        day = int(round(x))
        if day < 0 or day > r.max_days:
            return None
        n = r.n_sims
        passed = np.count_nonzero(r.passed & (r.eval_days <= day)) / n
        failed = np.count_nonzero((r.eval_outcome >= FAIL_DD) & (r.eval_outcome <= FAIL_DAILY) & (r.eval_days <= day)) / n
        rows = [
            f"{swatch_html(theme.MC_BEST)} Passate entro questa giornata: {span(theme.INK, f'{passed * 100:.1f}%', True)}",
            f"{swatch_html(theme.MC_WORST)} Bocciate: {span(theme.INK, f'{failed * 100:.1f}%', True)}",
            f"{swatch_html(theme.MC_GRAY)} Ancora in valutazione: {span(theme.INK, f'{(1 - passed - failed) * 100:.1f}%', True)}",
        ]
        return f"<b>Giornata {day}</b><br>" + "<br>".join(rows), float(day)

    def _hover_hist(self, key: str, x: float):
        data = self._hists.get(key)
        if data is None:
            return None
        counts, edges = data
        i = int(np.searchsorted(edges, x, side="right")) - 1
        if i < 0 or i >= len(counts):
            return None
        lo, hi = edges[i] + 0.5, edges[i + 1] - 0.5
        label = f"{lo:.0f}" if hi - lo < 1 else f"{lo:.0f}–{hi:.0f}"
        total = counts.sum()
        return f"{label} giornate<br><b>{int(counts[i])} simulazioni</b> ({counts[i] / total * 100:.1f}%)", None
