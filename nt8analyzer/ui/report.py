"""Impaginazione dei report di stampa: A4 verticale o orizzontale, stile dell'app, carta chiara.

Un documento (``Document``) è formato da una o più sezioni (``ReportContent``), ognuna delle quali
inizia su una pagina nuova. Una sezione è una sequenza di blocchi: indicatori, grafici, tabelle,
testo. Il layout viene calcolato una sola volta in punti tipografici (1/72 di pollice), con misure
del testo indipendenti dal dispositivo. Poi si disegna su stampante, PDF o immagine, stampando
tutte le pagine o solo quelle scelte, e numerandole "Pagina X di Y".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintDevice,
    QPen,
    QTextLayout,
    QTextOption,
)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, __version__
from . import theme

# La carta è sempre chiara: si usa la palette chiara qualunque sia il tema dell'app.
P = theme.LIGHT
PAGE_MARGIN_MM = 10.0
LANDSCAPE = QPageLayout.Landscape
PORTRAIT = QPageLayout.Portrait
ORIENTATION_LABELS = {"landscape": "Orizzontale", "portrait": "Verticale"}

HEADER_H = 58.0
FOOTER_H = 16.0
GAP = 10.0
CELL_PAD = 4.0
SWATCH_W = 15.0
CHART_TEXT_SCALE = 0.8  # i grafici vengono ridisegnati in modo che il testo sia all'80% della dimensione a schermo
CHART_DPI = 220.0
LEGEND_LINE = 12.0


def orientation_from_key(key: str) -> QPageLayout.Orientation:
    return PORTRAIT if key == "portrait" else LANDSCAPE


def orientation_key(orientation: QPageLayout.Orientation) -> str:
    return "portrait" if orientation == PORTRAIT else "landscape"


# --------------------------------------------------------------------------- contenuti


@dataclass
class Kpi:
    label: str
    value: str
    tone: int = 0
    sub: str = ""


@dataclass
class KpiBlock:
    items: list[Kpi]


@dataclass
class InfoBar:
    """Riga di riepilogo (etichetta, valore) sotto l'intestazione; va a capo se non entra."""

    items: list[tuple[str, str]]


@dataclass
class SectionTitle:
    text: str


@dataclass
class TextBlock:
    text: str
    size: float = 7.5
    color: str = P["INK_2"]


@dataclass
class ChartBlock:
    """Grafico: un'immagine pronta oppure ``render(larghezza, altezza, dpr)`` che lo ridisegna
    alla dimensione giusta per la pagina (verticale o orizzontale)."""

    image: QImage | None = None
    title: str = ""
    render: Callable[[int, int, float], QImage] | None = None
    aspect: float = 0.4  # altezza / larghezza sul foglio orizzontale
    aspect_portrait: float | None = None  # sul foglio verticale (None = come ``aspect``)
    label: str = ""  # nome usato nell'elenco delle pagine
    legend: list[tuple[str, str, Qt.PenStyle]] = field(default_factory=list)  # legenda sotto il grafico


@dataclass
class ChartGrid:
    charts: list[ChartBlock]
    columns: int = 2


@dataclass
class LegendBlock:
    """Legenda compatta (campione di colore + nome) su più colonne."""

    items: list[tuple[str, str, Qt.PenStyle]]  # (nome, colore, stile linea)
    title: str = ""


@dataclass
class Cell:
    text: str
    color: str | None = None
    bold: bool = False
    bg: str | None = None
    swatch: tuple[str, Qt.PenStyle] | None = None


@dataclass
class TableColumn:
    header: str
    weight: float = 1.0
    align: str = "right"
    min_width: float = 0.0  # minimo oltre alla larghezza del contenuto
    swatch: tuple[str, Qt.PenStyle] | None = None
    color: str | None = None  # colore del testo dell'intestazione
    priority: int = 0  # > 0: colonna omessa (prima le priorità più alte) se il foglio è troppo stretto
    max_share: float = 0.45  # larghezza massima rispetto al foglio (il testo più lungo viene troncato)


@dataclass
class TableRow:
    cells: list[Cell]
    kind: str = "normal"  # normal | section | highlight


@dataclass
class TableBlock:
    columns: list[TableColumn]
    rows: list[TableRow]
    title: str = ""
    frozen: int = 1  # prime colonne ripetute in ogni gruppo quando la tabella va divisa in larghezza
    font_size: float = 7.3
    repeat: tuple[int, ...] = ()  # altre colonne ripetute in fondo a ogni gruppo (es. portafoglio combinato)
    group_noun: str = "colonne"  # es. "strategie": "strategie 1–6 di 12"


@dataclass
class TocBlock:
    """Indice delle sezioni del documento con le pagine stampate."""

    title: str = "Contenuto"


@dataclass
class PageBreak:
    pass


@dataclass
class ReportContent:
    """Una sezione: inizia sempre su una pagina nuova, con la sua intestazione."""

    title: str
    subtitle: str = ""
    blocks: list = field(default_factory=list)
    meta: list[str] = field(default_factory=list)
    created: datetime = field(default_factory=datetime.now)
    key: str = ""


@dataclass
class Document:
    title: str
    sections: list[ReportContent]
    created: datetime = field(default_factory=datetime.now)


def as_document(content: "ReportContent | Document") -> Document:
    if isinstance(content, Document):
        return content
    return Document(content.title, [content], content.created)


def tone_color(tone: int) -> str:
    return P["POSITIVE_TEXT"] if tone > 0 else P["NEGATIVE_TEXT"] if tone < 0 else P["INK"]


# --------------------------------------------------------------------------- misure del testo

_REF_DPI = 720.0
_REF_DEVICE: QImage | None = None


def _ref_device() -> QImage:
    """Dispositivo di riferimento ad alta risoluzione: le misure non dipendono dalla stampante."""
    global _REF_DEVICE
    if _REF_DEVICE is None:
        image = QImage(8, 8, QImage.Format_ARGB32)
        dpm = int(round(_REF_DPI / 0.0254))
        image.setDotsPerMeterX(dpm)
        image.setDotsPerMeterY(dpm)
        _REF_DEVICE = image
    return _REF_DEVICE


