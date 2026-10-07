"""Dialogs."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QCompleter, QDateTimeEdit, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

from ..config import Settings
from ..metadata.exiftool_backend import locate_exiftool
from ..naming import DEFAULT_TEMPLATE, FilenameTemplate
from ..operations.report import Summary
from ..timestamps import list_zones
from .style import tabular, ui_font


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


# ------------------------------------------------------------------ settings
class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.original = settings
        s = settings
        lay = QVBoxLayout(self)

        # Timezone
        tz = QGroupBox("Timezone")
        tzl = QVBoxLayout(tz)
        self.rb_auto = QRadioButton("Automatic")
        self.rb_system = QRadioButton("System timezone")
        self.rb_manual = QRadioButton("Manual")
        grp = QButtonGroup(self)
        for i, rb in enumerate((self.rb_auto, self.rb_system, self.rb_manual)):
            grp.addButton(rb, i)
        {"auto": self.rb_auto, "system": self.rb_system, "manual": self.rb_manual}[s.tz_mode].setChecked(True)
        tzl.addWidget(self.rb_auto)
        tzl.addWidget(_muted("Keeps each file's recorded local time exactly as recorded. Values stored "
                             "only as UTC (movie headers) are shown in this computer's timezone."))
        tzl.addWidget(self.rb_system)
        tzl.addWidget(_muted("Converts every time with a known timezone into this computer's timezone."))
        row = QHBoxLayout()
        row.addWidget(self.rb_manual)
        self.zone = QComboBox()
        self.zone.setEditable(True)
        zones = list_zones()
        self.zone.addItems(zones)
        self.zone.setCurrentText(s.tz_zone)
        comp = QCompleter(zones, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.zone.setCompleter(comp)
        row.addWidget(self.zone, 1)
        tzl.addLayout(row)
        tzl.addWidget(_muted("Use the timezone where the footage was recorded, e.g. America/Sao_Paulo. "
                             "Times recorded without a timezone are never shifted."))
        self.naive_utc = QCheckBox("Treat capture times without a timezone as UTC (only if a camera is known to do this)")
        self.naive_utc.setChecked(s.naive_is_utc)
        tzl.addWidget(self.naive_utc)
        grp.idToggled.connect(lambda *_: self.zone.setEnabled(self.rb_manual.isChecked()))
        self.zone.setEnabled(self.rb_manual.isChecked())
        lay.addWidget(tz)

        # Naming
        nm = QGroupBox("Filenames")
        form = QFormLayout(nm)
        self.template = QLineEdit(s.template)
        self.template.setFont(tabular(ui_font()))
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda: self.template.setText(DEFAULT_TEMPLATE))
        trow = QHBoxLayout()
        trow.addWidget(self.template, 1)
        trow.addWidget(reset)
        form.addRow("Template", trow)
        self.example = _muted("")
        form.addRow("", self.example)
        self.ext_case = QComboBox()
        self.ext_case.addItems(["Keep original (.MOV stays .MOV)", "Lowercase (.mov)", "Uppercase (.MOV)"])
        self.ext_case.setCurrentIndex({"preserve": 0, "lower": 1, "upper": 2}.get(s.ext_case, 0))
        form.addRow("Extension", self.ext_case)
        form.addRow("", _muted("Tokens: {DEVICE} {MODEL} {YYYY} {YY} {MM} {DD} {HH} {MIN} {SEC} "
                               "{YYYYMMDD} {HHMMSS} {SUFFIX} {EXT} {ORIGINAL}"))
        self.template.textChanged.connect(self._preview)
        self.ext_case.currentIndexChanged.connect(self._preview)
        lay.addWidget(nm)

        # Detection
        det = QGroupBox("Scanning and detection")
        dl = QFormLayout(det)
        self.subfolders = QCheckBox("Include subfolders when a folder is added")
        self.subfolders.setChecked(s.include_subfolders)
        dl.addRow(self.subfolders)
        self.threshold = QSpinBox()
        self.threshold.setRange(40, 95)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(int(round(s.unknown_threshold * 100)))
        dl.addRow("Label as UNKNOWN below", self.threshold)
        self.fn_dates = QCheckBox("Use dates written in camera filenames when no embedded date exists")
        self.fn_dates.setChecked(s.allow_filename_dates)
        dl.addRow(self.fn_dates)
        self.fs_dates = QCheckBox("Use the file's modified time as a last resort (flagged for review)")
        self.fs_dates.setChecked(s.allow_filesystem_fallback)
        dl.addRow(self.fs_dates)
        lay.addWidget(det)

        # Engine
        eng = QGroupBox("Metadata engine")
        el = QVBoxLayout(eng)
        self.use_et = QCheckBox("Also use ExifTool when it is bundled with the app")
        self.use_et.setChecked(s.use_exiftool)
        el.addWidget(self.use_et)
        found = locate_exiftool(s.exiftool_path or None)
        el.addWidget(_muted(f"ExifTool found: {found}" if found else
                            "ExifTool not found. The built-in readers are used (fully supported)."))
        self.dev = QCheckBox("Developer mode (adds Show Raw Metadata)")
        self.dev.setChecked(s.developer_mode)
        el.addWidget(self.dev)
        lay.addWidget(eng)

        self.error = QLabel("")
        self.error.setStyleSheet("color: #B42318;")
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.buttons = bb
        lay.addWidget(bb)
        self.result_settings: Settings | None = None
        self._preview()

    def _ext_case(self) -> str:
        return ["preserve", "lower", "upper"][self.ext_case.currentIndex()]

    def _preview(self) -> None:
        try:
            tpl = FilenameTemplate(self.template.text(), self.original.suffix_format, self._ext_case())
            when = dt.datetime(2026, 7, 17, 19, 0, 49)
            self.example.setText(f"Example: {tpl.render('RAYBAN', when, 'MOV')}   then   "
                                 f"{tpl.render('RAYBAN', when, 'MOV', n=1)}")
            self.error.setText("")
            self.buttons.button(QDialogButtonBox.Save).setEnabled(True)
        except ValueError as exc:
            self.example.setText("")
            self.error.setText(str(exc))
            self.buttons.button(QDialogButtonBox.Save).setEnabled(False)

    def _accept(self) -> None:
        mode = "manual" if self.rb_manual.isChecked() else "system" if self.rb_system.isChecked() else "auto"
        zone = self.zone.currentText().strip()
        if mode == "manual" and zone not in list_zones():
            self.error.setText(f"Unknown timezone '{zone}'. Choose one from the list, e.g. America/Sao_Paulo.")
            return
        self.result_settings = replace(
            self.original, tz_mode=mode, tz_zone=zone or self.original.tz_zone,
            naive_is_utc=self.naive_utc.isChecked(), template=self.template.text().strip(),
            ext_case=self._ext_case(), include_subfolders=self.subfolders.isChecked(),
            unknown_threshold=self.threshold.value() / 100, allow_filename_dates=self.fn_dates.isChecked(),
            allow_filesystem_fallback=self.fs_dates.isChecked(), use_exiftool=self.use_et.isChecked(),
            developer_mode=self.dev.isChecked())
        self.accept()


# ------------------------------------------------------------------ manual time
class TimeDialog(QDialog):
    def __init__(self, filename: str, initial: dt.datetime | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set capture time")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Capture time for {filename}"))
        self.edit = QDateTimeEdit()
        self.edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.edit.setCalendarPopup(True)
        init = initial or dt.datetime.now().replace(microsecond=0)
        self.edit.setDateTime(QDateTime(init.year, init.month, init.day, init.hour, init.minute, init.second))
        self.edit.setFont(tabular(ui_font(11)))
        lay.addWidget(self.edit)
        lay.addWidget(_muted("This time is used exactly as entered; no timezone conversion is applied."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def value(self) -> dt.datetime:
        q = self.edit.dateTime()
        d, t = q.date(), q.time()
        return dt.datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())


# ------------------------------------------------------------------ confirmation
class ConfirmRenameDialog(QDialog):
    def __init__(self, count: int, unchanged: int, collisions: int, review: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename original files")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        head = QLabel(f"Rename {count} original file{'s' if count != 1 else ''}?")
        head.setObjectName("sectionTitle")
        head.setFont(ui_font(13, ui_font().Weight.DemiBold))
        lay.addWidget(head)
        lines = ["The files stay in their folders; only their names change.",
                 "No file is ever overwritten. An undo record is saved, so Tools > Undo Last Rename "
                 "can restore the original names."]
        if collisions:
            lines.append(f"{collisions} file(s) share a capture time with another file and get a numbered suffix.")
        if unchanged:
            lines.append(f"{unchanged} file(s) already have the right name and are left alone.")
        if review:
            lines.append(f"{review} included file(s) need review (unknown device or no embedded date). "
                         "Check them in the table, or uncheck them to leave them out.")
        for l in lines:
            lay.addWidget(_muted(l))
        self.ack = QCheckBox("I understand the original filenames will change")
        lay.addSpacing(6)
        lay.addWidget(self.ack)
        bb = QDialogButtonBox()
        self.go = bb.addButton(f"Rename {count} file{'s' if count != 1 else ''}", QDialogButtonBox.AcceptRole)
        self.go.setObjectName("danger")
        cancel = bb.addButton(QDialogButtonBox.Cancel)
        cancel.setDefault(True)
        self.go.setEnabled(False)
        self.ack.toggled.connect(self.go.setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class ConfirmCopyDialog(QDialog):
    def __init__(self, count: int, destination: Path, collisions: int, review: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create renamed copies")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        head = QLabel(f"Create {count} renamed cop{'ies' if count != 1 else 'y'}?")
        head.setFont(ui_font(13, ui_font().Weight.DemiBold))
        lay.addWidget(head)
        dest = QLabel(str(destination))
        dest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dest.setWordWrap(True)
        lay.addWidget(_muted("Copies go to:"))
        lay.addWidget(dest)
        lay.addWidget(_muted("Your original files are not modified. Existing files in the destination "
                             "are never overwritten."))
        if collisions:
            lay.addWidget(_muted(f"{collisions} copy name(s) get a numbered suffix to stay unique."))
        if review:
            lay.addWidget(_muted(f"{review} included file(s) need review (unknown device or no embedded date)."))
        bb = QDialogButtonBox()
        go = bb.addButton(f"Create {count} cop{'ies' if count != 1 else 'y'}", QDialogButtonBox.AcceptRole)
        go.setObjectName("primary")
        go.setDefault(True)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ------------------------------------------------------------------ results
class ResultsDialog(QDialog):
    def __init__(self, summary: Summary, problems: list[str], export_cb, open_folder: Path | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(summary.action)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        head = QLabel("Done" if not summary.errors else "Finished with errors")
        head.setFont(ui_font(13, ui_font().Weight.DemiBold))
        lay.addWidget(head)
        for line in summary.lines():
            lab = QLabel(line)
            lab.setFont(tabular(ui_font()))
            lay.addWidget(lab)
        if summary.destination:
            lay.addWidget(_muted(f"Destination: {summary.destination}"))
        if problems:
            lay.addSpacing(6)
            lay.addWidget(QLabel("Files that were not processed"))
            lst = QListWidget()
            lst.addItems(problems)
            lst.setMinimumHeight(140)
            lay.addWidget(lst)
            lay.addWidget(_muted("The other files were processed normally. Fix the cause (for example, close "
                                 "the program using the file) and run the action again for these."))
        bb = QDialogButtonBox()
        exp = bb.addButton("Export Report…", QDialogButtonBox.ActionRole)
        exp.clicked.connect(export_cb)
        if open_folder:
            op = bb.addButton("Open Folder", QDialogButtonBox.ActionRole)
            op.clicked.connect(lambda: _open_folder(open_folder))
        close = bb.addButton(QDialogButtonBox.Close)
        close.setDefault(True)
        bb.rejected.connect(self.accept)
        close.clicked.connect(self.accept)
        lay.addWidget(bb)


def _open_folder(path: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ------------------------------------------------------------------ raw metadata
class RawMetadataDialog(QDialog):
    def __init__(self, title: str, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Raw metadata: {title}")
        self.resize(820, 640)
        self.payload = payload
        lay = QVBoxLayout(self)
        lay.addWidget(_muted("Every field read from the file plus the detector and timestamp reasoning. "
                             "Share this when adding support for a new camera or glasses model. "
                             "It is shown here only; nothing is sent anywhere."))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        mono = ui_font(9.5)
        mono.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"])
        text.setFont(mono)
        self.json_text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        text.setPlainText(self.json_text)
        lay.addWidget(text, 1)
        bb = QDialogButtonBox()
        copy = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.json_text))
        save = bb.addButton("Save as JSON…", QDialogButtonBox.ActionRole)
        save.clicked.connect(self._save)
        bb.addButton(QDialogButtonBox.Close).clicked.connect(self.accept)
        lay.addWidget(bb)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save raw metadata", "metadata.json", "JSON (*.json)")
        if path:
            Path(path).write_text(self.json_text, encoding="utf-8")
