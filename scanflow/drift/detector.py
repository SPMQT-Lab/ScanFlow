"""Drift detection between successive STM scans.

Strategy: phase cross-correlation on level-corrected, Gaussian-smoothed images.
This is the same approach proven in py-createc's scan_with_tracking.py, exposed
here as a reusable, testable class with optional continuous-drift extrapolation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class DriftResult:
    dx_pixels: float
    dy_pixels: float
    dx_angstrom: float
    dy_angstrom: float
    magnitude_angstrom: float
    rate_angstrom_per_s: Optional[float]  # None if timestamps unavailable
    confidence: float                      # 0–1, from cross-correlation peak


class DriftDetector:
    """Compares two STM scan arrays and returns the measured drift.

    Parameters
    ----------
    angstrom_per_pixel:
        Physical scale used to convert pixel shift to real-space units.
    continuous:
        If True, extrapolate drift assuming linear rate between the two images.
    """

    def __init__(self, angstrom_per_pixel: float = 1.0, continuous: bool = True) -> None:
        self.angstrom_per_pixel = angstrom_per_pixel
        self.continuous = continuous

    def measure(
        self,
        reference: np.ndarray,
        current: np.ndarray,
        ref_timestamp: Optional[float] = None,
        cur_timestamp: Optional[float] = None,
        extra_seconds: float = 0.0,
    ) -> DriftResult:
        """Compute drift between reference and current scan arrays.

        Parameters
        ----------
        reference, current:
            2-D float arrays of the same shape (single channel, e.g. topography).
        ref_timestamp, cur_timestamp:
            Unix timestamps of each scan (used for drift rate and extrapolation).
        extra_seconds:
            Additional time expected before correction is applied (used for
            continuous-drift look-ahead).
        """
        from skimage.registration import phase_cross_correlation
        from skimage.filters import gaussian
        from skimage.exposure import rescale_intensity

        ref_proc = self._preprocess(reference, rescale_intensity, gaussian)
        cur_proc = self._preprocess(current, rescale_intensity, gaussian)

        # phase_cross_correlation(target, reference) returns the shift to apply
        # to `reference` to align it with `target` — i.e., the drift direction
        # of features in `current` relative to `reference`. This is exactly the
        # tip-offset correction we want.
        shift, _, _ = phase_cross_correlation(cur_proc, ref_proc, upsample_factor=10)
        dy_px, dx_px = float(shift[0]), float(shift[1])

        # Continuous-drift extrapolation: scale shift by elapsed-time ratio
        if self.continuous and ref_timestamp and cur_timestamp:
            dt_images = cur_timestamp - ref_timestamp
            dt_total = (cur_timestamp - ref_timestamp) + extra_seconds
            if dt_images > 0:
                scale = dt_total / dt_images
                dy_px *= scale
                dx_px *= scale

        dx_a = dx_px * self.angstrom_per_pixel
        dy_a = dy_px * self.angstrom_per_pixel
        magnitude = float(np.hypot(dx_a, dy_a))

        rate = None
        if ref_timestamp and cur_timestamp:
            dt = cur_timestamp - ref_timestamp
            rate = magnitude / dt if dt > 0 else None

        # Use the cross-correlation peak normalised value as a rough confidence score
        confidence = self._peak_confidence(ref_proc, cur_proc, shift)

        return DriftResult(
            dx_pixels=dx_px,
            dy_pixels=dy_px,
            dx_angstrom=dx_a,
            dy_angstrom=dy_a,
            magnitude_angstrom=magnitude,
            rate_angstrom_per_s=rate,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _preprocess(arr: np.ndarray, rescale_intensity, gaussian) -> np.ndarray:
        """Level-correct, rescale, and smooth an image for robust correlation."""
        levelled = _level_correct(arr)
        rescaled = rescale_intensity(levelled.astype(float))
        return gaussian(rescaled, sigma=1.0)

    @staticmethod
    def _peak_confidence(ref: np.ndarray, cur: np.ndarray, shift: np.ndarray) -> float:
        """Return normalised cross-correlation peak as a 0–1 confidence value."""
        try:
            from skimage.registration import phase_cross_correlation
            import numpy.fft as fft

            f_ref = fft.fft2(ref)
            f_cur = fft.fft2(cur)
            cross = f_ref * np.conj(f_cur)
            norm = np.abs(cross)
            norm[norm == 0] = 1
            cc = np.abs(fft.ifft2(cross / norm))
            peak = float(cc.max())
            return min(peak / cc.size, 1.0)
        except Exception:
            return 0.0


def _level_correct(img: np.ndarray) -> np.ndarray:
    """Subtract a fitted plane from a 2-D image array."""
    m, n = img.shape
    x1, x2 = np.mgrid[:m, :n]
    X = np.column_stack([np.ones(m * n), x1.ravel(), x2.ravel()])
    Y = img.ravel()
    theta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    plane = (X @ theta).reshape(m, n)
    return img - plane