def _base_font(size_pt: float, bold: bool, italic: bool) -> QFont:
    font = QFont(QApplication.font().family())
    font.setPointSizeF(size_pt)
    font.setBold(bold)
    font.setItalic(italic)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


class _Metrics:
    """Misure in punti di un carattere di ``size`` punti.

    Le larghezze includono un piccolo margine: a bassa risoluzione (anteprime, immagini) il testo
    disegnato può risultare un po' più largo delle misure tipografiche esatte."""

    SAFETY = 1.04

    _cache: dict[tuple, "_Metrics"] = {}

    def __init__(self, size: float, bold: bool = False, italic: bool = False):
        self.font = _base_font(size, bold, italic)
        self.fm = QFontMetricsF(self.font, _ref_device())
        self.k = 72.0 / (_ref_device().logicalDpiX() or _REF_DPI)

    @classmethod
    def get(cls, size: float, bold: bool = False, italic: bool = False) -> "_Metrics":
        key = (round(size, 3), bold, italic)
        if key not in cls._cache:
            cls._cache[key] = cls(size, bold, italic)
        return cls._cache[key]

    def width(self, text: str) -> float:
        return self.fm.horizontalAdvance(text) * self.k * self.SAFETY

    def line(self) -> float:
        return self.fm.lineSpacing() * self.k

    def elide(self, text: str, width: float, tolerance: float = 0.0) -> str:
        if self.width(text) <= width + tolerance:
            return text
        return self.fm.elidedText(text, Qt.ElideRight, width / self.k / self.SAFETY)

    def wrapped_height(self, text: str, width: float) -> float:
        """Altezza del testo a capo: misurato su una larghezza un po' più stretta, come margine di sicurezza."""
        rect = self.fm.boundingRect(QRectF(0, 0, width / self.k / self.SAFETY, 1e7), Qt.TextWordWrap, text)
        return rect.height() * self.k

    def line_count(self, text: str, width: float, anywhere: bool = True) -> int:
        """Righe necessarie per ``text`` largo al massimo ``width`` punti."""
        layout = QTextLayout(text, self.font, _ref_device())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere if anywhere else QTextOption.WordWrap)
        layout.setTextOption(option)
        layout.beginLayout()
        count = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, width / self.k))
            count += 1
        layout.endLayout()
        return max(1, count)


def _header_text(text: str) -> str:
    """Permette di andare a capo dopo "_" nei nomi delle strategie."""
    return text.replace("_", "_​")


def _header_pieces(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=_)|\s+", text) if p]


# --------------------------------------------------------------------------- layout di una sezione


