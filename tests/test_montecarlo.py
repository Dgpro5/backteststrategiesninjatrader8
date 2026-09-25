import numpy as np
import pytest

from nt8analyzer.metrics import drawdown_info, max_streak, worst_losing_run
from nt8analyzer.montecarlo import (
    BOOTSTRAP,
    RANK_FINAL,
    SHUFFLE,
    MCConfig,
    _path_metrics,
    run_monte_carlo,
)

RNG = np.random.default_rng(7)
PNL = np.round(RNG.choice([100.0, -150.0, 40.0, -20.0], size=300, p=[0.45, 0.3, 0.15, 0.1]), 2)


def test_vectorized_metrics_match_scalar_versions():
    rows = np.vstack([np.random.default_rng(i).permutation(PNL) for i in range(5)])
    m = _path_metrics(rows, capital=5000)
    for i, row in enumerate(rows):
        info = drawdown_info(row, 5000)
        assert m["max_dd"][i] == pytest.approx(info.max_dd)
        assert m["max_dd_pct"][i] == pytest.approx(info.max_dd_pct)
        assert m["max_consec_losses"][i] == max_streak(row < 0)
        assert m["max_consec_loss_amount"][i] == pytest.approx(worst_losing_run(row))
        assert m["final"][i] == pytest.approx(row.sum())


def test_shuffle_keeps_final_profit_and_ranks_by_drawdown():
    r = run_monte_carlo(PNL, MCConfig(n_sims=400, method=SHUFFLE, seed=1, capital=10_000))
    assert r.n_sims == 400
    np.testing.assert_allclose(r.metrics["final"], PNL.sum())
    assert drawdown_info(np.diff(r.best_curve)).max_dd == pytest.approx(r.metrics["max_dd"].min())
    assert drawdown_info(np.diff(r.worst_curve)).max_dd == pytest.approx(r.metrics["max_dd"].max())
    # la media dello shuffle è la retta da 0 al profitto finale
    np.testing.assert_allclose(r.mean_curve[-1], PNL.sum())
    dd_row = next(s for s in r.summary if s.key == "max_dd")
    assert dd_row.best <= dd_row.median <= dd_row.conf95 <= dd_row.conf99 <= dd_row.worst
    assert dd_row.original == pytest.approx(drawdown_info(PNL).max_dd)


def test_bootstrap_custom_length_and_seed():
    cfg = MCConfig(n_sims=200, method=BOOTSTRAP, n_trades=50, seed=42, rank_by=RANK_FINAL)
    r1 = run_monte_carlo(PNL, cfg)
    r2 = run_monte_carlo(PNL, cfg)
    assert r1.n_trades == 50
    assert r1.plot_curves.shape == (200, 51)
    np.testing.assert_allclose(r1.metrics["final"], r2.metrics["final"])
    assert r1.best_curve[-1] == pytest.approx(r1.metrics["final"].max())
    assert r1.worst_curve[-1] == pytest.approx(r1.metrics["final"].min())
    final_row = next(s for s in r1.summary if s.key == "final")
    assert final_row.worst <= final_row.conf99 <= final_row.conf95 <= final_row.median <= final_row.best


def test_chunking_and_plot_limit():
    big = np.tile(PNL, 10)  # 3000 trade -> blocchi da ~666 simulazioni
    r = run_monte_carlo(big, MCConfig(n_sims=1500, seed=3, max_plot=100))
    assert r.n_sims == 1500
    assert len(r.plot_curves) == 100
    assert r.mean_curve.shape == (len(big) + 1,)


def test_threshold_probability():
    r = run_monte_carlo(PNL, MCConfig(n_sims=300, seed=5))
    assert r.prob_dd_exceeds(0) is None
    assert r.prob_dd_exceeds(1e9) == 0.0
    assert r.prob_dd_exceeds(1e-9) == 1.0


def test_empty_input_raises():
    with pytest.raises(ValueError):
        run_monte_carlo(np.array([]), MCConfig())
