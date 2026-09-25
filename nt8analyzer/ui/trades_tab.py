"""Scheda "Lista trade": tutti i trade in ordine cronologico combinato."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..models import TradeSet
from ..portfolio import Portfolio
from . import theme

COLUMNS = [
    ("#", "idx"),
    ("Strategia", "strategy"),
    ("Strumento", "instrument"),
    ("Direzione", "market_pos"),
    ("Qtà", "qty"),
    ("Entrata", "entry_time"),
    ("Uscita", "exit_time"),
    ("Prezzo entrata", "entry_price"),
    ("Prezzo uscita", "exit_price"),
    ("Profitto", "profit"),
    ("Cumulativo", "cum"),
    ("Drawdown", "dd"),
    ("Nome entrata", "entry_name"),
    ("Nome uscita", "exit_name"),
    ("MAE", "mae"),
    ("MFE", "mfe"),
    ("Barre", "bars"),
]
MONEY = {"profit", "cum", "dd", "mae", "mfe"}
_ROLES = (Qt.DisplayRole, Qt.TextAlignmentRole, Qt.ForegroundRole)


class TradesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.ts = TradeSet.empty_set()
        self.cum = np.array([])
        self.dd = np.array([])

    def set_trades(self, ts: TradeSet) -> None:
        self.beginResetModel()
        self.ts = ts
        self.cum = np.cumsum(ts.profit)
        self.dd = self.cum - np.maximum.accumulate(np.maximum(self.cum, 0.0)) if len(ts) else np.array([])
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ts)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return None

    def _raw(self, row: int, key: str):
        if key == "idx":
            return row + 1
        if key == "cum":
            return float(self.cum[row])
        if key == "dd":
            return float(self.dd[row])
        return getattr(self.ts, key)[row]

    def data(self, index, role=Qt.DisplayRole):
        if role not in _ROLES or not index.isValid():
            return None
        key = COLUMNS[index.column()][1]
        value = self._raw(index.row(), key)
        if role == Qt.DisplayRole:
            if key in MONEY:
                return theme.fmt_money(float(value))
            if key in ("entry_time", "exit_time"):
                return theme.fmt_datetime(value)
            if key in ("entry_price", "exit_price"):
                return f"{float(value):,.2f}"
            if key in ("qty", "bars"):
                return f"{float(value):g}"
            return str(value)
        if role == Qt.TextAlignmentRole and key not in ("strategy", "instrument", "market_pos", "entry_name", "exit_name"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and key in ("profit", "cum", "dd"):
            v = float(value)
            if v > 0:
                return QColor(theme.POSITIVE_TEXT)
            if v < 0:
                return QColor(theme.NEGATIVE_TEXT)
        return None


class TradesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.portfolio: Portfolio | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        bar = QHBoxLayout()
        label = QLabel("Mostra:")
        label.setObjectName("sectionTitle")
        bar.addWidget(label)
        self.selector = QComboBox()
        self.selector.setMinimumWidth(280)
        self.selector.currentIndexChanged.connect(self._selection_changed)
        bar.addWidget(self.selector)
        self.count = QLabel("")
        self.count.setObjectName("hint")
        bar.addWidget(self.count)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.model = TradesModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        # Adatta le colonne guardando solo le prime righe (con migliaia di trade sarebbe lento).
        self.view.horizontalHeader().setResizeContentsPrecision(300)
        layout.addWidget(self.view, 1)
        note = QLabel("Cumulativo e drawdown sono calcolati sulla selezione mostrata, nell'ordine di uscita dei trade.")
        note.setObjectName("hint")
        layout.addWidget(note)

    def update_portfolio(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        current = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        for e in portfolio.entities():
            if e.is_combined:
                icon = theme.swatch_icon(theme.COMBINED)
                label = "Tutte le strategie (ordine combinato)"
            else:
                icon = theme.swatch_icon(theme.strategy_color(e.color_index), theme.strategy_line_style(e.color_index))
                label = e.name
            self.selector.addItem(icon, label, e.key)
        idx = self.selector.findData(current)
        self.selector.setCurrentIndex(idx if idx >= 0 else 0)
        self.selector.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        if self.portfolio is None:
            return
        entity = self.portfolio.entity(self.selector.currentData())
        if entity is None:
            return
        self.model.set_trades(entity.trades)
        self.count.setText(f"{len(entity.trades):,} trade")
        self.view.resizeColumnsToContents()
