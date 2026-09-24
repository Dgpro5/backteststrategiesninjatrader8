"""Colori, formattazione dei numeri e configurazione grafica."""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyleFactory

# Superfici e inchiostri (tema chiaro).
SURFACE = "#fcfcfb"
PAGE = "#f4f4f1"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BORDER = "#d6d5ce"
ACCENT = "#2a78d6"

# Colori richiesti per il Monte Carlo e per il portafoglio combinato.
MC_GRAY = "#8c8c8c"
MC_BEST = "#0a8f0a"
MC_WORST = "#d03b3b"
MC_MEAN = "#2a78d6"
MC_ORIGINAL = "#eb6834"
COMBINED = "#000000"

POSITIVE = "#0ca30c"
NEGATIVE = "#d03b3b"
POSITIVE_TEXT = "#006300"
NEGATIVE_TEXT = "#b42323"

# Palette categoriale in ordine fisso (validata per daltonismo sulle coppie adiacenti).
# Dopo l'ottava strategia i colori si ripetono con linea tratteggiata.
STRATEGY_COLORS = [
    "#2a78d6",  # blu
    "#eb6834",  # arancio
    "#1baf7a",  # acqua
    "#eda100",  # giallo
    "#e87ba4",  # magenta
    "#008300",  # verde
    "#4a3aa7",  # viola
    "#e34948",  # rosso
]

MONTHS_IT = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
WEEKDAYS_IT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


def strategy_color(index: int) -> str:
    return STRATEGY_COLORS[index % len(STRATEGY_COLORS)]


def strategy_line_style(index: int) -> Qt.PenStyle:
    return Qt.SolidLine if (index // len(STRATEGY_COLORS)) % 2 == 0 else Qt.DashLine


def strategy_pen(index: int, width: float = 2.0) -> QPen:
    return pg.mkPen(strategy_color(index), width=width, style=strategy_line_style(index))


def swatch_icon(color: str, style: Qt.PenStyle = Qt.SolidLine, size: int = 14) -> QIcon:
    pix = QPixmap(size * 2, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 3, style)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(2, size // 2, size * 2 - 2, size // 2)
    painter.end()
    return QIcon(pix)


# --------------------------------------------------------------------------- formattazione


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def fmt_money(value, decimals: int = 2) -> str:
    if _is_missing(value):
        return "—"
    if math.isinf(value):
        return "∞" if value > 0 else "-∞"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def fmt_pct(value, decimals: int = 2) -> str:
    if _is_missing(value):
        return "—"
    return f"{value * 100:.{decimals}f}%"


def fmt_ratio(value, decimals: int = 2) -> str:
    if _is_missing(value):
        return "—"
    if math.isinf(value):
        return "∞" if value > 0 else "-∞"
    return f"{value:,.{decimals}f}"


def fmt_datetime(value) -> str:
    if _is_missing(value):
        return "—"
    if isinstance(value, str):
        return value
    dt = np.datetime64(value, "s").item()
    return dt.strftime("%d/%m/%Y %H:%M")


def fmt_date(value) -> str:
    if _is_missing(value):
        return "—"
    return np.datetime64(value, "D").item().strftime("%d/%m/%Y")


def fmt_month(value) -> str:
    d = np.datetime64(value, "M").item()
    return f"{MONTHS_IT[d.month - 1]} {d.year}"


def fmt_duration(seconds) -> str:
    if _is_missing(seconds):
        return "—"
    seconds = int(round(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}g {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def fmt_value(kind: str, value) -> str:
    if kind == "money":
        return fmt_money(value)
    if kind == "pct":
        return fmt_pct(value)
    if kind == "ratio":
        return fmt_ratio(value)
    if kind == "num":
        return fmt_ratio(value, 2)
    if kind == "num1":
        return fmt_ratio(value, 1)
    if kind == "int":
        return "—" if _is_missing(value) else f"{int(round(value)):,}"
    if kind == "datetime":
        return fmt_datetime(value)
    if kind == "duration":
        return fmt_duration(value)
    if kind == "money_date":
        return "—" if value is None else f"{fmt_money(value[0])}  ({fmt_date(value[1])})"
    if kind == "money_month":
        return "—" if value is None else f"{fmt_money(value[0])}  ({fmt_month(value[1])})"
    return "—" if value is None else str(value)


def value_tone(kind: str, value) -> int:
    """+1 positivo, -1 negativo, 0 neutro (per colorare i numeri)."""
    if kind in ("money_date", "money_month") and value is not None:
        value = value[0]
    elif kind != "money":
        return 0
    if _is_missing(value) or value == 0:
        return 0
    return 1 if value > 0 else -1


def tone_color(tone: int) -> QColor:
    return QColor(POSITIVE_TEXT if tone > 0 else NEGATIVE_TEXT if tone < 0 else INK)


# --------------------------------------------------------------------------- setup


def apply_theme(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(PAGE))
    pal.setColor(QPalette.WindowText, QColor(INK))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f7f7f4"))
    pal.setColor(QPalette.Text, QColor(INK))
    pal.setColor(QPalette.Button, QColor("#ffffff"))
    pal.setColor(QPalette.ButtonText, QColor(INK))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipText, QColor(INK))
    pal.setColor(QPalette.PlaceholderText, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
    pg.setConfigOptions(antialias=True, background=SURFACE, foreground=INK_2)


STYLESHEET = f"""
QMainWindow, QWidget#page {{ background: {PAGE}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PAGE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {INK_2}; padding: 8px 16px; margin-right: 2px;
    border: 1px solid transparent; border-bottom: none; font-weight: 600;
}}
QTabBar::tab:selected {{ background: {PAGE}; color: {INK}; border-color: {BORDER}; border-top: 3px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {INK}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#cardTitle {{ color: {INK_2}; font-weight: 600; }}
QLabel#kpiTitle {{ color: {INK_2}; font-size: 11px; }}
QLabel#kpiValue {{ color: {INK}; font-size: 18px; font-weight: 600; }}
QLabel#kpiSub {{ color: {MUTED}; font-size: 11px; }}
QLabel#sectionTitle {{ color: {INK}; font-size: 13px; font-weight: 700; }}
QLabel#hint {{ color: {INK_2}; }}
QLabel#emptyTitle {{ color: {INK}; font-size: 22px; font-weight: 700; }}
QLabel#emptyText {{ color: {INK_2}; font-size: 13px; }}
QTableWidget, QTableView, QListWidget {{
    background: #ffffff; border: 1px solid {BORDER}; border-radius: 6px; gridline-color: {GRID};
}}
QHeaderView::section {{
    background: #f1f0ec; color: {INK_2}; padding: 5px 8px; border: none;
    border-right: 1px solid {GRID}; border-bottom: 1px solid {BORDER}; font-weight: 600;
}}
QPushButton {{
    background: #ffffff; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: #256abf; }}
QPushButton:disabled {{ color: {MUTED}; }}
QToolTip {{ background: #ffffff; color: {INK}; border: 1px solid {BORDER}; padding: 4px; }}
"""
