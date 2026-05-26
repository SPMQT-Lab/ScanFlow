"""Tests for the ScanFlow ProbeFlow-backed preview tab."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from scanflow.core import STMClient
from scanflow.gui.panels.preview_panel import (
    PreviewPanel,
    _FeatureScanWorker,
    _latest_dat_in_folder,
)
from probeflow.analysis.preview import PreviewAnalysisParams, run_preview


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "ProbeFlow"
    / "test_data"
    / "createc_scan_preview_120nm.dat"
)


def test_latest_dat_in_folder_picks_newest(tmp_path) -> None:
    older = tmp_path / "older.dat"
    newer = tmp_path / "newer.dat"
    older.write_text("old", encoding="utf-8")
    time.sleep(0.05)
    newer.write_text("new", encoding="utf-8")

    assert _latest_dat_in_folder(tmp_path) == newer


def _wait_until(app, predicate, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while not predicate() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)


def test_preview_panel_loads_raw_scan_without_auto_analysis(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, source)

    panel = PreviewPanel(STMClient())
    panel.handle_scan_completed(str(source))

    _wait_until(app, lambda: panel._current_scan is not None)

    assert panel._current_scan is not None
    assert panel._current_state is not None
    assert panel._current_state.source_path == source
    assert panel._feature_table.rowCount() == 0
    assert panel._folder_edit.text() == str(tmp_path)
    assert panel._display_mode.currentData() == "raw"
    assert panel._viewer._image.image is not None
    assert panel._current_state.corrected_plane is None
    assert panel._current_state.feature_rows == ()


def test_preview_panel_background_and_detection_are_user_driven(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, source)

    panel = PreviewPanel(STMClient())
    panel.handle_scan_completed(str(source))
    _wait_until(app, lambda: panel._current_scan is not None)

    panel._apply_background()
    _wait_until(app, lambda: panel._current_state is not None and panel._current_state.corrected_plane is not None)
    assert panel._display_mode.currentData() == "background_corrected"
    assert panel._current_state is not None
    assert panel._current_state.corrected_plane is not None
    assert panel._current_state.background_image is not None
    assert panel._feature_table.rowCount() == 0

    panel._detect_features()
    _wait_until(app, lambda: panel._feature_table.rowCount() > 0)

    assert panel._current_state is not None
    assert panel._current_state.feature_rows == ()
    assert panel._feature_table.rowCount() == len(panel._current_state.preview_rows)
    assert all(
        panel._feature_table.item(row, 0).checkState() == Qt.CheckState.Unchecked
        for row in range(panel._feature_table.rowCount())
    )

    first_index = panel._current_state.preview_rows[0].index
    panel._toggle_feature_selection(first_index)
    assert panel._feature_table.item(0, 0).checkState() == Qt.CheckState.Checked


def test_preview_panel_labels_selected_features(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, source)

    panel = PreviewPanel(STMClient())
    panel.handle_scan_completed(str(source))
    _wait_until(app, lambda: panel._current_scan is not None)
    panel._apply_background()
    _wait_until(app, lambda: panel._current_state is not None and panel._current_state.corrected_plane is not None)
    panel._detect_features()
    _wait_until(app, lambda: panel._feature_table.rowCount() > 0)
    panel._apply_segmentation_settings()

    assert panel._current_state is not None
    first = panel._current_state.feature_rows[0].index
    second = panel._current_state.feature_rows[1].index
    third = panel._current_state.feature_rows[2].index
    for feature_index in (first, second, third):
        panel._toggle_feature_selection(feature_index)

    panel._sample_name_edit.setText("class-a")
    panel._label_selected_samples()

    assert panel._current_state.sample_labels[first] == "class-a"
    assert panel._current_state.sample_labels[second] == "class-a"
    assert panel._current_state.sample_labels[third] == "class-a"
    assert panel._feature_table.item(0, 3).text() == "class-a"
    assert panel._feature_table.item(1, 3).text() == "class-a"
    assert panel._feature_table.item(2, 3).text() == "class-a"


def test_preview_panel_reset_analysis_returns_to_raw(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, source)

    panel = PreviewPanel(STMClient())
    panel.handle_scan_completed(str(source))
    _wait_until(app, lambda: panel._current_scan is not None)
    panel._apply_background()
    _wait_until(app, lambda: panel._current_state is not None and panel._current_state.corrected_plane is not None)
    panel._detect_features()
    _wait_until(app, lambda: panel._feature_table.rowCount() > 0)
    panel._apply_segmentation_settings()
    assert panel._stage == "classification"

    panel._reset_analysis()

    assert panel._stage == "raw"
    assert panel._display_mode.currentData() == "raw"
    assert panel._current_state is not None
    assert panel._current_state.corrected_plane is None
    assert panel._current_state.background_image is None
    assert panel._current_state.feature_rows == ()
    assert panel._current_state.sample_labels == {}
    assert panel._current_state.classifications == {}
    assert panel._feature_table.rowCount() == 0
    assert not panel._flatten_section.isVisible()


def test_follow_up_scan_worker_writes_a_scan_target(tmp_path) -> None:
    source = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, source)
    preview = run_preview(source, PreviewAnalysisParams())
    assert preview.feature_rows

    stm = STMClient()
    stm.connect_mock()
    saved: list[str] = []
    errors: list[str] = []
    worker = _FeatureScanWorker(stm, source, [preview.feature_rows[0]])
    worker.scan_saved.connect(saved.append)
    worker.failed.connect(errors.append)

    worker.run()

    assert errors == []
    assert saved
    assert stm.scan.last_saved_path() == Path(saved[0])
