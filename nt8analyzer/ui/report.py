"""Impaginazione dei report di stampa: A4, stile dell'app, sempre su carta chiara.

Un report è una sequenza di blocchi (indicatori, grafici, tabelle, testo). Il layout
viene calcolato per il dispositivo di destinazione (stampante, PDF o immagine) in punti
tipografici, poi ogni pagina viene disegnata con intestazione e "Pagina X di Y".
"""

from __future__ import annotations

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
)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, __version__
from . import theme

# La carta è sempre chiara: si usa la palette chiara qualunque sia il tema dell'app.
P = theme.LIGHT
PAGE_MARGIN_MM = 10.0


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
    """Riga di riepilogo (etichetta, valore) sotto l'intestazione."""

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
    image: QImage
    title: str = ""


@dataclass
class ChartGrid:
    charts: list[ChartBlock]
    columns: int = 2


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


@dataclass
class TableRow:
    cells: list[Cell]
    kind: str = "normal"  # normal | section | highlight


@dataclass
class TableBlock:
    columns: list[TableColumn]
    rows: list[TableRow]
    title: str = ""
    frozen: int = 1  # colonne ripetute quando la tabella va divisa in larghezza
    font_size: float = 7.3


@dataclass
class PageBreak:
    pass


@dataclass
class ReportContent:
    title: str
    subtitle: str = ""
    blocks: list = field(default_factory=list)
    meta: list[str] = field(default_factory=list)
    created: datetime = field(default_factory=datetime.now)


def tone_color(tone: int) -> str:
    return P["POSITIVE_TEXT"] if tone > 0 else P["NEGATIVE_TEXT"] if tone < 0 else P["INK"]


# --------------------------------------------------------------------------- layout

HEADER_H = 58.0
FOOTER_H = 16.0
GAP = 10.0