class _Layout:
    """Distribuisce i blocchi di una sezione sulle pagine (unità: punti)."""

    def __init__(self, content: ReportContent, width: float, height: float, document: "DocumentLayout | None" = None,
                 section_index: int = 0):
        self.content = content
        self.document = document
        self.section_index = section_index
        self.W = width
        self.H = height
        self.portrait = height > width
        self.top = HEADER_H + 8
        self.bottom = height - FOOTER_H - 6
        self.pages: list[list[Callable[[QPainter], None]]] = []
        self.page_starts: list[list[str]] = []
        self._paint_dpi = 72.0
        self._fonts: dict[tuple, QFont] = {}
        self._new_page()
        blocks = list(content.blocks)
        for i, block in enumerate(blocks):
            self._next = blocks[i + 1] if i + 1 < len(blocks) else None
            self._place(block)

    # ---- caratteri: misure indipendenti dal dispositivo, font creati per chi disegna
    def set_paint_dpi(self, dpi: float) -> None:
        if dpi != self._paint_dpi:
            self._paint_dpi = dpi
            self._fonts.clear()

    def font(self, size: float, bold: bool = False, italic: bool = False) -> QFont:
        """Carattere per il dispositivo su cui si sta disegnando (dimensione compensata con la scala punti→pixel)."""
        key = (size, bold, italic)
        if key not in self._fonts:
            self._fonts[key] = _base_font(size * 72.0 / self._paint_dpi, bold, italic)
        return self._fonts[key]

    @staticmethod
    def m(size: float, bold: bool = False, italic: bool = False) -> _Metrics:
        return _Metrics.get(size, bold, italic)

    # ---- pagine
    def _new_page(self) -> None:
        self.pages.append([])
        self.page_starts.append([])
        self.y = self.top

    def _ensure(self, height: float) -> None:
        if self.y + height > self.bottom and self.y > self.top + 0.5:
            self._new_page()

    def _add(self, op: Callable[[QPainter], None]) -> None:
        self.pages[-1].append(op)

    def _mark(self, label: str) -> None:
        if label:
            self.page_starts[-1].append(label)

    def page_label(self, index: int) -> str:
        starts = self.page_starts[index]
        if starts:
            return " · ".join(dict.fromkeys(starts))
        previous = next((s[-1] for s in reversed(self.page_starts[:index]) if s), "")
        return f"{previous} (continua)" if previous else self.content.title

    # ---- blocchi
    def _place(self, block) -> None:
        if isinstance(block, PageBreak):
            if self.y > self.top + 0.5:
                self._new_page()
        elif isinstance(block, SectionTitle):
            self._section(block)
        elif isinstance(block, InfoBar):
            self._info_bar(block)
        elif isinstance(block, KpiBlock):
            self._kpis(block)
        elif isinstance(block, ChartBlock):
            self._chart_row([block], 1)
        elif isinstance(block, ChartGrid):
            for i in range(0, len(block.charts), block.columns):
                self._chart_row(block.charts[i : i + block.columns], block.columns)
        elif isinstance(block, LegendBlock):
            self._legend(block)
        elif isinstance(block, TableBlock):
            self._table(block)
        elif isinstance(block, TextBlock):
            self._text(block)
        elif isinstance(block, TocBlock):
            self._toc(block)

    def _min_height(self, block) -> float:
        """Spazio minimo che il blocco seguente occupa sulla stessa pagina di un titolo."""
        if isinstance(block, TableBlock):
            row_h = block.font_size * 2.05
            whole = (len(block.rows) + 2) * row_h + (15 if block.title else 0)
            small = whole <= (self.bottom - self.top) * 0.35
            return whole if small else (15 if block.title else 0) + row_h * 5
        if isinstance(block, (ChartBlock, ChartGrid)):
            chart = block if isinstance(block, ChartBlock) else block.charts[0]
            columns = 1 if isinstance(block, ChartBlock) else block.columns
            return self.W / columns * self._chart_aspect(chart) * 0.75
        if isinstance(block, KpiBlock):
            return 56.0
        return 40.0

    def _section(self, block: SectionTitle) -> None:
        # il titolo resta sulla stessa pagina dell'inizio del contenuto che segue
        self._ensure(20 + (self._min_height(self._next) if self._next is not None else 40.0))
        y = self.y + 2
        self._mark(block.text)

        def op(p: QPainter, y=y, text=block.text):
            p.fillRect(QRectF(0, y + 1.5, 3, 11), QColor(P["ACCENT"]))
            p.setFont(self.font(9.5, bold=True))
            p.setPen(QColor(P["INK"]))
            p.drawText(QRectF(9, y, self.W - 9, 14), Qt.AlignLeft | Qt.AlignVCenter, text)

        self._add(op)
        self.y += 20

    def _info_bar(self, block: InfoBar) -> None:
        items = block.items or [("", "")]
        lm, vm = self.m(6.3, bold=True), self.m(8.6, bold=True)
        natural = [max(lm.width(label.upper()), vm.width(value)) + 18 for label, value in items]
        # Righe: gli elementi vanno a capo quando non entrano nella larghezza della pagina.
        rows: list[list[int]] = [[]]
        used = 0.0
        for i, w in enumerate(natural):
            if rows[-1] and used + w > self.W:
                rows.append([])
                used = 0.0
            rows[-1].append(i)
            used += w
        cells = []  # (riga, x, larghezza, indice)
        for r, row in enumerate(rows):
            total = sum(natural[i] for i in row)
            free = self.W - total
            x = 0.0
            for i in row:
                w = natural[i] + free / len(row) if free >= 0 else natural[i] * self.W / total
                cells.append((r, x, w, i))
                x += w
        row_h = 30.0
        h = row_h * len(rows)
        self._ensure(h + GAP)
        y = self.y

        def op(p: QPainter, y=y):
            _card(p, QRectF(0, y, self.W, h), P["ALT_BASE"])
            for r in range(1, len(rows)):
                p.setPen(QPen(QColor(P["GRID"]), 0.6))
                p.drawLine(QPointF(6, y + r * row_h), QPointF(self.W - 6, y + r * row_h))
            for r, x, w, i in cells:
                label, value = items[i]
                ry = y + r * row_h
                if x > 0:
                    p.setPen(QPen(QColor(P["GRID"]), 0.6))
                    p.drawLine(QPointF(x, ry + 6), QPointF(x, ry + row_h - 6))
                p.setFont(self.font(6.3, bold=True))
                p.setPen(QColor(P["MUTED"]))
                p.drawText(QRectF(x + 9, ry + 4, w - 14, 10), Qt.AlignLeft | Qt.AlignVCenter,
                           lm.elide(label.upper(), w - 14, 1.0))
                p.setFont(self.font(8.6, bold=True))
                p.setPen(QColor(P["INK"]))
                p.drawText(QRectF(x + 9, ry + 14, w - 12, 13), Qt.AlignLeft | Qt.AlignVCenter,
                           vm.elide(value, w - 14, 1.0))

        self._add(op)
        self.y += h + GAP

    def _kpis(self, block: KpiBlock) -> None:
        items = block.items
        if not items:
            return
        gap, h = 7.0, 46.0
        max_per_row = max(1, int((self.W + gap) // (95 + gap)))
        n_rows = math.ceil(len(items) / max_per_row)
        per_row = math.ceil(len(items) / n_rows)  # righe bilanciate (es. 4 + 3 invece di 5 + 2)
        label_m, small_m, value_m, sub_m = self.m(6.8), self.m(6.0), self.m(12.5, bold=True), self.m(6.3)
        for start in range(0, len(items), per_row):
            row = items[start : start + per_row]
            self._ensure(h + GAP)
            y = self.y
            card_w = (self.W - gap * (per_row - 1)) / per_row

            def op(p: QPainter, y=y, row=row, card_w=card_w):
                for i, kpi in enumerate(row):
                    x = i * (card_w + gap)
                    _card(p, QRectF(x, y, card_w, h), P["SURFACE"])
                    inner = card_w - 16
                    small = label_m.width(kpi.label) > inner
                    p.setFont(self.font(6.0 if small else 6.8))
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(x + 8, y + 5, inner, 10), Qt.AlignLeft | Qt.AlignVCenter,
                               (small_m if small else label_m).elide(kpi.label, inner))
                    p.setFont(self.font(12.5, bold=True))
                    p.setPen(QColor(tone_color(kpi.tone)))
                    p.drawText(QRectF(x + 8, y + 15, inner + 4, 17), Qt.AlignLeft | Qt.AlignVCenter,
                               value_m.elide(kpi.value, inner, 2.0))
                    if kpi.sub:
                        p.setFont(self.font(6.3))
                        p.setPen(QColor(P["MUTED"]))
                        p.drawText(QRectF(x + 8, y + 32, inner, 10), Qt.AlignLeft | Qt.AlignVCenter,
                                   sub_m.elide(kpi.sub, inner))

            self._add(op)
            self.y += h + GAP

    def _chart_aspect(self, c: ChartBlock) -> float:
        if c.render is None and c.image is not None:
            return c.image.height() / max(1, c.image.width())
        if self.portrait and c.aspect_portrait:
            return c.aspect_portrait
        return c.aspect

    @staticmethod
    def _chart_image(c: ChartBlock, width_pt: float, aspect: float) -> QImage | None:
        if c.render is None:
            return c.image
        logical_w = max(200, round(width_pt / CHART_TEXT_SCALE))
        logical_h = max(80, round(logical_w * aspect))
        dpr = (width_pt / 72.0 * CHART_DPI) / logical_w
        return c.render(logical_w, logical_h, dpr)

    def _legend_grid(self, items, width: float) -> tuple[int, int, float]:
        """(colonne, righe, larghezza colonna) per una legenda larga ``width``."""
        m = self.m(7.0)
        col_w = min(width / 2, max(m.width(name) for name, _, _ in items) + SWATCH_W + 16)
        cols = max(1, int(width // col_w))
        return cols, math.ceil(len(items) / cols), width / cols

    def _paint_legend(self, p: QPainter, items, x0: float, y0: float, width: float) -> None:
        cols, n_rows, col_w = self._legend_grid(items, width)
        m = self.m(7.0)
        p.setFont(self.font(7.0))
        for k, (name, color, style) in enumerate(items):
            r, c = k % n_rows, k // n_rows
            x = x0 + c * col_w
            ly = y0 + r * LEGEND_LINE
            _swatch(p, QPointF(x, ly + LEGEND_LINE / 2), color, style)
            p.setPen(QColor(P["INK"]))
            p.drawText(QRectF(x + SWATCH_W, ly, col_w - SWATCH_W - 6, LEGEND_LINE), Qt.AlignLeft | Qt.AlignVCenter,
                       m.elide(name, col_w - SWATCH_W - 6, 1.0))

    def _chart_row(self, charts: list[ChartBlock], columns: int) -> None:
        gap, pad = 8.0, 5.0
        cell_w = (self.W - gap * (columns - 1)) / columns
        inner_w = cell_w - 2 * pad
        title_h = 13.0 if any(c.title for c in charts) else 0.0
        aspects = [self._chart_aspect(c) for c in charts]
        legend_h = max((self._legend_grid(c.legend, inner_w - 6)[1] * LEGEND_LINE + 8 if c.legend else 0.0)
                       for c in charts)
        height = max(inner_w * a + 2 * pad for a in aspects) + title_h + legend_h
        page_room = self.bottom - self.top
        if height > page_room:  # grafico più alto di una pagina: si riduce
            height = page_room
        if self.y + height > self.bottom:
            room = self.bottom - self.y
            # si accetta una riduzione fino al 75% prima di passare alla pagina seguente
            if room >= height * 0.75:
                height = room
            else:
                self._new_page()
        for c in charts:
            self._mark(c.label or c.title)
        images = [self._chart_image(c, inner_w, a) for c, a in zip(charts, aspects)]
        y = self.y

        def op(p: QPainter, y=y, charts=charts, images=images, height=height):
            for i, (c, img) in enumerate(zip(charts, images)):
                x = i * (cell_w + gap)
                top = y
                if title_h:
                    p.setFont(self.font(8, bold=True))
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(x + 2, y, cell_w, title_h - 2), Qt.AlignLeft | Qt.AlignVCenter, c.title)
                    top = y + title_h
                box = QRectF(x, top, cell_w, height - title_h)
                _card(p, box, "#ffffff")
                inner = box.adjusted(pad, pad, -pad, -pad - legend_h)
                if c.legend:
                    ly = inner.bottom() + 4
                    p.setPen(QPen(QColor(P["GRID"]), 0.6))
                    p.drawLine(QPointF(box.left() + 6, ly - 1), QPointF(box.right() - 6, ly - 1))
                    self._paint_legend(p, c.legend, inner.left() + 3, ly + 2, inner.width() - 6)
                if img is None or img.isNull():
                    continue
                ratio = img.width() / max(1, img.height())
                w, h = inner.width(), inner.width() / ratio
                if h > inner.height():
                    h = inner.height()
                    w = h * ratio
                p.drawImage(QRectF(inner.left() + (inner.width() - w) / 2, inner.top(), w, h), img)

        self._add(op)
        self.y += height + GAP

    def _legend(self, block: LegendBlock) -> None:
        if not block.items:
            return
        _cols, n_rows, _w = self._legend_grid(block.items, self.W - 16)
        title_h = 14.0 if block.title else 0.0
        h = title_h + n_rows * LEGEND_LINE + 8
        self._ensure(h + GAP)
        y = self.y

        def op(p: QPainter, y=y):
            if block.title:
                p.setFont(self.font(8, bold=True))
                p.setPen(QColor(P["INK_2"]))
                p.drawText(QRectF(2, y, self.W, title_h - 3), Qt.AlignLeft | Qt.AlignVCenter, block.title)
            box = QRectF(0, y + title_h, self.W, h - title_h)
            _card(p, box, "#ffffff")
            self._paint_legend(p, block.items, 8, box.top() + 4, self.W - 16)

        self._add(op)
        self.y += h + GAP

    def _text(self, block: TextBlock) -> None:
        m = self.m(block.size)
        h = m.wrapped_height(block.text, self.W) + 2
        self._ensure(h)
        y = self.y

        def op(p: QPainter, y=y):
            p.setFont(self.font(block.size))
            p.setPen(QColor(block.color))
            p.drawText(QRectF(0, y, self.W, h + m.line()), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, block.text)

        self._add(op)
        self.y += h + GAP * 0.6

    def _toc(self, block: TocBlock) -> None:
        doc = self.document
        if doc is None:
            return
        entries = [i for i in range(len(doc.doc.sections)) if i != self.section_index]
        row_h = 17.0
        title_h = 20.0
        h = title_h + row_h * len(entries) + 4
        self._ensure(h + GAP)
        self._mark(block.title)
        y = self.y
        title_m, sub_m = self.m(8.2, bold=True), self.m(7.0)

        def op(p: QPainter, y=y):
            p.fillRect(QRectF(0, y + 3.5, 3, 11), QColor(P["ACCENT"]))
            p.setFont(self.font(9.5, bold=True))
            p.setPen(QColor(P["INK"]))
            p.drawText(QRectF(9, y + 2, self.W - 9, 14), Qt.AlignLeft | Qt.AlignVCenter, block.title)
            ry = y + title_h
            for si in entries:
                pages = doc.printed_range(si)
                if pages is None:
                    continue
                section = doc.doc.sections[si]
                first, last = pages
                p.setFont(self.font(8.2, bold=True))
                p.setPen(QColor(P["INK"]))
                title_w = title_m.width(section.title)
                p.drawText(QRectF(2, ry, self.W * 0.5, row_h), Qt.AlignLeft | Qt.AlignVCenter, section.title)
                if section.subtitle:
                    p.setFont(self.font(7.0))
                    p.setPen(QColor(P["MUTED"]))
                    room = self.W * 0.8 - title_w - 12
                    p.drawText(QRectF(title_w + 10, ry, room, row_h), Qt.AlignLeft | Qt.AlignVCenter,
                               sub_m.elide(section.subtitle, room))
                p.setFont(self.font(8.2))
                p.setPen(QColor(P["INK_2"]))
                text = f"pag. {first}" if first == last else f"pag. {first}–{last}"
                p.drawText(QRectF(self.W * 0.8, ry, self.W * 0.2 - 2, row_h), Qt.AlignRight | Qt.AlignVCenter, text)
                p.setPen(QPen(QColor(P["GRID"]), 0.6))
                p.drawLine(QPointF(0, ry + row_h), QPointF(self.W, ry + row_h))
                ry += row_h

        self._add(op)
        self.y += h + GAP

    # ---- tabelle
    def _natural_widths(self, block: TableBlock, size: float) -> list[float]:
        """Larghezza minima di ogni colonna per mostrare tutto il contenuto (intestazioni anche su 2 righe)."""
        fm, fm_bold, fm_head = self.m(size), self.m(size, bold=True), self.m(size - 0.3, bold=True)
        widths = []
        for i, col in enumerate(block.columns):
            data = 0.0
            for row in block.rows:
                if row.kind == "section" or i >= len(row.cells):
                    continue
                cell = row.cells[i]
                metrics = fm_bold if (cell.bold or row.kind == "highlight") else fm
                data = max(data, metrics.width(cell.text) * 1.02 + (SWATCH_W if cell.swatch else 0))
            swatch = SWATCH_W if col.swatch else 0
            head_full = fm_head.width(col.header) * 1.02 + swatch
            if head_full <= data:
                head = head_full
            elif len(_header_pieces(col.header)) >= 2 or col.swatch:
                # intestazione su più righe (al massimo 3), andando a capo tra le parole
                # (i nomi delle strategie, con il campione di colore, possono andare a capo anche dentro una parola)
                head = max(data, min(head_full, self._wrapped_header_width(col.header, fm_head) + swatch))
            else:  # una sola parola: non si spezza
                head = head_full
            natural = max(data, head) + 2 * CELL_PAD + 1
            widths.append(min(max(natural, col.min_width), self.W * col.max_share))
        return widths

    @staticmethod
    def _wrapped_header_width(header: str, fm: _Metrics) -> float:
        """Larghezza minima dell'intestazione su 2 o 3 righe."""
        pieces = _header_pieces(header)
        n = len(pieces)
        if n < 2:
            return fm.width(header) / 2 + fm.width("MM")

        def w(a: int, b: int) -> float:
            return fm.width("".join(pieces[a:b]).strip())

        best = min(max(w(0, i), w(i, n)) for i in range(1, n))
        if n >= 3:
            best = min(best, min(max(w(0, i), w(i, j), w(j, n)) for i in range(1, n - 1) for j in range(i + 1, n)))
        return best * 1.02 + 1

    @staticmethod
    def _column_widths(columns: list[TableColumn], mins: list[float], width: float) -> list[float]:
        """Parte dal minimo necessario e distribuisce lo spazio in più in base ai pesi."""
        free = width - sum(mins)
        if free <= 0:
            return [m * width / sum(mins) for m in mins] if sum(mins) > width else list(mins)
        total = sum(c.weight for c in columns) or 1.0
        return [m + free * c.weight / total for m, c in zip(mins, columns)]

    @staticmethod
    def _groups(free_cols: list[int], mins: list[float], room: float) -> list[list[int]]:
        """Divide le colonne in gruppi che entrano in ``room``, il più possibile uguali."""
        if not free_cols:
            return [[]]
        greedy: list[list[int]] = [[]]
        used = 0.0
        for c in free_cols:
            if greedy[-1] and used + mins[c] > room:
                greedy.append([])
                used = 0.0
            greedy[-1].append(c)
            used += mins[c]
        k = len(greedy)
        if k == 1:
            return greedy
        n = len(free_cols)
        sizes = [n // k + (1 if i < n % k else 0) for i in range(k)]
        balanced, start = [], 0
        for s in sizes:
            balanced.append(free_cols[start : start + s])
            start += s
        if all(sum(mins[c] for c in g) <= room for g in balanced):
            return balanced
        return greedy

    def _table(self, block: TableBlock) -> None:
        size = block.font_size
        mins = self._natural_widths(block, size)
        cols = list(range(len(block.columns)))

        def total(indexes) -> float:
            return sum(mins[c] for c in indexes)

        if total(cols) > self.W and total(cols) * 0.8 <= self.W:
            # poco più larga del foglio: si riduce il carattere invece di dividerla
            size = max(5.8, size * self.W / total(cols) * 0.98)
            mins = self._natural_widths(block, size)
        # colonne facoltative: omesse (prima le priorità più alte) se il foglio è troppo stretto
        dropped: list[str] = []
        optional = sorted((c for c in cols if block.columns[c].priority > 0),
                          key=lambda c: (-block.columns[c].priority, -c))
        for c in optional:
            if total(cols) <= self.W:
                break
            cols.remove(c)
            dropped.append(block.columns[c].header)
        if dropped and total(cols) > self.W and total(cols) * 0.8 <= self.W:
            size = max(5.8, size * self.W / total(cols) * 0.98)
            mins = self._natural_widths(block, size)

        lead = [c for c in cols if c < block.frozen]
        tail = [c for c in cols if c in block.repeat and c >= block.frozen]
        free = [c for c in cols if c not in lead and c not in tail]

        def split() -> list[list[int]]:
            room = self.W - total(lead) - total(tail)
            return self._groups(free, mins, room) if room > 0 else ([[c] for c in free] or [[]])

        groups = split()
        if len(groups) > 1:
            # con un carattere un po' più piccolo entrano più colonne per gruppo: meno gruppi, più leggibile
            base_size, base_mins = size, mins
            for factor in (0.94, 0.88):
                size = max(5.8, base_size * factor)
                mins = self._natural_widths(block, size)
                candidate = split()
                if len(candidate) < len(groups):
                    groups = candidate
                    break
            else:
                size, mins = base_size, base_mins
        n_free = len(free)
        position = {c: i + 1 for i, c in enumerate(free)}
        for n, group in enumerate(groups):
            indexes = lead + group + tail
            title = block.title
            if len(groups) > 1 and group:
                a, b = position[group[0]], position[group[-1]]
                part = f"{block.group_noun} {a}–{b} di {n_free}" if a != b else f"{block.group_noun} {a} di {n_free}"
                title = f"{block.title} · {part}" if block.title else part[0].upper() + part[1:]
            self._table_part(block, indexes, [mins[i] for i in indexes], title, size, new_group=n > 0)
        if dropped:
            self._text(TextBlock(
                "Colonne non stampate per mancanza di spazio: " + ", ".join(dropped)
                + ". Per vederle usa il foglio orizzontale.", size=6.8, color=P["MUTED"]))

    def _table_part(self, block: TableBlock, indexes: list[int], mins: list[float], title: str, size: float,
                    new_group: bool = False) -> None:
        columns = [block.columns[i] for i in indexes]
        widths = self._column_widths(columns, mins, self.W)
        xs = [0.0]
        for w in widths:
            xs.append(xs[-1] + w)
        pad = CELL_PAD
        row_h = size * 2.05
        head_size = size - 0.3
        fm, fm_bold, hfm = self.m(size), self.m(size, bold=True), self.m(head_size, bold=True)
        head_lines = 1
        for i, col in enumerate(columns):
            room = widths[i] - 2 * pad - (SWATCH_W if col.swatch else 0)
            if hfm.width(col.header) > room:
                head_lines = max(head_lines, min(3, hfm.line_count(_header_text(col.header), room)))
        head_h = (hfm.line() * head_lines + 7) if head_lines > 1 else row_h + 3
        title_h = 15.0 if title else 0.0

        needed = title_h + head_h + row_h * len(block.rows)
        small = needed <= (self.bottom - self.top) * 0.35
        # - i gruppi di colonne dopo il primo iniziano su una pagina nuova se non entrano nello spazio rimasto,
        #   così ogni gruppo si legge dall'inizio e non resta mescolato alla fine del gruppo precedente;
        # - le tabelle piccole non si dividono tra due pagine.
        if (new_group or small) and self.y + needed > self.bottom and self.y > self.top + 0.5:
            self._new_page()

        def start_segment(continued: bool) -> float:
            self._ensure(title_h + head_h + row_h * 3)
            y = self.y
            text = f"{title} (continua)" if (continued and title) else title
            if title and not continued:
                self._mark(title)
            if title_h:

                def op_title(p: QPainter, y=y, text=text):
                    p.setFont(self.font(8, bold=True))
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(2, y, self.W, title_h - 3), Qt.AlignLeft | Qt.AlignVCenter, text)

                self._add(op_title)
            hy = y + title_h

            def op_head(p: QPainter, hy=hy):
                p.fillRect(QRectF(0, hy, self.W, head_h), QColor(P["HEADER_BG"]))
                p.setFont(self.font(head_size, bold=True))
                for i, col in enumerate(columns):
                    rect = QRectF(xs[i] + pad, hy + 1, widths[i] - 2 * pad, head_h - 2)
                    text_w = hfm.width(col.header)
                    one_line = text_w <= rect.width() - (SWATCH_W if col.swatch else 0) + 0.5
                    align = _align(col.align)
                    if col.swatch:
                        rect = _with_swatch(p, rect, text_w if one_line else rect.width(), col.align, col.swatch)
                        align = Qt.AlignLeft
                    elif one_line and text_w > rect.width():
                        rect = rect.adjusted(-pad * 0.5, 0, pad * 0.5, 0)
                    p.setPen(QColor(col.color or P["INK_2"]))
                    if one_line:
                        p.drawText(rect, align | Qt.AlignVCenter, col.header)
                    else:
                        option = QTextOption(align | Qt.AlignVCenter)
                        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                        p.drawText(rect, _header_text(col.header), option)
                p.setPen(QPen(QColor(P["BORDER"]), 0.8))
                p.drawLine(QPointF(0, hy + head_h), QPointF(self.W, hy + head_h))

            self._add(op_head)
            return hy

        seg_top = start_segment(False)
        y = seg_top + head_h
        zebra = 0
        for row in block.rows:
            if y + row_h > self.bottom:
                self._add(_border_op(seg_top, y, self.W))
                self.y = y
                self._new_page()
                seg_top = start_segment(True)
                y = seg_top + head_h
            ry = y
            if row.kind == "section":
                zebra = 0

                def op_section(p: QPainter, ry=ry, row=row):
                    p.fillRect(QRectF(0, ry, self.W, row_h), QColor(P["SECTION_BG"]))
                    p.setFont(self.font(size, bold=True))
                    p.setPen(QColor(P["INK"]))
                    p.drawText(QRectF(pad, ry, self.W - 2 * pad, row_h), Qt.AlignLeft | Qt.AlignVCenter, row.cells[0].text)

                self._add(op_section)
            else:
                bg = P["HIGHLIGHT_ROW"] if row.kind == "highlight" else ("#ffffff" if zebra % 2 == 0 else P["ALT_BASE"])
                zebra += 1
                cells = [row.cells[i] if i < len(row.cells) else Cell("") for i in indexes]

                def op_row(p: QPainter, ry=ry, row=row, cells=cells, bg=bg):
                    p.fillRect(QRectF(0, ry, self.W, row_h), QColor(bg))
                    for i, cell in enumerate(cells):
                        if cell.bg:
                            p.fillRect(QRectF(xs[i], ry, widths[i], row_h), QColor(cell.bg))
                        rect = QRectF(xs[i] + pad, ry, widths[i] - 2 * pad, row_h)
                        is_bold = cell.bold or row.kind == "highlight"
                        metrics = fm_bold if is_bold else fm
                        align = _align(columns[i].align)
                        if cell.swatch:
                            rect = _with_swatch(p, rect, metrics.width(cell.text), columns[i].align, cell.swatch)
                            align = Qt.AlignLeft
                        elif metrics.width(cell.text) > rect.width():
                            # si tollera un piccolo sconfinamento nel margine della cella prima di troncare
                            rect = rect.adjusted(-pad * 0.8, 0, pad * 0.8, 0)
                        p.setFont(self.font(size, bold=is_bold))
                        p.setPen(QColor(cell.color or P["INK"]))
                        p.drawText(rect, align | Qt.AlignVCenter, metrics.elide(cell.text, rect.width()))
                    p.setPen(QPen(QColor(P["GRID"]), 0.5))
                    p.drawLine(QPointF(0, ry + row_h), QPointF(self.W, ry + row_h))

                self._add(op_row)
            y += row_h
        self._add(_border_op(seg_top, y, self.W))
        self.y = y + GAP


# --------------------------------------------------------------------------- disegno


def _align(align: str) -> Qt.AlignmentFlag:
    return {"left": Qt.AlignLeft, "center": Qt.AlignHCenter}.get(align, Qt.AlignRight)


def _card(p: QPainter, rect: QRectF, fill: str) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(P["BORDER"]), 0.7))
    p.setBrush(QColor(fill))
    p.drawRoundedRect(rect, 5, 5)
    p.restore()


