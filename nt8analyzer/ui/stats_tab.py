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
        self.kpi = {
            "net_profit": KpiTile("Profitto netto"),
            "max_dd": KpiTile("Max drawdown"),
            "profit_factor": KpiTile("Profit factor"),
            "win_rate": KpiTile("% vincenti"),
            "avg_trade": KpiTile("Expectancy / trade"),
            "n_trades": KpiTile("Numero trade"),
            "sharpe": KpiTile("Sharpe ratio"),
        }
        for i, tile in enumerate(self.kpi.values()):
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
        s = primary.stats
        self.kpi["net_profit"].set(theme.fmt_money(s["net_profit"]), theme.value_tone("money", s["net_profit"]))
        dd_sub = theme.fmt_pct(s["max_dd_pct"]) + " dal picco" if s["max_dd_pct"] is not None else ""
        self.kpi["max_dd"].set(theme.fmt_money(-s["max_dd"]) if s["max_dd"] else "$0.00", -1 if s["max_dd"] else 0, dd_sub)
        self.kpi["profit_factor"].set(theme.fmt_ratio(s["profit_factor"]))
        self.kpi["win_rate"].set(theme.fmt_pct(s["win_rate"], 1), sub=f"{s['n_wins']} V / {s['n_losses']} P")
        self.kpi["avg_trade"].set(theme.fmt_money(s["avg_trade"]), theme.value_tone("money", s["avg_trade"]))
        self.kpi["n_trades"].set(f"{s['n_trades']:,}", sub=f"{theme.fmt_ratio(s['trades_per_month'], 1)} al mese")
        self.kpi["sharpe"].set(theme.fmt_ratio(s["sharpe"]), sub=f"Sortino {theme.fmt_ratio(s['sortino'])}")
        self._fill_table(portfolio)

    def _columns(self, portfolio: Portfolio):
        cols = [(e.name, e.stats, e.color_index) for e in portfolio.strategies]
        if portfolio.combined is not None:
            cols.append((portfolio.combined.name, portfolio.combined.stats, None))
        return cols

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
        section_brush = QBrush(QColor("#ecebe6"))
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
