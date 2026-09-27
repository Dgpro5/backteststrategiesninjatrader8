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

from dataclasses import dataclass, field

import numpy as np

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
        in_eval = phase[active] == 0
        dead = ~alive

        # bocciati
        idx = active[dead & in_eval]
        eval_outcome[idx] = failed_as[dead & in_eval]
        eval_days[idx] = days_in_phase[idx]
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
