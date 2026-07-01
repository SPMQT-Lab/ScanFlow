"""Drift-estimator baselines against the synthetic tracking scenes.

Every drift strategy (current or future) must hold these baselines —
this is the comparison harness ROADMAP §4 requires before rig use.
The two v1 estimators are deliberately complementary:

* feature_match — exact on sparse molecules, REFUSES on lattices /
  low contrast / mismatched scenes;
* phase_correlation — works on any overlapping texture (including
  lattices, where feature_match has nothing to match), refuses on
  non-overlapping or noise-floor content.

The refusal cases matter as much as the accuracy cases: a wrong drift
"correction" physically moves the scan frame the wrong way.
"""

from __future__ import annotations

import numpy as np
import pytest

from scanflow.drift import (
    ALL_ESTIMATORS,
    DriftEstimator,
    FeatureMatchDriftEstimator,
    PhaseCorrelationDriftEstimator,
)
from tests import synthetic_surfaces as syn


@pytest.fixture
def fm():
    return FeatureMatchDriftEstimator()


@pytest.fixture
def pc():
    return PhaseCorrelationDriftEstimator()


# ── accuracy baselines ────────────────────────────────────────────────────

def test_feature_match_recovers_integer_drift_exactly(fm):
    a, b, nm_per_px, _ = syn.drift_pair(drift_px=(8.0, 5.0))
    e = fm.estimate(a, b, nm_per_px)
    assert e.ok
    assert e.dx_px == pytest.approx(8.0, abs=0.1)
    assert e.dy_px == pytest.approx(5.0, abs=0.1)
    assert e.confidence > 0.8
    assert e.dx_nm == pytest.approx(e.dx_px * nm_per_px)


def test_phase_correlation_recovers_integer_drift(pc):
    a, b, nm_per_px, _ = syn.drift_pair(drift_px=(8.0, 5.0))
    e = pc.estimate(a, b, nm_per_px)
    assert e.ok
    assert e.dx_px == pytest.approx(8.0, abs=0.35)
    assert e.dy_px == pytest.approx(5.0, abs=0.35)


def test_subpixel_drift_recovered_by_both(fm, pc):
    a, b, nm_per_px, _ = syn.drift_pair(drift_px=(3.4, -2.6), seed=20)
    for est in (fm, pc):
        e = est.estimate(a, b, nm_per_px)
        assert e.ok, f"{est.name}: {e.reason}"
        assert e.dx_px == pytest.approx(3.4, abs=0.4), est.name
        assert e.dy_px == pytest.approx(-2.6, abs=0.4), est.name


def test_zero_drift_reports_zero(fm, pc):
    """No drift must not produce a phantom correction (different noise
    realisations between the frames)."""
    a, b, nm_per_px, _ = syn.drift_pair(drift_px=(0.0, 0.0), seed=30)
    for est in (fm, pc):
        e = est.estimate(a, b, nm_per_px)
        assert e.ok, f"{est.name}: {e.reason}"
        assert abs(e.dx_px) < 0.2, est.name
        assert abs(e.dy_px) < 0.2, est.name


# ── complementarity ───────────────────────────────────────────────────────

def test_lattice_feature_match_refuses_phase_correlation_succeeds(fm, pc):
    """On a bare atomic lattice there are no discrete features —
    feature_match must refuse; phase correlation recovers the shift.
    (Caveat held by design: lattice shifts are only unambiguous below
    half a lattice period of *accumulated* drift.)"""
    img, nm_per_px = syn.bare_lattice()
    shifted = np.roll(img, (5, 8), axis=(0, 1))  # dy=5, dx=8

    e_fm = fm.estimate(img, shifted, nm_per_px)
    assert not e_fm.ok
    assert "too few features" in e_fm.reason

    e_pc = pc.estimate(img, shifted, nm_per_px)
    assert e_pc.ok
    assert e_pc.dx_px == pytest.approx(8.0, abs=0.2)
    assert e_pc.dy_px == pytest.approx(5.0, abs=0.2)
    assert e_pc.confidence > 0.8


# ── refusal cases (as important as accuracy) ──────────────────────────────

def test_low_contrast_both_refuse(fm, pc):
    a, nm_per_px, positions = syn.noisy_low_contrast(seed=4)
    b, _ = syn.terrace(noise_nm=0.02, seed=41)
    for c, r in positions:
        syn.add_molecule(b, nm_per_px, c + 6, r + 4, height_nm=0.06)
    for est in (fm, pc):
        e = est.estimate(a, b, nm_per_px)
        assert not e.ok, f"{est.name} should refuse at the noise floor"
        assert e.reason


def test_unrelated_frames_both_refuse(fm, pc):
    a, nm_per_px, _ = syn.sparse_monomers(seed=1)
    b, _, _ = syn.sparse_monomers(seed=99)
    for est in (fm, pc):
        e = est.estimate(a, b, nm_per_px)
        assert not e.ok, f"{est.name} matched two unrelated frames"


def test_phase_correlation_rejects_shape_mismatch(pc):
    a, nm_per_px, _ = syn.sparse_monomers()
    e = pc.estimate(a, a[:-10, :], nm_per_px)
    assert not e.ok
    assert "shape" in e.reason


# ── interface conformance ─────────────────────────────────────────────────

def test_all_estimators_conform_to_protocol():
    assert len(ALL_ESTIMATORS) >= 2
    for cls in ALL_ESTIMATORS:
        inst = cls()
        assert isinstance(inst, DriftEstimator)
        assert inst.name and inst.version


def test_failed_estimates_are_inert():
    """A refused estimate must carry zero displacement and zero
    confidence so naive consumers cannot apply it by accident."""
    fm = FeatureMatchDriftEstimator()
    img, nm_per_px = syn.bare_lattice()
    e = fm.estimate(img, img, nm_per_px)
    assert not e.ok
    assert e.dx_px == e.dy_px == e.dx_nm == e.dy_nm == 0.0
    assert e.confidence == 0.0
