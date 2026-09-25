"""Template di stampa: un report A4 per ogni scheda, con lo stesso stile dell'app.

I grafici vengono ridisegnati fuori schermo con il tema chiaro (la carta è bianca anche
quando l'app usa il tema scuro), riusando i dati già calcolati: il Monte Carlo non viene
rieseguito.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from ..metrics import STAT_DEFS, daily_correlation, monthly_pnl
from ..montecarlo import RANK_FINAL, RANK_MAX_DD, RANK_RATIO, SHUFFLE
from ..portfolio import Entity, Portfolio
from . import theme
from .analysis_tab import AnalysisTab
from .equity_tab import EquityTab, diverging_color, summary_values
from .montecarlo_tab import HIST_METRICS, MonteCarloTab, kpi_values as mc_kpi_values
from .report import (
    P,
    Cell,
    ChartBlock,
    ChartGrid,
    InfoBar,
    Kpi,
    KpiBlock,
    ReportContent,
    SectionTitle,
    TableBlock,
    TableColumn,
    TableRow,
    TextBlock,
    tone_color,
)
from .stats_tab import kpi_values as stats_kpi_values, table_columns

CHART_SCALE = 2.4  # risoluzione dei grafici rispetto alla dimensione logica (≈ 220 dpi su A4)

RANK_LABELS = {
    RANK_MAX_DD: "Max drawdown",
    RANK_FINAL: "Profitto finale",
    RANK_RATIO: "Profitto / max drawdown",
}


# --------------------------------------------------------------------------- utilità


@contextmanager
def _light():
    """Palette chiara temporanea: i grafici creati qui dentro sono pensati per la carta."""
    previous = theme.MODE
    theme.set_mode("light")
    try:
        yield
    finally:
        theme.set_mode(previous)


def capture_plot(plot: pg.PlotWidget, width: int, height: int, scale: float = CHART_SCALE) -> QImage:
    """Disegna un grafico fuori schermo alla dimensione logica indicata, ad alta risoluzione."""
    plot.setParent(None)
    plot.setAttribute(Qt.WA_DontShowOnScreen, True)
    plot.setMinimumSize(0, 0)
    plot.resize(width, height)
    plot.show()
    for _ in range(2):  # geometria e layout interni del grafico
        QApplication.sendPostedEvents()
    image = QImage(round(width * scale), round(height * scale), QImage.Format_RGB32)
    image.setDevicePixelRatio(scale)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    plot.scene().render(painter, QRectF(0, 0, width, height), QRectF(plot.viewport().rect()))
    painter.end()
    plot.hide()
    image.setDevicePixelRatio(1.0)
    return image


def _swatch(entity: Entity) -> tuple[str, Qt.PenStyle]:
    if entity.is_combined:
        return theme.COMBINED, Qt.SolidLine
    return theme.strategy_color(entity.color_index), theme.strategy_line_style(entity.color_index)


def _kpis(values) -> KpiBlock:
    return KpiBlock([Kpi(label, value, tone, sub) for label, value, tone, sub in values])


def _value_cell(kind: str, value, bold: bool = False) -> Cell:
    return Cell(theme.fmt_value(kind, value), color=tone_color(theme.value_tone(kind, value)), bold=bold)


def _period(entity: Entity) -> str:
    st = entity.stats
    return f"{theme.fmt_date(st['period_start'])} → {theme.fmt_date(st['period_end'])}"


def _info_bar(portfolio: Portfolio, entity: Entity, extra: list[tuple[str, str]] | None = None) -> InfoBar:
    n = len(portfolio.strategies)
    items = [
        ("Serie", entity.name),
        ("Strategie incluse", str(n)),
        ("Periodo", _period(entity)),
        ("Trade", f"{len(entity.trades):,}"),
        ("Capitale iniziale", theme.fmt_money(portfolio.capital, 0) if portfolio.capital else "non impostato"),
    ]
    return InfoBar(items + (extra or []))


def _meta(portfolio: Portfolio) -> list[str]:
    names = [e.name for e in portfolio.strategies]
    listed = ", ".join(names[:3]) + (f" e altre {len(names) - 3}" if len(names) > 3 else "")
    return [
        f"Stampato il {datetime.now().strftime('%d/%m/%Y alle %H:%M')}",
        f"Strategie: {listed}",
    ]


def _recovery_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):  # "Non recuperato"
        return value.lower()
    return f"recuperato il {theme.fmt_date(value)}"


def report_filename(content: ReportContent) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", content.title).strip("_")
    return f"NT8_{slug}_{content.created.strftime('%Y-%m-%d_%H%M')}.pdf"


# --------------------------------------------------------------------------- schede


def stats_report(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    primary = portfolio.primary
    if primary.is_combined:
        subtitle = f"Riepilogo del portafoglio combinato ({len(portfolio.strategies)} strategie)"
    else:
        subtitle = f"Riepilogo: {primary.name}"

    cols = table_columns(portfolio)
    entities = portfolio.strategies + ([portfolio.combined] if portfolio.combined else [])
    columns = [TableColumn("Statistica", weight=1.6, align="left")]
    for (name, _stats, _ci), entity in zip(cols, entities):
        columns.append(TableColumn(name, weight=1.0, min_width=80, swatch=_swatch(entity)))
    rows = []
    for definition in STAT_DEFS:
        if definition[0] == "section":
            rows.append(TableRow([Cell(definition[1])], kind="section"))
            continue
        key, label, kind = definition
        cells = [Cell(label)]
        for _name, stats, color_index in cols:
            cells.append(_value_cell(kind, stats.get(key), bold=color_index is None))
        rows.append(TableRow(cells))

    blocks = [
        _info_bar(portfolio, primary),
        _kpis(stats_kpi_values(primary.stats)),
        SectionTitle("Statistiche per strategia" + (" e portafoglio combinato" if portfolio.combined else "")),
        TableBlock(columns, rows, font_size=7.0),
        TextBlock(
            "Drawdown calcolato sull'equity a trade chiusi, dal massimo precedente (partendo da 0). "
            "Il portafoglio combinato ordina i trade di tutte le strategie per orario di uscita."
        ),
    ]
    return ReportContent("Statistiche", subtitle, blocks, _meta(portfolio))


def equity_report(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: EquityTab = window.equity_tab
    primary = portfolio.primary
    hidden = {s.entity.key for s in live.series if not s.visible}

    tab = EquityTab()
    tab.show_single_dd.setChecked(live.show_single_dd.isChecked())
    tab.update_portfolio(portfolio)
    for s in tab.series:
        s.visible = s.entity.key not in hidden
    tab._apply_visibility()
    eq_image = capture_plot(tab.eq_plot, 1000, 370)
    dd_image = capture_plot(tab.dd_plot, 1000, 250)

    st = primary.stats
    who = "combinato" if primary.is_combined else ""
    dd_pct = f"{theme.fmt_pct(st['max_dd_pct'])} dal picco" if st["max_dd_pct"] is not None else ""
    worst_day = st["worst_day"]
    kpis = [
        ("Profitto netto" + (" combinato" if who else ""), theme.fmt_money(st["net_profit"]),
         theme.value_tone("money", st["net_profit"]), f"{st['n_trades']:,} trade"),
        ("Max drawdown" + (" combinato" if who else ""), theme.fmt_money(-st["max_dd"]) if st["max_dd"] else "$0.00",
         -1 if st["max_dd"] else 0, dd_pct),
        ("Inizio → minimo DD", f"{theme.fmt_date(st['max_dd_start'])}", 0, f"minimo il {theme.fmt_date(st['max_dd_trough'])}"),
        ("Durata max DD", f"{theme.fmt_ratio(st['max_dd_days'], 1)} gg", 0,
         _recovery_text(st.get("max_dd_recovery"))),
        ("Recovery factor", theme.fmt_ratio(st["recovery_factor"]), 0, "profitto / max DD"),
        ("Peggior giorno", theme.fmt_money(worst_day[0]) if worst_day else "—",
         -1 if worst_day and worst_day[0] < 0 else 0, theme.fmt_date(worst_day[1]) if worst_day else ""),
        ("Max perdite consecutive", f"{st['max_consec_losses']}", 0,
         f"{theme.fmt_money(st['max_consec_loss_amount'])} in totale"),
    ]

    columns = [TableColumn("Serie", weight=2.0, align="left")]
    columns += [TableColumn(header) for header in EquityTab.COLUMNS[1:]]
    rows = []
    for s in tab.series:
        combined = s.entity.is_combined
        name = s.entity.name + ("" if s.visible else " (nascosta)")
        cells = [Cell(name, swatch=(s.color, s.style), color=None if s.visible else P["MUTED"])]
        cells += [_value_cell(kind, value) for kind, value in summary_values(s.entity.stats)]
        rows.append(TableRow(cells, kind="highlight" if combined else "normal"))

    if primary.is_combined:
        subtitle = (f"{len(portfolio.strategies)} strategie · curva nera = portafoglio combinato · "
                    f"max drawdown combinato {theme.fmt_money(-st['max_dd'])}")
    else:
        subtitle = f"{primary.name} · max drawdown {theme.fmt_money(-st['max_dd'])}"
    blocks = [
        _info_bar(portfolio, primary),
        _kpis(kpis),
        ChartBlock(eq_image),
        ChartBlock(dd_image),
        SectionTitle("Max drawdown e risultati per strategia"),
        TableBlock(columns, rows, font_size=7.0),
    ]
    if len(portfolio.strategies) >= 2:
        matrix = daily_correlation([e.trades for e in portfolio.strategies])
        n = len(portfolio.strategies)
        corr_cols = [TableColumn("Strategia", weight=3.0, align="left")]
        corr_cols += [TableColumn(str(j + 1), weight=1.0, align="center", min_width=40) for j in range(n)]
        corr_rows = []
        for i, e in enumerate(portfolio.strategies):
            cells = [Cell(f"{i + 1}. {e.name}", swatch=_swatch(e))]
            for j in range(n):
                v = float(matrix[i, j])
                cells.append(Cell(f"{v:+.2f}", bg=diverging_color(v).name(), bold=i == j))
            corr_rows.append(TableRow(cells))
        blocks += [
            SectionTitle("Correlazione del P&L giornaliero"),
            TableBlock(corr_cols, corr_rows, font_size=7.0),
            TextBlock("Valori bassi o negativi (blu) indicano strategie che si diversificano; "
                      "valori vicini a +1 (rosso) indicano strategie che guadagnano e perdono negli stessi giorni."),
        ]
    blocks.append(TextBlock(
        "Equity a trade chiusi. Drawdown dal massimo precedente, partendo da 0. "
        "▼ picco e ▲ minimo del max drawdown della serie principale."
    ))
    return ReportContent("Equity curve", subtitle, blocks, _meta(portfolio))


def montecarlo_report(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: MonteCarloTab = window.mc_tab
    r = live.result
    if r is None:
        entity = portfolio.entity(live.series_box.currentData()) or portfolio.primary
        blocks = [
            _info_bar(portfolio, entity),
            TextBlock("Nessuna simulazione disponibile: premi «Esegui simulazione» nella scheda Monte Carlo "
                      "e poi stampa di nuovo.", size=10, color=P["INK"]),
        ]
        return ReportContent("Monte Carlo", entity.name, blocks, _meta(portfolio))

    tab = MonteCarloTab()
    tab.portfolio = portfolio
    tab.threshold.setValue(live.threshold.value())
    tab.show_original.setChecked(live.show_original.isChecked())
    tab.hist_metric.setCurrentIndex(live.hist_metric.currentIndex())
    tab.result = r
    tab._entity_name = live._entity_name
    tab._draw()
    main_image = capture_plot(tab.plot, 1000, 390)
    hist_image = capture_plot(tab.hist_plot, 1000, 290)

    method = "Shuffle (rimescola l'ordine)" if r.config.method == SHUFFLE else "Bootstrap (con reinserimento)"
    seed = str(r.config.seed) if r.config.seed else "casuale"
    threshold = live.threshold.value()
    info = InfoBar([
        ("Serie", live._entity_name),
        ("Metodo", method),
        ("Simulazioni", f"{r.n_sims:,}"),
        ("Trade per simulazione", f"{r.n_trades:,}"),
        ("Migliore / peggiore per", RANK_LABELS.get(r.config.rank_by, "")),
        ("Soglia DD", theme.fmt_money(threshold, 0) if threshold else "—"),
        ("Seed", seed),
    ])

    colored = {1: P["POSITIVE_TEXT"], 2: P["MC_MEAN"], 6: P["NEGATIVE_TEXT"]}
    headers = ["Metrica", "Migliore", "Media", "Mediana", "Conf. 95%", "Conf. 99%", "Peggiore", "Originale"]
    columns = [TableColumn(headers[0], weight=2.0, align="left")]
    columns += [TableColumn(h, color=colored.get(c)) for c, h in enumerate(headers[1:], start=1)]
    rows = []
    for s in r.summary:
        important = s.key in ("max_dd", "max_consec_loss_amount", "largest_loss")
        cells = [Cell(s.label, bold=important)]
        for c, v in enumerate([s.best, s.mean, s.median, s.conf95, s.conf99, s.worst, s.original], start=1):
            kind = "num" if (s.kind == "int" and c == 2) else s.kind
            cells.append(Cell(theme.fmt_value(kind, v), color=colored.get(c), bold=important and c in colored))
        rows.append(TableRow(cells))

    hist_label = next(label for key, label, _ in HIST_METRICS if key == tab.hist_metric.currentData())
    subtitle = (f"{live._entity_name} · {r.n_sims:,} simulazioni · "
                f"{'Shuffle' if r.config.method == SHUFFLE else 'Bootstrap'} · {r.n_trades:,} trade")
    blocks = [
        info,
        _kpis(mc_kpi_values(r, threshold)),
        ChartBlock(main_image),
        TextBlock("Grigio: tutte le simulazioni · verde: migliore · rosso: peggiore · blu: media"
                  + (" · arancione tratteggiata: sequenza originale" if live.show_original.isChecked() else "")
                  + f" (migliore e peggiore scelte per {RANK_LABELS.get(r.config.rank_by, '').lower()})."),
        SectionTitle("Statistiche Monte Carlo: caso migliore, medio e peggiore"),
        TableBlock(columns, rows, font_size=7.2),
        TextBlock(live.note.text()),
        ChartBlock(hist_image, title=f"Distribuzione delle simulazioni: {hist_label}"),
    ]
    return ReportContent("Monte Carlo", subtitle, blocks, _meta(portfolio))


def analysis_report(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: AnalysisTab = window.analysis_tab
    entity = portfolio.entity(live.selector.currentData()) or portfolio.primary

    tab = AnalysisTab()
    tab.update_portfolio(portfolio)
    index = tab.selector.findData(entity.key)
    if index >= 0 and index != tab.selector.currentIndex():
        tab.selector.setCurrentIndex(index)
    plots = [tab.eq_plot, tab.dd_plot, tab.trade_plot, tab.hist_plot, tab.month_plot,
             tab.hour_plot, tab.weekday_plot, tab.exit_plot, tab.mae_plot]
    charts = [ChartBlock(capture_plot(p, 540, 250)) for p in plots]

    months, pnl = monthly_pnl(entity.trades)
    years = sorted({int(str(m)[:4]) for m in months})
    lookup = {str(m): float(v) for m, v in zip(months, pnl)}
    columns = [TableColumn("Anno", weight=0.8, align="left")]
    columns += [TableColumn(m.capitalize()) for m in theme.MONTHS_IT]
    columns.append(TableColumn("Totale", weight=1.2))
    rows = []
    for year in years:
        cells = [Cell(str(year), bold=True)]
        total = 0.0
        for mi in range(12):
            v = lookup.get(f"{year}-{mi + 1:02d}")
            if v is None:
                cells.append(Cell(""))
            else:
                total += v
                cells.append(Cell(theme.fmt_money(v, 0), color=tone_color(theme.value_tone("money", v))))
        cells.append(Cell(theme.fmt_money(total, 0), color=tone_color(theme.value_tone("money", total)), bold=True))
        rows.append(TableRow(cells))

    blocks = [
        _info_bar(portfolio, entity),
        _kpis(stats_kpi_values(entity.stats)),
        ChartGrid(charts, columns=2),
        SectionTitle("P&L mensile per anno"),
        TableBlock(columns, rows, font_size=7.0),
    ]
    return ReportContent("Analisi grafica", entity.name, blocks, _meta(portfolio))


def trades_report(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live = window.trades_tab
    entity = portfolio.entity(live.selector.currentData()) or portfolio.primary
    ts = entity.trades
    cum = np.cumsum(ts.profit)
    dd = cum - np.maximum.accumulate(np.maximum(cum, 0.0)) if len(ts) else np.array([])
    st = entity.stats

    spec = [("#", 0.4, "right")]
    if entity.is_combined:
        spec.append(("Strategia", 1.6, "left"))
    spec += [
        ("Direzione", 0.8, "left"),
        ("Qtà", 0.4, "right"),
        ("Entrata", 1.0, "right"),
        ("Uscita", 1.0, "right"),
        ("Prezzo entrata", 1.0, "right"),
        ("Prezzo uscita", 1.0, "right"),
        ("Profitto", 1.0, "right"),
        ("Cumulativo", 1.0, "right"),
        ("Drawdown", 1.0, "right"),
        ("Nome uscita", 1.4, "left"),
        ("MAE", 0.8, "right"),
        ("MFE", 0.8, "right"),
    ]
    columns = [TableColumn(h, weight=w, align=a) for h, w, a in spec]

    def money(v: float) -> Cell:
        return Cell(theme.fmt_money(v), color=tone_color(theme.value_tone("money", v)))

    rows = []
    for i in range(len(ts)):
        cells = [Cell(str(i + 1))]
        if entity.is_combined:
            cells.append(Cell(str(ts.strategy[i])))
        cells += [
            Cell(str(ts.market_pos[i])),
            Cell(f"{float(ts.qty[i]):g}"),
            Cell(theme.fmt_datetime(ts.entry_time[i])),
            Cell(theme.fmt_datetime(ts.exit_time[i])),
            Cell(f"{float(ts.entry_price[i]):,.2f}"),
            Cell(f"{float(ts.exit_price[i]):,.2f}"),
            money(float(ts.profit[i])),
            money(float(cum[i])),
            money(float(dd[i])),
            Cell(str(ts.exit_name[i])),
            Cell(theme.fmt_money(float(ts.mae[i]))),
            Cell(theme.fmt_money(float(ts.mfe[i]))),
        ]
        rows.append(TableRow(cells))

    kpis = [
        ("Numero trade", f"{st['n_trades']:,}", 0, f"{st['n_wins']} V / {st['n_losses']} P"),
        ("Profitto netto", theme.fmt_money(st["net_profit"]), theme.value_tone("money", st["net_profit"]), ""),
        ("% vincenti", theme.fmt_pct(st["win_rate"], 1), 0, ""),
        ("Miglior trade", theme.fmt_money(st["largest_win"]), theme.value_tone("money", st["largest_win"]), ""),
        ("Peggior trade", theme.fmt_money(st["largest_loss"]), theme.value_tone("money", st["largest_loss"]), ""),
        ("Max drawdown", theme.fmt_money(-st["max_dd"]) if st["max_dd"] else "$0.00", -1 if st["max_dd"] else 0, ""),
        ("Expectancy / trade", theme.fmt_money(st["avg_trade"]), theme.value_tone("money", st["avg_trade"]), ""),
    ]
    label = "Tutte le strategie (ordine combinato)" if entity.is_combined else entity.name
    blocks = [
        _info_bar(portfolio, entity),
        _kpis(kpis),
        TableBlock(columns, rows, font_size=6.8, title=f"{len(ts):,} trade in ordine di uscita"),
        TextBlock("Cumulativo e drawdown sono calcolati sulla selezione stampata, nell'ordine di uscita dei trade."),
    ]
    return ReportContent("Lista trade", f"{label} · {len(ts):,} trade", blocks, _meta(portfolio))


# --------------------------------------------------------------------------- ingresso

BUILDERS = {
    "stats_tab": stats_report,
    "equity_tab": equity_report,
    "mc_tab": montecarlo_report,
    "analysis_tab": analysis_report,
    "trades_tab": trades_report,
}


def build_report(window, index: int | None = None) -> ReportContent | None:
    """Report della scheda ``index`` (default: quella visibile). ``None`` se non ci sono dati."""
    if window.portfolio is None or window.portfolio.empty:
        return None
    index = window.tabs.currentIndex() if index is None else index
    page = window.tabs.widget(index)
    page = page.widget() if hasattr(page, "widget") else page
    for attr, builder in BUILDERS.items():
        if getattr(window, attr) is page:
            with _light():
                return builder(window)
    return None

