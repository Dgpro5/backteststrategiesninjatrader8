"""Stampa e PDF: un report per ogni scheda, sempre su carta chiara."""

import glob
import os

import pytest
from conftest import EXAMPLES_DIR

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

EXAMPLES = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))


@pytest.fixture
def window():
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui import theme
    from nt8analyzer.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.set_mode("light")
    theme.apply_theme(app)
    win = MainWindow(theme_preference="light", persist_theme=False)
    yield win
    theme.set_mode("light")
    theme.apply_theme(app)
    win.close()


def _is_pdf(path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(5) == b"%PDF-"


def test_report_for_every_tab(window, tmp_path):
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import ChartBlock, ChartGrid, TableBlock, export_pdf, render_images

    window.load_files(EXAMPLES, interactive=False)
    titles = []
    for i in range(window.tabs.count()):
        content = build_report(window, i)
        assert content is not None
        titles.append(content.title)
        assert any(isinstance(b, TableBlock) for b in content.blocks)
        images = render_images(content, dpi=60)
        assert images and all(not img.isNull() for img in images)
        pdf = tmp_path / f"{i}.pdf"
        assert export_pdf(content, str(pdf)) == len(images)
        assert _is_pdf(pdf) and pdf.stat().st_size > 5_000
    assert titles == ["Statistiche", "Equity curve", "Monte Carlo", "Analisi grafica", "Lista trade"]

    # la scheda visibile è quella stampata
    window.tabs.setCurrentIndex(2)
    content = build_report(window)
    assert content.title == "Monte Carlo"
    charts = [b for b in content.blocks if isinstance(b, (ChartBlock, ChartGrid))]
    assert len(charts) == 2
    window.tabs.setCurrentIndex(3)
    grid = next(b for b in build_report(window).blocks if isinstance(b, ChartGrid))
    assert len(grid.charts) == 9


def test_print_uses_light_paper_in_dark_theme(window):
    from nt8analyzer.ui import theme
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import ChartBlock

    window.load_files(EXAMPLES, interactive=False)
    mc_result = window.mc_tab.result
    window.set_theme_preference("dark", persist=False)
    assert theme.is_dark()
    content = build_report(window, 1)
    assert theme.is_dark()  # il tema dell'app non cambia
    chart = next(b for b in content.blocks if isinstance(b, ChartBlock)).image
    assert chart.width() > 2000  # alta risoluzione
    assert chart.pixelColor(3, 3).name() == "#ffffff"  # carta bianca
    assert window.equity_tab.eq_plot.backgroundBrush().color().name() == theme.DARK["SURFACE"]
    build_report(window, 2)
    assert window.mc_tab.result is mc_result  # il Monte Carlo non viene rieseguito


def test_single_strategy_and_missing_simulation(window):
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import TableBlock, TextBlock, render_images

    assert build_report(window) is None  # nessun dato
    window.load_files(EXAMPLES[:1], interactive=False)
    stats = build_report(window, 0)
    table = next(b for b in stats.blocks if isinstance(b, TableBlock))
    assert [c.header for c in table.columns][1:] == [window.portfolio.strategies[0].name]
    equity = build_report(window, 1)
    assert not any("Correlazione" in getattr(b, "text", "") for b in equity.blocks)
    assert render_images(equity, dpi=50)

    window.mc_tab._clear("")
    mc = build_report(window, 2)
    assert any(isinstance(b, TextBlock) and "Esegui simulazione" in b.text for b in mc.blocks)
    assert render_images(mc, dpi=50)


def test_wide_and_long_tables_are_paginated():
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui.report import (
        Cell,
        ReportContent,
        TableBlock,
        TableColumn,
        TableRow,
        render_images,
    )

    QApplication.instance() or QApplication([])
    columns = [TableColumn("Nome", align="left")] + [TableColumn(f"Colonna numero {i}") for i in range(40)]
    rows = [TableRow([Cell(f"Riga {r}")] + [Cell(f"{r * i:,.2f}") for i in range(40)]) for r in range(120)]
    content = ReportContent("Prova", blocks=[TableBlock(columns, rows, title="Tabella larga")])
    pages = render_images(content, dpi=40)
    # divisa in larghezza (più blocchi di colonne) e in altezza (più pagine per blocco)
    assert len(pages) >= 6


def test_print_and_pdf_actions(window, tmp_path, monkeypatch):
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QFileDialog

    from nt8analyzer.ui import main_window

    window.load_files(EXAMPLES, interactive=False)
    window.tabs.setCurrentIndex(1)

    printed = tmp_path / "anteprima.pdf"

    def fake_exec(dialog):
        # simula l'anteprima: la finestra chiede di disegnare le pagine sulla stampante
        printer = dialog.printer()
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(printed))
        dialog.paintRequested.emit(printer)
        return 0

    monkeypatch.setattr(main_window.QPrintPreviewDialog, "exec", fake_exec)
    window.print_current()
    assert _is_pdf(printed)

    target = tmp_path / "equity"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF (*.pdf)"))
    window.export_current_pdf()
    assert _is_pdf(tmp_path / "equity.pdf")
    assert "PDF salvato" in window.statusBar().currentMessage()