def _swatch(p: QPainter, left_mid: QPointF, color: str, style: Qt.PenStyle = Qt.SolidLine) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2.2, style)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(left_mid.x(), left_mid.y()), QPointF(left_mid.x() + 11, left_mid.y()))
    p.restore()


def _with_swatch(p: QPainter, rect: QRectF, text_w: float, align: str, swatch) -> QRectF:
    """Disegna il campione di colore subito prima del testo; restituisce l'area del testo."""
    text_w = min(text_w, rect.width() - SWATCH_W)
    if align == "right":
        x = rect.right() - text_w - SWATCH_W
    elif align == "center":
        x = rect.center().x() - (text_w + SWATCH_W) / 2
    else:
        x = rect.left()
    _swatch(p, QPointF(x, rect.center().y()), *swatch)
    return QRectF(x + SWATCH_W, rect.top(), rect.right() - x - SWATCH_W + CELL_PAD * 0.8, rect.height())


def _border_op(top: float, bottom: float, width: float) -> Callable[[QPainter], None]:
    def op(p: QPainter):
        p.save()
        p.setPen(QPen(QColor(P["BORDER"]), 0.7))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, top, width, bottom - top))
        p.restore()

    return op


def _paint_header_footer(p: QPainter, layout: _Layout, number: int, total: int) -> None:
    c, W, H = layout.content, layout.W, layout.H
    doc = layout.document.doc if layout.document is not None else None
    multi = doc is not None and len(doc.sections) > 1
    p.fillRect(QRectF(0, 0, W, 3.2), QColor(P["ACCENT"]))
    p.setFont(layout.font(6.4, bold=True))
    p.setPen(QColor(P["ACCENT"]))
    top_line = APP_NAME.upper() + (f"  ·  {doc.title.upper()}" if multi else "")
    p.drawText(QRectF(0, 9, W * 0.6, 10), Qt.AlignLeft | Qt.AlignVCenter, top_line)
    p.setFont(layout.font(15.5, bold=True))
    p.setPen(QColor(P["INK"]))
    p.drawText(QRectF(0, 19, W * 0.66, 21), Qt.AlignLeft | Qt.AlignVCenter,
               _Metrics.get(15.5, True).elide(c.title, W * 0.66))
    if c.subtitle:
        p.setFont(layout.font(8.3))
        p.setPen(QColor(P["INK_2"]))
        width = W * (0.62 if c.meta else 1.0)
        p.drawText(QRectF(0, 40, width, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   _Metrics.get(8.3).elide(c.subtitle, width))
    p.setFont(layout.font(7))
    p.setPen(QColor(P["INK_2"]))
    meta_m = _Metrics.get(7)
    for i, line in enumerate(c.meta[:4]):
        p.drawText(QRectF(W * 0.36, 9 + i * 11, W * 0.64, 11), Qt.AlignRight | Qt.AlignVCenter,
                   meta_m.elide(line, W * 0.64))
    p.setPen(QPen(QColor(P["BORDER"]), 0.8))
    p.drawLine(QPointF(0, HEADER_H - 2), QPointF(W, HEADER_H - 2))

    fy = H - FOOTER_H + 4
    p.setPen(QPen(QColor(P["GRID"]), 0.7))
    p.drawLine(QPointF(0, fy - 3), QPointF(W, fy - 3))
    p.setFont(layout.font(6.5))
    p.setPen(QColor(P["MUTED"]))
    created = (doc.created if doc is not None else c.created).strftime("%d/%m/%Y %H:%M")
    where = f"{doc.title} · {c.title}" if multi and c.title != doc.title else c.title
    p.drawText(QRectF(0, fy, W * 0.75, 11), Qt.AlignLeft | Qt.AlignVCenter,
               _Metrics.get(6.5).elide(f"{APP_NAME} {__version__} · {where} · generato il {created}", W * 0.75))
    p.drawText(QRectF(W * 0.6, fy, W * 0.4, 11), Qt.AlignRight | Qt.AlignVCenter, f"Pagina {number} di {total}")


# --------------------------------------------------------------------------- documento


class DocumentLayout:
    """Tutte le pagine di un documento per una dimensione di pagina (in punti)."""

    def __init__(self, doc: Document, width: float, height: float):
        self.doc = doc
        self.W = width
        self.H = height
        self.pages: list[tuple[int, _Layout, list]] = []  # (sezione, layout della sezione, operazioni)
        self.labels: list[str] = []
        self.section_pages: list[list[int]] = []
        self._numbers: dict[int, int] = {}
        for si, content in enumerate(doc.sections):
            layout = _Layout(content, width, height, self, si)
            indexes = []
            for pi, ops in enumerate(layout.pages):
                indexes.append(len(self.pages))
                self.pages.append((si, layout, ops))
                self.labels.append(layout.page_label(pi))
            self.section_pages.append(indexes)

    @property
    def orientation(self) -> QPageLayout.Orientation:
        return PORTRAIT if self.H > self.W else LANDSCAPE

    def __len__(self) -> int:
        return len(self.pages)

    def printed_range(self, section_index: int) -> tuple[int, int] | None:
        numbers = [self._numbers[g] for g in self.section_pages[section_index] if g in self._numbers]
        return (min(numbers), max(numbers)) if numbers else None

    def paint(self, device: QPaintDevice, origin: QPointF, new_page: Callable[[], bool] | None,
              pages: list[int] | None = None, keep: Callable[[int], bool] | None = None) -> int:
        """Disegna le pagine ``pages`` (tutte se None) numerandole di seguito; ``keep(numero)``
        esclude pagine (es. intervallo scelto nella finestra di stampa) senza cambiare la numerazione."""
        selected = list(range(len(self.pages))) if pages is None else [g for g in pages if 0 <= g < len(self.pages)]
        self._numbers = {g: n + 1 for n, g in enumerate(selected)}
        dpi = device.logicalDpiX() or 72
        for _, layout, _ in self.pages:
            layout.set_paint_dpi(dpi)
        painter = QPainter(device)
        if not painter.isActive():  # es. file PDF non scrivibile
            return 0
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        scale = dpi / 72.0
        painted = 0
        for n, g in enumerate(selected):
            if keep is not None and not keep(n + 1):
                continue
            if painted and new_page is not None:
                new_page()
            _si, layout, ops = self.pages[g]
            painter.save()
            painter.scale(scale, scale)
            painter.translate(origin)
            _paint_header_footer(painter, layout, n + 1, len(selected))
            for op in ops:
                op(painter)
            painter.restore()
            painted += 1
        painter.end()
        return painted


# --------------------------------------------------------------------------- stampa / PDF / immagini


def page_layout(orientation: QPageLayout.Orientation = LANDSCAPE) -> QPageLayout:
    margins = QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM)
    return QPageLayout(QPageSize(QPageSize.A4), orientation, margins, QPageLayout.Millimeter)


