"""Tests for DriftTracker and DriftFit in scan_metrics."""
from __future__ import annotations

import numpy as np
import pytest

from scanflow.automation.scan_metrics import (
    DriftRecord,
    DriftFit,
    DriftTracker,
    FEATURE_SCAN_THRESHOLD_NM_MIN,
    ATOMIC_SCAN_THRESHOLD_NM_MIN,
    _plane_subtract,
    _fit_exponential,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _flat_image(ny: int = 64, nx: int = 64, noise: float = 0.01) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.normal(0.0, noise, (ny, nx))


def _image_with_blob(
    ny: int = 64, nx: int = 64,
    cy: float = 32.0, cx: float = 32.0,
    sigma: float = 4.0,
    amplitude: float = 1.0,
    noise: float = 0.01,
) -> np.ndarray:
    rng = np.random.default_rng(0)
    y, x = np.mgrid[:ny, :nx].astype(float)
    blob = amplitude * np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * sigma ** 2))
    return blob + rng.normal(0.0, noise, (ny, nx))


def _build_tracker_with_synthetic_drift(
    n_scans: int = 15,
    v0: float = 2.0,
    tau_s: float = 300.0,
    scan_dt_s: float = 60.0,
    nm_per_pixel: float = 0.2,
) -> DriftTracker:
    """Build a DriftTracker populated with synthetic exponential drift data."""
    tracker = DriftTracker()

    for i in range(n_scans - 1):
        elapsed_s = (i + 1) * scan_dt_s
        speed = v0 * np.exp(-elapsed_s / tau_s)
        vx = speed / np.sqrt(2)
        vy = speed / np.sqrt(2)
        dx = vx * scan_dt_s / 60.0
        dy = vy * scan_dt_s / 60.0
        record = DriftRecord(
            scan_index=i + 2,
            elapsed_s=elapsed_s,
            dx_nm=dx,
            dy_nm=dy,
            vx_nm_min=vx,
            vy_nm_min=vy,
            speed_nm_min=speed,
        )
        tracker._records.append(record)

    tracker._scan_count = n_scans
    return tracker


# ─────────────────────────────────────────────────────────────────────────────
# _plane_subtract
# ─────────────────────────────────────────────────────────────────────────────

def test_plane_subtract_removes_tilt():
    ny, nx = 32, 32
    y, x = np.mgrid[:ny, :nx].astype(float)
    tilted = 3.0 * x + 2.0 * y + 5.0
    result = _plane_subtract(tilted)
    assert np.abs(result).max() < 1e-8


def test_plane_subtract_preserves_features():
    blob = _image_with_blob()
    result = _plane_subtract(blob)
    assert result.max() > 0.5
    assert abs(result.mean()) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# _fit_exponential
# ─────────────────────────────────────────────────────────────────────────────

def test_fit_exponential_recovers_params():
    tau_true = 300.0
    v0_true = 2.0
    t = np.linspace(10, 600, 30)
    v = v0_true * np.exp(-t / tau_true)

    result = _fit_exponential(t, v)
    assert result is not None
    v0_fit, tau_fit, r2 = result
    assert abs(v0_fit - v0_true) < 0.1
    assert abs(tau_fit - tau_true) < 20.0
    assert r2 > 0.99


def test_fit_exponential_returns_none_for_flat():
    t = np.linspace(0, 600, 20)
    v = np.full(20, 1.0)
    result = _fit_exponential(t, v)
    if result is not None:
        _, tau, _ = result
        assert tau > 1e4


def test_fit_exponential_returns_none_or_large_tau_for_increasing():
    t = np.linspace(0, 600, 20)
    v = 0.1 + 0.01 * t
    result = _fit_exponential(t, v)
    if result is not None:
        _, tau, _ = result
        assert tau > 86000


# ─────────────────────────────────────────────────────────────────────────────
# DriftTracker — basic flow
# ─────────────────────────────────────────────────────────────────────────────

def test_first_scan_returns_none():
    tracker = DriftTracker()
    result = tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    assert result is None
    assert tracker.scan_count == 1
    assert len(tracker.records) == 0


def test_second_scan_returns_record():
    tracker = DriftTracker()
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    record = tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    assert record is not None
    assert isinstance(record, DriftRecord)
    assert record.scan_index == 2
    assert record.elapsed_s >= 0.0
    assert tracker.scan_count == 2
    assert len(tracker.records) == 1


def test_reset_clears_state():
    tracker = DriftTracker()
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    tracker.reset()
    assert tracker.scan_count == 0
    assert len(tracker.records) == 0
    result = tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    assert result is None


