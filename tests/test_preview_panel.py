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
    PreviewWindow,
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


def test_preview_window_opens_on_scan_completed_and_tracks_recent(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / f"first_{FIXTURE.name}"
    second = tmp_path / f"second_{FIXTURE.name}"
    shutil.copy2(FIXTURE, first)
    shutil.copy2(FIXTURE, second)

    window = PreviewWindow(STMClient())
    window.handle_scan_completed(str(first))
    _wait_until(app, lambda: window.isVisible() and window.panel._current_scan is not None)

    assert window.isVisible()
    assert window.panel._current_state is not None
    assert window.panel._current_state.source_path == first

    window.handle_scan_completed(str(second))
    _wait_until(app, lambda: window.panel._current_state is not None and window.panel._current_state.source_path == second)

    assert window.panel._current_state is not None
    assert window.panel._current_state.source_path == second
    assert window.panel._recent_list.count() == 2
    assert window.panel._recent_list.item(0).data(Qt.ItemDataRole.UserRole) == str(second.resolve())


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


def test_preview_panel_creates_default_class_names_and_allows_deselect(tmp_path) -> None:
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

    panel._label_selected_samples()

    assert panel._current_state.sample_labels[first] == "Name 1"
    assert panel._current_state.sample_labels[second] == "Name 1"
    assert panel._current_state.sample_labels[third] == "Name 1"
    assert panel._feature_table.item(0, 3).text() == "Name 1"
    assert panel._feature_table.item(1, 3).text() == "Name 1"
    assert panel._feature_table.item(2, 3).text() == "Name 1"

    active = panel._active_class_record()
    assert active is not None
    widgets = panel._class_widgets[active.key]
    card_margins = widgets.root.layout().getContentsMargins()
    header_margins = widgets.header.layout().getContentsMargins()
    assert panel._class_list_layout.spacing() <= 2
    assert max(card_margins) <= 4
    assert max(header_margins) <= 6
    assert widgets.name_edit.text() == ""
    assert widgets.name_edit.placeholderText() == "Name 1"
    assert not widgets.body_scroll.isHidden()

    panel._set_stage("scan")
    panel._set_stage("classification")
    assert panel._current_class_key() == active.key
    assert panel._class_widgets[active.key].header.property("active") is True
    panel._set_active_class_key(active.key)
    assert panel._selected_feature_indices_from_table() == {first, second, third}
    widgets = panel._class_widgets[active.key]

    panel._set_class_card_expanded(active.key, False)
    assert widgets.body_scroll.isHidden()

    panel._toggle_active_class_key(active.key)
    assert panel._current_class_key() == ""
    assert not panel._queue_class_btn.isEnabled()
    assert panel._selected_feature_indices_from_table().isdisjoint({first, second, third})


def test_preview_panel_scan_tray_exposes_direct_scan_actions(tmp_path) -> None:
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

    assert panel._features_btn.property("role") == "accent"
    assert panel._classify_btn.property("role") == "accent"
    assert panel._scan_selected_btn.property("role") == "primary"
    assert panel._queue_class_btn.property("role") == "primary"
    assert panel._source_section._toggle.objectName() == "previewTrayHeader"
    assert panel._classification_section._toggle.objectName() == "previewTrayHeader"
    assert panel._scan_section._toggle.objectName() == "previewTrayHeader"
    for button in (
        panel._scan_selected_btn,
        panel._queue_class_btn,
        panel._scan_groups_btn,
        panel._stop_scan_btn,
    ):
        assert "background-color" in button.styleSheet()
        assert "border:" in button.styleSheet()
    assert not panel._scan_selected_btn.isHidden()
    assert not panel._scan_groups_btn.isHidden()
    assert panel._scan_selected_btn.isEnabled()
    assert panel._scan_groups_btn.isEnabled()
    assert not panel._stop_scan_btn.isEnabled()

    panel._add_class()
    assert panel._queue_class_btn.isEnabled()


def test_preview_panel_allocates_next_default_class_name(tmp_path) -> None:
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

    panel._add_class()
    panel._toggle_active_class_key(panel._current_class_key())

    panel._add_class()
    active = panel._active_class_record()
    assert active is not None
    assert active.label == "Name 2"
    assert active.key == "name 2"
    assert panel._class_widgets[active.key].name_edit.text() == ""
    assert panel._class_widgets[active.key].name_edit.placeholderText() == "Name 2"


def test_preview_panel_can_assign_samples_to_multiple_default_classes(tmp_path) -> None:
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
    rows = panel._current_state.feature_rows[:3]
    assert len(rows) == 3

    panel._add_class()
    panel._toggle_feature_selection(rows[0].index)
    panel._label_selected_samples()

    panel._set_all_selected(False)
    panel._add_class()
    panel._toggle_feature_selection(rows[1].index)
    panel._label_selected_samples()

    panel._set_all_selected(False)
    panel._add_class()
    panel._toggle_feature_selection(rows[2].index)
    panel._label_selected_samples()

    labels = panel._current_state.sample_labels
    assert labels[rows[0].index] == "Name 1"
    assert labels[rows[1].index] == "Name 2"
    assert labels[rows[2].index] == "Name 3"


def test_preview_panel_preserves_zoom_when_selecting_samples(tmp_path) -> None:
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

    panel._add_class()
    panel._viewer._plot.setRange(xRange=(20.0, 60.0), yRange=(15.0, 45.0), padding=0.0)
    before = panel._viewer._plot.viewRange()

    first_index = panel._current_state.feature_rows[0].index
    panel._toggle_feature_selection(first_index)

    after = panel._viewer._plot.viewRange()
    assert after[0][0] == before[0][0]
    assert after[0][1] == before[0][1]
    assert after[1][0] == before[1][0]
    assert after[1][1] == before[1][1]


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
