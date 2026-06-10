"""Locks in the Createc Y-convention so it can never silently regress.

These tests use the actual numbers from the 2026-06-01 lab session that
exposed the original bug: home=(-33.758, 9.149), wide=60×60 nm, group
Δ=(16.32, -26.78), frame=15×15 nm. Old (wrong) Y formula
`home_y + dy` gave -17.63, outside the scan; correct formula gives 4.87,
which clamps to the home_y top edge (9.149).
"""

import pytest

from scanflow.core.scan_geometry import (
    clamp_frame_to_wide_bounds,
    feature_target_xy_nm,
    frame_top_edge_y_nm,
    image_center_y_nm,
)


# ------------------------------------------------------------------
# image_center_y_nm
# ------------------------------------------------------------------

def test_image_center_is_top_edge_plus_half_height():
    # 60 nm wide scan starting at top edge Y = 9.149 → centre = 39.149
    assert image_center_y_nm(home_y_nm=9.149, scan_range_y_nm=60.0) == 39.149


def test_image_center_zero_at_origin():
    assert image_center_y_nm(0.0, 100.0) == 50.0


# ------------------------------------------------------------------
# frame_top_edge_y_nm
# ------------------------------------------------------------------

def test_frame_top_edge_dy_zero_centred_on_image_centre():
    # Feature at dy=0 (image centre), 10 nm tall frame → top edge = centre - 5
    assert frame_top_edge_y_nm(50.0, 0.0, 10.0) == 45.0


def test_frame_top_edge_positive_dy_moves_down():
    # +5 nm dy = below centre = larger Createc Y
    assert frame_top_edge_y_nm(50.0, 5.0, 10.0) == 50.0


# ------------------------------------------------------------------
# feature_target_xy_nm — the full reduction
# ------------------------------------------------------------------

def test_feature_target_with_lab_2026_06_01_numbers():
    """The exact numbers that exposed the Y-axis bug originally."""
    target_x, target_y = feature_target_xy_nm(
        home_x_nm=-33.758,
        home_y_nm=9.149,
        scan_range_y_nm=60.0,
        feature_dx_nm=16.32,
        feature_dy_nm=-26.78,
        frame_height_nm=15.0,
    )
    # X: -33.758 + 16.32 = -17.438
    assert target_x == pytest.approx(-17.438, abs=1e-6)
    # Y: image_centre = 9.149 + 30 = 39.149 ; top edge = 39.149 + (-26.78) - 7.5 = 4.869
    assert target_y == pytest.approx(4.869, abs=1e-3)


def test_feature_at_image_centre_yields_centered_frame():
    # dy=0, dx=0 means feature is at the wide-scan image centre.
    # The new frame's TOP EDGE should sit at (centre - frame/2).
    target_x, target_y = feature_target_xy_nm(
        home_x_nm=0.0,
        home_y_nm=0.0,
        scan_range_y_nm=100.0,
        feature_dx_nm=0.0,
        feature_dy_nm=0.0,
        frame_height_nm=20.0,
    )
    assert target_x == 0.0
    # image centre Y = 50; new frame top edge = 50 - 10 = 40
    assert target_y == 40.0


# ------------------------------------------------------------------
# clamp_frame_to_wide_bounds
# ------------------------------------------------------------------

def test_clamp_returns_input_when_inside_bounds():
    x, y = clamp_frame_to_wide_bounds(
        0.0, 0.0,
        home_x_nm=0.0, home_y_nm=0.0,
        scan_range_x_nm=100.0, scan_range_y_nm=100.0,
        frame_width_nm=10.0, frame_height_nm=10.0,
    )
    assert (x, y) == (0.0, 0.0)


def test_clamp_pulls_frame_inside_x_bounds():
    # Centre at +50, frame 20 wide, scan range 60 → max centre = 30 - 10 = 20
    x, _ = clamp_frame_to_wide_bounds(
        50.0, 0.0,
        home_x_nm=0.0, home_y_nm=0.0,
        scan_range_x_nm=60.0, scan_range_y_nm=60.0,
        frame_width_nm=20.0, frame_height_nm=10.0,
    )
    assert x == 20.0


def test_clamp_2026_06_01_case_pulls_frame_to_top_edge():
    """The original failing case: target_y=4.869, top edge of scan = 9.149,
    so the frame would extend above the scan and gets clamped to home_y."""
    _, y = clamp_frame_to_wide_bounds(
        -17.438, 4.869,
        home_x_nm=-33.758, home_y_nm=9.149,
        scan_range_x_nm=60.0, scan_range_y_nm=60.0,
        frame_width_nm=15.0, frame_height_nm=15.0,
    )
    assert y == pytest.approx(9.149, abs=1e-6)


def test_clamp_skips_axis_when_frame_larger_than_scan():
    # 200 nm frame, 100 nm scan → can't fit → return target unchanged
    x, y = clamp_frame_to_wide_bounds(
        99.0, -99.0,
        home_x_nm=0.0, home_y_nm=0.0,
        scan_range_x_nm=100.0, scan_range_y_nm=100.0,
        frame_width_nm=200.0, frame_height_nm=200.0,
    )
    assert (x, y) == (99.0, -99.0)


# ------------------------------------------------------------------
# Cross-module: feature_target then clamp
# ------------------------------------------------------------------

def test_feature_target_then_clamp_full_pipeline():
    """End-to-end: target a feature at the wide-scan boundary, expect clamping."""
    home_x, home_y = -33.758, 9.149
    scan_x, scan_y = 60.0, 60.0
    target_x, target_y = feature_target_xy_nm(
        home_x_nm=home_x, home_y_nm=home_y,
        scan_range_y_nm=scan_y,
        feature_dx_nm=16.32, feature_dy_nm=-26.78,
        frame_height_nm=15.0,
    )
    clamped_x, clamped_y = clamp_frame_to_wide_bounds(
        target_x, target_y,
        home_x_nm=home_x, home_y_nm=home_y,
        scan_range_x_nm=scan_x, scan_range_y_nm=scan_y,
        frame_width_nm=15.0, frame_height_nm=15.0,
    )
    # X stays (feature is inside X bounds)
    assert clamped_x == pytest.approx(-17.438, abs=1e-6)
    # Y clamps to home_y (frame would extend above scan top)
    assert clamped_y == pytest.approx(9.149, abs=1e-6)
