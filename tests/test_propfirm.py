"""Simulazione prop firm: regole su casi costruiti a mano e confronto con un'implementazione di riferimento."""

import glob
import itertools
import os

import numpy as np
import pytest
from conftest import EXAMPLES_DIR

from nt8analyzer.models import TradeSet
from nt8analyzer.parser import parse_file
from nt8analyzer.propfirm import (
    DAILY_FAIL,
    DAILY_STOP,
    DD_STATIC,
    DD_TRAILING_EOD,
    DD_TRAILING_INTRADAY,
    FAIL_DAILY,
    FAIL_DD,
    INCOMPLETE,
    NOT_STARTED,
    PASSED,
    PropFirmConfig,
    describe,
    run_prop_firm,
)


def trades_by_day(days: list[list[float]], mae: list[list[float]] | None = None) -> TradeSet:
    """Un TradeSet con i trade indicati, una giornata dopo l'altra (dal 2 gennaio 2024)."""
    entry, exit_, pnl, maes = [], [], [], []
    for d, day in enumerate(days):
        date = np.datetime64("2024-01-02") + np.timedelta64(d, "D")
        for k, p in enumerate(day):
            t = date + np.timedelta64(9 * 3600 + k * 600, "s")
            entry.append(t)
            exit_.append(t + np.timedelta64(300, "s"))
            pnl.append(p)
            maes.append(mae[d][k] if mae else max(0.0, -p))
    return TradeSet(entry_time=entry, exit_time=exit_, profit=pnl, mae=maes)


def run_in_order(days, cfg: PropFirmConfig, order=None, mae=None):
    order = list(range(len(days))) if order is None else order
    return run_prop_firm(trades_by_day(days, mae), cfg, orders=np.array([order]))


def test_payout_counts_small_days_in_amount_but_not_as_valid_days():
    # +80 non è una giornata valida (minimo 100) ma resta nel payout
    cfg = PropFirmConfig(profit_target=100, max_drawdown=1000, payout_days=2, payout_min_profit=100)
    r = run_in_order([[150.0], [80.0], [150.0], [120.0]], cfg)
    assert r.eval_outcome[0] == PASSED and r.eval_days[0] == 1
    assert r.funded_outcome[0] == PASSED
    assert r.funded_days[0] == 3  # 80 (non valida), 150, 120
    assert r.payout[0] == pytest.approx(350.0)


def test_min_days_before_passing():
    cfg = PropFirmConfig(profit_target=100, max_drawdown=1000, min_days=3)
    r = run_in_order([[500.0], [10.0], [-5.0], [1.0]], cfg)
    assert r.eval_outcome[0] == PASSED and r.eval_days[0] == 3


def test_static_drawdown_and_daily_limit_zero_means_none():
    days = [[-600.0], [-500.0], [2000.0]]
    static = PropFirmConfig(profit_target=5000, max_drawdown=1000, drawdown_type=DD_STATIC)
    r = run_in_order(days, static)
    assert r.eval_outcome[0] == FAIL_DD and r.eval_days[0] == 2
    assert r.funded_outcome[0] == NOT_STARTED
    # limite giornaliero 0 = nessun limite: una giornata da -900 non boccia
    r = run_in_order([[-900.0], [3000.0]], PropFirmConfig(profit_target=2000, max_drawdown=1000, daily_loss=0))
    assert r.eval_outcome[0] == PASSED
    # con limite a 500 la stessa giornata boccia per il limite giornaliero
    r = run_in_order([[-900.0], [3000.0]], PropFirmConfig(profit_target=2000, max_drawdown=1000, daily_loss=500))
    assert r.eval_outcome[0] == FAIL_DAILY


def test_daily_limit_stop_closes_the_day_at_the_limit():
    cfg = PropFirmConfig(profit_target=1000, max_drawdown=2000, daily_loss=300, daily_action=DAILY_STOP)
    r = run_in_order([[-200.0, -250.0, 400.0], [900.0], [500.0]], cfg)
    # giorno 1: -200, poi -250 porta a -450 -> chiusura a -300, il +400 non si fa
    assert r.eval_outcome[0] == PASSED
    assert r.eval_days[0] == 3  # -300 + 900 = 600, + 500 = 1100


