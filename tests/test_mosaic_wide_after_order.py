"""Regression: wide_after must resize to the wide frame BEFORE repositioning.

Lab run 2026-06-20: the final wide overview landed one tile (−Y) off
because the runner moved to the wide centre while the small tile frame
was still loaded and only then resized. Createc shifts SCAN.OFFSET when
IMAGESIZE changes (finding B2), so the offset must be (re)set with the
wide frame already active — the same context in which wide_centre was
recorded for wide_before.
"""

import pytest

from scanflow.core import STMClient
from scanflow.core.motion import TipMotionManager
from scanflow.automation import MeasurementRecipe, MosaicConfig
from scanflow.automation.recipe import MosaicStep
from scanflow.automation.runner import AutomationRunner


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def test_wide_after_applies_frame_before_return_move(stm, tmp_path, monkeypatch):
    cfg = MosaicConfig(
        wide_size_nm=(120.0, 120.0), wide_pixels=(64, 64), wide_speed_nm_s=400.0,
        tile_pixels=(64, 64), tile_speed_nm_s=400.0,
        grid_n=1, iterations_per_tile=1, settling_s=0.0,
        output_folder=str(tmp_path), name="order-test",
    )
    recipe = MeasurementRecipe(name="mosaic order", steps=[MosaicStep(config=cfg)])
    recipe.safety_poll_interval_s = 0.1

    events: list[str] = []

    real_apply = AutomationRunner._apply_scan_params
    def spy_apply(self, params):
        if params.memo and "wide after" in params.memo:
            events.append("apply_wide_after")
        return real_apply(self, params)
    monkeypatch.setattr(AutomationRunner, "_apply_scan_params", spy_apply)

    real_move = TipMotionManager.move_absolute_nm
    def spy_move(self, x, y, **kw):
        if "wide centre" in str(kw.get("reason", "")):
            events.append("return_to_centre")
        return real_move(self, x, y, **kw)
    monkeypatch.setattr(TipMotionManager, "move_absolute_nm", spy_move)

    runner = AutomationRunner(stm, recipe)
    errs: list[str] = []
    runner.error.connect(lambda m: errs.append(m))
    runner.start()
    assert runner.wait(60000)
    assert errs == [], f"mosaic reported errors: {errs}"

    assert "apply_wide_after" in events, "wide_after frame was never applied"
    assert "return_to_centre" in events, "return-to-centre move never happened"
    assert events.index("apply_wide_after") < events.index("return_to_centre"), (
        "wide frame must be applied before repositioning, else the resize "
        f"shifts the offset (B2). Got order: {events}"
    )
