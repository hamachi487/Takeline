"""Metadata inspector: everything known about the selected file, grouped by category."""

from __future__ import annotations

import datetime as dt
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QMenu, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..detection.base import MAKE_TAGS, MODEL_TAGS, SOFTWARE_TAGS
from ..pipeline import MediaItem
from ..timestamps.timezone import format_offset
from .style import tokens

CAMERA_TAGS = MAKE_TAGS | MODEL_TAGS | SOFTWARE_TAGS | {
    "LensModel", "LensMake", "SerialNumber", "LensSerialNumber", "CameraSerialNumber",
    "MakerNoteType", "HandlerDescription", "CompressorName", "SerialNumberHash"}
QT_GROUPS = ("QuickTime", "Keys", "ItemList", "UserData", "XML", "Canon")
EXIF_GROUPS = ("IFD0", "ExifIFD", "GPS", "IFD1", "InteropIFD", "MakerNotes", "Apple")
DATE_RX = re.compile(r"(Date|Time)(?!.*(Duration|Exposure|Zone|Offset|Scale))")
_SUFFIX = re.compile(r" \(\d+\)$")


def fmt(value) -> str:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            return f"{value:%Y-%m-%d %H:%M:%S} ({format_offset(value.utcoffset())})"
        return f"{value:%Y-%m-%d %H:%M:%S}"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(fmt(v) for v in value)
    return str(value)


def category_of(group: str) -> str:
    if group in ("File", "System"):
        return "File"
    if group == "Container":
        return "Container"
    if group in QT_GROUPS or group.startswith("Track"):
        return "QuickTime"
    if group in EXIF_GROUPS:
        return "EXIF"
    if group.startswith("XMP"):
        return "XMP"
    if group == "IPTC":
        return "IPTC"
    return "Other"


class Inspector(QWidget):
    ORDER = ["Summary", "Capture time candidates", "File", "Container", "Camera / Device", "Dates",
             "QuickTime", "EXIF", "XMP", "IPTC", "Other"]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = QLabel("Metadata")
        self.title.setObjectName("sectionTitle")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Field", "Value"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().resizeSection(0, 190)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setWordWrap(False)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.show_item(None)

    def show_item(self, it: MediaItem | None) -> None:
        self.tree.clear()
        if it is None:
            self.title.setText("Metadata")
            QTreeWidgetItem(self.tree, ["Select a file to see its metadata.", ""])
            return
        self.title.setText(it.path.name)
        sections = {name: QTreeWidgetItem([name, ""]) for name in self.ORDER}
        bold = self.font()
        bold.setBold(True)
        for s in sections.values():
            s.setFont(0, bold)

        summ = sections["Summary"]
        self._add(summ, "Device", f"{it.device_name}  ({it.device_confidence:.0%})")
        if it.model:
            self._add(summ, "Model", it.model)
        if it.detection and not it.device_override:
            reasons = self._add(summ, "Why", f"{len(it.detection.reasons)} signal(s)")
            for r in it.detection.reasons:
                self._add(reasons, "", r)
            if it.detection.alternatives:
                self._add(summ, "Also considered",
                          ", ".join(f"{d} {c:.0%}" for d, c in it.detection.alternatives))
        ct = it.capture_time
        self._add(summ, "Capture time", ct.strftime("%Y-%m-%d %H:%M:%S") if ct else "Not found")
        ts = it.timestamp
        if it.time_override:
            self._add(summ, "Time source", "Set manually")
        elif ts:
            self._add(summ, "Time source", f"{ts.source}  ({ts.label})")
            self._add(summ, "Interpretation", ts.interpretation)
            self._add(summ, "Time confidence", f"{ts.confidence:.0%}")
            for n in ts.notes:
                self._add(summ, "Note", n)
        self._add(summ, "New filename", it.proposed_name or "—")
        self._add(summ, "Status", it.status[1])
        if it.duplicate_of:
            self._add(summ, "Same content as", str(it.duplicate_of))
        rec = it.record
        if rec:
            self._add(summ, "Read by", ", ".join(rec.backends) or "—")
            for e in rec.errors:
                self._add(summ, "Error", e)
            for w in rec.warnings[:20]:
                self._add(summ, "Warning", w)

        if ts:
            cand = sections["Capture time candidates"]
            for c in ts.candidates:
                node = self._add(cand, c.source,
                                 f"{c.interp.local:%Y-%m-%d %H:%M:%S}  (tier {c.tier}, {c.confidence:.0%})")
                self._add(node, "Raw value", fmt(c.raw))
                self._add(node, "Meaning", c.label)
                self._add(node, "Interpretation", c.interp.explanation)

        if rec:
            for group, tag, value in rec.items():
                qname = f"{group}:{tag}"
                base = _SUFFIX.sub("", tag)
                self._add(sections[category_of(group)], qname if category_of(group) in ("QuickTime", "EXIF", "XMP", "Other", "File") else tag, fmt(value))
                if base in CAMERA_TAGS and group not in ("File", "System"):
                    self._add(sections["Camera / Device"], qname, fmt(value))
                if (DATE_RX.search(base) or isinstance(value, dt.datetime)) and base not in ("Duration",):
                    self._add(sections["Dates"], qname, fmt(value))

        for name in self.ORDER:
            s = sections[name]
            if s.childCount():
                s.setText(1, f"{s.childCount()}" if name not in ("Summary",) else "")
                self.tree.addTopLevelItem(s)
                if name in ("Summary", "Camera / Device", "Dates"):
                    s.setExpanded(True)

    def _add(self, parent: QTreeWidgetItem, key: str, value: str) -> QTreeWidgetItem:
        node = QTreeWidgetItem(parent, [key, value])
        node.setToolTip(1, value)
        if key in ("Error",):
            node.setForeground(1, self.palette().brush(self.foregroundRole()))
            from PySide6.QtGui import QColor
            node.setForeground(1, QColor(tokens().error))
        return node

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        a1 = menu.addAction("Copy value")
        a2 = menu.addAction("Copy field and value")
        a3 = menu.addAction("Copy everything")
        act = menu.exec(self.tree.viewport().mapToGlobal(pos))
        sel = self.tree.selectedItems()
        if act == a1 and sel:
            QGuiApplication.clipboard().setText("\n".join(i.text(1) for i in sel))
        elif act == a2 and sel:
            QGuiApplication.clipboard().setText("\n".join(f"{i.text(0)}: {i.text(1)}" for i in sel))
        elif act == a3:
            lines = []

            def walk(node, depth):
                lines.append("  " * depth + (f"{node.text(0)}: {node.text(1)}" if node.text(1) else node.text(0)))
                for k in range(node.childCount()):
                    walk(node.child(k), depth + 1)
            for k in range(self.tree.topLevelItemCount()):
                walk(self.tree.topLevelItem(k), 0)
            QGuiApplication.clipboard().setText("\n".join(lines))
