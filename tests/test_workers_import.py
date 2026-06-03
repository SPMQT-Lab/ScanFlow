"""Smoke test: automation.workers exposes the expected QThread workers
and pure-Python path helpers, with no GUI panel dependency.
"""

from pathlib import Path


def test_workers_package_exports():
    from scanflow.automation.workers import (
        FeatureScanWorker,
        FeatureGroupScanWorker,
        latest_dat_in_folder,
        unique_dat_path,
    )
    assert FeatureScanWorker is not None
    assert FeatureGroupScanWorker is not None
    assert callable(latest_dat_in_folder)
    assert callable(unique_dat_path)


def test_unique_dat_path_collision(tmp_path):
    from scanflow.automation.workers import unique_dat_path

    first = unique_dat_path(tmp_path, "scan_01")
    assert first.name == "scan_01.dat"
    first.write_bytes(b"x")

    second = unique_dat_path(tmp_path, "scan_01")
    assert second != first
    assert second.suffix == ".dat"
    assert second.stem.startswith("scan_01_")


def test_latest_dat_in_folder_orders_by_mtime(tmp_path):
    import time
    from scanflow.automation.workers import latest_dat_in_folder

    assert latest_dat_in_folder(tmp_path) is None  # empty folder
    a = tmp_path / "a.dat"
    a.write_bytes(b"a")
    time.sleep(0.01)
    b = tmp_path / "b.dat"
    b.write_bytes(b"b")
    assert latest_dat_in_folder(tmp_path) == b


def test_preview_panel_aliases_still_work():
    """Backwards-compat: preview_panel re-exports the old underscore names."""
    from scanflow.gui.panels import preview_panel
    from scanflow.automation.workers import (
        FeatureScanWorker, FeatureGroupScanWorker,
        unique_dat_path, latest_dat_in_folder,
    )
    assert preview_panel._FeatureScanWorker is FeatureScanWorker
    assert preview_panel._FeatureGroupScanWorker is FeatureGroupScanWorker
    assert preview_panel._unique_dat_path is unique_dat_path
    assert preview_panel._latest_dat_in_folder is latest_dat_in_folder
