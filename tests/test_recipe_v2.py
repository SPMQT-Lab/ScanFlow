"""Tests for recipe v2 — mixed step types and YAML round-trip."""

import pytest

from scanflow.automation import (
    MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep,
)


def test_mixed_recipe_builds():
    r = MeasurementRecipe(name="mixed")
    r.add_step(ScanStep(bias_V=0.1, setpoint_A=50e-12, label="overview"))
    r.add_step(SpectroscopyStep(positions=[(50, 50), (100, 100)], label="spec"))
    r.add_step(WaitStep(seconds=30.0, label="settle"))
    r.add_step(ApproachStep(label="re-approach"))
    r.add_step(ScanStep(bias_V=0.05, setpoint_A=20e-12, label="overview2"))

    assert len(r.steps) == 5
    assert [s.kind for s in r.steps] == [
        "scan", "spectroscopy", "wait", "approach", "scan",
    ]
    assert r.total_steps() == 5


def test_mixed_yaml_roundtrip(tmp_path):
    r = MeasurementRecipe(name="mix")
    r.add_step(ScanStep(bias_V=0.1, setpoint_A=50e-12))
    r.add_step(SpectroscopyStep(positions=[(10, 20), (30, 40)],
                                bias_start_V=-0.5, bias_end_V=0.5))
    r.add_step(WaitStep(seconds=15.0))
    r.add_step(ApproachStep(burst_count=2))

    path = tmp_path / "mix.yaml"
    r.save(path)
    r2 = MeasurementRecipe.load(path)

    assert [s.kind for s in r2.steps] == ["scan", "spectroscopy", "wait", "approach"]
    spec = r2.steps[1]
    assert spec.positions == [(10, 20), (30, 40)]
    assert spec.bias_start_V == pytest.approx(-0.5)
    wait = r2.steps[2]
    assert wait.seconds == pytest.approx(15.0)
    appr = r2.steps[3]
    assert appr.burst_count == 2


def test_scan_then_spec_builder():
    r = MeasurementRecipe.scan_then_spec(
        scan_bias_V=0.1,
        scan_setpoint_A=50e-12,
        spec_positions=[(64, 64), (128, 128)],
    )
    assert [s.kind for s in r.steps] == ["scan", "spectroscopy", "scan"]
    assert r.steps[1].positions == [(64, 64), (128, 128)]


def test_v1_recipe_still_loads(tmp_path):
    """A recipe.yaml from before v2 (no kind field) must still load."""
    text = """\
name: legacy
steps:
- bias_V: 0.1
  setpoint_A: 5.0e-11
  size_nm: [50.0, 50.0]
  speed_nm_s: 50.0
  pixels: [256, 256]
  rotation_deg: 0.0
  const_height: false
  channels: [TOPOGRAPHY, CURRENT]
  preamp_exponent: 9
  settling_s: 0.0
  label: ''
  memo: ''
drift_correction: true
drift_channel: 0
drift_reposition_delay_s: 3.0
drift_template: ''
repetitions: 1
inter_step_delay_s: 0.0
save_folder: ''
suppress_dst_change: true
stop_on_error: true
"""
    path = tmp_path / "legacy.yaml"
    path.write_text(text)
    r = MeasurementRecipe.load(path)
    assert len(r.steps) == 1
    assert r.steps[0].kind == "scan"
    assert r.steps[0].bias_V == pytest.approx(0.1)
