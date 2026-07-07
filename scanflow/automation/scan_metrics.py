"""Per-scan quality metrics: Z stability and inter-scan drift tracking.

Z stability
-----------
Computed from the just-acquired topography array.  Each scan line is
linearly detrended (removes local slope) and we report the median
per-line RMS residual in picometres.

Drift tracking
--------------
``DriftTracker`` accepts successive scan images, registers consecutive
pairs via phase cross-correlation, and fits an exponential decay model
to the drift-speed series.  After a sweep it predicts when the drift
will reach a target reduction (default 98%) and reports both a wall-clock
estimate and a scan-count estimate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

# Absolute drift-speed thresholds for imaging readiness (nm/min).
FEATURE_SCAN_THRESHOLD_NM_MIN: float = 0.2   # molecules, clusters
ATOMIC_SCAN_THRESHOLD_NM_MIN: float = 0.05   # atomic resolution


# ─────────────────────────────────────────────────────────────────────────────
# Z stability
# ─────────────────────────────────────────────────────────────────────────────

def compute_z_stability(topo_nm: np.ndarray) -> Dict[str, float]:
    """Return Z-stability metrics for a 2-D topography array (units: nm).

    A scan with only smooth topographic features will have small per-line
    residuals after slope removal.  Tip noise, sample contamination, or
    feedback oscillations all inflate the residual.
    """
    if topo_nm is None or topo_nm.ndim != 2 or topo_nm.size < 4:
        return _empty()

    arr = np.asarray(topo_nm, dtype=float)
    ny, nx = arr.shape
    if nx < 4 or ny < 2:
        return _empty()

    x = np.arange(nx, dtype=float)
    line_rms = []
    for row in arr:
        finite = np.isfinite(row)
        if finite.sum() < 4:
            continue
        rx = x[finite]
        ry = row[finite]
        m, b = np.polyfit(rx, ry, 1)
        resid = ry - (m * rx + b)
        line_rms.append(float(np.std(resid)))

    if not line_rms:
        return _empty()

    arr_rms = np.asarray(line_rms)
    med = float(np.median(arr_rms))
    mx = float(arr_rms.max())
    jumps = int(np.sum(arr_rms > 3.0 * med)) if med > 0 else 0

    rms_pm = med * 1000.0  # nm → pm
    return {
        "rms_pm": rms_pm,
        "max_pm": mx * 1000.0,
        "jumps": jumps,
        "rating": _rate(rms_pm, jumps),
    }


def format_z_stability(metrics: Dict[str, float]) -> str:
    """Human-readable one-liner for the log panel."""
    if not metrics or "rms_pm" not in metrics:
        return "Z stability: unavailable"
    return (
        f"Z stability: {metrics['rms_pm']:.1f} pm RMS  "
        f"(max {metrics['max_pm']:.1f} pm, "
        f"{int(metrics['jumps'])} line jump(s)) "
        f"[{metrics.get('rating', '?')}]"
    )


def is_z_frozen(metrics: Dict[str, float]) -> bool:
    """True when a VALID scan shows exactly zero Z variation (frozen Z).

    A live feedback loop always leaves some per-line residual (tip noise +
    surface corrugation), so an exactly-flat Z channel on real scan data
    means the Z readback or feedback loop is stuck — a malfunction worth
    stopping for. A missing/invalid image (rating ``"n/a"``) is NOT frozen,
    just absent, so it never trips this.
    """
    if not metrics or metrics.get("rating") in (None, "n/a"):
        return False
    return (float(metrics.get("rms_pm", 0.0)) == 0.0
            and float(metrics.get("max_pm", 0.0)) == 0.0)


def _empty() -> Dict[str, float]:
    return {"rms_pm": 0.0, "max_pm": 0.0, "jumps": 0, "rating": "n/a"}


def _rate(rms_pm: float, jumps: int) -> str:
    """Coarse quality label.  Tuned for LT-STM on metals."""
    if rms_pm <= 5.0 and jumps == 0:
        return "excellent"
    if rms_pm <= 15.0 and jumps <= 1:
        return "good"
    if rms_pm <= 50.0 and jumps <= 5:
        return "noisy"
    return "unstable"


# ─────────────────────────────────────────────────────────────────────────────
# Drift tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DriftRecord:
    """Drift measurement between one scan and its predecessor."""
    scan_index: int          # 1-based index of the later scan in the pair
    elapsed_s: float         # wall-clock seconds since the first scan started
    dx_nm: float             # X displacement (col direction, nm)
    dy_nm: float             # Y displacement (row direction, nm)
    vx_nm_min: float         # drift velocity X (nm/min)
    vy_nm_min: float         # drift velocity Y (nm/min)
    speed_nm_min: float      # total drift speed |v| (nm/min)


@dataclass
class DriftFit:
    """Result of fitting an exponential decay to the drift-speed series."""
    v0_nm_min: float           # fitted initial speed at t = 0 (nm/min)
    tau_s: float               # exponential time constant (s); inf if not decaying
    r_squared: float           # goodness of fit (0–1)
    n_records: int             # number of velocity measurements used
    pct_reduction: float       # actual % speed reduction between first and last record
    feature_scan_ready: bool = False    # current speed ≤ FEATURE_SCAN_THRESHOLD_NM_MIN
    atomic_scan_ready: bool = False     # current speed ≤ ATOMIC_SCAN_THRESHOLD_NM_MIN
    feature_scan_at_s: Optional[float] = None   # predicted elapsed s to reach 0.2 nm/min
    feature_scan_at_scan: Optional[int] = None
    atomic_scan_at_s: Optional[float] = None    # predicted elapsed s to reach 0.05 nm/min
    atomic_scan_at_scan: Optional[int] = None


class DriftTracker:
    """Track inter-scan XY drift across a sweep run.

    Usage::

        tracker = DriftTracker(target_reduction=0.98)
        tracker.reset()

        # called for every completed scan:
        record = tracker.add_scan(image_array, nm_per_pixel=0.2)
        # record is None for the first scan (no reference yet)

        # after the sweep:
        fit = tracker.fit_model()
        print(tracker.summary())

    The tracker uses phase cross-correlation (``skimage.registration``) to
    measure the pixel shift between consecutive scans, converts to nm, and
    derives drift velocity.  An exponential model ``v(t) = v₀·exp(−t/τ)`` is
    then fitted to predict when the drift will reach
    ``v_first × (1 − target_reduction)``.
    """

    def __init__(self) -> None:
        self._records: List[DriftRecord] = []
        self._prev_image: Optional[np.ndarray] = None
        self._prev_time: Optional[float] = None
        self._start_time: Optional[float] = None
        self._scan_count: int = 0
        self._feature_threshold_logged: bool = False
        self._atomic_threshold_logged: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all stored data and prepare for a new sweep."""
        self._records.clear()
        self._prev_image = None
        self._prev_time = None
        self._start_time = None
        self._scan_count = 0
        self._feature_threshold_logged = False
        self._atomic_threshold_logged = False

    @property
    def records(self) -> List[DriftRecord]:
        return list(self._records)

    @property
    def scan_count(self) -> int:
        """Total scans fed so far (including the initial reference scan)."""
        return self._scan_count

    def add_scan(
        self,
        image: np.ndarray,
        nm_per_pixel: float,
    ) -> Optional[DriftRecord]:
        """Register *image* against the previous scan and return a DriftRecord.

        Returns ``None`` for the first scan (stored as the reference).
        Silently returns ``None`` if registration fails (bad data, too small).
        """
        now = time.monotonic()
        arr = np.asarray(image, dtype=float)

        if arr.ndim != 2 or arr.size < 16:
            return None

        corrected = _plane_subtract(arr)
        # Non-finite pixels survive the (finite-fitted) plane subtraction;
        # zero-fill them so they contribute nothing to the registration
        # (the levelled data is ~zero-mean, same convention as the drift
        # estimators in scanflow.drift.estimators).
        corrected = np.nan_to_num(corrected, nan=0.0, posinf=0.0, neginf=0.0)
        self._scan_count += 1

        if self._prev_image is None:
            # First scan — store as reference, no measurement yet.
            self._prev_image = corrected
            self._prev_time = now
            self._start_time = now
            return None

        dt_s = now - self._prev_time  # type: ignore[operator]
        elapsed_s = now - self._start_time  # type: ignore[operator]

        shift = _cross_correlate(self._prev_image, corrected)
        if shift is None:
            self._prev_image = corrected
            self._prev_time = now
            return None

        # phase_cross_correlation returns the shift to apply to *mov* to align it
        # to *ref*.  If the sample drifted right (+X), the feature appears further
        # right in the new image, so the registration shift is −X.  We negate to
        # recover the physical sample-drift direction.
        dx_nm = -float(shift[1]) * float(nm_per_pixel)
        dy_nm = -float(shift[0]) * float(nm_per_pixel)

        if dt_s > 0:
            vx_nm_min = dx_nm / dt_s * 60.0
            vy_nm_min = dy_nm / dt_s * 60.0
        else:
            vx_nm_min = 0.0
            vy_nm_min = 0.0

        speed_nm_min = float(np.hypot(vx_nm_min, vy_nm_min))

        record = DriftRecord(
            scan_index=self._scan_count,
            elapsed_s=elapsed_s,
            dx_nm=dx_nm,
            dy_nm=dy_nm,
            vx_nm_min=vx_nm_min,
            vy_nm_min=vy_nm_min,
            speed_nm_min=speed_nm_min,
        )
        self._records.append(record)

        # Log one-time milestones when absolute speed thresholds are first crossed.
        if not self._feature_threshold_logged and speed_nm_min <= FEATURE_SCAN_THRESHOLD_NM_MIN:
            self._feature_threshold_logged = True
            log.info(
                "Drift milestone: speed %.3f nm/min — READY FOR FEATURE SCANNING "
                "(scan #%d, elapsed %.1f min)",
                speed_nm_min, self._scan_count, elapsed_s / 60.0,
            )
        if not self._atomic_threshold_logged and speed_nm_min <= ATOMIC_SCAN_THRESHOLD_NM_MIN:
            self._atomic_threshold_logged = True
            log.info(
                "Drift milestone: speed %.3f nm/min — READY FOR ATOMIC SCANNING "
                "(scan #%d, elapsed %.1f min)",
                speed_nm_min, self._scan_count, elapsed_s / 60.0,
            )

        self._prev_image = corrected
        self._prev_time = now
        return record

    def fit_model(self) -> Optional[DriftFit]:
        """Fit an exponential decay to the drift-speed series.

        Requires at least 3 velocity measurements.  Returns ``None`` if
        insufficient data or if all speeds are zero.
        """
        if len(self._records) < 3:
            return None

        times = np.array([r.elapsed_s for r in self._records])
        speeds = np.array([r.speed_nm_min for r in self._records])

        mask = speeds > 0
        if mask.sum() < 3:
            return None

        t = times[mask]
        v = speeds[mask]

        v_first = self._records[0].speed_nm_min
        v_last = self._records[-1].speed_nm_min
        pct_reduction = max(0.0, (v_first - v_last) / v_first * 100.0) if v_first > 0 else 0.0
        avg_scan_s = self._records[-1].elapsed_s / max(len(self._records), 1)

        fit_result = _fit_exponential(t, v)
        if fit_result is None:
            return DriftFit(
                v0_nm_min=float(v[0]),
                tau_s=float("inf"),
                r_squared=0.0,
                n_records=len(self._records),
                pct_reduction=pct_reduction,
                feature_scan_ready=v_last <= FEATURE_SCAN_THRESHOLD_NM_MIN,
                atomic_scan_ready=v_last <= ATOMIC_SCAN_THRESHOLD_NM_MIN,
            )

        v0, tau_s, r2 = fit_result

        feat_at_s, feat_at_scan = self._predict_abs_threshold(
            v0, tau_s, FEATURE_SCAN_THRESHOLD_NM_MIN,
            self._records[-1].elapsed_s, avg_scan_s,
        )
        atom_at_s, atom_at_scan = self._predict_abs_threshold(
            v0, tau_s, ATOMIC_SCAN_THRESHOLD_NM_MIN,
            self._records[-1].elapsed_s, avg_scan_s,
        )

        return DriftFit(
            v0_nm_min=v0,
            tau_s=tau_s,
            r_squared=r2,
            n_records=len(self._records),
            pct_reduction=pct_reduction,
            feature_scan_ready=v_last <= FEATURE_SCAN_THRESHOLD_NM_MIN,
            atomic_scan_ready=v_last <= ATOMIC_SCAN_THRESHOLD_NM_MIN,
            feature_scan_at_s=feat_at_s,
            feature_scan_at_scan=feat_at_scan,
            atomic_scan_at_s=atom_at_s,
            atomic_scan_at_scan=atom_at_scan,
        )

    def summary(self) -> str:
        """Multi-line human-readable drift report for the log panel."""
        if not self._records:
            return "Drift tracking: no data (only one scan acquired)."

        v_first = self._records[0].speed_nm_min
        v_last = self._records[-1].speed_nm_min
        t_total_min = self._records[-1].elapsed_s / 60.0
        cum_dx = sum(r.dx_nm for r in self._records)
        cum_dy = sum(r.dy_nm for r in self._records)

        lines = [
            f"─── Drift Report ({len(self._records)} measurements, "
            f"{self._scan_count} scans, {t_total_min:.1f} min) ───",
            f"  Speed:  {v_first:.3f} → {v_last:.3f} nm/min  "
            f"(↓ {max(0.0, (v_first - v_last) / v_first * 100.0):.1f}%)" if v_first > 0
            else f"  Speed:  {v_first:.3f} → {v_last:.3f} nm/min",
            f"  X:  {self._records[0].vx_nm_min:+.3f} → "
            f"{self._records[-1].vx_nm_min:+.3f} nm/min  |  "
            f"Y:  {self._records[0].vy_nm_min:+.3f} → "
            f"{self._records[-1].vy_nm_min:+.3f} nm/min",
            f"  Cumulative:  ΔX = {cum_dx:+.2f} nm,  ΔY = {cum_dy:+.2f} nm",
        ]

        fit = self.fit_model()
        if fit is None:
            lines.append("  Model: need ≥ 3 scans for exponential fit.")
        elif fit.tau_s == float("inf"):
            lines.append("  Model: drift is not decaying — prediction unavailable.")
            lines.append("  ── Imaging readiness ──")
            lines.append(self._readiness_line(
                "Feature scanning", FEATURE_SCAN_THRESHOLD_NM_MIN,
                fit.feature_scan_ready, None, None,
            ))
            lines.append(self._readiness_line(
                "Atomic resolution", ATOMIC_SCAN_THRESHOLD_NM_MIN,
                fit.atomic_scan_ready, None, None,
            ))
        else:
            tau_min = fit.tau_s / 60.0
            lines.append(
                f"  Model: v₀ = {fit.v0_nm_min:.3f} nm/min,  "
                f"τ = {tau_min:.1f} min,  R² = {fit.r_squared:.3f}"
            )
            lines.append("  ── Imaging readiness ──")
            lines.append(self._readiness_line(
                "Feature scanning", FEATURE_SCAN_THRESHOLD_NM_MIN,
                fit.feature_scan_ready, fit.feature_scan_at_s, fit.feature_scan_at_scan,
            ))
            lines.append(self._readiness_line(
                "Atomic resolution", ATOMIC_SCAN_THRESHOLD_NM_MIN,
                fit.atomic_scan_ready, fit.atomic_scan_at_s, fit.atomic_scan_at_scan,
            ))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _predict_abs_threshold(
        self,
        v0: float,
        tau_s: float,
        v_thr: float,
        current_elapsed_s: float,
        avg_scan_s: float,
    ) -> tuple[Optional[float], Optional[int]]:
        """Predict when drift will drop to *v_thr* using the fitted model.

        Returns (t_s, scan_number) or (None, None) if already reached or
        not predictable.
        """
        v_last = self._records[-1].speed_nm_min if self._records else 0.0
        if v_last <= v_thr or v0 <= v_thr or tau_s <= 0 or not np.isfinite(tau_s):
            return None, None
        ratio = v0 / v_thr
        if ratio <= 1.0:
            return None, None
        t_s = float(tau_s * np.log(ratio))
        remaining_s = max(0.0, t_s - current_elapsed_s)
        at_scan: Optional[int] = None
        if avg_scan_s > 0:
            at_scan = self._scan_count + int(np.ceil(remaining_s / avg_scan_s))
        return t_s, at_scan

    @staticmethod
    def _readiness_line(
        label: str,
        threshold: float,
        ready: bool,
        at_s: Optional[float],
        at_scan: Optional[int],
    ) -> str:
        if ready:
            return f"    {label} (≤ {threshold} nm/min): READY NOW"
        if at_s is not None:
            at_min = at_s / 60.0
            scan_str = f" (~scan #{at_scan})" if at_scan else ""
            return (
                f"    {label} (≤ {threshold} nm/min): "
                f"predicted at t ≈ {at_min:.1f} min{scan_str}"
            )
        return f"    {label} (≤ {threshold} nm/min): unable to predict"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _plane_subtract(arr: np.ndarray) -> np.ndarray:
    """Subtract a least-squares plane from *arr* to remove tilt before registration.

    NaN-safe: the plane is fitted on FINITE pixels only. Real rigs emit NaN
    rows in partial/interrupted frames, and fitting through them silently
    returns an all-NaN result on some LAPACKs and raises "SVD did not
    converge" on others — either way poisoning every downstream consumer
    (drift registration, auto-centering). Non-finite pixels stay non-finite
    in the output; callers decide how to fill them.
    """
    m, n = arr.shape
    r, c = np.mgrid[:m, :n]
    X = np.column_stack([np.ones(m * n), r.ravel(), c.ravel()])
    Y = arr.ravel()
    mask = np.isfinite(Y)
    if mask.sum() < 8:
        # Not enough data for a plane — best effort: remove the median.
        offset = float(np.nanmedian(arr)) if mask.any() else 0.0
        return arr - offset
    try:
        theta, *_ = np.linalg.lstsq(X[mask], Y[mask], rcond=None)
    except np.linalg.LinAlgError:
        return arr - float(np.nanmedian(arr))
    return arr - (X @ theta).reshape(m, n)


