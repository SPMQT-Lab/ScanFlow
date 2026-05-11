"""Drift monitor panel: live display of measured drift per scan."""

from __future__ import annotations

from collections import deque

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QGridLayout
)
from PySide6.QtCore import Qt

try:
    import pyqtgraph as pg
    _HAS_PYQTGRAPH = True
except ImportError:
    _HAS_PYQTGRAPH = False

from scanflow.drift import DriftResult


class DriftPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._history: deque[DriftResult] = deque(maxlen=200)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # -- Live readout --
        info_group = QGroupBox("Last measurement")
        ig = QGridLayout(info_group)

        self._dx_label = QLabel("dx: —")
        self._dy_label = QLabel("dy: —")
        self._mag_label = QLabel("Magnitude: —")
        self._rate_label = QLabel("Rate: —")
        self._conf_label = QLabel("Confidence: —")

        for row, lbl in enumerate([self._dx_label, self._dy_label,
                                    self._mag_label, self._rate_label,
                                    self._conf_label]):
            ig.addWidget(lbl, row, 0)

        layout.addWidget(info_group)

        # -- Chart --
        if _HAS_PYQTGRAPH:
            chart_group = QGroupBox("Drift history (Å)")
            cg = QVBoxLayout(chart_group)
            self._plot = pg.PlotWidget()
            self._plot.setLabel("left", "Drift (Å)")
            self._plot.setLabel("bottom", "Scan #")
            self._plot.addLegend()
            self._dx_curve = self._plot.plot(pen="c", name="dx")
            self._dy_curve = self._plot.plot(pen="m", name="dy")
            self._mag_curve = self._plot.plot(pen="y", name="|drift|")
            cg.addWidget(self._plot)
            layout.addWidget(chart_group)
        else:
            layout.addWidget(QLabel("Install pyqtgraph for drift charts."))

    def update_drift(self, result: DriftResult) -> None:
        self._history.append(result)

        self._dx_label.setText(f"dx: {result.dx_angstrom:.3f} Å")
        self._dy_label.setText(f"dy: {result.dy_angstrom:.3f} Å")
        self._mag_label.setText(f"Magnitude: {result.magnitude_angstrom:.3f} Å")
        rate_txt = f"{result.rate_angstrom_per_s:.3f} Å/s" if result.rate_angstrom_per_s else "—"
        self._rate_label.setText(f"Rate: {rate_txt}")
        self._conf_label.setText(f"Confidence: {result.confidence:.2f}")

        if _HAS_PYQTGRAPH and self._history:
            xs = list(range(len(self._history)))
            self._dx_curve.setData(xs, [r.dx_angstrom for r in self._history])
            self._dy_curve.setData(xs, [r.dy_angstrom for r in self._history])
            self._mag_curve.setData(xs, [r.magnitude_angstrom for r in self._history])
