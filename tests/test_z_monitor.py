"""Tests for the ZMonitor's window-stats math.

We bypass the QTimer / COM path by feeding samples directly via add_sample().
"""

from __future__ import annotations

import math

import pytest

from scanflow.core.z_monitor import ZMonitor, format_summary


@pytest.fixture
def monitor():
    return ZMonitor(stm=None, interval_s=1.0, summary_interval_s=10.0)


def test_empty_buffer_returns_zero_stats(monitor):
    stats = monitor.window_stats(60)
    assert stats == {"ptp_A": 0.0, "std_A": 0.0, "drift_A_per_h": 0.0,
                     "n": 0, "span_s": 0.0}


def test_single_sample_returns_zero_stats(monitor):
    monitor.add_sample(t=0.0, raw=1.0)
    stats = monitor.window_stats(60)
    assert stats["n"] == 1
    assert stats["ptp_A"] == 0.0
    assert stats["drift_A_per_h"] == 0.0


def test_linear_drift_one_angstrom_per_hour(monitor):
    # 100 samples spaced 36 s apart → spans exactly 1 h.
    # raw goes 0.0 → 0.99 linearly → drift rate 0.99 Å/h (≈1).
    for i in range(100):
        monitor.add_sample(t=i * 36.0, raw=i / 100.0)
    stats = monitor.window_stats(3600)
    assert stats["n"] == 100
    assert stats["drift_A_per_h"] == pytest.approx(1.0, abs=0.05)
    assert stats["ptp_A"] == pytest.approx(0.99, abs=0.01)


def test_window_filter_excludes_old_samples(monitor):
    # 10 samples in window 0..90 s; another 10 at 120..210 s.
    for i in range(10):
        monitor.add_sample(t=float(i * 10), raw=0.0)
    for i in range(10):
        monitor.add_sample(t=120.0 + i * 10, raw=1.0)
    # Window of last 60 s should only see the second batch
    stats = monitor.window_stats(60)
    # Sample at t=120 has raw=1.0; subsequent stay at 1.0 → ptp = 0
    assert stats["n"] >= 6  # at least 6 recent samples
    assert stats["ptp_A"] == pytest.approx(0.0, abs=1e-9)


def test_scale_reinterprets_history(monitor):
    for i in range(50):
        monitor.add_sample(t=float(i), raw=float(i))
    stats_unit = monitor.window_stats(60)
    monitor.set_scale(2.0)
    stats_doubled = monitor.window_stats(60)
    assert stats_doubled["ptp_A"] == pytest.approx(2 * stats_unit["ptp_A"])
    assert stats_doubled["drift_A_per_h"] == pytest.approx(
        2 * stats_unit["drift_A_per_h"]
    )


def test_summary_fires_after_interval():
    received: list[dict] = []
    monitor = ZMonitor(stm=None, interval_s=1.0, summary_interval_s=5.0)
    monitor.summary.connect(received.append)
    for t in range(10):
        monitor.add_sample(t=float(t), raw=float(t))
    # 0..9 s of samples with 5 s summary interval → one summary at t≈5
    assert len(received) >= 1
    assert "5min" in received[-1]


def test_format_summary_is_readable():
    stats = {
        "5min": {"ptp_A": 0.42, "std_A": 0.1, "drift_A_per_h": 0.0, "n": 30, "span_s": 300.0},
        "1h":   {"ptp_A": 2.10, "std_A": 0.5, "drift_A_per_h": 0.7,  "n": 360, "span_s": 3600.0},
        "3h":   {"ptp_A": 4.80, "std_A": 1.2, "drift_A_per_h": 1.4,  "n": 1080, "span_s": 10800.0},
    }
    msg = format_summary(stats)
    assert "Z drift" in msg
    assert "0.42" in msg
    assert "2.10" in msg
    assert "Å/h" in msg


def test_get_samples_returns_numpy_arrays():
    monitor = ZMonitor(stm=None, dac_to_angstrom=0.5)
    for i in range(5):
        monitor.add_sample(t=float(i), raw=float(i * 10))
    ts, zs = monitor.get_samples()
    assert ts.shape == (5,)
    assert zs.shape == (5,)
    # raw=40 with scale 0.5 → 20.0
    assert zs[-1] == pytest.approx(20.0)


def test_clear_resets_buffer(monitor):
    for i in range(20):
        monitor.add_sample(t=float(i), raw=float(i))
    monitor.clear()
    ts, zs = monitor.get_samples()
    assert ts.size == 0
    assert zs.size == 0
    assert monitor.window_stats(60)["n"] == 0
