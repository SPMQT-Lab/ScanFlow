"""Tests for the voltage-warning logic in the sweep confirmation dialog."""

from __future__ import annotations

from scanflow.automation import MeasurementRecipe
from scanflow.automation.recipe import ScanStep
from scanflow.gui.widgets.sweep_confirm import (
    HIGH_BIAS_V, LOW_BIAS_V, categorize_voltages,
)


def test_no_warning_for_safe_voltages():
    steps = [ScanStep(bias_V=0.5, setpoint_A=5e-11),
             ScanStep(bias_V=-0.5, setpoint_A=5e-11)]
    cats = categorize_voltages(steps)
    assert cats["high"] == []
    assert cats["low"] == []


def test_flags_high_voltage_steps():
    steps = [ScanStep(bias_V=0.5, setpoint_A=5e-11),
             ScanStep(bias_V=2.5, setpoint_A=5e-11),
             ScanStep(bias_V=-1.8, setpoint_A=5e-11)]
    cats = categorize_voltages(steps)
    biases = {s.bias_V for s in cats["high"]}
    assert biases == {2.5, -1.8}
    assert cats["low"] == []


def test_flags_low_voltage_steps():
    steps = [ScanStep(bias_V=0.5, setpoint_A=5e-11),
             ScanStep(bias_V=0.05, setpoint_A=5e-11),
             ScanStep(bias_V=-0.08, setpoint_A=5e-11)]
    cats = categorize_voltages(steps)
    biases = {s.bias_V for s in cats["low"]}
    assert biases == {0.05, -0.08}
    assert cats["high"] == []


def test_const_height_steps_skip_low_warning():
    # In const-height mode the feedback isn't engaged, so a low bias isn't a
    # "feedback may struggle" condition — only const-current low bias should warn.
    steps = [ScanStep(bias_V=0.02, setpoint_A=5e-11, const_height=True),
             ScanStep(bias_V=0.02, setpoint_A=5e-11, const_height=False)]
    cats = categorize_voltages(steps)
    assert len(cats["low"]) == 1
    assert cats["low"][0].const_height is False


def test_real_bias_ramp_high_voltage():
    r = MeasurementRecipe.bias_ramp(start_V=-2.0, end_V=2.0, steps=21, setpoint_A=5e-11)
    cats = categorize_voltages([s for s in r.steps if getattr(s, "kind", "scan") == "scan"])
    # 21 steps from -2 to +2, step 0.2 V. |V|>1.0 → |V| in {1.2, 1.4, 1.6, 1.8, 2.0}
    # That's 5 negatives + 5 positives = 10 steps.
    assert len(cats["high"]) == 10
    for s in cats["high"]:
        assert abs(s.bias_V) > HIGH_BIAS_V


def test_thresholds_are_sensible():
    """Sanity check on the threshold constants — they should be human values
    matching the user's mental model (1 V and 100 mV)."""
    assert HIGH_BIAS_V == 1.0
    assert LOW_BIAS_V == 0.1
