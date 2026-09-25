"""Lettura degli export CSV della griglia "Trades" di NinjaTrader 8."""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .models import TradeSet


class ParseError(Exception):
    """Il file non è un export trade di NinjaTrader leggibile."""


# Nome interno -> intestazioni accettate (confronto case-insensitive, senza punteggiatura).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_number": ("trade number", "trade #", "trade", "numero trade", "n trade"),
    "instrument": ("instrument", "strumento"),
    "account": ("account", "conto"),
    "strategy": ("strategy", "strategia"),
    "market_pos": ("market pos", "market position", "posizione", "direzione"),
    "qty": ("qty", "quantity", "quantità", "quantita"),
    "entry_price": ("entry price", "prezzo entrata", "prezzo di entrata"),
    "exit_price": ("exit price", "prezzo uscita", "prezzo di uscita"),
    "entry_time": ("entry time", "orario entrata", "ora entrata", "data entrata"),
    "exit_time": ("exit time", "orario uscita", "ora uscita", "data uscita"),
    "entry_name": ("entry name", "nome entrata"),
    "exit_name": ("exit name", "nome uscita"),
    "profit": ("profit", "profitto", "net profit", "p&l", "pnl"),
    "cum_profit": ("cum net profit", "cumulative net profit", "profitto netto cumulativo"),
    "commission": ("commission", "commissione", "commissioni"),
    "mae": ("mae",),
    "mfe": ("mfe",),
    "etd": ("etd",),
    "bars": ("bars", "barre"),
}

# Ordine standard delle colonne dell'export NinjaTrader 8 (usato se le intestazioni
# non sono riconosciute, ad es. interfaccia in un'altra lingua).
STANDARD_ORDER = (
    "trade_number",
    "instrument",
    "account",
    "strategy",
    "market_pos",
    "qty",
    "entry_price",
    "exit_price",
    "entry_time",
    "exit_time",
    "entry_name",
    "exit_name",
    "profit",
    "cum_profit",
    "commission",
    None,  # Clearing Fee
    None,  # Exchange Fee
    None,  # IP Fee
    None,  # NFA Fee
    "mae",
    "mfe",
    "etd",
    "bars",
)

REQUIRED = ("entry_time", "exit_time", "profit")

DATE_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M:%S %p",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


@dataclass
class ParsedFile:
    trades: TradeSet
    strategy_name: str
    instrument: str
    path: str
    date_format: str
    warnings: list[str] = field(default_factory=list)


