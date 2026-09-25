"""Scheda "Statistiche": KPI e tabella completa per strategia e portafoglio."""

from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..metrics import STAT_DEFS
from ..portfolio import Portfolio
from . import theme
from .widgets import KpiTile


KPI_LABELS = [
    "Profitto netto",
    "Max drawdown",
    "Profit factor",
    "% vincenti",
    "Expectancy / trade",
    "Numero trade",
    "Sharpe ratio",
]


def kpi_values(s: dict) -> list[tuple[str, str, int, str]]:
    """(etichetta, valore, tono, sottotitolo) dei riquadri in alto, usati anche nella stampa."""
    dd_sub = theme.fmt_pct(s["max_dd_pct"]) + " dal picco" if s["max_dd_pct"] is not None else ""
    values = [
        (theme.fmt_money(s["net_profit"]), theme.value_tone("money", s["net_profit"]), ""),
        (theme.fmt_money(-s["max_dd"]) if s["max_dd"] else "$0.00", -1 if s["max_dd"] else 0, dd_sub),
        (theme.fmt_ratio(s["profit_factor"]), 0, ""),
        (theme.fmt_pct(s["win_rate"], 1), 0, f"{s['n_wins']} V / {s['n_losses']} P"),
        (theme.fmt_money(s["avg_trade"]), theme.value_tone("money", s["avg_trade"]), ""),
        (f"{s['n_trades']:,}", 0, f"{theme.fmt_ratio(s['trades_per_month'], 1)} al mese"),
        (theme.fmt_ratio(s["sharpe"]), 0, f"Sortino {theme.fmt_ratio(s['sortino'])}"),
    ]
    return [(label, *v) for label, v in zip(KPI_LABELS, values)]


def table_columns(portfolio: Portfolio) -> list[tuple[str, dict, int | None]]:
    """(nome, statistiche, indice colore) per ogni colonna; None = portafoglio combinato."""
    cols = [(e.name, e.stats, e.color_index) for e in portfolio.strategies]
    if portfolio.combined is not None:
        cols.append((portfolio.combined.name, portfolio.combined.stats, None))
    return cols


class StatsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.portfolio: Portfolio | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.title = QLabel("Riepilogo")
        self.title.setObjectName("sectionTitle")
        header.addWidget(self.title)
        header.addStretch(1)
        self.export_btn = QPushButton("Esporta statistiche CSV…")
        self.export_btn.clicked.connect(self.export_csv)
        header.addWidget(self.export_btn)
        layout.addLayout(header)

        kpis = QGridLayout()
        kpis.setSpacing(8)
        self.kpi = [KpiTile(label) for label in KPI_LABELS]
        for i, tile in enumerate(self.kpi):
            kpis.addWidget(tile, 0, i)
        layout.addLayout(kpis)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        layout.addWidget(self.table, 1)

        note = QLabel(
            "Drawdown calcolato sull'equity a trade chiusi, dal massimo precedente (partendo da 0). "
            "Il portafoglio combinato ordina i trade di tutte le strategie per orario di uscita."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)

    def update_portfolio(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        primary = portfolio.primary
        if primary is None:
            return
        if primary.is_combined:
            self.title.setText(f"Riepilogo portafoglio combinato ({len(portfolio.strategies)} strategie)")
        else:
            self.title.setText(f"Riepilogo: {primary.name}")
        for tile, (_label, value, tone, sub) in zip(self.kpi, kpi_values(primary.stats)):
            tile.set(value, tone, sub)
        self._fill_table(portfolio)

    def _columns(self, portfolio: Portfolio):
        return table_columns(portfolio)

    def _fill_table(self, portfolio: Portfolio) -> None:
        cols = self._columns(portfolio)
        self.table.clear()
        self.table.setColumnCount(len(cols) + 1)
        self.table.setRowCount(len(STAT_DEFS))
        header_item = QTableWidgetItem("Statistica")
        self.table.setHorizontalHeaderItem(0, header_item)
        for c, (name, _, color_index) in enumerate(cols, start=1):
            item = QTableWidgetItem(name)
            if color_index is None:
                item.setIcon(theme.swatch_icon(theme.COMBINED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            else:
                item.setIcon(theme.swatch_icon(theme.strategy_color(color_index), theme.strategy_line_style(color_index)))
            self.table.setHorizontalHeaderItem(c, item)

        section_font = QFont(self.table.font())
        section_font.setBold(True)
        section_brush = QBrush(QColor(theme.SECTION_BG))
        for r, definition in enumerate(STAT_DEFS):
            if definition[0] == "section":
                item = QTableWidgetItem(definition[1])
                item.setFont(section_font)
                item.setBackground(section_brush)
                self.table.setItem(r, 0, item)
                for c in range(1, len(cols) + 1):
                    filler = QTableWidgetItem("")
                    filler.setBackground(section_brush)
                    self.table.setItem(r, c, filler)
                continue
            key, label, kind = definition
            self.table.setItem(r, 0, QTableWidgetItem(label))
            for c, (_, stats, color_index) in enumerate(cols, start=1):
                value = stats.get(key)
                item = QTableWidgetItem(theme.fmt_value(kind, value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setForeground(theme.tone_color(theme.value_tone(kind, value)))
                if color_index is None:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        head = self.table.horizontalHeader()
        head.setMinimumSectionSize(130)
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for c in range(1, len(cols) + 1):
            head.setSectionResizeMode(c, QHeaderView.Stretch)
        self.table.resizeRowsToContents()

    def export_csv(self) -> None:
        if self.portfolio is None or self.portfolio.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta statistiche", "statistiche.csv", "CSV (*.csv)")
        if not path:
            return
        cols = self._columns(self.portfolio)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(["Statistica"] + [name for name, _, _ in cols])
                for definition in STAT_DEFS:
                    if definition[0] == "section":
                        writer.writerow([f"[{definition[1]}]"])
                        continue
                    key, label, kind = definition
                    writer.writerow([label] + [theme.fmt_value(kind, stats.get(key)) for _, stats, _ in cols])
        except OSError as exc:
            QMessageBox.warning(self, "Esportazione", f"Impossibile salvare il file:\n{exc}")
