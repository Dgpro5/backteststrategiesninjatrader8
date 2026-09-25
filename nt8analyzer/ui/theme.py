"""Colori, formattazione dei numeri e configurazione grafica."""

from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyleFactory

# Due palette con gli stessi ruoli: i widget leggono sempre theme.<RUOLO> al momento del
# disegno, quindi set_mode() + ridisegno basta per cambiare tema.
# Categoriali: palette validata per daltonismo, in ordine fisso, con il passo adatto a
# ciascuna superficie. Dopo l'ottava strategia i colori si ripetono con linea tratteggiata.
LIGHT = {
    "SURFACE": "#fcfcfb",
    "PAGE": "#f4f4f1",
    "BASE": "#ffffff",
    "ALT_BASE": "#f7f7f4",
    "HEADER_BG": "#f1f0ec",
    "SECTION_BG": "#ecebe6",
    "HIGHLIGHT_ROW": "#efeee9",
    "INK": "#0b0b0b",
    "INK_2": "#52514e",
    "MUTED": "#898781",
    "GRID": "#e1e0d9",
    "BORDER": "#d6d5ce",
    "ACCENT": "#2a78d6",
    "ACCENT_HOVER": "#256abf",
    # Monte Carlo (grigio / verde / rosso / blu) e portafoglio combinato (nero).
    "MC_GRAY": "#8c8c8c",
    "MC_GRAY_RGB": (140, 140, 140),
    "MC_BEST": "#0a8f0a",
    "MC_WORST": "#d03b3b",
    "MC_MEAN": "#2a78d6",
    "MC_ORIGINAL": "#eb6834",
    "MC_CONF": "#b55d00",
    "COMBINED": "#000000",
    "POSITIVE": "#0ca30c",
    "NEGATIVE": "#d03b3b",
    "POSITIVE_TEXT": "#006300",
    "NEGATIVE_TEXT": "#b42323",
    "HIST_BAR": (42, 120, 214, 150),
    "CORR_NEUTRAL": (240, 239, 236),
    "CORR_POSITIVE": (224, 104, 103),
    "CORR_NEGATIVE": (109, 167, 236),
    "STRATEGY_COLORS": [
        "#2a78d6",  # blu
        "#eb6834",  # arancio
        "#1baf7a",  # acqua
        "#eda100",  # giallo
        "#e87ba4",  # magenta
        "#008300",  # verde
        "#4a3aa7",  # viola
        "#e34948",  # rosso
    ],
}

DARK = {
    "SURFACE": "#1a1a19",
    "PAGE": "#111110",
    "BASE": "#1a1a19",
    "ALT_BASE": "#202020",
    "HEADER_BG": "#242423",
    "SECTION_BG": "#2b2b29",
    "HIGHLIGHT_ROW": "#2e2e2c",
    "INK": "#ffffff",
    "INK_2": "#c3c2b7",
    "MUTED": "#898781",
    "GRID": "#2c2c2a",
    "BORDER": "#383835",
    "ACCENT": "#3987e5",
    "ACCENT_HOVER": "#5598e7",
    # Sullo sfondo scuro il portafoglio combinato diventa bianco (il nero sarebbe invisibile).
    "MC_GRAY": "#a0a0a0",
    "MC_GRAY_RGB": (175, 175, 175),
    "MC_BEST": "#0ca30c",
    "MC_WORST": "#e66767",
    "MC_MEAN": "#3987e5",
    "MC_ORIGINAL": "#d95926",
    "MC_CONF": "#c98500",
    "COMBINED": "#ffffff",
    "POSITIVE": "#0ca30c",
    "NEGATIVE": "#e66767",
    "POSITIVE_TEXT": "#0ca30c",
    "NEGATIVE_TEXT": "#e66767",
    "HIST_BAR": (57, 135, 229, 170),
    "CORR_NEUTRAL": (56, 56, 53),
    "CORR_POSITIVE": (230, 103, 103),
    "CORR_NEGATIVE": (57, 135, 229),
    "STRATEGY_COLORS": [
        "#3987e5",  # blu
        "#d95926",  # arancio
        "#199e70",  # acqua
        "#c98500",  # giallo
        "#d55181",  # magenta
        "#008300",  # verde
        "#9085e9",  # viola
        "#e66767",  # rosso
    ],
}

