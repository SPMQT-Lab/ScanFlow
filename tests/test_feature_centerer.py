"""Tests for find_feature_center_offset and CorrectionTracker."""
from __future__ import annotations

import numpy as np
import pytest

from scanflow.automation.feature_centerer import (
    CenteringResult,
    CorrectionTracker,
    find_feature_center_offset,
    _try_gaussian_fit,
    _try_centroid,
    _peak_pixel,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gaussian_image(
    ny: int = 64, nx: int = 64,
    cy: float = 32.0, cx: float = 40.0,
    sigma: float = 5.0,
    amplitude: float = 1.0,
    noise: float = 0.02,
    tilt_x: float = 0.0,
    tilt_y: float = 0.0,
) -> np.ndarray:
    rng = np.random.default_rng(7)
    y, x = np.mgrid[:ny, :nx].astype(float)
    blob = amplitude * np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * sigma ** 2))
    tilt = tilt_x * x + tilt_y * y
    return blob + tilt + rng.normal(0.0, noise, (ny, nx))


# ─────────────────────────────────────────────────────────────────────────────
# Gaussian fit
# ─────────────────────────────────────────────────────────────────────────────

def test_gaussian_fit_finds_center():
    """Gaussian blob offset from image centre — fit should locate it within 1 px."""
    ny, nx = 64, 64
    cy_true, cx_true = 38.0, 44.0
    img = _gaussian_image(ny=ny, nx=nx, cy=cy_true, cx=cx_true, sigma=6.0, amplitude=1.0, noise=0.01)
    result = _try_gaussian_fit(img - img.mean(), ny, nx)
    assert result is not None, "Gaussian fit should succeed on a clean blob"
    cy_fit, cx_fit, r2 = result
    assert abs(cy_fit - cy_true) < 1.5
    assert abs(cx_fit - cx_true) < 1.5
    assert r2 > 0.5


def test_gaussian_fit_dark_feature():
    """Inverted (dark) blob should also be located correctly."""
    ny, nx = 64, 64
    cy_true, cx_true = 20.0, 20.0
    img = _gaussian_image(ny=ny, nx=nx, cy=cy_true, cx=cx_true, sigma=5.0, amplitude=-1.0, noise=0.01)
    result = _try_gaussian_fit(img - img.mean(), ny, nx)
    assert result is not None
    cy_fit, cx_fit, _ = result
    assert abs(cy_fit - cy_true) < 1.5
    assert abs(cx_fit - cx_true) < 1.5


# ─────────────────────────────────────────────────────────────────────────────
# Centroid fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_centroid_fallback_locates_feature():
    """Centroid should find a blob even when Gaussian fit is not expected."""
    ny, nx = 32, 32
    cy_true, cx_true = 10.0, 22.0
    img = _gaussian_image(ny=ny, nx=nx, cy=cy_true, cx=cx_true, sigma=3.0, amplitude=1.0, noise=0.0)
    arr = img - img.mean()
    result = _try_centroid(arr, ny, nx)
    assert result is not None
    cy_fit, cx_fit, frac = result
    assert abs(cy_fit - cy_true) < 2.0
    assert abs(cx_fit - cx_true) < 2.0
    assert 0.0 < frac <= 1.0


def test_centroid_returns_none_for_flat():
    arr = np.zeros((32, 32))
    assert _try_centroid(arr, 32, 32) is None


# ─────────────────────────────────────────────────────────────────────────────
# Peak pixel fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_peak_fallback_bright():
    arr = np.zeros((16, 16))
    arr[3, 12] = 5.0
    cy, cx = _peak_pixel(arr)
    assert abs(cy - 3.0) < 0.5
    assert abs(cx - 12.0) < 0.5


def test_peak_fallback_dark():
    arr = np.zeros((16, 16))
    arr[10, 5] = -4.0
    cy, cx = _peak_pixel(arr)
    assert abs(cy - 10.0) < 0.5
    assert abs(cx - 5.0) < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# find_feature_center_offset (full cascade)
# ─────────────────────────────────────────────────────────────────────────────

def test_find_offset_returns_correct_dx_dy():
    """Feature at (cy=32, cx=48) in a 64×64 image → dx_nm should be positive."""
    ny, nx = 64, 64
    nm_per_pixel = 0.5
    cy_true, cx_true = 32.0, 48.0
    img = _gaussian_image(ny=ny, nx=nx, cy=cy_true, cx=cx_true, sigma=6.0, noise=0.01)
    dx_nm, dy_nm, method, quality = find_feature_center_offset(img, nm_per_pixel)
    expected_dx = (cx_true - nx / 2.0) * nm_per_pixel  # positive (right of centre)
    expected_dy = (cy_true - ny / 2.0) * nm_per_pixel  # 0 (at centre vertically)
    assert abs(dx_nm - expected_dx) < 1.5 * nm_per_pixel
    assert abs(dy_nm - expected_dy) < 1.5 * nm_per_pixel
    assert method in ("gaussian_fit", "centroid", "peak")
    assert quality >= 0.0


