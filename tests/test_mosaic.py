"""Tests for the mosaic config + grid centre calculation."""

from __future__ import annotations

from scanflow.automation import MosaicConfig, tile_centers_in_wide_pixels


def test_default_grid_has_9_tiles():
    cfg = MosaicConfig()
    assert cfg.total_tiles() == 9


def test_resolved_tile_size_auto_fills_when_zero():
    cfg = MosaicConfig(wide_size_nm=(90.0, 90.0), tile_size_nm=(0.0, 0.0))
    tx, ty = cfg.resolved_tile_size_nm()
    assert tx == 30.0
    assert ty == 30.0


def test_resolved_tile_size_keeps_user_override():
    cfg = MosaicConfig(wide_size_nm=(90.0, 90.0), tile_size_nm=(20.0, 25.0))
    assert cfg.resolved_tile_size_nm() == (20.0, 25.0)


def test_tile_centers_cover_wide_pixels():
    cfg = MosaicConfig(wide_size_nm=(90.0, 90.0), wide_pixels=(300, 300), grid_n=3)
    centres = list(tile_centers_in_wide_pixels(cfg))
    assert len(centres) == 9
    # Middle-first row order: tile 1 is middle-LEFT (same Y as wide centre).
    idx1, cx1, cy1 = centres[0]
    assert idx1 == 1
    assert cx1 == 50.0   # 300/3 = 100 wide → centre 50 (left column)
    assert cy1 == 150.0  # middle row → centre Y
    # Tile 2 is middle-centre (the wide centre itself in pixel terms)
    idx2, cx2, cy2 = centres[1]
    assert (cx2, cy2) == (150.0, 150.0)
    # Tile 3 is middle-right
    assert (centres[2][1], centres[2][2]) == (250.0, 150.0)
    # Tile 4 is top-left (next row out)
    assert (centres[3][1], centres[3][2]) == (50.0, 50.0)
    # Tile 9 is bottom-right
    idx9, cx9, cy9 = centres[-1]
    assert idx9 == 9
    assert cx9 == 250.0
    assert cy9 == 250.0


def test_tile_pixel_centres_are_evenly_spaced():
    cfg = MosaicConfig(wide_pixels=(300, 300), grid_n=3)
    centres = list(tile_centers_in_wide_pixels(cfg))
    # Each row's X coords are still [left, centre, right] — only the row
    # *order* changed (middle first).
    middle_row = [c[1] for c in centres[0:3]]
    top_row    = [c[1] for c in centres[3:6]]
    bottom_row = [c[1] for c in centres[6:9]]
    assert middle_row == top_row == bottom_row == [50.0, 150.0, 250.0]
    # Y of middle row is centre, top row is min, bottom row is max
    assert centres[0][2] == 150.0   # middle
    assert centres[3][2] == 50.0    # top
    assert centres[6][2] == 250.0   # bottom


def test_estimate_duration_scales_with_tiles_and_iterations():
    """More iterations or a finer grid should mean a longer estimate."""
    from scanflow.automation import MosaicStep
    cfg = MosaicConfig(iterations_per_tile=3, grid_n=3)
    longer = MosaicConfig(iterations_per_tile=5, grid_n=3)
    assert MosaicStep(config=longer).estimate_duration_s() > \
           MosaicStep(config=cfg).estimate_duration_s()