def test_trailing_eod_locks_at_initial_balance():
    cfg = PropFirmConfig(profit_target=5000, max_drawdown=1000, drawdown_type=DD_TRAILING_EOD, trailing_lock=True)
    # picco a fine giornata +2000: il limite sale fino a 0 (saldo iniziale) e si ferma lì
    r = run_in_order([[2000.0], [-1500.0], [-600.0]], cfg)
    assert r.eval_outcome[0] == FAIL_DD and r.eval_days[0] == 3  # +500, poi -100: sotto il saldo iniziale
    r = run_in_order([[2000.0], [-1900.0], [100.0]], cfg)
    assert r.eval_outcome[0] == INCOMPLETE  # +100 > 0: ancora vivo alla fine dei dati
    unlocked = PropFirmConfig(profit_target=5000, max_drawdown=1000, drawdown_type=DD_TRAILING_EOD, trailing_lock=False)
    r = run_in_order([[2000.0], [-1100.0]], unlocked)
    assert r.eval_outcome[0] == FAIL_DD  # limite a +1000 senza blocco


def test_trailing_intraday_follows_closed_trades():
    days = [[800.0, -1100.0], [2000.0]]
    eod = run_in_order(days, PropFirmConfig(profit_target=1500, max_drawdown=1000, drawdown_type=DD_TRAILING_EOD))
    intraday = run_in_order(days, PropFirmConfig(profit_target=1500, max_drawdown=1000,
                                                 drawdown_type=DD_TRAILING_INTRADAY))
    assert eod.eval_outcome[0] == PASSED  # a fine giornata -300: il limite resta a -1000
    assert intraday.eval_outcome[0] == FAIL_DD  # picco +800 durante il giorno (limite -200), poi -300


def test_mae_is_used_only_when_enabled():
    days = [[100.0], [1000.0]]
    mae = [[1200.0], [0.0]]  # il primo trade scende a -1200 prima di chiudere a +100
    cfg = PropFirmConfig(profit_target=1000, max_drawdown=1000, drawdown_type=DD_STATIC)
    assert run_in_order(days, cfg, mae=mae).eval_outcome[0] == PASSED
    cfg.use_mae = True
    assert run_in_order(days, cfg, mae=mae).eval_outcome[0] == FAIL_DD


def _reference(days_pnl, days_mae, order, cfg: PropFirmConfig):
    """Implementazione diretta, trade per trade, delle stesse regole."""
    D, L = cfg.max_drawdown, cfg.daily_loss

    def limit(peak):
        level = peak - D
        return min(level, 0.0) if cfg.trailing_lock else level

    phase, bal, peak, thr, days_in, qual = 0, 0.0, 0.0, -D, 0, 0
    result = {"eval": INCOMPLETE, "eval_days": 0, "funded": NOT_STARTED, "funded_days": 0, "payout": np.nan}
    for d in order:
        if phase == 2:
            break
        run, failed, trading = 0.0, None, True
        for p, m in zip(days_pnl[d], days_mae[d]):
            if not trading:
                break
            new_b, new_run = bal + p, run + p
            low_b = min(bal - m, new_b) if cfg.use_mae else new_b
            low_run = min(run - m, new_run) if cfg.use_mae else new_run
            dd_hit = low_b <= thr
            if L > 0 and low_run <= -L:
                daily_level = bal - run - L
                if not dd_hit or daily_level >= thr:
                    if cfg.daily_action == DAILY_STOP:
                        new_b, new_run, trading, dd_hit = daily_level, -L, False, False
                    else:
                        failed = FAIL_DAILY
                        break
            if dd_hit:
                failed = FAIL_DD
                break
            bal, run = new_b, new_run
            if cfg.drawdown_type == DD_TRAILING_INTRADAY:
                peak = max(peak, bal)
                thr = limit(peak)
        days_in += 1
        if failed is not None:
            key = "eval" if phase == 0 else "funded"
            result[key], result[key + "_days"] = failed, days_in
            phase = 2
            break
        if cfg.drawdown_type == DD_TRAILING_EOD:
            peak = max(peak, bal)
            thr = limit(peak)
        if phase == 0:
            if bal >= cfg.profit_target and days_in >= cfg.min_days:
                result["eval"], result["eval_days"], result["funded"] = PASSED, days_in, INCOMPLETE
                phase, bal, peak, thr, days_in, qual = 1, 0.0, 0.0, -D, 0, 0
        else:
            if run >= cfg.payout_min_profit and run > 0:
                qual += 1
            if qual >= cfg.payout_days and bal > 0:
                result["funded"], result["funded_days"], result["payout"] = PASSED, days_in, bal
                phase = 2
    if phase == 0:
        result["eval_days"] = days_in
    elif phase == 1:
        result["funded_days"] = days_in
    return result


