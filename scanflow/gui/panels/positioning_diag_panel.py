"""Positioning diagnostic — characterise CreaTec's XY offset commands.

ScanFlow currently has three plausible APIs for moving the scan offset
and each one has misbehaved in different ways on the real rig. Rather
than keep guessing, this tab lets you fire a single offset command,
read the tip XY position before/after, and accumulate a table of
(command, sent, before, after, delta) rows that we can use to build
the real positioning algorithm.

Safety:
  * Every send is a single one-shot operation — no loops.
  * Default magnitudes are tiny (1 V/V, 10 pixels) and the spinbox
    ranges are clamped.
  * Spec X = 0 and Y = 0 gives a no-op — useful for read-only tests.
  * No scanning is triggered. The tip will move; the feedback loop
    keeps the current setpoint, so the same precautions as moving the
    tip manually in STMAFM apply.

Workflow:
  1. Click "Read tip position" to confirm getxypos() works.
  2. Pick a Command, set small X / Y values, click "Send".
  3. The panel auto-reads the tip position before and after, logs the
     delta. Photograph or note STMAFM's offset display for cross-check.
  4. Repeat for the three commands and a couple of magnitudes.
  5. "Save CSV" → send the file to the developer; they translate the
     results into a working positioning algorithm.
"""

from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from scanflow.core import STMClient

log = logging.getLogger(__name__)


COMMANDS = [
    "setxyoffvolt(x, y)",
    "setxyoffpixel(x, y)",
    "SETXYOFF.IMAGECOORD (x, y)",
]


