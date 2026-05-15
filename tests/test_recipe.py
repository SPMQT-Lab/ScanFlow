"""Basic recipe construction tests (no STM connection required)."""

from scanflow.automation import MeasurementRecipe, ScanStep


def test_overnight_recipe():
    r = MeasurementRecipe.overnight(bias_V=0.1, setpoint_A=50e-12, repetitions=5)
    assert r.total_steps() == 5
    assert r.steps[0].bias_V == 0.1
    assert r.suppress_dst_change is True


def test_bias_ramp_recipe():
    # 0 V is dropped automatically in constant-current mode — it would crash
    # the tip — so a 11-point symmetric ramp produces 10 steps.
    r = MeasurementRecipe.bias_ramp(start_V=-0.5, end_V=0.5, steps=11, setpoint_A=100e-12)
    assert len(r.steps) == 10
    assert r.steps[0].bias_V == -0.5
    assert r.steps[-1].bias_V == 0.5
    assert all(abs(s.bias_V) >= 1e-3 for s in r.steps)


def test_bias_ramp_skips_zero():
    """0 V is unreachable in constant-current mode; the runner skips it."""
    r = MeasurementRecipe.bias_ramp(start_V=-0.01, end_V=0.01, steps=3, setpoint_A=50e-12)
    biases = [s.bias_V for s in r.steps]
    assert 0.0 not in biases
    assert -0.01 in biases and 0.01 in biases


def test_bias_ramp_const_height_keeps_zero():
    """Constant-height scans CAN run at 0 V — feedback isn't engaged."""
    r = MeasurementRecipe.bias_ramp(start_V=-0.01, end_V=0.01, steps=3,
                                    setpoint_A=50e-12, const_height=True)
    assert any(s.bias_V == 0.0 for s in r.steps)


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


def test_fast_alignment_defaults_false():
    """Backward-compat: existing recipes don't set fast_alignment, so it
    defaults to False and behaves like before."""
    r = MeasurementRecipe.overnight(bias_V=0.1, setpoint_A=50e-12)
    assert r.fast_alignment is False


def test_fast_alignment_propagates_through_bias_ramp():
    r = MeasurementRecipe.bias_ramp(
        start_V=-0.5, end_V=0.5, steps=11, setpoint_A=50e-12,
        fast_alignment=True,
    )
    assert r.fast_alignment is True


def test_fast_alignment_shortens_estimate():
    full = MeasurementRecipe.bias_ramp(
        start_V=-0.5, end_V=0.5, steps=11, setpoint_A=50e-12,
        fast_alignment=False,
    )
    fast = MeasurementRecipe.bias_ramp(
        start_V=-0.5, end_V=0.5, steps=11, setpoint_A=50e-12,
        fast_alignment=True,
    )
    assert full.drift_correction and fast.drift_correction
    assert fast.estimate_duration_s() < full.estimate_duration_s()


def test_fast_alignment_persists_through_yaml(tmp_path):
    r = MeasurementRecipe.bias_ramp(
        start_V=-0.5, end_V=0.5, steps=11, setpoint_A=50e-12,
        fast_alignment=True,
    )
    path = tmp_path / "recipe.yaml"
    r.save(path)
    r2 = MeasurementRecipe.load(path)
    assert r2.fast_alignment is True
