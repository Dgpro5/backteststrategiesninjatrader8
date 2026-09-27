"""Finestra "Resoconto completo": scelta di orientamento, sezioni e singole pagine da stampare."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .print_templates import build_full_report
from .report import LANDSCAPE, PORTRAIT, Document, DocumentLayout, PrintJob, layout_document

TRADES_DEFAULT_MAX_PAGES = 10  # la lista trade più lunga di così parte deselezionata


class ReportDialog(QDialog):
    """Mostra tutte le pagine del resoconto raggruppate per sezione, ognuna con la sua spunta."""

    def __init__(self, window, doc: Document | None = None):
        super().__init__(window)
        self.main = window
        self.settings = QSettings()
        self.setWindowTitle("Resoconto completo")
        self.resize(760, 720)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.doc = doc if doc is not None else build_full_report(window)
        finally:
            QApplication.restoreOverrideCursor()
        self.layouts: dict[int, DocumentLayout] = {}
        self._excluded = set(str(self.settings.value("report/excluded", "")).split(",")) - {""}
        self._first_build = not self.settings.contains("report/excluded")

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Il resoconto raccoglie tutte le schede in un unico documento. Togli la spunta alle sezioni o alle "
            "singole pagine che non vuoi stampare; la numerazione delle pagine segue quelle scelte."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        title = QLabel("Foglio:")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        self.landscape = QRadioButton("Orizzontale")
        self.portrait = QRadioButton("Verticale")
        group = QButtonGroup(self)
        group.addButton(self.landscape)
        group.addButton(self.portrait)
        if str(self.settings.value("print/orientation", "landscape")) == "portrait":
            self.portrait.setChecked(True)
        else:
            self.landscape.setChecked(True)
        row.addWidget(self.landscape)
        row.addWidget(self.portrait)
        row.addStretch(1)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Sezioni e pagine", "Pagine"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        layout.addWidget(self.tree, 1)

        tools = QHBoxLayout()
        all_btn = QPushButton("Seleziona tutto")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Deseleziona tutto")
        none_btn.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(all_btn)
        tools.addWidget(none_btn)
        tools.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("sectionTitle")
        tools.addWidget(self.count)
        layout.addLayout(tools)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.print_btn = QPushButton("Anteprima e stampa…")
        self.print_btn.setObjectName("primary")
        self.print_btn.clicked.connect(self.print_selected)
        self.pdf_btn = QPushButton("Salva PDF…")
        self.pdf_btn.clicked.connect(self.save_pdf)
        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.print_btn)
        buttons.addWidget(self.pdf_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.landscape.toggled.connect(self._orientation_changed)
        self._rebuild()

    # ------------------------------------------------------------------ pagine
    @property
    def orientation(self):
        return PORTRAIT if self.portrait.isChecked() else LANDSCAPE

    def current_layout(self) -> DocumentLayout:
        key = int(self.orientation == PORTRAIT)
        if key not in self.layouts:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.layouts[key] = layout_document(self.doc, self.orientation)
            finally:
                QApplication.restoreOverrideCursor()
        return self.layouts[key]

    def _orientation_changed(self) -> None:
        self.settings.setValue("print/orientation", "portrait" if self.portrait.isChecked() else "landscape")
        self._rebuild()

    def _rebuild(self) -> None:
        """Ricrea l'elenco per l'orientamento scelto, mantenendo le sezioni escluse."""
        if self.tree.topLevelItemCount():
            self._excluded = {self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())
                              if self.tree.topLevelItem(i).checkState(0) == Qt.Unchecked}
        layout = self.current_layout()
        self.tree.blockSignals(True)
        self.tree.clear()
        for si, section in enumerate(self.doc.sections):
            pages = layout.section_pages[si]
            item = QTreeWidgetItem([section.title, f"{len(pages)}"])
            item.setData(0, Qt.UserRole, section.key)
            item.setToolTip(0, section.subtitle)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            excluded = section.key in self._excluded
            if self._first_build and section.key == "trades" and len(pages) > TRADES_DEFAULT_MAX_PAGES:
                excluded = True
            for g in pages:
                child = QTreeWidgetItem([f"Pagina {g + 1} · {layout.labels[g]}", ""])
                child.setData(0, Qt.UserRole, g)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if excluded else Qt.Checked)
                item.addChild(child)
            item.setCheckState(0, Qt.Unchecked if excluded else Qt.Checked)
            self.tree.addTopLevelItem(item)
        self._first_build = False
        self.tree.blockSignals(False)
        self._update_count()

    def selected_pages(self) -> list[int]:
        pages = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            for k in range(item.childCount()):
                child = item.child(k)
                if child.checkState(0) == Qt.Checked:
                    pages.append(int(child.data(0, Qt.UserRole)))
        return sorted(pages)

    def set_section_checked(self, key: str, checked: bool) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) == key:
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def set_page_checked(self, page: int, checked: bool) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            for k in range(item.childCount()):
                child = item.child(k)
                if child.data(0, Qt.UserRole) == page:
                    child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def _set_all(self, checked: bool) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def _item_changed(self, *_args) -> None:
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.selected_pages())
        total = len(self.current_layout())
        self.count.setText(f"Pagine da stampare: {n} di {total}")
        self.print_btn.setEnabled(n > 0)
        self.pdf_btn.setEnabled(n > 0)

    def _remember(self) -> None:
        excluded = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).checkState(0) == Qt.Unchecked]
        self.settings.setValue("report/excluded", ",".join(excluded))

    # ------------------------------------------------------------------ azioni
    def job(self) -> PrintJob:
        return PrintJob(self.doc, self.orientation, self.selected_pages(), layout=self.current_layout())

    def print_selected(self) -> None:
        self._remember()
        self.main.preview_job(self.job())

    def save_pdf(self) -> None:
        self._remember()
        if self.main.save_job_pdf(self.job()):
            self.accept()