class PositioningDiagPanel(QWidget):
    """Single-shot, read-before/after diagnostic for offset-move commands."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._rows: list[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Warning banner
        warn = QLabel(
            "<b>⚠ This panel moves the tip.</b> Each Send fires one offset "
            "command — the tip will physically translate. Use small values, "
            "make sure the surface is clear, and watch STMAFM."
        )
        warn.setTextFormat(Qt.RichText)
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background-color: #fff8e1; color: #6d4c00; "
            "padding: 8px; border: 1px solid #ffd54f; border-radius: 4px;"
        )
        root.addWidget(warn)

        # Read-position section
        readbox = QGroupBox("Tip position")
        rg = QGridLayout(readbox)
        self._pos_label = QLabel("—")
        self._pos_label.setStyleSheet("font-family: monospace; font-size: 11pt;")
        rg.addWidget(QLabel("<b>Current XY:</b>"), 0, 0)
        rg.addWidget(self._pos_label, 0, 1)
        read_btn = QPushButton("Read tip position")
        read_btn.clicked.connect(self._read_position)
        rg.addWidget(read_btn, 0, 2)
        root.addWidget(readbox)

        # Command section
        cmdbox = QGroupBox("Send one offset command")
        cg = QGridLayout(cmdbox)
        cg.addWidget(QLabel("Command:"), 0, 0)
        self._cmd_combo = QComboBox()
        self._cmd_combo.addItems(COMMANDS)
        cg.addWidget(self._cmd_combo, 0, 1, 1, 3)

        cg.addWidget(QLabel("X value:"), 1, 0)
        self._x_spin = QDoubleSpinBox()
        self._x_spin.setRange(-100.0, 100.0)
        self._x_spin.setDecimals(4)
        self._x_spin.setSingleStep(0.5)
        self._x_spin.setValue(0.0)
        cg.addWidget(self._x_spin, 1, 1)

        cg.addWidget(QLabel("Y value:"), 1, 2)
        self._y_spin = QDoubleSpinBox()
        self._y_spin.setRange(-100.0, 100.0)
        self._y_spin.setDecimals(4)
        self._y_spin.setSingleStep(0.5)
        self._y_spin.setValue(0.0)
        cg.addWidget(self._y_spin, 1, 3)

        self._unit_hint = QLabel("<i>Units depend on the command (V for volt, "
                                 "pixels for the other two).</i>")
        self._unit_hint.setTextFormat(Qt.RichText)
        cg.addWidget(self._unit_hint, 2, 0, 1, 4)

        send_btn = QPushButton("Send command")
        send_btn.setStyleSheet("font-weight: bold;")
        send_btn.clicked.connect(self._send_command)
        cg.addWidget(send_btn, 3, 0, 1, 4)
        root.addWidget(cmdbox)

        # Results table
        table_box = QGroupBox("Results")
        tg = QVBoxLayout(table_box)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Time", "Command", "Sent X", "Sent Y",
             "Before XY", "After XY", "Δ"],
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        tg.addWidget(self._table)

        button_row = QHBoxLayout()
        button_row.addStretch()
        clear_btn = QPushButton("Clear table")
        clear_btn.clicked.connect(self._clear_table)
        button_row.addWidget(clear_btn)
        save_btn = QPushButton("Save CSV…")
        save_btn.clicked.connect(self._save_csv)
        button_row.addWidget(save_btn)
        tg.addLayout(button_row)
        root.addWidget(table_box, 1)

    # ------------------------------------------------------------------
    # Position read
    # ------------------------------------------------------------------

    def _read_position(self) -> Optional[Tuple[float, float]]:
        if not self._stm.connected and not self._stm.is_mock:
            self._pos_label.setText("(not connected)")
            return None
        try:
            xy = self._stm.tip_xy_position()
        except Exception as e:
            log.exception("tip_xy_position failed")
            self._pos_label.setText(f"<error: {e}>")
            return None
        if xy is None:
            self._pos_label.setText(
                "<i>getxypos() returned None — user dispatch unavailable on this rig?</i>"
            )
            return None
        x, y = xy
        self._pos_label.setText(f"X = {x:+.6f}    Y = {y:+.6f}")
        self.log_message.emit(f"tip XY = ({x:+.6f}, {y:+.6f})")
        return (x, y)

    # ------------------------------------------------------------------
    # Send a one-shot command
    # ------------------------------------------------------------------

    def _send_command(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "Not connected",
                                "Connect to the STM (or Mock) first.")
            return
        cmd = self._cmd_combo.currentText()
        x = self._x_spin.value()
        y = self._y_spin.value()

        # Pre-position read
        before = self._read_position()

        # Fire the command
        try:
            if cmd.startswith("setxyoffvolt"):
                self._stm.scan.set_offset_volts(x, y)
                sent_repr = f"setxyoffvolt({x:.4f}, {y:.4f}) V"
            elif cmd.startswith("setxyoffpixel"):
                self._stm.scan.nudge_offset_pixels(x, y)
                sent_repr = f"setxyoffpixel({x:.4f}, {y:.4f}) px"
            else:
                self._stm.scan.set_offset_image_coord(int(round(x)), int(round(y)))
                sent_repr = f"SETXYOFF.IMAGECOORD({int(round(x))}, {int(round(y))})"
        except Exception as e:
            log.exception("offset command failed")
            QMessageBox.critical(self, "Command failed", str(e))
            return

        self.log_message.emit(f"sent: {sent_repr}")

        # Brief settle, then post-position read
        time.sleep(0.2)
        after = self._read_position()

        # Row to table
        if before is not None and after is not None:
            dx = after[0] - before[0]
            dy = after[1] - before[1]
            delta_str = f"({dx:+.4f}, {dy:+.4f})"
        else:
            delta_str = "n/a"
        self._append_row({
            "time": datetime.now().strftime("%H:%M:%S"),
            "command": cmd,
            "sent_x": x,
            "sent_y": y,
            "before": _fmt_xy(before),
            "after": _fmt_xy(after),
            "delta": delta_str,
        })

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def _append_row(self, row: dict) -> None:
        self._rows.append(row)
        r = self._table.rowCount()
        self._table.insertRow(r)
        for c, key in enumerate(
            ["time", "command", "sent_x", "sent_y", "before", "after", "delta"]
        ):
            item = QTableWidgetItem(str(row[key]))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(r, c, item)
        self._table.scrollToBottom()

    def _clear_table(self) -> None:
        self._rows.clear()
        self._table.setRowCount(0)

    def _save_csv(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Empty", "No results to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save positioning diagnostic CSV",
            "positioning_diag.csv", "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=["time", "command", "sent_x", "sent_y",
                                "before", "after", "delta"],
                )
                w.writeheader()
                for row in self._rows:
                    w.writerow(row)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.log_message.emit(f"Saved diagnostic CSV: {path}")


def _fmt_xy(xy: Optional[Tuple[float, float]]) -> str:
    if xy is None:
        return "?"
    return f"({xy[0]:+.4f}, {xy[1]:+.4f})"