class _Layout:
    """Distribuisce i blocchi sulle pagine. Unità: punti tipografici (1/72 di pollice)."""

    def __init__(self, content: ReportContent, width: float, height: float, device: QPaintDevice):
        self.content = content
        self.W = width
        self.H = height
        self.device = device
        self.dpi = device.logicalDpiY() or 72
        self.top = HEADER_H + 8
        self.bottom = height - FOOTER_H - 6
        self.pages: list[list[Callable[[QPainter], None]]] = []
        self._fonts: dict[tuple, QFont] = {}
        self._new_page()
        for block in content.blocks:
            self._place(block)

    # ---- utilità
    def font(self, size: float, bold: bool = False, italic: bool = False) -> QFont:
        key = (size, bold, italic)
        if key not in self._fonts:
            f = QFont(QApplication.font().family())
            # La dimensione in punti viene compensata con la risoluzione del dispositivo,
            # così dopo la scala punti→pixel del painter il testo misura `size` punti.
            f.setPointSizeF(size * 72.0 / self.dpi)
            f.setBold(bold)
            f.setItalic(italic)
            f.setHintingPreference(QFont.PreferNoHinting)
            self._fonts[key] = f
        return self._fonts[key]

    def metrics(self, font: QFont) -> QFontMetricsF:
        return QFontMetricsF(font, self.device)

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = self.top

    def _ensure(self, height: float) -> None:
        if self.y + height > self.bottom and self.y > self.top + 0.5:
            self._new_page()

    def _add(self, op: Callable[[QPainter], None]) -> None:
        self.pages[-1].append(op)

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
        elif isinstance(block, TableBlock):
            self._table(block)
        elif isinstance(block, TextBlock):
            self._text(block)

    def _section(self, block: SectionTitle) -> None:
        self._ensure(18 + 90)  # il titolo resta con il contenuto che segue
        y = self.y + 2
        font = self.font(9.5, bold=True)

        def op(p: QPainter, y=y, text=block.text):
            p.fillRect(QRectF(0, y + 1.5, 3, 11), QColor(P["ACCENT"]))
            p.setFont(font)
            p.setPen(QColor(P["INK"]))
            p.drawText(QRectF(9, y, self.W - 9, 14), Qt.AlignLeft | Qt.AlignVCenter, text)

        self._add(op)
        self.y += 20

    def _info_bar(self, block: InfoBar) -> None:
        h = 30.0
        self._ensure(h + GAP)
        y = self.y
        label_font, value_font = self.font(6.3, bold=True), self.font(8.6, bold=True)
        items = block.items or [("", "")]
        lfm, vfm = self.metrics(label_font), self.metrics(value_font)
        # Larghezza proporzionale al contenuto, con lo spazio libero diviso in parti uguali.
        natural = [max(lfm.horizontalAdvance(label.upper()), vfm.horizontalAdvance(value)) + 18 for label, value in items]
        free = self.W - sum(natural)
        if free >= 0:
            widths = [w + free / len(items) for w in natural]
        else:
            widths = [w * self.W / sum(natural) for w in natural]
        xs = [sum(widths[:i]) for i in range(len(items))]

        def op(p: QPainter, y=y):
            _card(p, QRectF(0, y, self.W, h), P["ALT_BASE"])
            for i, (label, value) in enumerate(items):
                x, cell_w = xs[i], widths[i]
                if i:
                    p.setPen(QPen(QColor(P["GRID"]), 0.6))
                    p.drawLine(QPointF(x, y + 6), QPointF(x, y + h - 6))
                p.setFont(label_font)
                p.setPen(QColor(P["MUTED"]))
                p.drawText(QRectF(x + 9, y + 4, cell_w - 14, 10), Qt.AlignLeft | Qt.AlignVCenter,
                           lfm.elidedText(label.upper(), Qt.ElideRight, cell_w - 14))
                p.setFont(value_font)
                p.setPen(QColor(P["INK"]))
                text = vfm.elidedText(value, Qt.ElideRight, cell_w - 14)
                p.drawText(QRectF(x + 9, y + 14, cell_w - 14, 13), Qt.AlignLeft | Qt.AlignVCenter, text)

        self._add(op)
        self.y += h + GAP

    def _kpis(self, block: KpiBlock) -> None:
        items = block.items
        if not items:
            return
        gap, h = 7.0, 46.0
        per_row = max(1, min(len(items), int((self.W + gap) // (95 + gap))))
        label_font, small_label, value_font, sub_font = self.font(6.8), self.font(6.0), self.font(12.5, bold=True), self.font(6.3)
        for start in range(0, len(items), per_row):
            row = items[start : start + per_row]
            self._ensure(h + GAP)
            y = self.y
            card_w = (self.W - gap * (per_row - 1)) / per_row

            def op(p: QPainter, y=y, row=row, card_w=card_w):
                for i, kpi in enumerate(row):
                    x = i * (card_w + gap)
                    rect = QRectF(x, y, card_w, h)
                    _card(p, rect, P["SURFACE"])
                    inner = card_w - 16
                    font = label_font if self.metrics(label_font).horizontalAdvance(kpi.label) <= inner else small_label
                    p.setFont(font)
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(x + 8, y + 5, inner, 10), Qt.AlignLeft | Qt.AlignVCenter,
                               self.metrics(font).elidedText(kpi.label, Qt.ElideRight, inner))
                    p.setFont(value_font)
                    p.setPen(QColor(tone_color(kpi.tone)))
                    p.drawText(QRectF(x + 8, y + 15, inner, 17), Qt.AlignLeft | Qt.AlignVCenter,
                               self.metrics(value_font).elidedText(kpi.value, Qt.ElideRight, inner))
                    if kpi.sub:
                        p.setFont(sub_font)
                        p.setPen(QColor(P["MUTED"]))
                        p.drawText(QRectF(x + 8, y + 32, inner, 10), Qt.AlignLeft | Qt.AlignVCenter,
                                   self.metrics(sub_font).elidedText(kpi.sub, Qt.ElideRight, inner))

            self._add(op)
            self.y += h + GAP

    def _chart_row(self, charts: list[ChartBlock], columns: int) -> None:
        gap, pad = 8.0, 5.0
        cell_w = (self.W - gap * (columns - 1)) / columns
        title_h = 13.0 if any(c.title for c in charts) else 0.0

        def natural_height(c: ChartBlock) -> float:
            img = c.image
            return (cell_w - 2 * pad) * img.height() / max(1, img.width()) + 2 * pad

        height = max(natural_height(c) for c in charts) + title_h
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
        y = self.y
        title_font = self.font(8, bold=True)

        def op(p: QPainter, y=y, charts=charts, height=height):
            for i, c in enumerate(charts):
                x = i * (cell_w + gap)
                top = y
                if title_h:
                    p.setFont(title_font)
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(x + 2, y, cell_w, title_h - 2), Qt.AlignLeft | Qt.AlignVCenter, c.title)
                    top = y + title_h
                box = QRectF(x, top, cell_w, height - title_h)
                _card(p, box, "#ffffff")
                inner = box.adjusted(pad, pad, -pad, -pad)
                img = c.image
                ratio = img.width() / max(1, img.height())
                w, h = inner.width(), inner.width() / ratio
                if h > inner.height():
                    h = inner.height()
                    w = h * ratio
                target = QRectF(inner.left() + (inner.width() - w) / 2, inner.top(), w, h)
                p.drawImage(target, img)

        self._add(op)
        self.y += height + GAP

    def _text(self, block: TextBlock) -> None:
        font = self.font(block.size)
        rect = self.metrics(font).boundingRect(QRectF(0, 0, self.W, 10_000), Qt.TextWordWrap, block.text)
        h = rect.height() + 2
        self._ensure(h)
        y = self.y

        def op(p: QPainter, y=y):
            p.setFont(font)
            p.setPen(QColor(block.color))
            p.drawText(QRectF(0, y, self.W, h), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, block.text)

        self._add(op)
        self.y += h + GAP * 0.6

    # ---- tabelle
    CELL_PAD = 4.0
    SWATCH_W = 15.0

    def _natural_widths(self, block: TableBlock, size: float) -> list[float]:
        """Larghezza minima di ogni colonna per mostrare tutto il contenuto (intestazione a capo)."""
        fm, fm_bold = self.metrics(self.font(size)), self.metrics(self.font(size, bold=True))
        fm_head = self.metrics(self.font(size - 0.3, bold=True))
        widths = []
        for i, col in enumerate(block.columns):
            data = 0.0
            for row in block.rows:
                if row.kind == "section" or i >= len(row.cells):
                    continue
                cell = row.cells[i]
                metrics = fm_bold if (cell.bold or row.kind == "highlight") else fm
                data = max(data, metrics.horizontalAdvance(cell.text) + (self.SWATCH_W if cell.swatch else 0))
            head_full = fm_head.horizontalAdvance(col.header)
            words = col.header.split()
            if head_full <= data or len(words) < 2:
                head = head_full
            else:  # intestazione su due righe, divisa tra due parole
                two_lines = min(
                    max(fm_head.horizontalAdvance(" ".join(words[:k])), fm_head.horizontalAdvance(" ".join(words[k:])))
                    for k in range(1, len(words))
                )
                head = max(data, two_lines)
            head += self.SWATCH_W if col.swatch else 0
            natural = max(data, head) + 2 * self.CELL_PAD + 1
            widths.append(min(max(natural, col.min_width), self.W * 0.45))
        return widths

    def _column_widths(self, columns: list[TableColumn], mins: list[float], width: float) -> list[float]:
        """Parte dal minimo necessario e distribuisce lo spazio in più in base ai pesi."""
        free = width - sum(mins)
        if free <= 0:
            return [m * width / sum(mins) for m in mins] if sum(mins) > width else list(mins)
        total = sum(c.weight for c in columns) or 1.0
        return [m + free * c.weight / total for m, c in zip(mins, columns)]

    def _table(self, block: TableBlock) -> None:
        size = block.font_size
        mins = self._natural_widths(block, size)
        total = sum(mins)
        if total > self.W and total * 0.84 <= self.W:
            # poco più larga della pagina: si riduce il carattere invece di dividerla
            size = max(5.8, size * self.W / total * 0.99)
            mins = self._natural_widths(block, size)
        frozen_w = sum(mins[: block.frozen])
        others = list(range(block.frozen, len(block.columns)))
        # Divisione in larghezza se le colonne non entrano nella pagina.
        chunks: list[list[int]] = []
        current: list[int] = []
        used = frozen_w
        for idx in others:
            need = mins[idx]
            if current and used + need > self.W:
                chunks.append(current)
                current, used = [], frozen_w
            current.append(idx)
            used += need
        chunks.append(current)
        for n, chunk in enumerate(chunks):
            indexes = list(range(block.frozen)) + chunk
            title = block.title
            if len(chunks) > 1:
                part = f"colonne {n + 1} di {len(chunks)}"
                title = f"{block.title} ({part})" if block.title else part.capitalize()
            self._table_part(block, indexes, [mins[i] for i in indexes], title, size)

    def _table_part(self, block: TableBlock, indexes: list[int], mins: list[float], title: str, size: float) -> None:
        columns = [block.columns[i] for i in indexes]
        widths = self._column_widths(columns, mins, self.W)
        xs = [0.0]
        for w in widths:
            xs.append(xs[-1] + w)
        pad = self.CELL_PAD
        row_h = size * 2.05
        font = self.font(size)
        bold = self.font(size, bold=True)
        head_font = self.font(size - 0.3, bold=True)
        fm, fm_bold, hfm = self.metrics(font), self.metrics(bold), self.metrics(head_font)
        wraps = any(
            hfm.horizontalAdvance(col.header) > widths[i] - 2 * pad - (self.SWATCH_W if col.swatch else 0)
            for i, col in enumerate(columns)
        )
        head_h = (hfm.lineSpacing() * 2 + 7) if wraps else row_h + 3
        title_h = 15.0 if title else 0.0

        def start_segment(continued: bool) -> float:
            self._ensure(title_h + head_h + row_h * 3)
            y = self.y
            text = f"{title} (continua)" if (continued and title) else title
            if title_h:
                title_font = self.font(8, bold=True)

                def op_title(p: QPainter, y=y, text=text):
                    p.setFont(title_font)
                    p.setPen(QColor(P["INK_2"]))
                    p.drawText(QRectF(2, y, self.W, title_h - 3), Qt.AlignLeft | Qt.AlignVCenter, text)

                self._add(op_title)
            hy = y + title_h

            def op_head(p: QPainter, hy=hy):
                p.fillRect(QRectF(0, hy, self.W, head_h), QColor(P["HEADER_BG"]))
                p.setFont(head_font)
                for i, col in enumerate(columns):
                    rect = QRectF(xs[i] + pad, hy + 1, widths[i] - 2 * pad, head_h - 2)
                    align = _align(col.align)
                    one_line = hfm.horizontalAdvance(col.header) <= rect.width() - (self.SWATCH_W if col.swatch else 0)
                    if col.swatch:
                        text_w = hfm.horizontalAdvance(col.header) if one_line else rect.width()
                        rect = _with_swatch(p, rect, text_w, col.align, col.swatch, self.SWATCH_W)
                        align = Qt.AlignLeft
                    p.setPen(QColor(col.color or P["INK_2"]))
                    flags = align | Qt.AlignVCenter
                    p.drawText(rect, flags if one_line else flags | Qt.TextWordWrap, col.header)
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
                    p.setFont(bold)
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
                            text_w = metrics.horizontalAdvance(cell.text)
                            rect = _with_swatch(p, rect, text_w, columns[i].align, cell.swatch, self.SWATCH_W)
                            align = Qt.AlignLeft
                        p.setFont(bold if is_bold else font)
                        p.setPen(QColor(cell.color or P["INK"]))
                        p.drawText(rect, align | Qt.AlignVCenter,
                                   metrics.elidedText(cell.text, Qt.ElideRight, rect.width() + 0.5))
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


def _with_swatch(p: QPainter, rect: QRectF, text_w: float, align: str, swatch, swatch_w: float) -> QRectF:
    """Disegna il campione di colore subito prima del testo; restituisce l'area del testo."""
    text_w = min(text_w, rect.width() - swatch_w)
    if align == "right":
        x = rect.right() - text_w - swatch_w
    elif align == "center":
        x = rect.center().x() - (text_w + swatch_w) / 2
    else:
        x = rect.left()
    _swatch(p, QPointF(x, rect.center().y()), *swatch)
    return QRectF(x + swatch_w, rect.top(), rect.right() - x - swatch_w, rect.height())


def _border_op(top: float, bottom: float, width: float) -> Callable[[QPainter], None]:
    def op(p: QPainter):
        p.save()
        p.setPen(QPen(QColor(P["BORDER"]), 0.7))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, top, width, bottom - top))
        p.restore()

    return op


