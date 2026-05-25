"""Tests for the mosaic config + grid centre calculation."""

from __future__ import annotations

import pytest

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
    # Top-first row order: tile 1 is top-LEFT (smallest Y).
    idx1, cx1, cy1 = centres[0]
    assert idx1 == 1
    assert cx1 == 50.0   # 300/3 = 100 wide → centre 50 (left column)
    assert cy1 == 50.0   # top row → minimum Y
    # Tile 2 is top-centre
    idx2, cx2, cy2 = centres[1]
    assert (cx2, cy2) == (150.0, 50.0)
    # Tile 3 is top-right
    assert (centres[2][1], centres[2][2]) == (250.0, 50.0)
    # Tile 4 is middle-left
    assert (centres[3][1], centres[3][2]) == (50.0, 150.0)
    # Tile 9 is bottom-right
    idx9, cx9, cy9 = centres[-1]
    assert idx9 == 9
    assert cx9 == 250.0
    assert cy9 == 250.0


def test_tile_pixel_centres_are_evenly_spaced():
    cfg = MosaicConfig(wide_pixels=(300, 300), grid_n=3)
    centres = list(tile_centers_in_wide_pixels(cfg))
    # Each row's X coords are [left, centre, right] regardless of row order.
    top_row    = [c[1] for c in centres[0:3]]
    middle_row = [c[1] for c in centres[3:6]]
    bottom_row = [c[1] for c in centres[6:9]]
    assert top_row == middle_row == bottom_row == [50.0, 150.0, 250.0]
    # Y of top row is min, middle is centre, bottom is max
    assert centres[0][2] == 50.0    # top
    assert centres[3][2] == 150.0   # middle
    assert centres[6][2] == 250.0   # bottom


def test_estimate_duration_scales_with_tiles_and_iterations():
    """More iterations or a finer grid should mean a longer estimate."""
    from scanflow.automation import MosaicStep
    cfg = MosaicConfig(iterations_per_tile=3, grid_n=3)
    longer = MosaicConfig(iterations_per_tile=5, grid_n=3)
    assert MosaicStep(config=longer).estimate_duration_s() > \
           MosaicStep(config=cfg).estimate_duration_s()


# ---------------------------------------------------------------------------
# Grid N tests
# ---------------------------------------------------------------------------

def test_4x4_grid_has_16_tiles():
    cfg = MosaicConfig(grid_n=4)
    assert cfg.total_tiles() == 16


def test_4x4_auto_tile_size():
    cfg = MosaicConfig(wide_size_nm=(120.0, 120.0), tile_size_nm=(0.0, 0.0), grid_n=4)
    assert cfg.resolved_tile_size_nm() == (30.0, 30.0)


def test_4x4_tile_centers_count():
    cfg = MosaicConfig(wide_size_nm=(120.0, 120.0), wide_pixels=(256, 256), grid_n=4)
    centres = list(tile_centers_in_wide_pixels(cfg))
    assert len(centres) == 16


def test_4x4_tile_centers_top_row_first():
    """Top row (smallest cy) must be tiles 1–4."""
    cfg = MosaicConfig(wide_pixels=(400, 400), grid_n=4)
    centres = list(tile_centers_in_wide_pixels(cfg))
    # Top row: cy = (0 + 0.5) * 400 / 4 = 50.0
    for idx in range(4):
        assert centres[idx][2] == pytest.approx(50.0), \
            f"tile {idx + 1} should be in top row (cy=50), got cy={centres[idx][2]}"


def test_tile_centers_1x1():
    """1×1 grid yields a single tile at the wide-image centre."""
    cfg = MosaicConfig(wide_pixels=(256, 256), grid_n=1)
    centres = list(tile_centers_in_wide_pixels(cfg))
    assert len(centres) == 1
    _, cx, cy = centres[0]
    assert cx == pytest.approx(128.0)
    assert cy == pytest.approx(128.0)


# ---------------------------------------------------------------------------
# Bias sweep tests
# ---------------------------------------------------------------------------

def test_effective_bias_sequence_no_sweep():
    """Without a sweep, returns bias_V repeated iterations_per_tile times."""
    cfg = MosaicConfig(bias_V=0.2, iterations_per_tile=4)
    seq = cfg.effective_bias_sequence()
    assert seq == [0.2, 0.2, 0.2, 0.2]


def test_effective_bias_sequence_with_sweep():
    """With a sweep, returns the sweep list exactly."""
    sweep = [-0.5, -0.1, 0.1, 0.5]
    cfg = MosaicConfig(bias_V=0.1, iterations_per_tile=99, bias_sweep=sweep)
    assert cfg.effective_bias_sequence() == sweep


def test_effective_iterations_no_sweep():
    cfg = MosaicConfig(iterations_per_tile=3)
    assert cfg.effective_iterations() == 3


def test_effective_iterations_with_sweep():
    cfg = MosaicConfig(iterations_per_tile=1, bias_sweep=[-0.5, 0.0, 0.5, 1.0])
    assert cfg.effective_iterations() == 4


def test_bias_sweep_overrides_iterations_per_tile_in_estimate():
    """estimate_duration_s must use sweep length, not iterations_per_tile."""
    from scanflow.automation import MosaicStep
    # 2 sweep values → 2 iters per tile
    cfg_sweep = MosaicConfig(iterations_per_tile=10,
                             bias_sweep=[-0.1, 0.1], grid_n=3)
    # 2 manual iters, no sweep
    cfg_plain = MosaicConfig(iterations_per_tile=2, grid_n=3)
    t_sweep = MosaicStep(config=cfg_sweep).estimate_duration_s()
    t_plain = MosaicStep(config=cfg_plain).estimate_duration_s()
    assert t_sweep == pytest.approx(t_plain, rel=1e-6), \
        "sweep with 2 values should match plain 2-iter estimate"


def test_empty_bias_sweep_falls_back_to_single_bias():
    """An explicit empty list must behave like no sweep."""
    cfg_empty = MosaicConfig(bias_V=0.3, iterations_per_tile=2, bias_sweep=[])
    cfg_none = MosaicConfig(bias_V=0.3, iterations_per_tile=2)
    assert cfg_empty.effective_bias_sequence() == cfg_none.effective_bias_sequence()
