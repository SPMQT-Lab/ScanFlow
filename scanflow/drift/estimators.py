"""Drift estimators: image pair in, displacement + confidence out.

Two complementary classical strategies (no ML, no hardware access):

* :class:`FeatureMatchDriftEstimator` — detect features in both frames,
  nearest-neighbour match, median shift. Robust for sparse molecule
  images; refuses when there are too few features to trust.
* :class:`PhaseCorrelationDriftEstimator` — windowed phase correlation
  with sub-pixel parabolic peak refinement. Works on texture without
  discrete features; degrades (low confidence) on periodic lattices,
  where the answer is only defined modulo a lattice vector.

Sign convention: ``dx/dy`` is the displacement OF THE SCENE from
``before`` to ``after`` in pixels (positive dx = features moved right,
positive dy = features moved down, i.e. toward later scanlines). A
*correction* would move the scan frame by the same amount — but applying
it is control-layer work, not this module's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftEstimate:
    """Result of one drift measurement between two frames."""

    ok: bool
    dx_px: float
    dy_px: float
    dx_nm: float
    dy_nm: float
    #: 0..1 — method-specific quality measure; compare within a method,
    #: not across methods.
    confidence: float
    method: str
    method_version: str
    reason: str = ""                       # non-empty when ok is False
    details: dict = field(default_factory=dict)


@runtime_checkable
class DriftEstimator(Protocol):
    """The one interface every drift strategy implements."""

    name: str
    version: str

    def estimate(self, before: np.ndarray, after: np.ndarray,
                 nm_per_px: float) -> DriftEstimate:
        """Estimate scene displacement from ``before`` to ``after``."""
        ...


def _failed(method: str, version: str, reason: str, **details) -> DriftEstimate:
    return DriftEstimate(
        ok=False, dx_px=0.0, dy_px=0.0, dx_nm=0.0, dy_nm=0.0,
        confidence=0.0, method=method, method_version=version,
        reason=reason, details=dict(details),
    )


class FeatureMatchDriftEstimator:
    """Median displacement of nearest-neighbour-matched bright features.

    Refuses (ok=False) with fewer than ``min_matches`` matches or when
    the matched shifts disagree by more than ``max_spread_px`` (median
    absolute deviation) — a disagreeing match set usually means features
    were mispaired, and a wrong drift estimate is worse than none.
    """

    name = "feature_match"
    version = "1.0"

    def __init__(self, *, min_matches: int = 3, max_spread_px: float = 2.0,
                 max_features: int = 30) -> None:
        self.min_matches = int(min_matches)
        self.max_spread_px = float(max_spread_px)
        self.max_features = int(max_features)

    def estimate(self, before: np.ndarray, after: np.ndarray,
                 nm_per_px: float) -> DriftEstimate:
        from scanflow.analysis.feature_discovery import discover_features

        feats_a = discover_features(before, nm_per_px,
                                    max_features=self.max_features)
        feats_b = discover_features(after, nm_per_px,
                                    max_features=self.max_features)
        if len(feats_a) < self.min_matches or len(feats_b) < self.min_matches:
            return _failed(
                self.name, self.version,
                f"too few features (before={len(feats_a)}, after={len(feats_b)}, "
                f"need {self.min_matches})",
                n_before=len(feats_a), n_after=len(feats_b),
            )

        shifts = []
        for f in feats_a:
            nearest = min(
                feats_b,
                key=lambda g: (g.cx_px - f.cx_px) ** 2 + (g.cy_px - f.cy_px) ** 2,
            )
            shifts.append((nearest.cx_px - f.cx_px, nearest.cy_px - f.cy_px))
        arr = np.asarray(shifts, dtype=float)
        median = np.median(arr, axis=0)
        mad = float(np.median(np.abs(arr - median).max(axis=1)))
        # Inliers: matches that agree with the median displacement.
        inliers = int(np.sum(np.abs(arr - median).max(axis=1) <= self.max_spread_px))

        if inliers < self.min_matches:
            return _failed(
                self.name, self.version,
                f"matched shifts disagree (only {inliers} inliers, "
                f"MAD={mad:.2f} px)",
                n_before=len(feats_a), n_after=len(feats_b),
                mad_px=mad, inliers=inliers,
            )
        confidence = (inliers / max(len(feats_a), len(feats_b))) * \
            max(0.0, 1.0 - mad / self.max_spread_px)
        dx, dy = float(median[0]), float(median[1])
        return DriftEstimate(
            ok=True, dx_px=dx, dy_px=dy,
            dx_nm=dx * nm_per_px, dy_nm=dy * nm_per_px,
            confidence=float(confidence),
            method=self.name, method_version=self.version,
            details={"n_before": len(feats_a), "n_after": len(feats_b),
                     "inliers": inliers, "mad_px": mad},
        )


class PhaseCorrelationDriftEstimator:
    """Windowed phase correlation with sub-pixel parabolic refinement.

    ``min_confidence`` gates ok: the normalised correlation peak is ~1
    for identical content and falls toward the noise floor when frames
    do not actually overlap, or is ambiguous on periodic-only content.
    """

    name = "phase_correlation"
    version = "1.0"

    def __init__(self, *, min_confidence: float = 0.05,
                 max_shift_fraction: float = 0.5) -> None:
        self.min_confidence = float(min_confidence)
        self.max_shift_fraction = float(max_shift_fraction)

    def estimate(self, before: np.ndarray, after: np.ndarray,
                 nm_per_px: float) -> DriftEstimate:
        from scanflow.analysis.feature_discovery import _level_correct

        if before.shape != after.shape:
            return _failed(self.name, self.version,
                           f"shape mismatch {before.shape} vs {after.shape}")
        a = _level_correct(np.asarray(before, dtype=float))
        b = _level_correct(np.asarray(after, dtype=float))
        # NaN rows (partial frames) would propagate through the FFT and
        # could yield a confidently-wrong result; zero-fill them so they
        # contribute nothing to the correlation.
        a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        b = np.nan_to_num(b, nan=0.0, posinf=0.0, neginf=0.0)
        ny, nx = a.shape
        window = np.outer(np.hanning(ny), np.hanning(nx))
        fa = np.fft.fft2(a * window)
        fb = np.fft.fft2(b * window)
        cross = fb * np.conj(fa)
        denom = np.abs(cross)
        denom[denom == 0] = 1e-12
        corr = np.fft.ifft2(cross / denom).real

        peak_idx = np.unravel_index(int(np.argmax(corr)), corr.shape)
        peak_val = float(corr[peak_idx])
        dy_i, dx_i = peak_idx
        # Wrap to signed shifts
        if dy_i > ny // 2:
            dy_i -= ny
        if dx_i > nx // 2:
            dx_i -= nx

        # Sub-pixel: parabola through the peak and its wrapped neighbours.
        def _subpixel(axis_len: int, idx: int, line: np.ndarray) -> float:
            c0 = line[idx % axis_len]
            cm = line[(idx - 1) % axis_len]
            cp = line[(idx + 1) % axis_len]
            denom_ = (cm - 2 * c0 + cp)
            if denom_ == 0:
                return float(idx)
            return float(idx) + 0.5 * (cm - cp) / denom_

        dy = _subpixel(ny, dy_i, corr[:, peak_idx[1]])
        dx = _subpixel(nx, dx_i, corr[peak_idx[0], :])

        confidence = peak_val  # normalised cross-power peak height
        # Never emit a confidently-wrong estimate: any non-finite number
        # in the result means the correlation was degenerate.
        if not (np.isfinite(dx) and np.isfinite(dy) and np.isfinite(confidence)):
            return _failed(self.name, self.version,
                           "degenerate correlation (non-finite result)")
        max_dx = nx * self.max_shift_fraction
        max_dy = ny * self.max_shift_fraction
        if confidence < self.min_confidence:
            return _failed(
                self.name, self.version,
                f"correlation peak too weak ({confidence:.3f} < "
                f"{self.min_confidence}) — frames may not overlap or "
                "content is periodic/ambiguous",
                peak=confidence, dx_px=dx, dy_px=dy,
            )
        if abs(dx) > max_dx or abs(dy) > max_dy:
            return _failed(
                self.name, self.version,
                f"implausible shift ({dx:+.1f}, {dy:+.1f}) px exceeds "
                f"{self.max_shift_fraction:.0%} of the frame",
                peak=confidence,
            )
        return DriftEstimate(
            ok=True, dx_px=dx, dy_px=dy,
            dx_nm=dx * nm_per_px, dy_nm=dy * nm_per_px,
            confidence=min(1.0, confidence),
            method=self.name, method_version=self.version,
            details={"peak": peak_val},
        )


#: Registry for comparison harnesses and future GUI/strategy selection.
ALL_ESTIMATORS: tuple[type, ...] = (
    FeatureMatchDriftEstimator,
    PhaseCorrelationDriftEstimator,
)


def estimate_with_all(before: np.ndarray, after: np.ndarray,
                      nm_per_px: float) -> list[DriftEstimate]:
    """Run every registered estimator on one frame pair.

    Used by campaign code to LOG drift measurements from both methods
    side by side — that comparison, accumulated over real runs, is the
    evidence base for choosing the lab's drift-correction strategy.
    Estimator exceptions are converted into failed estimates so a broken
    strategy can never take down an acquisition run.
    """
    results: list[DriftEstimate] = []
    for cls in ALL_ESTIMATORS:
        est = cls()
        try:
            results.append(est.estimate(before, after, nm_per_px))
        except Exception as exc:  # estimator bugs must not kill a campaign
            log.exception("drift estimator %s raised", est.name)
            results.append(_failed(est.name, est.version,
                                   f"estimator raised: {exc}"))
    return results
