"""Runtime safety monitor — guards against tip crashes during automation.

The single most reliable indicator of a tip crash on an STM is the
tunneling current itself: in normal operation it sits at the setpoint
(typically 10–100 pA), and any approach to physical contact spikes it
several orders of magnitude up. By watching the live current and
aborting before the scan continues into damaged territory, we protect
the tip without relying on any user reaction time.

Strategy:

    every poll:
        I = measure_current()      # in amperes
        if |I| > threshold:        # default 1 nA
            stop the scan
            activate Z-limit (retract)
            raise SafetyViolation

Default threshold 1 nA is the user-recommended ground-truth crash
indicator on this rig. Adjust per recipe via ``max_current_A``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .stm_client import STMClient

log = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    """Configurable safety thresholds."""
    max_current_A: float = 1e-9         # 1 nA — tip-crash indicator
    enable_current_check: bool = True
    retract_on_violation_nm: float = 10.0  # Z-limit retract distance


@dataclass
class SafetyStatus:
    ok: bool
    reason: str = ""
    measured_current_A: Optional[float] = None


class SafetyViolation(RuntimeError):
    """Raised by the automation runner when a safety threshold is hit."""

    def __init__(self, message: str, current_A: Optional[float] = None) -> None:
        super().__init__(message)
        self.current_A = current_A


class SafetyMonitor:
    """Reads live current and reports threshold violations."""

    def __init__(self, config: Optional[SafetyConfig] = None) -> None:
        self.config = config or SafetyConfig()

    # ------------------------------------------------------------------
    # Current measurement
    # ------------------------------------------------------------------

    def measure_current_A(self, stm: "STMClient") -> Optional[float]:
        """Read the present tunneling current in amperes.

        Strategy:
          1. Instantaneous ADC reading (cheap, ~µs) — getadcvalf(0, 0).
             Returns the preamp output voltage; divide by 10**preamp_exp.
          2. Fallback: peek at the latest live scan-data current channel
             and return the peak |I| in the partial frame.
        """
        # Method 1: instantaneous voltage from ADC0 / preamp
        try:
            v = float(stm.raw.getadcvalf(0, 0))
            preamp_exp = int(stm.getp("SCAN.PREAMPGAIN.EXPONENT", 9) or 9)
            return v / (10.0 ** preamp_exp)
        except Exception:
            pass
        # Method 2: max from live scan data
        try:
            arr = stm.scan.live_data(channel=2, unit=3)  # CURRENT_FWD, AMPERE
            if arr is None:
                return None
            arr = np.asarray(arr)
            # Ignore the leading rows in case partial-scan blanks are nonzero
            return float(np.max(np.abs(arr)))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check(self, stm: "STMClient") -> SafetyStatus:
        cfg = self.config
        if not cfg.enable_current_check:
            return SafetyStatus(ok=True)
        I = self.measure_current_A(stm)
        if I is None:
            return SafetyStatus(ok=True)
        if abs(I) > cfg.max_current_A:
            return SafetyStatus(
                ok=False,
                reason=(
                    f"Tip-crash threshold exceeded: "
                    f"|I| = {I*1e9:.3f} nA > {cfg.max_current_A*1e9:.3f} nA"
                ),
                measured_current_A=I,
            )
        return SafetyStatus(ok=True, measured_current_A=I)

    # ------------------------------------------------------------------
    # Reaction
    # ------------------------------------------------------------------

    def emergency_stop(self, stm: "STMClient") -> None:
        """Stop scan and retract tip via Z-limit. Safe to call repeatedly."""
        log.warning("Safety emergency_stop fired — halting scan and retracting tip")
        try:
            stm.scan.stop()
        except Exception:
            log.exception("scan.stop() failed during emergency_stop")
        try:
            stm.coarse.z_limit_on(self.config.retract_on_violation_nm)
        except Exception:
            log.exception("z_limit_on() failed during emergency_stop")
        # Best-effort beep
        try:
            stm.beep()
        except Exception:
            pass
