"""Tests for the frame-resize diagnostic experiment (mock plumbing).

The mock cannot answer the B2 question (its offset model has no
frame-size dependence) — these tests verify the experiment's mechanics:
state capture, resize + restore, report writing, abort safety.
"""

import json

import pytest

from scanflow.core import STMClient
from scanflow.diagnostics import run_frame_resize_experiment


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def test_experiment_records_resize_and_restores(stm, tmp_path):
    original_h = float(stm.getp("SCAN.IMAGESIZE.NM.Y"))
    report = run_frame_resize_experiment(
        stm,
        with_scans=True,
        save_folder=tmp_path,
        interactive=False,
        out=lambda _msg: None,
    )

    # Resize happened and was recorded
    assert report["pre_resize"]["size_nm"][1] == pytest.approx(original_h)
    assert report["post_resize"]["size_nm"][1] == pytest.approx(original_h / 2)
    # ...and the original height was restored
    assert report["restored"]["size_nm"][1] == pytest.approx(original_h)
    assert float(stm.getp("SCAN.IMAGESIZE.NM.Y")) == pytest.approx(original_h)

    # Offset readbacks captured pre and post
    assert report["pre_resize"]["offset_nm"] is not None
    assert report["post_resize"]["offset_nm"] is not None
    assert "derived" in report
    assert report["derived"]["expected_shift_if_centre_preserved_nm"] == \
        pytest.approx(original_h / 4)

    # Both scans ran and were saved
    assert set(report["scans"]) == {"before", "after"}

    # Report file written and parseable
    data = json.loads((tmp_path / report["report_path"].split("/")[-1]).read_text())
    assert data["schema"] == "scanflow.diagnostic.frame_resize.v1"
    assert data["mock"] is True


def test_experiment_refuses_while_scanning(stm, tmp_path):
    stm.setp("STMAFM.BTN.START", "")
    with pytest.raises(RuntimeError, match="scan is running"):
        run_frame_resize_experiment(
            stm, save_folder=tmp_path, interactive=False,
            out=lambda _msg: None,
        )
    stm.setp("STMAFM.BTN.STOP", "")


def test_experiment_declined_resize_leaves_size_untouched(stm, tmp_path):
    """Interactive run where the operator declines the resize step."""
    original_h = float(stm.getp("SCAN.IMAGESIZE.NM.Y"))
    answers = iter([
        "",   # "press Enter when positioned"
        "n",  # decline the resize
    ])
    report = run_frame_resize_experiment(
        stm,
        with_scans=False,
        save_folder=tmp_path,
        interactive=True,
        ask=lambda _prompt: next(answers),
        out=lambda _msg: None,
    )
    assert any("declined" in n for n in report["notes"])
    assert "post_resize" not in report
    assert float(stm.getp("SCAN.IMAGESIZE.NM.Y")) == pytest.approx(original_h)


def test_cli_diag_frame_resize_mock(tmp_path):
    from scanflow.cli import main
    rc = main([
        "diag", "frame-resize", "--mock", "--no-input", "--no-scans",
        "--save-folder", str(tmp_path),
    ])
    assert rc == 0
    reports = list(tmp_path.glob("frame_resize_report_*.json"))
    assert len(reports) == 1