def _cross_correlate(
    ref: np.ndarray,
    mov: np.ndarray,
    upsample_factor: int = 20,
) -> Optional[np.ndarray]:
    """Return the (row, col) pixel shift between *ref* and *mov*.

    Uses phase cross-correlation for sub-pixel accuracy.  Returns ``None``
    if skimage is unavailable or the computation fails.
    """
    try:
        from skimage.registration import phase_cross_correlation
        shift, _, _ = phase_cross_correlation(
            ref, mov, upsample_factor=upsample_factor
        )
        return np.asarray(shift, dtype=float)
    except Exception:
        return None


def _fit_exponential(
    t: np.ndarray,
    v: np.ndarray,
) -> Optional[tuple[float, float, float]]:
    """Fit v(t) = v0·exp(−t/τ) and return (v0, tau_s, r²).

    Uses scipy.optimize.curve_fit with physically meaningful bounds.
    Falls back to log-linear regression if curve_fit fails.
    Returns ``None`` if both approaches fail or yield a non-decaying model.
    """
    try:
        from scipy.optimize import curve_fit

        def _model(t_, v0_, tau_):
            return v0_ * np.exp(-t_ / tau_)

        t_norm = t - t[0]
        p0 = [float(v[0]), float(np.median(t_norm)) + 1.0]
        bounds = ([0.0, 1.0], [np.inf, 86400.0])  # τ ∈ [1 s, 24 h]
        popt, _ = curve_fit(
            _model, t_norm, v,
            p0=p0, bounds=bounds, maxfev=8000,
        )
        v0, tau = float(popt[0]), float(popt[1])

    except Exception:
        # Fallback: log-linear regression
        try:
            log_v = np.log(v)
            coeffs = np.polyfit(t - t[0], log_v, 1)
            slope, intercept = float(coeffs[0]), float(coeffs[1])
            if slope >= 0:
                return None  # not decaying
            tau = -1.0 / slope
            v0 = float(np.exp(intercept))
        except Exception:
            return None

    if tau <= 0 or not np.isfinite(tau) or not np.isfinite(v0):
        return None

    # R² against the original (non-normalized) times
    t_norm = t - t[0]
    v_pred = v0 * np.exp(-t_norm / tau)
    ss_res = float(np.sum((v - v_pred) ** 2))
    ss_tot = float(np.sum((v - v.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return v0, tau, max(0.0, r2)
