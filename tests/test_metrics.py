import numpy as np
import pytest
from conftest import NT_SAMPLE

from nt8analyzer.metrics import (
    compute_stats,
    daily_correlation,
    drawdown_info,
    hour_index,
    mae_drawdown,
    max_streak,
    weekday_index,
    worst_losing_run,
)
from nt8analyzer.models import TradeSet
from nt8analyzer.parser import parse_text


def make_trades(profits, start="2024-01-01T10:00", minutes=60, gap_minutes=None):
    n = len(profits)
    gap = gap_minutes if gap_minutes is not None else 24 * 60
    entry = np.datetime64(start) + np.arange(n) * np.timedelta64(gap, "m")
    return TradeSet(entry_time=entry, exit_time=entry + np.timedelta64(minutes, "m"), profit=profits)


def test_drawdown_known_sequence():
    info = drawdown_info([100, -50, -80, 200, -30])
    np.testing.assert_allclose(info.equity, [0, 100, 50, -30, 170, 140])
    np.testing.assert_allclose(info.drawdown, [0, 0, -50, -130, 0, -30])
    assert info.max_dd == 130
    assert (info.peak_index, info.trough_index, info.recovery_index) == (1, 3, 4)


def test_drawdown_from_start_and_not_recovered():
    info = drawdown_info([-50, 20])
    assert info.max_dd == 50
    assert info.peak_index == 0
    assert info.recovery_index is None


def test_drawdown_percent_uses_capital():
    info = drawdown_info([1000, -550], capital=10_000)
    assert info.max_dd_pct == pytest.approx(550 / 11_000)
    assert np.isnan(drawdown_info([1, -1]).max_dd_pct)


def test_streak_helpers():
    assert max_streak(np.array([True, True, False, True, True, True])) == 3
    assert worst_losing_run(np.array([-10, -20, 5, -40, -1, 3])) == -41


def test_compute_stats_values():
    ts = make_trades([100, -50, -80, 200, -30, 0])
    s = compute_stats(ts, capital=1000)
    assert s["n_trades"] == 6
    assert (s["n_wins"], s["n_losses"], s["n_even"]) == (2, 3, 1)
    assert s["net_profit"] == 140
    assert s["profit_factor"] == pytest.approx(300 / 160)
    assert s["max_dd"] == 130
    assert s["max_consec_losses"] == 2
    assert s["max_consec_loss_amount"] == -130
    assert s["largest_loss"] == -80
    assert s["avg_trade"] == pytest.approx(140 / 6)
    assert s["return_pct"] == pytest.approx(0.14)
    assert s["worst_day"][0] == -80


def test_compute_stats_real_sample():
    ts = parse_text(NT_SAMPLE).trades
    s = compute_stats(ts)
    assert s["net_profit"] == pytest.approx(-2.0)
    assert s["max_dd"] == pytest.approx(200.0)
    assert s["max_dd_pct"] is None  # nessun capitale impostato


def test_mae_drawdown():
    ts = make_trades([100, -50])
    ts.mae[:] = [30, 120]
    # prima del 2° trade equity 100 (picco) -> minimo intra-trade 100-120 = -20 -> DD 120
    assert mae_drawdown(ts) == pytest.approx(120)
    overlapping = make_trades([10, 10], minutes=120, gap_minutes=30)
    assert mae_drawdown(overlapping) is None


def test_time_helpers():
    times = np.array(["2024-02-05T09:35:00", "2024-02-11T14:00:00"], dtype="datetime64[s]")  # lunedì, domenica
    np.testing.assert_array_equal(weekday_index(times), [0, 6])
    np.testing.assert_array_equal(hour_index(times), [9, 14])


def test_daily_correlation():
    a = make_trades([10, -10, 10, -10])
    b = make_trades([-10, 10, -10, 10])
    corr = daily_correlation([a, b])
    assert corr[0, 1] == pytest.approx(-1.0)
    assert corr[0, 0] == 1.0
