"""Z Stability tab — live plot of tip Z, plus rolling-window ΔZ stats."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QGridLayout, QGroupBox, QFileDialog, QFrame,
)

from scanflow.core.z_monitor import ZMonitor, format_summary

log = logging.getLogger(__name__)


class ZStabilityPanel(QWidget):
    """Live Z-drift plot and 5 min / 1 h / 3 h ΔZ readouts."""

    log_message = Signal(str)

    def __init__(self, monitor: ZMonitor, parent=None) -> None:
        super().__init__(parent)
        self._monitor = monitor
        self._window_s: float | None = 5 * 60.0
        self._build_ui()

        monitor.sample_added.connect(self._on_sample)
        monitor.summary.connect(self._on_summary)

        # Refresh the stats grid once a second from the buffer (cheaper than
        # recomputing on every sample, and looks smooth in the UI)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Controls row
        controls = QHBoxLayout()
        controls.addWidget(QLabel("View window:"))
        self._window_combo = QComboBox()
        self._window_combo.addItems(["5 min", "1 hour", "3 hour", "All history"])
        self._window_combo.currentIndexChanged.connect(self._on_window_change)
        controls.addWidget(self._window_combo)

        controls.addSpacing(20)
        controls.addWidget(QLabel("DAC scale (Å / count):"))
        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(1e-6, 1e6)
        self._scale_spin.setDecimals(6)
        self._scale_spin.setSingleStep(0.001)
        self._scale_spin.setValue(self._monitor.scale)
        self._scale_spin.setToolTip(
            "Multiplier from raw DAC counts to Ångström. Calibrate against a "
            "known Z movement (e.g. a step edge of known height) and enter the "
            "ratio here. Defaults to 1.0 — Å values are then in 'DAC counts'."
        )
        self._scale_spin.valueChanged.connect(self._on_scale_change)
        controls.addWidget(self._scale_spin)

        controls.addStretch(1)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_history)
        controls.addWidget(clear_btn)
        save_btn = QPushButton("Save CSV…")
        save_btn.clicked.connect(self._save_csv)
        controls.addWidget(save_btn)
        root.addLayout(controls)

        # Live plot
        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.setLabel("left", "Z drift (Å, mean-centred)")
        self._plot.setLabel("bottom", "Time (min)")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._curve = self._plot.plot(pen=pg.mkPen("#0077b6", width=1.5))
        root.addWidget(self._plot, 1)

        # Stats: 5 min / 1 h / 3 h rolling windows
        stats_group = QGroupBox("Δ Z over rolling window")
        grid = QGridLayout(stats_group)
        headers = ["Window", "Δ peak-to-peak (Å)", "σ (Å)", "Drift (Å / h)", "Samples"]
        for c, h in enumerate(headers):
            lbl = QLabel(f"<b>{h}</b>")
            grid.addWidget(lbl, 0, c)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setFrameShadow(QFrame.Sunken)
            grid.addWidget(sep, 1, c)
        self._stat_labels: dict[tuple[int, int], QLabel] = {}
        for row, name in enumerate(["5 min", "1 hour", "3 hour"], start=2):
            grid.addWidget(QLabel(f"<b>{name}</b>"), row, 0)
            for col in range(1, 5):
                lbl = QLabel("—")
                lbl.setAlignment(Qt.AlignCenter)
                self._stat_labels[(row, col)] = lbl
                grid.addWidget(lbl, row, col)
        root.addWidget(stats_group)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_window_change(self, idx: int) -> None:
        self._window_s = {0: 5 * 60.0, 1: 60 * 60.0, 2: 3 * 60 * 60.0, 3: None}[idx]
        self._refresh_plot()

    def _on_scale_change(self, value: float) -> None:
        self._monitor.set_scale(value)
        self._refresh_plot()
        self._refresh_stats()

    def _on_sample(self, t: float, z: float) -> None:
        # Throttle plot updates to avoid hammering on long histories;
        # the timer-driven refresh is fast enough at 1 Hz on its own.
        self._refresh_plot()

    def _refresh_plot(self) -> None:
        ts, zs = self._monitor.get_samples()
        if ts.size == 0:
            self._curve.setData([], [])
            return
        t0 = float(ts[0])
        t_min_full = (ts - t0) / 60.0
        zs_centered = zs - float(np.mean(zs))
        if self._window_s:
            cutoff = (float(ts[-1]) - t0) / 60.0 - self._window_s / 60.0
            mask = t_min_full >= cutoff
            self._curve.setData(t_min_full[mask], zs_centered[mask])
        else:
            self._curve.setData(t_min_full, zs_centered)

    def _refresh_stats(self) -> None:
        for row, sec in [(2, 5 * 60), (3, 60 * 60), (4, 3 * 60 * 60)]:
            stats = self._monitor.window_stats(sec)
            self._stat_labels[(row, 1)].setText(f"{stats['ptp_A']:.3f}")
            self._stat_labels[(row, 2)].setText(f"{stats['std_A']:.3f}")
            self._stat_labels[(row, 3)].setText(f"{stats['drift_A_per_h']:+.3f}")
            self._stat_labels[(row, 4)].setText(str(stats["n"]))

    def _on_summary(self, stats: dict) -> None:
        self.log_message.emit(format_summary(stats))

    def _clear_history(self) -> None:
        self._monitor.clear()
        self._refresh_plot()
        self._refresh_stats()
        self.log_message.emit("Z stability history cleared")

    def _save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Z drift history", "z_drift.csv", "CSV (*.csv)"
        )
        if not path:
            return
        ts, zs = self._monitor.get_samples()
        if ts.size == 0:
            self.log_message.emit("Nothing to save — buffer is empty")
            return
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t_seconds_epoch", "z_angstrom"])
                for t, z in zip(ts, zs):
                    w.writerow([f"{t:.3f}", f"{z:.6f}"])
        except Exception as e:
            log.exception("CSV save failed")
            self.log_message.emit(f"CSV save failed: {e}")
            return
        self.log_message.emit(f"Saved {ts.size} samples → {path}")
