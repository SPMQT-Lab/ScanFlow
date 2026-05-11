"""Coarse motion panel: approach, Z-limit, and XYZ slider stepper motion.

This is the panel users touch FIRST in a session — to move the tip into
tunnelling range from a fully retracted position. Tip-crash safety is
critical, so Z-limit is exposed prominently and the approach status is
polled live.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton, QGridLayout,
    QCheckBox, QProgressBar, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, Signal

from scanflow.core import (
    STMClient, STMNotConnectedError,
    CoarseController, ApproachConfig, RampParams,
)
from scanflow.core.coarse import SliderAxis


class CoarsePanel(QWidget):
    """Approach + slider motion controls."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._approach_timer = QTimer(self)
        self._approach_timer.setInterval(1000)
        self._approach_timer.timeout.connect(self._poll_approach)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(self._build_zlimit_group())
        layout.addWidget(self._build_approach_group())
        layout.addWidget(self._build_ramp_group())
        layout.addWidget(self._build_slider_group())
        layout.addStretch()

    # ------------------------------------------------------------------

    def _build_zlimit_group(self) -> QGroupBox:
        box = QGroupBox("Z-limit (tip safety)")
        g = QGridLayout(box)

        g.addWidget(QLabel("Retract height (nm)"), 0, 0)
        self._retract_nm = QDoubleSpinBox()
        self._retract_nm.setRange(0.1, 1000.0)
        self._retract_nm.setDecimals(2)
        self._retract_nm.setValue(10.0)
        g.addWidget(self._retract_nm, 0, 1)

        zlim_on = QPushButton("Z-limit ON (retract)")
        zlim_on.clicked.connect(self._zlimit_on)
        g.addWidget(zlim_on, 1, 0)

        zlim_off = QPushButton("Z-limit OFF (release)")
        zlim_off.clicked.connect(self._zlimit_off)
        g.addWidget(zlim_off, 1, 1)

        self._zlim_status = QLabel("Status: —")
        g.addWidget(self._zlim_status, 2, 0, 1, 2)

        return box

    def _build_approach_group(self) -> QGroupBox:
        box = QGroupBox("Coarse approach")
        g = QGridLayout(box)

        g.addWidget(QLabel("Bias (V)"), 0, 0)
        self._app_bias = QDoubleSpinBox()
        self._app_bias.setRange(-10.0, 10.0)
        self._app_bias.setDecimals(3)
        self._app_bias.setValue(2.0)
        g.addWidget(self._app_bias, 0, 1)

        g.addWidget(QLabel("Setpoint (pA)"), 0, 2)
        self._app_current = QDoubleSpinBox()
        self._app_current.setRange(0.001, 1e5)
        self._app_current.setDecimals(3)
        self._app_current.setValue(1000.0)  # 1 nA
        g.addWidget(self._app_current, 0, 3)

        g.addWidget(QLabel("Burst per cycle"), 1, 0)
        self._app_burst = QSpinBox()
        self._app_burst.setRange(1, 100)
        self._app_burst.setValue(1)
        g.addWidget(self._app_burst, 1, 1)

        g.addWidget(QLabel("Retries"), 1, 2)
        self._app_retry = QSpinBox()
        self._app_retry.setRange(1, 100)
        self._app_retry.setValue(1)
        g.addWidget(self._app_retry, 1, 3)

        g.addWidget(QLabel("Cycle period (s)"), 2, 0)
        self._app_period = QDoubleSpinBox()
        self._app_period.setRange(0.1, 30.0)
        self._app_period.setDecimals(2)
        self._app_period.setValue(1.5)
        g.addWidget(self._app_period, 2, 1)

        start = QPushButton("Start approach")
        start.clicked.connect(self._start_approach)
        g.addWidget(start, 3, 0, 1, 2)

        stop = QPushButton("Stop")
        stop.clicked.connect(self._stop_approach)
        g.addWidget(stop, 3, 2, 1, 2)

        self._approach_bar = QProgressBar()
        self._approach_bar.setMinimum(0)
        self._approach_bar.setMaximum(0)
        self._approach_bar.setVisible(False)
        g.addWidget(self._approach_bar, 4, 0, 1, 4)

        self._approach_status = QLabel("Status: idle")
        g.addWidget(self._approach_status, 5, 0, 1, 4)

        return box

    def _build_ramp_group(self) -> QGroupBox:
        box = QGroupBox("Coarse-pulse ramp parameters")
        g = QGridLayout(box)

        g.addWidget(QLabel("Pulse height (V)"), 0, 0)
        self._ramp_height = QDoubleSpinBox()
        self._ramp_height.setRange(1.0, 200.0)
        self._ramp_height.setDecimals(1)
        self._ramp_height.setValue(50.0)
        g.addWidget(self._ramp_height, 0, 1)

        g.addWidget(QLabel("Pulse duration (s)"), 0, 2)
        self._ramp_dur = QDoubleSpinBox()
        self._ramp_dur.setRange(0.0001, 1.0)
        self._ramp_dur.setDecimals(4)
        self._ramp_dur.setValue(0.003)
        g.addWidget(self._ramp_dur, 0, 3)

        g.addWidget(QLabel("Burst count XY"), 1, 0)
        self._ramp_burst_xy = QSpinBox()
        self._ramp_burst_xy.setRange(0, 10000)
        self._ramp_burst_xy.setValue(1)
        g.addWidget(self._ramp_burst_xy, 1, 1)

        g.addWidget(QLabel("Burst count Z"), 1, 2)
        self._ramp_burst_z = QSpinBox()
        self._ramp_burst_z.setRange(0, 10000)
        self._ramp_burst_z.setValue(1)
        g.addWidget(self._ramp_burst_z, 1, 3)

        self._burst_xy_on = QCheckBox("Burst XY on")
        g.addWidget(self._burst_xy_on, 2, 0, 1, 2)
        self._burst_z_on = QCheckBox("Burst Z on")
        g.addWidget(self._burst_z_on, 2, 2, 1, 2)

        apply_btn = QPushButton("Apply ramp parameters")
        apply_btn.clicked.connect(self._apply_ramp)
        g.addWidget(apply_btn, 3, 0, 1, 4)

        return box

    def _build_slider_group(self) -> QGroupBox:
        box = QGroupBox("XYZ slider (coarse motion across sample)")
        g = QGridLayout(box)
        g.addWidget(QLabel("Steps"), 0, 0)
        self._slider_steps = QSpinBox()
        self._slider_steps.setRange(1, 1000)
        self._slider_steps.setValue(100)
        g.addWidget(self._slider_steps, 0, 1)

        directions = [
            ("X+", SliderAxis.X_PLUS, 1, 0),
            ("X-", SliderAxis.X_MINUS, 1, 1),
            ("Y+", SliderAxis.Y_PLUS, 1, 2),
            ("Y-", SliderAxis.Y_MINUS, 1, 3),
            ("Z+ (down/closer)", SliderAxis.Z_PLUS, 2, 0),
            ("Z- (up/away)", SliderAxis.Z_MINUS, 2, 1),
        ]
        for label, axis, row, col in directions:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, ax=axis: self._slider_step(ax))
            g.addWidget(btn, row, col)

        warn = QLabel("⚠ Always set Z-limit ON before XY moves with tip in tunnelling.")
        warn.setWordWrap(True)
        g.addWidget(warn, 3, 0, 1, 4)
        return box

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _zlimit_on(self) -> None:
        try:
            self._stm.coarse.z_limit_on(retract_nm=self._retract_nm.value())
            self._zlim_status.setText(f"Status: ON (retract {self._retract_nm.value():.1f} nm)")
            self.log_message.emit(f"Z-limit ON ({self._retract_nm.value():.1f} nm)")
        except STMNotConnectedError:
            self._not_connected()

    def _zlimit_off(self) -> None:
        try:
            self._stm.coarse.z_limit_off()
            self._zlim_status.setText("Status: OFF (tip may approach)")
            self.log_message.emit("Z-limit OFF")
        except STMNotConnectedError:
            self._not_connected()

    def _apply_ramp(self) -> None:
        try:
            self._stm.coarse.set_ramp_params(RampParams(
                pulse_height_V=self._ramp_height.value(),
                pulse_duration_s=self._ramp_dur.value(),
                burst_count_xy=self._ramp_burst_xy.value(),
                burst_count_z=self._ramp_burst_z.value(),
                burst_xy_on=self._burst_xy_on.isChecked(),
                burst_z_on=self._burst_z_on.isChecked(),
            ))
            self.log_message.emit("Coarse ramp parameters applied")
        except STMNotConnectedError:
            self._not_connected()

    def _start_approach(self) -> None:
        try:
            self._stm.coarse.configure_approach(ApproachConfig(
                burst_count=self._app_burst.value(),
                retry_count=self._app_retry.value(),
                period_s=self._app_period.value(),
                target_current_A=self._app_current.value() * 1e-12,
                bias_V=self._app_bias.value(),
            ))
            self._stm.coarse.start_approach()
            self._approach_bar.setVisible(True)
            self._approach_status.setText("Status: approaching…")
            self._approach_timer.start()
            self.log_message.emit("Coarse approach started")
        except STMNotConnectedError:
            self._not_connected()

    def _stop_approach(self) -> None:
        try:
            self._stm.coarse.stop_approach()
            self._approach_timer.stop()
            self._approach_bar.setVisible(False)
            self._approach_status.setText("Status: stopped")
            self.log_message.emit("Coarse approach stopped")
        except STMNotConnectedError:
            self._not_connected()

    def _poll_approach(self) -> None:
        try:
            if self._stm.coarse.approach_finished:
                self._approach_timer.stop()
                self._approach_bar.setVisible(False)
                self._approach_status.setText("Status: tunnelling reached ✓")
                self.log_message.emit("Coarse approach finished — tunnelling reached")
        except STMNotConnectedError:
            self._approach_timer.stop()
            self._approach_bar.setVisible(False)

    def _slider_step(self, axis: SliderAxis) -> None:
        # Safety check: ensure Z-limit is on for XY motion
        if axis in (SliderAxis.X_PLUS, SliderAxis.X_MINUS,
                    SliderAxis.Y_PLUS, SliderAxis.Y_MINUS):
            try:
                if not self._stm.coarse.z_limit_active:
                    reply = QMessageBox.question(
                        self, "Z-limit is OFF",
                        "Z-limit is OFF. Moving XY with the tip in tunnelling "
                        "can crash the tip. Continue anyway?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    )
                    if reply != QMessageBox.Yes:
                        return
            except STMNotConnectedError:
                self._not_connected()
                return
        try:
            self._stm.coarse.slider_step(axis, self._slider_steps.value())
            self.log_message.emit(f"Slider {axis.name} × {self._slider_steps.value()}")
        except STMNotConnectedError:
            self._not_connected()

    def _not_connected(self) -> None:
        self.log_message.emit("STM not connected")
