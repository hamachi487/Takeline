"""App icon, drawn in code so it is crisp at every size and needs no asset file.

Three clips of different lengths stepping down a timeline, with a playhead:
footage put back into the order it was shot.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap


def render_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
    p.fillPath(bg, QColor("#0A7C78"))
    clips = [(10, 15, 26, "#FFFFFF"), (20, 28, 30, "#BFE7E4"), (32, 41, 22, "#FFFFFF")]
    for x, y, w, col in clips:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x * s, y * s, w * s, 8 * s), 3 * s, 3 * s)
        p.fillPath(path, QColor(col))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#F6C453"))
    p.drawRoundedRect(QRectF(46 * s, 10 * s, 3 * s, 44 * s), 1.5 * s, 1.5 * s)
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render_icon(sz)))
    return icon


def save_ico(path: str) -> bool:
    """Write a multi-resolution Windows .ico (used by the PyInstaller build)."""
    try:
        import io

        from PIL import Image
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        render_icon(256).save(buf, "PNG")
        img = Image.open(io.BytesIO(bytes(buf.data())))
        img.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return True
    except Exception:
        return render_icon(256).save(path, "ico")
