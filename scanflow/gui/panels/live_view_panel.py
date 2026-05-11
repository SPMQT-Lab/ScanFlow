"""Live view panel — displays the scan image as it is acquired.

Pulls data directly from the DSP via ``ScanController.live_data`` so the
image updates in real time during a scan, no disk round-trip required.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QGroupBox, QGridLayout, QCheckBox, QSpinBox,
)
from PySide6.QtCore import QTimer, Signal

try:
    import pyqtgraph as pg
    _HAS_PYQTGRAPH = True
except ImportError:
    _HAS_PYQTGRAPH = False

from scanflow.core import STMClient
from scanflow.core.scan import ScanDataChannel, ScanDataUnit


_CHANNEL_PRESETS = [
    ("Topography (nm) — fwd", ScanDataChannel.TOPOGRAPHY_FWD, ScanDataUnit.NM),
    ("Topography (nm) — bwd", ScanDataChannel.TOPOGRAPHY_BWD, ScanDataUnit.NM),
    ("Current (A) — fwd",     ScanDataChannel.CURRENT_FWD,    ScanDataUnit.AMPERE),
    ("Current (A) — bwd",     ScanDataChannel.CURRENT_BWD,    ScanDataUnit.AMPERE),
    ("Δf (Hz) — fwd",         ScanDataChannel.DF_FWD,         ScanDataUnit.HZ),
    ("Damping (V) — fwd",     ScanDataChannel.DAMPING_FWD,    ScanDataUnit.VOLT),
    ("Amplitude (V) — fwd",   ScanDataChannel.AMPLITUDE_FWD,  ScanDataUnit.VOLT),
]


class LiveViewPanel(QWidget):
    """Polls the STM during a scan and renders the live image."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._last_array: Optional[np.ndarray] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Controls
        ctl = QGroupBox("Live view controls")
        cg = QGridLayout(ctl)

        cg.addWidget(QLabel("Channel"), 0, 0)
        self._channel_combo = QComboBox()
        for label, _, _ in _CHANNEL_PRESETS:
            self._channel_combo.addItem(label)
        cg.addWidget(self._channel_combo, 0, 1)

        cg.addWidget(QLabel("Poll interval (ms)"), 0, 2)
        self._poll_ms = QSpinBox()
        self._poll_ms.setRange(100, 5000)
        self._poll_ms.setSingleStep(100)
        self._poll_ms.setValue(500)
        self._poll_ms.valueChanged.connect(self._on_interval_changed)
        cg.addWidget(self._poll_ms, 0, 3)

        self._auto_chk = QCheckBox("Auto-start while scan is running")
        self._auto_chk.setChecked(True)
        cg.addWidget(self._auto_chk, 1, 0, 1, 2)

        self._auto_levels_chk = QCheckBox("Auto-level (percentile clip)")
        self._auto_levels_chk.setChecked(True)
        cg.addWidget(self._auto_levels_chk, 1, 2, 1, 2)

        self._btn_start = QPushButton("Start")
        self._btn_start.clicked.connect(self.start)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.clicked.connect(self.stop)
        self._btn_snap = QPushButton("Snapshot")
        self._btn_snap.clicked.connect(self._poll)
        cg.addWidget(self._btn_start, 2, 0)
        cg.addWidget(self._btn_stop, 2, 1)
        cg.addWidget(self._btn_snap, 2, 2)

        self._status_label = QLabel("Idle")
        cg.addWidget(self._status_label, 2, 3)

        root.addWidget(ctl)

        # Image
        if _HAS_PYQTGRAPH:
            self._image = pg.ImageView()
            self._image.ui.roiBtn.hide()
            self._image.ui.menuBtn.hide()
            root.addWidget(self._image, stretch=1)
        else:
            self._image = None
            root.addWidget(QLabel("Install pyqtgraph to see the live image."))

        # Auto-start when a runner emits frames or a manual scan begins
        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.timeout.connect(self._maybe_auto_start)
        self._status_poll_timer.start(2000)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self._stm.connected:
            self._status_label.setText("STM not connected")
            return
        self._timer.start(self._poll_ms.value())
        self._status_label.setText("Live")

    def stop(self) -> None:
        self._timer.stop()
        self._status_label.setText("Stopped")

    def _on_interval_changed(self, ms: int) -> None:
        if self._timer.isActive():
            self._timer.start(ms)

    def _maybe_auto_start(self) -> None:
        if not self._auto_chk.isChecked():
            return
        if not self._stm.connected:
            return
        try:
            running = self._stm.scan.is_running
        except Exception:
            return
        if running and not self._timer.isActive():
            self.start()
        elif not running and self._timer.isActive():
            # Stop after one final poll so the final frame is captured
            self._poll()
            self.stop()

    # ------------------------------------------------------------------
    # Data pull
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        if not self._stm.connected:
            self._status_label.setText("STM not connected")
            return
        ch_idx = self._channel_combo.currentIndex()
        _, channel, unit = _CHANNEL_PRESETS[ch_idx]
        arr = self._stm.scan.live_data(channel=int(channel), unit=int(unit))
        if arr is None:
            return
        if arr.ndim == 1:
            return  # unexpected shape — skip
        self._last_array = arr
        self.show_frame(arr)

    def show_frame(self, arr: np.ndarray) -> None:
        """Public entry-point so the AutomationRunner can push frames in too."""
        if not _HAS_PYQTGRAPH or self._image is None or arr is None:
            return
        levels = None
        if self._auto_levels_chk.isChecked() and arr.size > 0:
            lo, hi = np.percentile(arr, (2, 98))
            if hi > lo:
                levels = (lo, hi)
        self._image.setImage(arr.T, autoLevels=False, levels=levels)
