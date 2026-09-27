"""Stampa e PDF: un modello per ogni scheda, resoconto completo, fogli verticali e orizzontali."""

import glob
import importlib.util
import os

import pytest
from conftest import EXAMPLES_DIR, ROOT

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

EXAMPLES = sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv")))
TAB_TITLES = ["Statistiche", "Equity curve", "Monte Carlo", "Prop Firm", "Analisi grafica", "Lista trade"]


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


@pytest.fixture(scope="module")
def many_strategies(tmp_path_factory):
    """Nove strategie sintetiche con nomi lunghi (come quando se ne caricano tante)."""
    spec = importlib.util.spec_from_file_location("genera_esempi", os.path.join(ROOT, "tools", "genera_esempi.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    folder = tmp_path_factory.mktemp("molte")
    names = ["BreakoutNQ_Opening_Range_v3", "MeanReversionES_Bollinger", "TrendFollowCL_Donchian55",
             "GapFillNQ_Morning", "ScalperES_VWAP_5min", "SwingNQ_Daily_Pivot", "MomentumYM_ORB_15",
             "CrudeOil_News_Fade", "RTY_MeanRev_RSI2"]
    for i, name in enumerate(names):
        inst = ["MNQ 12-26", "MES 12-26", "MCL 12-26"][i % 3]
        point, tick = {"MNQ 12-26": (2.0, 0.25), "MES 12-26": (5.0, 0.25), "MCL 12-26": (100.0, 0.01)}[inst]
        rows = gen.generate(name, inst, point, tick, 0.18, 90.0, 80.0, 0.5, 200 + i, (9, 30, 14, 30))
        with open(folder / f"{name}.csv", "w", encoding="utf-8") as fh:
            fh.write(gen.HEADER + "\n" + "\n".join(rows) + "\n")
    return sorted(str(p) for p in folder.glob("*.csv"))


def _is_pdf(path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(5) == b"%PDF-"


def test_report_for_every_tab(window, tmp_path):
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import ChartBlock, ChartGrid, PORTRAIT, TableBlock, export_pdf, render_images

    window.load_files(EXAMPLES, interactive=False)
    titles = []
    for i in range(window.tabs.count()):
        content = build_report(window, i)
        assert content is not None
        titles.append(content.title)
        assert any(isinstance(b, TableBlock) for b in content.blocks)
        images = render_images(content, dpi=50)
        assert images and all(not img.isNull() for img in images)
        pdf = tmp_path / f"{i}.pdf"
        assert export_pdf(content, str(pdf)) == len(images)
        assert _is_pdf(pdf) and pdf.stat().st_size > 5_000
        portrait = render_images(content, dpi=40, orientation=PORTRAIT, max_pages=2)
        assert portrait[0].height() > portrait[0].width()
    assert titles == TAB_TITLES

    # la scheda visibile è quella stampata
    window.tabs.setCurrentIndex(2)
    content = build_report(window)
    assert content.title == "Monte Carlo"
    charts = [b for b in content.blocks if isinstance(b, (ChartBlock, ChartGrid))]
    assert len(charts) == 2
    window.tabs.setCurrentIndex(4)
    grid = next(b for b in build_report(window).blocks if isinstance(b, ChartGrid))
    assert len(grid.charts) == 9
    # la correlazione è anche nel foglio delle statistiche
    stats = build_report(window, 0)
    assert any(getattr(b, "text", "").startswith("Correlazione") for b in stats.blocks)


def test_print_uses_light_paper_in_dark_theme(window):
    from nt8analyzer.ui import theme
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import ChartBlock

    window.load_files(EXAMPLES, interactive=False)
    mc_result, prop_result = window.mc_tab.result, window.prop_tab.result
    window.set_theme_preference("dark", persist=False)
    assert theme.is_dark()
    content = build_report(window, 1)
    assert theme.is_dark()  # il tema dell'app non cambia
    chart = next(b for b in content.blocks if isinstance(b, ChartBlock)).render(1000, 400, 2.4)
    assert chart.width() == 2400  # alta risoluzione
    assert chart.pixelColor(3, 3).name() == "#ffffff"  # carta bianca
    assert window.equity_tab.eq_plot.backgroundBrush().color().name() == theme.DARK["SURFACE"]
    build_report(window, 2)
    build_report(window, 3)
    assert window.mc_tab.result is mc_result  # le simulazioni non vengono rifatte
    assert window.prop_tab.result is prop_result
    # un cambio di tema dopo la stampa non ricolora le copie fuori schermo dei grafici
    window.set_theme_preference("light", persist=False)
    again = next(b for b in content.blocks if isinstance(b, ChartBlock)).render(900, 360, 1.0)
    assert again.pixelColor(3, 3).name() == "#ffffff"


def test_single_strategy_and_missing_simulations(window):
    from nt8analyzer.ui.print_templates import build_full_report, build_report
    from nt8analyzer.ui.report import TableBlock, TextBlock, render_images

    assert build_report(window) is None  # nessun dato
    assert build_full_report(window) is None
    window.load_files(EXAMPLES[:1], interactive=False)
    stats = build_report(window, 0)
    table = next(b for b in stats.blocks if isinstance(b, TableBlock))
    assert [c.header for c in table.columns][1:] == [window.portfolio.strategies[0].name]
    equity = build_report(window, 1)
    assert not any("Correlazione" in getattr(b, "text", "") for b in equity.blocks)
    assert render_images(equity, dpi=40)

    window.mc_tab._clear("")
    window.prop_tab._clear("")
    for index in (2, 3):
        section = build_report(window, index)
        assert any(isinstance(b, TextBlock) and "Esegui simulazione" in b.text for b in section.blocks)
        assert render_images(section, dpi=40)
    doc = build_full_report(window)
    assert "correlation" not in [s.key for s in doc.sections]  # una sola strategia


def test_many_strategies_are_split_in_readable_groups(window, many_strategies, monkeypatch):
    from nt8analyzer.ui import report
    from nt8analyzer.ui.print_templates import build_report
    from nt8analyzer.ui.report import LANDSCAPE, PORTRAIT, TableBlock, layout_document

    window.load_files(many_strategies, interactive=False)
    n = len(window.portfolio.strategies)
    assert n == 9
    content = build_report(window, 0)
    stats_table = next(b for b in content.blocks if isinstance(b, TableBlock) and b.group_noun == "strategie")
    combined = len(stats_table.columns) - 1
    parts = []
    original = report._Layout._table_part

    def spy(self, block, indexes, mins, title, size, new_group=False):
        if block is stats_table:
            parts.append((indexes, title, new_group, self.y, len(self.pages)))
        return original(self, block, indexes, mins, title, size, new_group)

    monkeypatch.setattr(report._Layout, "_table_part", spy)
    for orientation in (LANDSCAPE, PORTRAIT):
        parts.clear()
        layout = layout_document(content, orientation)
        assert len(parts) >= 2  # 9 strategie non entrano in un solo foglio
        shown = []
        for indexes, title, new_group, _y, _page in parts:
            assert indexes[0] == 0 and indexes[-1] == combined  # statistica + ... + portafoglio combinato
            assert title.startswith("Strategie ") and title.endswith(f" di {n}")
            shown += indexes[1:-1]
        assert sorted(shown) == list(range(1, combined))  # ogni strategia compare una volta, con tutte le righe
        # i gruppi dopo il primo iniziano su una pagina nuova, sotto l'intestazione
        for _indexes, title, new_group, y, _page in parts[1:]:
            assert new_group
            starts = [i for i, label in enumerate(layout.labels) if label.startswith(title)]
            assert starts, title
    # correlazione con 9 strategie: una colonna per strategia più la media
    corr = build_report(window, 1)
    table = next(b for b in corr.blocks if isinstance(b, TableBlock) and b.columns[-1].header == "Media")
    assert len(table.columns) == n + 2 and len(table.rows) == n


def test_wide_long_and_optional_columns():
    from PySide6.QtWidgets import QApplication

    from nt8analyzer.ui.report import (
        PORTRAIT,
        Cell,
        ReportContent,
        TableBlock,
        TableColumn,
        TableRow,
        TextBlock,
        layout_document,
        render_images,
    )

    QApplication.instance() or QApplication([])
    columns = [TableColumn("Nome", align="left")] + [TableColumn(f"Colonna numero {i}") for i in range(40)]
    rows = [TableRow([Cell(f"Riga {r}")] + [Cell(f"{r * i:,.2f}") for i in range(40)]) for r in range(120)]
    content = ReportContent("Prova", blocks=[TableBlock(columns, rows, title="Tabella larga")])
    pages = render_images(content, dpi=30)
    # divisa in larghezza (più gruppi di colonne) e in altezza (più pagine per gruppo)
    assert len(pages) >= 6
    # colonne facoltative: sul foglio verticale si omettono invece di dividere la tabella
    cols = [TableColumn("Data e ora", align="left")] + [TableColumn(f"Valore {i}", priority=1 if i > 5 else 0)
                                                        for i in range(12)]
    rows = [TableRow([Cell("01/02/2024 10:30")] + [Cell(f"-${1000 + i * 37:,.2f}") for i in range(12)])
            for _ in range(10)]
    layout = layout_document(ReportContent("Prova", blocks=[TableBlock(cols, rows)]), PORTRAIT)
    assert not any(label.startswith("Colonne") for label in layout.labels)  # un solo gruppo
    for c in cols:
        c.priority = 0
    layout = layout_document(ReportContent("Prova", blocks=[TableBlock(cols, rows)]), PORTRAIT)
    assert any(label.startswith("Colonne 1–") for label in layout.labels)  # senza priorità va divisa
    text_blocks = ReportContent("Prova", blocks=[TextBlock("x " * 400)])
    assert len(layout_document(text_blocks, PORTRAIT)) == 1


def test_full_report_page_selection_and_numbering(window, tmp_path):
    from PySide6.QtPrintSupport import QPrinter

    from nt8analyzer.ui.print_templates import build_full_report
    from nt8analyzer.ui.report import LANDSCAPE, PORTRAIT, PrintJob, layout_document

    window.load_files(EXAMPLES, interactive=False)
    doc = build_full_report(window)
    keys = [s.key for s in doc.sections]
    assert keys == ["summary", "stats", "correlation", "equity", "montecarlo", "propfirm", "analysis", "trades"]
    land = layout_document(doc, LANDSCAPE)
    port = layout_document(doc, PORTRAIT)
    assert len(land) > 20 and len(port) > 15
    assert land.labels[0].startswith("Contenuto") or "Strategie" in land.labels[0]

    # solo riepilogo, prima pagina del Monte Carlo e Prop Firm: numerate 1..n
    mc = keys.index("montecarlo")
    prop = keys.index("propfirm")
    pages = land.section_pages[0] + land.section_pages[mc][:1] + land.section_pages[prop]
    job = PrintJob(doc, LANDSCAPE, pages, layout=land)
    pdf = tmp_path / "scelta.pdf"
    assert job.export_pdf(str(pdf)) == len(pages)
    assert _is_pdf(pdf)
    assert land.printed_range(0) == (1, 1)
    assert land.printed_range(mc) == (2, 2)
    assert land.printed_range(prop) == (3, len(pages))
    assert land.printed_range(keys.index("trades")) is None  # non stampata: esclusa dall'indice

    # intervallo scelto nella finestra di stampa: pagine 2-3 della selezione
    printer = job.make_printer()
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "intervallo.pdf"))
    printer.setPrintRange(QPrinter.PageRange)
    printer.setFromTo(2, 3)
    assert job.print_to(printer) == 2

    # orientamento cambiato dall'anteprima: si stampano le sezioni scelte, ricalcolate
    other, other_pages = job.layout_for(port.W, port.H)
    assert {other.pages[g][0] for g in other_pages} == {0, mc, prop}


def test_report_dialog(window, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog

    from nt8analyzer.ui import main_window
    from nt8analyzer.ui.report_dialog import ReportDialog

    window.settings.remove("report/excluded")
    window.settings.setValue("print/orientation", "landscape")
    window.load_files(EXAMPLES, interactive=False)
    dialog = ReportDialog(window)
    layout = dialog.current_layout()
    trades = dialog.tree.topLevelItem(dialog.tree.topLevelItemCount() - 1)
    assert trades.data(0, Qt.UserRole) == "trades"
    assert trades.checkState(0) == Qt.Unchecked  # lista trade lunga: esclusa di default
    selected = dialog.selected_pages()
    assert len(selected) == len(layout) - len(layout.section_pages[-1])
    # togliere una singola pagina
    dialog.set_page_checked(selected[1], False)
    assert selected[1] not in dialog.selected_pages()
    assert dialog.tree.topLevelItem(1).checkState(0) in (Qt.PartiallyChecked, Qt.Unchecked)
    assert "Pagine da stampare" in dialog.count.text()
    # foglio verticale: le pagine vengono ricalcolate, le sezioni escluse restano escluse
    dialog.set_section_checked("montecarlo", False)
    dialog.portrait.setChecked(True)
    assert dialog.current_layout().orientation == dialog.orientation
    mc_item = next(dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
                   if dialog.tree.topLevelItem(i).data(0, Qt.UserRole) == "montecarlo")
    assert mc_item.checkState(0) == Qt.Unchecked
    assert window.settings.value("print/orientation") == "portrait"

    printed = []
    monkeypatch.setattr(window, "preview_job", lambda job: printed.append(job))
    dialog.print_selected()
    assert printed and printed[0].pages == dialog.selected_pages()
    assert "montecarlo" in str(window.settings.value("report/excluded"))

    target = tmp_path / "resoconto.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF (*.pdf)"))
    dialog.save_pdf()
    assert _is_pdf(target)
    assert main_window.ReportDialog is ReportDialog


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
    window.settings.setValue("print/orientation", "portrait")
    window.print_current()
    assert _is_pdf(printed)

    target = tmp_path / "equity"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF (*.pdf)"))
    window.export_current_pdf()
    assert _is_pdf(tmp_path / "equity.pdf")
    assert "PDF salvato" in window.statusBar().currentMessage()
    window.settings.setValue("print/orientation", "landscape")
