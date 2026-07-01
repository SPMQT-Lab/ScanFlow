"""Tip-forming controller — voltage-pulse tip conditioning at a pixel position.

Known Createc quirk (motivates the pre-flight assessment below): when
``TIP-FORM.CMD.START`` is issued, the tip travels to the target pixel at
``TIP-FORM.LATSPEED.NM/SEC`` — NOT at the scan speed. If the lateral
speed is much higher than the scan speed the move is a violent jump; if
it is much lower, the tip crawls for a long time and **STMAFM cannot be
interrupted once the command is given**. Always run
:func:`assess_tip_form_motion` (the GUI arming dialog and the runner do)
and show the warnings to the operator before arming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
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


# Lateral-speed sanity thresholds for the pre-flight assessment.
# Both relative (vs the scan speed the operator is used to watching) and
# absolute (worst-case uninterruptible travel time).
FAST_SPEED_RATIO = 10.0     # lateral > 10× scan speed → violent jump
SLOW_TRAVEL_S = 60.0        # worst-case travel longer than this → crawl


@dataclass
class TipFormMotionAssessment:
    """Pre-flight result for the travel to the tip-form target."""

    lateral_speed_nm_s: float
    scan_speed_nm_s: float
    speed_ratio: float               # lateral / scan speed
    worst_travel_nm: float           # frame diagonal — position is unknown
    worst_travel_s: float
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


def assess_tip_form_motion(
    lateral_speed_nm_s: float,
    *,
    scan_speed_nm_s: float,
    frame_size_nm: tuple[float, float],
) -> TipFormMotionAssessment:
    """Sanity-check the tip's travel to a tip-form target.

    The current tip position inside the frame is generally unknown when
    the command is armed, so the travel distance is taken as the frame
    diagonal (worst case). Warnings, not errors: the operator may have
    good reasons — but they must see them before arming, because the
    move cannot be aborted once ``TIP-FORM.CMD.START`` is sent.
    """
    lateral = max(float(lateral_speed_nm_s), 1e-9)
    scan_speed = max(float(scan_speed_nm_s), 1e-9)
    ratio = lateral / scan_speed
    diagonal = hypot(float(frame_size_nm[0]), float(frame_size_nm[1]))
    travel_s = diagonal / lateral

    warnings: list[str] = []
    if ratio > FAST_SPEED_RATIO:
        warnings.append(
            f"tip-form lateral speed {lateral:.1f} nm/s is {ratio:.0f}× the "
            f"scan speed ({scan_speed:.1f} nm/s) — the move to the target "
            "will be a fast jump"
        )
    if travel_s > SLOW_TRAVEL_S:
        warnings.append(
            f"worst-case travel to the target is {travel_s:.0f} s "
            f"({diagonal:.0f} nm at {lateral:.1f} nm/s) — STMAFM cannot be "
            "interrupted once the tip-form command is issued; consider "
            "matching the lateral speed to the scan speed"
        )
    return TipFormMotionAssessment(
        lateral_speed_nm_s=lateral,
        scan_speed_nm_s=scan_speed,
        speed_ratio=ratio,
        worst_travel_nm=diagonal,
        worst_travel_s=travel_s,
        warnings=warnings,
    )


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