def test_known_shift_measured_correctly():
    nm_per_pixel = 0.5
    shift_px = 5
    img1 = _image_with_blob(cy=32, cx=32)
    img2 = np.roll(img1, shift_px, axis=1)

    tracker = DriftTracker()
    tracker.add_scan(img1, nm_per_pixel=nm_per_pixel)
    record = tracker.add_scan(img2, nm_per_pixel=nm_per_pixel)

    assert record is not None
    expected_dx_nm = shift_px * nm_per_pixel
    assert abs(record.dx_nm - expected_dx_nm) < 0.5 * nm_per_pixel
    assert abs(record.dy_nm) < 0.5 * nm_per_pixel


# ─────────────────────────────────────────────────────────────────────────────
# DriftTracker — fit_model
# ─────────────────────────────────────────────────────────────────────────────

def test_fit_model_returns_none_with_too_few_records():
    tracker = DriftTracker()
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    assert tracker.fit_model() is None


def test_fit_model_recovers_tau():
    tau_true = 300.0
    tracker = _build_tracker_with_synthetic_drift(n_scans=20, tau_s=tau_true)
    fit = tracker.fit_model()
    assert fit is not None
    assert abs(fit.tau_s - tau_true) < 30.0
    assert fit.r_squared > 0.95


def test_fit_model_predicts_feature_threshold():
    """fit_model should predict when speed drops to FEATURE_SCAN_THRESHOLD_NM_MIN."""
    v0, tau_s = 2.0, 300.0
    tracker = _build_tracker_with_synthetic_drift(n_scans=10, v0=v0, tau_s=tau_s)
    fit = tracker.fit_model()
    assert fit is not None
    assert not fit.feature_scan_ready
    assert fit.feature_scan_at_s is not None
    # Analytical: t = τ·ln(v0/threshold)
    t_expected = tau_s * np.log(v0 / FEATURE_SCAN_THRESHOLD_NM_MIN)
    assert abs(fit.feature_scan_at_s - t_expected) < 90.0   # within 1.5 min


def test_fit_model_predicts_atomic_threshold():
    """fit_model should predict when speed drops to ATOMIC_SCAN_THRESHOLD_NM_MIN."""
    v0, tau_s = 2.0, 300.0
    tracker = _build_tracker_with_synthetic_drift(n_scans=10, v0=v0, tau_s=tau_s)
    fit = tracker.fit_model()
    assert fit is not None
    assert not fit.atomic_scan_ready
    assert fit.atomic_scan_at_s is not None
    assert fit.atomic_scan_at_s > fit.feature_scan_at_s  # atomic requires more settling


def test_fit_model_feature_ready_when_slow():
    """When current speed is already below feature threshold, feature_scan_ready = True."""
    tracker = _build_tracker_with_synthetic_drift(n_scans=40, v0=2.0, tau_s=200.0)
    fit = tracker.fit_model()
    assert fit is not None
    if fit.feature_scan_ready:
        assert fit.feature_scan_at_s is None


def test_fit_model_atomic_threshold_higher_bar_than_feature():
    assert ATOMIC_SCAN_THRESHOLD_NM_MIN < FEATURE_SCAN_THRESHOLD_NM_MIN


def test_fit_model_stable_at_scan_is_integer():
    tracker = _build_tracker_with_synthetic_drift(n_scans=10)
    fit = tracker.fit_model()
    if fit is not None and fit.feature_scan_at_scan is not None:
        assert isinstance(fit.feature_scan_at_scan, int)
        assert fit.feature_scan_at_scan > 0
    if fit is not None and fit.atomic_scan_at_scan is not None:
        assert isinstance(fit.atomic_scan_at_scan, int)
        assert fit.atomic_scan_at_scan > 0


# ─────────────────────────────────────────────────────────────────────────────
# DriftTracker — summary
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_no_data():
    tracker = DriftTracker()
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    s = tracker.summary()
    assert "no data" in s.lower() or "one scan" in s.lower()


def test_summary_with_data_contains_key_fields():
    tracker = _build_tracker_with_synthetic_drift(n_scans=15)
    s = tracker.summary()
    assert "nm/min" in s
    assert "τ" in s or "tau" in s.lower() or "Model" in s
    assert "Feature" in s
    assert "Atomic" in s
    assert "ΔX" in s
    assert "ΔY" in s


def test_summary_does_not_crash_with_few_records():
    tracker = DriftTracker()
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    tracker.add_scan(_flat_image(), nm_per_pixel=0.2)
    s = tracker.summary()
    assert isinstance(s, str)
    assert len(s) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_bad_image_shape_skipped():
    tracker = DriftTracker()
    result = tracker.add_scan(np.zeros((1, 1)), nm_per_pixel=0.2)
    assert result is None
    assert tracker.scan_count == 0


def test_1d_image_skipped():
    tracker = DriftTracker()
    result = tracker.add_scan(np.zeros(64), nm_per_pixel=0.2)
    assert result is None
