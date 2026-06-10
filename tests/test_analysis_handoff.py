"""End-to-end analysis/ML hand-off: the full contract chain on mock data.

This test IS the worked example for analysis/ML contributors
(docs/analysis_ml_handoff.md). It exercises the entire
long_term_architecture §6 flow without any instrument:

    acquisition (mock) -> sidecar -> ScanRecord
        -> detector -> AnalysisResult (JSON round-trip on disk)
        -> planner -> ProposedAction (JSON round-trip)
        -> control validation -> operator approval -> ValidatedAction
        -> MeasurementRecipe

Execution of the recipe (with motion to each target) is the Phase 3
executor deliverable; the chain is asserted up to the runnable recipe.
"""

from __future__ import annotations

import json

import pytest

from scanflow.analysis import ThresholdFeatureDetector, ZoomFeaturesPlanner
from scanflow.automation.proposals import (
    build_validated_action,
    validate_proposed_action,
    validated_actions_to_recipe,
)
from scanflow.contracts import (
    IMAGE_CENTER_RELATIVE_NM,
    AnalysisResult,
    ProposedAction,
    ScanRecord,
)
from scanflow.io.analysis_artifacts import (
    analysis_result_path,
    read_analysis_result,
    write_analysis_result,
)
from scanflow.io.sidecar import write_scan_sidecar
from tests import synthetic_surfaces as syn


def test_full_handoff_chain(tmp_path):
    # ── 1. Acquisition side: a scan exists with its sidecar ──────────
    dat = tmp_path / "wide_001.dat"
    dat.write_bytes(b"fake-createc-dat")
    sidecar_path = write_scan_sidecar(
        dat,
        session_id="sess-handoff",
        routine="survey",
        step_index=1,
        step_kind="scan",
        step_label="wide",
        position_nm=(10.0, -5.0),
    )

    # ── 2. Analysis side reads the ScanRecord (not ad-hoc JSON) ──────
    record = ScanRecord.from_payload(json.loads(sidecar_path.read_text()))
    assert record.raw_path == "wide_001.dat"
    assert record.coordinate_system == "createc_scan_offset_nm"

    # ── 3. Detector emits a contract AnalysisResult ──────────────────
    image, nm_per_px, true_positions = syn.sparse_monomers(n=10)
    detector = ThresholdFeatureDetector()
    result = detector.detect(image, nm_per_px, input_scan_id=record.raw_path)
    assert result.algorithm == "bright_feature_threshold"
    assert len(result.features) == 10
    for feature in result.features:
        assert feature.frame == IMAGE_CENTER_RELATIVE_NM
        assert feature.source_version == detector.version

    # positions are physical: centre-relative nm must map back to pixels
    ny, nx = image.shape
    for feature in result.features:
        col = feature.x_nm / nm_per_px + nx / 2.0
        row = feature.y_nm / nm_per_px + ny / 2.0
        assert any(abs(col - c) <= 4 and abs(row - r) <= 4
                   for c, r in true_positions)

    # ── 4. Cross-process boundary: result round-trips through disk ───
    artifact = write_analysis_result(result, dat)
    assert artifact == analysis_result_path(dat)
    loaded = read_analysis_result(artifact)
    assert loaded == result

    # ── 5. Planner proposes follow-ups (suggestions only) ────────────
    planner = ZoomFeaturesPlanner(
        bias_V=0.1, setpoint_A=50e-12, max_proposals=5,
    )
    proposals = planner.propose(loaded)
    assert len(proposals) == 5
    for p in proposals:
        assert p.kind == "scan_region"
        assert p.frame == IMAGE_CENTER_RELATIVE_NM
        assert p.requires_operator_confirmation is True
        assert 3.0 <= p.size_nm[0] <= 30.0
        # proposals also survive the disk boundary
        assert ProposedAction.from_payload(p.to_payload()) == p

    # ── 6. Control core validates; approval is required ──────────────
    first = proposals[0]
    verdict = validate_proposed_action(first)
    assert verdict.ok
    assert verdict.required_confirmations == ["operator"]
    with pytest.raises(ValueError, match="confirmations outstanding"):
        build_validated_action(first, verdict)

    # ── 7. Operator approves -> ValidatedAction -> runnable recipe ───
    approved = ProposedAction.from_payload(
        {**first.to_payload(), "requires_operator_confirmation": False}
    )
    verdict = validate_proposed_action(approved)
    assert verdict.ok and not verdict.required_confirmations
    validated = build_validated_action(approved, verdict)
    recipe = validated_actions_to_recipe([validated], name="handoff zooms")
    assert recipe.total_steps() == 1
    assert recipe.validate(mode="mock") == []


def test_unvalidated_actions_cannot_become_a_recipe():
    from scanflow.contracts import ValidatedAction, ValidationResult
    bogus = ValidatedAction(
        action_id="x", proposed_action_id="y",
        validation=ValidationResult(ok=False, proposed_action_id="y",
                                    errors=["nope"]),
    )
    with pytest.raises(ValueError, match="not validated"):
        validated_actions_to_recipe([bogus])


def test_detector_warns_instead_of_hallucinating(tmp_path):
    """Contract behaviour on a scene the detector cannot handle."""
    image, nm_per_px = syn.inverted_polarity()
    result = ThresholdFeatureDetector().detect(image, nm_per_px)
    assert result.features == []
    assert result.warnings  # explicit, machine-readable "found nothing"


def test_planner_respects_pixel_frame_rejection():
    """A proposal whose target is in pixels must die at validation."""
    result = AnalysisResult(
        analysis_id="an", input_scan_id="s", algorithm="x",
        algorithm_version="1",
    )
    # hand-build a pixel-frame feature (a sloppy future detector)
    from scanflow.contracts import Feature, IMAGE_PIXELS
    result.features = [Feature(feature_id="f1", x_nm=10, y_nm=20,
                               frame=IMAGE_PIXELS)]
    proposals = ZoomFeaturesPlanner(bias_V=0.1, setpoint_A=50e-12).propose(result)
    assert len(proposals) == 1
    verdict = validate_proposed_action(proposals[0])
    assert not verdict.ok
    assert any("frame" in e for e in verdict.errors)
