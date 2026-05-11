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
