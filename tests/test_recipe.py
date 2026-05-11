"""Basic recipe construction tests (no STM connection required)."""

from scanflow.automation import MeasurementRecipe, ScanStep


def test_overnight_recipe():
    r = MeasurementRecipe.overnight(bias_V=0.1, setpoint_A=50e-12, repetitions=5)
    assert r.total_steps() == 5
    assert r.steps[0].bias_V == 0.1
    assert r.suppress_dst_change is True


def test_bias_ramp_recipe():
    r = MeasurementRecipe.bias_ramp(start_V=-0.5, end_V=0.5, steps=11, setpoint_A=100e-12)
    assert len(r.steps) == 11
    assert r.steps[0].bias_V == -0.5
    assert r.steps[-1].bias_V == 0.5


def test_current_ramp_recipe():
    r = MeasurementRecipe.current_ramp(start_pA=10, end_pA=100, steps=10, bias_V=0.1)
    assert len(r.steps) == 10
    assert r.steps[0].setpoint_A == 10e-12
    assert r.steps[-1].setpoint_A == 100e-12


def test_recipe_yaml_roundtrip(tmp_path):
    r = MeasurementRecipe.overnight(bias_V=0.2, setpoint_A=80e-12, repetitions=3)
    path = tmp_path / "recipe.yaml"
    r.save(path)
    r2 = MeasurementRecipe.load(path)
    assert r2.steps[0].bias_V == 0.2
    assert r2.repetitions == 3
    assert r2.suppress_dst_change is True
    assert r2.steps[0].size_nm == (50.0, 50.0)
