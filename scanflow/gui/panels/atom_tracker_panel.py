"""Atom Tracker tab — 5-point Z-gradient lateral drift correction.

The user sets a reference point (or auto-detects one from the last scan),
then triggers drift measurements manually or automatically after each scan.
All intermediate Z readings are emitted to the Log panel for debugging.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QCheckBox, QGroupBox, QGridLayout, QFrame,
)

from scanflow.core.atom_tracker import _FivePointWorker, TrackResult, find_reference_nm

log = logging.getLogger(__name__)


class AtomTrackerPanel(QWidget):
    """GUI panel for 5-point atom tracking drift correction."""

    log_message   = Signal(str)
    error_message = Signal(str)

    def __init__(self, stm, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._ref_x: Optional[float] = None
        self._ref_y: Optional[float] = None
        self._worker: Optional[_FivePointWorker] = None

        # History for plot: list of (t_rel_min, dx_nm, dy_nm)
        self._correction_history: list[tuple[float, float, float]] = []
        self._t0: Optional[float] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Reference group
        ref_group = QGroupBox("Reference position")
        ref_layout = QGridLayout(ref_group)

        ref_layout.addWidget(QLabel("Ref X (nm):"), 0, 0)
        self._ref_x_spin = QDoubleSpinBox()
        self._ref_x_spin.setRange(-5000, 5000)
        self._ref_x_spin.setDecimals(3)
        self._ref_x_spin.setSuffix(" nm")
        self._ref_x_spin.setEnabled(False)
        ref_layout.addWidget(self._ref_x_spin, 0, 1)

        ref_layout.addWidget(QLabel("Ref Y (nm):"), 1, 0)
        self._ref_y_spin = QDoubleSpinBox()
        self._ref_y_spin.setRange(-5000, 5000)
        self._ref_y_spin.setDecimals(3)
        self._ref_y_spin.setSuffix(" nm")
        self._ref_y_spin.setEnabled(False)
        ref_layout.addWidget(self._ref_y_spin, 1, 1)

        self._use_current_btn = QPushButton("Use current tip position")
        self._use_current_btn.setToolTip(
            "Read SCAN.OFFSET from the STM and use it as the reference."
        )
        self._use_current_btn.clicked.connect(self._set_ref_from_stm)
        ref_layout.addWidget(self._use_current_btn, 0, 2)

        self._ref_status = QLabel("No reference set")
        self._ref_status.setStyleSheet("color: #e63946; font-style: italic;")
        ref_layout.addWidget(self._ref_status, 1, 2)

        root.addWidget(ref_group)

        # Parameters group
        param_group = QGroupBox("Probe parameters")
        param_layout = QGridLayout(param_group)

        param_layout.addWidget(QLabel("Probe delta (nm):"), 0, 0)
        self._delta_spin = QDoubleSpinBox()
        self._delta_spin.setRange(0.1, 10.0)
        self._delta_spin.setDecimals(2)
        self._delta_spin.setValue(0.5)
        self._delta_spin.setSuffix(" nm")
        self._delta_spin.setToolTip(
            "Distance from reference to each probe point. "
            "Should be smaller than the feature but larger than noise (~0.3-1 nm)."
        )
        param_layout.addWidget(self._delta_spin, 0, 1)

        param_layout.addWidget(QLabel("Settle time (s):"), 0, 2)
        self._settle_spin = QDoubleSpinBox()
        self._settle_spin.setRange(0.05, 5.0)
        self._settle_spin.setDecimals(2)
        self._settle_spin.setValue(0.15)
        self._settle_spin.setSuffix(" s")
        param_layout.addWidget(self._settle_spin, 0, 3)

        param_layout.addWidget(QLabel("Gain:"), 1, 0)
        self._gain_spin = QDoubleSpinBox()
        self._gain_spin.setRange(0.0, 2.0)
        self._gain_spin.setDecimals(2)
        self._gain_spin.setValue(0.5)
        self._gain_spin.setToolTip(
            "Proportional gain: 0 = no correction, 1 = full delta step. "
            "Start at 0.5 and tune based on log output."
        )
        param_layout.addWidget(self._gain_spin, 1, 1)

        param_layout.addWidget(QLabel("Min feature strength (DAC):"), 1, 2)
        self._strength_spin = QDoubleSpinBox()
        self._strength_spin.setRange(0.0, 10000.0)
        self._strength_spin.setDecimals(0)
        self._strength_spin.setValue(50.0)
        self._strength_spin.setToolTip(
            "Minimum Z0 - mean(off-centre) in DAC counts to accept as a real feature. "
            "Increase if noise triggers false corrections."
        )
        param_layout.addWidget(self._strength_spin, 1, 3)

        root.addWidget(param_group)

        # Controls row
        ctrl = QHBoxLayout()
        self._measure_btn = QPushButton("Measure drift now")
        self._measure_btn.setEnabled(False)
        self._measure_btn.clicked.connect(self._run_measurement)
        ctrl.addWidget(self._measure_btn)

        self._dryrun_chk = QCheckBox("Dry run (measure only, don't apply)")
        ctrl.addWidget(self._dryrun_chk)

        self._auto_chk = QCheckBox("Auto-correct after each scan")
        self._auto_chk.setToolTip(
            "Automatically run a 5-point correction after every scan completes."
        )
        ctrl.addWidget(self._auto_chk)

        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # Last result
        result_group = QGroupBox("Last measurement")
        result_grid = QGridLayout(result_group)
        headers = ["Metric", "Value"]
        for c, h in enumerate(headers):
            result_grid.addWidget(QLabel(f"<b>{h}</b>"), 0, c)
        rows = [
            ("feature_strength", "Feature strength (DAC)"),
            ("gradient_x",       "Gradient X (DAC)"),
            ("gradient_y",       "Gradient Y (DAC)"),
            ("dx_correction",    "Δx correction (nm)"),
            ("dy_correction",    "Δy correction (nm)"),
            ("status",           "Status"),
        ]
        self._result_labels: dict[str, QLabel] = {}
        for r, (key, label) in enumerate(rows, start=1):
            result_grid.addWidget(QLabel(label), r, 0)
            val_lbl = QLabel("—")
            val_lbl.setAlignment(Qt.AlignCenter)
            result_grid.addWidget(val_lbl, r, 1)
            self._result_labels[key] = val_lbl
        root.addWidget(result_group)

        # Correction history plot
        plot_group = QGroupBox("Correction history")
        plot_layout = QVBoxLayout(plot_group)
        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.setLabel("left", "Correction (nm)")
        self._plot.setLabel("bottom", "Time (min)")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.addLegend(offset=(10, 10))
        self._curve_x = self._plot.plot(
            [], [], pen=pg.mkPen("#0077b6", width=2), name="Δx"
        )
        self._curve_y = self._plot.plot(
            [], [], pen=pg.mkPen("#e63946", width=2), name="Δy"
        )
        self._plot.addLine(y=0, pen=pg.mkPen("#aaaaaa", width=1, style=Qt.DashLine))
        plot_layout.addWidget(self._plot)
        root.addWidget(plot_group)

    # ------------------------------------------------------------------
    # Reference management
    # ------------------------------------------------------------------

    def _set_ref_from_stm(self) -> None:
        try:
            pos = self._stm.scan.get_offset_nm()
        except Exception as e:
            self.error_message.emit(f"AtomTracker: could not read tip position: {e}")
            return
        if pos is None:
            self.error_message.emit("AtomTracker: tip position returned None")
            return
        self._ref_x, self._ref_y = float(pos[0]), float(pos[1])
        self._ref_x_spin.setValue(self._ref_x)
        self._ref_y_spin.setValue(self._ref_y)
        self._ref_status.setText(
            f"({self._ref_x:.3f}, {self._ref_y:.3f}) nm"
        )
        self._ref_status.setStyleSheet("color: #2dc653; font-style: normal;")
        self._measure_btn.setEnabled(True)
        self.log_message.emit(
            f"AtomTracker: reference set to ({self._ref_x:.3f}, {self._ref_y:.3f}) nm"
        )

    def set_reference_nm(self, x_nm: float, y_nm: float) -> None:
        """Set reference programmatically (called from auto-detect or external code)."""
        self._ref_x, self._ref_y = float(x_nm), float(y_nm)
        self._ref_x_spin.setValue(self._ref_x)
        self._ref_y_spin.setValue(self._ref_y)
        self._ref_status.setText(
            f"({self._ref_x:.3f}, {self._ref_y:.3f}) nm  [auto-detected]"
        )
        self._ref_status.setStyleSheet("color: #2dc653; font-style: normal;")
        self._measure_btn.setEnabled(True)
        self.log_message.emit(
            f"AtomTracker: reference auto-detected at "
            f"({self._ref_x:.3f}, {self._ref_y:.3f}) nm"
        )

    def try_auto_detect(
        self,
        z_array: np.ndarray,
        scan_x_nm: float,
        scan_y_nm: float,
        home_x_nm: float,
        home_y_nm: float,
    ) -> bool:
        """Try to auto-detect a reference feature from a completed scan's Z data.

        Returns True if a feature was found and the reference was updated.
        """
        pos = find_reference_nm(
            z_array, scan_x_nm, scan_y_nm, home_x_nm, home_y_nm,
        )
        if pos is None:
            self.log_message.emit(
                "AtomTracker: auto-detect found no prominent feature in scan "
                "(surface may be too flat, or prominence threshold too high)"
            )
            return False
        self.set_reference_nm(pos[0], pos[1])
        return True

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def _run_measurement(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.log_message.emit("AtomTracker: measurement already running")
            return
        if self._ref_x is None or self._ref_y is None:
            self.error_message.emit("AtomTracker: no reference set")
            return

        apply_corr = not self._dryrun_chk.isChecked()
        self._worker = _FivePointWorker(
            self._stm,
            self._ref_x, self._ref_y,
            delta_nm=self._delta_spin.value(),
            settle_s=self._settle_spin.value(),
            gain=self._gain_spin.value(),
            min_feature_strength=self._strength_spin.value(),
            max_correction_nm=self._delta_spin.value(),
            apply_correction=apply_corr,
        )
        self._worker.log_message.connect(self.log_message)
        self._worker.error_message.connect(self.error_message)
        self._worker.result_ready.connect(self._on_result)
        self._measure_btn.setEnabled(False)
        self._worker.start()

    def handle_scan_completed(self) -> None:
        """Called externally after each scan — runs correction if auto is on."""
        if self._auto_chk.isChecked():
            self._run_measurement()

    def _on_result(self, result: TrackResult) -> None:
        self._measure_btn.setEnabled(True)

        # Update last-result table
        self._result_labels["feature_strength"].setText(
            f"{result.feature_strength:.1f}"
        )
        self._result_labels["gradient_x"].setText(f"{result.gradient_x:+.1f}")
        self._result_labels["gradient_y"].setText(f"{result.gradient_y:+.1f}")
        self._result_labels["dx_correction"].setText(
            f"{result.dx_correction_nm:+.3f}"
        )
        self._result_labels["dy_correction"].setText(
            f"{result.dy_correction_nm:+.3f}"
        )
        if result.applied:
            status = "Applied ✓"
            self._result_labels["status"].setStyleSheet("color: #2dc653;")
            # Update reference for next measurement
            self._ref_x = result.ref_x_nm + result.dx_correction_nm
            self._ref_y = result.ref_y_nm + result.dy_correction_nm
            self._ref_x_spin.setValue(self._ref_x)
            self._ref_y_spin.setValue(self._ref_y)
        elif result.skip_reason:
            status = f"Skipped: {result.skip_reason}"
            self._result_labels["status"].setStyleSheet("color: #f77f00;")
        else:
            status = "Dry run"
            self._result_labels["status"].setStyleSheet("color: #0077b6;")
        self._result_labels["status"].setText(status)

        # Append to history and refresh plot
        import time as _time
        t_now = _time.time()
        if self._t0 is None:
            self._t0 = t_now
        t_min = (t_now - self._t0) / 60.0
        self._correction_history.append(
            (t_min, result.dx_correction_nm, result.dy_correction_nm)
        )
        self._refresh_history_plot()

    def _refresh_history_plot(self) -> None:
        if not self._correction_history:
            return
        ts  = np.array([h[0] for h in self._correction_history])
        dxs = np.array([h[1] for h in self._correction_history])
        dys = np.array([h[2] for h in self._correction_history])
        self._curve_x.setData(ts, dxs)
        self._curve_y.setData(ts, dys)
