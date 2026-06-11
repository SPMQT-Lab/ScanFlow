"""Tracking test dataset — expected behaviour of feature discovery.

This is the harness ROADMAP §4 requires before any tracking/drift change:
run feature discovery (and, later, drift estimators) against the ten
deterministic scenes in tests/synthetic_surfaces.py and hold the
behaviour pinned here. If you change feature discovery, grouping, atom
tracking, survey, or mosaic centring, these tests are the first thing
that must still pass — and the place to encode any intentional change.

The assertions also document KNOWN LIMITATIONS (conservative failures we
rely on operationally):

* low-contrast molecules are MISSED, never hallucinated;
* molecules on the bright side of a step edge are missed;
* depressions (inverted polarity) are invisible to the bright-feature
  detector;
* partial/interrupted scans yield NO features (leveling breaks on the
  blank region) — callers must crop to valid rows before detection.

If a future detector improves one of these, update the corresponding
test deliberately, with rig-relevant evidence.
"""

from __future__ import annotations

import numpy as np
import pytest

from scanflow.analysis.feature_discovery import discover_features
from tests import synthetic_surfaces as syn


def _matches(cand, positions, tol_px=4.0):
    """True if a candidate centroid is within tol of any true position."""
    return any(
        abs(cand.cx_px - c) <= tol_px and abs(cand.cy_px - r) <= tol_px
        for c, r in positions
    )


# ── 1. sparse monomers ────────────────────────────────────────────────────

def test_sparse_monomers_all_found_at_true_positions():
    img, nm_per_px, positions = syn.sparse_monomers(n=10)
    cands = discover_features(img, nm_per_px)
    assert len(cands) == 10
    for cand in cands:
        assert _matches(cand, positions), (
            f"detection at ({cand.cx_px:.0f}, {cand.cy_px:.0f}) matches no "
            "true molecule (false positive)"
        )
    # every true molecule found
    found = sum(
        any(abs(c.cx_px - pc) <= 4 and abs(c.cy_px - pr) <= 4 for c in cands)
        for pc, pr in positions
    )
    assert found == 10


def test_sparse_monomer_zoom_frames_are_sensible():
    """Plan §5.6 expectation: monomer zooms land in the 3–6 nm band."""
    img, nm_per_px, _ = syn.sparse_monomers(n=10)
    for cand in discover_features(img, nm_per_px):
        assert 0.8 <= cand.char_dim_nm <= 3.0
        assert 3.0 <= cand.zoom_nm[0] <= 6.0


# ── 2. clusters ───────────────────────────────────────────────────────────

def test_clusters_merge_to_one_feature_each():
    """A 2/3/5-molecule cluster must read as ONE feature, not 2/3/5."""
    img, nm_per_px, centres = syn.clusters()
    cands = discover_features(img, nm_per_px)
    assert len(cands) == 3
    for cc, cr in centres:
        assert any(abs(c.cx_px - cc) <= 10 and abs(c.cy_px - cr) <= 10
                   for c in cands), f"cluster at ({cc:.0f}, {cr:.0f}) not found"
    # clusters are bigger than monomers and get bigger zooms
    assert all(c.char_dim_nm > 2.0 for c in cands)
    assert all(c.zoom_nm[0] > 4.0 for c in cands)


# ── 3. high density ───────────────────────────────────────────────────────

def test_high_density_respects_max_features_cap():
    img, nm_per_px = syn.high_density(n=60)
    cands = discover_features(img, nm_per_px, max_features=30)
    assert len(cands) == 30
    cands_small = discover_features(img, nm_per_px, max_features=5)
    assert len(cands_small) == 5


# ── 4. noisy low contrast ─────────────────────────────────────────────────

def test_noisy_low_contrast_fails_conservatively():
    """KNOWN LIMITATION: near the noise floor the detector finds nothing.

    The operationally important half: it must not HALLUCINATE features —
    a missed molecule wastes a zoom; a hallucinated one moves the tip to
    noise.
    """
    img, nm_per_px, positions = syn.noisy_low_contrast()
    cands = discover_features(img, nm_per_px)
    for cand in cands:  # any detection must be a real molecule
        assert _matches(cand, positions, tol_px=8.0)
    assert len(cands) <= len(positions)


# ── 5. step edge ──────────────────────────────────────────────────────────

