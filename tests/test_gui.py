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
    shots = sorted(f for f in os.listdir(tmp_path) if f.endswith(".png"))
    assert len(shots) == 5
    assert all(os.path.getsize(tmp_path / s) > 10_000 for s in shots)
    # report di stampa di ogni scheda: PDF + anteprima della prima pagina
    reports = sorted(os.listdir(tmp_path / "stampa"))
    assert len([f for f in reports if f.endswith(".pdf")]) == 5
    assert len([f for f in reports if f.endswith("_pagina1.png")]) == 5


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


def test_dark_theme_switch_keeps_results(tmp_path):
    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui import theme
    from nt8analyzer.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.set_mode("light")
    theme.apply_theme(app)
    window = MainWindow(theme_preference="light")
    try:
        window.load_files(sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv"))), interactive=False)
        mc_result = window.mc_tab.result
        window.set_theme_preference("dark")
        assert theme.is_dark()
        assert QSettings().value("theme") == "dark"
        assert window.theme_box.currentData() == "dark"
        assert window._theme_actions["dark"].isChecked()
        # grafici ricolorati, Monte Carlo non ricalcolato
        assert window.equity_tab.eq_plot.backgroundBrush().color().name() == theme.DARK["SURFACE"]
        assert window.mc_tab.result is mc_result
        combined = next(s for s in window.equity_tab.series if s.entity.is_combined)
        assert combined.color == theme.DARK["COMBINED"]
        # i tooltip funzionano anche dopo il cambio di tema
        x_mid = float(np.median(combined.x))
        assert window.equity_tab._hover_equity(x_mid, 0)
        assert window.mc_tab._hover(10, 0)
        assert window.grab().save(str(tmp_path / "dark.png"))
        window.set_theme_preference("light")
        assert not theme.is_dark()
        assert window.equity_tab.eq_plot.backgroundBrush().color().name() == theme.LIGHT["SURFACE"]
    finally:
        theme.set_mode("light")
        theme.apply_theme(app)
        window.close()


def test_theme_command_line_is_not_saved(tmp_path):
    from PySide6.QtCore import QSettings

    from nt8analyzer.app import main
    from nt8analyzer.ui import theme

    settings = QSettings("NT8BacktestAnalyzer", "NT8 Backtest Analyzer")
    settings.setValue("theme", "light")
    try:
        files = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
        assert main(["--tema", "scuro", "--screenshots", str(tmp_path), *files]) == 0
        assert theme.is_dark()
        assert QSettings("NT8BacktestAnalyzer", "NT8 Backtest Analyzer").value("theme") == "light"
    finally:
        theme.set_mode("light")


def test_resolve_mode():
    from nt8analyzer.ui import theme

    assert theme.resolve_mode("dark") == "dark"
    assert theme.resolve_mode("light") == "light"
    assert theme.resolve_mode("system") in ("light", "dark")
    assert theme.resolve_mode("qualsiasi") in ("light", "dark")
