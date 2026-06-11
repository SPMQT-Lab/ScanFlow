"""Tests for the contracts layer and control-core proposal validation."""

import json

import pytest

from scanflow.contracts import (
    CREATEC_SCAN_OFFSET_NM,
    IMAGE_CENTER_RELATIVE_NM,
    IMAGE_PIXELS,
    AnalysisResult,
    Feature,
    ProposedAction,
    ScanRecord,
    ValidationResult,
    require_known_frame,
)
from scanflow.automation.proposals import (
    build_validated_action,
    validate_proposed_action,
)


# ── coordinate frames ─────────────────────────────────────────────────────

def test_require_known_frame_accepts_and_rejects():
    assert require_known_frame(CREATEC_SCAN_OFFSET_NM) == CREATEC_SCAN_OFFSET_NM
    with pytest.raises(ValueError, match="unknown coordinate frame"):
        require_known_frame("some_made_up_frame")


def test_feature_requires_a_known_frame():
    Feature(feature_id="f1", x_nm=1.0, y_nm=2.0, frame=IMAGE_CENTER_RELATIVE_NM)
    with pytest.raises(ValueError):
        Feature(feature_id="f2", x_nm=1.0, y_nm=2.0, frame="pixels-ish")


def test_feature_confidence_bounds():
    with pytest.raises(ValueError, match="confidence"):
        Feature(feature_id="f", x_nm=0, y_nm=0, frame=IMAGE_PIXELS, confidence=1.5)


# ── ScanRecord <-> sidecar payload ────────────────────────────────────────

def _record(**overrides) -> ScanRecord:
    base = dict(
        session_id="sess-1",
        routine="survey",
        raw_path="scan_001.dat",
        created_at="2026-06-10T00:00:00Z",
        step_index=3,
        step_kind="scan",
        step_label="zoom",
        scan_parameters={"bias_V": 0.1, "setpoint_A": 5e-11},
        scan_offset_nm=(1.5, -2.0),
        quality={"z_stability": {"rms_pm": 1.0}},
        safety={"enabled": True},
    )
    base.update(overrides)
    return ScanRecord(**base)


def test_scan_record_payload_matches_sidecar_schema():
    payload = _record().to_payload()
    assert payload["schema"] == "scanflow.acquisition.v1"
    assert payload["record_type"] == "scanflow_scan"
    assert payload["raw_file"]["path"] == "scan_001.dat"
    assert payload["session"] == {"session_id": "sess-1", "routine": "survey"}
    assert payload["step"] == {"index": 3, "kind": "scan", "label": "zoom"}
    assert payload["position"]["scan_offset_nm"] == [1.5, -2.0]
    assert payload["position"]["coordinate_system"] == CREATEC_SCAN_OFFSET_NM
    json.dumps(payload)  # payload must be JSON-serialisable as-is


def test_scan_record_roundtrip():
    record = _record()
    again = ScanRecord.from_payload(record.to_payload())
    assert again == record


def test_scan_record_reads_legacy_payload_without_coordinate_system():
    payload = _record().to_payload()
    del payload["position"]["coordinate_system"]  # pre-contracts sidecar
    record = ScanRecord.from_payload(payload)
    assert record.coordinate_system == CREATEC_SCAN_OFFSET_NM


def test_sidecar_writer_goes_through_scan_record(tmp_path):
    """write_scan_sidecar output must parse back as a ScanRecord."""
    from scanflow.io.sidecar import write_scan_sidecar

    dat = tmp_path / "a.dat"
    dat.write_bytes(b"x")
    path = write_scan_sidecar(
        dat, session_id="s", routine="r", step_index=1,
        step_kind="scan", step_label="L",
        position_nm=(3.0, 4.0),
    )
    record = ScanRecord.from_payload(json.loads(path.read_text()))
    assert record.raw_path == "a.dat"
    assert record.scan_offset_nm == (3.0, 4.0)
    assert record.coordinate_system == CREATEC_SCAN_OFFSET_NM


# ── ProposedAction construction rules ─────────────────────────────────────

def test_proposed_action_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        ProposedAction(action_id="a", source_analysis_id="x", kind="launch_rocket")