def _paint_header_footer(p: QPainter, layout: _Layout, index: int, total: int) -> None:
    c, W, H = layout.content, layout.W, layout.H
    p.fillRect(QRectF(0, 0, W, 3.2), QColor(P["ACCENT"]))
    p.setFont(layout.font(6.4, bold=True))
    p.setPen(QColor(P["ACCENT"]))
    p.drawText(QRectF(0, 9, W / 2, 10), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME.upper())
    p.setFont(layout.font(15.5, bold=True))
    p.setPen(QColor(P["INK"]))
    p.drawText(QRectF(0, 19, W * 0.66, 21), Qt.AlignLeft | Qt.AlignVCenter, c.title)
    if c.subtitle:
        font = layout.font(8.3)
        p.setFont(font)
        p.setPen(QColor(P["INK_2"]))
        p.drawText(QRectF(0, 40, W * 0.7, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   layout.metrics(font).elidedText(c.subtitle, Qt.ElideRight, W * 0.7))
    p.setFont(layout.font(7))
    p.setPen(QColor(P["INK_2"]))
    for i, line in enumerate(c.meta[:4]):
        p.drawText(QRectF(W * 0.55, 9 + i * 11, W * 0.45, 11), Qt.AlignRight | Qt.AlignVCenter, line)
    p.setPen(QPen(QColor(P["BORDER"]), 0.8))
    p.drawLine(QPointF(0, HEADER_H - 2), QPointF(W, HEADER_H - 2))

    fy = H - FOOTER_H + 4
    p.setPen(QPen(QColor(P["GRID"]), 0.7))
    p.drawLine(QPointF(0, fy - 3), QPointF(W, fy - 3))
    p.setFont(layout.font(6.5))
    p.setPen(QColor(P["MUTED"]))
    p.drawText(QRectF(0, fy, W * 0.7, 11), Qt.AlignLeft | Qt.AlignVCenter,
               f"{APP_NAME} {__version__} · {c.title} · generato il {c.created.strftime('%d/%m/%Y %H:%M')}")
    p.drawText(QRectF(W * 0.6, fy, W * 0.4, 11), Qt.AlignRight | Qt.AlignVCenter, f"Pagina {index + 1} di {total}")


def _paint(content: ReportContent, device: QPaintDevice, page_pt: QRectF, next_page: Callable[[], bool] | None) -> int:
    """Disegna tutte le pagine; ``page_pt`` è l'area utile in punti rispetto all'origine del painter."""
    layout = _Layout(content, page_pt.width(), page_pt.height(), device)
    scale = (device.logicalDpiX() or 72) / 72.0
    painter = QPainter(device)
    if not painter.isActive():  # es. file PDF non scrivibile
        return 0
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    total = len(layout.pages)
    for index, ops in enumerate(layout.pages):
        if index and next_page is not None:
            next_page()
        painter.save()
        painter.scale(scale, scale)
        painter.translate(page_pt.left(), page_pt.top())
        _paint_header_footer(painter, layout, index, total)
        for op in ops:
            op(painter)
        painter.restore()
    painter.end()
    return total


# --------------------------------------------------------------------------- stampa / PDF / immagini


def page_layout(orientation: QPageLayout.Orientation = QPageLayout.Landscape) -> QPageLayout:
    margins = QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM)
    return QPageLayout(QPageSize(QPageSize.A4), orientation, margins, QPageLayout.Millimeter)


