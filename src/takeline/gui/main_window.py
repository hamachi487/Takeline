"""Main window."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QTableView, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..config import Settings, history_dir
from ..detection import DEVICE_CHOICES, DetectionEngine
from ..metadata.record import jsonable
from ..naming import find_duplicates, sanitize_component
from ..operations import copy_batch, latest_manifest, rename_batch, undo_manifest
from ..operations.report import Summary, export_report
from ..pipeline import Analyzer, MediaItem, collect_files, plan, re_resolve
from . import style
from .dialogs import (ConfirmCopyDialog, ConfirmRenameDialog, RawMetadataDialog, ResultsDialog,
                      SettingsDialog, TimeDialog)
from .inspector import Inspector
from .table_model import (COL_CONF, COL_DEVICE, COL_INCLUDE, COL_NEW, COL_ORIGINAL, COL_SOURCE, COL_STATUS,
                          COL_TIME, ID_ROLE, ItemTableModel, SortProxy)
from .timeline import Tick, TimelineStrip
from .workers import TaskRunner

DIALOG_FILTER = ("Photos and videos (*.mov *.mp4 *.m4v *.avi *.mkv *.webm *.jpg *.jpeg *.heic *.heif "
                 "*.png *.tif *.tiff);;All files (*)")


def _act(menu: QMenu, text: str, fn, shortcut=None) -> QAction:
    a = menu.addAction(text)
    if shortcut is not None:
        a.setShortcut(QKeySequence(shortcut))
    a.triggered.connect(lambda _checked=False: fn())
    return a


def _key(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.items: list[MediaItem] = []
        self._next_id = 0
        self.runner = TaskRunner(self)
        self.last_summary: Summary | None = None
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 840)
        self.setMinimumSize(1000, 640)
        self.setAcceptDrops(True)
        self._build()
        self._build_menus()
        self.refresh_views()

    # ================================================================== UI
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Takeline")
        title.setObjectName("title")
        subtitle = QLabel("Organize your media by actual capture time.")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        privacy = QLabel("Your files never leave this computer.")
        privacy.setObjectName("privacy")
        privacy.setToolTip("Everything is processed locally. No uploads, no analytics, no telemetry.")
        header.addWidget(privacy, 0, Qt.AlignBottom)
        root.addLayout(header)

        self.strip = TimelineStrip()
        self.strip.filesDropped.connect(self.add_paths)
        self.strip.selectFilesRequested.connect(self.add_files_dialog)
        self.strip.selectFolderRequested.connect(self.add_folder_dialog)
        self.strip.itemClicked.connect(self.select_item_id)
        root.addWidget(self.strip)

        self.summary_row = QWidget()
        sr = QHBoxLayout(self.summary_row)
        sr.setContentsMargins(2, 0, 0, 0)
        sr.setSpacing(18)
        self.count_label = QLabel()
        self.count_label.setObjectName("count")
        sr.addWidget(self.count_label)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(16)
        sr.addLayout(self.chips)
        sr.addStretch(1)
        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_clear = QPushButton("Clear List")
        self.btn_add_files.clicked.connect(self.add_files_dialog)
        self.btn_add_folder.clicked.connect(self.add_folder_dialog)
        self.btn_clear.clicked.connect(self.clear)
        for b in (self.btn_add_files, self.btn_add_folder, self.btn_clear):
            sr.addWidget(b)
        root.addWidget(self.summary_row)

        self.model = ItemTableModel(self)
        self.model.includeToggled.connect(self.replan)
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_TIME, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setStretchLastSection(True)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col, w in ((COL_INCLUDE, 30), (COL_ORIGINAL, 200), (COL_DEVICE, 128), (COL_TIME, 172),
                       (COL_SOURCE, 136), (COL_CONF, 88), (COL_NEW, 250)):
            hh.resizeSection(col, w)
        hh.setSectionResizeMode(COL_INCLUDE, QHeaderView.Fixed)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _i: self.inspector.setFocus())
        space = QShortcut(QKeySequence(Qt.Key_Space), self.table)
        space.setContext(Qt.WidgetShortcut)
        space.activated.connect(self.toggle_include)

        self.inspector = Inspector()
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([1000, 330])
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.progress_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.runner.cancel)
        for w in (self.progress_label, self.progress, self.btn_cancel):
            w.setVisible(False)
        bottom.addWidget(self.hint)
        bottom.addWidget(self.progress_label)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.btn_cancel)
        bottom.addStretch(1)
        self.btn_settings = QPushButton("Settings")
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_export = QPushButton("Export Report…")
        self.btn_export.clicked.connect(self.export_report)
        self.btn_rename = QPushButton("Rename Originals…")
        self.btn_rename.setObjectName("danger")
        self.btn_rename.clicked.connect(self.rename_originals)
        self.btn_copy = QPushButton("Create Renamed Copies…")
        self.btn_copy.setObjectName("primary")
        self.btn_copy.clicked.connect(self.create_copies)
        for b in (self.btn_settings, self.btn_export):
            bottom.addWidget(b)
        bottom.addSpacing(12)
        bottom.addWidget(self.btn_rename)
        bottom.addWidget(self.btn_copy)
        root.addLayout(bottom)
        self.setCentralWidget(central)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self.act_add_files = _act(m, "Add Files…", self.add_files_dialog, QKeySequence.Open)
        self.act_add_folder = _act(m, "Add Folder…", self.add_folder_dialog, QKeySequence("Ctrl+Shift+O"))
        m.addSeparator()
        self.act_remove = _act(m, "Remove Selected from List", self.remove_selected, QKeySequence.Delete)
        _act(m, "Clear List", self.clear)
        m.addSeparator()
        _act(m, "Export Report…", self.export_report, QKeySequence("Ctrl+E"))
        m.addSeparator()
        _act(m, "Exit", self.close, QKeySequence.Quit)

        e = mb.addMenu("&Edit")
        self.device_menu = self._device_menu(e)
        _act(e, "Set Capture Time…", self.set_time_manually, QKeySequence("Ctrl+T"))
        _act(e, "Use Detected Capture Time", self.use_detected_time)
        e.addSeparator()
        _act(e, "Include Selected", lambda: self.set_include(True))
        _act(e, "Exclude Selected", lambda: self.set_include(False))
        _act(e, "Reset Overrides", self.reset_overrides)
        e.addSeparator()
        _act(e, "Select All", self.table.selectAll, QKeySequence.SelectAll)

        t = mb.addMenu("&Tools")
        _act(t, "Settings…", self.open_settings, QKeySequence("Ctrl+,"))
        _act(t, "Undo Last Rename…", self.undo_last_rename)
        t.addSeparator()
        self.act_raw = _act(t, "Show Raw Metadata…", self.show_raw_metadata, QKeySequence("Ctrl+I"))
        _act(t, "Open Undo History Folder", lambda: self._open_path(history_dir()))

        h = mb.addMenu("&Help")
        _act(h, "How It Works", self.show_help)
        _act(h, "Privacy", self.show_privacy)
        _act(h, "Third-Party Licenses", self.show_licenses)
        _act(h, f"About {APP_NAME}", self.show_about)

    def _device_menu(self, parent_menu: QMenu) -> QMenu:
        dm = parent_menu.addMenu("Set Device")
        _act(dm, "Auto (detected)", lambda: self.set_device(None))
        dm.addSeparator()
        for code, name in DEVICE_CHOICES:
            _act(dm, name, lambda c=code: self.set_device(c))
        dm.addSeparator()
        _act(dm, "Custom…", self.set_custom_device)
        return dm

    # ================================================================== adding files
    def add_files_dialog(self) -> None:
        if self.runner.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Select photos and videos", self._start_dir(), DIALOG_FILTER)
        if files:
            self.add_paths(files)

    def add_folder_dialog(self) -> None:
        if self.runner.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select a folder", self._start_dir())
        if folder:
            self.add_paths([folder])

    def _start_dir(self) -> str:
        if self.items:
            return str(self.items[-1].path.parent)
        return str(Path.home())

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and not self.runner.busy:
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        if self.runner.busy or not paths:
            return
        settings = self.settings
        existing = {_key(it.path) for it in self.items}
        all_entries = [(it.id, it.path, it.size) for it in self.items if not it.error]
        start_id = self._next_id

        def work(cancel, progress):
            progress(0, 0, "Looking for photos and videos…")
            files, ignored = collect_files(paths, settings.include_subfolders, cancel)
            files = [f for f in files if _key(f) not in existing]
            items = [MediaItem(start_id + i, f) for i, f in enumerate(files)]
            if items:
                an = Analyzer(settings)
                try:
                    an.analyze_many(items, lambda d, t: progress(d, t, f"Reading metadata  {d} of {t}"), cancel)
                finally:
                    an.close()
            items = [it for it in items if it.record is not None]
            progress(0, 0, "Checking for duplicate files…")
            dupes = find_duplicates(all_entries + [(it.id, it.path, it.size) for it in items if not it.error])
            return items, ignored, dupes, cancel.is_set()

        self._next_id += 10_000_000  # id space reserved for this scan
        self._busy(True, "Scanning…")
        self.runner.start(work, self._on_progress, self._scan_done, self._task_failed)

    def _scan_done(self, result) -> None:
        items, ignored, dupes, cancelled = result
        self._busy(False)
        self.items.extend(items)
        by_id = {it.id: it for it in self.items}
        for it in self.items:
            it.duplicate_of = None
        for k, first in dupes.items():
            if k in by_id and first in by_id:
                by_id[k].duplicate_of = by_id[first].path
        self.replan()
        msg = f"Added {len(items)} file{'s' if len(items) != 1 else ''}."
        if ignored:
            msg += f" Ignored {ignored} unsupported or system file{'s' if ignored != 1 else ''}."
        if cancelled:
            msg += " Scan cancelled."
        self.hint.setText(msg)
        if not items and not ignored and not cancelled:
            self.hint.setText("No supported photos or videos were found there.")

    # ================================================================== state
    def replan(self) -> None:
        plan(self.items, self.settings, "rename")
        self.refresh_views()

    def refresh_views(self) -> None:
        has = bool(self.items)
        if self.model.items is not self.items or self.model.known_rows != len(self.items):
            self.model.set_items(self.items)
        else:
            self.model.refresh()
        self.proxy.sort(self.table.horizontalHeader().sortIndicatorSection(),
                        self.table.horizontalHeader().sortIndicatorOrder())
        # The timeline shows what will be processed: included files with a usable time.
        self.strip.set_items([Tick(it.id, it.capture_time, it.device, it.device_name, it.path.name)
                              for it in self.items if it.capture_time and it.include and not it.error])
        self.summary_row.setVisible(has)
        self._update_summary()
        self._update_buttons()
        sel = self.selected_items()
        self.inspector.show_item(sel[0] if len(sel) == 1 else None)

    def _update_summary(self) -> None:
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        n = len(self.items)
        self.count_label.setText(f"Files: {n}")
        groups: dict[str, list] = {}
        for it in self.items:
            name = it.device_name.replace(" (manual)", "")
            if it.device == "APPLE":
                name = "Apple"
            groups.setdefault(it.device, [name, 0])
            groups[it.device][1] += 1
        t = style.tokens()
        for code, (name, count) in sorted(groups.items(), key=lambda kv: (-kv[1][1], kv[0])):
            col = style.device_color(code).name()
            lab = QLabel(f'<span style="color:{col}; font-size:13pt;">●</span>&nbsp;{name}: <b>{count}</b>')
            self.chips.addWidget(lab)
        review = sum(1 for it in self.items if it.status[0] != "ok")
        if review:
            lab = QLabel(f'<span style="color:{t.review};">{review} need review</span>')
            lab.setToolTip("Sort by the Status column to see them first.")
            self.chips.addWidget(lab)

    def _update_buttons(self) -> None:
        busy = self.runner.busy
        ready = [it for it in self.items if it.include and it.can_process]
        for b in (self.btn_copy, self.btn_rename):
            b.setEnabled(bool(ready) and not busy)
        self.btn_export.setEnabled(bool(self.items) and not busy)
        for w in (self.btn_add_files, self.btn_add_folder, self.btn_clear, self.btn_settings,
                  self.strip.btn_files, self.strip.btn_folder):
            w.setEnabled(not busy)
        self.act_raw.setVisible(self.settings.developer_mode)
        if not busy and self.items and not self.hint.text():
            self.hint.setText("Right-click a file to change its device or capture time.")

    def _busy(self, on: bool, text: str = "") -> None:
        self.progress_label.setText(text)
        for w in (self.progress_label, self.progress, self.btn_cancel):
            w.setVisible(on)
        self.hint.setVisible(not on)
        self.progress.setRange(0, 0)
        self.table.setEnabled(not on)
        self._update_buttons()

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        if text:
            self.progress_label.setText(text)

    def _task_failed(self, error: str) -> None:
        self._busy(False)
        self.replan()
        QMessageBox.critical(self, APP_NAME, "Something went wrong. No further files were changed.\n\n"
                             + error.strip().splitlines()[-1], QMessageBox.Ok)

    # ================================================================== selection
    def selected_items(self) -> list[MediaItem]:
        rows = {self.proxy.mapToSource(ix).row() for ix in self.table.selectionModel().selectedRows()}
        return [self.items[r] for r in sorted(rows) if r < len(self.items)]

    def _selection_changed(self, *_a) -> None:
        sel = self.selected_items()
        self.inspector.show_item(sel[0] if len(sel) == 1 else None)
        self.strip.set_selected({it.id for it in sel})

    def select_item_id(self, item_id: int) -> None:
        for row, it in enumerate(self.items):
            if it.id == item_id:
                ix = self.proxy.mapFromSource(self.model.index(row, 0))
                self.table.selectionModel().select(
                    ix, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                self.table.scrollTo(ix)
                return

    def _table_menu(self, pos) -> None:
        if not self.selected_items():
            return
        menu = QMenu(self)
        self._device_menu(menu)
        tm = menu.addMenu("Capture Time")
        _act(tm, "Use detected", self.use_detected_time)
        a = _act(tm, "Choose manually…", self.set_time_manually)
        a.setEnabled(len(self.selected_items()) == 1)
        menu.addSeparator()
        _act(menu, "Include", lambda: self.set_include(True))
        _act(menu, "Exclude", lambda: self.set_include(False))
        _act(menu, "Reset Overrides", self.reset_overrides)
        menu.addSeparator()
        _act(menu, "Open Containing Folder", lambda: self._open_path(self.selected_items()[0].path.parent))
        if self.settings.developer_mode:
            _act(menu, "Show Raw Metadata…", self.show_raw_metadata)
        _act(menu, "Remove from List", self.remove_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ================================================================== overrides
    def set_device(self, code: str | None) -> None:
        sel = self.selected_items()
        for it in sel:
            it.device_override = code
        re_resolve(sel, self.settings)  # device-specific timestamp rules may change
        self._after_edit()

    def set_custom_device(self) -> None:
        if not self.selected_items():
            return
        text, ok = QInputDialog.getText(self, "Custom device",
                                        "Device label used in filenames (for example DRONE or CAM-B):")
        if ok and text.strip():
            self.set_device(sanitize_component(text.strip()))

    def set_time_manually(self) -> None:
        sel = self.selected_items()
        if len(sel) != 1:
            return
        it = sel[0]
        dlg = TimeDialog(it.path.name, it.capture_time, self)
        if dlg.exec():
            it.time_override = dlg.value()
            it.include = True
            self._after_edit()

    def use_detected_time(self) -> None:
        for it in self.selected_items():
            it.time_override = None
        self._after_edit()

    def set_include(self, on: bool) -> None:
        for it in self.selected_items():
            it.include = on and it.can_process
        self._after_edit()

    def toggle_include(self) -> None:
        sel = self.selected_items()
        if sel:
            target = not all(it.include for it in sel)
            self.set_include(target)

    def reset_overrides(self) -> None:
        sel = self.selected_items()
        for it in sel:
            it.device_override = None
            it.time_override = None
            it.include = it.error is None
        re_resolve(sel, self.settings)
        self._after_edit()

    def remove_selected(self) -> None:
        if self.runner.busy:
            return
        ids = {it.id for it in self.selected_items()}
        if ids:
            self.items = [it for it in self.items if it.id not in ids]
            self.model.set_items(self.items)
            self.replan()

    def clear(self) -> None:
        if self.runner.busy:
            return
        self.items = []
        self.model.set_items(self.items)
        self.hint.setText("")
        self.replan()

    def _after_edit(self) -> None:
        for it in self.items:
            it.result_status = ""
        keep = {it.id for it in self.selected_items()}
        self.replan()
        for row, it in enumerate(self.items):
            if it.id in keep:
                ix = self.proxy.mapFromSource(self.model.index(row, 0))
                self.table.selectionModel().select(ix, QItemSelectionModel.Select | QItemSelectionModel.Rows)

    # ================================================================== actions
    def _ready(self) -> list[MediaItem]:
        return [it for it in self.items if it.include and it.can_process]

    def _review_count(self, items) -> int:
        return sum(1 for it in items if it.status[0] != "ok")

    def create_copies(self) -> None:
        ready = self._ready()
        if not ready or self.runner.busy:
            return
        start = self.settings.last_copy_destination or str(ready[0].path.parent)
        dest = QFileDialog.getExistingDirectory(self, "Choose a folder for the renamed copies", start)
        if not dest:
            return
        dest_path = Path(dest)
        for it in self.items:
            it.result_status = ""
        stats = plan(self.items, self.settings, "copy", dest_path)
        self.refresh_views()
        dlg = ConfirmCopyDialog(len(ready), dest_path, stats.collisions, self._review_count(ready), self)
        if not dlg.exec():
            self.replan()
            return
        self.settings.last_copy_destination = str(dest_path)
        self.settings.save()
        jobs = [(it.id, it.path, dest_path / it.proposed_name) for it in ready]

        def work(cancel, progress):
            return copy_batch(jobs, lambda d, t, n: progress(d, t, f"Copying  {d} of {t}"), cancel)

        self._busy(True, "Copying…")
        self.runner.start(work, self._on_progress,
                          lambda res: self._op_done("Create Renamed Copies", res, None, dest_path),
                          self._task_failed)

    def rename_originals(self) -> None:
        if self.runner.busy:
            return
        for it in self.items:
            it.result_status = ""
        stats = plan(self.items, self.settings, "rename")
        self.refresh_views()
        ready = self._ready()
        jobs = [(it.id, it.path, it.path.with_name(it.proposed_name)) for it in ready if not it.unchanged]
        if not jobs:
            QMessageBox.information(self, APP_NAME, "All included files already have the right names.")
            return
        dlg = ConfirmRenameDialog(len(jobs), stats.unchanged, stats.collisions, self._review_count(ready), self)
        if not dlg.exec():
            return

        def work(cancel, progress):
            return rename_batch(jobs, history_dir(), lambda d, t, n: progress(d, t, f"Renaming  {d} of {t}"), cancel)

        self._busy(True, "Renaming…")
        self.runner.start(work, self._on_progress,
                          lambda res: self._op_done("Rename Originals", res[0], res[1], None),
                          self._task_failed)

    def _op_done(self, action: str, results, manifest, dest: Path | None) -> None:
        self._busy(False)
        by_id = {it.id: it for it in self.items}
        s = Summary(action=action, scanned=len(self.items), destination=str(dest) if dest else "",
                    manifest=str(manifest) if manifest else "")
        problems = []
        for r in results:
            it = by_id.get(r.key)
            if it is None:
                continue
            it.result_status, it.result_path, it.result_error = r.status, r.dst, r.error
            if r.status in ("copied", "renamed"):
                s.processed += 1
                s.collisions += 1 if it.suffix_n else 0
                if r.status == "renamed":
                    it.path = Path(r.dst)
                    if it.record is not None:
                        it.record.path = r.dst
            elif r.status == "unchanged":
                s.unchanged += 1
            elif r.status == "skipped":
                s.skipped += 1
                problems.append(f"{Path(r.src).name}: {r.error or 'skipped'}")
            else:
                s.errors += 1
                problems.append(f"{Path(r.src).name}: {r.error}")
        s.duplicates = sum(1 for it in self.items if it.duplicate_of)
        self.last_summary = s
        plan(self.items, self.settings, "rename")
        self.refresh_views()
        self.hint.setText("  ".join(s.lines()[:3]))
        folder = dest or (self.items[0].path.parent if self.items else None)
        ResultsDialog(s, problems, self.export_report, folder, self).exec()

    def undo_last_rename(self) -> None:
        if self.runner.busy:
            return
        m = latest_manifest(history_dir())
        if m is None:
            QMessageBox.information(self, APP_NAME, "There is no rename to undo.")
            return
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            QMessageBox.warning(self, APP_NAME, f"The undo record could not be read:\n{m}")
            return
        n = sum(1 for e in data.get("entries", []) if e.get("status", "renamed") == "renamed")
        when = data.get("created", "").replace("T", " ")
        if QMessageBox.question(self, "Undo last rename",
                                f"Restore the original names of {n} file(s) renamed on {when}?\n\n"
                                "Nothing is overwritten; files that changed since are left alone.") != QMessageBox.Yes:
            return

        def work(cancel, progress):
            progress(0, 0, "Restoring original names…")
            return undo_manifest(m)

        self._busy(True, "Restoring…")
        self.runner.start(work, self._on_progress, self._undo_done, self._task_failed)

    def _undo_done(self, results) -> None:
        self._busy(False)
        index = {_key(it.path): it for it in self.items}
        s = Summary(action="Undo", scanned=len(results))
        problems = []
        for r in results:
            if r.status == "renamed":
                s.processed += 1
                it = index.get(_key(Path(r.src)))
                if it is not None:
                    it.path = Path(r.dst)
                    it.result_status = ""
            elif r.status == "error":
                s.errors += 1
                problems.append(f"{Path(r.src).name}: {r.error}")
            elif r.status == "skipped":
                s.skipped += 1
        self.replan()
        ResultsDialog(s, problems, self.export_report, None, self).exec()

    # ================================================================== misc
    def export_report(self) -> None:
        if not self.items:
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export report", str(Path(self._start_dir()) / "Takeline report.csv"),
                                                "CSV spreadsheet (*.csv);;JSON (*.json);;Text (*.txt)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in (".csv", ".json", ".txt"):
            p = p.with_suffix(".json" if "JSON" in flt else ".txt" if "Text" in flt else ".csv")
        try:
            export_report(p, self.items, self.last_summary)
            self.hint.setText(f"Report saved: {p}")
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not save the report:\n{exc}")

    def show_raw_metadata(self) -> None:
        sel = self.selected_items()
        if len(sel) != 1 or sel[0].record is None:
            QMessageBox.information(self, APP_NAME, "Select one file first.")
            return
        it = sel[0]
        payload = {
            "file": str(it.path),
            "metadata": it.record.to_jsonable(),
            "detection": it.detection.to_dict() if it.detection else None,
            "timestamp": it.timestamp.to_dict() if it.timestamp else None,
            "overrides": {"device": it.device_override,
                          "capture_time": jsonable(it.time_override) if it.time_override else None},
        }
        RawMetadataDialog(it.path.name, payload, self).exec()

    def open_settings(self) -> None:
        if self.runner.busy:
            return
        dlg = SettingsDialog(self.settings, self)
        if not dlg.exec() or dlg.result_settings is None:
            return
        old, new = self.settings, dlg.result_settings
        self.settings = new
        new.save()
        if new.unknown_threshold != old.unknown_threshold:
            engine = DetectionEngine(threshold=new.unknown_threshold)
            for it in self.items:
                if it.record is not None:
                    it.detection = engine.detect(it.record)
        re_resolve(self.items, new)
        for it in self.items:
            it.result_status = ""
        self.replan()
        if self.items and (new.use_exiftool != old.use_exiftool or new.exiftool_path != old.exiftool_path):
            if QMessageBox.question(self, APP_NAME, "Read the files again with the new metadata engine setting?") \
                    == QMessageBox.Yes:
                paths = [str(it.path) for it in self.items]
                self.items = []
                self.model.set_items(self.items)
                self.add_paths(paths)

    def _open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True) if path == history_dir() else None
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_help(self) -> None:
        QMessageBox.information(self, "How Takeline works", (
            "1. Add photos and videos (drop them, or use Select Files / Select Folder).\n"
            "2. Takeline reads the capture date stored inside each file, not the Windows file dates, "
            "and identifies the camera from the file's own metadata.\n"
            "3. Review the preview. Right-click a file to set its device or capture time. Sort by any column; "
            "hover a cell to see why a value was chosen.\n"
            "4. Create Renamed Copies keeps your originals untouched. Rename Originals renames in place and "
            "can be undone from Tools > Undo Last Rename.\n\n"
            "Files with the same device and second get -01, -02 suffixes. Nothing is ever overwritten."))

    def show_privacy(self) -> None:
        QMessageBox.information(self, "Privacy", (
            "Your files never leave this computer.\n\nTakeline has no network code: it does not upload "
            "photos, videos, metadata or filenames, and it has no analytics or telemetry. Settings and undo "
            f"records are stored locally in:\n{history_dir().parent}"))

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<b>{APP_NAME} {__version__}</b><br>Rename photos and videos by their real capture time.<br><br>"
            "Metadata is read by built-in parsers; ExifTool (by Phil Harvey) is used additionally when bundled."
            "<br><br>Built with Python and Qt for Python (PySide6, LGPL v3). See Help &gt; Third-Party Licenses."))

    def show_licenses(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
        from ..metadata.exiftool_backend import app_base_dir
        p = app_base_dir() / "THIRD-PARTY-NOTICES.md"
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = "THIRD-PARTY-NOTICES.md was not found next to the application."
        dlg = QDialog(self)
        dlg.setWindowTitle("Third-Party Licenses")
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def refresh_theme(self) -> None:
        self.model.clear_cache()
        self.refresh_views()
        self.strip.update()

    def closeEvent(self, ev) -> None:
        if self.runner.busy:
            if QMessageBox.question(self, APP_NAME, "An operation is still running. Stop it and quit?\n\n"
                                    "Files already processed stay processed; the rest are left untouched.") \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            self.runner.cancel()
            if self.runner.thread is not None:
                self.runner.thread.quit()
                self.runner.thread.wait(15000)
        self.settings.save()
        ev.accept()
