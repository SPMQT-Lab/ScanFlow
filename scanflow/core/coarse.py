"""Coarse-motion controller — approach, Z-limit, XYZ slider.

The coarse-approach machinery brings the tip down to the surface using
piezoelectric stepper pulses. Misuse can crash the tip into the sample,
so a Z-limit is wired in as a safety. Most operations here will be the
first thing a user does in a session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient


class SliderAxis(IntEnum):
    X_PLUS = 1
    X_MINUS = 2
    Y_PLUS = 3
    Y_MINUS = 4
    Z_PLUS = 5
    Z_MINUS = 6


@dataclass
class RampParams:
    """Coarse-pulse ramp parameters (piezo stepper waveform)."""
    pulse_height_V: float = 50.0
    pulse_duration_s: float = 0.003
    burst_count_xy: int = 1
    burst_count_z: int = 1
    burst_xy_on: bool = False
    burst_z_on: bool = False


@dataclass
class ApproachConfig:
    """Configuration for an automated coarse approach."""
    burst_count: int = 1            # coarse steps per approach cycle
    retry_count: int = 1            # retries per cycle
    period_s: float = 1.5           # duration of one approach cycle
    target_current_A: float = 1e-9  # tunnelling current target
    bias_V: float = 2.0             # approach bias


class CoarseController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # Ramp parameters
    # ------------------------------------------------------------------

    def set_ramp_params(self, params: RampParams) -> None:
        """Set the coarse-pulse ramp waveform. AutoUpdate is paused around
        the writes to keep the parameter set atomic."""
        c = self._c
        c.setp("UPDATE.AUTOUPDATE.ON", "OFF")
        c.setp("HVAMPCOARSE.PULSEHEIGHT.VOLT", float(params.pulse_height_V))
        c.setp("HVAMPCOARSE.PULSEDURATION.SEC", float(params.pulse_duration_s))
        c.setp("HVAMPCOARSE.BURSTCOUNT.XY", int(params.burst_count_xy))
        c.setp("HVAMPCOARSE.BURSTCOUNT.Z", int(params.burst_count_z))
        c.setp("HVAMPCOARSE.CHK.BURST.XY", "ON" if params.burst_xy_on else "OFF")
        c.setp("HVAMPCOARSE.CHK.BURST.Z", "ON" if params.burst_z_on else "OFF")
        c.setp("UPDATE.AUTOUPDATE.ON", "ON")

    # ------------------------------------------------------------------
    # Z-limit
    # ------------------------------------------------------------------

    def z_limit_on(self, retract_nm: Optional[float] = None) -> None:
        """Activate the Z-limit, placing the tip at a safe retracted height."""
        if retract_nm is not None:
            self._c.setp("SLIDER.ZLIMIT.RETRACT.NM", float(retract_nm))
        self._c.setp("SLIDER.ZLIMIT.ON", "ON")

    def z_limit_off(self) -> None:
        """Deactivate the Z-limit (tip can approach)."""
        self._c.setp("SLIDER.ZLIMIT.ON", "OFF")

    @property
    def z_limit_active(self) -> bool:
        return str(self._c.getp("SLIDER.ZLIMIT.ON", "")).upper() == "ON"

    # ------------------------------------------------------------------
    # Coarse approach
    # ------------------------------------------------------------------

    def configure_approach(self, cfg: ApproachConfig) -> None:
        c = self._c
        c.setp("SCAN.BIASVOLTAGE.VOLT", float(cfg.bias_V))
        c.setp("SCAN.SETPOINT.AMPERE", float(cfg.target_current_A))
        c.setp("HVAMPCOARSE.APPROACH.BURSTCOUNT", int(cfg.burst_count))
        c.setp("HVAMPCOARSE.APPROACH.RETRYCOUNT", int(cfg.retry_count))
        c.setp("HVAMPCOARSE.APPROACH.PERIOD.SEC", float(cfg.period_s))

    def start_approach(self) -> None:
        """Begin a coarse approach. Use wait_for_approach() to block."""
        self._c.setp("HVAMPCOARSE.APPROACH.START", "")

    def stop_approach(self) -> None:
        self._c.setp("HVAMPCOARSE.APPROACH.STOP", "")

    @property
    def approach_finished(self) -> bool:
        return int(self._c.getp("HVAMPCOARSE.APPROACH.FINISHED", "") or 0) == 1

    def wait_for_approach(self, poll_interval_s: float = 1.0,
                          timeout_s: Optional[float] = 600.0) -> bool:
        """Block until approach reports finished. Returns False on timeout."""
        start = time.time()
        while not self.approach_finished:
            if timeout_s is not None and (time.time() - start) > timeout_s:
                return False
            time.sleep(poll_interval_s)
        return True

    def approach(self, cfg: ApproachConfig, ramp: RampParams,
                 timeout_s: Optional[float] = 600.0) -> bool:
        """One-shot: configure ramp + approach, run it, wait, return success."""
        self.set_ramp_params(ramp)
        self.configure_approach(cfg)
        self.start_approach()
        return self.wait_for_approach(timeout_s=timeout_s)

    # ------------------------------------------------------------------
    # XYZ slider — coarse stepper motion across the sample
    # ------------------------------------------------------------------

    def slider_step(self, axis: SliderAxis, n_steps: int = 1) -> None:
        """Move the coarse slider by n_steps pulses along the given axis.

        Note: this issues `n_steps` pulses with the currently configured
        ramp parameters. Be conservative; one pulse can travel hundreds of nm.
        """
        # The XYZSLIDER command is (axis_code, x_pulses, y_pulses) — count
        # encoding depends on hardware. For safety we step one direction at a time.
        self._c.setp("HVAMPCOARSE.CMD.XYZSLIDER", (int(axis), int(n_steps), 0))

    def slider_burst(self, n: int) -> None:
        """Number of pulses per slider click — applies to subsequent slider_step()."""
        self._c.setp("XYBurst", int(n))
