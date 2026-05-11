"""Lateral atom manipulation controller.

Lateral manipulation is the headline use case of low-temperature STM: the
tip is moved across the surface at a controlled bias and tunneling current
strong enough to drag an individual adsorbate from one site to another.

The CreaTec API exposes this through three commands on ``stmafmrem``:

    latmanip(x1, y1, x2, y2)           # one drag
    latmanipxymove(xs, ys, xe, ye, ...) # parametric variant
    latsave()                          # write the recorded trace to disk

Manipulation parameters (bias, setpoint, lateral speed, feedback toggle)
live under the ``LATMAN.*`` key namespace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient


@dataclass
class LateralParams:
    """Tip-drag parameters in SI units."""
    bias_V: float = 0.010        # 10 mV — typical for atom dragging
    setpoint_A: float = 50e-9    # 50 nA — much higher than imaging current
    speed_nm_s: float = 0.5      # slow — atoms drag, don't fly
    feedback_on: bool = True     # keep Z feedback during the drag


class LateralController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # Parameter configuration
    # ------------------------------------------------------------------

    def apply(self, params: LateralParams) -> None:
        """Push a complete LateralParams set to the instrument."""
        c = self._c
        c.setp("LATMAN.BIASVOLTAGE.VOLT", float(params.bias_V))
        c.setp("LATMAN.SETPOINT.AMPERE", float(params.setpoint_A))
        c.setp("LATMAN.LATSPEED.NM/SEC", float(params.speed_nm_s))
        c.setp("LATMAN.CHK.FB", "ON" if params.feedback_on else "OFF")

    def set_bias_V(self, value: float) -> None:
        self._c.setp("LATMAN.BIASVOLTAGE.VOLT", float(value))

    def set_setpoint_A(self, value: float) -> None:
        self._c.setp("LATMAN.SETPOINT.AMPERE", float(value))

    def set_speed_nm_s(self, value: float) -> None:
        self._c.setp("LATMAN.LATSPEED.NM/SEC", float(value))

    def feedback_on(self) -> None:
        self._c.setp("LATMAN.CHK.FB", "ON")

    def feedback_off(self) -> None:
        self._c.setp("LATMAN.CHK.FB", "OFF")

    # ------------------------------------------------------------------
    # Drag operations
    # ------------------------------------------------------------------

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Drag the tip from pixel (x1, y1) to (x2, y2) in the current image."""
        self._c.raw.latmanip(int(x1), int(y1), int(x2), int(y2))

    def drag_xy(self, xs: float, ys: float, xe: float, ye: float) -> None:
        """Parametric drag using raw XY values (nm or volts depending on mode)."""
        self._c.raw.latmanipxymove(float(xs), float(ys), float(xe), float(ye))

    def save_trace(self) -> None:
        """Write the most recent manipulation trace to disk."""
        self._c.raw.latsave()
