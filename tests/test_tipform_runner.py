import numpy as np
import pytest

pytest.importorskip("PySide6")

from scanflow.automation import AutomationRunner, MeasurementRecipe, RunnerState, TipFormStep


@pytest.fixture
def stm():
    from scanflow.core import STMClient

    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def _runner(stm) -> AutomationRunner:
    return AutomationRunner(stm, MeasurementRecipe(name="tip form test"))


def test_tipform_approval_is_scoped_to_the_run_it_was_armed_for(stm):
    stm.raw.setxyoffvolt(1.0, 1.0)
    runner = _runner(stm)
    runner.approve_next_tip_form()

    runner._run_generation = 2
    runner._active_run_generation = 2
    runner._state = RunnerState.RUNNING

    errors: list[str] = []
    runner.error.connect(lambda msg: errors.append(msg))

    form_calls: list[tuple] = []
    runner._stm.tipform.form_at = lambda *args, **kwargs: form_calls.append((args, kwargs))
    runner._stm.scan.live_data = lambda *args, **kwargs: np.zeros((8, 8))

    runner._do_tip_form_step(
        TipFormStep(x_px=16, y_px=24, voltage_V=0.2, pulse_length_s=0.2),
        "tip",
    )

    assert form_calls == []
    assert runner._stop_requested is True
    assert runner._tip_form_approval_pending is False
    assert runner._tip_form_approval_run_generation is None
    assert any("armed for run" in msg.lower() for msg in errors)


def test_tipform_requires_pre_snapshot_before_pulse(stm):
    stm.raw.setxyoffvolt(1.0, 1.0)
    runner = _runner(stm)
    runner.approve_next_tip_form()

    runner._run_generation = 1
    runner._active_run_generation = 1
    runner._state = RunnerState.RUNNING

    errors: list[str] = []
    runner.error.connect(lambda msg: errors.append(msg))

    form_calls: list[tuple] = []
    runner._stm.tipform.form_at = lambda *args, **kwargs: form_calls.append((args, kwargs))
    runner._stm.scan.live_data = lambda *args, **kwargs: None

    runner._do_tip_form_step(
        TipFormStep(x_px=16, y_px=24, voltage_V=0.2, pulse_length_s=0.2),
        "tip",
    )

    assert form_calls == []
    assert runner._stop_requested is True
    assert any("pre quality snapshot" in msg.lower() for msg in errors)


def test_tipform_requires_post_snapshot_after_pulse(stm):
    stm.raw.setxyoffvolt(1.0, 1.0)
    runner = _runner(stm)
    runner.approve_next_tip_form()

    runner._run_generation = 1
    runner._active_run_generation = 1
    runner._state = RunnerState.RUNNING

    errors: list[str] = []
    runner.error.connect(lambda msg: errors.append(msg))

    form_calls: list[tuple] = []
    runner._stm.tipform.form_at = lambda *args, **kwargs: form_calls.append((args, kwargs))

    live_calls = {"count": 0}

    def live_data(*args, **kwargs):
        live_calls["count"] += 1
        if live_calls["count"] == 1:
            return np.zeros((8, 8))
        return None

    runner._stm.scan.live_data = live_data

    runner._do_tip_form_step(
        TipFormStep(x_px=16, y_px=24, voltage_V=0.2, pulse_length_s=0.2),
        "tip",
    )

    assert len(form_calls) == 1
    assert runner._stop_requested is True
    assert any("post quality snapshot" in msg.lower() for msg in errors)