PALETTES = {"light": LIGHT, "dark": DARK}
MODE_LABELS = {"light": "Chiaro", "dark": "Scuro", "system": "Come il sistema"}

MODE = "light"
# Colori attivi: partono dal tema chiaro, set_mode() li sostituisce.
SURFACE = LIGHT["SURFACE"]
PAGE = LIGHT["PAGE"]
BASE = LIGHT["BASE"]
ALT_BASE = LIGHT["ALT_BASE"]
HEADER_BG = LIGHT["HEADER_BG"]
SECTION_BG = LIGHT["SECTION_BG"]
HIGHLIGHT_ROW = LIGHT["HIGHLIGHT_ROW"]
INK = LIGHT["INK"]
INK_2 = LIGHT["INK_2"]
MUTED = LIGHT["MUTED"]
GRID = LIGHT["GRID"]
BORDER = LIGHT["BORDER"]
ACCENT = LIGHT["ACCENT"]
ACCENT_HOVER = LIGHT["ACCENT_HOVER"]
MC_GRAY = LIGHT["MC_GRAY"]
MC_GRAY_RGB = LIGHT["MC_GRAY_RGB"]
MC_BEST = LIGHT["MC_BEST"]
MC_WORST = LIGHT["MC_WORST"]
MC_MEAN = LIGHT["MC_MEAN"]
MC_ORIGINAL = LIGHT["MC_ORIGINAL"]
MC_CONF = LIGHT["MC_CONF"]
COMBINED = LIGHT["COMBINED"]
POSITIVE = LIGHT["POSITIVE"]
NEGATIVE = LIGHT["NEGATIVE"]
POSITIVE_TEXT = LIGHT["POSITIVE_TEXT"]
NEGATIVE_TEXT = LIGHT["NEGATIVE_TEXT"]
HIST_BAR = LIGHT["HIST_BAR"]
CORR_NEUTRAL = LIGHT["CORR_NEUTRAL"]
CORR_POSITIVE = LIGHT["CORR_POSITIVE"]
CORR_NEGATIVE = LIGHT["CORR_NEGATIVE"]
STRATEGY_COLORS = LIGHT["STRATEGY_COLORS"]


def set_mode(mode: str) -> None:
    """Attiva la palette "light" o "dark" (i widget vanno poi ridisegnati)."""
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    globals().update(PALETTES[MODE])


def is_dark() -> bool:
    return MODE == "dark"


def resolve_mode(preference: str) -> str:
    """Traduce la preferenza (light/dark/system) nel tema effettivo."""
    if preference in PALETTES:
        return preference
    app = QGuiApplication.instance()
    if app is not None:
        try:
            if app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return "dark"
        except AttributeError:  # Qt < 6.5
            pass
    return "light"


def rgba(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def label_brush(alpha: int = 225):
    """Sfondo semitrasparente per etichette e legende sopra i grafici."""
    return pg.mkBrush(rgba(SURFACE, alpha))


def ring_pen(width: float = 1.5):
    """Bordo dei marker: stesso colore della superficie, per staccarli dalle linee."""
    return pg.mkPen(SURFACE, width=width)


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
    """Applica la palette corrente a Qt e a pyqtgraph."""
    app.setStyle(QStyleFactory.create("Fusion"))
    dark = is_dark()
    pal = QPalette()
    roles = {
        QPalette.Window: PAGE,
        QPalette.WindowText: INK,
        QPalette.Base: BASE,
        QPalette.AlternateBase: ALT_BASE,
        QPalette.Text: INK,
        QPalette.Button: BASE,
        QPalette.ButtonText: INK,
        QPalette.BrightText: "#ffffff",
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: BASE,
        QPalette.ToolTipText: INK,
        QPalette.PlaceholderText: MUTED,
        QPalette.Link: ACCENT,
        QPalette.Light: "#3a3a38" if dark else "#ffffff",
        QPalette.Midlight: "#2e2e2c" if dark else "#f0f0ee",
        QPalette.Mid: BORDER,
        QPalette.Dark: "#0b0b0b" if dark else "#a0a09a",
        QPalette.Shadow: "#000000" if dark else "#6b6b66",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())
    pg.setConfigOptions(antialias=True, background=SURFACE, foreground=INK_2)


_IMAGES: dict[str, str] = {}


def _image_file(key: str, width: int, height: int, draw) -> str:
    """Piccole immagini per il foglio di stile (spunta, frecce), generate una volta in PNG."""
    path = _IMAGES.get(key)
    if path and os.path.exists(path):
        return path
    pix = QPixmap(width, height)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    draw(painter)
    painter.end()
    path = os.path.join(tempfile.gettempdir(), f"nt8analyzer_{key}.png")
    pix.save(path, "PNG")
    _IMAGES[key] = path.replace("\\", "/")
    return _IMAGES[key]


def _check_image() -> str:
    def draw(painter: QPainter) -> None:
        pen = QPen(QColor("#ffffff"), 4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(6, 14.5), QPointF(11.5, 20), QPointF(22, 8)])

    return _image_file("check", 28, 28, draw)


