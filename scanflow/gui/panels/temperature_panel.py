"""Temperature monitor tab — live plot of all cryostat sensors.

Shows one pyqtgraph curve per active sensor, marks LN2 refill events
as vertical lines, and emits hourly ΔT summaries to the Log panel.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QGroupBox, QGridLayout, QFileDialog, QFrame,
)

from scanflow.core.temp_poller import (
    TemperaturePoller, SENSOR_LABELS, SENSOR_COLORS, SENSOR_FIELDS,
    format_summary,
)

log = logging.getLogger(__name__)


class TemperaturePanel(QWidget):
    """Live temperature plot + per-sensor readouts + refill event markers."""

    log_message = Signal(str)

    def __init__(self, poller: TemperaturePoller, parent=None) -> None:
        super().__init__(parent)
        self._poller = poller
        self._window_s: float | None = 3600.0   # default: 1 hour
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._refill_lines: list[pg.InfiniteLine] = []
        self._stat_labels: dict[tuple, QLabel] = {}
        self._build_ui()

        poller.sample_added.connect(self._on_sample)
        poller.summary.connect(self._on_summary)
        poller.refill_detected.connect(self._on_refill)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(5000)   # refresh value table every 5 s
        self._stats_timer.timeout.connect(self._refresh_values)
        self._stats_timer.start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("View window:"))
        self._window_combo = QComboBox()
        self._window_combo.addItems(["1 hour", "3 hours", "6 hours", "All history"])
        self._window_combo.currentIndexChanged.connect(self._on_window_change)
        ctrl.addWidget(self._window_combo)

        ctrl.addSpacing(20)
        ctrl.addWidget(QLabel("Refill threshold (K):"))
        self._refill_spin = QDoubleSpinBox()
        self._refill_spin.setRange(0.5, 20.0)
        self._refill_spin.setDecimals(1)
        self._refill_spin.setSingleStep(0.5)
        self._refill_spin.setValue(self._poller._refill_K)
        self._refill_spin.setToolTip(
            "Temperature drop in a single poll that triggers a REFILL event."
        )
        self._refill_spin.valueChanged.connect(
            lambda v: setattr(self._poller, "_refill_K", float(v))
        )
        ctrl.addWidget(self._refill_spin)

        ctrl.addStretch(1)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_history)
        ctrl.addWidget(clear_btn)

        save_btn = QPushButton("Save CSV…")
        save_btn.clicked.connect(self._save_csv)
        ctrl.addWidget(save_btn)

        root.addLayout(ctrl)

        # Live plot
        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.setLabel("left", "Temperature (K)")
        self._plot.setLabel("bottom", "Time (min)")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.addLegend(offset=(10, 10))

        # Pre-create one curve per sensor (hidden until data arrives)
        for field in SENSOR_FIELDS:
            color = SENSOR_COLORS.get(field, "#888888")
            label = SENSOR_LABELS.get(field, field)
            curve = self._plot.plot(
                [], [],
                pen=pg.mkPen(color, width=2),
                name=label,
            )
            curve.setVisible(False)
            self._curves[field] = curve

        root.addWidget(self._plot, 1)

        # Current values table
        val_group = QGroupBox("Current values")
        val_grid = QGridLayout(val_group)
        headers = ["Sensor", "Temperature (K)", "ΔT 1h (K)", "ΔT 3h (K)"]
        for c, h in enumerate(headers):
            lbl = QLabel(f"<b>{h}</b>")
            val_grid.addWidget(lbl, 0, c)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setFrameShadow(QFrame.Sunken)
            val_grid.addWidget(sep, 1, c)

        self._val_rows: dict[str, dict[str, QLabel]] = {}
        row = 2
        for field in SENSOR_FIELDS:
            label = SENSOR_LABELS.get(field, field)
            color = SENSOR_COLORS.get(field, "#888888")
            name_lbl = QLabel(f"<span style='color:{color}'><b>{label}</b></span>")
            t_lbl   = QLabel("—")
            d1h_lbl = QLabel("—")
            d3h_lbl = QLabel("—")
            t_lbl.setAlignment(Qt.AlignCenter)
            d1h_lbl.setAlignment(Qt.AlignCenter)
            d3h_lbl.setAlignment(Qt.AlignCenter)
            val_grid.addWidget(name_lbl, row, 0)
            val_grid.addWidget(t_lbl,   row, 1)
            val_grid.addWidget(d1h_lbl, row, 2)
            val_grid.addWidget(d3h_lbl, row, 3)
            self._val_rows[field] = {
                "t": t_lbl, "d1h": d1h_lbl, "d3h": d3h_lbl,
                "row": row,
            }
            row += 1

        root.addWidget(val_group)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_window_change(self, idx: int) -> None:
        self._window_s = {0: 3600.0, 1: 3 * 3600.0, 2: 6 * 3600.0, 3: None}[idx]
        self._refresh_plot()

    def _on_sample(self, t: float, reading) -> None:
        self._refresh_plot()
        self._refresh_values()

    def _on_summary(self, stats: dict) -> None:
        msg = format_summary(stats)
        self.log_message.emit(msg)
        self._refresh_values()

    def _on_refill(self, sensor: str, delta_K: float) -> None:
        label = SENSOR_LABELS.get(sensor, sensor)
        self.log_message.emit(
            f"⚠ LN2 REFILL detected on {label}: ΔT = {delta_K:.2f} K  "
            f"(scan paused automatically if running)"
        )
        # Mark refill on plot as a vertical line
        if self._poller._buffer:
            t_event = self._poller._buffer[-1][0]
            t0 = self._poller._buffer[0][0]
            t_min = (t_event - t0) / 60.0
            line = pg.InfiniteLine(
                pos=t_min, angle=90,
                pen=pg.mkPen("#e63946", width=2, style=Qt.DashLine),
                label=f"REFILL\n{label}",
                labelOpts={"color": "#e63946", "position": 0.05},
            )
            self._plot.addItem(line)
            self._refill_lines.append(line)

    # ------------------------------------------------------------------
    # Plot refresh
    # ------------------------------------------------------------------

    def _refresh_plot(self) -> None:
        active = self._poller.active_sensors()
        if not active:
            return

        # Compute t0 from first sample across all active sensors
        all_ts = []
        for field in active:
            ts, _ = self._poller.get_samples(field)
            if ts.size:
                all_ts.append(float(ts[0]))
        if not all_ts:
            return
        t0 = min(all_ts)

        for field in SENSOR_FIELDS:
            curve = self._curves[field]
            if field not in active:
                curve.setVisible(False)
                continue
            ts, ks = self._poller.get_samples(field)
            if ts.size == 0:
                curve.setVisible(False)
                continue
            t_min = (ts - t0) / 60.0
            if self._window_s:
                latest_min = float(t_min[-1])
                window_min = self._window_s / 60.0
                cutoff_min = latest_min - window_min
                mask = t_min >= cutoff_min
                curve.setData(t_min[mask], ks[mask])
                self._plot.setXRange(cutoff_min, latest_min, padding=0.02)
            else:
                curve.setData(t_min, ks)
            curve.setVisible(True)

    def _refresh_values(self) -> None:
        latest = self._poller.get_latest()
        stats1h = self._poller._compute_summary().get("1h", {})
        stats3h = self._poller._compute_summary().get("3h", {})
        for field, row_widgets in self._val_rows.items():
            val = getattr(latest, field, None) if latest else None
            row_widgets["t"].setText(f"{val:.3f}" if val is not None else "—")
            d1 = stats1h.get(field, {}).get("delta_K")
            d3 = stats3h.get(field, {}).get("delta_K")
            row_widgets["d1h"].setText(f"{d1:.3f}" if d1 is not None else "—")
            row_widgets["d3h"].setText(f"{d3:.3f}" if d3 is not None else "—")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_history(self) -> None:
        self._poller.clear()
        for line in self._refill_lines:
            self._plot.removeItem(line)
        self._refill_lines.clear()
        for curve in self._curves.values():
            curve.setData([], [])
            curve.setVisible(False)
        self.log_message.emit("Temperature history cleared")

    def _save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save temperature history", "temperature.csv", "CSV (*.csv)"
        )
        if not path:
            return
        active = self._poller.active_sensors()
        if not active:
            self.log_message.emit("Nothing to save — no temperature data")
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["t_epoch"] + [SENSOR_LABELS.get(s, s) for s in active])
                # Merge all sensor timelines
                all_ts: dict[float, dict[str, float]] = {}
                for field in active:
                    ts, ks = self._poller.get_samples(field)
                    for t, k in zip(ts.tolist(), ks.tolist()):
                        all_ts.setdefault(t, {})[field] = k
                for t in sorted(all_ts):
                    row = [f"{t:.3f}"] + [
                        f"{all_ts[t].get(s, '')}" for s in active
                    ]
                    w.writerow(row)
        except Exception as e:
            log.exception("Temperature CSV save failed")
            self.log_message.emit(f"CSV save failed: {e}")
            return
        self.log_message.emit(f"Temperature history saved → {path}")