def test_vectorized_engine_matches_reference():
    files = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
    ts = TradeSet.combine([parse_file(f).trades for f in files])
    rng = np.random.default_rng(3)
    base = run_prop_firm(ts, PropFirmConfig(n_sims=2, seed=1))
    n_days = base.n_days
    from nt8analyzer.propfirm import _day_matrix

    P, A, M, _ = _day_matrix(ts, 1.0)
    days_pnl = [list(P[d][M[d]]) for d in range(n_days)]
    days_mae = [list(A[d][M[d]]) for d in range(n_days)]
    orders = np.vstack([rng.permutation(n_days) for _ in range(12)])
    combos = itertools.product(
        (DD_TRAILING_EOD, DD_TRAILING_INTRADAY, DD_STATIC), (True, False), (0.0, 250.0), (DAILY_FAIL, DAILY_STOP),
        (False, True),
    )
    for dd_type, lock, daily, action, use_mae in combos:
        cfg = PropFirmConfig(profit_target=1500, max_drawdown=700, drawdown_type=dd_type, trailing_lock=lock,
                             daily_loss=daily, daily_action=action, use_mae=use_mae, min_days=3,
                             payout_days=4, payout_min_profit=80)
        r = run_prop_firm(ts, cfg, orders=orders)
        for s in range(len(orders)):
            ref = _reference(days_pnl, days_mae, orders[s], cfg)
            where = (dd_type, lock, daily, action, use_mae, s)
            assert r.eval_outcome[s] == ref["eval"], where
            assert r.eval_days[s] == ref["eval_days"], where
            assert r.funded_outcome[s] == ref["funded"], where
            assert r.funded_days[s] == ref["funded_days"], where
            if ref["funded"] == PASSED:
                assert r.payout[s] == pytest.approx(ref["payout"]), where


def test_random_runs_are_reproducible_and_consistent():
    files = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
    ts = parse_file(files[0]).trades
    cfg = PropFirmConfig(max_drawdown=800, daily_loss=300, n_sims=500, seed=42, multiplier=1.0)
    a, b = run_prop_firm(ts, cfg), run_prop_firm(ts, cfg)
    assert np.array_equal(a.eval_outcome, b.eval_outcome)
    assert np.array_equal(a.funded_days, b.funded_days)
    total = sum(a.rate(a.eval_outcome, v) for v in (PASSED, FAIL_DD, FAIL_DAILY, INCOMPLETE))
    assert total == pytest.approx(1.0)
    assert a.payout_rate <= a.pass_rate
    assert np.all(a.payout[a.paid] > 0)
    assert np.isnan(a.payout[~a.paid]).all()
    assert a.paths.shape[0] == 250 and np.all(a.paths[:, 0] == 0)
    # moltiplicatore: con metà contratti si fallisce di meno ma si passa più lentamente
    half = run_prop_firm(ts, PropFirmConfig(max_drawdown=800, daily_loss=300, n_sims=500, seed=42, multiplier=0.5))
    assert half.rate(half.eval_outcome, FAIL_DD) <= a.rate(a.eval_outcome, FAIL_DD)
    stats = describe(a.eval_days[a.passed])
    assert stats["min"] <= stats["median"] <= stats["max"]
    assert np.isnan(describe(np.array([]))["mean"])


# --------------------------------------------------------------------------- selezione delle strategie