def layout_document(content: "ReportContent | Document",
                    orientation: QPageLayout.Orientation = LANDSCAPE) -> DocumentLayout:
    paint = page_layout(orientation).paintRect(QPageLayout.Point)
    return DocumentLayout(as_document(content), paint.width(), paint.height())


def make_printer(title: str = APP_NAME, orientation: QPageLayout.Orientation = LANDSCAPE) -> QPrinter:
    printer = QPrinter(QPrinter.HighResolution)
    printer.setPageLayout(page_layout(orientation))
    printer.setDocName(title)
    printer.setCreator(f"{APP_NAME} {__version__}")
    return printer


def _printer_filter(printer: QPrinter) -> Callable[[int], bool] | None:
    """Intervallo di pagine scelto nella finestra di stampa (es. "1-3, 5")."""
    try:
        ranges = printer.pageRanges()
        if not ranges.isEmpty():
            return ranges.contains
    except AttributeError:
        pass
    if printer.printRange() == QPrinter.PageRange and printer.fromPage() > 0:
        low, high = printer.fromPage(), printer.toPage() or 10**9
        return lambda n: low <= n <= high
    return None


class PrintJob:
    """Un documento pronto da stampare: layout calcolato e pagine scelte."""

    def __init__(self, content: "ReportContent | Document", orientation: QPageLayout.Orientation = LANDSCAPE,
                 pages: list[int] | None = None, layout: DocumentLayout | None = None):
        self.doc = as_document(content)
        self.orientation = orientation
        self.layout = layout if layout is not None else layout_document(self.doc, orientation)
        self.pages = list(range(len(self.layout))) if pages is None else list(pages)

    @property
    def title(self) -> str:
        return self.doc.title

    def layout_for(self, width: float, height: float) -> tuple[DocumentLayout, list[int]]:
        """Layout per un'altra dimensione di pagina (es. orientamento cambiato nell'anteprima):
        si stampano per intero le sezioni che avevano almeno una pagina scelta."""
        if abs(width - self.layout.W) < 0.5 and abs(height - self.layout.H) < 0.5:
            return self.layout, self.pages
        other = DocumentLayout(self.doc, width, height)
        sections = {self.layout.pages[g][0] for g in self.pages}
        return other, [g for g, (si, _, _) in enumerate(other.pages) if si in sections]

    def print_to(self, printer: QPrinter) -> int:
        paint = printer.pageLayout().paintRect(QPageLayout.Point)
        layout, pages = self.layout_for(paint.width(), paint.height())
        return layout.paint(printer, QPointF(0, 0), printer.newPage, pages, _printer_filter(printer))

    def make_printer(self) -> QPrinter:
        return make_printer(self.doc.title, self.orientation)

    def export_pdf(self, path: str) -> int:
        printer = self.make_printer()
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        return self.print_to(printer)

    def render_images(self, dpi: int = 110, max_pages: int | None = None) -> list[QImage]:
        """Pagine come immagini (A4 con margini), per anteprime e test."""
        layout_ = page_layout(self.orientation)
        full = layout_.fullRect(QPageLayout.Point)
        paint = layout_.paintRect(QPageLayout.Point)
        scale = dpi / 72.0
        size = (int(full.width() * scale), int(full.height() * scale))
        count = len(self.pages) if max_pages is None else min(len(self.pages), max_pages)
        images = []
        for n in range(count):
            img = QImage(*size, QImage.Format_RGB32)
            img.setDotsPerMeterX(int(dpi / 0.0254))
            img.setDotsPerMeterY(int(dpi / 0.0254))
            img.fill(QColor("#ffffff"))
            # stessa numerazione del documento completo: si disegna solo la pagina n + 1
            self.layout.paint(img, paint.topLeft(), None, self.pages, keep=lambda k, n=n: k == n + 1)
            images.append(img)
        return images


def print_to(content: "ReportContent | Document", printer: QPrinter) -> int:
    """Disegna il report sulla stampante (usato anche dall'anteprima). Restituisce le pagine."""
    paint = printer.pageLayout().paintRect(QPageLayout.Point)
    layout = DocumentLayout(as_document(content), paint.width(), paint.height())
    return PrintJob(content, printer.pageLayout().orientation(), layout=layout).print_to(printer)


def export_pdf(content: "ReportContent | Document", path: str,
               orientation: QPageLayout.Orientation = LANDSCAPE) -> int:
    return PrintJob(content, orientation).export_pdf(path)


def render_images(content: "ReportContent | Document", dpi: int = 110, max_pages: int | None = None,
                  orientation: QPageLayout.Orientation = LANDSCAPE) -> list[QImage]:
    return PrintJob(content, orientation).render_images(dpi, max_pages)
