"""Finestra principale: gestione dei file caricati e delle schede di analisi."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtPrintSupport import QPrintPreviewDialog
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..models import LoadedStrategy
from ..parser import ParseError, parse_file
from ..portfolio import Portfolio
from . import theme
from .analysis_tab import AnalysisTab
from .equity_tab import EquityTab
from .montecarlo_tab import MonteCarloTab
from .print_templates import build_report, report_filename
from .report import ReportContent, export_pdf, make_printer, print_to
from .stats_tab import StatsTab
from .trades_tab import TradesTab
from .widgets import restyle_all


def _scrollable(widget: QWidget) -> QScrollArea:
    """Su schermi piccoli la scheda scorre invece di forzare una finestra più grande dello schermo."""
    widget.setObjectName("page")
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


class MainWindow(QMainWindow):
    def __init__(self, theme_preference: str = "system", persist_theme: bool = True):
        """``theme_preference``: "light", "dark" o "system" (già applicata da chi crea la finestra)."""
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1480, 940)
        self.setAcceptDrops(True)
        self.settings = QSettings()
        self._theme_pref = theme_preference if theme_preference in theme.MODE_LABELS else "system"
        self._persist_theme = persist_theme
        self.strategies: list[LoadedStrategy] = []
        self.portfolio: Portfolio | None = None
        self._next_uid = 1
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh)

        self._build_menu()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_empty_page())
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.stats_tab = StatsTab()
        self.analysis_tab = AnalysisTab()
        self.equity_tab = EquityTab()
        self.mc_tab = MonteCarloTab()
        self.trades_tab = TradesTab()
        for widget, title in (
            (self.stats_tab, "Statistiche"),
            (self.equity_tab, "Equity curve"),
            (self.mc_tab, "Monte Carlo"),
            (self.analysis_tab, "Analisi grafica"),
            (self.trades_tab, "Lista trade"),
        ):
            self.tabs.addTab(_scrollable(widget), title)
        self.tabs.setCornerWidget(self._build_print_buttons(), Qt.TopRightCorner)
        self.stack.addWidget(self.tabs)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([270, 1210])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Pronto. Carica uno o più file CSV esportati da NinjaTrader 8.")
        self._sync_theme_controls()
        try:  # segue il tema di Windows se la preferenza è "Come il sistema" (Qt >= 6.5)
            QApplication.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except AttributeError:
            pass

    # ------------------------------------------------------------------ costruzione UI
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("Apri CSV…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(open_action)
        clear_action = QAction("Rimuovi tutti", self)
        clear_action.triggered.connect(self.clear_all)
        file_menu.addAction(clear_action)
        file_menu.addSeparator()
        print_action = QAction("Stampa pagina corrente…", self)
        print_action.setShortcut(QKeySequence.Print)
        print_action.setToolTip("Anteprima e stampa della scheda visibile")
        print_action.triggered.connect(self.print_current)
        file_menu.addAction(print_action)
        pdf_action = QAction("Esporta pagina corrente in PDF…", self)
        pdf_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        pdf_action.triggered.connect(self.export_current_pdf)
        file_menu.addAction(pdf_action)
        file_menu.addSeparator()
        quit_action = QAction("Esci", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&Visualizza")
        theme_menu = view_menu.addMenu("Tema")
        self._theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for key, label in theme.MODE_LABELS.items():
            action = QAction(label, self, checkable=True)
            action.triggered.connect(lambda checked=False, k=key: self.set_theme_preference(k))
            group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[key] = action
        toggle = QAction("Alterna chiaro / scuro", self)
        toggle.setShortcut(QKeySequence("Ctrl+T"))
        toggle.triggered.connect(lambda: self.set_theme_preference("light" if theme.is_dark() else "dark"))
        view_menu.addAction(toggle)

        help_menu = self.menuBar().addMenu("&Aiuto")
        about = QAction("Informazioni", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _build_print_buttons(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 4, 2)
        row.setSpacing(6)
        print_btn = QPushButton("Stampa…")
        print_btn.setToolTip("Anteprima e stampa della scheda visibile (Ctrl+P)")
        print_btn.clicked.connect(self.print_current)
        pdf_btn = QPushButton("PDF…")
        pdf_btn.setToolTip("Salva la scheda visibile come PDF (Ctrl+Maiusc+P)")
        pdf_btn.clicked.connect(self.export_current_pdf)
        row.addWidget(print_btn)
        row.addWidget(pdf_btn)
        return box

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("page")
        side.setMinimumWidth(250)
        side.setMaximumWidth(360)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 12, 6, 12)
        layout.setSpacing(8)

        title = QLabel("Strategie caricate")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self.open_btn = QPushButton("Aggiungi file CSV…")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self.open_dialog)
        layout.addWidget(self.open_btn)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.list.itemChanged.connect(self._item_changed)
        layout.addWidget(self.list, 1)

        hint = QLabel("Spunta per includere nel portafoglio · doppio clic per rinominare · trascina qui i file")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        remove = QPushButton("Rimuovi selezionate")
        remove.clicked.connect(self.remove_selected)
        clear = QPushButton("Svuota")
        clear.clicked.connect(self.clear_all)
        row.addWidget(remove)
        row.addWidget(clear)
        layout.addLayout(row)

        line = QFrame()
        line.setObjectName("separator")
        line.setFixedHeight(1)
        layout.addWidget(line)

        cap_label = QLabel("Capitale iniziale")
        cap_label.setObjectName("sectionTitle")
        layout.addWidget(cap_label)
        self.capital = QDoubleSpinBox()
        self.capital.setRange(0, 1_000_000_000)
        self.capital.setDecimals(0)
        self.capital.setSingleStep(1000)
        self.capital.setPrefix("$ ")
        self.capital.setGroupSeparatorShown(True)
        self.capital.setValue(float(self.settings.value("capital", 10000)))
        self.capital.setToolTip("Usato per drawdown %, rendimento % e CAGR. 0 = calcoli percentuali disattivati")
        self.capital.valueChanged.connect(self._capital_changed)
        layout.addWidget(self.capital)

        cap_hint = QLabel("Serve per drawdown %, rendimento e CAGR.")
        cap_hint.setObjectName("hint")
        cap_hint.setWordWrap(True)
        layout.addWidget(cap_hint)

        line2 = QFrame()
        line2.setObjectName("separator")
        line2.setFixedHeight(1)
        layout.addWidget(line2)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Tema")
        theme_label.setObjectName("sectionTitle")
        theme_row.addWidget(theme_label)
        self.theme_box = QComboBox()
        for key, label in theme.MODE_LABELS.items():
            self.theme_box.addItem(label, key)
        self.theme_box.setToolTip("Colori dell'interfaccia: chiaro, scuro o come Windows (Ctrl+T per alternare)")
        self.theme_box.currentIndexChanged.connect(
            lambda: self.set_theme_preference(self.theme_box.currentData())
        )
        theme_row.addWidget(self.theme_box, 1)
        layout.addLayout(theme_row)
        return side

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        box = QFrame()
        box.setObjectName("card")
        box.setMaximumWidth(620)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(36, 32, 36, 32)
        inner.setSpacing(12)
        title = QLabel("Analizza i tuoi backtest NinjaTrader 8")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignCenter)
        text = QLabel(
            "Trascina qui uno o più file CSV esportati dalla griglia <b>Trades</b> dello Strategy Analyzer "
            "(tasto destro → Export), oppure usa il pulsante qui sotto.<br><br>"
            "Con 2 o più file i trade vengono uniti in ordine cronologico e ottieni la curva del "
            "<b>portafoglio combinato</b>, il suo max drawdown e il Monte Carlo sull'insieme."
        )
        text.setObjectName("emptyText")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)
        button = QPushButton("Apri file CSV…")
        button.setObjectName("primary")
        button.clicked.connect(self.open_dialog)
        inner.addWidget(title)
        inner.addWidget(text)
        inner.addWidget(button, 0, Qt.AlignCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(box, 3)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    # ------------------------------------------------------------------ caricamento
    def open_dialog(self) -> None:
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Seleziona export CSV di NinjaTrader", start, "File CSV (*.csv);;Tutti i file (*.*)"
        )
        if paths:
            self.settings.setValue("last_dir", os.path.dirname(paths[0]))
            self.load_files(paths)

    def _unique_name(self, base: str, instrument: str, path: str) -> str:
        used = {s.name for s in self.strategies}
        if base not in used:
            return base
        candidates = []
        if instrument:
            candidates.append(f"{base} ({instrument})")
        candidates.append(f"{base} [{os.path.splitext(os.path.basename(path))[0]}]")
        for c in candidates:
            if c not in used:
                return c
        i = 2
        while f"{base} #{i}" in used:
            i += 1
        return f"{base} #{i}"

    def _free_color(self) -> int:
        used = {s.color_index for s in self.strategies}
        i = 0
        while i in used:
            i += 1
        return i

    def load_files(self, paths: list[str], interactive: bool = True) -> list[str]:
        errors, loaded = [], 0
        known = {os.path.normcase(os.path.abspath(s.source_path)) for s in self.strategies}
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key in known:
                errors.append(f"{os.path.basename(path)}: già caricato")
                continue
            try:
                parsed = parse_file(path)
            except ParseError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:  # file inatteso: non far chiudere l'app
                errors.append(f"{os.path.basename(path)}: errore imprevisto ({exc})")
                continue
            strategy = LoadedStrategy(
                uid=self._next_uid,
                name=self._unique_name(parsed.strategy_name, parsed.instrument, path),
                source_path=path,
                trades=parsed.trades,
                color_index=self._free_color(),
                csv_strategy=parsed.strategy_name,
                instrument=parsed.instrument,
                warnings=parsed.warnings,
            )
            self._next_uid += 1
            self.strategies.append(strategy)
            known.add(key)
            loaded += 1
            for w in parsed.warnings:
                errors.append(f"{os.path.basename(path)}: {w}")
        self._rebuild_list()
        if loaded:
            self.refresh()
            self.statusBar().showMessage(f"Caricati {loaded} file · {len(self.strategies)} strategie totali", 8000)
        if errors and interactive:
            QMessageBox.warning(self, "Caricamento file", "\n".join(errors))
        return errors

    def _rebuild_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.strategies:
            item = QListWidgetItem(theme.swatch_icon(theme.strategy_color(s.color_index), theme.strategy_line_style(s.color_index)), s.name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
            item.setCheckState(Qt.Checked if s.included else Qt.Unchecked)
            item.setData(Qt.UserRole, s.uid)
            ts = s.trades
            period = f"{theme.fmt_date(ts.entry_time.min())} → {theme.fmt_date(ts.exit_time.max())}" if len(ts) else ""
            item.setToolTip(
                f"{s.source_path}\nStrategia: {s.csv_strategy} · Strumento: {s.instrument or 'n/d'}\n"
                f"{len(ts)} trade · {period}\nProfitto netto: {theme.fmt_money(float(ts.profit.sum()))}"
            )
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _item_changed(self, item: QListWidgetItem) -> None:
        uid = item.data(Qt.UserRole)
        strategy = next((s for s in self.strategies if s.uid == uid), None)
        if strategy is None:
            return
        name = item.text().strip() or strategy.name
        included = item.checkState() == Qt.Checked
        if name != item.text():
            self.list.blockSignals(True)
            item.setText(name)
            self.list.blockSignals(False)
        if name != strategy.name or included != strategy.included:
            strategy.name = name
            strategy.included = included
            self._refresh_timer.start()

    def remove_selected(self) -> None:
        uids = {item.data(Qt.UserRole) for item in self.list.selectedItems()}
        if not uids:
            return
        self.strategies = [s for s in self.strategies if s.uid not in uids]
        self._rebuild_list()
        self.refresh()

    def clear_all(self) -> None:
        self.strategies = []
        self._rebuild_list()
        self.refresh()

    def _capital_changed(self, value: float) -> None:
        self.settings.setValue("capital", value)
        self._refresh_timer.start()

    # ------------------------------------------------------------------ ricalcolo
    def refresh(self) -> None:
        self.portfolio = Portfolio(self.strategies, self.capital.value())
        if self.portfolio.empty:
            self.stack.setCurrentIndex(0)
            if self.strategies:
                self.statusBar().showMessage("Nessuna strategia inclusa: spunta almeno una strategia nella lista.")
            return
        self.stack.setCurrentIndex(1)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for tab in (self.stats_tab, self.equity_tab, self.analysis_tab, self.trades_tab):
                tab.update_portfolio(self.portfolio)
        finally:
            QApplication.restoreOverrideCursor()
        self.mc_tab.update_portfolio(self.portfolio)

    # ------------------------------------------------------------------ stampa
    def current_report(self) -> ReportContent | None:
        """Report della scheda visibile, pronto per stampante o PDF."""
        if self.portfolio is None or self.portfolio.empty:
            QMessageBox.information(self, "Stampa", "Carica almeno un file CSV prima di stampare.")
            return None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return build_report(self)
        finally:
            QApplication.restoreOverrideCursor()

    def print_current(self) -> None:
        content = self.current_report()
        if content is None:
            return
        printer = make_printer(content.title)
        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle(f"Anteprima di stampa · {content.title}")
        preview.paintRequested.connect(lambda target: print_to(content, target))
        preview.resize(1200, 860)
        preview.exec()

    def export_current_pdf(self) -> None:
        content = self.current_report()
        if content is None:
            return
        folder = str(self.settings.value("pdf_dir", self.settings.value("last_dir", os.path.expanduser("~"))))
        path, _ = QFileDialog.getSaveFileName(
            self, "Esporta in PDF", os.path.join(folder, report_filename(content)), "PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pages = export_pdf(content, path)
        finally:
            QApplication.restoreOverrideCursor()
        if not pages or not os.path.exists(path):
            QMessageBox.warning(self, "Esporta in PDF", f"Impossibile salvare il file:\n{path}")
            return
        self.settings.setValue("pdf_dir", os.path.dirname(path))
        self.statusBar().showMessage(f"PDF salvato ({pages} pagine): {path}", 10000)

    # ------------------------------------------------------------------ tema
    def set_theme_preference(self, preference: str, persist: bool = True) -> None:
        """Cambia il tema dell'interfaccia: "light", "dark" o "system"."""
        if preference not in theme.MODE_LABELS:
            return
        self._theme_pref = preference
        if persist and self._persist_theme:
            self.settings.setValue("theme", preference)
        self._sync_theme_controls()
        self._apply_theme()

    def _sync_theme_controls(self) -> None:
        self.theme_box.blockSignals(True)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(self._theme_pref)))
        self.theme_box.blockSignals(False)
        for key, action in self._theme_actions.items():
            action.setChecked(key == self._theme_pref)

    def _system_scheme_changed(self, *_args) -> None:
        if self._theme_pref == "system":
            self._apply_theme()

    def _apply_theme(self) -> None:
        mode = theme.resolve_mode(self._theme_pref)
        theme.set_mode(mode)
        theme.apply_theme(QApplication.instance())
        restyle_all()
        self._rebuild_list()
        # Ridisegna con i nuovi colori senza ricalcolare (il Monte Carlo mantiene le simulazioni).
        if self.portfolio is not None and not self.portfolio.empty:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for tab in (self.stats_tab, self.equity_tab, self.analysis_tab, self.trades_tab):
                    tab.update_portfolio(self.portfolio)
                self.mc_tab.redraw()
            finally:
                QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Tema: {theme.MODE_LABELS[self._theme_pref]}", 4000)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        files = []
        for p in paths:
            if os.path.isdir(p):
                files.extend(os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(".csv"))
            else:
                files.append(p)
        if files:
            self.load_files(files)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            APP_NAME,
            f"<b>{APP_NAME} {__version__}</b><br><br>"
            "Analisi di export trade di NinjaTrader 8: statistiche, equity curve per strategia e combinate, "
            "drawdown e simulazione Monte Carlo (shuffle / bootstrap).<br><br>"
            "Ctrl+P stampa la scheda visibile, Ctrl+Maiusc+P la salva in PDF, Ctrl+T alterna il tema.",
        )
