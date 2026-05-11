"""Feedback controller — bias, setpoint, preamp gain, feedback mode."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .stm_client import STMClient


class FeedbackController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # Bias
    # ------------------------------------------------------------------

    @property
    def bias_V(self) -> float:
        return float(self._c.getp("SCAN.BIASVOLTAGE.VOLT", ""))

    @bias_V.setter
    def bias_V(self, value: float) -> None:
        self._c.setp("SCAN.BIASVOLTAGE.VOLT", float(value))

    def ramp_bias_V(self, target_V: float, steps: int = 100, dwell_s: float = 0.01) -> None:
        """Gradually ramp bias from current value to target_V.

        Uses logarithmic spacing for same-polarity ramps to be gentle on the
        junction. Crosses zero linearly to avoid divergent log values.
        """
        steps = max(int(steps), 1)
        start = self.bias_V
        if start == target_V or steps == 1:
            self.bias_V = target_V
            return

        if start * target_V > 0:
            self._ramp_log(start, target_V, steps, dwell_s)
        elif start == 0 or target_V == 0:
            self._ramp_lin(start, target_V, steps, dwell_s)
        else:
            # Cross zero: log down to a small value of the start polarity,
            # flip, then log up to target.
            self._ramp_log(start, np.sign(start) * 0.001, steps // 2, dwell_s)
            self.bias_V = -np.sign(start) * 0.001
            self._ramp_log(np.sign(target_V) * 0.001, target_V, steps // 2, dwell_s)
        self.bias_V = target_V

    def _ramp_log(self, start: float, end: float, steps: int, dwell: float) -> None:
        sign = float(np.sign(start))
        log_start = np.log10(abs(start)) if start else -3.0
        log_end = np.log10(abs(end)) if end else -3.0
        for v in np.linspace(log_start, log_end, steps):
            self.bias_V = sign * float(10 ** v)
            time.sleep(dwell)

    def _ramp_lin(self, start: float, end: float, steps: int, dwell: float) -> None:
        for v in np.linspace(start, end, steps):
            self.bias_V = float(v)
            time.sleep(dwell)

    # ------------------------------------------------------------------
    # Setpoint (tunneling current)
    # ------------------------------------------------------------------

    @property
    def setpoint_A(self) -> float:
        return float(self._c.getp("SCAN.SETPOINT.AMPERE", ""))

    @setpoint_A.setter
    def setpoint_A(self, value: float) -> None:
        self._c.setp("SCAN.SETPOINT.AMPERE", float(value))

    @property
    def setpoint_pA(self) -> float:
        return self.setpoint_A * 1e12

    def ramp_setpoint_A(self, target_A: float, steps: int = 100, dwell_s: float = 0.01) -> None:
        """Logarithmic current ramp."""
        steps = max(int(steps), 1)
        start = max(self.setpoint_A, 1e-15)
        target = max(target_A, 1e-15)
        if start == target or steps == 1:
            self.setpoint_A = target
            return
        log_start = np.log10(start)
        log_end = np.log10(target)
        for v in np.linspace(log_start, log_end, steps):
            self.setpoint_A = float(10 ** v)
            time.sleep(dwell_s)
        self.setpoint_A = target

    # ------------------------------------------------------------------
    # Preamp
    # ------------------------------------------------------------------

    @property
    def preamp_exponent(self) -> int:
        return int(self._c.getp("SCAN.PREAMPGAIN.EXPONENT", "") or 9)

    @preamp_exponent.setter
    def preamp_exponent(self, value: int) -> None:
        self._c.setp("SCAN.PREAMPGAIN.EXPONENT", int(value))

    # ------------------------------------------------------------------
    # Feedback enable / mode
    # ------------------------------------------------------------------

    def feedback_on(self) -> None:
        self._c.setp("DSP.CHK.FBON", "ON")

    def feedback_off(self) -> None:
        self._c.setp("DSP.CHK.FBON", "OFF")

    def set_feedback_channel(self, channel: str = "CURRENT") -> None:
        """Set which channel drives feedback (e.g. 'CURRENT' or 'DF' for AFM)."""
        self._c.setp("DSP.FBCHANNEL", channel)

    def set_feedback_mode_log(self) -> None:
        self._c.setp("DSP.FBMODE", "Log(FB-Channel)")

    def set_feedback_mode_linear(self) -> None:
        self._c.setp("DSP.FBMODE", "Constant Lin(FB-Channel)")
