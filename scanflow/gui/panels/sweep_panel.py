"""ScanFlow sweep panel — the entire automation UI on one screen.

Two sweep modes:
  • Bias ramp at constant tunneling current
  • Current ramp at constant bias

Both with tip-crash safety abort. Live monitoring of the actual scan is done
in the manufacturer's STMAFM software — this panel just orchestrates
parameter changes between scans.
"""

from __future__ import annotations

import logging
import time
import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton, QComboBox,
    QCheckBox, QProgressBar, QLineEdit, QFileDialog, QMessageBox,
    QDialog,
)
from PySide6.QtCore import Signal, Qt, QTimer

from scanflow.core import STMClient
from scanflow.automation import MeasurementRecipe, AutomationRunner, RunnerState
from scanflow.automation.recipe import format_duration
from scanflow.automation.scan_metrics import (
    DriftTracker, DriftFit,
    FEATURE_SCAN_THRESHOLD_NM_MIN, ATOMIC_SCAN_THRESHOLD_NM_MIN,
)
from scanflow.gui.widgets.sweep_confirm import SweepConfirmDialog

log = logging.getLogger(__name__)


class DriftPlotWidget(QWidget):
    """Live pyqtgraph chart of inter-scan drift speed with exponential fit overlay.

    Call ``update(records, fit)`` after each new scan to refresh the plot.
    Call ``clear()`` to reset for a new sweep.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotWidget(background="w")
        self._plot.setLabel("left", "Drift speed", units="nm/min")
        self._plot.setLabel("bottom", "Elapsed time", units="min")
        self._plot.setTitle("Inter-scan drift", color="#111", size="10pt")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.addLegend(offset=(10, 10))

        self._scatter = self._plot.plot(
            [], [],
            pen=None,
            symbol="o",
            symbolSize=7,
            symbolBrush="#2980b9",
            symbolPen=pg.mkPen("#1a5276", width=1),
            name="measured",
        )
        self._fit_curve = self._plot.plot(
            [], [],
            pen=pg.mkPen("#e74c3c", width=2, style=Qt.PenStyle.SolidLine),
            name="fit v₀·e^(−t/τ)",
        )
        # Fixed horizontal lines at the two absolute thresholds
        self._feat_line = pg.InfiniteLine(
            angle=0, pos=FEATURE_SCAN_THRESHOLD_NM_MIN,
            pen=pg.mkPen("#27ae60", width=1, style=Qt.PenStyle.DashLine),
            label=f"feature ({FEATURE_SCAN_THRESHOLD_NM_MIN} nm/min)",
            labelOpts={"color": "#27ae60", "position": 0.05},
        )
        self._atom_line = pg.InfiniteLine(
            angle=0, pos=ATOMIC_SCAN_THRESHOLD_NM_MIN,
            pen=pg.mkPen("#e67e22", width=1, style=Qt.PenStyle.DashLine),
            label=f"atomic ({ATOMIC_SCAN_THRESHOLD_NM_MIN} nm/min)",
            labelOpts={"color": "#e67e22", "position": 0.05},
        )
        # Vertical prediction lines (shown when fit is available)
        self._feat_pred_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen("#27ae60", width=1, style=Qt.PenStyle.DotLine),
            label="feature ready",
            labelOpts={"color": "#27ae60", "position": 0.85},
        )
        self._atom_pred_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen("#e67e22", width=1, style=Qt.PenStyle.DotLine),
            label="atomic ready",
            labelOpts={"color": "#e67e22", "position": 0.75},
        )
        self._feat_pred_line.setVisible(False)
        self._atom_pred_line.setVisible(False)
        self._plot.addItem(self._feat_line)
        self._plot.addItem(self._atom_line)
        self._plot.addItem(self._feat_pred_line)
        self._plot.addItem(self._atom_pred_line)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(
            "font-family: monospace; font-size: 9pt; color: #222;"
        )

        layout.addWidget(self._plot, 1)
        layout.addWidget(self._summary_label)

    def clear(self) -> None:
        self._scatter.setData([], [])
        self._fit_curve.setData([], [])
        self._feat_pred_line.setVisible(False)
        self._atom_pred_line.setVisible(False)
        self._summary_label.setText("")

    def update(self, records, fit: Optional[DriftFit]) -> None:
        if not records:
            self.clear()
            return

        t_min = np.array([r.elapsed_s / 60.0 for r in records])
        v = np.array([r.speed_nm_min for r in records])
        self._scatter.setData(t_min, v)

        if fit is not None and fit.tau_s < float("inf") and fit.r_squared > 0.5:
            t_end = max(
                max(t_min) * 1.5,
                (fit.atomic_scan_at_s or fit.feature_scan_at_s or 0) / 60.0 * 1.1,
            )
            t_fit = np.linspace(0.0, t_end, 200)
            v_fit = fit.v0_nm_min * np.exp(-t_fit * 60.0 / fit.tau_s)
            self._fit_curve.setData(t_fit, v_fit)

            if fit.feature_scan_at_s is not None and not fit.feature_scan_ready:
                self._feat_pred_line.setValue(fit.feature_scan_at_s / 60.0)
                self._feat_pred_line.setVisible(True)
            else:
                self._feat_pred_line.setVisible(False)

            if fit.atomic_scan_at_s is not None and not fit.atomic_scan_ready:
                self._atom_pred_line.setValue(fit.atomic_scan_at_s / 60.0)
                self._atom_pred_line.setVisible(True)
            else:
                self._atom_pred_line.setVisible(False)
        else:
            self._fit_curve.setData([], [])
            self._feat_pred_line.setVisible(False)
            self._atom_pred_line.setVisible(False)

        self._update_summary(records, fit)

    def _update_summary(self, records, fit: Optional[DriftFit]) -> None:
        v_last = records[-1].speed_nm_min
        elapsed_s = records[-1].elapsed_s
        parts = [f"Speed: {v_last:.3f} nm/min  (scan #{records[-1].scan_index})"]
        if fit is not None and fit.tau_s < float("inf"):
            tau_min = fit.tau_s / 60.0
            parts.append(f"τ = {tau_min:.1f} min  R² = {fit.r_squared:.3f}")
        if fit is not None:
            if fit.feature_scan_ready:
                parts.append("Feature: READY")
            elif fit.feature_scan_at_s is not None:
                rem = max(0.0, (fit.feature_scan_at_s - elapsed_s) / 60.0)
                parts.append(f"Feature: {rem:.1f} min")
            if fit.atomic_scan_ready:
                parts.append("Atomic: READY")
            elif fit.atomic_scan_at_s is not None:
                rem = max(0.0, (fit.atomic_scan_at_s - elapsed_s) / 60.0)
                parts.append(f"Atomic: {rem:.1f} min")
        self._summary_label.setText("  |  ".join(parts))


class SweepPanel(QWidget):
    """Configure and run a bias or current sweep."""

    log_message = Signal(str)
    error_message = Signal(str)
    scan_completed = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        self._recipe: MeasurementRecipe | None = None
        self._resume_from_step: int = 0
        self._run_start_t: float | None = None
        self._drift_tracker = DriftTracker()
        self._build_ui()
        self._refresh_estimate()
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(60_000)
        self._eta_timer.timeout.connect(self._refresh_eta_from_runner)

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_scan_group())
        root.addWidget(self._build_sweep_group())
        root.addWidget(self._build_safety_group())
        root.addWidget(self._build_output_group())
        root.addWidget(self._build_run_group())

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        self._status = QLabel("Ready")
        root.addWidget(self._status)

        self._time_label = QLabel("")
        self._time_label.setStyleSheet("color: #555; font-style: italic;")
        root.addWidget(self._time_label)

        self._drift_plot = DriftPlotWidget()
        self._drift_plot.setMinimumHeight(200)
        drift_box = QGroupBox("Drift tracking")
        drift_layout = QVBoxLayout(drift_box)
        drift_layout.setContentsMargins(4, 4, 4, 4)
        drift_layout.addWidget(self._drift_plot)
        root.addWidget(drift_box)

        root.addStretch(1)

    def _build_scan_group(self) -> QGroupBox:
        box = QGroupBox("Scan frame")
        g = QGridLayout(box)

        g.addWidget(QLabel("Size X (nm)"), 0, 0)
        self._size_x = QDoubleSpinBox()
        self._size_x.setRange(0.1, 50000.0)
        self._size_x.setDecimals(2)
        self._size_x.setValue(50.0)
        self._size_x.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._size_x, 0, 1)

        g.addWidget(QLabel("Size Y (nm)"), 0, 2)
        self._size_y = QDoubleSpinBox()
        self._size_y.setRange(0.1, 50000.0)
        self._size_y.setDecimals(2)
        self._size_y.setValue(50.0)
        self._size_y.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._size_y, 0, 3)

        g.addWidget(QLabel("Speed (nm/s)"), 1, 0)
        self._speed = QDoubleSpinBox()
        self._speed.setRange(0.01, 1000.0)
        self._speed.setDecimals(2)
        self._speed.setValue(50.0)
        self._speed.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._speed, 1, 1)

        g.addWidget(QLabel("Pixels"), 1, 2)
        self._pixels = QSpinBox()
        self._pixels.setRange(8, 8192)
        self._pixels.setValue(256)
        self._pixels.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._pixels, 1, 3)
        return box

    def _build_sweep_group(self) -> QGroupBox:
        box = QGroupBox("Sweep")
        g = QGridLayout(box)

        g.addWidget(QLabel("Type"), 0, 0)
        self._kind = QComboBox()
        self._kind.addItems(["Bias ramp (constant current)",
                             "Current ramp (constant bias)"])
        self._kind.currentIndexChanged.connect(self._on_kind_change)
        g.addWidget(self._kind, 0, 1, 1, 3)

        self._label_fixed = QLabel("Setpoint (pA)")
        g.addWidget(self._label_fixed, 1, 0)
        self._fixed_value = QDoubleSpinBox()
        self._fixed_value.setRange(0.001, 1e6)
        self._fixed_value.setDecimals(3)
        self._fixed_value.setValue(50.0)
        g.addWidget(self._fixed_value, 1, 1)

        g.addWidget(QLabel("Start"), 2, 0)
        self._start = QDoubleSpinBox()
        self._start.setRange(-10.0, 10.0)
        self._start.setDecimals(4)
        self._start.setValue(-1.0)
        self._start.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._start, 2, 1)

        g.addWidget(QLabel("End"), 2, 2)
        self._end = QDoubleSpinBox()
        self._end.setRange(-10.0, 10.0)
        self._end.setDecimals(4)
        self._end.setValue(1.0)
        self._end.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._end, 2, 3)

        g.addWidget(QLabel("Step"), 3, 0)
        self._step = QDoubleSpinBox()
        self._step.setRange(0.0001, 1000.0)
        self._step.setDecimals(4)
        self._step.setSingleStep(0.001)
        self._step.setValue(0.010)   # 10 mV default
        self._step.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._step, 3, 1)

        self._step_unit_label = QLabel("V (e.g. 0.010 = 10 mV)")
        g.addWidget(self._step_unit_label, 3, 2, 1, 2)

        self._count_label = QLabel("Number of scans: 0")
        self._count_label.setStyleSheet("font-weight: bold;")
        g.addWidget(self._count_label, 4, 0, 1, 2)

        self._estimate_label = QLabel("Estimated total time: —")
        self._estimate_label.setStyleSheet("font-weight: bold;")
        g.addWidget(self._estimate_label, 4, 2, 1, 2)

        # Apply initial kind config
        self._on_kind_change(0)
        return box

    def _build_safety_group(self) -> QGroupBox:
        box = QGroupBox("Safety")
        g = QGridLayout(box)

        self._safety_chk = QCheckBox("Tip-crash safety abort")
        self._safety_chk.setChecked(True)
        g.addWidget(self._safety_chk, 0, 0, 1, 2)

        g.addWidget(QLabel("|I| threshold (nA)"), 0, 2)
        self._safety_nA = QDoubleSpinBox()
        self._safety_nA.setRange(0.001, 1000.0)
        self._safety_nA.setDecimals(3)
        self._safety_nA.setValue(1.0)
        g.addWidget(self._safety_nA, 0, 3)

        self._safety_label = QLabel("Live |I|: —")
        g.addWidget(self._safety_label, 1, 2, 1, 2)

        g.addWidget(QLabel("Settle before scan (s)"), 2, 0)
        self._settle_s = QDoubleSpinBox()
        self._settle_s.setRange(0.0, 600.0)
        self._settle_s.setDecimals(1)
        self._settle_s.setSingleStep(1.0)
        self._settle_s.setValue(5.0)
        self._settle_s.setToolTip(
            "Pause this many seconds after applying scan parameters and "
            "before each scan starts — lets the piezo and feedback settle "
            "so the first scanline isn't biased by post-apply drift."
        )
        self._settle_s.valueChanged.connect(self._refresh_estimate)
        g.addWidget(self._settle_s, 2, 1)
        return box

    def _build_output_group(self) -> QGroupBox:
        box = QGroupBox("Output")
        g = QGridLayout(box)
        g.addWidget(QLabel("Save folder"), 0, 0)
        self._save_folder = QLineEdit()
        self._save_folder.setPlaceholderText("(leave empty for STMAFM default)")
        g.addWidget(self._save_folder, 0, 1, 1, 2)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_folder)
        g.addWidget(pick, 0, 3)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        g = QGridLayout(box)
        self._start_btn = QPushButton("Start")
        self._start_btn.clicked.connect(self._start_run)
        g.addWidget(self._start_btn, 0, 0)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._pause_run)
        g.addWidget(self._pause_btn, 0, 1)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setProperty("accent", "true")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_run)
        g.addWidget(self._stop_btn, 0, 2)
        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setEnabled(False)
        self._resume_btn.setToolTip(
            "Resume the sweep from the step that was interrupted.\n"
            "Verify the tip is safe before resuming."
        )
        self._resume_btn.clicked.connect(self._resume_run)
        g.addWidget(self._resume_btn, 0, 3)
        self._force_quit_btn = QPushButton("Force Quit")
        self._force_quit_btn.setProperty("accent", "true")
        self._force_quit_btn.setEnabled(False)
        self._force_quit_btn.setToolTip(
            "Hard-terminate the automation thread. Use only if Stop hangs — "
            "the STM may be left in an unclean state."
        )
        self._force_quit_btn.clicked.connect(self._force_quit_run)
        g.addWidget(self._force_quit_btn, 0, 4)
        save_btn = QPushButton("Save recipe…")
        save_btn.clicked.connect(self._save_recipe)
        g.addWidget(save_btn, 0, 5)
        return box

    # ------------------------------------------------------------------
    # Sweep-kind config
    # ------------------------------------------------------------------

    def _on_kind_change(self, idx: int) -> None:
        if idx == 0:  # Bias ramp
            self._label_fixed.setText("Setpoint (pA)")
            self._fixed_value.setRange(0.001, 1e6)
            self._fixed_value.setValue(self._fixed_value.value() or 50.0)
            self._start.setRange(-10.0, 10.0)
            self._end.setRange(-10.0, 10.0)
            self._start.setValue(-1.0)
            self._end.setValue(1.0)
            self._step.setValue(0.010)
            self._step_unit_label.setText("V (e.g. 0.010 = 10 mV)")
        else:        # Current ramp
            self._label_fixed.setText("Bias (V)")
            self._fixed_value.setRange(-10.0, 10.0)
            self._fixed_value.setValue(0.1)
            self._start.setRange(0.001, 1e6)
            self._end.setRange(0.001, 1e6)
            self._start.setValue(10.0)
            self._end.setValue(100.0)
            self._step.setValue(5.0)
            self._step_unit_label.setText("pA")
        self._refresh_estimate()

    # ------------------------------------------------------------------
    # Estimate
    # ------------------------------------------------------------------

    def _ramp_step_count(self) -> int:
        a, b, s = self._start.value(), self._end.value(), self._step.value()
        if s <= 0:
            return 0
        return int(abs(b - a) / s + 1e-9) + 1

    def _build_recipe(self) -> MeasurementRecipe:
        size = (self._size_x.value(), self._size_y.value())
        pixels = (self._pixels.value(), self._pixels.value())
        speed = self._speed.value()
        n = self._ramp_step_count()

        settle = self._settle_s.value()
        if self._kind.currentIndex() == 0:
            recipe = MeasurementRecipe.bias_ramp(
                start_V=self._start.value(),
                end_V=self._end.value(),
                steps=n,
                setpoint_A=self._fixed_value.value() * 1e-12,
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                settling_s=settle,
            )
        else:
            recipe = MeasurementRecipe.current_ramp(
                start_pA=self._start.value(),
                end_pA=self._end.value(),
                steps=n,
                bias_V=self._fixed_value.value(),
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                settling_s=settle,
            )

        recipe.save_folder = self._save_folder.text()
        recipe.safety_enable = self._safety_chk.isChecked()
        recipe.safety_max_current_A = self._safety_nA.value() * 1e-9
        return recipe

    def _refresh_estimate(self) -> None:
        try:
            recipe = self._build_recipe()
        except Exception:
            self._count_label.setText("Number of scans: 0")
            self._estimate_label.setText("Estimated total time: —")
            return
        self._count_label.setText(f"Number of scans: {recipe.total_steps()}")
        self._estimate_label.setText(
            f"Estimated total time: {format_duration(recipe.estimate_duration_s())}"
        )

    def load_from_stm(self) -> None:
        """Read current Createc scan parameters and populate the panel fields."""
        try:
            params = self._stm.scan.read()
        except Exception:
            return
        # Block signals while loading to avoid redundant estimate refreshes
        for w in (self._size_x, self._size_y, self._speed, self._pixels):
            w.blockSignals(True)
        self._size_x.setValue(params.size_nm[0])
        self._size_y.setValue(params.size_nm[1])
        self._speed.setValue(params.speed_nm_s)
        self._pixels.setValue(params.pixels[0])
        # Populate setpoint or bias fixed value depending on current sweep kind
        if self._kind.currentIndex() == 0:  # Bias ramp — fixed param is setpoint (pA)
            self._fixed_value.setValue(params.setpoint_A * 1e12)
        else:                               # Current ramp — fixed param is bias (V)
            self._fixed_value.setValue(params.bias_V)
        for w in (self._size_x, self._size_y, self._speed, self._pixels):
            w.blockSignals(False)
        self._refresh_estimate()
        self.log_message.emit(
            f"Loaded from Createc: "
            f"X={params.size_nm[0]:.1f} nm  Y={params.size_nm[1]:.1f} nm  "
            f"speed={params.speed_nm_s:.1f} nm/s  pixels={params.pixels[0]}"
        )

    # ------------------------------------------------------------------
    # Run controls
    # ------------------------------------------------------------------

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save folder")
        if path:
            self._save_folder.setText(path)

    def _save_recipe(self) -> None:
        recipe = self._build_recipe()
        path, _ = QFileDialog.getSaveFileName(self, "Save recipe", "recipe.yaml",
                                              "YAML (*.yaml)")
        if not path:
            return
        recipe.save(Path(path))
        self.log_message.emit(f"Recipe saved: {path}")

    def _start_run(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "STM Not Connected",
                                "Connect to the STM (or Mock) first.")
            return
        recipe = self._build_recipe()
        if recipe.total_steps() == 0:
            QMessageBox.warning(self, "Empty sweep",
                                "Check start, end, and step — no scans to run.")
            return
        self._recipe = recipe

        # Editable preview + voltage warnings; modifies recipe.steps in-place
        # if the user tweaks anything, then returns Accepted/Rejected.
        dlg = SweepConfirmDialog(recipe, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        self._drift_tracker.reset()
        self._drift_plot.clear()

        self._runner = AutomationRunner(self._stm, recipe)
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.scan_completed.connect(
            lambda p: self.log_message.emit(f"saved: {p}")
        )
        self._runner.scan_completed.connect(self._on_scan_completed)
        self._runner.error.connect(self._on_error)
        self._runner.safety_violation.connect(self._on_safety_violation)
        self._runner.safety_reading.connect(self._on_safety_reading)
        self._runner.z_stability.connect(self._on_z_stability)
        self._runner.settling.connect(self._on_settling)
        self._runner.info_message.connect(self.log_message)
        self._runner.start()

        self._run_start_t = time.time()
        self._time_label.setText("")
        self._eta_timer.start()
        self._progress.setMaximum(recipe.total_steps())
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._force_quit_btn.setEnabled(True)

    def _pause_run(self) -> None:
        if not self._runner:
            return
        if self._runner._state == RunnerState.PAUSED:
            self._runner.resume()
            self._pause_btn.setText("Pause")
        else:
            self._runner.pause()
            self._pause_btn.setText("Resume")

    def _stop_run(self) -> None:
        if not self._runner:
            return
        # First click: graceful. Second: emergency retract.
        stop_count_before = self._runner._stop_count
        self._runner.stop()
        if stop_count_before == 0:
            self.log_message.emit(
                "Stop requested — will finish current scan then halt"
            )
            self._stop_btn.setText("Emergency Stop")
            self._status.setText("Stopping…")
        else:
            self.log_message.emit(
                "EMERGENCY STOP requested — aborting scan, retracting tip"
            )
            self._status.setText("Emergency stop — retracting tip…")

    def _resume_run(self) -> None:
        if not self._recipe:
            return
        total = self._recipe.total_steps()
        restart_at = self._resume_from_step + 1   # 1-based step we will run first
        ans = QMessageBox.question(
            self,
            "Resume sweep?",
            f"Resume from step {restart_at} of {total}?\n\n"
            "Steps 1–{} are skipped (already completed or aborted mid-scan).\n\n"
            "Make sure the tip is safe before continuing.".format(
                restart_at - 1 if restart_at > 1 else 0
            ),
        )
        if ans != QMessageBox.Yes:
            return

        self._resume_btn.setEnabled(False)
        self._resume_btn.setText("Resume")
        self._drift_tracker.reset()
        self._drift_plot.clear()

        self._runner = AutomationRunner(
            self._stm,
            self._recipe,
            start_from_step=self._resume_from_step,
        )
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.scan_completed.connect(
            lambda p: self.log_message.emit(f"saved: {p}")
        )
        self._runner.scan_completed.connect(self._on_scan_completed)
        self._runner.error.connect(self._on_error)
        self._runner.safety_violation.connect(self._on_safety_violation)
        self._runner.safety_reading.connect(self._on_safety_reading)
        self._runner.z_stability.connect(self._on_z_stability)
        self._runner.settling.connect(self._on_settling)
        self._runner.info_message.connect(self.log_message)
        self._runner.start()

        self._run_start_t = time.time()
        self._time_label.setText("")
        self._eta_timer.start()
        self._progress.setMaximum(total)
        self._progress.setValue(self._resume_from_step)
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._force_quit_btn.setEnabled(True)
        self.log_message.emit(
            f"Sweep resumed from step {restart_at}/{total} "
            f"(skipped {self._resume_from_step} completed step(s))"
        )

    def _on_scan_completed(self, path: str) -> None:
        self.scan_completed.emit(path)
        self._feed_drift_tracker(path)

    def _feed_drift_tracker(self, path: str) -> None:
        """Load the completed scan, extract the topography plane, and update drift tracking."""
        try:
            from probeflow.core.scan_loader import load_scan
            scan = load_scan(Path(path))
            planes = getattr(scan, "planes", None)
            if not planes:
                return
            # Use the first plane (topography)
            plane = np.asarray(planes[0], dtype=float)
            if plane.ndim != 2 or plane.size < 16:
                return
            nm_per_pixel = self._nm_per_pixel()
            record = self._drift_tracker.add_scan(plane, nm_per_pixel)
            if record is not None:
                fit = self._drift_tracker.fit_model()
                self._drift_plot.update(self._drift_tracker.records, fit)
                self.log_message.emit(
                    f"Drift scan #{record.scan_index}: "
                    f"speed {record.speed_nm_min:.3f} nm/min  "
                    f"(ΔX={record.dx_nm:+.2f} nm, ΔY={record.dy_nm:+.2f} nm)"
                )
        except Exception as exc:
            log.debug("Drift tracker update failed for %s: %s", path, exc)

    def _nm_per_pixel(self) -> float:
        """Derive nm/pixel from current scan-frame UI fields."""
        size_x = self._size_x.value()
        pixels = max(self._pixels.value(), 1)
        return size_x / pixels

    def stop_for_close(self) -> None:
        """Gracefully stop any running automation when the main window closes.

        Sends a stop request (which triggers scan.stop() on the worker thread
        so Createc receives the halt command), then waits up to 8 seconds for
        the worker to exit cleanly. Falls back to hard-terminate only if the
        worker is truly stuck — this skips the COM finally block, which can
        leave STMAFM in an inconsistent state, but it's the last resort.
        """
        if not self._runner or not self._runner.isRunning():
            return
        log.info("Window closing — requesting graceful automation stop")
        self._runner.stop()
        if not self._runner.wait(8000):
            log.warning(
                "Automation thread did not exit after 8 s on window close — "
                "terminating. STMAFM may need a restart."
            )
            self._runner.terminate()
            self._runner.wait(1000)

    def _force_quit_run(self) -> None:
        if not self._runner:
            return
        ans = QMessageBox.question(
            self, "Force-terminate runner?",
            "Hard-terminate the automation thread? The STM may be left in "
            "an unclean state — only use this if Stop is not responding.",
        )
        if ans == QMessageBox.Yes:
            self.log_message.emit(
                "FORCE QUIT — hard-terminating runner thread"
            )
            self._status.setText("Force-terminating…")
            self._runner.force_stop()

    # ── runner callbacks ────────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self._progress.setValue(current)
        self._status.setText(f"{current}/{total}: {label}")
        self._update_eta(current, total)

    def _on_state(self, state) -> None:
        self._status.setText(state.name.capitalize())
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._eta_timer.stop()
            self._time_label.setText("")
            self._run_start_t = None
            self._start_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._force_quit_btn.setEnabled(False)
            self._pause_btn.setText("Pause")
            self._stop_btn.setText("Stop")
        if state == RunnerState.ERROR and self._runner is not None:
            aborted = self._runner._current_step_index
            if aborted is not None and aborted > 0 and self._recipe is not None:
                # Aborted scan was not saved — restart from that step
                self._resume_from_step = aborted - 1
                total = self._recipe.total_steps()
                self._resume_btn.setEnabled(True)
                self._resume_btn.setText(
                    f"Resume (step {aborted}/{total})"
                )
        if state == RunnerState.FINISHED:
            self._resume_btn.setEnabled(False)
            self._resume_btn.setText("Resume")
            self._emit_drift_summary()

    def _emit_drift_summary(self) -> None:
        summary = self._drift_tracker.summary()
        for line in summary.splitlines():
            self.log_message.emit(line)
        # Final update of plot with the complete fit
        fit = self._drift_tracker.fit_model()
        self._drift_plot.update(self._drift_tracker.records, fit)

    def _on_error(self, msg: str) -> None:
        self.error_message.emit(msg)

    def _on_safety_reading(self, current_A: float) -> None:
        nA = current_A * 1e9
        threshold_nA = self._safety_nA.value()
        if nA >= threshold_nA:
            colour = "#C75100"
        elif nA >= 0.5 * threshold_nA:
            colour = "#B58100"
        else:
            colour = "#1E7A1E"
        self._safety_label.setText(
            f"<span style='color:{colour}'>Live |I|: {nA:.3f} nA</span>"
        )

    def _on_safety_violation(self, message: str, current_A: float) -> None:
        nA = current_A * 1e9
        self._safety_label.setText(
            f"<b><span style='color:#C75100'>⚠ ABORT — |I|={nA:.3f} nA</span></b>"
        )
        QMessageBox.critical(
            self, "Tip-crash safety triggered",
            f"{message}\n\n"
            f"Scan stopped, Z-limit activated. Investigate before resuming."
        )

    def _on_settling(self, remaining_s: int, label: str) -> None:
        self._status.setText(f"{label} — {remaining_s} s")

    def _on_z_stability(self, metrics) -> None:
        from scanflow.automation.scan_metrics import format_z_stability
        self.log_message.emit(format_z_stability(metrics))

    def _refresh_eta_from_runner(self) -> None:
        if self._runner is None:
            return
        current = self._runner._current_step_index or 0
        total = self._recipe.total_steps() if self._recipe else 0
        self._update_eta(current, total)

    def _update_eta(self, completed: int, total: int) -> None:
        if self._run_start_t is None or total == 0:
            return
        elapsed = time.time() - self._run_start_t
        # progress(N) fires at step N *start*, so only N-1 steps have actually finished
        finished = max(completed - 1 - self._resume_from_step, 0)
        effective_total = total - self._resume_from_step
        if finished > 0:
            pace_s = elapsed / finished
            remaining_s = (effective_total - finished) * pace_s
        elif self._recipe is not None:
            # No step finished yet — use static estimate minus time already spent
            remaining_s = max(0.0, self._recipe.estimate_duration_s() - elapsed)
        else:
            return
        remaining_s = max(0.0, remaining_s)
        if remaining_s < 60:
            rem_str = "< 1 min"
        elif remaining_s < 3600:
            rem_str = f"~{int(remaining_s / 60)}m"
        else:
            h = int(remaining_s / 3600)
            m = int((remaining_s % 3600) / 60)
            rem_str = f"~{h}h {m:02d}m"
        eta = datetime.datetime.now() + datetime.timedelta(seconds=remaining_s)
        self._time_label.setText(f"Remaining: {rem_str}  (ETA {eta.strftime('%H:%M')})")
