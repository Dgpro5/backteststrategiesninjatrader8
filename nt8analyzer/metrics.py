"""Statistiche di performance su un TradeSet."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .models import TradeSet

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_MONTH = 365.25 / 12


# --------------------------------------------------------------------------- drawdown


@dataclass
class DrawdownInfo:
    """Drawdown sull'equity a trade chiusi.

    Gli indici si riferiscono ai punti dell'equity: 0 = prima del primo trade,
    ``i`` = dopo la chiusura del trade ``i`` (1-based).
    """

    equity: np.ndarray  # profitto cumulativo, len n+1, parte da 0
    drawdown: np.ndarray  # equity - massimo precedente (<= 0)
    drawdown_pct: np.ndarray  # drawdown / (capitale + massimo) (<= 0), NaN se capitale <= 0
    max_dd: float  # valore positivo in $
    max_dd_pct: float  # frazione positiva, NaN se non calcolabile
    peak_index: int
    trough_index: int
    recovery_index: int | None


def drawdown_info(pnl: np.ndarray, capital: float = 0.0) -> DrawdownInfo:
    pnl = np.asarray(pnl, dtype=float)
    equity = np.concatenate(([0.0], np.cumsum(pnl)))
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    trough = int(np.argmin(dd)) if len(dd) else 0
    max_dd = float(-dd[trough]) if len(dd) else 0.0
    peak_value = peak[trough] if len(dd) else 0.0
    peak_idx = int(np.flatnonzero(equity[: trough + 1] == peak_value)[-1]) if len(dd) else 0
    recovery = None
    if max_dd > 0:
        after = np.flatnonzero(equity[trough:] >= peak_value)
        if len(after):
            recovery = int(trough + after[0])
    if capital > 0:
        base = capital + peak
        dd_pct = dd / base
        max_dd_pct = float(-dd_pct.min()) if len(dd_pct) else 0.0
    else:
        dd_pct = np.full_like(dd, np.nan)
        max_dd_pct = float("nan")
    return DrawdownInfo(equity, dd, dd_pct, max_dd, max_dd_pct, peak_idx, trough, recovery)


def equity_times(ts: TradeSet) -> np.ndarray:
    """Orari dei punti dell'equity: primo ingresso, poi le uscite."""
    if ts.empty:
        return np.array([], dtype="datetime64[s]")
    return np.concatenate((ts.entry_time[:1], ts.exit_time))


def max_streak(mask: np.ndarray) -> int:
    best = run = 0
    for flag in mask:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def worst_losing_run(pnl: np.ndarray) -> float:
    """Somma più negativa di trade perdenti consecutivi (valore <= 0)."""
    worst = run = 0.0
    for p in pnl:
        run = run + p if p < 0 else 0.0
        worst = min(worst, run)
    return worst


def mae_drawdown(ts: TradeSet) -> float | None:
    """Max drawdown includendo l'escursione avversa (MAE) durante ogni trade.

    Valido solo se i trade non si sovrappongono nel tempo (una posizione alla volta);
    altrimenti restituisce ``None``.
    """
    if ts.empty:
        return 0.0
    if np.any(ts.entry_time[1:] < ts.exit_time[:-1]):
        return None
    equity = np.concatenate(([0.0], np.cumsum(ts.profit)))
    peak = np.maximum.accumulate(equity)
    closed_dd = float(np.max(peak - equity))
    intra = peak[:-1] - (equity[:-1] - ts.mae)
    return max(closed_dd, float(np.max(intra)))


# --------------------------------------------------------------------------- aggregazioni


