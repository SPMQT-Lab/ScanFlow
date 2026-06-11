"""Pixel-count selector: a dropdown of standard scan resolutions.

Replaces the QSpinBox (±1 arrows are useless for pixel counts — nobody
scans at 257 px). Clicking shows the standard power-of-two resolutions;
the field stays editable so a non-standard value read back from the rig
can still be displayed or hand-typed.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QComboBox

STANDARD_PIXELS = (64, 128, 256, 512, 1024)


class PixelComboBox(QComboBox):
    """API-compatible stand-in for the pixels QSpinBox.

    Exposes ``value()``, ``setValue()`` and a ``valueChanged(int)`` signal
    so existing spinbox wiring keeps working unchanged.
    """

    valueChanged = Signal(int)

    def __init__(self, default: int = 256, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for v in STANDARD_PIXELS:
            self.addItem(str(v))
        self.setValidator(QIntValidator(8, 8192, self))
        self.setValue(default)
        self.currentTextChanged.connect(self._emit_value_changed)

    def value(self) -> int:
        try:
            return int(self.currentText())
        except ValueError:
            return STANDARD_PIXELS[2]  # mid-list default: 256

    def setValue(self, v: int) -> None:
        self.setCurrentText(str(int(v)))

    def _emit_value_changed(self, text: str) -> None:
        try:
            self.valueChanged.emit(int(text))
        except ValueError:
            pass  # mid-edit empty/partial text — wait for a full number