def test_find_offset_centred_feature_near_zero():
    """A blob exactly at the image centre should give dx ≈ dy ≈ 0."""
    ny, nx = 64, 64
    nm_per_pixel = 0.3
    img = _gaussian_image(ny=ny, nx=nx, cy=32.0, cx=32.0, sigma=6.0, noise=0.01)
    dx_nm, dy_nm, method, quality = find_feature_center_offset(img, nm_per_pixel)
    assert abs(dx_nm) < 2.0 * nm_per_pixel
    assert abs(dy_nm) < 2.0 * nm_per_pixel


def test_tilted_image_corrected_before_centering():
    """Heavy tilt must be subtracted (plane-subtracted) before centering."""
    ny, nx = 64, 64
    nm_per_pixel = 0.5
    cy_true, cx_true = 40.0, 40.0
    img = _gaussian_image(ny=ny, nx=nx, cy=cy_true, cx=cx_true, sigma=5.0,
                          noise=0.01, tilt_x=5.0, tilt_y=3.0)
    dx_nm, dy_nm, method, quality = find_feature_center_offset(img, nm_per_pixel)
    expected_dx = (cx_true - nx / 2.0) * nm_per_pixel
    expected_dy = (cy_true - ny / 2.0) * nm_per_pixel
    assert abs(dx_nm - expected_dx) < 3.0 * nm_per_pixel
    assert abs(dy_nm - expected_dy) < 3.0 * nm_per_pixel


def test_find_offset_always_returns_method_and_quality():
    """Even for a flat image, the cascade must return (method, quality)."""
    img = np.zeros((32, 32))
    dx_nm, dy_nm, method, quality = find_feature_center_offset(img, 0.2)
    assert method in ("gaussian_fit", "centroid", "peak")
    assert isinstance(quality, float)


# ─────────────────────────────────────────────────────────────────────────────
# CorrectionTracker
# ─────────────────────────────────────────────────────────────────────────────

def test_correction_tracker_accumulates():
    tracker = CorrectionTracker()
    r1 = CenteringResult(feature_idx=0, dx_nm=1.0, dy_nm=-0.5, method="gaussian_fit", quality=0.92)
    r2 = CenteringResult(feature_idx=1, dx_nm=-0.3, dy_nm=0.2, method="centroid", quality=0.31)
    r3 = CenteringResult(feature_idx=2, dx_nm=0.5, dy_nm=0.1, method="peak", quality=0.0)

    tracker.add(r1)
    tracker.add(r2)
    msg = tracker.add(r3)

    cum_x, cum_y = tracker.cumulative
    assert abs(cum_x - (1.0 - 0.3 + 0.5)) < 1e-9
    assert abs(cum_y - (-0.5 + 0.2 + 0.1)) < 1e-9
    assert "Auto-center #3" in msg
    assert "session:" in msg


def test_correction_tracker_log_line_format():
    tracker = CorrectionTracker()
    r = CenteringResult(feature_idx=0, dx_nm=2.0, dy_nm=-1.0, method="gaussian_fit", quality=0.85)
    msg = tracker.add(r)
    assert "+2.00" in msg
    assert "-1.00" in msg
    assert "R²=0.85" in msg
    assert "gaussian_fit" in msg
    assert "session:" in msg


def test_correction_tracker_summary_empty():
    tracker = CorrectionTracker()
    s = tracker.summary()
    assert "no corrections" in s.lower()


def test_correction_tracker_summary_with_data():
    tracker = CorrectionTracker()
    tracker.add(CenteringResult(feature_idx=0, dx_nm=1.0, dy_nm=0.5, method="centroid", quality=0.4))
    tracker.add(CenteringResult(feature_idx=1, dx_nm=-0.5, dy_nm=-0.2, method="peak", quality=0.0))
    s = tracker.summary()
    assert "Auto-center summary" in s
    assert "Session cumulative" in s
    assert "ΔX" in s
    assert "ΔY" in s


def test_correction_tracker_cumulative_empty():
    tracker = CorrectionTracker()
    assert tracker.cumulative == (0.0, 0.0)
