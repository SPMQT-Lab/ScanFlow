"""Spectroscopy panel: I/V (dI/dV) point spectroscopy."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton,
    QCheckBox, QListWidget, QFileDialog, QLineEdit,
)
from PySide6.QtCore import Signal

from scanflow.core import STMClient, STMNotConnectedError, IVTable, LockInMode


SPEC_CHANNELS = ["Current(filtered)", "Lock-in X", "Lock-in Y", "Topography"]


class SpectroscopyPanel(QWidget):
    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_lockin_group())
        layout.addWidget(self._build_iv_group())
        layout.addWidget(self._build_run_group())
        layout.addStretch()

    def _build_lockin_group(self) -> QGroupBox:
        box = QGroupBox("Lock-in amplifier (bias modulation)")
        g = QGridLayout(box)

        g.addWidget(QLabel("Frequency (Hz)"), 0, 0)
        self._li_freq = QDoubleSpinBox()
        self._li_freq.setRange(1.0, 10000.0)
        self._li_freq.setDecimals(2)
        self._li_freq.setValue(652.7)
        g.addWidget(self._li_freq, 0, 1)

        g.addWidget(QLabel("Amplitude (mV pp)"), 0, 2)
        self._li_amp = QDoubleSpinBox()
        self._li_amp.setRange(0.01, 500.0)
        self._li_amp.setDecimals(2)
        self._li_amp.setValue(20.0)
        g.addWidget(self._li_amp, 0, 3)

        self._li_autophase = QCheckBox("Autophase (+90° → push dI/dV to X)")
        self._li_autophase.setChecked(True)
        g.addWidget(self._li_autophase, 1, 0, 1, 4)

        apply_btn = QPushButton("Configure lock-in")
        apply_btn.clicked.connect(self._apply_lockin)
        g.addWidget(apply_btn, 2, 0, 1, 4)

        return box

    def _build_iv_group(self) -> QGroupBox:
        box = QGroupBox("I/V spectrum")
        g = QGridLayout(box)

        g.addWidget(QLabel("Start bias (V)"), 0, 0)
        self._iv_start = QDoubleSpinBox()
        self._iv_start.setRange(-10.0, 10.0)
        self._iv_start.setDecimals(4)
        self._iv_start.setValue(-0.7)
        g.addWidget(self._iv_start, 0, 1)

        g.addWidget(QLabel("End bias (V)"), 0, 2)
        self._iv_end = QDoubleSpinBox()
        self._iv_end.setRange(-10.0, 10.0)
        self._iv_end.setDecimals(4)
        self._iv_end.setValue(0.7)
        g.addWidget(self._iv_end, 0, 3)

        g.addWidget(QLabel("Points"), 1, 0)
        self._iv_points = QSpinBox()
        self._iv_points.setRange(8, 16384)
        self._iv_points.setValue(1024)
        g.addWidget(self._iv_points, 1, 1)

        g.addWidget(QLabel("Duration (s)"), 1, 2)
        self._iv_duration = QDoubleSpinBox()
        self._iv_duration.setRange(0.1, 600.0)
        self._iv_duration.setDecimals(2)
        self._iv_duration.setValue(10.0)
        g.addWidget(self._iv_duration, 1, 3)

        g.addWidget(QLabel("Repeat count"), 2, 0)
        self._iv_repeat = QSpinBox()
        self._iv_repeat.setRange(1, 1000)
        self._iv_repeat.setValue(1)
        g.addWidget(self._iv_repeat, 2, 1)

        g.addWidget(QLabel("Average count"), 2, 2)
        self._iv_avg = QSpinBox()
        self._iv_avg.setRange(1, 100)
        self._iv_avg.setValue(1)
        g.addWidget(self._iv_avg, 2, 3)

        self._iv_backward = QCheckBox("Include backward sweep")
        self._iv_backward.setChecked(True)
        g.addWidget(self._iv_backward, 3, 0, 1, 4)

        g.addWidget(QLabel("Channels"), 4, 0)
        self._channel_list = QListWidget()
        self._channel_list.setSelectionMode(QListWidget.MultiSelection)
        for ch in SPEC_CHANNELS:
            self._channel_list.addItem(ch)
            self._channel_list.item(self._channel_list.count() - 1).setSelected(True)
        g.addWidget(self._channel_list, 4, 1, 1, 3)

        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        g = QGridLayout(box)

        g.addWidget(QLabel("Pixel X"), 0, 0)
        self._pix_x = QSpinBox()
        self._pix_x.setRange(0, 8192)
        self._pix_x.setValue(128)
        g.addWidget(self._pix_x, 0, 1)

        g.addWidget(QLabel("Pixel Y"), 0, 2)
        self._pix_y = QSpinBox()
        self._pix_y.setRange(0, 8192)
        self._pix_y.setValue(128)
        g.addWidget(self._pix_y, 0, 3)

        single_btn = QPushButton("Run single spectrum")
        single_btn.clicked.connect(self._run_single)
        g.addWidget(single_btn, 1, 0, 1, 2)

        save_btn = QPushButton("Save last .VERT…")
        save_btn.clicked.connect(self._save_vert)
        g.addWidget(save_btn, 1, 2, 1, 2)

        g.addWidget(QLabel("File name"), 2, 0)
        self._fn = QLineEdit("spectrum.VERT")
        g.addWidget(self._fn, 2, 1, 1, 3)

        return box

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _apply_lockin(self) -> None:
        try:
            self._stm.lockin.configure(
                freq_Hz=self._li_freq.value(),
                amplitude_mVpp=self._li_amp.value(),
                channel="Current(ADC0)",
                mode=LockInMode.INTERNAL_SCAN_OFF,
                autophase=self._li_autophase.isChecked(),
            )
            self.log_message.emit(f"Lock-in: {self._li_freq.value()} Hz @ {self._li_amp.value()} mVpp")
        except STMNotConnectedError:
            self.log_message.emit("STM not connected")

    def _selected_channels(self) -> tuple[str, ...]:
        return tuple(
            self._channel_list.item(i).text()
            for i in range(self._channel_list.count())
            if self._channel_list.item(i).isSelected()
        )

    def _run_single(self) -> None:
        try:
            table = IVTable(
                bias_start_V=self._iv_start.value(),
                bias_end_V=self._iv_end.value(),
                points=self._iv_points.value(),
                backward_sweep=self._iv_backward.isChecked(),
            )
            self._stm.spec.configure(
                table=table,
                channels=self._selected_channels() or ("Current(filtered)",),
                duration_s=self._iv_duration.value(),
                repeat_count=self._iv_repeat.value(),
                average_count=self._iv_avg.value(),
            )
            self._stm.spec.single_at_pixel(self._pix_x.value(), self._pix_y.value())
            self.log_message.emit(
                f"Spec @ ({self._pix_x.value()}, {self._pix_y.value()}): "
                f"{self._iv_start.value():.3f} → {self._iv_end.value():.3f} V"
            )
        except STMNotConnectedError:
            self.log_message.emit("STM not connected")

    def _save_vert(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save spectrum", self._fn.text(),
                                              "Vert files (*.VERT *.vert)")
        if not path:
            return
        try:
            self._stm.spec.save_vert(path)
            self.log_message.emit(f"Saved spectrum: {path}")
        except STMNotConnectedError:
            self.log_message.emit("STM not connected")
