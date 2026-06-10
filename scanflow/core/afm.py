"""AFM / PLL controller for qPlus-equipped systems.

The PLL drives the qPlus tuning fork on resonance and detects the frequency
shift (Δf) caused by tip-sample forces. This controller wraps frequency
scanning, PLL configuration, amplitude/frequency tuning, and switching the
main feedback loop between STM (current) and AFM (Δf) modes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient


@dataclass
class FrequencyScanParams:
    center_Hz: float = 24500.0
    span_Hz: float = 3000.0
    duration_s: float = 60.0
    points: int = 1000


class AFMController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # PLL configuration
    # ------------------------------------------------------------------

    def set_excitation_V(self, value: float) -> None:
        self._c.setp("AFM.PLL_EXCITATION.VOLT", float(value))

    def set_srs_gain(self, gain: float) -> None:
        self._c.setp("AFM.SRS_GAIN", float(gain))

    def set_amplitude_nm(self, value: float) -> None:
        self._c.setp("AFM.PLL_AMPLITUDE.NM", float(value))

    def df_control_on(self) -> None:
        self._c.setp("AFM.CHK.DF_CONTROL", "ON")

    def df_control_off(self) -> None:
        self._c.setp("AFM.CHK.DF_CONTROL", "OFF")

    def amplitude_control_on(self) -> None:
        self._c.setp("AFM.CHK.AMPLITUDE_CONTROL", "ON")

    def amplitude_control_off(self) -> None:
        self._c.setp("AFM.CHK.AMPLITUDE_CONTROL", "OFF")

    # ------------------------------------------------------------------
    # Frequency scan (resonance hunt)
    # ------------------------------------------------------------------

    def configure_freqscan(self, params: FrequencyScanParams) -> None:
        self._c.setp("AFM.FREQSCAN", (
            float(params.center_Hz),
            float(params.span_Hz),
            float(params.duration_s),
            int(params.points),
        ))

    def start_freqscan(self) -> None:
        self._c.setp("AFM.BTN.FREQSCAN", "")

    def wait_freqscan(self, poll_s: float = 1.0,
                      timeout_s: Optional[float] = None) -> bool:
        """Block until the scan reports finished."""
        start = time.time()
        # Brief settle before polling
        time.sleep(2.0)
        while int(self._c.getp("STMAFM.SCANSTATUS", "") or 0) != 0:
            if timeout_s is not None and (time.time() - start) > timeout_s:
                return False
            time.sleep(poll_s)
        return True

    @property
    def resonance_Hz(self) -> float:
        return float(self._c.getp("AFM.RESULTS.FCENTER.HZ", ""))

    def apply_freqscan_results(self) -> None:
        """Apply the fitted resonance from the latest frequency scan to the PLL."""
        self._c.setp("AFM.BTN.FREQSCAN.RESULTS.APPLY", "")

    def find_resonance(self,
                       coarse: FrequencyScanParams,
                       fine_span_Hz: float = 50.0,
                       fine_duration_s: float = 60.0,
                       fine_points: int = 1000) -> float:
        """Run a coarse scan, then zoom in around the detected centre.

        Returns the final resonance frequency in Hz.
        """
        self.df_control_on()
        self.amplitude_control_off()
        self.configure_freqscan(coarse)
        self.start_freqscan()
        self.wait_freqscan()

        f0 = self.resonance_Hz
        fine = FrequencyScanParams(
            center_Hz=f0, span_Hz=fine_span_Hz,
            duration_s=fine_duration_s, points=fine_points,
        )
        self.configure_freqscan(fine)
        self.start_freqscan()
        self.wait_freqscan()
        self.apply_freqscan_results()
        return self.resonance_Hz

    # ------------------------------------------------------------------
    # Controller tuning
    # ------------------------------------------------------------------

    def tune_df_bandwidth_Hz(self, bandwidth_Hz: float) -> None:
        self._c.setp("AFM.TUNE.DF.BW.HZ", float(bandwidth_Hz))
        self._c.setp("AFM.TUNE.DF", "ON")

    def tune_amplitude_bandwidth_Hz(self, bandwidth_Hz: float) -> None:
        self._c.setp("AFM.TUNE.AMPLITUDE.BW.HZ", float(bandwidth_Hz))
        self._c.setp("AFM.TUNE.AMPLITUDE", "ON")

    # ------------------------------------------------------------------
    # Setpoint (in AFM mode)
    # ------------------------------------------------------------------

    def set_setpoint_Hz(self, value: float) -> None:
        self._c.setp("SCAN.SETPOINT.HZ", float(value))

    def ramp_setpoint_Hz(self, target_Hz: float, step_Hz: float = 1.0,
                        dwell_s: float = 2.0) -> None:
        """Slowly ramp the AFM setpoint to avoid sudden tip-sample force changes."""
        current = float(self._c.getp("SCAN.SETPOINT.HZ", "") or 0.0)
        direction = -1.0 if target_Hz < current else 1.0
        v = current
        while (direction == 1.0 and v < target_Hz) or (direction == -1.0 and v > target_Hz):
            v += direction * step_Hz
            if (direction == 1.0 and v > target_Hz) or (direction == -1.0 and v < target_Hz):
                v = target_Hz
            self.set_setpoint_Hz(v)
            time.sleep(dwell_s)