def make_printer(title: str = APP_NAME) -> QPrinter:
    printer = QPrinter(QPrinter.HighResolution)
    printer.setPageLayout(page_layout())
    printer.setDocName(title)
    printer.setCreator(f"{APP_NAME} {__version__}")
    return printer


def print_to(content: ReportContent, printer: QPrinter) -> int:
    """Disegna il report sulla stampante (usato anche dall'anteprima). Restituisce le pagine."""
    paint = printer.pageLayout().paintRect(QPageLayout.Point)
    return _paint(content, printer, QRectF(0, 0, paint.width(), paint.height()), printer.newPage)


def export_pdf(content: ReportContent, path: str) -> int:
    printer = make_printer(content.title)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    return print_to(content, printer)


def render_images(content: ReportContent, dpi: int = 110, max_pages: int | None = None) -> list[QImage]:
    """Pagine come immagini (A4 orizzontale con margini), per anteprime e test."""
    layout_ = page_layout()
    full = layout_.fullRect(QPageLayout.Point)
    paint = layout_.paintRect(QPageLayout.Point)
    scale = dpi / 72.0
    size = (int(full.width() * scale), int(full.height() * scale))
    probe = QImage(*size, QImage.Format_ARGB32)
    probe.setDotsPerMeterX(int(dpi / 0.0254))
    probe.setDotsPerMeterY(int(dpi / 0.0254))
    layout = _Layout(content, paint.width(), paint.height(), probe)
    count = len(layout.pages) if max_pages is None else min(len(layout.pages), max_pages)
    images = []
    for index in range(count):
        img = QImage(*size, QImage.Format_ARGB32)
        img.setDotsPerMeterX(probe.dotsPerMeterX())
        img.setDotsPerMeterY(probe.dotsPerMeterY())
        img.fill(QColor("#ffffff"))
        painter = QPainter(img)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        painter.scale(scale, scale)
        painter.translate(paint.left(), paint.top())
        _paint_header_footer(painter, layout, index, len(layout.pages))
        for op in layout.pages[index]:
            op(painter)
        painter.end()
        images.append(img)
    return images
