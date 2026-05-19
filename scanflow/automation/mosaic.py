"""Mosaic campaign data model.

A mosaic = one wide overview + a 3×3 grid of zoom tiles + one wide
overview at the end. Each zoom tile is acquired ``iterations_per_tile``
times with drift correction between iterations, so you keep the best
(or average) scan after the fact.

Grid layout (row-major, 1-indexed):

    1 2 3      ← top row (negative Y if Y points down)
    4 5 6
    7 8 9
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


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
    grid_n: int = 3  # 3 → 3×3 grid = 9 tiles

    # Shared tunneling -----------------------------------------------
    bias_V: float = 0.1
    setpoint_A: float = 50e-12
    settling_s: float = 5.0

    # Output ---------------------------------------------------------
    output_folder: str = ""
    name: str = "Mosaic"

    # Debug knob: when True, every tile scans at the *same* offset as
    # wide_before — no XY repositioning between tiles. Useful for
    # isolating whether the size-change works independently of the
    # offset-move logic on a real CreaTec rig.
    skip_tile_positioning: bool = False

    kind: str = "mosaic"

    def resolved_tile_size_nm(self) -> Tuple[float, float]:
        """Tile size with auto-fill when 0×0 was left in the config."""
        if self.tile_size_nm[0] > 0 and self.tile_size_nm[1] > 0:
            return self.tile_size_nm
        n = max(self.grid_n, 1)
        return (self.wide_size_nm[0] / n, self.wide_size_nm[1] / n)

    def total_tiles(self) -> int:
        return self.grid_n * self.grid_n


def tile_centers_in_wide_pixels(cfg: MosaicConfig):
    """Yield ``(tile_index_1based, cx_px, cy_px)`` for each tile.

    The pixel coordinates are in the wide-image frame. Row-major order:
    tile 1 is top-left, tile ``grid_n`` is top-right, tile ``grid_n+1`` is
    the start of the second row, etc.
    """
    n = cfg.grid_n
    if n < 1:
        return
    wpx, wpy = cfg.wide_pixels
    # Tile centers in pixels: split the wide image into n×n equal cells,
    # take the centre of each cell.
    idx = 1
    for row in range(n):
        for col in range(n):
            cx = (col + 0.5) * wpx / n
            cy = (row + 0.5) * wpy / n
            yield idx, float(cx), float(cy)
            idx += 1
