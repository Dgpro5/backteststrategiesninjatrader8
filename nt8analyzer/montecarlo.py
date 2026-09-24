"""Simulazione Monte Carlo sulla sequenza dei trade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

SHUFFLE = "shuffle"
BOOTSTRAP = "bootstrap"

RANK_MAX_DD = "max_dd"
RANK_FINAL = "final"
RANK_RATIO = "ratio"

# (chiave, etichetta, tipo, "più alto è meglio")
MC_METRICS: list[tuple[str, str, str, bool]] = [
    ("final", "Profitto netto finale", "money", True),
    ("max_dd", "Max drawdown ($)", "money", False),
    ("max_dd_pct", "Max drawdown (%)", "pct", False),
    ("max_consec_loss_amount", "Max perdita consecutiva ($)", "money", True),
    ("max_consec_losses", "Max perdite consecutive (n. trade)", "int", False),
    ("largest_loss", "Max perdita singola (peggior trade)", "money", True),
    ("dd_length", "Durata max drawdown (n. trade)", "int", False),
    ("recovery_factor", "Recovery factor (profitto / max DD)", "ratio", True),
    ("avg_trade", "Expectancy (profitto medio per trade)", "money", True),
    ("profit_factor", "Profit factor", "ratio", True),
    ("win_rate", "Percentuale vincenti", "pct", True),
]


@dataclass
class MCConfig:
    n_sims: int = 1000
    method: str = SHUFFLE
    n_trades: int | None = None  # solo bootstrap; None = come l'originale
    capital: float = 0.0
    seed: int | None = None
    rank_by: str = RANK_MAX_DD
    dd_threshold: float = 0.0
    max_plot: int = 1000


@dataclass
class MCSummaryRow:
    key: str
    label: str
    kind: str
    best: float
    mean: float
    median: float
    conf95: float
    conf99: float
    worst: float
    original: float


@dataclass
class MCResult:
    config: MCConfig
    n_trades: int
    metrics: dict[str, np.ndarray]
    plot_curves: np.ndarray  # (k, n+1) profitto cumulativo delle simulazioni disegnate
    best_curve: np.ndarray
    worst_curve: np.ndarray
    mean_curve: np.ndarray
    original_curve: np.ndarray
    best_index: int
    worst_index: int
    original_metrics: dict[str, float]
    summary: list[MCSummaryRow] = field(default_factory=list)

    @property
    def n_sims(self) -> int:
        return len(self.metrics["final"])

    def prob_dd_exceeds(self, threshold: float) -> float | None:
        if threshold <= 0:
            return None
        return float(np.mean(self.metrics["max_dd"] >= threshold))

    def prob_loss(self) -> float:
        return float(np.mean(self.metrics["final"] < 0))


def _path_metrics(pnl: np.ndarray, capital: float) -> dict[str, np.ndarray]:
    """Metriche per ogni riga di una matrice (simulazioni x trade)."""
    m, n = pnl.shape
    equity = np.zeros((m, n + 1))
    np.cumsum(pnl, axis=1, out=equity[:, 1:])
    peak = np.maximum.accumulate(equity, axis=1)
    dd = peak - equity
    max_dd = dd.max(axis=1)
    if capital > 0:
        max_dd_pct = (dd / (capital + peak)).max(axis=1)
    else:
        max_dd_pct = np.full(m, np.nan)

    # Serie consecutive e durata del drawdown: un passo vettoriale per trade.
    loss_run = np.zeros(m)
    loss_amt = np.zeros(m)
    under_run = np.zeros(m)
    best_loss_run = np.zeros(m)
    worst_loss_amt = np.zeros(m)
    longest_under = np.zeros(m)
    for j in range(n):
        col = pnl[:, j]
        is_loss = col < 0
        loss_run = np.where(is_loss, loss_run + 1, 0)
        loss_amt = np.where(is_loss, loss_amt + col, 0.0)
        np.maximum(best_loss_run, loss_run, out=best_loss_run)
        np.minimum(worst_loss_amt, loss_amt, out=worst_loss_amt)
        under = dd[:, j + 1] > 1e-9
        under_run = np.where(under, under_run + 1, 0)
        np.maximum(longest_under, under_run, out=longest_under)

    final = equity[:, -1]
    gross_profit = np.where(pnl > 0, pnl, 0.0).sum(axis=1)
    gross_loss = -np.where(pnl < 0, pnl, 0.0).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        profit_factor = np.where(gross_loss > 0, gross_profit / gross_loss, np.inf)
        recovery = np.where(max_dd > 0, final / max_dd, np.inf)
    return {
        "equity": equity,
        "final": final,
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "max_consec_loss_amount": worst_loss_amt,
        "max_consec_losses": best_loss_run,
        "largest_loss": pnl.min(axis=1) if n else np.zeros(m),
        "dd_length": longest_under,
        "recovery_factor": recovery,
        "avg_trade": final / max(n, 1),
        "profit_factor": profit_factor,
        "win_rate": (pnl > 0).mean(axis=1) if n else np.zeros(m),
    }


def _rank_score(metrics: dict[str, np.ndarray], rank_by: str) -> np.ndarray:
    """Punteggio "più alto = migliore" usato per scegliere la curva verde e quella rossa."""
    if rank_by == RANK_FINAL:
        # A parità di profitto (sempre, nello shuffle) decide il drawdown minore.
        scale = max(float(np.abs(metrics["max_dd"]).max()), 1.0)
        return metrics["final"] - 1e-6 * metrics["max_dd"] / scale
    if rank_by == RANK_RATIO:
        ratio = metrics["recovery_factor"].copy()
        finite = np.isfinite(ratio)
        if finite.any():
            ratio[~finite] = ratio[finite].max() + 1.0
        else:
            ratio[:] = 0.0
        return ratio
    return -metrics["max_dd"]


def run_monte_carlo(
    pnl: np.ndarray,
    config: MCConfig,
    progress: Callable[[int, int], bool] | None = None,
) -> MCResult:
    """Esegue la simulazione.

    ``shuffle`` rimescola l'ordine dei trade (stessi trade, profitto finale identico:
    cambia il percorso e quindi il drawdown). ``bootstrap`` estrae i trade con
    reinserimento (varia anche il profitto finale).

    ``progress(fatte, totale)`` può restituire ``False`` per interrompere.
    """
    pnl = np.asarray(pnl, dtype=float)
    if len(pnl) == 0:
        raise ValueError("Nessun trade da simulare")
    n_sims = max(1, int(config.n_sims))
    n = len(pnl) if config.method == SHUFFLE or not config.n_trades else int(config.n_trades)
    rng = np.random.default_rng(config.seed)
    chunk = int(max(1, min(n_sims, 2_000_000 // max(n, 1))))
    max_plot = min(n_sims, max(0, int(config.max_plot)))

    collected: dict[str, list[np.ndarray]] = {key: [] for key, *_ in MC_METRICS}
    plot_rows: list[np.ndarray] = []
    mean_sum = np.zeros(n + 1)
    best = (-np.inf, None, -1)
    worst = (np.inf, None, -1)
    done = 0
    while done < n_sims:
        m = min(chunk, n_sims - done)
        if config.method == SHUFFLE:
            block = np.tile(pnl, (m, 1))
            rng.permuted(block, axis=1, out=block)
        else:
            block = rng.choice(pnl, size=(m, n), replace=True)
        metrics = _path_metrics(block, config.capital)
        equity = metrics.pop("equity")
        for key in collected:
            collected[key].append(metrics[key])
        mean_sum += equity.sum(axis=0)
        need = max_plot - sum(len(r) for r in plot_rows)
        if need > 0:
            plot_rows.append(equity[:need].copy())
        score = _rank_score(metrics, config.rank_by)
        i_best, i_worst = int(np.argmax(score)), int(np.argmin(score))
        if score[i_best] > best[0]:
            best = (float(score[i_best]), equity[i_best].copy(), done + i_best)
        if score[i_worst] < worst[0]:
            worst = (float(score[i_worst]), equity[i_worst].copy(), done + i_worst)
        done += m
        if progress is not None and progress(done, n_sims) is False:
            break

    all_metrics = {key: np.concatenate(parts) for key, parts in collected.items()}
    original = _path_metrics(pnl[None, :], config.capital)
    original_curve = original.pop("equity")[0]
    original_metrics = {k: float(v[0]) for k, v in original.items()}
    result = MCResult(
        config=config,
        n_trades=n,
        metrics=all_metrics,
        plot_curves=np.vstack(plot_rows) if plot_rows else np.zeros((0, n + 1)),
        best_curve=best[1],
        worst_curve=worst[1],
        mean_curve=mean_sum / done,
        original_curve=original_curve,
        best_index=best[2],
        worst_index=worst[2],
        original_metrics=original_metrics,
    )
    result.summary = summarize(result)
    return result


def summarize(result: MCResult) -> list[MCSummaryRow]:
    rows = []
    for key, label, kind, higher_better in MC_METRICS:
        values = result.metrics[key]
        finite = values[np.isfinite(values)]
        if len(finite) == 0:
            if len(values) and np.all(np.isposinf(values)):
                inf = float("inf")
                rows.append(MCSummaryRow(key, label, kind, inf, inf, inf, inf, inf, inf, result.original_metrics[key]))
            continue
        # Per i valori infiniti (es. profit factor senza perdite) si usano i quantili su tutti i valori.
        data = np.where(np.isposinf(values), np.finfo(float).max, values)
        data = data[~np.isnan(data)]
        if higher_better:
            best, worst = data.max(), data.min()
            conf95, conf99 = np.percentile(data, 5), np.percentile(data, 1)
        else:
            best, worst = data.min(), data.max()
            conf95, conf99 = np.percentile(data, 95), np.percentile(data, 99)

        def clean(v: float) -> float:
            return float("inf") if v >= np.finfo(float).max else float(v)

        rows.append(
            MCSummaryRow(
                key=key,
                label=label,
                kind=kind,
                best=clean(best),
                mean=float(finite.mean()),
                median=clean(float(np.median(data))),
                conf95=clean(conf95),
                conf99=clean(conf99),
                worst=clean(worst),
                original=result.original_metrics[key],
            )
        )
    return rows