def group_sum(keys: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(keys) == 0:
        return keys[:0], np.array([], dtype=float)
    uniq, inverse = np.unique(keys, return_inverse=True)
    return uniq, np.bincount(inverse, weights=values, minlength=len(uniq))


def daily_pnl(ts: TradeSet) -> tuple[np.ndarray, np.ndarray]:
    return group_sum(ts.exit_time.astype("datetime64[D]"), ts.profit)


def monthly_pnl(ts: TradeSet) -> tuple[np.ndarray, np.ndarray]:
    return group_sum(ts.exit_time.astype("datetime64[M]"), ts.profit)


def business_day_series(ts: TradeSet) -> np.ndarray:
    """P&L giornaliero su tutti i giorni lavorativi del periodo (0 se nessun trade)."""
    days, pnl = daily_pnl(ts)
    if len(days) == 0:
        return np.array([])
    start = ts.entry_time.min().astype("datetime64[D]")
    end = days.max()
    calendar = np.arange(start, end + 1, dtype="datetime64[D]")
    calendar = np.union1d(calendar[np.is_busday(calendar)], days)
    series = np.zeros(len(calendar))
    series[np.searchsorted(calendar, days)] = pnl
    return series


def weekday_index(times: np.ndarray) -> np.ndarray:
    """0 = lunedì ... 6 = domenica."""
    return (times.astype("datetime64[D]").astype(np.int64) + 3) % 7


def hour_index(times: np.ndarray) -> np.ndarray:
    return ((times - times.astype("datetime64[D]")).astype("timedelta64[h]").astype(np.int64)) % 24


def daily_correlation(sets: list[TradeSet]) -> np.ndarray:
    """Matrice di correlazione del P&L giornaliero tra strategie."""
    k = len(sets)
    if k == 0:
        return np.zeros((0, 0))
    per = [daily_pnl(s) for s in sets]
    all_days = np.unique(np.concatenate([d for d, _ in per])) if per else np.array([])
    if len(all_days) < 2:
        return np.eye(k)
    matrix = np.zeros((k, len(all_days)))
    for i, (days, pnl) in enumerate(per):
        matrix[i, np.searchsorted(all_days, days)] = pnl
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(matrix)
    corr = np.atleast_2d(corr)
    corr[np.isnan(corr)] = 0.0
    np.fill_diagonal(corr, 1.0)
    return corr


# --------------------------------------------------------------------------- statistiche

# (chiave, etichetta, tipo). Una tupla ("section", titolo) apre una sezione.
STAT_DEFS: list[tuple[str, ...]] = [
    ("section", "Generale"),
    ("period_start", "Primo trade", "datetime"),
    ("period_end", "Ultimo trade", "datetime"),
    ("n_trades", "Numero trade", "int"),
    ("n_wins", "Trade vincenti", "int"),
    ("n_losses", "Trade perdenti", "int"),
    ("n_even", "Trade in pareggio", "int"),
    ("win_rate", "Percentuale vincenti", "pct"),
    ("long_short", "Trade long / short", "text"),
    ("trades_per_month", "Trade medi al mese", "num"),
    ("section", "Profitto"),
    ("net_profit", "Profitto netto totale", "money"),
    ("gross_profit", "Profitto lordo", "money"),
    ("gross_loss", "Perdita lorda", "money"),
    ("commission", "Commissioni totali", "money"),
    ("profit_factor", "Profit factor", "ratio"),
    ("avg_trade", "Expectancy (profitto medio per trade)", "money"),
    ("avg_win", "Vincita media", "money"),
    ("avg_loss", "Perdita media", "money"),
    ("payoff", "Rapporto vincita media / perdita media", "ratio"),
    ("expectancy_r", "Expectancy in R (trade medio / perdita media)", "ratio"),
    ("largest_win", "Miglior trade", "money"),
    ("largest_loss", "Peggior trade (max loss)", "money"),
    ("std_trade", "Deviazione standard per trade", "money"),
    ("section", "Drawdown e rischio"),
    ("max_dd", "Max drawdown ($)", "money"),
    ("max_dd_pct", "Max drawdown (% sul picco di equity)", "pct"),
    ("max_dd_start", "Max drawdown: inizio (picco)", "datetime"),
    ("max_dd_trough", "Max drawdown: minimo", "datetime"),
    ("max_dd_recovery", "Max drawdown: recuperato il", "datetime"),
    ("max_dd_days", "Durata max drawdown (giorni)", "num1"),
    ("longest_flat_days", "Periodo più lungo senza nuovi massimi (giorni)", "num1"),
    ("mae_dd", "Max drawdown intra-trade (stima con MAE)", "money"),
    ("recovery_factor", "Recovery factor (profitto / max DD)", "ratio"),
    ("annual_return_dd", "Profitto annuo / max DD", "ratio"),
    ("max_consec_loss_amount", "Max perdita consecutiva ($)", "money"),
    ("worst_day", "Peggior giorno", "money_date"),
    ("best_day", "Miglior giorno", "money_date"),
    ("worst_month", "Peggior mese", "money_month"),
    ("best_month", "Miglior mese", "money_month"),
    ("pct_months_positive", "Mesi positivi", "pct"),
    ("section", "Serie consecutive"),
    ("max_consec_wins", "Max vincite consecutive", "int"),
    ("max_consec_losses", "Max perdite consecutive", "int"),
    ("section", "Rendimento"),
    ("return_pct", "Rendimento sul capitale iniziale", "pct"),
    ("cagr", "Rendimento annuo composto (CAGR)", "pct"),
    ("avg_month", "Profitto medio mensile", "money"),
    ("avg_year", "Profitto medio annuo", "money"),
    ("sharpe", "Sharpe ratio (annualizzato, su P&L giornaliero)", "ratio"),
    ("sortino", "Sortino ratio (annualizzato)", "ratio"),
    ("sqn", "SQN (System Quality Number)", "ratio"),
    ("kelly", "Kelly %", "pct"),
    ("section", "Caratteristiche dei trade"),
    ("avg_mae", "MAE medio", "money"),
    ("avg_mfe", "MFE medio", "money"),
    ("avg_etd", "ETD medio", "money"),
    ("avg_bars", "Barre medie per trade", "num"),
    ("avg_duration", "Durata media trade", "duration"),
]


def _safe_div(a: float, b: float) -> float | None:
    if b == 0:
        if a == 0:
            return None
        return math.inf if a > 0 else -math.inf
    return a / b


def compute_stats(ts: TradeSet, capital: float = 0.0) -> dict:
    """Calcola tutte le statistiche definite in ``STAT_DEFS``."""
    stats: dict = {key[0]: None for key in STAT_DEFS if key[0] != "section"}
    n = len(ts)
    stats["n_trades"] = n
    if n == 0:
        return stats
    pnl = ts.profit
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_profit, gross_loss = float(wins.sum()), float(losses.sum())
    net = float(pnl.sum())
    start, end = ts.entry_time.min(), ts.exit_time.max()
    span_days = max((end - start).astype("timedelta64[s]").astype(np.int64) / 86400.0, 1.0)
    years = span_days / 365.25
    months = max(span_days / DAYS_PER_MONTH, 1.0)

    long_mask = np.array([str(p).lower().startswith("l") for p in ts.market_pos])
    short_mask = np.array([str(p).lower().startswith("s") for p in ts.market_pos])

    stats.update(
        period_start=start,
        period_end=end,
        n_wins=int(len(wins)),
        n_losses=int(len(losses)),
        n_even=int(n - len(wins) - len(losses)),
        win_rate=len(wins) / n,
        long_short=f"{int(long_mask.sum())} / {int(short_mask.sum())}",
        trades_per_month=n / months,
        net_profit=net,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        commission=float(ts.commission.sum()),
        profit_factor=_safe_div(gross_profit, -gross_loss),
        avg_trade=net / n,
        avg_win=float(wins.mean()) if len(wins) else None,
        avg_loss=float(losses.mean()) if len(losses) else None,
        largest_win=float(pnl.max()),
        largest_loss=float(pnl.min()),
        std_trade=float(pnl.std(ddof=1)) if n > 1 else None,
        max_consec_wins=max_streak(pnl > 0),
        max_consec_losses=max_streak(pnl < 0),
        max_consec_loss_amount=worst_losing_run(pnl),
        avg_mae=float(ts.mae.mean()),
        avg_mfe=float(ts.mfe.mean()),
        avg_etd=float(ts.etd.mean()),
        avg_bars=float(ts.bars.mean()),
        avg_duration=float((ts.exit_time - ts.entry_time).astype(np.int64).mean()),
        avg_month=net / months,
        avg_year=net / years if years > 0 else None,
    )
    if stats["avg_win"] is not None and stats["avg_loss"]:
        stats["payoff"] = stats["avg_win"] / -stats["avg_loss"]
        payoff = stats["payoff"]
        stats["kelly"] = stats["win_rate"] - (1 - stats["win_rate"]) / payoff if payoff > 0 else None
    if stats["avg_loss"]:
        stats["expectancy_r"] = stats["avg_trade"] / -stats["avg_loss"]
    if stats["std_trade"]:
        stats["sqn"] = math.sqrt(n) * stats["avg_trade"] / stats["std_trade"]

    # Drawdown
    dd = drawdown_info(pnl, capital)
    times = equity_times(ts)
    stats["max_dd"] = dd.max_dd
    stats["max_dd_pct"] = dd.max_dd_pct if capital > 0 else None
    if dd.max_dd > 0:
        stats["max_dd_start"] = times[dd.peak_index]
        stats["max_dd_trough"] = times[dd.trough_index]
        stats["max_dd_recovery"] = times[dd.recovery_index] if dd.recovery_index is not None else "Non recuperato"
        end_idx = dd.recovery_index if dd.recovery_index is not None else len(times) - 1
        stats["max_dd_days"] = (times[end_idx] - times[dd.peak_index]).astype("timedelta64[s]").astype(np.int64) / 86400.0
    else:
        stats["max_dd_days"] = 0.0
    new_high = np.flatnonzero(dd.equity >= np.maximum.accumulate(dd.equity))
    high_times = np.concatenate((times[new_high], times[-1:]))
    gaps = np.diff(high_times).astype("timedelta64[s]").astype(np.int64) / 86400.0
    stats["longest_flat_days"] = float(gaps.max()) if len(gaps) else 0.0
    stats["mae_dd"] = mae_drawdown(ts)
    stats["recovery_factor"] = _safe_div(net, dd.max_dd)
    if stats["avg_year"] is not None:
        stats["annual_return_dd"] = _safe_div(stats["avg_year"], dd.max_dd)

    # Giorni e mesi
    days, day_pnl = daily_pnl(ts)
    i_worst, i_best = int(np.argmin(day_pnl)), int(np.argmax(day_pnl))
    stats["worst_day"] = (float(day_pnl[i_worst]), days[i_worst])
    stats["best_day"] = (float(day_pnl[i_best]), days[i_best])
    months_keys, month_pnl = monthly_pnl(ts)
    j_worst, j_best = int(np.argmin(month_pnl)), int(np.argmax(month_pnl))
    stats["worst_month"] = (float(month_pnl[j_worst]), months_keys[j_worst])
    stats["best_month"] = (float(month_pnl[j_best]), months_keys[j_best])
    stats["pct_months_positive"] = float((month_pnl > 0).mean())

    # Rendimento
    if capital > 0:
        stats["return_pct"] = net / capital
        final = capital + net
        if final > 0 and years > 0:
            stats["cagr"] = (final / capital) ** (1.0 / years) - 1.0
    series = business_day_series(ts)
    if len(series) > 1 and series.std(ddof=1) > 0:
        mean = series.mean()
        stats["sharpe"] = mean / series.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)
        downside = math.sqrt(np.mean(np.minimum(series, 0.0) ** 2))
        stats["sortino"] = mean / downside * math.sqrt(TRADING_DAYS_PER_YEAR) if downside > 0 else None
    return stats
