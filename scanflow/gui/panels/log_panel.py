"""Session log panel."""

from __future__ import annotations

import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QPushButton, QHBoxLayout
from PySide6.QtGui import QTextCursor, QColor
from PySide6.QtCore import Qt


class LogPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        layout.addWidget(self._log)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._log.clear)
        btn_row.addStretch()
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

    def append(self, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{ts}] {message}")
        self._log.moveCursor(QTextCursor.End)

    def append_error(self, message: str) -> None:
        self.append(f"ERROR: {message}")
