"""The timeline strip.

Empty: a drop target drawn like an editing-timeline ruler.
Loaded: one lane per camera, each file a tick at its capture time, so a mixed
Ray-Ban + iPhone folder shows how the clips interleave before anything is
renamed. Clicking a tick selects that file in the table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QToolTip, QVBoxLayout, QWidget

from .style import device_color, tabular, tokens, ui_font

STEPS = [1, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200,
         86400, 2 * 86400, 7 * 86400, 30 * 86400, 91 * 86400, 365 * 86400]
MAX_LANES = 6
GUTTER = 128
LANE_H = 22


@dataclass
class Tick:
    id: int
    when: dt.datetime
    device: str
    device_name: str
    label: str


class TimelineStrip(QWidget):
    filesDropped = Signal(list)
    itemClicked = Signal(int)
    selectFilesRequested = Signal()
    selectFolderRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.ticks: list[Tick] = []
        self.lanes: list[tuple[str, str]] = []
        self.selected: set[int] = set()
        self._drag = False
        self._hit: list[tuple[QRectF, Tick]] = []

        self.btn_files = QPushButton("Select Files")
        self.btn_folder = QPushButton("Select Folder")
        self.btn_files.clicked.connect(self.selectFilesRequested)
        self.btn_folder.clicked.connect(self.selectFolderRequested)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_files)
        row.addSpacing(8)
        row.addWidget(self.btn_folder)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 36)
        lay.addStretch(1)
        lay.addLayout(row)
        self._update_mode()

    # ------------------------------------------------------------------ data
    def set_items(self, ticks: list[Tick]) -> None:
        self.ticks = [t for t in ticks if t.when is not None]
        counts: dict[str, list] = {}
        for t in self.ticks:
            counts.setdefault(t.device, [t.device_name, 0])
            counts[t.device][1] += 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1][1], kv[0]))
        lanes = [(code, name.replace(" (manual)", "")) for code, (name, _n) in ordered]
        if len(lanes) > MAX_LANES:
            keep = {c for c, _ in lanes[:MAX_LANES - 1]}
            lanes = lanes[:MAX_LANES - 1] + [("_OTHER", "Other")]
            for t in self.ticks:
                if t.device not in keep:
                    t.device = "_OTHER"
        self.lanes = lanes
        self._update_mode()
        self.update()

    def set_selected(self, ids: set[int]) -> None:
        self.selected = set(ids)
        self.update()

    def _update_mode(self) -> None:
        empty = not self.ticks
        self.btn_files.setVisible(empty)
        self.btn_folder.setVisible(empty)
        if empty:
            self.setFixedHeight(196)
        else:
            self.setFixedHeight(52 + LANE_H * max(1, len(self.lanes)) + 30)

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _ev) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(t.accent if self._drag else t.line), 2 if self._drag else 1.2,
                      Qt.SolidLine if (self.ticks or self._drag) else Qt.DashLine))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(r, 10, 10)
        if self.ticks:
            self._paint_timeline(p, r)
        else:
            self._paint_empty(p, r)
        p.end()

    def _paint_empty(self, p: QPainter, r: QRectF) -> None:
        t = tokens()
        # Ruler ticks along the top edge, like an editing timeline.
        p.setPen(QPen(QColor(t.line), 1))
        x = r.left() + 18
        i = 0
        while x < r.right() - 18:
            h = 10 if i % 5 == 0 else 5
            p.drawLine(QPointF(x, r.top() + 12), QPointF(x, r.top() + 12 + h))
            x += 12
            i += 1
        p.setPen(QColor(t.ink))
        p.setFont(ui_font(15, QFont.Weight.DemiBold))
        p.drawText(QRectF(r.left(), r.top() + 40, r.width(), 32), Qt.AlignCenter,
                   "Release to add these files" if self._drag else "Drop photos and videos here")
        p.setPen(QColor(t.muted))
        p.setFont(ui_font(10))
        p.drawText(QRectF(r.left(), r.top() + 74, r.width(), 22), Qt.AlignCenter,
                   "Folders work too. Nothing is renamed until you confirm.")

    def _range(self) -> tuple[dt.datetime, dt.datetime]:
        lo = min(t.when for t in self.ticks)
        hi = max(t.when for t in self.ticks)
        if (hi - lo).total_seconds() < 60:
            lo -= dt.timedelta(seconds=30)
            hi += dt.timedelta(seconds=30)
        pad = (hi - lo) * 0.03
        return lo - pad, hi + pad

    def _paint_timeline(self, p: QPainter, r: QRectF) -> None:
        t = tokens()
        lo, hi = self._range()
        span = (hi - lo).total_seconds()
        left, right = r.left() + GUTTER, r.right() - 18
        top = r.top() + 34
        axis_y = top + LANE_H * len(self.lanes) + 4

        def x_of(when: dt.datetime) -> float:
            return left + (when - lo).total_seconds() / span * (right - left)

        # Header: date range and count
        p.setPen(QColor(t.ink))
        p.setFont(tabular(ui_font(10, QFont.Weight.DemiBold)))
        first, last = min(x.when for x in self.ticks), max(x.when for x in self.ticks)
        header = first.strftime("%Y-%m-%d")
        if last.date() != first.date():
            header += "  to  " + last.strftime("%Y-%m-%d")
        p.drawText(QRectF(r.left() + 16, r.top() + 8, 400, 20), Qt.AlignLeft | Qt.AlignVCenter, header)
        p.setPen(QColor(t.muted))
        p.setFont(ui_font(9))
        p.drawText(QRectF(r.right() - 420, r.top() + 8, 404, 20), Qt.AlignRight | Qt.AlignVCenter,
                   "Capture timeline. Drop more files here to add them.")

        # Grid + ruler labels
        step = next((s for s in STEPS if span / s <= 8), STEPS[-1])
        first_tick = lo.replace(microsecond=0)
        epoch = dt.datetime(2000, 1, 1)
        offset = (first_tick - epoch).total_seconds() % step
        cur = first_tick + dt.timedelta(seconds=(step - offset) % step)
        p.setFont(tabular(ui_font(8.5)))
        while cur <= hi:
            x = x_of(cur)
            p.setPen(QPen(QColor(t.line), 1))
            p.drawLine(QPointF(x, top - 2), QPointF(x, axis_y))
            label = cur.strftime("%H:%M:%S" if step < 60 else "%H:%M") if step < 86400 else cur.strftime("%b %d")
            if step < 86400 and (hi - lo).days >= 1:
                label = cur.strftime("%b %d %H:%M")
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(x - 50, axis_y + 4, 100, 16), Qt.AlignHCenter | Qt.AlignTop, label)
            cur += dt.timedelta(seconds=step)

        # Lanes
        self._hit = []
        lane_index = {code: i for i, (code, _n) in enumerate(self.lanes)}
        for i, (code, name) in enumerate(self.lanes):
            y = top + i * LANE_H
            col = device_color(code) if code != "_OTHER" else QColor(t.muted)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QRectF(r.left() + 16, y + LANE_H / 2 - 4, 8, 8))
            p.setPen(QColor(t.ink))
            p.setFont(ui_font(9.5))
            fm = p.fontMetrics()
            p.drawText(QRectF(r.left() + 30, y, GUTTER - 36, LANE_H), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, int(GUTTER - 38)))
            p.setPen(QPen(QColor(t.line), 1, Qt.DotLine))
            p.drawLine(QPointF(left, y + LANE_H / 2), QPointF(right, y + LANE_H / 2))
        sel_ticks = []
        for tk in sorted(self.ticks, key=lambda k: k.id in self.selected):
            i = lane_index.get(tk.device, len(self.lanes) - 1)
            x = x_of(tk.when)
            y = top + i * LANE_H
            col = device_color(tk.device) if tk.device != "_OTHER" else QColor(t.muted)
            rect = QRectF(x - 2, y + 4, 4, LANE_H - 8)
            if tk.id in self.selected:
                sel_ticks.append(x)
                rect = QRectF(x - 3, y + 1, 6, LANE_H - 2)
                p.setPen(QPen(QColor(t.ink), 1.2))
            else:
                p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(rect, 2, 2)
            self._hit.append((QRectF(x - 5, y, 10, LANE_H), tk))
        for x in sel_ticks[:1]:
            p.setPen(QPen(QColor(t.accent), 1.5))
            p.drawLine(QPointF(x, top - 6), QPointF(x, axis_y))

    # ------------------------------------------------------------------ input
    def _tick_at(self, pos) -> Tick | None:
        best, best_d = None, 1e9
        for rect, tk in self._hit:
            if rect.contains(pos):
                d = abs(rect.center().x() - pos.x())
                if d < best_d:
                    best, best_d = tk, d
        return best

    def mouseMoveEvent(self, ev) -> None:
        tk = self._tick_at(ev.position())
        if tk:
            QToolTip.showText(ev.globalPosition().toPoint(),
                              f"{tk.label}\n{tk.device_name}\n{tk.when:%Y-%m-%d %H:%M:%S}", self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, ev) -> None:
        tk = self._tick_at(ev.position())
        if tk:
            self.itemClicked.emit(tk.id)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._drag = True
            self.update()

    def dragLeaveEvent(self, _ev) -> None:
        self._drag = False
        self.update()

    def dropEvent(self, ev) -> None:
        self._drag = False
        self.update()
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.filesDropped.emit(paths)
