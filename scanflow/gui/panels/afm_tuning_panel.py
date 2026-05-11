"""AFM / qPlus tuning wizard.

Three-step workflow:

    1. Broad frequency scan — locate the qPlus resonance somewhere in a wide
       sweep (e.g. 24500 ± 1500 Hz).
    2. Zoom-in scan around the detected peak — sharper readout for the fit.
    3. Apply the fit to the PLL, then enable amplitude control and tune the
       Δf / amplitude controller bandwidths.

The wizard runs the actual scans on a background thread so the GUI stays
responsive. Each step is gated by an "Apply / Next" button so the user
can verify the fit before committing.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QStackedWidget, QMessageBox,
)
from PySide6.QtCore import Signal, QThread

from scanflow.core import STMClient
from scanflow.core.afm import FrequencyScanParams


class _FreqScanWorker(QThread):
    """Runs one frequency scan on the AFM controller off the GUI thread."""

    finished_with = Signal(float)        # resonance frequency Hz, NaN on error
    error = Signal(str)

    def __init__(self, stm: STMClient, params: FrequencyScanParams,
                 enable_df_control: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._params = params
        self._enable_df = enable_df_control

    def run(self) -> None:
        try:
            afm = self._stm.afm
            if self._enable_df:
                afm.df_control_on()
                afm.amplitude_control_off()
            afm.configure_freqscan(self._params)
            afm.start_freqscan()
            afm.wait_freqscan()
            self.finished_with.emit(afm.resonance_Hz)
        except Exception as e:
            self.error.emit(str(e))
            self.finished_with.emit(float("nan"))


class AFMTuningPanel(QWidget):
    """Step-by-step qPlus / PLL tuning."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._worker: Optional[_FreqScanWorker] = None
        self._coarse_f0: Optional[float] = None
        self._fine_f0: Optional[float] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── PLL setup ──
        pll_box = QGroupBox("PLL drive")
        pg_ = QGridLayout(pll_box)

        pg_.addWidget(QLabel("Excitation (V)"), 0, 0)
        self._excitation_V = QDoubleSpinBox()
        self._excitation_V.setRange(0.0, 1.0)
        self._excitation_V.setDecimals(3)
        self._excitation_V.setSingleStep(0.005)
        self._excitation_V.setValue(0.05)
        pg_.addWidget(self._excitation_V, 0, 1)

        pg_.addWidget(QLabel("SRS gain"), 0, 2)
        self._srs_gain = QDoubleSpinBox()
        self._srs_gain.setRange(1.0, 1000.0)
        self._srs_gain.setValue(100.0)
        pg_.addWidget(self._srs_gain, 0, 3)

        self._btn_apply_drive = QPushButton("Apply drive")
        self._btn_apply_drive.clicked.connect(self._apply_drive)
        pg_.addWidget(self._btn_apply_drive, 0, 4)

        root.addWidget(pll_box)

        # ── Step 1: Broad scan ──
        broad_box = QGroupBox("Step 1 — Broad frequency scan")
        bg = QGridLayout(broad_box)
        bg.addWidget(QLabel("Centre (Hz)"), 0, 0)
        self._broad_center = QDoubleSpinBox()
        self._broad_center.setRange(100.0, 5_000_000.0)
        self._broad_center.setValue(24500.0)
        bg.addWidget(self._broad_center, 0, 1)
        bg.addWidget(QLabel("Span (Hz)"), 0, 2)
        self._broad_span = QDoubleSpinBox()
        self._broad_span.setRange(10.0, 100_000.0)
        self._broad_span.setValue(3000.0)
        bg.addWidget(self._broad_span, 0, 3)
        bg.addWidget(QLabel("Duration (s)"), 1, 0)
        self._broad_duration = QDoubleSpinBox()
        self._broad_duration.setRange(5.0, 600.0)
        self._broad_duration.setValue(60.0)
        bg.addWidget(self._broad_duration, 1, 1)
        bg.addWidget(QLabel("Points"), 1, 2)
        self._broad_points = QSpinBox()
        self._broad_points.setRange(64, 16384)
        self._broad_points.setValue(1000)
        bg.addWidget(self._broad_points, 1, 3)
        self._btn_run_broad = QPushButton("Run broad scan")
        self._btn_run_broad.clicked.connect(self._run_broad)
        bg.addWidget(self._btn_run_broad, 2, 0, 1, 2)
        self._broad_result = QLabel("Result: —")
        bg.addWidget(self._broad_result, 2, 2, 1, 2)
        root.addWidget(broad_box)

        # ── Step 2: Fine scan ──
        fine_box = QGroupBox("Step 2 — Zoom-in scan")
        fg = QGridLayout(fine_box)
        fg.addWidget(QLabel("Span (Hz)"), 0, 0)
        self._fine_span = QDoubleSpinBox()
        self._fine_span.setRange(1.0, 1000.0)
        self._fine_span.setValue(50.0)
        fg.addWidget(self._fine_span, 0, 1)
        fg.addWidget(QLabel("Duration (s)"), 0, 2)
        self._fine_duration = QDoubleSpinBox()
        self._fine_duration.setRange(5.0, 600.0)
        self._fine_duration.setValue(60.0)
        fg.addWidget(self._fine_duration, 0, 3)
        fg.addWidget(QLabel("Points"), 1, 0)
        self._fine_points = QSpinBox()
        self._fine_points.setRange(64, 16384)
        self._fine_points.setValue(1000)
        fg.addWidget(self._fine_points, 1, 1)
        self._btn_run_fine = QPushButton("Run zoom-in scan")
        self._btn_run_fine.clicked.connect(self._run_fine)
        self._btn_run_fine.setEnabled(False)
        fg.addWidget(self._btn_run_fine, 1, 2, 1, 2)
        self._fine_result = QLabel("Result: —")
        fg.addWidget(self._fine_result, 2, 0, 1, 4)
        root.addWidget(fine_box)

        # ── Step 3: Apply ──
        apply_box = QGroupBox("Step 3 — Apply to PLL")
        ag = QGridLayout(apply_box)
        ag.addWidget(QLabel("Amplitude setpoint (nm)"), 0, 0)
        self._amplitude_nm = QDoubleSpinBox()
        self._amplitude_nm.setRange(0.01, 5.0)
        self._amplitude_nm.setDecimals(3)
        self._amplitude_nm.setSingleStep(0.05)
        self._amplitude_nm.setValue(0.20)
        ag.addWidget(self._amplitude_nm, 0, 1)
        ag.addWidget(QLabel("Δf controller BW (Hz)"), 1, 0)
        self._df_bw = QDoubleSpinBox()
        self._df_bw.setRange(0.1, 1000.0)
        self._df_bw.setValue(12.0)
        ag.addWidget(self._df_bw, 1, 1)
        ag.addWidget(QLabel("Amplitude controller BW (Hz)"), 2, 0)
        self._amp_bw = QDoubleSpinBox()
        self._amp_bw.setRange(0.1, 1000.0)
        self._amp_bw.setValue(16.0)
        ag.addWidget(self._amp_bw, 2, 1)
        self._btn_apply = QPushButton("Apply fit and enable controllers")
        self._btn_apply.clicked.connect(self._apply_fit)
        self._btn_apply.setEnabled(False)
        ag.addWidget(self._btn_apply, 3, 0, 1, 2)
        root.addWidget(apply_box)

        root.addStretch(1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _require_connection(self) -> bool:
        if not self._stm.connected:
            QMessageBox.warning(self, "STM Not Connected",
                                "Connect to the STM before running a frequency scan.")
            return False
        return True

    def _apply_drive(self) -> None:
        if not self._require_connection():
            return
        try:
            self._stm.afm.set_excitation_V(self._excitation_V.value())
            self._stm.afm.set_srs_gain(self._srs_gain.value())
            self.log_message.emit(
                f"PLL drive: V_exc={self._excitation_V.value():.3f} V, "
                f"SRS gain={self._srs_gain.value():.0f}"
            )
        except Exception as e:
            QMessageBox.critical(self, "PLL drive error", str(e))

    def _params_broad(self) -> FrequencyScanParams:
        return FrequencyScanParams(
            center_Hz=self._broad_center.value(),
            span_Hz=self._broad_span.value(),
            duration_s=self._broad_duration.value(),
            points=self._broad_points.value(),
        )

    def _params_fine(self) -> FrequencyScanParams:
        if self._coarse_f0 is None:
            raise RuntimeError("Coarse scan must complete first")
        return FrequencyScanParams(
            center_Hz=self._coarse_f0,
            span_Hz=self._fine_span.value(),
            duration_s=self._fine_duration.value(),
            points=self._fine_points.value(),
        )

    def _run_broad(self) -> None:
        if not self._require_connection():
            return
        self._broad_result.setText("Result: scanning…")
        self._btn_run_broad.setEnabled(False)
        self._worker = _FreqScanWorker(self._stm, self._params_broad(),
                                       enable_df_control=True)
        self._worker.finished_with.connect(self._on_broad_done)
        self._worker.error.connect(lambda m: self.log_message.emit(f"Broad scan error: {m}"))
        self._worker.start()

    def _on_broad_done(self, fres: float) -> None:
        self._btn_run_broad.setEnabled(True)
        if fres != fres or fres <= 0:   # NaN or invalid
            self._broad_result.setText("Result: failed")
            return
        self._coarse_f0 = fres
        self._broad_result.setText(f"Result: f₀ ≈ {fres:.2f} Hz")
        self._btn_run_fine.setEnabled(True)
        self.log_message.emit(f"Coarse resonance: {fres:.2f} Hz")

    def _run_fine(self) -> None:
        if not self._require_connection():
            return
        try:
            params = self._params_fine()
        except RuntimeError as e:
            QMessageBox.warning(self, "Order required", str(e))
            return
        self._fine_result.setText("Result: scanning…")
        self._btn_run_fine.setEnabled(False)
        self._worker = _FreqScanWorker(self._stm, params, enable_df_control=False)
        self._worker.finished_with.connect(self._on_fine_done)
        self._worker.error.connect(lambda m: self.log_message.emit(f"Fine scan error: {m}"))
        self._worker.start()

    def _on_fine_done(self, fres: float) -> None:
        self._btn_run_fine.setEnabled(True)
        if fres != fres or fres <= 0:
            self._fine_result.setText("Result: failed")
            return
        self._fine_f0 = fres
        self._fine_result.setText(f"Result: f₀ ≈ {fres:.3f} Hz")
        self._btn_apply.setEnabled(True)
        self.log_message.emit(f"Fine resonance: {fres:.3f} Hz")

    def _apply_fit(self) -> None:
        if not self._require_connection():
            return
        try:
            afm = self._stm.afm
            afm.apply_freqscan_results()
            afm.set_amplitude_nm(self._amplitude_nm.value())
            afm.amplitude_control_on()
            afm.tune_df_bandwidth_Hz(self._df_bw.value())
            afm.tune_amplitude_bandwidth_Hz(self._amp_bw.value())
            self.log_message.emit(
                f"PLL applied: f₀={self._fine_f0:.3f} Hz, "
                f"A={self._amplitude_nm.value():.3f} nm, "
                f"Δf BW={self._df_bw.value():.1f} Hz, "
                f"Amp BW={self._amp_bw.value():.1f} Hz"
            )
            QMessageBox.information(self, "PLL applied",
                                    "Resonance applied and controllers enabled.")
        except Exception as e:
            QMessageBox.critical(self, "Apply error", str(e))
