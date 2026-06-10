"""Tip-forming controller — voltage-pulse tip conditioning at a pixel position."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stm_client import STMClient


@dataclass
class TipFormParams:
    voltage_V: float = 0.1
    z_approach_nm: float = 1.2
    pulse_length_s: float = 0.4
    z_offset_nm: float = 0.0
    lateral_speed_nm_s: float = 10.0


class TipFormController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    def configure(self, params: TipFormParams) -> None:
        c = self._c
        c.setp("TIP-FORM.VOLTAGE.VOLT", float(params.voltage_V))
        c.setp("TIP-FORM.Z_APPROACH.NM", float(params.z_approach_nm))
        c.setp("TIP-FORM.PULSELENGTH.SEC", float(params.pulse_length_s))
        c.setp("TIP-FORM.Z_OFFSET.NM", float(params.z_offset_nm))
        c.setp("TIP-FORM.LATSPEED.NM/SEC", float(params.lateral_speed_nm_s))

    def execute_at_pixel(self, x: int, y: int) -> None:
        """Run a tip-forming pulse at the given pixel of the current scan."""
        self._c.setp("TIP-FORM.CMD.START", (int(x), int(y)))

    def form_at(self, x: int, y: int, params: TipFormParams) -> None:
        """Configure + execute tip forming in one call."""
        self.configure(params)
        self.execute_at_pixel(x, y)
