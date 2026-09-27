"""Modelli di stampa: una sezione per ogni scheda, con lo stesso stile dell'app.

I grafici vengono ricreati fuori schermo con il tema chiaro (la carta è bianca anche quando
l'app usa il tema scuro) partendo dai dati già calcolati: il Monte Carlo non viene rieseguito.
Ogni grafico viene ridisegnato alla dimensione adatta al foglio, verticale o orizzontale.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime

import numpy as np
import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from ..metrics import STAT_DEFS, daily_correlation, monthly_pnl
from ..montecarlo import RANK_FINAL, RANK_MAX_DD, RANK_RATIO, SHUFFLE
from ..portfolio import Entity, Portfolio
from ..propfirm import RISK_MEASURES, SORT_KEYS, STATUS_OK, STATUS_OVER, STATUS_TOLERATED
from . import theme
from .analysis_tab import AnalysisTab
from .equity_tab import EquityTab, diverging_color, summary_values
from .montecarlo_tab import HIST_METRICS, MonteCarloTab, kpi_values as mc_kpi_values
from .report import (
    P,
    Cell,
    ChartBlock,
    ChartGrid,
    Document,
    InfoBar,
    Kpi,
    KpiBlock,
    PageBreak,
    ReportContent,
    SectionTitle,
    TableBlock,
    TableColumn,
    TableRow,
    TextBlock,
    TocBlock,
    tone_color,
)
from .propfirm_tab import (
    OPT_HEADERS,
    OUTCOME_HEADERS,
    TIMING_HEADERS,
    PropFirmTab,
    fmt_timing,
    kpi_values,
    method_note,
    optimization_rows,
    optimization_summary,
    outcome_rows,
    rules_text,
    timing_rows,
)
from .stats_tab import kpi_values as stats_kpi_values, table_columns
from .widgets import forget, set_category_ticks

RANK_LABELS = {
    RANK_MAX_DD: "Max drawdown",
    RANK_FINAL: "Profitto finale",
    RANK_RATIO: "Profitto / max drawdown",
}
LEGEND_IN_CHART_MAX = 6  # oltre questo numero di curve la legenda va sotto il grafico


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


def capture_plot(plot: pg.PlotWidget, width: int, height: int, scale: float = 2.4) -> QImage:
    """Disegna un grafico fuori schermo alla dimensione logica indicata, ad alta risoluzione."""
    plot.resize(width, height)
    plot.show()
    for _ in range(2):  # geometria e layout interni del grafico
        QApplication.sendPostedEvents()
    image = QImage(max(1, round(width * scale)), max(1, round(height * scale)), QImage.Format_RGB32)
    image.setDevicePixelRatio(scale)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    plot.scene().render(painter, QRectF(0, 0, width, height), QRectF(plot.viewport().rect()))
    painter.end()
    plot.hide()
    image.setDevicePixelRatio(1.0)
    return image


class PlotRenderer:
    """Ridisegna un grafico fuori schermo alle dimensioni chieste dall'impaginazione (con cache)."""

    def __init__(self, plot: pg.PlotWidget, before=None):
        self.plot = plot
        self.before = before  # before(larghezza): adatta il grafico (es. etichette) alla dimensione
        self._cache: dict[tuple, QImage] = {}
        plot.setParent(None)
        plot.setAttribute(Qt.WA_DontShowOnScreen, True)
        plot.setMinimumSize(0, 0)
        forget(plot)  # resta con i colori chiari anche se l'app cambia tema

    def __call__(self, width: int, height: int, dpr: float) -> QImage:
        key = (width, height, round(dpr, 2))
        if key not in self._cache:
            if self.before is not None:
                self.before(width)
            self._cache[key] = capture_plot(self.plot, width, height, dpr)
        return self._cache[key]


def _offscreen(window, key: str, factory):
    """Schede fuori schermo usate per disegnare i grafici della stampa: create una volta per finestra
    e riusate. Ricrearle e scartarle a ogni stampa lascerebbe al garbage collector la distruzione
    di widget con eventi Qt ancora in coda."""
    cache = window.__dict__.setdefault("_print_tabs", {})
    if key not in cache:
        tab = factory()
        # i grafici verranno staccati dalla scheda per disegnarli fuori schermo: si tengono in elenco
        # per poterli eliminare insieme alla finestra
        cache.setdefault("_widgets", []).extend([tab, *tab.findChildren(pg.PlotWidget)])
        cache[key] = tab
    return cache[key]


