"""Control panel: manual STM parameter controls in physical units (V, A, nm)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton, QGridLayout,
    QCheckBox, QListWidget, QListWidgetItem, QLineEdit,
)
from PySide6.QtCore import Qt

from scanflow.core import STMClient, STMNotConnectedError, ScanParams, Channel


STANDARD_CHANNELS = [
    Channel.TOPOGRAPHY,
    Channel.CURRENT,
    Channel.DF,
    Channel.DAMPING,
    Channel.AMPLITUDE,
    Channel.LOCKIN_X,
    Channel.LOCKIN_Y,
]


class ControlPanel(QWidget):
    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(self._build_bias_group(), 1)
        top_row.addWidget(self._build_current_group(), 1)
        layout.addLayout(top_row)

        layout.addWidget(self._build_scan_group())
        layout.addWidget(self._build_channel_group())

        # Action row
        action_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh from STM")
        refresh_btn.clicked.connect(self.refresh)
        action_row.addWidget(refresh_btn)

        apply_btn = QPushButton("Apply All to STM")
        apply_btn.clicked.connect(self._apply_all)
        action_row.addWidget(apply_btn)

        beep_btn = QPushButton("Beep (test link)")
        beep_btn.clicked.connect(self._beep)
        action_row.addWidget(beep_btn)
        layout.addLayout(action_row)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def _build_bias_group(self) -> QGroupBox:
        box = QGroupBox("Bias")
        g = QGridLayout(box)
        g.addWidget(QLabel("Target (V)"), 0, 0)
        self._bias_spin = QDoubleSpinBox()
        self._bias_spin.setRange(-10.0, 10.0)
        self._bias_spin.setDecimals(4)
        self._bias_spin.setSingleStep(0.01)
        self._bias_spin.setValue(0.1)
        g.addWidget(self._bias_spin, 0, 1)

        g.addWidget(QLabel("Ramp steps"), 1, 0)
        self._bias_steps = QSpinBox()
        self._bias_steps.setRange(1, 1000)
        self._bias_steps.setValue(100)
        g.addWidget(self._bias_steps, 1, 1)

        ramp_btn = QPushButton("Ramp Bias")
        ramp_btn.clicked.connect(self._ramp_bias)
        g.addWidget(ramp_btn, 2, 0, 1, 2)
        return box

    def _build_current_group(self) -> QGroupBox:
        box = QGroupBox("Setpoint (tunnelling current)")
        g = QGridLayout(box)
        g.addWidget(QLabel("Target (pA)"), 0, 0)
        self._current_spin = QDoubleSpinBox()
        self._current_spin.setRange(0.001, 1e6)
        self._current_spin.setDecimals(3)
        self._current_spin.setValue(100.0)
        g.addWidget(self._current_spin, 0, 1)

        g.addWidget(QLabel("Preamp 10^"), 1, 0)
        self._preamp_spin = QSpinBox()
        self._preamp_spin.setRange(6, 12)
        self._preamp_spin.setValue(9)
        g.addWidget(self._preamp_spin, 1, 1)

        g.addWidget(QLabel("Ramp steps"), 2, 0)
        self._current_steps = QSpinBox()
        self._current_steps.setRange(1, 1000)
        self._current_steps.setValue(100)
        g.addWidget(self._current_steps, 2, 1)

        ramp_btn = QPushButton("Ramp Current")
        ramp_btn.clicked.connect(self._ramp_current)
        g.addWidget(ramp_btn, 3, 0, 1, 2)
        return box

    def _build_scan_group(self) -> QGroupBox:
        box = QGroupBox("Scan geometry")
        g = QGridLayout(box)

        g.addWidget(QLabel("Size X (nm)"), 0, 0)
        self._size_x = QDoubleSpinBox()
        self._size_x.setRange(0.1, 50000.0)
        self._size_x.setDecimals(2)
        self._size_x.setValue(50.0)
        g.addWidget(self._size_x, 0, 1)

        g.addWidget(QLabel("Size Y (nm)"), 0, 2)
        self._size_y = QDoubleSpinBox()
        self._size_y.setRange(0.1, 50000.0)
        self._size_y.setDecimals(2)
        self._size_y.setValue(50.0)
        g.addWidget(self._size_y, 0, 3)

        g.addWidget(QLabel("Speed (nm/s)"), 1, 0)
        self._speed = QDoubleSpinBox()
        self._speed.setRange(0.01, 1000.0)
        self._speed.setDecimals(2)
        self._speed.setValue(50.0)
        g.addWidget(self._speed, 1, 1)

        g.addWidget(QLabel("Pixels X"), 1, 2)
        self._pix_x = QSpinBox()
        self._pix_x.setRange(8, 8192)
        self._pix_x.setValue(256)
        g.addWidget(self._pix_x, 1, 3)

        g.addWidget(QLabel("Pixels Y"), 2, 2)
        self._pix_y = QSpinBox()
        self._pix_y.setRange(8, 8192)
        self._pix_y.setValue(256)
        g.addWidget(self._pix_y, 2, 3)

        g.addWidget(QLabel("Rotation (°)"), 2, 0)
        self._rot = QDoubleSpinBox()
        self._rot.setRange(-360.0, 360.0)
        self._rot.setDecimals(2)
        g.addWidget(self._rot, 2, 1)

        self._const_h = QCheckBox("Constant height mode")
        g.addWidget(self._const_h, 3, 0, 1, 2)

        g.addWidget(QLabel("Memo"), 4, 0)
        self._memo = QLineEdit()
        g.addWidget(self._memo, 4, 1, 1, 3)

        self._duration_label = QLabel("Estimated duration: —")
        g.addWidget(self._duration_label, 5, 0, 1, 4)

        return box

    def _build_channel_group(self) -> QGroupBox:
        box = QGroupBox("Recorded channels")
        v = QVBoxLayout(box)
        self._channel_list = QListWidget()
        self._channel_list.setSelectionMode(QListWidget.MultiSelection)
        for ch in STANDARD_CHANNELS:
            item = QListWidgetItem(ch)
            self._channel_list.addItem(item)
            if ch in (Channel.TOPOGRAPHY, Channel.CURRENT):
                item.setSelected(True)
        v.addWidget(self._channel_list)
        return box

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _selected_channels(self) -> tuple[str, ...]:
        return tuple(
            self._channel_list.item(i).text()
            for i in range(self._channel_list.count())
            if self._channel_list.item(i).isSelected()
        )

    def _current_params(self) -> ScanParams:
        return ScanParams(
            bias_V=self._bias_spin.value(),
            setpoint_A=self._current_spin.value() * 1e-12,
            size_nm=(self._size_x.value(), self._size_y.value()),
            speed_nm_s=self._speed.value(),
            pixels=(self._pix_x.value(), self._pix_y.value()),
            rotation_deg=self._rot.value(),
            const_height=self._const_h.isChecked(),
            channels=self._selected_channels() or (Channel.TOPOGRAPHY,),
            preamp_exponent=self._preamp_spin.value(),
            memo=self._memo.text(),
        )

    def refresh(self) -> None:
        if not self._stm.connected:
            return
        try:
            params = self._stm.scan.read()
            self._bias_spin.setValue(params.bias_V)
            self._current_spin.setValue(params.setpoint_A * 1e12)
            self._size_x.setValue(params.size_nm[0])
            self._size_y.setValue(params.size_nm[1])
            self._speed.setValue(params.speed_nm_s)
            self._pix_x.setValue(params.pixels[0])
            self._pix_y.setValue(params.pixels[1])
            self._rot.setValue(params.rotation_deg)
            self._preamp_spin.setValue(params.preamp_exponent)
            duration = self._stm.scan.duration_s
            if duration:
                self._duration_label.setText(f"Estimated duration: {duration:.1f} s")
            self._sync_channels(params.channels)
        except STMNotConnectedError:
            pass

    def _sync_channels(self, channels: tuple[str, ...]) -> None:
        for i in range(self._channel_list.count()):
            item = self._channel_list.item(i)
            item.setSelected(item.text() in channels)

    def _ramp_bias(self) -> None:
        try:
            self._stm.feedback.ramp_bias_V(self._bias_spin.value(), self._bias_steps.value())
        except STMNotConnectedError:
            pass

    def _ramp_current(self) -> None:
        try:
            self._stm.feedback.ramp_setpoint_A(
                self._current_spin.value() * 1e-12,
                self._current_steps.value(),
            )
        except STMNotConnectedError:
            pass

    def _apply_all(self) -> None:
        try:
            self._stm.scan.apply(self._current_params())
        except STMNotConnectedError:
            pass

    def _beep(self) -> None:
        try:
            self._stm.beep()
        except STMNotConnectedError:
            pass
