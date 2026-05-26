import pytest

from scanflow.automation import MeasurementRecipe, TipFormStep


def test_tipform_step_round_trips_as_guarded_recipe_step(tmp_path):
    recipe = MeasurementRecipe(name="tip form readiness")
    recipe.add_step(TipFormStep(x_px=64, y_px=96, voltage_V=0.2, pulse_length_s=0.5))

    path = tmp_path / "recipe.yaml"
    recipe.save(path)
    loaded = MeasurementRecipe.load(path)

    assert isinstance(loaded.steps[0], TipFormStep)
    assert loaded.steps[0].kind == "tip_form"
    assert loaded.steps[0].require_confirmation is True
    assert loaded.steps[0].x_px == 64
    assert loaded.steps[0].y_px == 96


def test_tipform_step_enforces_basic_bounds():
    with pytest.raises(ValueError):
        TipFormStep(voltage_V=20.0)

    with pytest.raises(ValueError):
        TipFormStep(pulse_length_s=0.0)
