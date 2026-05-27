"""Drift detection between successive STM scans.

Two complementary methods are exposed via the ``method`` parameter:

* ``"phase"`` — phase cross-correlation on level-corrected, Gaussian-smoothed
  images. Robust on continuous textured surfaces. This is the original
  py-createc / scan_with_tracking strategy.

* ``"features"`` — particle segmentation followed by nearest-neighbour
  matching of feature centroids. Better suited to sparse molecule scans
  where bias-dependent contrast changes break cross-correlation but the
  *positions* of features remain stable.

* ``"hybrid"`` (default) — try features first; if too few particles match
  reliably, fall back to phase correlation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class DriftResult:
    dx_pixels: float
    dy_pixels: float
    dx_angstrom: float
    dy_angstrom: float
    magnitude_angstrom: float
    rate_angstrom_per_s: Optional[float]  # None if timestamps unavailable
    confidence: float                      # 0–1
    method: str = "phase"                  # which path produced this result
    matched_features: int = 0              # number of features used (feature mode)


class DriftDetector:
    """Compares two STM scan arrays and returns the measured drift.

    Parameters
    ----------
    angstrom_per_pixel:
        Physical scale used to convert pixel shift to real-space units.
    continuous:
        If True, extrapolate drift assuming linear rate between the two images.
    method:
        ``"phase"`` (cross-correlation), ``"features"`` (particle tracking),
        or ``"hybrid"`` (features with phase fallback).
    min_matched_features:
        Below this number of matched particles, the feature path is
        considered untrustworthy and ``hybrid`` falls back to phase.
    """

    def __init__(
        self,
        angstrom_per_pixel: float = 1.0,
        continuous: bool = True,
        method: str = "hybrid",
        min_matched_features: int = 3,
    ) -> None:
        if method not in ("phase", "features", "hybrid"):
            raise ValueError(f"Unknown drift method {method!r}")
        self.angstrom_per_pixel = angstrom_per_pixel
        self.continuous = continuous
        self.method = method
        self.min_matched_features = min_matched_features

    def measure(
        self,
        reference: np.ndarray,
        current: np.ndarray,
        ref_timestamp: Optional[float] = None,
        cur_timestamp: Optional[float] = None,
        extra_seconds: float = 0.0,
    ) -> DriftResult:
        """Compute drift between reference and current scan arrays."""
        # Drop into a no-drift result if frames have incompatible shapes —
        # avoids nuisance crashes when one scan was aborted partway through.
        if reference.shape != current.shape:
            return DriftResult(
                dx_pixels=0.0, dy_pixels=0.0, dx_angstrom=0.0, dy_angstrom=0.0,
                magnitude_angstrom=0.0, rate_angstrom_per_s=None,
                confidence=0.0, method="skipped-shape-mismatch",
                matched_features=0,
            )

        if self.method == "features":
            shift, conf, matched = self._measure_features(reference, current)
            method_used = "features"
            if shift is None:
                shift = np.zeros(2, dtype=float)
                method_used = "features-insufficient"
        elif self.method == "phase":
            shift, conf = self._measure_phase(reference, current)
            matched = 0
            method_used = "phase"
        else:  # hybrid
            shift, conf, matched = self._measure_features(reference, current)
            if shift is None or matched < self.min_matched_features:
                shift, conf = self._measure_phase(reference, current)
                method_used = "phase"
            else:
                method_used = "features"

        dy_px, dx_px = float(shift[0]), float(shift[1])

        if self.continuous and ref_timestamp and cur_timestamp:
            dt_images = cur_timestamp - ref_timestamp
            dt_total = dt_images + extra_seconds
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

        return DriftResult(
            dx_pixels=dx_px,
            dy_pixels=dy_px,
            dx_angstrom=dx_a,
            dy_angstrom=dy_a,
            magnitude_angstrom=magnitude,
            rate_angstrom_per_s=rate,
            confidence=float(conf),
            method=method_used,
            matched_features=int(matched),
        )

    # ------------------------------------------------------------------
    # Phase cross-correlation path
    # ------------------------------------------------------------------

    def _measure_phase(
        self, reference: np.ndarray, current: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        from skimage.registration import phase_cross_correlation
        from skimage.filters import gaussian
        from skimage.exposure import rescale_intensity

        ref_proc = self._preprocess(reference, rescale_intensity, gaussian)
        cur_proc = self._preprocess(current, rescale_intensity, gaussian)

        # phase_cross_correlation(target, reference) returns the shift to
        # apply to ``reference`` to align it with ``target`` — the tip-offset
        # correction we want.
        shift, _, _ = phase_cross_correlation(cur_proc, ref_proc, upsample_factor=10)
        confidence = self._peak_confidence(ref_proc, cur_proc, shift)
        return np.asarray(shift, dtype=float), float(confidence)

    # ------------------------------------------------------------------
    # Feature-based path
    # ------------------------------------------------------------------

    def _measure_features(
        self, reference: np.ndarray, current: np.ndarray
    ) -> Tuple[Optional[np.ndarray], float, int]:
        """Match segmented particle centroids and return the median shift.

        Returns ``(shift, confidence, n_matched)`` where ``shift`` is
        ``[dy_px, dx_px]`` or ``None`` if not enough features were found.
        Confidence is the inlier fraction of nearest-neighbour matches.
        """
        ref_centroids = self._segment_centroids(reference)
        cur_centroids = self._segment_centroids(current)
        if len(ref_centroids) < self.min_matched_features:
            return None, 0.0, 0
        if len(cur_centroids) < self.min_matched_features:
            return None, 0.0, 0

        # Nearest-neighbour match cur → ref. For each current feature, find the
        # closest reference feature; then compute the displacement vector. If
        # the drift is small relative to feature spacing, this is accurate.
        ref_arr = np.asarray(ref_centroids)
        cur_arr = np.asarray(cur_centroids)
        # Cap correspondences: ref point must be within
        # half the median nearest-feature distance, else discard.
        med_spacing = self._median_nearest_distance(ref_arr)
        max_match_dist = max(med_spacing * 0.6, 1.0)

        deltas: List[Tuple[float, float]] = []
        for c in cur_arr:
            d2 = np.sum((ref_arr - c) ** 2, axis=1)
            j = int(np.argmin(d2))
            dist = float(np.sqrt(d2[j]))
            if dist <= max_match_dist:
                deltas.append((c[0] - ref_arr[j, 0], c[1] - ref_arr[j, 1]))

        if len(deltas) < self.min_matched_features:
            return None, 0.0, len(deltas)

        arr = np.asarray(deltas)
        median = np.median(arr, axis=0)
        # Reject outliers > 3× MAD from the median.
        mad = np.median(np.abs(arr - median), axis=0) + 1e-6
        keep = np.all(np.abs(arr - median) <= 3.0 * mad, axis=1)
        if int(keep.sum()) >= self.min_matched_features:
            median = np.median(arr[keep], axis=0)
            inlier_fraction = float(keep.sum()) / float(len(arr))
        else:
            inlier_fraction = float(len(arr)) / float(len(cur_arr))

        return np.asarray([median[0], median[1]], dtype=float), inlier_fraction, int(keep.sum() if keep.sum() else len(arr))

    @staticmethod
    def _segment_centroids(arr: np.ndarray) -> List[Tuple[float, float]]:
        """Return ``[(y_px, x_px), ...]`` centroids of bright features.

        Uses Otsu thresholding on the level-corrected image; small regions
        are dropped (< 0.05 % of image area) so isolated noise pixels don't
        masquerade as features.
        """
        from skimage.filters import threshold_otsu, gaussian
        from skimage.measure import label, regionprops
        from skimage.exposure import rescale_intensity

        levelled = _level_correct(arr)
        smoothed = gaussian(rescale_intensity(levelled.astype(float)), sigma=1.0)
        try:
            thresh = threshold_otsu(smoothed)
        except ValueError:
            return []
        mask = smoothed > thresh
        if not mask.any():
            return []
        labels = label(mask)
        min_area = max(4, int(0.0005 * mask.size))
        out: List[Tuple[float, float]] = []
        for region in regionprops(labels):
            if region.area < min_area:
                continue
            cy, cx = region.centroid
            out.append((float(cy), float(cx)))
        return out

    @staticmethod
    def _median_nearest_distance(centroids: np.ndarray) -> float:
        if len(centroids) < 2:
            return 1.0
        dists: List[float] = []
        for i, c in enumerate(centroids):
            d2 = np.sum((centroids - c) ** 2, axis=1)
            d2[i] = np.inf
            dists.append(float(np.sqrt(d2.min())))
        return float(np.median(dists))

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
