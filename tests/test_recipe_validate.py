"""Tests for MeasurementRecipe.validate() pre-flight checks."""

from scanflow.automation.recipe import (
    MeasurementRecipe, ScanStep, TipFormStep, MIN_CONST_CURRENT_BIAS_V,
)


def _errors(issues):
    return [i for i in issues if i.startswith("ERROR")]


def _warnings(issues):
    return [i for i in issues if i.startswith("WARNING")]


def test_validate_clean_recipe_has_no_issues():
    r = MeasurementRecipe.bias_ramp(
        start_V=-1.0, end_V=1.0, steps=11, setpoint_A=50e-12,
    )
    assert r.validate(mode="live") == []


def test_validate_empty_recipe_is_error():
    r = MeasurementRecipe()
    assert _errors(r.validate())


def test_validate_flags_const_current_near_zero_bias():
    r = MeasurementRecipe()
    r.add_step(ScanStep(bias_V=0.001, setpoint_A=50e-12))
    errs = _errors(r.validate())
    assert len(errs) == 1
    assert "crash the tip" in errs[0]
    # Constant-height at the same bias is allowed
    r2 = MeasurementRecipe()
    r2.add_step(ScanStep(bias_V=0.001, setpoint_A=50e-12, const_height=True))
    assert _errors(r2.validate()) == []


def test_validate_flags_bias_out_of_range():
    r = MeasurementRecipe()
    r.add_step(ScanStep(bias_V=12.0, setpoint_A=50e-12))
    assert any("±10 V" in e for e in _errors(r.validate()))


def test_validate_flags_nonpositive_setpoint():
    r = MeasurementRecipe()
    r.add_step(ScanStep(bias_V=0.1, setpoint_A=0.0))
    assert any("setpoint" in e for e in _errors(r.validate()))


def test_validate_warns_on_disabled_safety_live_only():
    r = MeasurementRecipe()
    r.add_step(ScanStep(bias_V=0.1, setpoint_A=50e-12))
    r.safety_enable = False
    assert any("DISABLED" in w for w in _warnings(r.validate(mode="live")))
    assert not any("DISABLED" in w for w in _warnings(r.validate(mode="mock")))


def test_validate_warns_on_tip_form_step():
    r = MeasurementRecipe()
    r.add_step(TipFormStep())
    assert any("tip-form" in w for w in _warnings(r.validate()))


def test_validate_warns_on_very_long_run():
    r = MeasurementRecipe.overnight(
        bias_V=0.1, setpoint_A=50e-12, repetitions=2000,
        size_nm=(100.0, 100.0), speed_nm_s=50.0, pixels=(512, 512),
    )
    assert any("24 h" in w for w in _warnings(r.validate()))


def test_min_bias_guard_constant_is_5mV():
    # validate() and the runner guard must share this constant
    assert MIN_CONST_CURRENT_BIAS_V == 5e-3