def release_offscreen(window) -> None:
    """Elimina le schede e i grafici fuori schermo usati per la stampa (alla chiusura della finestra)."""
    cache = window.__dict__.pop("_print_tabs", {})
    for widget in cache.get("_widgets", []):
        if shiboken6.isValid(widget):
            widget.deleteLater()


def _chart(plot: pg.PlotWidget, aspect: float, aspect_portrait: float | None = None, title: str = "",
           label: str | None = None, before=None) -> ChartBlock:
    return ChartBlock(title=title, render=PlotRenderer(plot, before), aspect=aspect, aspect_portrait=aspect_portrait,
                      label=label if label is not None else (getattr(plot, "_nt8_title", "") or title))


def _category_ticks(plot: pg.PlotWidget, labels: list[str], px_per_label: int = 64):
    """Etichette dell'asse orizzontale diradate in base alla larghezza del grafico stampato."""

    def before(width: int) -> None:
        set_category_ticks(plot, labels, max_labels=max(3, width // px_per_label))

    return before


def _swatch(entity: Entity) -> tuple[str, Qt.PenStyle]:
    if entity.is_combined:
        return theme.COMBINED, Qt.SolidLine
    return theme.strategy_color(entity.color_index), theme.strategy_line_style(entity.color_index)


def _kpis(values) -> KpiBlock:
    return KpiBlock([Kpi(label, value, tone, sub) for label, value, tone, sub in values])


def _value_cell(kind: str, value, bold: bool = False, compact: bool = False) -> Cell:
    text = _compact_value(kind, value) if compact else theme.fmt_value(kind, value)
    return Cell(text, color=tone_color(theme.value_tone(kind, value)), bold=bold)


def _compact_value(kind: str, value) -> str:
    """Come nell'app ma con date più corte (gg/mm/aa): colonne più strette, più strategie per foglio."""
    if value is None or isinstance(value, str):
        return theme.fmt_value(kind, value)
    if kind == "datetime":
        return np.datetime64(value, "s").item().strftime("%d/%m/%y %H:%M")
    if kind == "money_date":
        return f"{theme.fmt_money(value[0])} ({np.datetime64(value[1], 'D').item().strftime('%d/%m/%y')})"
    if kind == "money_month":
        d = np.datetime64(value[1], "M").item()
        return f"{theme.fmt_money(value[0])} ({theme.MONTHS_IT[d.month - 1]} {d.year % 100:02d})"
    return theme.fmt_value(kind, value)


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


def report_filename(content: ReportContent | Document) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", content.title).strip("_")
    return f"NT8_{slug}_{content.created.strftime('%Y-%m-%d_%H%M')}.pdf"


# --------------------------------------------------------------------------- sezioni


def stats_section(window, with_correlation: bool = True) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    primary = portfolio.primary
    n = len(portfolio.strategies)
    if primary.is_combined:
        subtitle = f"Riepilogo del portafoglio combinato ({n} strategie)"
    else:
        subtitle = f"Riepilogo: {primary.name}"

    cols = table_columns(portfolio)
    entities = portfolio.strategies + ([portfolio.combined] if portfolio.combined else [])
    columns = [TableColumn("Statistica", weight=1.6, align="left")]
    for (name, _stats, _ci), entity in zip(cols, entities):
        columns.append(TableColumn(name, weight=1.0, min_width=64, swatch=_swatch(entity)))
    rows = []
    for definition in STAT_DEFS:
        if definition[0] == "section":
            rows.append(TableRow([Cell(definition[1])], kind="section"))
            continue
        key, label, kind = definition
        cells = [Cell(label)]
        for _name, stats, color_index in cols:
            cells.append(_value_cell(kind, stats.get(key), bold=color_index is None, compact=True))
        rows.append(TableRow(cells))
    # il portafoglio combinato viene ripetuto in ogni gruppo di strategie, per confrontarlo
    repeat = (len(columns) - 1,) if portfolio.combined is not None else ()

    blocks = [
        _info_bar(portfolio, primary),
        _kpis(stats_kpi_values(primary.stats)),
        SectionTitle("Statistiche per strategia" + (" e portafoglio combinato" if portfolio.combined else "")),
        TableBlock(columns, rows, font_size=7.0, repeat=repeat, group_noun="strategie"),
        TextBlock(
            "Drawdown calcolato sull'equity a trade chiusi, dal massimo precedente (partendo da 0). "
            "Il portafoglio combinato ordina i trade di tutte le strategie per orario di uscita."
            + (" Con molte strategie la tabella è divisa in gruppi, ognuno con tutte le righe e con la colonna "
               "del portafoglio combinato." if n > 4 else "")
        ),
    ]
    if with_correlation and n >= 2:
        blocks += _correlation_blocks(portfolio)
    return ReportContent("Statistiche", subtitle, blocks, _meta(portfolio), key="stats")


def _correlation_blocks(portfolio: Portfolio, pairs: bool = False) -> list:
    strategies = portfolio.strategies
    n = len(strategies)
    matrix = daily_correlation([e.trades for e in strategies])
    columns = [TableColumn("Strategia", weight=3.0, align="left")]
    columns += [TableColumn(str(j + 1), weight=1.0, align="center", min_width=34) for j in range(n)]
    columns.append(TableColumn("Media", weight=1.1, align="center", min_width=40))
    rows = []
    for i, e in enumerate(strategies):
        cells = [Cell(f"{i + 1}. {e.name}", swatch=_swatch(e))]
        for j in range(n):
            v = float(matrix[i, j])
            cells.append(Cell(f"{v:+.2f}", bg=diverging_color(v).name(), bold=i == j))
        others = np.delete(matrix[i], i)
        mean = float(others.mean()) if len(others) else 0.0
        cells.append(Cell(f"{mean:+.2f}", bold=True, bg=diverging_color(mean).name()))
        rows.append(TableRow(cells))
    blocks = [
        SectionTitle("Correlazione del P&L giornaliero fra strategie"),
        TableBlock(columns, rows, font_size=7.0, group_noun="strategie", repeat=(n + 1,)),
        TextBlock("Correlazione di Pearson del P&L giornaliero (i giorni senza trade valgono 0). "
                  "Blu: correlazione bassa o negativa, le strategie si diversificano. Rosso: vicina a +1, "
                  "guadagnano e perdono negli stessi giorni. «Media» è la correlazione media con le altre strategie."),
    ]
    if pairs and n >= 3:
        pair_list = [(float(matrix[i, j]), i, j) for i in range(n) for j in range(i + 1, n)]
        pair_list.sort(reverse=True)
        k = min(5, len(pair_list) // 2 or 1)
        pair_cols = [TableColumn("Coppia", weight=3.0, align="left"), TableColumn("Correlazione", align="center")]

        def pair_rows(items):
            return [TableRow([Cell(f"{i + 1}. {strategies[i].name}  ↔  {j + 1}. {strategies[j].name}"),
                              Cell(f"{v:+.2f}", bg=diverging_color(v).name(), bold=True)]) for v, i, j in items]

        blocks += [
            TableBlock(pair_cols, pair_rows(pair_list[:k]), title="Coppie più correlate", font_size=7.0),
            TableBlock(pair_cols, pair_rows(pair_list[::-1][:k]), title="Coppie meno correlate (più diversificazione)",
                       font_size=7.0),
        ]
    return blocks


def correlation_section(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    n = len(portfolio.strategies)
    blocks = _correlation_blocks(portfolio, pairs=True) if n >= 2 else [
        TextBlock("La correlazione richiede almeno 2 strategie.", size=10, color=P["INK"])]
    return ReportContent("Correlazione fra strategie", f"P&L giornaliero · {n} strategie", blocks,
                         _meta(portfolio), key="correlation")


def equity_section(window, with_correlation: bool = True) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: EquityTab = window.equity_tab
    primary = portfolio.primary
    hidden = {s.entity.key for s in live.series if not s.visible}

    tab = _offscreen(window, "equity", EquityTab)
    tab.eq_legend.show()
    tab.show_single_dd.setChecked(live.show_single_dd.isChecked())
    tab.update_portfolio(portfolio)
    for s in tab.series:
        s.visible = s.entity.key not in hidden
    tab._apply_visibility()
    visible = [s for s in tab.series if s.visible]
    legend_below = len(visible) > LEGEND_IN_CHART_MAX
    if legend_below:
        tab.eq_legend.hide()

    st = primary.stats
    combined = " combinato" if primary.is_combined else ""
    dd_pct = f"{theme.fmt_pct(st['max_dd_pct'])} dal picco" if st["max_dd_pct"] is not None else ""
    worst_day = st["worst_day"]
    kpis = [
        ("Profitto netto" + combined, theme.fmt_money(st["net_profit"]),
         theme.value_tone("money", st["net_profit"]), f"{st['n_trades']:,} trade"),
        ("Max drawdown" + combined, theme.fmt_money(-st["max_dd"]) if st["max_dd"] else "$0.00",
         -1 if st["max_dd"] else 0, dd_pct),
        ("Inizio → minimo DD", f"{theme.fmt_date(st['max_dd_start'])}", 0, f"minimo il {theme.fmt_date(st['max_dd_trough'])}"),
        ("Durata max DD", f"{theme.fmt_ratio(st['max_dd_days'], 1)} gg", 0, _recovery_text(st.get("max_dd_recovery"))),
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
        name = s.entity.name + ("" if s.visible else " (nascosta)")
        cells = [Cell(name, swatch=(s.color, s.style), color=None if s.visible else P["MUTED"])]
        cells += [_value_cell(kind, value) for kind, value in summary_values(s.entity.stats)]
        rows.append(TableRow(cells, kind="highlight" if s.entity.is_combined else "normal"))

    if primary.is_combined:
        subtitle = (f"{len(portfolio.strategies)} strategie · curva nera = portafoglio combinato · "
                    f"max drawdown combinato {theme.fmt_money(-st['max_dd'])}")
    else:
        subtitle = f"{primary.name} · max drawdown {theme.fmt_money(-st['max_dd'])}"
    equity_chart = _chart(tab.eq_plot, 0.37, 0.62, label="Equity curve")
    if legend_below:
        equity_chart.legend = [(s.entity.name, s.color, s.style) for s in visible]
    blocks = [
        _info_bar(portfolio, primary),
        _kpis(kpis),
        equity_chart,
        _chart(tab.dd_plot, 0.25, 0.4, label="Drawdown"),
        TextBlock("Equity a trade chiusi. Drawdown dal massimo precedente, partendo da 0. "
                  "▼ picco e ▲ minimo del max drawdown della serie principale."),
        SectionTitle("Max drawdown e risultati per strategia"),
        TableBlock(columns, rows, font_size=7.0),
    ]
    if with_correlation and len(portfolio.strategies) >= 2:
        blocks += _correlation_blocks(portfolio)
    return ReportContent("Equity curve", subtitle, blocks, _meta(portfolio), key="equity")


def montecarlo_section(window) -> ReportContent:
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
        return ReportContent("Monte Carlo", entity.name, blocks, _meta(portfolio), key="montecarlo")

    tab = _offscreen(window, "montecarlo", MonteCarloTab)
    tab.result = None
    tab.portfolio = portfolio
    tab.threshold.setValue(live.threshold.value())
    tab.show_original.setChecked(live.show_original.isChecked())
    tab.hist_metric.setCurrentIndex(live.hist_metric.currentIndex())
    tab.result = r
    tab._entity_name = live._entity_name
    tab._draw()
    # i dettagli sono già nell'intestazione: sul foglio verticale il titolo lungo non entrerebbe
    tab.plot.getPlotItem().setTitle("Simulazioni Monte Carlo", color=theme.INK, size="10.5pt", bold=True)

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
        _chart(tab.plot, 0.39, 0.62, label="Simulazioni"),
        TextBlock("Grigio: tutte le simulazioni · verde: migliore · rosso: peggiore · blu: media"
                  + (" · arancione tratteggiata: sequenza originale" if live.show_original.isChecked() else "")
                  + f" (migliore e peggiore scelte per {RANK_LABELS.get(r.config.rank_by, '').lower()})."),
        SectionTitle("Statistiche Monte Carlo: caso migliore, medio e peggiore"),
        TableBlock(columns, rows, font_size=7.2),
        TextBlock(live.note.text()),
        _chart(tab.hist_plot, 0.29, 0.45, title=f"Distribuzione delle simulazioni: {hist_label}"),
    ]
    return ReportContent("Monte Carlo", subtitle, blocks, _meta(portfolio), key="montecarlo")


def analysis_section(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: AnalysisTab = window.analysis_tab
    entity = portfolio.entity(live.selector.currentData()) or portfolio.primary

    tab = _offscreen(window, "analysis", AnalysisTab)
    tab.update_portfolio(portfolio)
    index = tab.selector.findData(entity.key)
    if index >= 0 and index != tab.selector.currentIndex():
        tab.selector.setCurrentIndex(index)
    months, pnl = monthly_pnl(entity.trades)
    short_months = [f"{theme.MONTHS_IT[int(str(m)[5:7]) - 1]} {str(m)[2:4]}" for m in months]
    plots = [tab.eq_plot, tab.dd_plot, tab.trade_plot, tab.hist_plot, tab.month_plot,
             tab.hour_plot, tab.weekday_plot, tab.exit_plot, tab.mae_plot]
    befores = {id(tab.month_plot): _category_ticks(tab.month_plot, short_months)}
    charts = [_chart(p, 0.46, 0.66, before=befores.get(id(p))) for p in plots]

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
    return ReportContent("Analisi grafica", entity.name, blocks, _meta(portfolio), key="analysis")


def trades_section(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live = window.trades_tab
    entity = portfolio.entity(live.selector.currentData()) or portfolio.primary
    ts = entity.trades
    cum = np.cumsum(ts.profit)
    dd = cum - np.maximum.accumulate(np.maximum(cum, 0.0)) if len(ts) else np.array([])
    st = entity.stats

    # (intestazione, peso, allineamento, priorità: le più alte si omettono per prime sul foglio verticale)
    spec = [("#", 0.4, "right", 0)]
    if entity.is_combined:
        spec.append(("Strategia", 1.6, "left", 0))
    spec += [
        ("Direzione", 0.8, "left", 0),
        ("Qtà", 0.4, "right", 1),
        ("Entrata", 1.0, "right", 0),
        ("Uscita", 1.0, "right", 0),
        ("Prezzo entrata", 1.0, "right", 2),
        ("Prezzo uscita", 1.0, "right", 2),
        ("Profitto", 1.0, "right", 0),
        ("Cumulativo", 1.0, "right", 0),
        ("Drawdown", 1.0, "right", 0),
        ("Nome uscita", 1.4, "left", 1),
        ("MAE", 0.8, "right", 3),
        ("MFE", 0.8, "right", 3),
    ]
    columns = [TableColumn(h, weight=w, align=a, priority=pr) for h, w, a, pr in spec]

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
    return ReportContent("Lista trade", f"{label} · {len(ts):,} trade", blocks, _meta(portfolio), key="trades")


def propfirm_section(window) -> ReportContent:
    portfolio: Portfolio = window.portfolio
    live: PropFirmTab = window.prop_tab
    r = live.result
    if r is None:
        entity = portfolio.entity(live.series_box.currentData()) or portfolio.primary
        blocks = [
            _info_bar(portfolio, entity),
            TextBlock("Nessuna simulazione disponibile: premi «Esegui simulazione» nella scheda Prop Firm "
                      "e poi stampa di nuovo.", size=10, color=P["INK"]),
        ]
        return ReportContent("Prop Firm", entity.name, blocks, _meta(portfolio), key="propfirm")

    tab = _offscreen(window, "propfirm", PropFirmTab)
    tab._auto_timer.stop()
    tab.portfolio = portfolio
    tab.result = r
    tab._entity_name = live._entity_name
    tab._draw()
    tab._auto_timer.stop()

    outcome_cols = [TableColumn(OUTCOME_HEADERS[0], weight=2.2, align="left")]
    outcome_cols += [TableColumn(h) for h in OUTCOME_HEADERS[1:]]
    tones = [P["POSITIVE_TEXT"], P["NEGATIVE_TEXT"], P["NEGATIVE_TEXT"], P["INK"]]
    outcomes = [TableRow([Cell(label, bold=i == 0), Cell(ev, color=tones[i], bold=i == 0),
                          Cell(fu, color=tones[i], bold=i == 0)]) for i, (label, ev, fu) in enumerate(outcome_rows(r))]
    timing_cols = [TableColumn(TIMING_HEADERS[0], weight=2.4, align="left")]
    timing_cols += [TableColumn(h) for h in TIMING_HEADERS[1:]]
    timing = []
    for label, kind, values in timing_rows(r):
        cells = [Cell(label)]
        for v in values:
            color = tone_color(theme.value_tone("money", v)) if kind == "money" and np.isfinite(v) else None
            cells.append(Cell(fmt_timing(kind, v), color=color))
        timing.append(TableRow(cells))

    cfg = r.config
    subtitle = (f"{live._entity_name} · account {theme.fmt_money(cfg.account_size, 0)} · target "
                f"{theme.fmt_money(cfg.profit_target, 0)} · drawdown {theme.fmt_money(cfg.max_drawdown, 0)} · "
                f"{r.n_sims:,} simulazioni")
    blocks = [
        InfoBar([("Serie", live._entity_name)] + rules_text(cfg)),
        _kpis(kpi_values(r)),
        _chart(tab.plot, 0.37, 0.62, label="Simulazioni della valutazione"),
        TextBlock("Verde: valutazioni passate · rosso: bocciate · grigio: non concluse (al massimo 250 percorsi). "
                  "Linee tratteggiate: target e limite di drawdown iniziale. " + method_note(r, short=True)),
        SectionTitle("Esiti e tempi"),
        TableBlock(outcome_cols, outcomes, title="Esiti delle simulazioni", font_size=7.2),
        TableBlock(timing_cols, timing, title="Tempi (giornate di trading) e importo del primo payout", font_size=7.2),
        ChartGrid([_chart(tab.pass_plot, 0.55, 0.75), _chart(tab.payout_plot, 0.55, 0.75)], columns=2),
    ]
    if live.optimization is not None:
        blocks += _optimization_blocks(live.optimization)
    return ReportContent("Prop Firm", subtitle, blocks, _meta(portfolio), key="propfirm")


def _optimization_blocks(res) -> list:
    """Classifica delle combinazioni di strategie (se è stata calcolata nella scheda Prop Firm)."""
    o = res.options
    status_colors = {STATUS_OK: P["POSITIVE_TEXT"], STATUS_TOLERATED: P["MC_CONF"], STATUS_OVER: P["NEGATIVE_TEXT"]}
    priorities = {"Giornate quando passa": 1, "Tempo al payout": 1, "Account medi": 2, "DD valutazione 95%": 2, "N.": 3}
    columns = [TableColumn(h, weight=3.0 if h == "Strategie" else 1.0, align="left" if h in ("Strategie", "Rischio") else "right",
                           priority=priorities.get(h, 0), max_share=0.34 if h == "Strategie" else 0.45)
               for h in OPT_HEADERS]
    rows = []
    for row in optimization_rows(res, limit=20, excluded_limit=5):
        combo = row["combo"]
        cells = []
        for c, text in enumerate(row["cells"]):
            color = status_colors[combo.status] if c == 11 else (P["MUTED"] if combo.status == STATUS_OVER else None)
            cells.append(Cell(text, color=color, bold=combo.refined and c in (0, 1, 3)))
        rows.append(TableRow(cells))
    return [
        PageBreak(),
        SectionTitle("Selezione delle strategie migliori per passare la prop firm"),
        InfoBar([
            ("Drawdown desiderato", theme.fmt_money(o.desired_dd, 0)),
            ("Massimo accettabile", theme.fmt_money(max(o.max_dd, o.desired_dd), 0)),
            ("Misura del rischio", RISK_MEASURES[o.risk_measure]),
            ("Ordinate per", SORT_KEYS[o.sort_by]),
            ("Combinazioni provate", f"{res.tested:,} di {res.total:,}"),
        ]),
        TableBlock(columns, rows, font_size=6.9, title="Combinazioni in ordine di velocità"),
        TextBlock(optimization_summary(res) + " ✓ = verificata con tutte le simulazioni e con il payout. "
                  "«Tempo per passare»: giornate di trading medie per ottenere il conto finanziato, comprando un nuovo "
                  "account a ogni bocciatura. Le combinazioni con rischio fino al massimo accettabile contano solo per "
                  "la velocità."),
    ]


def summary_section(window) -> ReportContent:
    """Prima pagina del resoconto completo: indicatori principali, strategie e indice."""
    portfolio: Portfolio = window.portfolio
    primary = portfolio.primary
    n = len(portfolio.strategies)
    columns = [TableColumn("Strategia", weight=2.4, align="left")]
    headers = ["Trade", "Profitto netto", "Max DD ($)", "Max DD (%)", "Profit factor", "% vincenti", "Expectancy",
               "Sharpe", "Recovery factor"]
    columns += [TableColumn(h) for h in headers]
    rows = []
    for e in portfolio.strategies + ([portfolio.combined] if portfolio.combined else []):
        st = e.stats
        values = [("int", st["n_trades"]), ("money", st["net_profit"]),
                  ("money", -st["max_dd"] if st["max_dd"] else 0.0), ("pct", st["max_dd_pct"]),
                  ("ratio", st["profit_factor"]), ("pct", st["win_rate"]), ("money", st["avg_trade"]),
                  ("ratio", st["sharpe"]), ("ratio", st["recovery_factor"])]
        cells = [Cell(e.name, swatch=_swatch(e))] + [_value_cell(kind, v) for kind, v in values]
        rows.append(TableRow(cells, kind="highlight" if e.is_combined else "normal"))

    blocks = [_info_bar(portfolio, primary), _kpis(stats_kpi_values(primary.stats))]
    extra = []
    mc = window.mc_tab.result
    if mc is not None:
        values = mc_kpi_values(mc, window.mc_tab.threshold.value())
        for i, label in ((0, "Monte Carlo · DD peggiore"), (1, "Monte Carlo · DD medio"), (3, "Monte Carlo · DD 95%")):
            _label, value, tone, sub = values[i]
            extra.append(Kpi(label, value, tone, sub))
    prop = window.prop_tab.result
    if prop is not None:
        values = kpi_values(prop)
        for i, label in ((0, "Prop Firm · passaggio"), (2, "Prop Firm · primo payout"), (1, "Prop Firm · giornate")):
            _label, value, tone, sub = values[i]
            extra.append(Kpi(label, value, tone, sub))
    if extra:
        blocks.append(KpiBlock(extra))
    blocks += [
        SectionTitle("Strategie incluse" if n > 1 else "Strategia"),
        TableBlock(columns, rows, font_size=7.0),
        TocBlock("Contenuto del resoconto"),
    ]
    if primary.is_combined:
        subtitle = f"Portafoglio combinato di {n} strategie · {_period(primary)}"
    else:
        subtitle = f"{primary.name} · {_period(primary)}"
    return ReportContent("Resoconto completo", subtitle, blocks, _meta(portfolio), key="summary")


# --------------------------------------------------------------------------- ingresso

BUILDERS = {
    "stats_tab": stats_section,
    "equity_tab": equity_section,
    "mc_tab": montecarlo_section,
    "prop_tab": propfirm_section,
    "analysis_tab": analysis_section,
    "trades_tab": trades_section,
}

# Sezioni del resoconto completo: (chiave, titolo, costruttore)
FULL_REPORT_SECTIONS = [
    ("summary", "Riepilogo e indice", summary_section),
    ("stats", "Statistiche", lambda w: stats_section(w, with_correlation=False)),
    ("correlation", "Correlazione fra strategie", correlation_section),
    ("equity", "Equity curve", lambda w: equity_section(w, with_correlation=False)),
    ("montecarlo", "Monte Carlo", montecarlo_section),
    ("propfirm", "Prop Firm", propfirm_section),
    ("analysis", "Analisi grafica", analysis_section),
    ("trades", "Lista trade", trades_section),
]


def build_full_report(window) -> Document | None:
    """Resoconto di tutte le schede in un unico documento (ogni sezione inizia su una pagina nuova)."""
    if window.portfolio is None or window.portfolio.empty:
        return None
    sections = []
    with _light():
        for key, _title, builder in FULL_REPORT_SECTIONS:
            if key == "correlation" and len(window.portfolio.strategies) < 2:
                continue
            sections.append(builder(window))
    return Document("Resoconto completo", sections)


def build_report(window, index: int | None = None) -> ReportContent | None:
    """Report della scheda ``index`` (default: quella visibile). ``None`` se non ci sono dati."""
    if window.portfolio is None or window.portfolio.empty:
        return None
    index = window.tabs.currentIndex() if index is None else index
    page = window.tabs.widget(index)
    page = page.widget() if hasattr(page, "widget") else page
    for attr, builder in BUILDERS.items():
        if getattr(window, attr, None) is page:
            with _light():
                return builder(window)
    return None
