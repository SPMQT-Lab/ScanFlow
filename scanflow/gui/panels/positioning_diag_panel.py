"""Positioning diagnostic — characterise CreaTec's XY offset commands.

ScanFlow currently has three plausible APIs for moving the scan offset
and each one has misbehaved in different ways on the real rig. This
tab lets you fire a single offset command and record what happened in
STMAFM, so we can derive what the commands actually do.

Two reading modes:
  * **Auto** — tries ``tip_xy_position()`` via getxypos(). Works only
    if the ``pstmafm.stmafmuser`` COM is accessible.
  * **Manual** — you read STMAFM's offset display and type the values
    in. Always works.

Safety:
  * Every Send is a single one-shot operation — no loops.
  * Default values are 0 (no-op) so the first Send is read-only.
  * Spinbox ranges are clamped to ±100.
  * No scanning is triggered.

Workflow:
  1. Click "Read auto (if available)". If it returns a number, great.
     If it says "unavailable", just type the offset STMAFM is currently
     showing into the Before X / Y boxes.
  2. Pick a Command, set small X / Y values, click "Send".
  3. Look at STMAFM. Type the NEW offset it displays into After X / Y.
  4. Click "Log row" to record this test.
  5. Repeat for the three commands × a couple of magnitudes.
  6. "Save CSV" → send to the developer.
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
            "<b>⚠ This panel moves the tip.</b> Each <i>Send</i> fires one "
            "offset command — the tip will physically translate. Use small "
            "values, make sure the surface is clear, and watch STMAFM."
            "<br><br>"
            "<b>Reading positions:</b> the 'Read auto' buttons try "
            "<code>getxypos()</code> via the secondary COM dispatch. On rigs "
            "where that's not exposed, just <b>type the offset STMAFM is "
            "currently showing</b> into the X/Y boxes by hand."
        )
        warn.setTextFormat(Qt.RichText)
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background-color: #fff8e1; color: #6d4c00; "
            "padding: 8px; border: 1px solid #ffd54f; border-radius: 4px;"
        )
        root.addWidget(warn)

        # 1) BEFORE position
        before_box = QGroupBox("1.  Before position  (what STMAFM shows now)")
        bg = QGridLayout(before_box)
        bg.addWidget(QLabel("X:"), 0, 0)
        self._before_x = QDoubleSpinBox()
        self._before_x.setRange(-1e6, 1e6)
        self._before_x.setDecimals(4)
        bg.addWidget(self._before_x, 0, 1)
        bg.addWidget(QLabel("Y:"), 0, 2)
        self._before_y = QDoubleSpinBox()
        self._before_y.setRange(-1e6, 1e6)
        self._before_y.setDecimals(4)
        bg.addWidget(self._before_y, 0, 3)
        read_before_btn = QPushButton("Read auto (if available)")
        read_before_btn.clicked.connect(lambda: self._auto_read(self._before_x, self._before_y))
        bg.addWidget(read_before_btn, 0, 4)

        probe_btn = QPushButton("Probe XY-read keys…")
        probe_btn.setToolTip(
            "Try a list of candidate getp keys and getdacval channels; "
            "log each one's response. Whichever one returns a sensible "
            "number (matching STMAFM's offset display) is our reader."
        )
        probe_btn.clicked.connect(self._probe_xy_keys)
        bg.addWidget(probe_btn, 1, 0, 1, 5)
        root.addWidget(before_box)

        # 2) Command to send
        cmdbox = QGroupBox("2.  Send one offset command")
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

        # 3) AFTER position
        after_box = QGroupBox("3.  After position  (what STMAFM shows after Send)")
        ag = QGridLayout(after_box)
        ag.addWidget(QLabel("X:"), 0, 0)
        self._after_x = QDoubleSpinBox()
        self._after_x.setRange(-1e6, 1e6)
        self._after_x.setDecimals(4)
        ag.addWidget(self._after_x, 0, 1)
        ag.addWidget(QLabel("Y:"), 0, 2)
        self._after_y = QDoubleSpinBox()
        self._after_y.setRange(-1e6, 1e6)
        self._after_y.setDecimals(4)
        ag.addWidget(self._after_y, 0, 3)
        read_after_btn = QPushButton("Read auto (if available)")
        read_after_btn.clicked.connect(lambda: self._auto_read(self._after_x, self._after_y))
        ag.addWidget(read_after_btn, 0, 4)

        log_row_btn = QPushButton("Log row to table")
        log_row_btn.setStyleSheet("font-weight: bold;")
        log_row_btn.clicked.connect(self._log_current_row)
        ag.addWidget(log_row_btn, 1, 0, 1, 5)
        root.addWidget(after_box)

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
    # Probe for the right XY-position-read mechanism
    # ------------------------------------------------------------------

    # Candidate getp keys to try when looking for the current XY offset.
    # Createc's naming isn't documented from outside; this enumerates the
    # most plausible variations. The first one that returns a number on
    # the rig is the one we'd hard-wire into tip_xy_position().
    _CANDIDATE_GETP_KEYS = [
        ("SCAN.OFFSET.X.NM",     "SCAN.OFFSET.Y.NM"),
        ("SCAN.OFFSET.X.VOLT",   "SCAN.OFFSET.Y.VOLT"),
        ("SCAN.XOFFSET.NM",      "SCAN.YOFFSET.NM"),
        ("SCAN.XOFFSET.VOLT",    "SCAN.YOFFSET.VOLT"),
        ("STMAFM.OFFSET.X.NM",   "STMAFM.OFFSET.Y.NM"),
        ("STMAFM.OFFSET.X.VOLT", "STMAFM.OFFSET.Y.VOLT"),
        ("STMAFM.XYOFF.X.NM",    "STMAFM.XYOFF.Y.NM"),
        ("STMAFM.XYOFF.X.VOLT",  "STMAFM.XYOFF.Y.VOLT"),
        ("SCAN.XYOFF.X.NM",      "SCAN.XYOFF.Y.NM"),
        ("SCAN.XYOFF.X.VOLT",    "SCAN.XYOFF.Y.VOLT"),
        ("SCAN.X.NM",            "SCAN.Y.NM"),
        ("SCAN.X.VOLT",          "SCAN.Y.VOLT"),
    ]

    def _probe_xy_keys(self) -> None:
        """Try every candidate read mechanism, log the results."""
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "Not connected",
                                "Connect to the STM (or Mock) first.")
            return

        self.log_message.emit("=== Probing XY-read mechanisms ===")
        self.log_message.emit(
            "Look at STMAFM's offset display now and note the X, Y values. "
            "Below, find whichever line returns a number matching that."
        )

        # 1) Secondary COM (already known to be unavailable on this rig,
        # but log the attempt anyway so the diagnostic is complete).
        try:
            xy = self._stm.tip_xy_position()
            self.log_message.emit(
                f"  tip_xy_position()                       → {xy}"
            )
        except Exception as e:
            self.log_message.emit(
                f"  tip_xy_position()                       → ERROR: {e}"
            )

        # 2) getp candidate keys.
        for kx, ky in self._CANDIDATE_GETP_KEYS:
            x_str = self._safe_getp(kx)
            y_str = self._safe_getp(ky)
            self.log_message.emit(
                f"  getp({kx!r}, {ky!r}) → X={x_str}, Y={y_str}"
            )

        # 3) Raw DAC channels — common conventions put X / Y on board 0
        # channels 0..3. Z is on the feedback DAC (getdacvalfb).
        try:
            raw = self._stm.raw
            for board in (0, 1):
                for ch in range(4):
                    try:
                        valf = raw.getdacvalf(board, ch)
                        self.log_message.emit(
                            f"  getdacvalf(board={board}, ch={ch})      → {valf}"
                        )
                    except Exception as e:
                        self.log_message.emit(
                            f"  getdacvalf(board={board}, ch={ch})      → ERROR: {e}"
                        )
        except Exception as e:
            self.log_message.emit(f"  raw DAC access failed: {e}")

        self.log_message.emit("=== End probe — copy this list and report ===")

    def _safe_getp(self, key: str) -> str:
        """Wrap getp in a try, return a printable result or 'ERROR: …'."""
        try:
            v = self._stm.getp(key, None)
            if v is None or v == "":
                return "(empty)"
            return repr(v)
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # Auto-read (best-effort; falls back to manual when unavailable)
    # ------------------------------------------------------------------

    def _auto_read(self, x_spin: QDoubleSpinBox, y_spin: QDoubleSpinBox) -> None:
        """Try tip_xy_position(); on success fill the spinboxes, on failure
        log a hint and leave the user to type the values from STMAFM."""
        if not self._stm.connected and not self._stm.is_mock:
            self.log_message.emit("auto-read: not connected — type values manually")
            return
        try:
            xy = self._stm.tip_xy_position()
        except Exception as e:
            log.exception("tip_xy_position raised")
            self.log_message.emit(f"auto-read: error ({e}) — type values manually")
            return
        if xy is None:
            self.log_message.emit(
                "auto-read: getxypos() unavailable on this rig — "
                "type the offset STMAFM is showing into the X / Y boxes."
            )
            return
        x, y = xy
        x_spin.setValue(float(x))
        y_spin.setValue(float(y))
        self.log_message.emit(f"auto-read: X={x:+.6f}  Y={y:+.6f}")

    # ------------------------------------------------------------------
    # Send a one-shot offset command
    # ------------------------------------------------------------------

    def _send_command(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "Not connected",
                                "Connect to the STM (or Mock) first.")
            return
        cmd = self._cmd_combo.currentText()
        x = self._x_spin.value()
        y = self._y_spin.value()
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
        self.log_message.emit(
            f"sent: {sent_repr} — now read STMAFM's offset display and "
            f"type it into the After X / Y boxes."
        )

    # ------------------------------------------------------------------
    # Log the current Before / Command / After triple as a table row
    # ------------------------------------------------------------------

    def _log_current_row(self) -> None:
        bx, by = self._before_x.value(), self._before_y.value()
        ax, ay = self._after_x.value(), self._after_y.value()
        cmd = self._cmd_combo.currentText()
        sx, sy = self._x_spin.value(), self._y_spin.value()
        before = (bx, by)
        after = (ax, ay)
        dx, dy = ax - bx, ay - by
        self._append_row({
            "time": datetime.now().strftime("%H:%M:%S"),
            "command": cmd,
            "sent_x": sx,
            "sent_y": sy,
            "before": _fmt_xy(before),
            "after": _fmt_xy(after),
            "delta": f"({dx:+.4f}, {dy:+.4f})",
        })
        # Stage the next test: copy 'after' into 'before' so the table chains
        # naturally if you keep sending commands.
        self._before_x.setValue(ax)
        self._before_y.setValue(ay)
        self._after_x.setValue(0.0)
        self._after_y.setValue(0.0)

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
