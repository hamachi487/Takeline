"""GUI entry point."""

from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from pathlib import Path


def _install_crash_handler(app) -> None:
    from PySide6.QtWidgets import QMessageBox

    from .config import app_data_dir

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log = app_data_dir() / "crash.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n--- {dt.datetime.now().isoformat()} ---\n{text}")
        except OSError:
            pass
        try:
            QMessageBox.critical(None, "Takeline",
                                 f"An unexpected error occurred. No files were changed by this error.\n\n"
                                 f"{exc_type.__name__}: {exc}\n\nDetails were saved to:\n{log}")
        except Exception:
            pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if os.name == "nt":
        try:  # own taskbar identity/icon instead of the generic Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Takeline.App.1")
        except Exception:
            pass
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, __version__
    from .gui import style
    from .gui.icon import app_icon
    from .gui.main_window import MainWindow

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(style.ui_font(9.5))
    app.setWindowIcon(app_icon())
    style.apply_palette(app)
    _install_crash_handler(app)

    window = MainWindow()

    def on_scheme_changed(*_):
        style.apply_palette(app)
        window.refresh_theme()
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(on_scheme_changed)
    except Exception:
        pass

    window.show()
    paths = [a for a in argv if Path(a).exists()]  # "Open with" / files dropped on the .exe
    if paths:
        window.add_paths(paths)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
