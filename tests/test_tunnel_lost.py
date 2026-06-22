"""Tunneling-loss safety: sustained near-zero current halts the scan."""

import time

import pytest

from scanflow.core.safety import SafetyMonitor, SafetyConfig


def _monitor(floor_A=10e-12, grace_s=5.0):
    cfg = SafetyConfig(
        max_current_A=1e-9,
        min_tunnel_current_A=floor_A,
        tunnel_lost_grace_s=grace_s,
    )
    return SafetyMonitor(cfg)


def _fixed_current(mon, value_A):
    mon.measure_current_A = lambda stm: value_A


def test_below_floor_within_grace_is_ok():
    mon = _monitor()
    _fixed_current(mon, 1e-12)          # 1 pA, below 10 pA floor
    status = mon.check(stm=None)        # first poll: timer just started
    assert status.ok is True


def test_sustained_below_floor_trips_tunnel_lost():
    mon = _monitor(grace_s=5.0)
    _fixed_current(mon, 1e-12)
    mon.check(stm=None)                 # start the timer
    mon._low_current_since = time.monotonic() - 6.0   # pretend 6 s elapsed
    status = mon.check(stm=None)
    assert status.ok is False
    assert status.kind == "tunnel_lost"
    assert "Tunneling lost" in status.reason


def test_recovery_resets_timer():
    mon = _monitor(grace_s=5.0)
    _fixed_current(mon, 1e-12)
    mon.check(stm=None)
    mon._low_current_since = time.monotonic() - 6.0
    _fixed_current(mon, 50e-12)         # back to tunneling (50 pA)
    status = mon.check(stm=None)
    assert status.ok is True
    assert mon._low_current_since is None


def test_crash_takes_priority_over_tunnel():
    mon = _monitor()
    _fixed_current(mon, 5e-9)           # 5 nA — crash
    status = mon.check(stm=None)
    assert status.ok is False
    assert status.kind == "crash"


def test_disabled_when_floor_none():
    cfg = SafetyConfig(max_current_A=1e-9, min_tunnel_current_A=None)
    mon = SafetyMonitor(cfg)
    mon.measure_current_A = lambda stm: 0.0
    mon._low_current_since = time.monotonic() - 100.0
    assert mon.check(stm=None).ok is True


def test_runner_tunnel_lost_stops_without_retract():
    """The runner halts gracefully on tunnel_lost — no SafetyViolation raised,
    no tip retract (unlike a crash)."""
    from scanflow.core import STMClient
    from scanflow.automation import MeasurementRecipe
    from scanflow.automation.recipe import WaitStep
    from scanflow.automation.runner import AutomationRunner

    stm = STMClient(); stm.connect_mock()
    runner = AutomationRunner(stm, MeasurementRecipe(name="t", steps=[WaitStep(seconds=0.01)]))
    errs = []
    runner.error.connect(errs.append)

    retracted = []
    runner._safety.emergency_stop = lambda s: retracted.append(True)
    runner._safety.config.min_tunnel_current_A = 10e-12
    runner._safety.config.tunnel_lost_grace_s = 5.0
    runner._safety.measure_current_A = lambda s: 1e-12
    runner._safety.check(stm)                                  # start timer
    runner._safety._low_current_since = time.monotonic() - 6.0

    runner._check_safety()    # must NOT raise
    assert runner._stop_requested is True
    assert retracted == [], "tunnel_lost must not retract the tip"
    assert errs and "Tunneling lost" in errs[0]

    stm.disconnect()
