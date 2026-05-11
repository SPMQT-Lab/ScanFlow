"""Spectroscopy controller — I/V, dI/dV, and time-spectroscopy operations.

Vertical manipulation (VERTMAN) is CreaTec's term for point spectroscopy:
the tip is held at a position, the bias is swept, and selected channels
(current, lock-in X/Y, etc.) are recorded as a function of bias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient


@dataclass
class IVTable:
    """Voltage/Z table for vertical manipulation.

    The VERTMAN.IVTABLE format is a 6x8 matrix where rows correspond to
    segments and columns to (start, points, bias_start, bias_end, ...).
    For a simple symmetric bias sweep we use one segment.
    """
    bias_start_V: float = -0.7
    bias_end_V: float = 0.7
    points: int = 1024
    backward_sweep: bool = True

    def to_table(self) -> tuple[tuple[float, ...], ...]:
        """Build the 6x8 IV-table tuple expected by VERTMAN.IVTABLE."""
        row0 = (0.0, float(self.points), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        row1 = (float(self.bias_start_V), float(self.bias_end_V),
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        zero_row = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return (row0, row1, zero_row, zero_row, zero_row, zero_row)


class SpectroscopyController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(
        self,
        table: IVTable,
        channels: tuple[str, ...] = ("Current(filtered)", "Lock-in X", "Lock-in Y"),
        duration_s: float = 10.0,
        repeat_count: int = 1,
        average_count: int = 1,
        lat_speed_nm_s: float = 1.0,
        preamp_exponent: int = 9,
    ) -> None:
        c = self._c
        c.setp("VERTMAN.PREAMPGAIN.EXPONENT", int(preamp_exponent))
        c.setp("VERTMAN.IVTABLE", table.to_table())
        c.setp("VERTMAN.SPEC.BACK", 1 if table.backward_sweep else 0)
        c.setp("VERTMAN.CHANNELs", tuple(channels))
        c.setp("VERTMAN.LATSPEED.NM/SEC", float(lat_speed_nm_s))
        c.setp("VERTMAN.REPEATCOUNT", int(repeat_count))
        c.setp("VERTMAN.SPECAVRG.COUNT", int(average_count))
        c.setp("VERTMAN.SPECLENGTH.SEC", float(duration_s))

    # ------------------------------------------------------------------
    # Single-point spectroscopy
    # ------------------------------------------------------------------

    def single_at_pixel(self, x: int, y: int) -> None:
        """Record a single spectrum at the given pixel position in the current scan frame."""
        self._c.setp("VERTMAN.CMD.SINGLE_SPECTRUM", (int(x), int(y)))

    def multi_at_pixels(self, positions: list[tuple[int, int]]) -> None:
        """Record spectra at a list of (x, y) pixel positions sequentially."""
        flat = tuple((int(p[0]), int(p[1])) for p in positions)
        self._c.raw.btn_vertspec_mult(flat)

    def line_between(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Record evenly-spaced spectra along a line between two pixel positions."""
        self._c.raw.btn_vertspec_line(int(x1), int(y1), int(x2), int(y2))

    def grid(self) -> None:
        """Run the configured spectra-on-grid measurement."""
        self._c.raw.btn_spectraongrid()

    # ------------------------------------------------------------------
    # Time spectroscopy
    # ------------------------------------------------------------------

    def time_spec_start(self) -> None:
        self._c.raw.btn_timespec()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_vert(self, filepath: str) -> None:
        """Save the most recent vertical-manipulation spectrum to .VERT."""
        self._c.setp("STMAFM.FILE.SAVE.VERT", str(filepath))

    def last_saved_vert(self) -> Optional[str]:
        try:
            return str(self._c.raw.savevertfilename) or None
        except Exception:
            return None
