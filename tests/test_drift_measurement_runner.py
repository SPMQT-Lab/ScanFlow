"""End-to-end test: mosaic campaigns log drift measurements.

Observation-only wiring (ROADMAP §3.2): every mosaic run measures
apparent drift between tile iterations and between the two wide
overviews, with BOTH registered estimators, into the acquisition log.
No correction is applied anywhere — these events are the evidence base
for choosing a strategy.
"""

import json

import pytest

from scanflow.core import STMClient
from scanflow.automation import MeasurementRecipe, MosaicConfig
from scanflow.automation.recipe import MosaicStep
from scanflow.automation.runner import AutomationRunner


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def _read_events(folder):
    events = []
    for path in folder.glob("**/scanflow_acquisition_*.jsonl"):
        with path.open(encoding="utf-8") as fh:
            events.extend(json.loads(line) for line in fh if line.strip())
    return events


def test_mosaic_run_logs_drift_measurements(stm, tmp_path):
    cfg = MosaicConfig(
        wide_size_nm=(40.0, 40.0),
        wide_pixels=(64, 64),
        wide_speed_nm_s=400.0,
        tile_pixels=(64, 64),
        tile_speed_nm_s=400.0,
        grid_n=1,
        iterations_per_tile=2,
        settling_s=0.0,
        output_folder=str(tmp_path),
        name="drift-test",
    )
    recipe = MeasurementRecipe(name="mosaic drift test",
                               steps=[MosaicStep(config=cfg)])
    recipe.safety_poll_interval_s = 0.1

    runner = AutomationRunner(stm, recipe)
    errors: list[str] = []
    runner.error.connect(lambda m: errors.append(m))
    runner.start()
    assert runner.wait(60000)
    assert errors == [], f"mosaic run reported errors: {errors}"

    drift_events = [e for e in _read_events(tmp_path)
                    if e["event_type"] == "drift_measurement"]
    assert drift_events, "no drift_measurement events in the acquisition log"

    tags = {e["tag"] for e in drift_events}
    assert "tile01_iter2_vs_iter1" in tags
    assert "wide_after_vs_wide_before" in tags

    # Both registered methods report on every comparison
    for tag in tags:
        methods = {e["method"] for e in drift_events if e["tag"] == tag}
        assert methods == {"feature_match", "phase_correlation"}, (
            f"{tag}: expected both estimators, got {methods}"
        )

    # Events carry the full measurement contract
    for e in drift_events:
        assert {"ok", "dx_nm", "dy_nm", "confidence", "reason",
                "method_version"} <= set(e)

    # The mock surface drifts ~0.001 nm/s — any OK estimate over a few
    # seconds must be near zero, and refusals must carry a reason.
    for e in drift_events:
        if e["ok"]:
            assert abs(e["dx_nm"]) < 2.0 and abs(e["dy_nm"]) < 2.0
        else:
            assert e["reason"]
