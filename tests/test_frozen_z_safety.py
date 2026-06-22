"""Frozen-Z safety: a scan with exactly zero Z drift halts the run."""

import pytest

from scanflow.core import STMClient
from scanflow.automation import MeasurementRecipe
from scanflow.automation.recipe import WaitStep
from scanflow.automation.runner import AutomationRunner

FROZEN = {"rms_pm": 0.0, "max_pm": 0.0, "jumps": 0, "rating": "excellent"}
REAL = {"rms_pm": 19.7, "max_pm": 96.5, "jumps": 0, "rating": "good"}
ABSENT = {"rms_pm": 0.0, "max_pm": 0.0, "jumps": 0, "rating": "n/a"}


@pytest.fixture
def runner():
    stm = STMClient()
    assert stm.connect_mock()
    recipe = MeasurementRecipe(name="t", steps=[WaitStep(seconds=0.01)])
    r = AutomationRunner(stm, recipe)
    yield r
    stm.disconnect()


def test_frozen_z_stops_run(runner):
    errs = []
    runner.error.connect(errs.append)
    runner._check_z_frozen(FROZEN)
    assert runner._stop_requested is True
    assert errs and "frozen" in errs[0].lower()


def test_real_scan_does_not_stop(runner):
    runner._check_z_frozen(REAL)
    assert runner._stop_requested is False


def test_absent_image_does_not_stop(runner):
    """rating 'n/a' (no/invalid image) must not be mistaken for frozen."""
    runner._check_z_frozen(ABSENT)
    assert runner._stop_requested is False


def test_disable_flag_skips_check(runner):
    runner._recipe.stop_on_frozen_z = False
    runner._check_z_frozen(FROZEN)
    assert runner._stop_requested is False
