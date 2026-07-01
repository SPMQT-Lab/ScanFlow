"""Tests for opt-in, conflated live-frame delivery and the safety fallback cache.

Every DATA.SCAN read is a full-frame COM transfer executed on STMAFM's GUI
thread — the documented cause of controller lag during automation. These
tests pin the two behaviours that keep that load bounded:

  * the runner pulls frames only when a consumer opted in, and
  * the safety monitor's full-frame fallback is served from a short cache.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from scanflow.core import STMClient, SafetyMonitor, SafetyConfig
from scanflow.automation import MeasurementRecipe, AutomationRunner


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def _count_data_scan_reads(stm, counter: dict) -> None:
    """Shadow the mock's getp with a wrapper counting DATA.SCAN pulls."""
    mock = stm.raw
    orig = mock.getp

    def counting_getp(key, default=""):
        if key == "DATA.SCAN":
            counter["n"] += 1
        return orig(key, default)

    mock.getp = counting_getp


def _short_recipe() -> MeasurementRecipe:
    r = MeasurementRecipe.overnight(
        bias_V=0.1, setpoint_A=50e-12, repetitions=1,
        size_nm=(10.0, 10.0), pixels=(16, 16), speed_nm_s=100.0,
    )
    r.safety_poll_interval_s = 0.05
    return r


# ── runner: frame pulls are opt-in ────────────────────────────────────────

def test_no_frame_pulls_when_not_enabled(stm):
    """Default runner must not pull DATA.SCAN inside the scan-wait loop.

    Regression: the old loop pulled a full frame every safety poll even
    with no consumer connected. Only the per-scan z-stability read (and
    similar one-shot reads) are allowed.
    """
    counter = {"n": 0}
    _count_data_scan_reads(stm, counter)

    runner = AutomationRunner(stm, _short_recipe())
    runner.start()
    assert runner.wait(15000)
    # One z-stability pull after the scan; a 0.05 s polling loop over a
    # ~2 s mock scan would have produced ~40 pulls before the fix.
    assert counter["n"] <= 2, f"{counter['n']} DATA.SCAN pulls with frames disabled"


def test_frame_pulls_rate_limited_when_enabled(stm):
    counter = {"n": 0}
    _count_data_scan_reads(stm, counter)

    runner = AutomationRunner(stm, _short_recipe())
    runner.enable_live_frames(interval_s=0.5)

    ready_count = {"n": 0}
    frames: list = []

    def on_ready():
        ready_count["n"] += 1
        frame = runner.take_live_frame()
        if frame is not None:
            frames.append(frame)

    runner.live_frame_ready.connect(on_ready, type=Qt.DirectConnection)
    runner.start()
    assert runner.wait(15000)

    assert ready_count["n"] >= 1, "no live frames delivered despite opt-in"
    assert frames and isinstance(frames[0], np.ndarray)
    # Mock scan lasts ~2 s; at a 0.5 s floor that is at most ~5 pulls
    # (plus the one-shot z-stability read) — nowhere near the 0.05 s poll rate.
    assert counter["n"] <= 8, f"{counter['n']} pulls — interval floor not honoured"


def test_live_frame_conflation_queues_single_notification(stm):
    """Publishing N frames without a take must notify once, deliver newest."""
    runner = AutomationRunner(stm, _short_recipe())
    emissions = {"n": 0}
    runner.live_frame_ready.connect(
        lambda: emissions.__setitem__("n", emissions["n"] + 1),
        type=Qt.DirectConnection,
    )

    a = np.zeros((2, 2))
    b = np.ones((2, 2))
    runner._publish_live_frame(a)
    runner._publish_live_frame(b)   # replaces a; no second notification
    assert emissions["n"] == 1

    got = runner.take_live_frame()
    assert got is b
    assert runner.take_live_frame() is None  # slot cleared

    runner._publish_live_frame(a)   # pending was cleared → notifies again
    assert emissions["n"] == 2


# ── safety monitor: fallback frame read is cached ─────────────────────────

def _break_adc(stm):
    def raising(*_a, **_k):
        raise RuntimeError("ADC unavailable")
    stm.raw.getadcvalf = raising


def test_safety_fallback_uses_cache_within_window(stm):
    monitor = SafetyMonitor(SafetyConfig())
    _break_adc(stm)
    counter = {"n": 0}
    _count_data_scan_reads(stm, counter)

    v1 = monitor.measure_current_A(stm)
    v2 = monitor.measure_current_A(stm)
    assert v1 is not None and v2 == v1
    assert counter["n"] == 1, "second call within cache window pulled a frame"


def test_safety_fallback_cache_expires(stm):
    monitor = SafetyMonitor(SafetyConfig())
    monitor.FALLBACK_CACHE_S = 0.05  # shrink the window for the test
    _break_adc(stm)
    counter = {"n": 0}
    _count_data_scan_reads(stm, counter)

    monitor.measure_current_A(stm)
    time.sleep(0.06)
    monitor.measure_current_A(stm)
    assert counter["n"] == 2, "cache never expired"
