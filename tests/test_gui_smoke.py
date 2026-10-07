"""Starts the real window offscreen, scans a folder and checks the preview."""
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_window_scans_and_previews(media):
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from takeline.config import Settings
    from takeline.gui import style
    from takeline.gui.main_window import MainWindow
    from test_pipeline import EXPECTED, build_acceptance_folder

    app = QApplication.instance() or QApplication([])
    style.apply_palette(app)
    build_acceptance_folder(media)
    w = MainWindow(Settings(use_exiftool=False))
    w.show()
    w.add_paths([str(media)])
    t0 = time.time()
    while w.runner.busy and time.time() - t0 < 30:
        app.processEvents(QEventLoop.AllEvents, 50)
    assert w.model.rowCount() == 5
    shown = [w.proxy.index(r, 6).data() for r in range(w.proxy.rowCount())]
    assert shown == EXPECTED  # sorted by capture time by default
    w.select_item_id(w.items[0].id)
    w.set_device("SONY")
    assert w.items[0].proposed_name.startswith("SONY-")
    w.close()
