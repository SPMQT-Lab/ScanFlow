"""Tests for the tip-crash safety monitor and runner integration."""

import time
import pytest

from scanflow.core import (
    STMClient, SafetyMonitor, SafetyConfig, SafetyViolation,
)
from scanflow.automation import MeasurementRecipe, AutomationRunner


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


# ── SafetyMonitor unit tests ──────────────────────────────────────────────

def test_safety_ok_at_idle(stm):
    monitor = SafetyMonitor(SafetyConfig(max_current_A=1e-9))
    status = monitor.check(stm)
    assert status.ok is True
    assert status.measured_current_A is not None
    assert abs(status.measured_current_A) < 1e-9


def test_safety_triggers_on_simulated_crash(stm):
    monitor = SafetyMonitor(SafetyConfig(max_current_A=1e-9))
    stm.raw.simulate_tip_crash(current_nA=5.0)
    status = monitor.check(stm)
    assert status.ok is False
    assert "threshold exceeded" in status.reason.lower()
    assert status.measured_current_A == pytest.approx(5.0e-9, rel=0.01)


def test_safety_disabled_passes_through(stm):
    monitor = SafetyMonitor(SafetyConfig(max_current_A=1e-12,
                                         enable_current_check=False))
    # Even with an unreasonably tight threshold, check returns ok
    status = monitor.check(stm)
    assert status.ok is True


def test_emergency_stop_retracts_tip(stm):
    monitor = SafetyMonitor(SafetyConfig(max_current_A=1e-9,
                                         retract_on_violation_nm=15.0))
    # Pretend we're scanning
    stm.setp("STMAFM.BTN.START", "")
    assert int(stm.getp("STMAFM.SCANSTATUS", 0)) == 2
    monitor.emergency_stop(stm)
    # Scan stopped and Z-limit on
    assert int(stm.getp("STMAFM.SCANSTATUS", 0)) == 0
    assert stm.getp("SLIDER.ZLIMIT.ON", "") == "ON"


# ── Runner-level integration ──────────────────────────────────────────────

def test_recipe_has_safety_defaults():
    r = MeasurementRecipe.overnight(bias_V=0.1, setpoint_A=50e-12, repetitions=1)
    assert r.safety_enable is True
    assert r.safety_max_current_A == pytest.approx(1e-9)
    assert r.safety_retract_nm == pytest.approx(10.0)


def test_runner_aborts_on_crash(stm):
    """Runner must transition to ERROR state when current exceeds threshold."""
    from PySide6.QtCore import Qt
    from scanflow.automation import RunnerState

    r = MeasurementRecipe.overnight(
        bias_V=0.1, setpoint_A=50e-12, repetitions=1,
        size_nm=(10.0, 10.0), pixels=(32, 32), speed_nm_s=50.0,
    )
    r.safety_max_current_A = 1e-9
    r.safety_poll_interval_s = 0.1

    runner = AutomationRunner(stm, r)

    # Direct connection so the slot fires on the worker thread without
    # needing a QApplication event loop.
    violations: list = []
    runner.safety_violation.connect(
        lambda msg, i: violations.append((msg, i)),
        type=Qt.DirectConnection,
    )

    # Simulate a crash before the runner starts polling
    stm.raw.simulate_tip_crash(current_nA=5.0)

    runner.start()
    assert runner.wait(8000)
    # State machine moved to ERROR — that's the load-bearing assertion
    assert runner._state == RunnerState.ERROR
    # And the violation slot was actually invoked
    assert len(violations) == 1
    msg, current = violations[0]
    assert "threshold exceeded" in msg.lower()
    assert current == pytest.approx(5.0e-9, rel=0.05)


def test_runner_completes_safely_without_crash(stm):
    """A recipe completes normally when current stays below threshold."""
    r = MeasurementRecipe.overnight(
        bias_V=0.1, setpoint_A=50e-12, repetitions=1,
        size_nm=(10.0, 10.0), pixels=(16, 16), speed_nm_s=100.0,
    )
    r.safety_max_current_A = 1e-9
    r.safety_poll_interval_s = 0.1
    r.drift_correction = False  # avoid createc.Createc_pyFile dependency

    runner = AutomationRunner(stm, r)

    errors: list = []
    runner.error.connect(lambda m: errors.append(m))

    runner.start()
    assert runner.wait(15000)
    assert errors == []
