"""Tests for preview follow-up scan timing and the numpy-import regression."""

import pytest

from scanflow.core.scan import (
    ScanParams,
    estimate_scan_duration_s,
    speed_for_target_duration_s,
)


def test_speed_for_target_duration_inverts_estimate():
    """The computed speed must make the scan take exactly target_s."""
    size_nm, pixels, target_s = 5.6, 256, 300.0
    speed = speed_for_target_duration_s(size_nm, pixels, target_s)
    params = ScanParams(
        bias_V=0.1, setpoint_A=1e-10,
        size_nm=(size_nm, size_nm), speed_nm_s=speed, pixels=(pixels, pixels),
    )
    assert estimate_scan_duration_s(params) == pytest.approx(target_s, rel=1e-6)


def test_speed_for_target_duration_clamped():
    # Huge frame in a short time → speed clamped to max, not unbounded.
    assert speed_for_target_duration_s(1000.0, 512, 1.0) == 1000.0
    # Absurdly slow request → clamped to min.
    assert speed_for_target_duration_s(1.0, 64, 1e9) == 0.5


def test_followup_worker_imports_numpy():
    """Regression: auto-center used np.asarray without importing numpy,
    which crashed the whole follow-up batch on the first feature
    (lab log 2026-06-16, 'name np is not defined')."""
    import scanflow.automation.workers.preview_followup as mod
    assert hasattr(mod, "np"), "numpy must be importable in the worker module"
    # The auto-center branch references np.asarray — ensure the name resolves.
    assert mod.np.asarray([[1.0, 2.0]]).ndim == 2


def test_followup_worker_accepts_timing_params():
    from scanflow.automation.workers.preview_followup import FeatureScanWorker
    from scanflow.core import STMClient

    w = FeatureScanWorker(
        STMClient(), "src.dat", [],
        pixels=512, target_duration_s=300.0,
    )
    assert w._pixels == 512
    assert w._target_duration_s == 300.0
