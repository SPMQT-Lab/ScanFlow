"""Mosaic campaign data model.

A mosaic = one wide overview + a 3×3 grid of zoom tiles + one wide
overview at the end. Each zoom tile is acquired ``iterations_per_tile``
times (closed-loop positioning sets the tile centre once; iterations
are for repeat / averaging, not re-centring), so you keep the best
(or average) scan after the fact.

Grid layout (top row first, then middle, then bottom; columns L→R):

    1 2 3      ← top    row (Y = wide_centre.Y − wide_size_nm/3)  ← tiles 1..3
    4 5 6      ← middle row (Y = wide_centre.Y)
    7 8 9      ← bottom row (Y = wide_centre.Y + wide_size_nm/3)

The top row is scanned immediately after the wide overview while drift
is minimal. Top tiles sit right at the upper boundary of the wide field;
scanning them first keeps them inside that boundary before Y-drift
accumulates. Middle and bottom rows follow in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class MosaicConfig:
    """All knobs for one mosaic campaign."""

    # Wide overview (before + after) ----------------------------------
    wide_size_nm: Tuple[float, float] = (90.0, 90.0)
    wide_pixels: Tuple[int, int] = (256, 256)
    wide_speed_nm_s: float = 100.0

    # Per-tile zoom ---------------------------------------------------
    # Default (None) auto-fills tile_size_nm to wide_size_nm / grid_n
    # so the 9 tiles exactly tile the wide area. Override only if you
    # explicitly want overlap or gaps.
    tile_size_nm: Tuple[float, float] = (0.0, 0.0)
    tile_pixels: Tuple[int, int] = (256, 256)
    tile_speed_nm_s: float = 20.0
    iterations_per_tile: int = 3

    # Grid -----------------------------------------------------------
    grid_n: int = 3  # N → N×N grid of zoom tiles

    # Shared tunneling -----------------------------------------------
    bias_V: float = 0.1       # used for wide overviews and as default tile bias
    setpoint_A: float = 50e-12
    settling_s: float = 5.0

    # Bias sweep — one value per tile iteration.
    # Empty list → all iterations use bias_V.
    # Non-empty → overrides iterations_per_tile; len(bias_sweep) iterations
    # are run per tile, each at the corresponding bias.
    bias_sweep: List[float] = field(default_factory=list)

    # Output ---------------------------------------------------------
    output_folder: str = ""
    name: str = "Mosaic"

    kind: str = "mosaic"

    def resolved_tile_size_nm(self) -> Tuple[float, float]:
        """Tile size with auto-fill when 0×0 was left in the config."""
        if self.tile_size_nm[0] > 0 and self.tile_size_nm[1] > 0:
            return self.tile_size_nm
        n = max(self.grid_n, 1)
        return (self.wide_size_nm[0] / n, self.wide_size_nm[1] / n)

    def total_tiles(self) -> int:
        return self.grid_n * self.grid_n

    def effective_bias_sequence(self) -> List[float]:
        """Bias value for each tile iteration.

        Returns ``bias_sweep`` when set, otherwise ``[bias_V] × iterations_per_tile``.
        Unsafe values (|bias| < 5 mV) are left in the sequence — the runner
        logs a warning and skips that iteration.
        """
        if self.bias_sweep:
            return list(self.bias_sweep)
        return [self.bias_V] * max(1, self.iterations_per_tile)

    def effective_iterations(self) -> int:
        """Number of iterations per tile (respects bias_sweep override)."""
        return len(self.effective_bias_sequence())


def tile_centers_in_wide_pixels(cfg: MosaicConfig):
    """Yield ``(tile_index_1based, cx_px, cy_px)`` for each tile.

    The pixel coordinates are in the wide-image frame.

    Row order: **top row first**, then middle, then bottom — so for a
    3×3 grid the row sequence is [0, 1, 2]. For 5×5 it's [0, 1, 2, 3, 4].
    Within each row, columns go left → right.

    Rationale: top tiles sit at the upper boundary of the wide scan. Scanning
    them first — right after the wide overview — minimises the Y-drift that
    would otherwise push them outside the wide-image border.
    """
    n = cfg.grid_n
    if n < 1:
        return
    wpx, wpy = cfg.wide_pixels

    # Row order: top → bottom (0, 1, 2, … n-1).
    row_order = list(range(n))

    idx = 1
    for row in row_order:
        for col in range(n):
            cx = (col + 0.5) * wpx / n
            cy = (row + 0.5) * wpy / n
            yield idx, float(cx), float(cy)
            idx += 1