def test_step_edge_no_giant_feature_and_lower_terrace_found():
    """The step itself must not become a feature (size filter rejects the
    bright terrace), and molecules on the dark terrace are still found.

    KNOWN LIMITATION: molecules sitting ON the bright terrace are missed
    (the terrace swallows them in the threshold) — campaign planning
    should not rely on counts from stepped frames.
    """
    img, nm_per_px, positions = syn.step_edge()
    cands = discover_features(img, nm_per_px)
    assert all(c.char_dim_nm <= 20.0 for c in cands)  # no terrace-sized blob
    lower_terrace = positions[:2]   # the two on the dark terrace
    for pc, pr in lower_terrace:
        assert any(abs(c.cx_px - pc) <= 4 and abs(c.cy_px - pr) <= 4
                   for c in cands)
    for cand in cands:              # nothing detected that isn't a molecule
        assert _matches(cand, positions)


# ── 6. bare atomic lattice ────────────────────────────────────────────────

def test_bare_lattice_yields_no_features():
    """Atomic corrugation must not register as molecules."""
    img, nm_per_px = syn.bare_lattice()
    assert discover_features(img, nm_per_px) == []


# ── 7. molecules at the frame edge ────────────────────────────────────────

def test_edge_molecules_rejected_interior_kept():
    """Plan §5.6: truncated edge features are rejected (their zoom would
    clip); interior molecules are unaffected."""
    img, nm_per_px, edge, interior = syn.edge_molecules()
    cands = discover_features(img, nm_per_px)
    assert len(cands) == len(interior)
    for pc, pr in interior:
        assert any(abs(c.cx_px - pc) <= 4 and abs(c.cy_px - pr) <= 4
                   for c in cands)
    for cand in cands:
        assert not _matches(cand, edge, tol_px=12.0)


# ── 8. drifted repeat scans ───────────────────────────────────────────────

def test_drift_pair_displacement_recovered():
    """Baseline for every drift estimator: matching detections between two
    scans of the same region must recover the injected drift to sub-pixel
    accuracy. Any future drift-correction strategy should beat or match
    this nearest-neighbour baseline on this scene.
    """
    drift = (8.0, 5.0)
    img_a, img_b, nm_per_px, _ = syn.drift_pair(drift_px=drift)
    before = discover_features(img_a, nm_per_px)
    after = discover_features(img_b, nm_per_px)
    assert len(before) == len(after) == 8

    shifts = []
    for f in before:
        nearest = min(after,
                      key=lambda g: (g.cx_px - f.cx_px) ** 2 + (g.cy_px - f.cy_px) ** 2)
        shifts.append((nearest.cx_px - f.cx_px, nearest.cy_px - f.cy_px))
    median = np.median(np.asarray(shifts), axis=0)
    assert median[0] == pytest.approx(drift[0], abs=0.5)
    assert median[1] == pytest.approx(drift[1], abs=0.5)


# ── 9. inverted polarity ──────────────────────────────────────────────────

def test_inverted_polarity_finds_nothing():
    """KNOWN LIMITATION: the detector is bright-feature-only. Depressions
    (wrong bias polarity / contrast) are invisible — it must return
    nothing rather than detect noise between the holes."""
    img, nm_per_px = syn.inverted_polarity()
    assert discover_features(img, nm_per_px) == []


# ── 10. partial / interrupted scan ────────────────────────────────────────

def test_partial_scan_no_crash_no_garbage():
    """KNOWN LIMITATION: a partially blank frame defeats plane leveling, so
    detection yields nothing — acceptable (fail-empty), but it must never
    crash or return features in the blank region. Callers wanting
    detections from partial frames must crop to the scanned rows first.
    """
    img, nm_per_px, valid_positions = syn.partial_scan(blank_from_row=200)
    cands = discover_features(img, nm_per_px)  # must not raise
    for cand in cands:
        assert cand.cy_px < 200, "feature reported inside the blank region"

    # Cropping to the scanned region recovers the molecules.
    cropped = img[:200, :]
    recovered = discover_features(cropped, nm_per_px, edge_margin_px=8)
    found = sum(
        any(abs(c.cx_px - pc) <= 4 and abs(c.cy_px - pr) <= 4 for c in recovered)
        for pc, pr in valid_positions
    )
    assert found >= max(1, len(valid_positions) - 2)
