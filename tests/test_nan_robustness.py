"""NaN / bad-readback robustness — the historical crash & freeze class.

Real rigs produce NaN rows in partial frames, NaN ADC readings, and
empty-string parameter readbacks while STMAFM is busy. The failure modes
this pins:

* a NaN current reading must count as a FAILED safety reading (NaN passes
  every ``abs(I) > threshold`` comparison, silently disabling protection);
* the plane fit must never see NaNs (poisons all detections on one
  LAPACK, raises "SVD did not converge" on the lab PC's — the historical
  "NaN crashes the program");
* drift estimators must never be confidently wrong on NaN input;
* one NaN Z sample must not poison the rolling stability statistics;
* '' / garbage parameter readbacks must not crash automation mid-run.
"""

from __future__ import annotations

import numpy as np
import pytest

from scanflow.analysis.feature_discovery import _level_correct, discover_features
from scanflow.automation.scan_metrics import compute_z_stability
from scanflow.core import STMClient, SafetyConfig, SafetyMonitor
from scanflow.drift import FeatureMatchDriftEstimator, PhaseCorrelationDriftEstimator
from tests import synthetic_surfaces as syn


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def _partial_nan_scene():
    """Molecules in the top region, NaN rows below (interrupted frame)."""
    img, nm_per_px, positions = syn.sparse_monomers(n=10, seed=11, margin_px=60)
    img[300:, :] = np.nan
    valid = [(c, r) for c, r in positions if r < 280]
    return img, nm_per_px, valid


# ── safety: NaN current must fail closed ──────────────────────────────────

def test_nan_adc_reading_is_a_failed_reading(stm):
    monitor = SafetyMonitor(SafetyConfig(warn_read_failures=2,
                                         max_read_failures=3))
    stm.raw.mock_current_override_V = float("nan")
    stm.scan.live_data = lambda **_kw: None  # disable the fallback too

    for _ in range(2):
        status = monitor.check(stm)
        assert status.read_failed is True
        assert status.measured_current_A is None
    status = monitor.check(stm)  # 3rd consecutive → fail closed
    assert status.ok is False


def test_nan_adc_falls_back_to_live_data(stm):
    """With the frame fallback available, a NaN ADC reading still yields a
    real (finite) current measurement."""
    monitor = SafetyMonitor()
    stm.raw.mock_current_override_V = float("nan")
    value = monitor.measure_current_A(stm)
    assert value is not None and np.isfinite(value)


def test_nan_rows_in_fallback_frame_are_ignored(stm):
    monitor = SafetyMonitor()
    stm.raw.mock_current_override_V = float("nan")  # force fallback
    real_live = stm.scan.live_data

    def nan_rows_live(**kw):
        arr = np.asarray(real_live(**kw), dtype=float)
        flat = arr.ravel()
        flat[: flat.size // 2] = np.nan
        return flat
    stm.scan.live_data = nan_rows_live
    value = monitor.measure_current_A(stm)
    assert value is not None and np.isfinite(value)


# ── analysis: NaN frames neither crash nor go blind ───────────────────────

def test_level_correct_fits_plane_on_finite_pixels_only():
    img, _, _ = _partial_nan_scene()
    levelled = _level_correct(img)
    finite = np.isfinite(levelled)
    assert finite[:300].all(), "valid region must stay finite"
    assert not finite[300:].any(), "NaN region must stay NaN (no invention)"
    # the valid region is actually levelled (tilt removed)
    assert abs(float(np.mean(levelled[:280]))) < 0.05


def test_level_correct_survives_all_nan_and_empty_variants():
    assert np.isfinite(_level_correct(np.zeros((8, 8)))).all()
    out = _level_correct(np.full((8, 8), np.nan))
    assert out.shape == (8, 8)  # no exception is the contract here


def test_discover_features_finds_valid_region_despite_nan_rows():
    """Regression: NaN rows used to poison the plane fit and kill ALL
    detections (or crash, depending on LAPACK)."""
    img, nm_per_px, valid = _partial_nan_scene()
    cands = discover_features(img, nm_per_px)
    found = sum(
        any(abs(c.cx_px - pc) <= 4 and abs(c.cy_px - pr) <= 4 for c in cands)
        for pc, pr in valid
    )
    assert found >= max(1, len(valid) - 2)
    for c in cands:
        assert c.cy_px < 300, "feature reported inside the NaN region"


def test_z_stability_metrics_finite_on_nan_frames():
    img, _, _ = _partial_nan_scene()
    metrics = compute_z_stability(img)
    assert np.isfinite(metrics["rms_pm"])
    assert np.isfinite(metrics["max_pm"])


# ── drift: never confidently wrong on NaN input ───────────────────────────

def test_drift_estimators_handle_nan_frames():
    img_a, img_b, nm_per_px, _ = syn.drift_pair(drift_px=(8.0, 5.0), seed=12)
    img_a = img_a.copy(); img_a[400:, :] = np.nan
    img_b = img_b.copy(); img_b[400:, :] = np.nan
    for est in (FeatureMatchDriftEstimator(), PhaseCorrelationDriftEstimator()):
        e = est.estimate(img_a, img_b, nm_per_px)
        # ok with a finite, roughly-correct answer — or an honest refusal.
        assert np.isfinite(e.dx_px) and np.isfinite(e.dy_px)
        assert np.isfinite(e.confidence)
        if e.ok:
            assert e.dx_px == pytest.approx(8.0, abs=1.0), est.name
            assert e.dy_px == pytest.approx(5.0, abs=1.0), est.name


def test_phase_correlation_refuses_all_nan():
    blank = np.full((64, 64), np.nan)
    e = PhaseCorrelationDriftEstimator().estimate(blank, blank, 0.1)
    assert not e.ok
    assert e.dx_px == 0.0 and e.dy_px == 0.0


# ── instrument readback parsing: '' must not crash automation ─────────────

def test_scan_read_survives_empty_and_garbage_readbacks(stm):
    stm.setp("SCAN.BIASVOLTAGE.VOLT", "")
    stm.setp("SCAN.SETPOINT.AMPERE", None)
    stm.setp("SCAN.SPEED.NM/SEC", "notanumber")
    stm.setp("SCAN.IMAGESIZE.NM.X", "")
    stm.setp("SCAN.ROTATION.DEG", float("nan"))
    params = stm.scan.read()  # must not raise
    assert params.bias_V == 0.0
    assert params.setpoint_A == 0.0
    assert params.speed_nm_s == 0.0
    assert params.size_nm[0] == 0.0
    assert params.rotation_deg == 0.0


# ── monitors: one NaN sample must not poison the statistics ───────────────

def test_z_monitor_skips_non_finite_samples():
    from scanflow.core.z_monitor import ZMonitor

    class _FakeRaw:
        def __init__(self): self.values = iter([1.0, float("nan"), 2.0])
        def getdacvalfb(self): return next(self.values)

    class _FakeSTM:
        def __init__(self): self.raw = _FakeRaw()

    mon = ZMonitor(_FakeSTM(), interval_s=1.0)
    for _ in range(3):
        mon._poll()
    ts, zs = mon.get_samples()
    assert len(zs) == 2                       # NaN sample dropped
    assert np.isfinite(zs).all()
    stats = mon.window_stats(3600)
    assert np.isfinite(stats["ptp_A"])
    assert np.isfinite(stats["drift_A_per_h"])