def _normalize_header(text: str) -> str:
    text = text.strip().lower().replace("﻿", "")
    text = re.sub(r"[.\-_:]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_number(text: str, decimal_hint: str = ".") -> float:
    """Converte importi/prezzi NinjaTrader in float.

    Gestisce ``$1,234.50``, ``($2.00)`` (negativo), ``-2.00 $``, ``€ 1.234,56``, ``1'234.5``.
    """
    if text is None:
        return 0.0
    s = str(text).strip()
    if not s:
        return 0.0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if "-" in s or "−" in s:
        negative = True
    s = re.sub(r"[^0-9.,]", "", s)
    if not s or not any(ch.isdigit() for ch in s):
        return 0.0
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        digits_after = len(s) - s.rfind(sep) - 1
        if s.count(sep) > 1:
            decimal = None  # separatore delle migliaia ripetuto
        elif sep == decimal_hint:
            decimal = sep
        else:
            decimal = None if digits_after == 3 else sep
    else:
        decimal = None
    if decimal is None:
        s = s.replace(",", "").replace(".", "")
    else:
        thousands = "." if decimal == "," else ","
        s = s.replace(thousands, "").replace(decimal, ".")
    value = float(s)
    return -value if negative else value


def _parse_dates(values: list[str], fmt: str) -> list[datetime] | None:
    out = []
    for v in values:
        try:
            out.append(datetime.strptime(v.strip(), fmt))
        except ValueError:
            return None
    return out


def detect_date_format(entry_values: list[str], exit_values: list[str]) -> str:
    """Sceglie il formato data che interpreta tutte le righe.

    Se più formati sono validi (es. giorni <= 12 con ``/``), preferisce quello con
    meno violazioni dell'ordine cronologico degli ingressi.
    """
    candidates = []
    for fmt in DATE_FORMATS:
        entries = _parse_dates(entry_values, fmt)
        if entries is None:
            continue
        exits = _parse_dates(exit_values, fmt)
        if exits is None:
            continue
        violations = sum(1 for a, b in zip(entries, entries[1:]) if b < a)
        violations += sum(1 for a, b in zip(entries, exits) if b < a)
        candidates.append((violations, len(candidates), fmt))
    if not candidates:
        sample = entry_values[0] if entry_values else ""
        raise ParseError(f"Formato data/ora non riconosciuto (esempio: '{sample}')")
    candidates.sort()
    return candidates[0][2]


def _sniff_delimiter(sample: str) -> str:
    first_line = sample.splitlines()[0] if sample else ""
    counts = {d: first_line.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _map_columns(header: list[str]) -> tuple[dict[str, int], bool]:
    normalized = [_normalize_header(h) for h in header]
    mapping: dict[str, int] = {}
    for key, aliases in COLUMN_ALIASES.items():
        for idx, name in enumerate(normalized):
            if name in aliases and idx not in mapping.values():
                mapping[key] = idx
                break
    if all(k in mapping for k in REQUIRED):
        return mapping, True
    positional = {key: idx for idx, key in enumerate(STANDARD_ORDER) if key and idx < len(header)}
    return positional, False


def parse_text(text: str, path: str = "") -> ParsedFile:
    if not text.strip():
        raise ParseError("Il file è vuoto")
    delimiter = _sniff_delimiter(text)
    decimal_hint = "," if delimiter == ";" else "."
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ParseError("Il file non contiene trade")
    header, body = rows[0], rows[1:]
    mapping, by_name = _map_columns(header)
    warnings: list[str] = []
    if not by_name:
        if len(header) < 13:
            raise ParseError(
                "Intestazioni non riconosciute: servono almeno le colonne "
                "'Entry time', 'Exit time' e 'Profit'"
            )
        warnings.append("Intestazioni non riconosciute: colonne lette nell'ordine standard NinjaTrader")

    def column(key: str) -> list[str]:
        idx = mapping.get(key)
        if idx is None:
            return [""] * len(body)
        return [r[idx] if idx < len(r) else "" for r in body]

    # Scarta righe senza orari (es. righe di riepilogo).
    entry_raw, exit_raw = column("entry_time"), column("exit_time")
    keep = [i for i, (a, b) in enumerate(zip(entry_raw, exit_raw)) if a.strip() and b.strip()]
    if len(keep) != len(body):
        warnings.append(f"{len(body) - len(keep)} righe senza orario ignorate")
    body = [body[i] for i in keep]
    if not body:
        raise ParseError("Nessun trade valido nel file")
    entry_raw, exit_raw = column("entry_time"), column("exit_time")

    fmt = detect_date_format(entry_raw, exit_raw)
    entry_times = _parse_dates(entry_raw, fmt)
    exit_times = _parse_dates(exit_raw, fmt)

    def numbers(key: str) -> np.ndarray:
        return np.array([parse_number(v, decimal_hint) for v in column(key)], dtype=float)

    profit = numbers("profit")
    trade_number = numbers("trade_number") if "trade_number" in mapping else np.arange(1, len(body) + 1)

    trades = TradeSet(
        entry_time=np.array(entry_times, dtype="datetime64[s]"),
        exit_time=np.array(exit_times, dtype="datetime64[s]"),
        profit=profit,
        trade_number=trade_number,
        qty=numbers("qty"),
        entry_price=numbers("entry_price"),
        exit_price=numbers("exit_price"),
        commission=numbers("commission"),
        mae=np.abs(numbers("mae")),
        mfe=np.abs(numbers("mfe")),
        etd=np.abs(numbers("etd")),
        bars=numbers("bars"),
        strategy=[v.strip() for v in column("strategy")],
        instrument=[v.strip() for v in column("instrument")],
        account=[v.strip() for v in column("account")],
        market_pos=[v.strip() for v in column("market_pos")],
        entry_name=[v.strip() for v in column("entry_name")],
        exit_name=[v.strip() for v in column("exit_name")],
    )

    # Controllo di coerenza con la colonna "Cum. net profit", se presente.
    if "cum_profit" in mapping:
        cum = numbers("cum_profit")
        if len(cum) and abs(cum[-1] - profit.sum()) > 0.01 + 1e-6 * abs(cum[-1]):
            warnings.append(
                "La somma dei profitti non coincide con 'Cum. net profit' "
                f"({profit.sum():.2f} vs {cum[-1]:.2f})"
            )

    # I trade devono essere in ordine cronologico per ricostruire l'equity.
    order = np.lexsort((trades.trade_number, trades.entry_time.astype(np.int64), trades.exit_time.astype(np.int64)))
    if not np.array_equal(order, np.arange(len(order))):
        trades = trades.take(order)

    strategies = [s for s in trades.strategy if s]
    instruments = [s for s in trades.instrument if s]
    stem = os.path.splitext(os.path.basename(path))[0] if path else "Strategia"
    strategy_name = max(set(strategies), key=strategies.count) if strategies else stem
    instrument = max(set(instruments), key=instruments.count) if instruments else ""
    return ParsedFile(trades, strategy_name, instrument, path, fmt, warnings)


def parse_file(path: str) -> ParsedFile:
    try:
        text = _read_text(path)
    except OSError as exc:
        raise ParseError(f"Impossibile leggere il file: {exc}") from exc
    return parse_text(text, path)
