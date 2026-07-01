"""Tests for scanflow.io.acquisition_log."""

import json

from scanflow.io.acquisition_log import AcquisitionLog


def test_emit_writes_jsonl(tmp_path):
    alog = AcquisitionLog(tmp_path / "acq.jsonl")
    alog.emit("safety_reading", current_A=1.2e-10, ok=True)
    alog.emit("scan_saved", path="A260610.dat")

    lines = (tmp_path / "acq.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event_type"] == "safety_reading"
    assert first["ok"] is True


def test_emit_none_path_is_noop():
    AcquisitionLog(None).emit("anything", x=1)


def test_emit_survives_unreachable_path(tmp_path, caplog):
    """A dropped network share must not crash the runner (overnight 2026-06-10)."""
    alog = AcquisitionLog(tmp_path / "acq.jsonl")
    # Simulate the share vanishing mid-run: parent directory no longer exists.
    alog.path = tmp_path / "gone" / "deeper" / "acq.jsonl"

    for _ in range(5):
        alog.emit("safety_reading", ok=True)  # must not raise
    assert alog._write_failures == 5
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # rate-limited: only the first failure warns

    # Share comes back: writing resumes and the failure counter resets.
    alog.path = tmp_path / "acq.jsonl"
    alog.emit("safety_reading", ok=True)
    assert alog._write_failures == 0
    assert (tmp_path / "acq.jsonl").exists()
