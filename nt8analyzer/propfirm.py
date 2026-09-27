"""Simulazione di un account prop firm: valutazione e primo payout, mescolando le giornate di trading.

Ogni simulazione prende le giornate del backtest (con i loro trade, nell'ordine in cui sono stati
chiusi) e le mette in un ordine casuale. Quando le giornate finiscono se ne usa una nuova permutazione.
Poi applica le regole dell'account giorno per giorno:

* **valutazione**: passata quando, a fine giornata, il profitto raggiunge il target e si sono fatte
  almeno ``min_days`` giornate di trading; bocciata se si tocca il drawdown massimo o il limite di
  perdita giornaliero (0 = nessun limite);
* **conto finanziato** (dopo il passaggio, con saldo e regole di nuovo all'inizio): il primo payout
  arriva quando si hanno ``payout_days`` giornate con profitto di almeno ``payout_min_profit`` e il conto
  è in utile. Le giornate sotto la soglia non contano come giornate valide, ma il loro profitto resta nel
  saldo e quindi nel payout.

Tutti gli importi sono relativi al saldo iniziale (profitto dell'account).
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from .metrics import drawdown_info
from .models import TradeSet

DD_TRAILING_EOD = "trailing_eod"
DD_TRAILING_INTRADAY = "trailing_intraday"
DD_STATIC = "static"
DD_TYPES = {
    DD_TRAILING_EOD: "Trailing EOD",
    DD_TRAILING_INTRADAY: "Trailing intraday",
    DD_STATIC: "Statico",
}
DD_DESCRIPTIONS = {
    DD_TRAILING_EOD: "trailing, aggiornato con il saldo di fine giornata",
    DD_TRAILING_INTRADAY: "trailing, aggiornato dopo ogni trade chiuso",
    DD_STATIC: "statico, dal saldo iniziale",
}

DAILY_FAIL = "fail"
DAILY_STOP = "stop"
DAILY_ACTIONS = {
    DAILY_FAIL: "Account bocciato",
    DAILY_STOP: "Stop per la giornata",
}

# Esiti (valutazione e conto finanziato)
INCOMPLETE = 0
PASSED = 1  # valutazione passata / payout raggiunto
FAIL_DD = 2
FAIL_DAILY = 3
NOT_STARTED = 4  # conto finanziato mai iniziato (valutazione non passata)


@dataclass
class PropFirmConfig:
    account_size: float = 50_000.0
    profit_target: float = 3_000.0
    max_drawdown: float = 2_500.0
    drawdown_type: str = DD_TRAILING_EOD
    trailing_lock: bool = True  # il drawdown trailing smette di salire al saldo iniziale
    daily_loss: float = 0.0  # 0 = nessun limite giornaliero
    daily_action: str = DAILY_FAIL
    min_days: int = 1  # giornate minime di trading per passare la valutazione
    payout_days: int = 5  # giornate con profitto minimo necessarie per il payout
    payout_min_profit: float = 100.0
    multiplier: float = 1.0  # scala il P&L dei trade (es. 0.1 da mini a micro, 2 per due contratti)
    use_mae: bool = False  # considera la perdita massima durante il trade (MAE)
    n_sims: int = 2000
    seed: int | None = None
    max_days: int | None = None  # giornate massime simulate per account (default: automatico)
    n_paths: int = 250  # simulazioni di cui si conserva il percorso per il grafico
    eval_only: bool = False  # solo la valutazione (più veloce: usato per confrontare molte combinazioni)


@dataclass
class PropFirmResult:
    config: PropFirmConfig
    n_days: int  # giornate di trading nel backtest
    calendar_per_day: float  # giorni di calendario per ogni giornata di trading (per le conversioni)
    max_days: int
    eval_outcome: np.ndarray
    eval_days: np.ndarray
    funded_outcome: np.ndarray
    funded_days: np.ndarray
    payout: np.ndarray  # importo del primo payout (nan se non raggiunto)
    paths: np.ndarray  # (n_paths, giorni + 1): profitto a fine giornata durante la valutazione (nan dopo)
    path_outcome: np.ndarray
    day_pnl: np.ndarray = field(default_factory=lambda: np.array([]))
    eval_max_dd: np.ndarray = field(default_factory=lambda: np.array([]))  # drawdown massimo durante la valutazione

    @property
    def n_sims(self) -> int:
        return len(self.eval_outcome)

    def rate(self, outcomes: np.ndarray, value: int, base: np.ndarray | None = None) -> float:
        mask = np.ones(len(outcomes), bool) if base is None else base
        n = int(mask.sum())
        return float(np.count_nonzero(outcomes[mask] == value)) / n if n else float("nan")

    @property
    def passed(self) -> np.ndarray:
        return self.eval_outcome == PASSED

    @property
    def paid(self) -> np.ndarray:
        return self.funded_outcome == PASSED

    @property
    def pass_rate(self) -> float:
        return self.rate(self.eval_outcome, PASSED)

    @property
    def payout_rate(self) -> float:
        """Probabilità di arrivare al primo payout partendo dalla valutazione."""
        return self.rate(self.funded_outcome, PASSED)

    @property
    def payout_rate_funded(self) -> float:
        """Probabilità di payout per chi ha passato la valutazione."""
        return self.rate(self.funded_outcome, PASSED, self.passed)

    @property
    def expected_days_to_pass(self) -> float:
        """Giornate di trading medie per ottenere il conto finanziato, comprando un nuovo account a ogni
        bocciatura: giornate delle valutazioni passate + quelle perse nei tentativi falliti."""
        p = self.pass_rate
        if not p > 0:
            return float("inf")
        passed = self.eval_days[self.passed].mean()
        failed = self.eval_days[~self.passed].mean() if (~self.passed).any() else 0.0
        return float(passed + failed * (1 - p) / p)

    @property
    def expected_days_to_payout(self) -> float:
        """Come ``expected_days_to_pass`` ma fino al primo payout (valutazione + conto finanziato)."""
        q = self.payout_rate
        if not q > 0:
            return float("inf")
        spent = self.eval_days + np.where(self.funded_outcome == NOT_STARTED, 0, self.funded_days)
        ok = self.paid
        failed = spent[~ok].mean() if (~ok).any() else 0.0
        return float(spent[ok].mean() + failed * (1 - q) / q)

    @property
    def total_days(self) -> np.ndarray:
        """Giornate dall'inizio della valutazione al primo payout (solo simulazioni con payout)."""
        return self.eval_days[self.paid] + self.funded_days[self.paid]


