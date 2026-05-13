"""Drift detector tests using synthetic shifted images (no STM required)."""

import numpy as np
import pytest
from scanflow.drift import DriftDetector


def _make_image(shift_x=0, shift_y=0, size=64):
    """Gaussian blob at centre, shifted by (shift_x, shift_y) pixels."""
    cx, cy = size // 2 + shift_x, size // 2 + shift_y
    x, y = np.meshgrid(np.arange(size), np.arange(size))
    return np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 5 ** 2))


def test_zero_drift():
    img = _make_image()
    det = DriftDetector(angstrom_per_pixel=1.0, continuous=False)
    result = det.measure(img, img)
    assert abs(result.dx_pixels) < 0.5
    assert abs(result.dy_pixels) < 0.5


def test_known_shift():
    ref = _make_image()
    shifted = _make_image(shift_x=5, shift_y=-3)
    det = DriftDetector(angstrom_per_pixel=1.0, continuous=False)
    result = det.measure(ref, shifted)
    assert abs(result.dx_pixels - 5) < 1.0
    assert abs(result.dy_pixels - (-3)) < 1.0


def _scene_with_blobs(positions, size=128, sigma=4.0):
    """Image with one Gaussian blob per (x, y) position."""
    img = np.zeros((size, size))
    x, y = np.meshgrid(np.arange(size), np.arange(size))
    for px, py in positions:
        img = img + np.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sigma ** 2))
    return img


def test_feature_tracking_recovers_known_shift():
    ref_positions = [(30, 30), (90, 40), (60, 90), (40, 70), (95, 95)]
    shift_x, shift_y = 6, -4
    cur_positions = [(x + shift_x, y + shift_y) for x, y in ref_positions]
    ref = _scene_with_blobs(ref_positions)
    cur = _scene_with_blobs(cur_positions)
    det = DriftDetector(method="features", continuous=False)
    result = det.measure(ref, cur)
    assert result.method == "features"
    assert result.matched_features >= 3
    assert abs(result.dx_pixels - shift_x) < 1.5
    assert abs(result.dy_pixels - shift_y) < 1.5


def test_hybrid_falls_back_to_phase_on_blank():
    """Plain Gaussian blob has no segmentable features → hybrid uses phase."""
    ref = _make_image()
    cur = _make_image(shift_x=4, shift_y=2)
    det = DriftDetector(method="hybrid", continuous=False)
    result = det.measure(ref, cur)
    assert result.method in ("phase", "features")
    assert abs(result.dx_pixels - 4) < 1.5
    assert abs(result.dy_pixels - 2) < 1.5
