"""Tests for the automation-activity flag (H7 COM-contention mitigation)."""

import threading

from scanflow.core import activity


def setup_function(_):
    # Drain any leaked state from other tests.
    while activity.is_active():
        activity.end()


def test_begin_end_refcount():
    assert not activity.is_active()
    activity.begin()
    assert activity.is_active()
    activity.begin()
    activity.end()
    assert activity.is_active()  # second runner still active
    activity.end()
    assert not activity.is_active()


def test_end_never_goes_negative():
    activity.end()
    assert not activity.is_active()
    activity.begin()
    assert activity.is_active()
    activity.end()


def test_thread_safety():
    def worker():
        for _ in range(1000):
            activity.begin()
            activity.end()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not activity.is_active()


def test_runner_clears_activity_after_run(tmp_path):
    """The flag must be set during a run and ALWAYS cleared afterwards."""
    from scanflow.core import STMClient
    from scanflow.automation import AutomationRunner, MeasurementRecipe
    from scanflow.automation.recipe import WaitStep

    stm = STMClient()
    assert stm.connect_mock()
    recipe = MeasurementRecipe(name="t", save_folder=str(tmp_path))
    recipe.add_step(WaitStep(seconds=0.1))

    from PySide6.QtCore import Qt

    seen_during_run = []
    runner = AutomationRunner(stm, recipe)
    # DirectConnection: the slot runs on the worker thread itself — no
    # event loop spins in this test to deliver queued signals.
    runner.progress.connect(
        lambda *_: seen_during_run.append(activity.is_active()),
        Qt.ConnectionType.DirectConnection,
    )
    runner.start()
    assert runner.wait(30000)
    assert seen_during_run and all(seen_during_run)
    assert not activity.is_active()
