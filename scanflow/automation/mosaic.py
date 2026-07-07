"""Mosaic campaign data model.

A mosaic = one wide overview + an N×N grid of zoom tiles + one wide
overview at the end. Each zoom tile is acquired ``iterations_per_tile``
times (closed-loop positioning sets the tile centre once; iterations
are for repeat / averaging, not re-centring), so you keep the best
(or average) scan after the fact.

Grid layout (top row first, then middle, then bottom; columns L→R),
expressed in the Createc offset convention (X = frame centre, Y = frame
TOP EDGE, increasing downward):

    1 2 3      ← top    row (tile top edge at wide top edge + 0·tile_h)
    4 5 6      ← middle row (tile top edge at wide top edge + 1·tile_h)
    7 8 9      ← bottom row (tile top edge at wide top edge + 2·tile_h)

The top row is scanned immediately after the wide overview while drift
is minimal. Top tiles sit right at the upper boundary of the wide field;
scanning them first keeps them inside that boundary before Y-drift
accumulates. Middle and bottom rows follow in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

from scanflow.core.scan_geometry import clamp_frame_to_wide_bounds


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

    # Bias sweep — the voltage(s) applied WITHIN each tile iteration.
    # Empty list → each iteration uses the single bias_V.
    # Non-empty → each iteration is scanned once per value in the list, so a
    # tile runs iterations_per_tile × len(bias_sweep) images in total
    # (e.g. iterations_per_tile=3, bias_sweep=[-0.5, 0.5] → 6 images/tile).
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
        """Flat list of bias values to scan per tile, in acquisition order.

        With a sweep, each iteration runs the whole ``bias_sweep`` list, so
        the sequence is ``bias_sweep`` repeated ``iterations_per_tile`` times
        (multiple voltages per iteration). Without a sweep it is
        ``[bias_V] × iterations_per_tile``. Unsafe values (|bias| < 5 mV) are
        left in — the runner logs a warning and skips that image.
        """
        reps = max(1, self.iterations_per_tile)
        if self.bias_sweep:
            return list(self.bias_sweep) * reps
        return [self.bias_V] * reps

    def biases_per_iteration(self) -> int:
        """How many voltages are scanned within a single tile iteration."""
        return len(self.bias_sweep) if self.bias_sweep else 1

    def effective_iterations(self) -> int:
        """Total images scanned per tile (iterations × voltages-per-iteration)."""
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


def tile_targets_nm(
    cfg: MosaicConfig,
    home_x_nm: float,
    home_y_nm: float,
) -> Iterator[Tuple[int, float, float, bool]]:
    """Yield ``(tile_idx, target_x_nm, target_y_nm, clamped)`` per tile.

    ``home_x_nm`` / ``home_y_nm`` is the wide frame's SCAN.OFFSET readback
    (X = frame centre, Y = frame TOP EDGE — see scan_geometry). Targets
    follow the same convention: write them to SCAN.OFFSET.{X,Y}.NM.

    Each target is clamped through
    :func:`scanflow.core.scan_geometry.clamp_frame_to_wide_bounds` so the
    tile frame stays inside the wide field on both axes. ``clamped`` is
    True when the requested target had to be moved — for an exactly
    tiling grid (auto tile size) it is always False; if it fires, the
    config requests tiles outside the wide frame and the caller should
    warn loudly.
    """
    tile_w, tile_h = cfg.resolved_tile_size_nm()
    wpx, wpy = cfg.wide_pixels
    nm_per_px_x = cfg.wide_size_nm[0] / max(wpx, 1)
    nm_per_px_y = cfg.wide_size_nm[1] / max(wpy, 1)

    for idx, cx_px, cy_px in tile_centers_in_wide_pixels(cfg):
        # X: centre convention — straight pixel-from-centre conversion.
        dx_nm = (cx_px - wpx / 2.0) * nm_per_px_x
        # Y: top-edge convention — the tile's top edge sits half a TILE
        # height above the tile centre. Subtract tile_h/2 in nm, NOT half
        # a grid cell in pixels: the two only coincide for the auto tile
        # size (tile_h == wide_h/grid_n); with an overridden tile_size_nm
        # (overlap/gaps) the grid-cell version shifted every tile in Y by
        # (tile_h − wide_h/grid_n)/2. For the auto size this still reduces
        # to dy = row · tile_h (0 for the top row).
        dy_nm = cy_px * nm_per_px_y - tile_h / 2.0
        target_x = home_x_nm + dx_nm
        target_y = home_y_nm + dy_nm
        clamped_x, clamped_y = clamp_frame_to_wide_bounds(
            target_x, target_y,
            home_x_nm=home_x_nm,
            home_y_nm=home_y_nm,
            scan_range_x_nm=cfg.wide_size_nm[0],
            scan_range_y_nm=cfg.wide_size_nm[1],
            frame_width_nm=tile_w,
            frame_height_nm=tile_h,
        )
        # Tolerance guards against float noise at exact-tiling boundaries.
        was_clamped = (abs(clamped_x - target_x) > 1e-9
                       or abs(clamped_y - target_y) > 1e-9)
        yield idx, float(clamped_x), float(clamped_y), was_clamped
