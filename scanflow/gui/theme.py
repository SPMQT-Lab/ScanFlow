"""ScanFlow brand themes — light (day) and dark (night)."""

from __future__ import annotations

AMBER = "#F5A800"   # constant across both themes (logo accent)


def _build(
    blue: str, blue_dark: str, blue_light: str,
    bg: str, surface: str, surface2: str,
    text: str, text_muted: str,
    border: str, white: str,
) -> str:
    return f"""
/* ── Global ─────────────────────────────────────────────── */
QWidget {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 13px;
    color: {text};
    background-color: {bg};
}}

QMainWindow, QDialog {{
    background-color: {bg};
}}

/* ── Toolbar ─────────────────────────────────────────────── */
QToolBar {{
    background-color: {blue_dark};
    border: none;
    padding: 4px 8px;
    spacing: 6px;
}}

QToolBar QToolButton {{
    color: {white};
    background: transparent;
    border: 1px solid rgba(255,255,255,0.25);
    border-radius: 4px;
    padding: 4px 12px;
}}

QToolBar QToolButton:hover {{
    background-color: rgba(255,255,255,0.15);
}}

QToolBar QToolButton:pressed {{
    background-color: rgba(255,255,255,0.25);
}}

/* ── Tabs ────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {border};
    border-top: 2px solid {blue};
    background-color: {surface};
}}

QTabBar::tab {{
    background-color: {blue_light};
    color: {blue};
    border: 1px solid {border};
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}

QTabBar::tab:selected {{
    background-color: {surface};
    color: {blue};
    border-bottom: 2px solid {AMBER};
    font-weight: bold;
}}

QTabBar::tab:hover:!selected {{
    background-color: {surface2};
}}

/* ── Status bar ──────────────────────────────────────────── */
QStatusBar {{
    background-color: {blue_dark};
    color: {white};
    border-top: 2px solid {AMBER};
}}

QStatusBar QLabel {{
    color: {white};
    padding: 2px 6px;
}}

/* ── Buttons ─────────────────────────────────────────────── */
QPushButton {{
    background-color: {blue};
    color: {white};
    border: none;
    border-radius: 4px;
    padding: 5px 16px;
    min-width: 60px;
}}

QPushButton:hover {{
    background-color: {blue_dark};
}}

QPushButton:pressed {{
    background-color: {blue_dark};
    padding: 6px 16px 4px 16px;
}}

QPushButton:disabled {{
    background-color: {border};
    color: {text_muted};
}}

QPushButton[accent="true"] {{
    background-color: {AMBER};
    color: #1A1A1A;
    font-weight: bold;
}}

QPushButton[accent="true"]:hover {{
    background-color: #E09A00;
}}

/* ── Group boxes ─────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {border};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 6px;
    font-weight: bold;
    color: {blue};
    background-color: {surface};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {blue};
}}

/* ── Inputs ──────────────────────────────────────────────── */
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    border: 1px solid {border};
    border-radius: 4px;
    padding: 3px 6px;
    background-color: {surface};
    color: {text};
}}

QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus {{
    border: 1.5px solid {blue};
}}

QComboBox::drop-down {{
    border: none;
}}

QComboBox QAbstractItemView {{
    background-color: {surface};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {blue};
    selection-color: {white};
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {blue_light};
    border: none;
    border-radius: 2px;
}}

/* ── Progress bar ────────────────────────────────────────── */
QProgressBar {{
    border: 1px solid {border};
    border-radius: 4px;
    text-align: center;
    background-color: {blue_light};
    color: {text};
    height: 14px;
}}

QProgressBar::chunk {{
    background-color: {AMBER};
    border-radius: 3px;
}}

/* ── Scroll bars ─────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {blue_light};
    width: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 5px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: {blue};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: {blue_light};
    height: 10px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 5px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {blue};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Lists / tables ──────────────────────────────────────── */
QListWidget, QTableWidget, QTreeWidget {{
    border: 1px solid {border};
    border-radius: 4px;
    background-color: {surface};
    alternate-background-color: {blue_light};
    color: {text};
}}

QListWidget::item:selected, QTableWidget::item:selected {{
    background-color: {blue};
    color: {white};
}}

QHeaderView::section {{
    background-color: {blue_light};
    color: {blue};
    padding: 4px 6px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    font-weight: bold;
}}

/* ── Check box ───────────────────────────────────────────── */
QCheckBox {{
    color: {text};
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1.5px solid {border};
    border-radius: 3px;
    background: {surface};
}}

QCheckBox::indicator:checked {{
    background-color: {blue};
    border-color: {blue};
}}

/* ── Labels ──────────────────────────────────────────────── */
QLabel {{
    color: {text};
    background: transparent;
}}

/* ── Splitter ────────────────────────────────────────────── */
QSplitter::handle {{
    background: {border};
}}

/* ── Message box ─────────────────────────────────────────── */
QMessageBox {{
    background-color: {surface};
}}

QMessageBox QLabel {{
    color: {text};
}}
"""


# ── Light (day) theme ─────────────────────────────────────────────────────────

LIGHT_BLUE        = "#2E86DE"   # sky blue — lighter than the original navy
LIGHT_BLUE_DARK   = "#1A5EA8"   # hover / pressed / toolbar / status bar
LIGHT_BLUE_LIGHT  = "#E8F1FC"   # tab background, alt-row tint
LIGHT_BG          = "#F4F7FB"
LIGHT_SURFACE     = "#FFFFFF"
LIGHT_SURFACE2    = "#DCE8F8"
LIGHT_TEXT        = "#1A2C4E"
LIGHT_TEXT_MUTED  = "#6B7FA3"
LIGHT_BORDER      = "#C8D6EE"
LIGHT_WHITE       = "#FFFFFF"

LIGHT_STYLESHEET = _build(
    blue=LIGHT_BLUE, blue_dark=LIGHT_BLUE_DARK, blue_light=LIGHT_BLUE_LIGHT,
    bg=LIGHT_BG, surface=LIGHT_SURFACE, surface2=LIGHT_SURFACE2,
    text=LIGHT_TEXT, text_muted=LIGHT_TEXT_MUTED,
    border=LIGHT_BORDER, white=LIGHT_WHITE,
)


# ── Dark (night) theme ────────────────────────────────────────────────────────

DARK_BLUE        = "#60A5FA"   # blue-400 — bright enough to read on dark bg
DARK_BLUE_DARK   = "#0F172A"   # toolbar / status bar (near-black navy)
DARK_BLUE_LIGHT  = "#1E3050"   # tab background, alt-row
DARK_BG          = "#111827"   # page background
DARK_SURFACE     = "#1E293B"   # panel / card background
DARK_SURFACE2    = "#263348"   # hovered tab
DARK_TEXT        = "#E2E8F0"   # body text
DARK_TEXT_MUTED  = "#64748B"
DARK_BORDER      = "#2D3F5A"
DARK_WHITE       = "#F1F5F9"

DARK_STYLESHEET = _build(
    blue=DARK_BLUE, blue_dark=DARK_BLUE_DARK, blue_light=DARK_BLUE_LIGHT,
    bg=DARK_BG, surface=DARK_SURFACE, surface2=DARK_SURFACE2,
    text=DARK_TEXT, text_muted=DARK_TEXT_MUTED,
    border=DARK_BORDER, white=DARK_WHITE,
)

# Default on startup
STYLESHEET = LIGHT_STYLESHEET

# Keep BLUE exportable for any code that imports it directly
BLUE = LIGHT_BLUE
