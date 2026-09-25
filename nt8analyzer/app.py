"""Avvio dell'applicazione."""

from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NT8 Backtest Analyzer")
    parser.add_argument("files", nargs="*", help="File CSV da caricare all'avvio")
    parser.add_argument(
        "--screenshots",
        metavar="CARTELLA",
        help="Salva uno screenshot di ogni scheda nella cartella indicata ed esce (verifica automatica)",
    )
    parser.add_argument(
        "--tema",
        choices=["chiaro", "scuro", "sistema"],
        help="Tema dell'interfaccia per questa sessione (non modifica quello salvato)",
    )
    parser.add_argument("--size", nargs=2, type=int, default=(1600, 950), metavar=("L", "A"), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QCoreApplication, QSettings, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .ui.main_window import MainWindow
    from .ui import theme

    QCoreApplication.setOrganizationName("NT8BacktestAnalyzer")
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    cli_theme = {"chiaro": "light", "scuro": "dark", "sistema": "system"}.get(args.tema)
    preference = cli_theme or str(QSettings().value("theme", "system"))
    theme.set_mode(theme.resolve_mode(preference))
    theme.apply_theme(app)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = MainWindow(theme_preference=preference, persist_theme=cli_theme is None)
    if args.screenshots:
        window.resize(args.size[0], args.size[1])
        window.show()
    else:
        window.showMaximized()
    if args.files:
        window.load_files(args.files, interactive=args.screenshots is None)

    if args.screenshots:
        return _take_screenshots(app, window, args.screenshots)

    QTimer.singleShot(0, window.raise_)
    return app.exec()


def _take_screenshots(app, window, folder: str) -> int:
    """Modalità di verifica: renderizza ogni scheda e salva un PNG, poi esce."""
    os.makedirs(folder, exist_ok=True)
    if window.portfolio is None or window.portfolio.empty:
        print("Nessun dato caricato", file=sys.stderr)
        return 2
    ok = True
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        for _ in range(5):
            app.processEvents()
        name = window.tabs.tabText(i).lower().replace(" ", "_")
        path = os.path.join(folder, f"{i + 1:02d}_{name}.png")
        ok &= window.grab().save(path)
        print(f"salvato {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
