"""Time spectroscopy: record a channel vs time at a fixed tip position.

Useful for:
  • Noise characterisation (I vs t at constant bias / setpoint)
  • Drift studies (topography Z vs t)
  • Manipulation event detection (current spikes during atom dragging)

The CreaTec API:
    btn_timespec()                  — start recording with stored params
    getp('DATA.TIMESPEC', (ch, u))  — read back the time trace
    timespecsave()                  — write the .tspec file
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QComboBox, QPushButton, QDoubleSpinBox, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Signal, QTimer

try:
    import pyqtgraph as pg
    _HAS_PYQTGRAPH = True
except ImportError:
    _HAS_PYQTGRAPH = False

from scanflow.core import STMClient


# DATA.TIMESPEC channel codes are similar to scan but referenced 0-based:
#   (1, 0) = topography (raw V), (2, 0) = current (V; multiply by preamp gain for A)
_TS_CHANNELS = [
    ("Topography (V)", 1, 0),
    ("Current (V)", 2, 0),
    ("Current (A)", 2, 3),
    ("Δf (Hz)", 7, 5),
    ("Damping (V)", 8, 0),
    ("Amplitude (V)", 9, 0),
]


class TimeSpecPanel(QWidget):
    """Triggers a time-spectrum recording and plots the result live."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_data)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        ctl = QGroupBox("Recording")
        cg = QGridLayout(ctl)

        cg.addWidget(QLabel("Channel"), 0, 0)
        self._channel_combo = QComboBox()
        for label, _, _ in _TS_CHANNELS:
            self._channel_combo.addItem(label)
        cg.addWidget(self._channel_combo, 0, 1)

        cg.addWidget(QLabel("Poll interval (s)"), 0, 2)
        self._poll_s = QDoubleSpinBox()
        self._poll_s.setRange(0.1, 10.0)
        self._poll_s.setSingleStep(0.1)
        self._poll_s.setValue(0.5)
        cg.addWidget(self._poll_s, 0, 3)

        start_btn = QPushButton("Start recording")
        start_btn.setProperty("accent", "true")
        start_btn.clicked.connect(self._start)
        cg.addWidget(start_btn, 1, 0, 1, 2)

        stop_btn = QPushButton("Stop polling")
        stop_btn.clicked.connect(self._stop)
        cg.addWidget(stop_btn, 1, 2)

        save_btn = QPushButton("Save .tspec…")
        save_btn.clicked.connect(self._save)
        cg.addWidget(save_btn, 1, 3)

        self._status = QLabel("Idle")
        cg.addWidget(self._status, 2, 0, 1, 4)
        root.addWidget(ctl)

        if _HAS_PYQTGRAPH:
            self._plot = pg.PlotWidget()
            self._plot.setLabel("bottom", "Time (s)")
            self._plot.setLabel("left", "Signal")
            self._curve = self._plot.plot(pen="y")
            root.addWidget(self._plot, 1)
        else:
            self._plot = None
            self._curve = None
            root.addWidget(QLabel("Install pyqtgraph for the time-trace plot."))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _require_connection(self) -> bool:
        if not self._stm.connected:
            QMessageBox.warning(self, "STM Not Connected",
                                "Connect before recording a time spectrum.")
            return False
        return True

    def _start(self) -> None:
        if not self._require_connection():
            return
        try:
            self._stm.spec.time_spec_start()
            self.log_message.emit("Time spec: recording started")
            self._status.setText("Recording — polling for live data…")
            interval_ms = int(self._poll_s.value() * 1000)
            self._poll_timer.start(interval_ms)
        except Exception as e:
            QMessageBox.critical(self, "Time spec error", str(e))

    def _stop(self) -> None:
        self._poll_timer.stop()
        self._status.setText("Polling stopped")

    def _save(self) -> None:
        if not self._require_connection():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save time spectrum", "timespec.tspec",
                                              "TSpec files (*.tspec)")
        if not path:
            return
        try:
            self._stm.raw.timespecsave()
            self.log_message.emit(f"Time spec saved")
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))

    def _poll_data(self) -> None:
        if not self._stm.connected:
            self._stop()
            return
        idx = self._channel_combo.currentIndex()
        _, channel, unit = _TS_CHANNELS[idx]
        try:
            raw = self._stm.getp("DATA.TIMESPEC", (int(channel), int(unit)))
        except Exception:
            return
        if raw is None:
            return
        try:
            data = np.asarray(raw, dtype=float)
        except Exception:
            return
        if data.size == 0:
            return
        # CreaTec layout: first row = time axis, second row = signal
        if data.ndim == 2 and data.shape[0] >= 2:
            t = data[0]
            y = data[1]
        elif data.size >= 2 and data.size % 2 == 0:
            half = data.size // 2
            t = data[:half]
            y = data[half:]
        else:
            t = np.arange(data.size, dtype=float) * self._poll_s.value()
            y = data
        if self._curve is not None:
            self._curve.setData(t, y)
        self._status.setText(f"Live trace: {len(y)} samples")
