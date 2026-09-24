"""Avvio dell'interfaccia in modalità offscreen con i file di esempio."""

import glob
import os

import pytest
from conftest import EXAMPLES_DIR

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")


def test_app_renders_all_tabs(tmp_path):
    from nt8analyzer.app import main

    files = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
    assert files
    code = main(["--screenshots", str(tmp_path), *files])
    assert code == 0
    shots = sorted(os.listdir(tmp_path))
    assert len(shots) == 5
    assert all(os.path.getsize(tmp_path / s) > 10_000 for s in shots)


def test_single_strategy_and_toggles(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui.main_window import MainWindow
    from nt8analyzer.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow()
    files = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
    window.load_files(files[:1], interactive=False)
    assert window.portfolio.combined is None
    window.load_files(files[1:], interactive=False)
    assert window.portfolio.combined is not None
    # il file già caricato viene ignorato
    errors = window.load_files(files[:1], interactive=False)
    assert errors and "già caricato" in errors[0]
    # escludi una strategia -> portafoglio ricalcolato
    window.list.item(0).setCheckState(Qt.Unchecked)
    window.refresh()
    assert len(window.portfolio.strategies) == len(files) - 1
    # bootstrap dal pannello Monte Carlo
    window.mc_tab.method.setCurrentIndex(1)
    window.mc_tab.sims.setValue(200)
    window.mc_tab.run()
    assert window.mc_tab.result.n_sims == 200
    window.mc_tab.show_original.setChecked(True)
    for i in range(window.mc_tab.hist_metric.count()):
        window.mc_tab.hist_metric.setCurrentIndex(i)
    window.clear_all()
    assert window.portfolio.empty
    window.close()


def test_hover_callbacks_return_content():
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui.main_window import MainWindow
    from nt8analyzer.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow()
    window.load_files(sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv"))), interactive=False)

    eq = window.equity_tab
    x_mid = float(np.median(eq.series[-1].x))
    assert eq._hover_equity(x_mid, 0)
    assert eq._hover_drawdown(x_mid, 0)

    an = window.analysis_tab
    assert an._hover_equity(x_mid, 0)
    assert an._hover_trade(1, 0)
    counts, edges = an._data["hist"]
    assert an._hover_hist(float(edges[0] + edges[1]) / 2, 0)
    for key in ("month", "hour", "weekday", "exit"):
        assert an._hover_bars(key, 0), key

    mc = window.mc_tab
    assert mc._hover(10, 0)
    counts, edges, _ = mc._hist_data
    assert mc._hover_hist(float(edges[0] + edges[1]) / 2, 0)
    window.close()