def _strategy(seed: int, win: float, loss: float, p_win: float, n_days: int = 250) -> TradeSet:
    rng = np.random.default_rng(seed)
    days = [[win if rng.random() < p_win else -loss] for _ in range(n_days)]
    return trades_by_day(days)


def test_optimizer_ranks_by_speed_within_the_risk_limit():
    from nt8analyzer.propfirm import (
        RISK_EVAL,
        SORT_PASS,
        STATUS_OK,
        STATUS_OVER,
        STATUS_TOLERATED,
        OptimizerOptions,
        count_combinations,
        optimize_combinations,
    )

    strategies = [
        ("Lenta sicura", _strategy(1, 60, 40, 0.55)),
        ("Veloce", _strategy(2, 300, 200, 0.55)),
        ("Molto rischiosa", _strategy(3, 1500, 1400, 0.5)),
    ]
    cfg = PropFirmConfig(profit_target=3000, max_drawdown=2500, n_sims=400, seed=7)
    opts = OptimizerOptions(desired_dd=1500, max_dd=2500, sims=150, refine_top=3)
    res = optimize_combinations(strategies, cfg, opts)
    assert res.total == count_combinations(3) == 7 and res.tested == 7 and not res.cancelled
    by_names = {c.names: c for c in res.combos}
    # rischio classificato con il drawdown storico: desiderato 1.500, accettato fino a 2.500
    for combo in res.combos:
        if combo.hist_dd <= 1500:
            assert combo.status == STATUS_OK
        elif combo.hist_dd <= 2500:
            assert combo.status == STATUS_TOLERATED
        else:
            assert combo.status == STATUS_OVER and not combo.simulated  # esclusa senza simularla
    assert by_names[("Molto rischiosa",)].status == STATUS_OVER
    accepted = res.accepted
    assert accepted and all(c.status != STATUS_OVER for c in accepted)
    # le verificate (✓) vengono prima e sono in ordine di tempo per passare
    refined = [c for c in accepted if c.refined]
    assert len(refined) == min(3, len(accepted)) and accepted[: len(refined)] == refined
    assert [c.expected_days for c in refined] == sorted(c.expected_days for c in refined)
    assert all(np.isfinite(c.payout_rate) for c in refined)  # con il payout
    # la strategia lenta è la più lenta a passare
    assert by_names[("Veloce",)].expected_days < by_names[("Lenta sicura",)].expected_days
    assert res.best is accepted[0]

    # misura sul drawdown in valutazione: tutte simulate; ordinamento per probabilità
    res = optimize_combinations(strategies, cfg, OptimizerOptions(desired_dd=1500, max_dd=2500, sims=150,
                                                                   refine_top=2, risk_measure=RISK_EVAL,
                                                                   sort_by=SORT_PASS, max_size=2))
    assert res.total == 6 and all(c.simulated for c in res.combos)
    top = [c for c in res.accepted if c.refined]
    assert [c.pass_rate for c in top] == sorted((c.pass_rate for c in top), reverse=True)

    # annullamento
    calls = []
    res = optimize_combinations(strategies, cfg, opts, progress=lambda done, total: calls.append(done) or done < 2)
    assert res.cancelled and res.tested == 2


def test_expected_days_include_failed_attempts():
    cfg = PropFirmConfig(profit_target=1000, max_drawdown=500, drawdown_type=DD_STATIC)
    days = [[600.0], [600.0], [-300.0]]
    orders = np.array([[0, 1, 2], [2, 2, 0], [2, 0, 1], [1, 0, 2]])
    r = run_prop_firm(trades_by_day(days), cfg, orders=orders)
    # 1° e 4° passano in 2 giornate; 2° bocciata in 2 (-300, -300); 3° non conclusa in 3 (-300, +600, +600 = 900)
    assert list(r.eval_outcome) == [PASSED, FAIL_DD, INCOMPLETE, PASSED]
    assert r.pass_rate == 0.5
    # giornate delle passate + giornate perse nei tentativi non riusciti × (1 - p) / p
    assert r.expected_days_to_pass == pytest.approx(2 + 2.5 * 1)
    assert list(r.eval_max_dd) == pytest.approx([0.0, 600.0, 300.0, 0.0])