def test_proposed_action_position_requires_frame():
    with pytest.raises(ValueError, match="frame"):
        ProposedAction(
            action_id="a", source_analysis_id="x", kind="scan_region",
            target_nm=(1.0, 2.0),  # no frame given
        )


def _good_action(**overrides) -> ProposedAction:
    base = dict(
        action_id="act-1",
        source_analysis_id="scan_001",
        kind="scan_region",
        target_nm=(10.0, -5.0),
        frame=IMAGE_CENTER_RELATIVE_NM,
        size_nm=(20.0, 20.0),
        bias_V=0.1,
        setpoint_A=50e-12,
        pixels=(256, 256),
        speed_nm_s=50.0,
        requires_operator_confirmation=False,
    )
    base.update(overrides)
    return ProposedAction(**base)


# ── control-core validation ───────────────────────────────────────────────

def test_validate_good_scan_region_passes():
    verdict = validate_proposed_action(_good_action())
    assert verdict.ok
    assert verdict.errors == []
    assert verdict.required_confirmations == []


def test_validate_rejects_zero_bias():
    verdict = validate_proposed_action(_good_action(bias_V=0.001))
    assert not verdict.ok
    assert any("constant-current guard" in e for e in verdict.errors)


def test_validate_rejects_pixel_frame_targets():
    verdict = validate_proposed_action(_good_action(frame=IMAGE_PIXELS))
    assert not verdict.ok
    assert any("frame" in e for e in verdict.errors)


def test_validate_rejects_huge_relative_target():
    verdict = validate_proposed_action(_good_action(target_nm=(9999.0, 0.0)))
    assert not verdict.ok


def test_validate_rejects_unimplemented_kinds():
    action = ProposedAction(
        action_id="a", source_analysis_id="x", kind="track_feature",
        target_nm=(1.0, 1.0), frame=CREATEC_SCAN_OFFSET_NM,
    )
    verdict = validate_proposed_action(action)
    assert not verdict.ok
    assert any("not executable" in e for e in verdict.errors)


def test_validate_records_operator_confirmation_requirement():
    verdict = validate_proposed_action(
        _good_action(requires_operator_confirmation=True)
    )
    assert verdict.ok
    assert verdict.required_confirmations == ["operator"]


def test_ignore_kind_validates_trivially():
    action = ProposedAction(action_id="a", source_analysis_id="x", kind="ignore")
    assert validate_proposed_action(action).ok


# ── ValidatedAction construction ──────────────────────────────────────────

def test_build_validated_action_produces_scan_step():
    from scanflow.automation.recipe import ScanStep
    action = _good_action()
    validated = build_validated_action(action, validate_proposed_action(action))
    assert validated.proposed_action_id == action.action_id
    assert len(validated.recipe_steps) == 1
    step = validated.recipe_steps[0]
    assert isinstance(step, ScanStep)
    assert step.bias_V == pytest.approx(0.1)
    assert step.size_nm == (20.0, 20.0)


def test_build_refuses_failed_validation():
    action = _good_action(bias_V=0.0)
    verdict = validate_proposed_action(action)
    with pytest.raises(ValueError, match="failed validation"):
        build_validated_action(action, verdict)


def test_build_refuses_outstanding_confirmation():
    action = _good_action(requires_operator_confirmation=True)
    verdict = validate_proposed_action(action)
    with pytest.raises(ValueError, match="confirmations outstanding"):
        build_validated_action(action, verdict)


def test_build_refuses_mismatched_validation():
    action = _good_action()
    other = ValidationResult(ok=True, proposed_action_id="someone-else")
    with pytest.raises(ValueError, match="does not belong"):
        build_validated_action(action, other)


# ── AnalysisResult smoke ──────────────────────────────────────────────────

def test_analysis_result_carries_features():
    result = AnalysisResult(
        analysis_id="an-1", input_scan_id="scan_001.dat",
        algorithm="threshold", algorithm_version="1.0",
        features=[Feature(feature_id="f1", x_nm=1, y_nm=2,
                          frame=IMAGE_CENTER_RELATIVE_NM, confidence=0.9)],
    )
    assert result.schema == "scanflow.analysis.v1"
    assert result.features[0].confidence == pytest.approx(0.9)
