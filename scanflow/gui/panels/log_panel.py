"""Session log panel."""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)
from PySide6.QtGui import QTextCursor
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
        btn_row.addStretch()
        save_btn = QPushButton("Save log…")
        save_btn.setToolTip(
            "Save the current Log tab text to a .log file. For full Python-"
            "level diagnostics (warnings, COM errors, runner internals), use "
            "the rolling daily log file ScanFlow writes automatically to "
            "C:\\ScanflowMonitor\\logs (or the SCANFLOW_LOG_DIR env var)."
        )
        save_btn.clicked.connect(self._save_log)
        btn_row.addWidget(save_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._log.clear)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

    def append(self, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{ts}] {message}")
        self._log.moveCursor(QTextCursor.End)

    def append_error(self, message: str) -> None:
        self.append(f"ERROR: {message}")

    def _save_log(self) -> None:
        if not self._log.toPlainText().strip():
            QMessageBox.information(self, "Empty log", "Nothing to save yet.")
            return
        default = f"scanflow_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", default, "Log files (*.log *.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self._log.toPlainText(), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.append(f"log saved → {path}")
