import numpy as np

from nt8analyzer.metrics import drawdown_info
from nt8analyzer.models import LoadedStrategy, TradeSet
from nt8analyzer.portfolio import COMBINED_KEY, Portfolio


def ts(rows):
    """rows: (entry, exit, profit)"""
    return TradeSet(
        entry_time=np.array([r[0] for r in rows], dtype="datetime64[s]"),
        exit_time=np.array([r[1] for r in rows], dtype="datetime64[s]"),
        profit=[r[2] for r in rows],
    )


A = ts(
    [
        ("2024-01-01T09:00", "2024-01-01T10:00", -100),
        ("2024-01-02T09:00", "2024-01-02T12:00", 300),
        ("2024-01-03T09:00", "2024-01-03T10:00", -100),
    ]
)
B = ts(
    [
        ("2024-01-01T09:30", "2024-01-01T09:45", -50),
        ("2024-01-02T10:00", "2024-01-02T11:00", -200),
        ("2024-01-03T11:00", "2024-01-03T11:30", 100),
    ]
)


def test_combine_orders_by_exit_time():
    combined = TradeSet.combine([A, B])
    assert len(combined) == 6
    assert np.all(np.diff(combined.exit_time.astype(np.int64)) >= 0)
    np.testing.assert_allclose(combined.profit, [-50, -100, -200, 300, -100, 100])


def test_combined_drawdown_differs_from_single_strategies():
    combined = TradeSet.combine([A, B])
    # equity: 0,-50,-150,-350,-50,-150,-50 -> max DD 350 dal punto di partenza
    assert drawdown_info(combined.profit).max_dd == 350
    assert drawdown_info(A.profit).max_dd == 100
    assert drawdown_info(B.profit).max_dd == 250


def _strategy(uid, trades, included=True):
    return LoadedStrategy(uid=uid, name=f"S{uid}", source_path=f"s{uid}.csv", trades=trades, color_index=uid, included=included)


def test_portfolio_combined_only_with_two_or_more():
    single = Portfolio([_strategy(1, A)], capital=1000)
    assert single.combined is None
    assert single.primary.key == "1"

    both = Portfolio([_strategy(1, A), _strategy(2, B)], capital=1000)
    assert both.combined is not None
    assert both.primary.key == COMBINED_KEY
    assert both.combined.stats["max_dd"] == 350
    assert set(both.combined.trades.strategy) == {"S1", "S2"}
    assert [e.key for e in both.entities()] == [COMBINED_KEY, "1", "2"]


def test_excluded_strategies_are_ignored():
    p = Portfolio([_strategy(1, A), _strategy(2, B, included=False)])
    assert p.combined is None
    assert [e.key for e in p.entities()] == ["1"]
