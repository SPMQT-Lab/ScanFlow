"""Auto-centering of preview features via a quick low-resolution scan.

``find_feature_center_offset`` returns the (dx_nm, dy_nm) correction needed
to shift the scan offset so the dominant feature sits at the frame centre.
``CorrectionTracker`` accumulates per-feature results and emits formatted log lines.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from scanflow.automation.scan_metrics import _plane_subtract

log = logging.getLogger(__name__)


@dataclass
class CenteringResult:
    feature_idx: int
    dx_nm: float        # correction applied to X offset
    dy_nm: float        # correction applied to Y offset
    method: str         # 'gaussian_fit' | 'centroid' | 'peak'
    quality: float      # R² (gaussian), mask fraction (centroid), 0.0 (peak)


def find_feature_center_offset(
    image: np.ndarray,
    nm_per_pixel: float,
) -> tuple[float, float, str, float]:
    """Return (dx_nm, dy_nm, method, quality) to center the dominant feature.

    Applies plane subtraction first, then cascades:
    1. 2D Gaussian fit (R² > 0.3 required)
    2. Thresholded centroid
    3. Peak pixel (always succeeds)

    Sign convention: positive dx means the feature is to the right of the
    image centre, so the offset must be shifted by +dx to re-centre it.
    """
    arr = _plane_subtract(np.asarray(image, dtype=float))
    ny, nx = arr.shape

    result = _try_gaussian_fit(arr, ny, nx)
    if result is not None:
        cy_px, cx_px, r2 = result
        return (
            (cx_px - nx / 2.0) * nm_per_pixel,
            (cy_px - ny / 2.0) * nm_per_pixel,
            "gaussian_fit",
            float(r2),
        )

    result2 = _try_centroid(arr, ny, nx)
    if result2 is not None:
        cy_px, cx_px, frac = result2
        return (
            (cx_px - nx / 2.0) * nm_per_pixel,
            (cy_px - ny / 2.0) * nm_per_pixel,
            "centroid",
            float(frac),
        )

    cy_px, cx_px = _peak_pixel(arr)
    return (
        (cx_px - nx / 2.0) * nm_per_pixel,
        (cy_px - ny / 2.0) * nm_per_pixel,
        "peak",
        0.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gaussian_2d(xy, amplitude, cx, cy, sigma_x, sigma_y, offset):
    x, y = xy
    return (
        offset
        + amplitude * np.exp(
            -(
                ((x - cx) ** 2) / (2 * sigma_x ** 2)
                + ((y - cy) ** 2) / (2 * sigma_y ** 2)
            )
        )
    ).ravel()


def _try_gaussian_fit(
    arr: np.ndarray, ny: int, nx: int
) -> Optional[tuple[float, float, float]]:
    """2D Gaussian fit. Returns (cy_px, cx_px, R²) or None."""
    try:
        from scipy.optimize import curve_fit

        y, x = np.mgrid[:ny, :nx].astype(float)
        peak_idx = np.unravel_index(np.argmax(np.abs(arr - arr.mean())), arr.shape)
        amplitude_guess = float(arr[peak_idx])
        sigma_guess = min(ny, nx) / 4.0
        p0 = [amplitude_guess, float(peak_idx[1]), float(peak_idx[0]),
              sigma_guess, sigma_guess, 0.0]
        bounds = (
            [-np.inf, 0.0, 0.0, 1.0, 1.0, -np.inf],
            [np.inf, float(nx), float(ny), float(nx), float(ny), np.inf],
        )
        popt, _ = curve_fit(
            _gaussian_2d, (x, y), arr.ravel(),
            p0=p0, bounds=bounds, maxfev=4000,
        )
        _, cx_fit, cy_fit, _sx, _sy, _ = popt
        v_pred = _gaussian_2d((x, y), *popt).reshape(ny, nx)
        ss_res = float(np.sum((arr - v_pred) ** 2))
        ss_tot = float(np.sum((arr - arr.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if r2 < 0.3:
            return None
        return float(cy_fit), float(cx_fit), max(0.0, r2)
    except Exception:
        return None


def _try_centroid(
    arr: np.ndarray, ny: int, nx: int
) -> Optional[tuple[float, float, float]]:
    """Thresholded centroid. Returns (cy_px, cx_px, mask_fraction) or None."""
    sigma = float(np.std(arr))
    if sigma == 0:
        return None
    mean = float(arr.mean())
    mask = np.abs(arr - mean) > 0.5 * sigma
    n_masked = int(mask.sum())
    if n_masked < 4:
        return None
    y, x = np.mgrid[:ny, :nx].astype(float)
    weights = np.where(mask, np.abs(arr - mean), 0.0)
    total_w = float(weights.sum())
    if total_w == 0:
        return None
    cy = float((y * weights).sum() / total_w)
    cx = float((x * weights).sum() / total_w)
    return cy, cx, n_masked / (ny * nx)


def _peak_pixel(arr: np.ndarray) -> tuple[float, float]:
    """Row and column of the pixel with the largest absolute deviation from mean."""
    idx = np.unravel_index(np.argmax(np.abs(arr - arr.mean())), arr.shape)
    return float(idx[0]), float(idx[1])


# ─────────────────────────────────────────────────────────────────────────────
# Correction accumulator
# ─────────────────────────────────────────────────────────────────────────────

class CorrectionTracker:
    """Accumulates per-feature centering results and emits formatted log lines."""

    def __init__(self) -> None:
        self._results: list[CenteringResult] = []

    def add(self, result: CenteringResult) -> str:
        """Append *result* and return a formatted one-line log entry."""
        self._results.append(result)
        cum_x, cum_y = self.cumulative
        if result.method == "gaussian_fit":
            q_str = f"R²={result.quality:.2f}"
        else:
            q_str = f"q={result.quality:.3f}"
        return (
            f"Auto-center #{len(self._results)}: "
            f"correction ({result.dx_nm:+.2f}, {result.dy_nm:+.2f}) nm  "
            f"[{result.method}, {q_str}]  |  "
            f"session: ({cum_x:+.2f}, {cum_y:+.2f}) nm"
        )

    @property
    def cumulative(self) -> tuple[float, float]:
        """Sum of all corrections applied so far."""
        if not self._results:
            return 0.0, 0.0
        return (
            sum(r.dx_nm for r in self._results),
            sum(r.dy_nm for r in self._results),
        )

    def summary(self) -> str:
        """Multi-line session summary for the sweep-end log."""
        if not self._results:
            return "Auto-centering: no corrections applied."
        cum_x, cum_y = self.cumulative
        lines = [f"─── Auto-center summary ({len(self._results)} feature(s)) ───"]
        for r in self._results:
            lines.append(
                f"  Feature {r.feature_idx + 1}: "
                f"({r.dx_nm:+.2f}, {r.dy_nm:+.2f}) nm  via {r.method}"
            )
        lines.append(
            f"  Session cumulative: ΔX = {cum_x:+.2f} nm,  ΔY = {cum_y:+.2f} nm"
        )
        return "\n".join(lines)
