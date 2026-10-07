"""Design tokens and stylesheet.

One family (Segoe UI Variable, Windows' own UI face), one accent (a timecode
teal), device colours used consistently in the timeline, chips and table.
The timeline strip is the single expressive element; everything else stays
quiet so the file list reads clearly.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtCore import Qt


@dataclass(frozen=True)
class Tokens:
    bg: str
    surface: str
    surface_alt: str
    ink: str
    muted: str
    line: str
    accent: str
    accent_ink: str
    review: str
    error: str
    ok: str
    selection: str


LIGHT = Tokens(bg="#F2F4F7", surface="#FFFFFF", surface_alt="#F7F8FA", ink="#161C2A", muted="#5A6478",
               line="#D9DEE7", accent="#0A7C78", accent_ink="#FFFFFF", review="#A85A06", error="#B42318",
               ok="#2F7D4F", selection="#D5ECEA")
DARK = Tokens(bg="#11151B", surface="#19202A", surface_alt="#1E2632", ink="#E5E9F0", muted="#95A0B4",
              line="#2A3341", accent="#35C2B9", accent_ink="#06201E", review="#E8A04A", error="#F07A6E",
              ok="#6CC28E", selection="#1E4744")

DEVICE_COLORS = {
    "RAYBAN": "#3156D3", "OAKLEY": "#8B5CF6", "APPLE": "#6B7280", "SONY": "#7A3FC8",
    "CANON": "#C2410C", "DJI": "#4D7C0F", "GOPRO": "#0E7490", "UNKNOWN": "#B25E09",
}
DEVICE_COLORS_DARK = {
    "RAYBAN": "#6E8EF7", "OAKLEY": "#B39BFA", "APPLE": "#A7AFBC", "SONY": "#B08AE8",
    "CANON": "#F08A50", "DJI": "#97C459", "GOPRO": "#4FB6CC", "UNKNOWN": "#E8A04A",
}
OTHER_COLOR = ("#64748B", "#94A3B8")


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def tokens() -> Tokens:
    return DARK if is_dark() else LIGHT


def device_color(code: str) -> QColor:
    dark = is_dark()
    table = DEVICE_COLORS_DARK if dark else DEVICE_COLORS
    return QColor(table.get(code, OTHER_COLOR[1 if dark else 0]))


def ui_font(point_size: float | None = None, weight: QFont.Weight | None = None) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Helvetica Neue", "Arial"])
    if point_size:
        f.setPointSizeF(point_size)
    if weight is not None:
        f.setWeight(weight)
    return f


def tabular(font: QFont) -> QFont:
    """Tabular figures so times and filenames line up in columns (Qt 6.7+)."""
    try:
        font.setFeature(QFont.Tag("tnum"), 1)  # type: ignore[attr-defined]
    except Exception:
        pass
    return font


def apply_palette(app) -> None:
    t = tokens()
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(t.bg))
    pal.setColor(QPalette.WindowText, QColor(t.ink))
    pal.setColor(QPalette.Base, QColor(t.surface))
    pal.setColor(QPalette.AlternateBase, QColor(t.surface_alt))
    pal.setColor(QPalette.Text, QColor(t.ink))
    pal.setColor(QPalette.Button, QColor(t.surface))
    pal.setColor(QPalette.ButtonText, QColor(t.ink))
    pal.setColor(QPalette.Highlight, QColor(t.selection))
    pal.setColor(QPalette.HighlightedText, QColor(t.ink))
    pal.setColor(QPalette.ToolTipBase, QColor(t.surface))
    pal.setColor(QPalette.ToolTipText, QColor(t.ink))
    pal.setColor(QPalette.PlaceholderText, QColor(t.muted))
    pal.setColor(QPalette.Link, QColor(t.accent))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.muted))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t.muted))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t.muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))


def stylesheet(t: Tokens) -> str:
    return f"""
    QMainWindow, QDialog {{ background: {t.bg}; }}
    QLabel#title {{ font-size: 19pt; font-weight: 600; color: {t.ink}; }}
    QLabel#subtitle {{ font-size: 10.5pt; color: {t.muted}; }}
    QLabel#privacy {{ color: {t.muted}; font-size: 9.5pt; }}
    QLabel#muted {{ color: {t.muted}; }}
    QLabel#sectionTitle {{ font-weight: 600; font-size: 10.5pt; }}
    QLabel#count {{ font-size: 10.5pt; font-weight: 600; }}

    QPushButton {{
        background: {t.surface}; color: {t.ink}; border: 1px solid {t.line};
        border-radius: 6px; padding: 6px 14px; min-height: 20px;
    }}
    QPushButton:hover {{ border-color: {t.muted}; }}
    QPushButton:pressed {{ background: {t.surface_alt}; }}
    QPushButton:disabled {{ color: {t.muted}; background: {t.surface_alt}; border-color: {t.line}; }}
    QPushButton:focus {{ border: 2px solid {t.accent}; padding: 5px 13px; }}
    QPushButton#primary {{ background: {t.accent}; color: {t.accent_ink}; border: 1px solid {t.accent};
                           font-weight: 600; }}
    QPushButton#primary:disabled {{ background: {t.line}; color: {t.muted}; border-color: {t.line}; }}
    QPushButton#danger {{ color: {t.error}; border-color: {t.error}; font-weight: 600; }}
    QPushButton#danger:disabled {{ color: {t.muted}; border-color: {t.line}; }}
    QPushButton#link {{ border: none; background: transparent; color: {t.accent}; padding: 2px 4px; }}

    QTableView, QTreeWidget, QPlainTextEdit, QListWidget {{
        background: {t.surface}; border: 1px solid {t.line}; border-radius: 8px;
        gridline-color: transparent; selection-background-color: {t.selection}; selection-color: {t.ink};
    }}
    QTableView::item, QTreeWidget::item {{ padding: 3px 6px; }}
    QHeaderView::section {{
        background: {t.surface}; color: {t.muted}; border: none; border-bottom: 1px solid {t.line};
        padding: 6px 8px; font-weight: 600;
    }}
    QTableView QTableCornerButton::section {{ background: {t.surface}; border: none; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
        background: {t.surface}; border: 1px solid {t.line}; border-radius: 6px; padding: 5px 8px;
        color: {t.ink};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 2px solid {t.accent}; padding: 4px 7px; }}
    QGroupBox {{ border: 1px solid {t.line}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.ink}; font-weight: 600; }}
    QProgressBar {{ border: none; background: {t.line}; border-radius: 3px; max-height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
    QSplitter::handle {{ background: transparent; }}
    QStatusBar {{ color: {t.muted}; }}
    QToolTip {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.line}; padding: 6px; }}
    QMenu {{ background: {t.surface}; border: 1px solid {t.line}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.selection}; color: {t.ink}; }}
    QMenu::separator {{ height: 1px; background: {t.line}; margin: 4px 8px; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    """