def _arrow_image(direction: str, color: str) -> str:
    points = [QPointF(2, 9), QPointF(8, 2), QPointF(14, 9)] if direction == "up" else [
        QPointF(2, 1), QPointF(8, 8), QPointF(14, 1)
    ]

    def draw(painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(points)

    return _image_file(f"arrow_{direction}_{color.lstrip('#')}", 16, 10, draw)


def stylesheet() -> str:
    check = _check_image()
    up, down = _arrow_image("up", INK_2), _arrow_image("down", INK_2)
    return f"""
QMainWindow, QWidget#page {{ background: {PAGE}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PAGE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {INK_2}; padding: 8px 16px; margin-right: 2px;
    border: 1px solid transparent; border-bottom: none; font-weight: 600;
}}
QTabBar::tab:selected {{ background: {PAGE}; color: {INK}; border-color: {BORDER}; border-top: 3px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {INK}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#separator {{ background: {BORDER}; border: none; max-height: 1px; }}
QLabel#cardTitle {{ color: {INK_2}; font-weight: 600; }}
QLabel#kpiTitle {{ color: {INK_2}; font-size: 11px; }}
QLabel#kpiValue {{ color: {INK}; font-size: 18px; font-weight: 600; }}
QLabel#kpiSub {{ color: {MUTED}; font-size: 11px; }}
QLabel#sectionTitle {{ color: {INK}; font-size: 13px; font-weight: 700; }}
QLabel#hint {{ color: {INK_2}; }}
QLabel#emptyTitle {{ color: {INK}; font-size: 22px; font-weight: 700; }}
QLabel#emptyText {{ color: {INK_2}; font-size: 13px; }}
QTableWidget, QTableView, QListWidget {{
    background: {BASE}; alternate-background-color: {ALT_BASE}; color: {INK};
    border: 1px solid {BORDER}; border-radius: 6px; gridline-color: {GRID};
}}
QTableCornerButton::section {{ background: {HEADER_BG}; border: none; }}
QHeaderView::section {{
    background: {HEADER_BG}; color: {INK_2}; padding: 5px 8px; border: none;
    border-right: 1px solid {GRID}; border-bottom: 1px solid {BORDER}; font-weight: 600;
}}
QPushButton {{
    background: {BASE}; color: {INK}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BASE}; color: {INK}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 17px;
    border-left: 1px solid {BORDER}; border-top-right-radius: 4px; background: {HEADER_BG};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 17px;
    border-left: 1px solid {BORDER}; border-bottom-right-radius: 4px; background: {HEADER_BG};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {SECTION_BG}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 8px; height: 5px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 8px; height: 5px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right; width: 18px;
    border: none; border-left: 1px solid {BORDER};
}}
QComboBox::down-arrow {{ image: url({down}); width: 8px; height: 5px; }}
QComboBox QAbstractItemView {{ background: {BASE}; color: {INK}; selection-background-color: {ACCENT}; }}
QCheckBox::indicator, QAbstractItemView::indicator {{
    width: 14px; height: 14px; border: 1px solid {MUTED}; border-radius: 3px; background: {BASE};
}}
QCheckBox::indicator:hover, QAbstractItemView::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT}; image: url({check});
}}
QMenuBar {{ background: {PAGE}; color: {INK}; }}
QMenuBar::item:selected {{ background: {HEADER_BG}; }}
QMenu {{ background: {BASE}; color: {INK}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QStatusBar {{ background: {PAGE}; color: {INK_2}; }}
QToolTip {{ background: {BASE}; color: {INK}; border: 1px solid {BORDER}; padding: 4px; }}
"""