def _day_matrix(trades: TradeSet, multiplier: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Trade raggruppati per giornata (data di uscita), in ordine di uscita: P&L, MAE, maschera, date."""
    order = np.lexsort((trades.entry_time, trades.exit_time))
    exit_day = trades.exit_time[order].astype("datetime64[D]")
    pnl = trades.profit[order] * multiplier
    mae_raw = trades.mae[order] if trades.mae is not None else np.zeros(len(order))
    mae = np.abs(np.nan_to_num(mae_raw.astype(float))) * abs(multiplier)
    days, start, counts = np.unique(exit_day, return_index=True, return_counts=True)
    width = int(counts.max()) if len(counts) else 0
    n = len(days)
    P = np.zeros((n, max(width, 1)))
    A = np.zeros((n, max(width, 1)))
    M = np.zeros((n, max(width, 1)), dtype=bool)
    pos = np.arange(len(order)) - np.repeat(start, counts)
    row = np.repeat(np.arange(n), counts)
    P[row, pos] = pnl
    A[row, pos] = mae
    M[row, pos] = True
    return P, A, M, days


def run_prop_firm(trades: TradeSet, cfg: PropFirmConfig, progress=None, orders: np.ndarray | None = None) -> PropFirmResult:
    """Esegue ``cfg.n_sims`` simulazioni. ``progress(fatti, totale) -> bool`` (False = annulla).

    ``orders`` (simulazioni x giornate, indici delle giornate) impone l'ordine dei giorni: serve ai test."""
    if len(trades) == 0:
        raise ValueError("Nessun trade da simulare")
    P, A, M, days = _day_matrix(trades, cfg.multiplier)
    n_days, width = P.shape
    span = (days[-1] - days[0]).astype("timedelta64[D]").astype(int) + 1 if n_days else 1
    calendar_per_day = span / max(n_days, 1)
    max_days = cfg.max_days or max(2 * n_days, 120)
    S = int(cfg.n_sims)
    if orders is not None:
        orders = np.asarray(orders, dtype=np.int32)
        S, max_days = orders.shape
    rng = np.random.default_rng(cfg.seed)

    D = float(cfg.max_drawdown)
    L = float(cfg.daily_loss)
    target = float(cfg.profit_target)
    intraday = cfg.drawdown_type == DD_TRAILING_INTRADAY
    stop_day = cfg.daily_action == DAILY_STOP

    phase = np.zeros(S, np.int8)  # 0 valutazione, 1 conto finanziato, 2 finito
    bal = np.zeros(S)  # profitto nel conto attuale
    peak = np.zeros(S)
    thr = np.full(S, -D)  # livello di profitto che fa scattare il drawdown massimo
    days_in_phase = np.zeros(S, np.int32)
    qualifying = np.zeros(S, np.int32)
    eval_outcome = np.zeros(S, np.int8)
    eval_days = np.zeros(S, np.int32)
    funded_outcome = np.full(S, NOT_STARTED, np.int8)
    funded_days = np.zeros(S, np.int32)
    payout = np.full(S, np.nan)
    closed_peak = np.zeros(S)  # massimo del saldo a trade chiusi durante la valutazione
    eval_dd = np.zeros(S)
    eval_max_dd = np.zeros(S)
    n_paths = min(cfg.n_paths, S)
    paths = np.full((n_paths, max_days + 1), np.nan)
    paths[:, 0] = 0.0

    def threshold(pk: np.ndarray) -> np.ndarray:
        level = pk - D
        return np.minimum(level, 0.0) if cfg.trailing_lock else level

    perm = None
    for t in range(max_days):
        active = np.flatnonzero(phase < 2)
        if len(active) == 0:
            break
        if orders is not None:
            d = orders[active, t]
        else:
            if t % n_days == 0:  # nuova permutazione delle giornate per tutte le simulazioni
                perm = rng.permuted(np.tile(np.arange(n_days, dtype=np.int32), (S, 1)), axis=1)
            d = perm[active, t % n_days]
        b = bal[active]
        pk = peak[active]
        th = thr[active]
        cpk = closed_peak[active]
        mdd = eval_dd[active]
        run = np.zeros(len(active))  # P&L della giornata
        alive = np.ones(len(active), bool)  # non bocciati
        trading = np.ones(len(active), bool)  # non fermati dal limite giornaliero
        failed_as = np.zeros(len(active), np.int8)
        for k in range(width):
            live = M[d, k] & alive & trading
            if not live.any():
                if not M[d, k].any():
                    break
                continue
            p = np.where(live, P[d, k], 0.0)
            new_b = b + p
            new_run = run + p
            # punto più basso toccato dal trade (a trade chiuso, o durante il trade se si usa il MAE)
            low_b, low_run = new_b, new_run
            if cfg.use_mae:
                mae = np.where(live, A[d, k], 0.0)
                low_b = np.minimum(b - mae, new_b)
                low_run = np.minimum(run - mae, new_run)
            dd_hit = live & (low_b <= th)
            mdd = np.where(live, np.maximum(mdd, cpk - low_b), mdd)
            if L > 0:
                daily_level = b - run - L  # saldo al quale la perdita del giorno arriva al limite
                over = live & (low_run <= -L)
                # se si superano entrambi i limiti, scatta prima quello più alto (toccato per primo)
                daily_first = over & (~dd_hit | (daily_level >= th))
                if stop_day:
                    # chiusura al limite giornaliero e stop fino al giorno dopo
                    new_b = np.where(daily_first, daily_level, new_b)
                    new_run = np.where(daily_first, -L, new_run)
                    trading &= ~daily_first
                    dd_hit &= ~daily_first
                else:
                    failed_as[daily_first] = FAIL_DAILY
                    alive &= ~daily_first
                    dd_hit &= ~daily_first
            failed_as[dd_hit] = FAIL_DD
            alive &= ~dd_hit
            b = np.where(live, new_b, b)
            run = np.where(live, new_run, run)
            cpk = np.where(live, np.maximum(cpk, b), cpk)
            if intraday:
                pk = np.where(alive, np.maximum(pk, b), pk)
                th = threshold(pk)

        # fine giornata
        days_in_phase[active] += 1
        if cfg.drawdown_type == DD_TRAILING_EOD:
            pk = np.maximum(pk, b)
            th = threshold(pk)
        bal[active] = b
        peak[active] = pk
        thr[active] = th
        closed_peak[active] = cpk
        eval_dd[active] = mdd
        in_eval = phase[active] == 0
        dead = ~alive

        # bocciati
        idx = active[dead & in_eval]
        eval_outcome[idx] = failed_as[dead & in_eval]
        eval_days[idx] = days_in_phase[idx]
        eval_max_dd[idx] = eval_dd[idx]
        phase[idx] = 2
        idx = active[dead & ~in_eval]
        funded_outcome[idx] = failed_as[dead & ~in_eval]
        funded_days[idx] = days_in_phase[idx]
        phase[idx] = 2

        # valutazione passata: il conto finanziato riparte dal saldo iniziale il giorno dopo
        ok = alive & in_eval & (b >= target) & (days_in_phase[active] >= cfg.min_days)
        idx = active[ok]
        eval_outcome[idx] = PASSED
        eval_days[idx] = days_in_phase[idx]
        eval_max_dd[idx] = eval_dd[idx]
        if cfg.eval_only:
            phase[idx] = 2
        else:
            phase[idx] = 1
            funded_outcome[idx] = INCOMPLETE
            bal[idx] = 0.0
            peak[idx] = 0.0
            thr[idx] = -D
            days_in_phase[idx] = 0
            qualifying[idx] = 0

        # conto finanziato: giornate valide e primo payout
        funded = alive & ~in_eval
        good = funded & (run >= cfg.payout_min_profit) & (run > 0)
        qualifying[active[good]] += 1
        ready = funded & (qualifying[active] >= cfg.payout_days) & (b > 0)
        idx = active[ready]
        funded_outcome[idx] = PASSED
        funded_days[idx] = days_in_phase[idx]
        payout[idx] = bal[idx]
        phase[idx] = 2

        # percorsi per il grafico (solo valutazione)
        track = active < n_paths
        if track.any():
            rows = active[track]
            was_eval = in_eval[track]
            paths[rows[was_eval], t + 1] = b[track][was_eval]

        if progress is not None and t % 25 == 0:
            if not progress(S - len(active), S):
                break

    # chi non ha concluso entro le giornate massime
    eval_max_dd[phase == 0] = eval_dd[phase == 0]
    eval_outcome[phase == 0] = INCOMPLETE
    eval_days[phase == 0] = days_in_phase[phase == 0]
    funded_outcome[phase == 1] = INCOMPLETE
    funded_days[phase == 1] = days_in_phase[phase == 1]

    last = int(np.max(np.where(np.isfinite(paths), np.arange(paths.shape[1]), 0))) if n_paths else 0
    return PropFirmResult(
        config=cfg,
        n_days=n_days,
        calendar_per_day=float(calendar_per_day),
        max_days=max_days,
        eval_outcome=eval_outcome,
        eval_days=eval_days,
        funded_outcome=funded_outcome,
        funded_days=funded_days,
        payout=payout,
        paths=paths[:, : last + 1],
        path_outcome=eval_outcome[:n_paths].copy(),
        day_pnl=P.sum(axis=1),
        eval_max_dd=eval_max_dd,
    )


def describe(values: np.ndarray) -> dict[str, float]:
    """Media, mediana, percentili 10/90, minimo e massimo (nan se vuoto)."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {k: float("nan") for k in ("mean", "median", "p10", "p90", "min", "max")}
    return {
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "p10": float(np.percentile(values, 10)),
        "p90": float(np.percentile(values, 90)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


# --------------------------------------------------------------------------- selezione delle strategie

RISK_HISTORICAL = "historical"
RISK_EVAL = "evaluation"
RISK_MEASURES = {
    RISK_HISTORICAL: "DD storico",
    RISK_EVAL: "DD in valutazione (95%)",
}

SORT_EXPECTED = "expected"
SORT_DAYS = "days"
SORT_PASS = "pass"
SORT_PAYOUT = "payout"
SORT_KEYS = {
    SORT_EXPECTED: "Tempo per passare",
    SORT_DAYS: "Giornate quando passa",
    SORT_PASS: "Probabilità di passare",
    SORT_PAYOUT: "Tempo al primo payout",
}

STATUS_OK = "ok"  # rischio entro il drawdown desiderato
STATUS_TOLERATED = "tolerated"  # oltre il desiderato ma entro il massimo accettabile
STATUS_OVER = "over"  # oltre il massimo accettabile: esclusa


@dataclass
class OptimizerOptions:
    desired_dd: float = 2_000.0  # drawdown che si vorrebbe non superare
    max_dd: float = 2_500.0  # oltre questo la combinazione è esclusa; fino a qui conta solo la velocità
    risk_measure: str = RISK_HISTORICAL
    sort_by: str = SORT_EXPECTED
    min_size: int = 1
    max_size: int = 0  # 0 = fino a tutte le strategie
    sims: int = 300  # simulazioni per combinazione nel primo passaggio
    refine_top: int = 10  # le migliori vengono ricalcolate con tutte le simulazioni
    max_days: int = 250  # giornate massime di una valutazione (oltre: non passata)


@dataclass
class ComboResult:
    members: tuple[int, ...]
    names: tuple[str, ...]
    hist_dd: float
    net_profit: float
    eval_dd95: float = float("nan")
    pass_rate: float = float("nan")
    days_mean: float = float("nan")
    days_median: float = float("nan")
    expected_days: float = float("nan")
    payout_rate: float = float("nan")
    expected_payout_days: float = float("nan")
    status: str = STATUS_OK
    refined: bool = False
    simulated: bool = False

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def accounts(self) -> float:
        """Account medi da acquistare per passare una valutazione (1 / probabilità)."""
        return 1 / self.pass_rate if self.pass_rate > 0 else float("inf")

    def risk(self, measure: str) -> float:
        return self.eval_dd95 if measure == RISK_EVAL else self.hist_dd


@dataclass
class OptimizationResult:
    options: OptimizerOptions
    combos: list[ComboResult]  # accettate in ordine di classifica, poi le escluse
    tested: int
    total: int
    cancelled: bool = False
    elapsed: float = 0.0

    @property
    def accepted(self) -> list[ComboResult]:
        return [c for c in self.combos if c.status != STATUS_OVER and c.simulated]

    @property
    def excluded(self) -> list[ComboResult]:
        return [c for c in self.combos if c.status == STATUS_OVER]

    @property
    def best(self) -> ComboResult | None:
        accepted = self.accepted
        return accepted[0] if accepted else None


class _Trades:
    """I soli campi che servono alla simulazione, per unire velocemente molte combinazioni."""

    def __init__(self, entry_time, exit_time, profit, mae):
        self.entry_time, self.exit_time, self.profit, self.mae = entry_time, exit_time, profit, mae

    def __len__(self) -> int:
        return len(self.profit)


def _combine(parts: list[TradeSet]) -> _Trades:
    entry = np.concatenate([p.entry_time for p in parts])
    exit_ = np.concatenate([p.exit_time for p in parts])
    order = np.lexsort((entry, exit_))
    return _Trades(entry[order], exit_[order], np.concatenate([p.profit for p in parts])[order],
                   np.concatenate([p.mae for p in parts])[order])


def count_combinations(n: int, min_size: int = 1, max_size: int = 0) -> int:
    from math import comb

    max_size = max_size or n
    return sum(comb(n, k) for k in range(max(1, min_size), min(n, max_size) + 1))


def _classify(combo: ComboResult, opts: OptimizerOptions) -> str:
    risk = combo.risk(opts.risk_measure)
    if not np.isfinite(risk):
        return STATUS_OK
    limit = max(opts.max_dd, opts.desired_dd)
    if risk > limit:
        return STATUS_OVER
    return STATUS_OK if risk <= opts.desired_dd else STATUS_TOLERATED


def _fill(combo: ComboResult, r: PropFirmResult, opts: OptimizerOptions) -> None:
    passed = r.passed
    combo.simulated = True
    combo.pass_rate = r.pass_rate
    combo.days_mean = float(r.eval_days[passed].mean()) if passed.any() else float("nan")
    combo.days_median = float(np.median(r.eval_days[passed])) if passed.any() else float("nan")
    combo.expected_days = r.expected_days_to_pass
    combo.eval_dd95 = float(np.percentile(r.eval_max_dd, 95)) if len(r.eval_max_dd) else float("nan")
    if not r.config.eval_only:
        combo.payout_rate = r.payout_rate
        combo.expected_payout_days = r.expected_days_to_payout
    combo.status = _classify(combo, opts)


def _sort_key(combo: ComboResult, opts: OptimizerOptions):
    risk = combo.risk(opts.risk_measure)
    risk = risk if np.isfinite(risk) else 0.0
    if opts.sort_by == SORT_PASS:
        return (-combo.pass_rate, combo.expected_days, risk)
    if opts.sort_by == SORT_DAYS:
        days = combo.days_mean if np.isfinite(combo.days_mean) else float("inf")
        return (days, -combo.pass_rate, risk)
    if opts.sort_by == SORT_PAYOUT:
        return (combo.expected_payout_days, combo.expected_days, risk)
    return (combo.expected_days, -combo.pass_rate, risk)


def optimize_combinations(
    strategies: list[tuple[str, TradeSet]],
    cfg: PropFirmConfig,
    opts: OptimizerOptions,
    progress: Callable[[int, int], bool] | None = None,
) -> OptimizationResult:
    """Prova tutte le combinazioni delle strategie e le ordina per velocità di passaggio.

    1. Per ogni combinazione: drawdown storico e, se non è già oltre il massimo accettabile, una simulazione
       veloce (``opts.sims`` simulazioni, solo valutazione salvo l'ordinamento per payout).
    2. Le ``opts.refine_top`` migliori vengono ricalcolate con tutte le simulazioni di ``cfg`` e con il payout.

    Tutte le combinazioni usano lo stesso seed, così il confronto non dipende dalla fortuna del sorteggio."""
    start = time.time()
    n = len(strategies)
    max_size = min(n, opts.max_size or n)
    combos_idx = [c for k in range(max(1, opts.min_size), max_size + 1) for c in itertools.combinations(range(n), k)]
    total = len(combos_idx) + min(opts.refine_top, len(combos_idx))
    seed = cfg.seed if cfg.seed is not None else int(np.random.default_rng().integers(1, 2**31 - 1))
    screen_cfg = replace(cfg, n_sims=opts.sims, eval_only=opts.sort_by != SORT_PAYOUT, max_days=opts.max_days,
                         n_paths=0, seed=seed)
    full_cfg = replace(cfg, eval_only=False, n_paths=0, seed=seed,
                       max_days=cfg.max_days or opts.max_days + 4 * max(cfg.payout_days, 1) + 60)
    parts = [ts for _name, ts in strategies]
    results: list[ComboResult] = []
    cancelled = False
    done = 0

    def trades_of(members) -> _Trades:
        return _combine([parts[i] for i in members])

    for members in combos_idx:
        trades = trades_of(members)
        info = drawdown_info(trades.profit * cfg.multiplier)
        combo = ComboResult(members, tuple(strategies[i][0] for i in members), info.max_dd,
                            float(trades.profit.sum() * cfg.multiplier))
        results.append(combo)
        if opts.risk_measure == RISK_HISTORICAL and _classify(combo, opts) == STATUS_OVER:
            combo.status = STATUS_OVER  # già troppo rischiosa: inutile simularla
        else:
            _fill(combo, run_prop_firm(trades, screen_cfg), opts)
        done += 1
        if progress is not None and not progress(done, total):
            cancelled = True
            break

    accepted = sorted((c for c in results if c.simulated and c.status != STATUS_OVER), key=lambda c: _sort_key(c, opts))
    if not cancelled:
        for combo in accepted[: opts.refine_top]:
            _fill(combo, run_prop_firm(trades_of(combo.members), full_cfg), opts)
            combo.refined = True
            done += 1
            if progress is not None and not progress(done, total):
                cancelled = True
                break
    accepted = sorted((c for c in results if c.simulated and c.status != STATUS_OVER),
                      key=lambda c: (not c.refined, _sort_key(c, opts)))
    over = sorted((c for c in results if c.status == STATUS_OVER), key=lambda c: c.risk(opts.risk_measure))
    others = [c for c in results if not c.simulated and c.status != STATUS_OVER]
    return OptimizationResult(opts, accepted + others + over, tested=len(results), total=len(combos_idx),
                              cancelled=cancelled, elapsed=time.time() - start)

