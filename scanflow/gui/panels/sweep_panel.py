"""ScanFlow sweep panel — the entire automation UI on one screen.

Two sweep modes:
  • Bias ramp at constant tunneling current
  • Current ramp at constant bias

Both with optional drift correction and tip-crash safety abort. Live monitoring
of the actual scan is done in the manufacturer's STMAFM software — this panel
just orchestrates parameter changes between scans.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton, QComboBox,
    QCheckBox, QProgressBar, QLineEdit, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Signal, Qt

from scanflow.core import STMClient
from scanflow.automation import MeasurementRecipe, AutomationRunner, RunnerState
from scanflow.automation.recipe import format_duration


class SweepPanel(QWidget):
    """Configure and run a bias or current sweep."""

    log_message = Signal(str)
    error_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        self._recipe: MeasurementRecipe | None = None
        self._build_ui()
        self._refresh_estimate()

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
        box = QGroupBox("Safety and drift")
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

        self._drift_chk = QCheckBox("Drift correction")
        self._drift_chk.setChecked(True)
        self._drift_chk.toggled.connect(self._refresh_estimate)
        g.addWidget(self._drift_chk, 1, 0, 1, 2)

        self._safety_label = QLabel("Live |I|: —")
        g.addWidget(self._safety_label, 1, 2, 1, 2)
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
        self._force_quit_btn = QPushButton("Force Quit")
        self._force_quit_btn.setProperty("accent", "true")
        self._force_quit_btn.setEnabled(False)
        self._force_quit_btn.setToolTip(
            "Hard-terminate the automation thread. Use only if Stop hangs — "
            "the STM may be left in an unclean state."
        )
        self._force_quit_btn.clicked.connect(self._force_quit_run)
        g.addWidget(self._force_quit_btn, 0, 3)
        save_btn = QPushButton("Save recipe…")
        save_btn.clicked.connect(self._save_recipe)
        g.addWidget(save_btn, 0, 4)
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
        drift = self._drift_chk.isChecked()
        n = self._ramp_step_count()

        if self._kind.currentIndex() == 0:
            recipe = MeasurementRecipe.bias_ramp(
                start_V=self._start.value(),
                end_V=self._end.value(),
                steps=n,
                setpoint_A=self._fixed_value.value() * 1e-12,
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                drift_correction=drift,
            )
        else:
            recipe = MeasurementRecipe.current_ramp(
                start_pA=self._start.value(),
                end_pA=self._end.value(),
                steps=n,
                bias_V=self._fixed_value.value(),
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                drift_correction=drift,
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

        # Confirmation dialog with the estimate
        confirm = QMessageBox.question(
            self, "Confirm sweep",
            f"<b>{recipe.name}</b><br><br>"
            f"Number of scans: {recipe.total_steps()}<br>"
            f"Estimated total time: <b>{format_duration(recipe.estimate_duration_s())}</b><br>"
            f"Drift correction: {'on' if recipe.drift_correction else 'off'}<br>"
            f"Safety threshold: {recipe.safety_max_current_A*1e9:.3f} nA<br><br>"
            f"Start now?"
        )
        if confirm != QMessageBox.Yes:
            return

        self._runner = AutomationRunner(self._stm, recipe)
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.scan_completed.connect(
            lambda p: self.log_message.emit(f"saved: {p}")
        )
        self._runner.error.connect(self._on_error)
        self._runner.safety_violation.connect(self._on_safety_violation)
        self._runner.safety_reading.connect(self._on_safety_reading)
        self._runner.drift_measured.connect(self._on_drift)
        self._runner.start()

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
            self._stop_btn.setText("Emergency Stop")
            self._status.setText("Stopping…")
        else:
            self._status.setText("Emergency stop — retracting tip…")

    def _force_quit_run(self) -> None:
        if not self._runner:
            return
        ans = QMessageBox.question(
            self, "Force-terminate runner?",
            "Hard-terminate the automation thread? The STM may be left in "
            "an unclean state — only use this if Stop is not responding.",
        )
        if ans == QMessageBox.Yes:
            self._status.setText("Force-terminating…")
            self._runner.force_stop()

    # ── runner callbacks ────────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self._progress.setValue(current)
        self._status.setText(f"{current}/{total}: {label}")

    def _on_state(self, state) -> None:
        self._status.setText(state.name.capitalize())
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._start_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._force_quit_btn.setEnabled(False)
            self._pause_btn.setText("Pause")
            self._stop_btn.setText("Stop")

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

    def _on_drift(self, result) -> None:
        try:
            self.log_message.emit(
                f"drift: dx={result.dx_angstrom:+.3f} Å  dy={result.dy_angstrom:+.3f} Å  "
                f"|d|={result.magnitude_angstrom:.3f} Å"
            )
        except AttributeError:
            pass
