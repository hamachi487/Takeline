"""Preview table model."""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from ..pipeline import MediaItem
from .style import device_color, tabular, tokens, ui_font

COLUMNS = ["", "Original", "Device", "Capture Time", "Source", "Confidence", "New Filename", "Status"]
COL_INCLUDE, COL_ORIGINAL, COL_DEVICE, COL_TIME, COL_SOURCE, COL_CONF, COL_NEW, COL_STATUS = range(8)
SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2


def _natural(text: str) -> str:
    return re.sub(r"\d+", lambda m: m.group(0).zfill(12), text.casefold())


class ItemTableModel(QAbstractTableModel):
    includeToggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[MediaItem] = []
        self.known_rows = 0  # rows the view knows about (updated only on reset)
        self._dots: dict[str, QIcon] = {}
        self._font_tab = tabular(ui_font())

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self.items = items
        self.known_rows = len(items)
        self.endResetModel()

    def refresh(self) -> None:
        if self.known_rows:
            self.dataChanged.emit(self.index(0, 0), self.index(self.known_rows - 1, len(COLUMNS) - 1))

    def clear_cache(self) -> None:
        self._dots.clear()

    def item_at(self, row: int) -> MediaItem:
        return self.items[row]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.known_rows

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section == COL_INCLUDE:
            return "Include this file when renaming or copying"
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_INCLUDE:
            f |= Qt.ItemIsUserCheckable
        return f

    def _dot(self, code: str) -> QIcon:
        if code not in self._dots:
            pm = QPixmap(12, 12)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(device_color(code))
            p.drawEllipse(2, 2, 8, 8)
            p.end()
            self._dots[code] = QIcon(pm)
        return self._dots[code]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        col = index.column()
        if role == ID_ROLE:
            return it.id
        if role == Qt.CheckStateRole and col == COL_INCLUDE:
            return Qt.Checked if it.include else Qt.Unchecked
        if role == Qt.DisplayRole:
            return self._display(it, col)
        if role == SORT_ROLE:
            return self._sort_key(it, col)
        if role == Qt.ToolTipRole:
            return self._tooltip(it, col)
        if role == Qt.DecorationRole and col == COL_DEVICE:
            return self._dot(it.device)
        if role == Qt.FontRole and col in (COL_TIME, COL_NEW, COL_CONF):
            return self._font_tab
        if role == Qt.ForegroundRole:
            t = tokens()
            level, _ = it.status
            if col == COL_STATUS:
                return QColor({"ok": t.ok, "review": t.review, "error": t.error}[level])
            if col == COL_CONF and it.device_confidence < 0.6:
                return QColor(t.review)
            if col == COL_SOURCE and it.timestamp and it.timestamp.is_fallback and not it.time_override:
                return QColor(t.review)
            if col == COL_NEW and (not it.include or it.unchanged):
                return QColor(t.muted)
            if col in (COL_SOURCE,):
                return QColor(t.muted)
        return None

    def _display(self, it: MediaItem, col: int):
        if col == COL_ORIGINAL:
            return it.path.name
        if col == COL_DEVICE:
            return it.device_name
        if col == COL_TIME:
            return it.capture_time.strftime("%Y-%m-%d %H:%M:%S") if it.capture_time else "—"
        if col == COL_SOURCE:
            return it.time_source
        if col == COL_CONF:
            return f"{it.device_confidence:.0%}" if not it.device_override else "Manual"
        if col == COL_NEW:
            if not it.proposed_name:
                return "—"
            if it.unchanged:
                return f"{it.proposed_name}  (unchanged)"
            return it.proposed_name
        if col == COL_STATUS:
            return it.status[1]
        return None

    def _sort_key(self, it: MediaItem, col: int):
        if col == COL_INCLUDE:
            return 1 if it.include else 0
        if col == COL_TIME:
            return it.capture_time.timestamp() if it.capture_time else 1e12
        if col == COL_CONF:
            return float(it.device_confidence)
        if col == COL_ORIGINAL:
            return _natural(it.path.name)
        if col == COL_STATUS:
            return {"error": 0, "review": 1, "ok": 2}[it.status[0]]
        return _natural(str(self._display(it, col) or ""))

    def _tooltip(self, it: MediaItem, col: int):
        if col == COL_ORIGINAL:
            return str(it.path)
        if col == COL_DEVICE:
            if it.device_override:
                return "Set manually"
            if it.detection:
                lines = [f"{it.detection.display_name}  ({it.detection.confidence:.0%})"]
                if it.detection.model:
                    lines.append(f"Model: {it.detection.model}")
                lines += [f"• {r}" for r in it.detection.reasons[:8]]
                return "\n".join(lines)
        if col in (COL_TIME, COL_SOURCE):
            if it.time_override:
                return "Capture time set manually"
            ts = it.timestamp
            if ts:
                lines = [f"{ts.label}  ({ts.confidence:.0%})", ts.interpretation] + [f"• {n}" for n in ts.notes]
                return "\n".join(lines)
            return "No capture date found"
        if col == COL_CONF:
            ts = it.timestamp
            text = f"Device confidence: {it.device_confidence:.0%}"
            if ts and not it.time_override:
                text += f"\nCapture-time confidence: {ts.confidence:.0%}"
            return text
        if col == COL_NEW and it.suffix_n:
            return "Another file has the same device and capture time; a suffix keeps both names unique."
        if col == COL_STATUS:
            return it.status[1]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == COL_INCLUDE:
            checked = value in (Qt.Checked, 2) or getattr(value, "value", None) == 2
            self.items[index.row()].include = bool(checked)
            self.dataChanged.emit(index, index)
            self.includeToggled.emit()
            return True
        return False


class SortProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(False)

    def lessThan(self, left, right):
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            if a == b:
                # stable tiebreak: capture time, then name
                m = self.sourceModel()
                ia, ib = m.item_at(left.row()), m.item_at(right.row())
                ka = (ia.capture_time.timestamp() if ia.capture_time else 1e12, _natural(ia.path.name))
                kb = (ib.capture_time.timestamp() if ib.capture_time else 1e12, _natural(ib.path.name))
                return ka < kb
            return a < b
        except TypeError:
            return str(a) < str(b)
