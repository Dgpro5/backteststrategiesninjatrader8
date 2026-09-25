"""Strutture dati per i trade (orientate agli array numpy)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Colonne numeriche e testuali di un TradeSet, nell'ordine usato per concatenare.
NUMERIC_FIELDS = (
    "trade_number",
    "qty",
    "entry_price",
    "exit_price",
    "profit",
    "commission",
    "mae",
    "mfe",
    "etd",
    "bars",
)
TEXT_FIELDS = ("strategy", "instrument", "account", "market_pos", "entry_name", "exit_name")
TIME_FIELDS = ("entry_time", "exit_time")


@dataclass
class TradeSet:
    """Lista di trade memorizzata come array paralleli.

    Gli orari sono ``datetime64[s]`` "naive" (orario così come esportato da NinjaTrader).
    ``profit`` è il profitto netto del trade (colonna *Profit* dell'export).
    """

    entry_time: np.ndarray
    exit_time: np.ndarray
    profit: np.ndarray
    trade_number: np.ndarray = None
    qty: np.ndarray = None
    entry_price: np.ndarray = None
    exit_price: np.ndarray = None
    commission: np.ndarray = None
    mae: np.ndarray = None
    mfe: np.ndarray = None
    etd: np.ndarray = None
    bars: np.ndarray = None
    strategy: np.ndarray = None
    instrument: np.ndarray = None
    account: np.ndarray = None
    market_pos: np.ndarray = None
    entry_name: np.ndarray = None
    exit_name: np.ndarray = None

    def __post_init__(self) -> None:
        n = len(self.profit)
        self.entry_time = np.asarray(self.entry_time, dtype="datetime64[s]")
        self.exit_time = np.asarray(self.exit_time, dtype="datetime64[s]")
        self.profit = np.asarray(self.profit, dtype=float)
        for name in NUMERIC_FIELDS:
            if name == "profit":
                continue
            value = getattr(self, name)
            if value is None:
                value = np.arange(1, n + 1) if name == "trade_number" else np.zeros(n)
            setattr(self, name, np.asarray(value, dtype=float))
        for name in TEXT_FIELDS:
            value = getattr(self, name)
            if value is None:
                value = [""] * n
            setattr(self, name, np.asarray(value, dtype=object))
        lengths = {len(getattr(self, f)) for f in NUMERIC_FIELDS + TEXT_FIELDS + TIME_FIELDS}
        if len(lengths) != 1:
            raise ValueError("Tutte le colonne di un TradeSet devono avere la stessa lunghezza")

    def __len__(self) -> int:
        return len(self.profit)

    @property
    def empty(self) -> bool:
        return len(self) == 0

    def take(self, index: np.ndarray) -> "TradeSet":
        """Nuovo TradeSet con le righe indicate (nell'ordine indicato)."""
        kwargs = {f: getattr(self, f)[index] for f in NUMERIC_FIELDS + TEXT_FIELDS + TIME_FIELDS}
        return TradeSet(**kwargs)

    def with_strategy_label(self, label: str) -> "TradeSet":
        ts = self.take(np.arange(len(self)))
        ts.strategy = np.asarray([label] * len(self), dtype=object)
        return ts

    @staticmethod
    def empty_set() -> "TradeSet":
        return TradeSet(entry_time=[], exit_time=[], profit=[])

    @staticmethod
    def combine(sets: list["TradeSet"]) -> "TradeSet":
        """Unisce più TradeSet in ordine cronologico.

        L'ordinamento è per orario di uscita (momento in cui il P&L viene realizzato),
        poi per orario di entrata, poi per ordine di caricamento: così l'equity combinata
        riflette la sequenza reale in cui i profitti/perdite si sarebbero verificati.
        """
        sets = [s for s in sets if s is not None and len(s) > 0]
        if not sets:
            return TradeSet.empty_set()
        kwargs = {
            f: np.concatenate([getattr(s, f) for s in sets])
            for f in NUMERIC_FIELDS + TEXT_FIELDS + TIME_FIELDS
        }
        source_order = np.concatenate([np.full(len(s), i) for i, s in enumerate(sets)])
        within = np.concatenate([np.arange(len(s)) for s in sets])
        merged = TradeSet(**kwargs)
        order = np.lexsort(
            (
                within,
                source_order,
                merged.entry_time.astype(np.int64),
                merged.exit_time.astype(np.int64),
            )
        )
        return merged.take(order)


@dataclass
class LoadedStrategy:
    """Un file CSV caricato nell'app."""

    uid: int
    name: str
    source_path: str
    trades: TradeSet
    color_index: int
    included: bool = True
    csv_strategy: str = ""
    instrument: str = ""
    warnings: list[str] = field(default_factory=list)

    def labeled_trades(self) -> TradeSet:
        """Trade con la colonna ``strategy`` impostata al nome visualizzato."""
        return self.trades.with_strategy_label(self.name)
