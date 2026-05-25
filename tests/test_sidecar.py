import json

from scanflow.core import ScanParams
from scanflow.io.sidecar import (
    SessionManifestWriter,
    new_session_id,
    scanflow_sidecar_path,
    write_scan_sidecar,
)


def test_write_scan_sidecar(tmp_path):
    dat = tmp_path / "scan_001.dat"
    dat.write_bytes(b"fake")
    session_id = new_session_id()

    path = write_scan_sidecar(
        dat,
        session_id=session_id,
        routine="bias_ramp",
        step_index=1,
        step_kind="scan",
        step_label="100 mV",
        scan_parameters=ScanParams(
            bias_V=0.1,
            setpoint_A=50e-12,
            size_nm=(20.0, 10.0),
            pixels=(64, 32),
        ),
        position_nm=(1.0, 2.0),
        motion={"ok": True, "reason": "test"},
        drift={"method": "hybrid", "dx_pixels": 0.0, "dy_pixels": 0.0, "confidence": 1.0},
        quality={"z_stability": {"rms_pm": 1.0}},
        safety={"enabled": True, "max_current_A": 1e-9, "aborted": False},
    )

    assert path == scanflow_sidecar_path(dat)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == "scanflow.acquisition.v1"
    assert data["record_type"] == "scanflow_scan"
    assert data["raw_file"]["path"] == "scan_001.dat"
    assert data["session"]["session_id"] == session_id
    assert data["scan_parameters"]["bias_V"] == 0.1
    assert data["position"]["scan_offset_nm"] == [1.0, 2.0]


def test_session_manifest_writer(tmp_path):
    writer = SessionManifestWriter(
        tmp_path,
        session_id="session-1",
        recipe_name="Test recipe",
        routine="mosaic",
    )
    writer.add_scan(
        dat_path=tmp_path / "tile.dat",
        sidecar_path=tmp_path / "tile.scanflow.json",
        role="tile",
        step_index=2,
        label="tile 1",
    )

    data = json.loads((tmp_path / "scanflow_session.json").read_text(encoding="utf-8"))
    assert data["schema"] == "scanflow.session.v1"
    assert data["session_id"] == "session-1"
    assert data["recipe"]["routine"] == "mosaic"
    assert data["scans"][0]["dat_path"] == "tile.dat"
    assert data["scans"][0]["sidecar_path"] == "tile.scanflow.json"

