"""Tests for the Z-stability metric."""

from __future__ import annotations

import numpy as np

from scanflow.automation.scan_metrics import compute_z_stability, format_z_stability


def _smooth_topo(size: int = 64, slope_nm: float = 0.5) -> np.ndarray:
    """Pure topographic gradient — no noise. Should report ~0 pm RMS after detrend."""
    x = np.linspace(0, slope_nm, size)
    return np.tile(x, (size, 1))


def test_smooth_scan_reports_excellent_stability():
    arr = _smooth_topo()
    m = compute_z_stability(arr)
    assert m["rms_pm"] < 1.0   # essentially noiseless
    assert m["jumps"] == 0
    assert m["rating"] == "excellent"


def test_noisy_scan_inflates_rms():
    rng = np.random.default_rng(0)
    arr = _smooth_topo() + rng.normal(scale=0.020, size=(64, 64))  # 20 pm white noise
    m = compute_z_stability(arr)
    # Expect ~20 pm RMS — comfortably in 'good' / 'noisy' band
    assert 10 < m["rms_pm"] < 60
    assert m["rating"] in {"good", "noisy"}


def test_line_jump_inflates_max_and_jumps():
    arr = _smooth_topo() + 0.001 * np.random.default_rng(1).normal(size=(64, 64))
    # Add a single big jump on one line
    arr[30] += np.random.default_rng(2).normal(scale=1.0, size=64)
    m = compute_z_stability(arr)
    assert m["max_pm"] > 10 * m["rms_pm"]   # at least one line is 10× worse
    assert m["jumps"] >= 1


def test_invalid_inputs_return_empty():
    assert compute_z_stability(np.array([])).get("rating") == "n/a"
    assert compute_z_stability(np.zeros(4)).get("rating") == "n/a"  # 1-D
    assert compute_z_stability(None).get("rating") == "n/a"


def test_format_is_readable():
    arr = _smooth_topo()
    msg = format_z_stability(compute_z_stability(arr))
    assert "Z stability" in msg
    assert "pm RMS" in msg
    assert "excellent" in msg
