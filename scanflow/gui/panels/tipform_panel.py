"""Tip Forming panel — supervised single-pulse tip conditioning.

Wires the GUI to the runner's arming gate (REVIEW H5): the operator sets
the pulse parameters, sees the motion pre-flight (the Createc
travel-speed quirk), passes the typed ARM confirmation, and exactly one
pulse executes through the normal AutomationRunner path — with its
existing protections (pre/post quality snapshots, acquisition-log
events, one-approval-per-run scoping).

COM discipline (ROADMAP §2.5): this panel has NO timers/pollers. The
instrument is touched only on explicit button clicks (Read from STM,
Match scan speed, Arm && Run).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QGridLayout, QGroupBox, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from scanflow.core import STMClient, assess_tip_form_motion
from scanflow.automation import MeasurementRecipe, AutomationRunner, RunnerState
from scanflow.automation.recipe import TipFormStep
from scanflow.gui.widgets.tipform_arm import TipFormArmDialog


class TipFormPanel(QWidget):
    """Configure and run one supervised tip-forming pulse."""

    log_message = Signal(str)
    error_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        # Scan context for the motion assessment — refreshed by the
        # explicit "Read from STM" button, never by a timer.
        self._scan_speed_nm_s = 50.0
        self._frame_size_nm = (50.0, 50.0)
        self._context_is_live = False
        self._build_ui()
        self._refresh_assessment()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        box = QGroupBox("Pulse parameters")
        g = QGridLayout(box)

        g.addWidget(QLabel("Position X (px)"), 0, 0)
        self._x_px = QSpinBox()
        self._x_px.setRange(0, 8192)
        self._x_px.setValue(128)
        g.addWidget(self._x_px, 0, 1)

        g.addWidget(QLabel("Position Y (px)"), 0, 2)
        self._y_px = QSpinBox()
        self._y_px.setRange(0, 8192)
        self._y_px.setValue(128)
        g.addWidget(self._y_px, 0, 3)

        g.addWidget(QLabel("Voltage (V)"), 1, 0)
        self._voltage = QDoubleSpinBox()
        self._voltage.setRange(-10.0, 10.0)
        self._voltage.setDecimals(3)
        self._voltage.setValue(2.0)
        g.addWidget(self._voltage, 1, 1)

        g.addWidget(QLabel("Pulse length (s)"), 1, 2)
        self._pulse_s = QDoubleSpinBox()
        self._pulse_s.setRange(0.001, 10.0)
        self._pulse_s.setDecimals(3)
        self._pulse_s.setValue(0.4)
        g.addWidget(self._pulse_s, 1, 3)

        g.addWidget(QLabel("Z approach (nm)"), 2, 0)
        self._z_approach = QDoubleSpinBox()
        self._z_approach.setRange(0.0, 5.0)
        self._z_approach.setDecimals(2)
        self._z_approach.setValue(1.2)
        g.addWidget(self._z_approach, 2, 1)

        g.addWidget(QLabel("Z offset (nm)"), 2, 2)
        self._z_offset = QDoubleSpinBox()
        self._z_offset.setRange(-5.0, 5.0)
        self._z_offset.setDecimals(2)
        self._z_offset.setValue(0.0)
        g.addWidget(self._z_offset, 2, 3)

        g.addWidget(QLabel("Travel speed (nm/s)"), 3, 0)
        self._lat_speed = QDoubleSpinBox()
        self._lat_speed.setRange(0.1, 1000.0)
        self._lat_speed.setDecimals(1)
        self._lat_speed.setValue(10.0)
        self._lat_speed.valueChanged.connect(self._refresh_assessment)
        g.addWidget(self._lat_speed, 3, 1)

        match_btn = QPushButton("Match scan speed")
        match_btn.setToolTip(
            "Set the travel speed equal to the current scan speed — the "
            "least surprising motion to the tip-form target."
        )
        match_btn.clicked.connect(self._match_scan_speed)
        g.addWidget(match_btn, 3, 2)

        read_btn = QPushButton("Read from STM")
        read_btn.setToolTip(
            "One-shot read of the tip-form parameters and the scan "
            "speed/frame used by the motion check. No polling."
        )
        read_btn.clicked.connect(self._read_from_stm)
        g.addWidget(read_btn, 3, 3)

        root.addWidget(box)

        # Motion pre-flight feedback (computed locally; no COM on edit)
        self._assessment_label = QLabel("")
        self._assessment_label.setWordWrap(True)
        root.addWidget(self._assessment_label)

        self._arm_btn = QPushButton("Arm && run one pulse…")
        self._arm_btn.setProperty("accent", "true")
        self._arm_btn.clicked.connect(self._arm_and_run)
        root.addWidget(self._arm_btn)

        self._status = QLabel(
            "One supervised pulse per arming. The runner takes pre/post "
            "quality snapshots and logs everything to the acquisition log."
        )
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        root.addStretch(1)

    # ------------------------------------------------------------------
    # Instrument context (explicit, one-shot)
    # ------------------------------------------------------------------

    def _read_from_stm(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "Not connected",
                                "Connect to the STM (or Mock) first.")
            return
        try:
            speed = float(self._stm.scan.speed_nm_s or 0.0)
            size = self._stm.scan.size_nm
            if speed > 0:
                self._scan_speed_nm_s = speed
            if min(size) > 0:
                self._frame_size_nm = (float(size[0]), float(size[1]))
            self._context_is_live = True
            # Pre-fill pulse parameters from the instrument where present
            v = self._stm.getp("TIP-FORM.VOLTAGE.VOLT", None)
            if v not in (None, ""):
                self._voltage.setValue(float(v))
            lat = self._stm.getp("TIP-FORM.LATSPEED.NM/SEC", None)
            if lat not in (None, ""):
                self._lat_speed.setValue(float(lat))
            pulse = self._stm.getp("TIP-FORM.PULSELENGTH.SEC", None)
            if pulse not in (None, ""):
                self._pulse_s.setValue(float(pulse))
            self.log_message.emit(
                f"Tip form: read STM context — scan {self._scan_speed_nm_s:.1f} nm/s, "
                f"frame {self._frame_size_nm[0]:.0f}×{self._frame_size_nm[1]:.0f} nm"
            )
        except Exception as exc:
            self.error_message.emit(f"Tip form: read from STM failed: {exc}")
        self._refresh_assessment()

    def _match_scan_speed(self) -> None:
        self._lat_speed.setValue(self._scan_speed_nm_s)

    def _refresh_assessment(self) -> None:
        assessment = assess_tip_form_motion(
            self._lat_speed.value(),
            scan_speed_nm_s=self._scan_speed_nm_s,
            frame_size_nm=self._frame_size_nm,
        )
        source = ("instrument" if self._context_is_live
                  else "defaults — click 'Read from STM'")
        if assessment.ok:
            self._assessment_label.setText(
                f"Motion check OK (vs scan {assessment.scan_speed_nm_s:.1f} nm/s, "
                f"worst travel {assessment.worst_travel_s:.1f} s; {source})."
            )
            self._assessment_label.setStyleSheet("color: #1E7A1E;")
        else:
            self._assessment_label.setText(
                "⚠ " + "\n⚠ ".join(assessment.warnings) + f"\n({source})"
            )
            self._assessment_label.setStyleSheet(
                "color: #C75100; font-weight: bold;")
        self._last_assessment = assessment

    # ------------------------------------------------------------------
    # Arm & run
    # ------------------------------------------------------------------

    def _build_step(self) -> TipFormStep:
        return TipFormStep(
            x_px=self._x_px.value(),
            y_px=self._y_px.value(),
            voltage_V=self._voltage.value(),
            z_approach_nm=self._z_approach.value(),
            pulse_length_s=self._pulse_s.value(),
            z_offset_nm=self._z_offset.value(),
            lateral_speed_nm_s=self._lat_speed.value(),
            label="supervised tip form",
        )

    def _arm_and_run(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "Not connected",
                                "Connect to the STM (or Mock) first.")
            return
        if self._runner is not None and self._runner.isRunning():
            QMessageBox.warning(self, "Busy", "A tip-form run is in progress.")
            return
        try:
            step = self._build_step()
        except ValueError as exc:   # TipFormStep bounds validation
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        self._refresh_assessment()

        dialog = TipFormArmDialog(step, self._last_assessment, parent=self)
        if dialog.exec() != QDialog.Accepted:
            self.log_message.emit("Tip form: arming cancelled")
            return

        recipe = MeasurementRecipe(name="Supervised tip form", steps=[step])
        runner = AutomationRunner(self._stm, recipe)
        runner.error.connect(self.error_message)
        runner.info_message.connect(self.log_message)
        runner.state_changed.connect(self._on_state)
        runner.approve_next_tip_form()   # the H5 arming call — one pulse
        self._runner = runner
        self._arm_btn.setEnabled(False)
        self._status.setText("Tip form armed — running…")
        self.log_message.emit(
            f"Tip form ARMED: {step.voltage_V:+.2f} V, "
            f"{step.pulse_length_s * 1000:.0f} ms at "
            f"({step.x_px}, {step.y_px}) px"
        )
        runner.start()

    def _on_state(self, state) -> None:
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._arm_btn.setEnabled(True)
            done = ("Tip form completed — check the post snapshot in the log."
                    if state == RunnerState.FINISHED
                    else "Tip form run ended with an error — see the Log tab.")
            self._status.setText(done)
