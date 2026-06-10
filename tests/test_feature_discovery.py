"""Tests for the bright-feature discovery used by the Survey workflow."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scanflow.automation.feature_discovery import discover_features
from scanflow.automation.survey import (
    SurveyConfig, FeatureRecord, SurveyManifest,
)


def _blob_image(positions, *, size=256, sigma=4.0, amp=1.0):
    """Render a 2-D image with one Gaussian per (x, y) position."""
    img = np.zeros((size, size))
    x, y = np.meshgrid(np.arange(size), np.arange(size))
    for px, py in positions:
        img = img + amp * np.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sigma ** 2))
    return img


def test_discovers_isolated_features():
    # Wide-scan: 120 nm in 256 px → ~0.47 nm/px. Place 4 well-separated blobs.
    positions = [(60, 60), (180, 60), (60, 180), (180, 180)]
    img = _blob_image(positions, size=256, sigma=4.0)
    nm_per_px = 120.0 / 256
    candidates = discover_features(
        img, nm_per_px,
        min_feature_nm=0.5, max_feature_nm=20.0,
        merge_distance_nm=0.0, edge_margin_px=8,
    )
    assert len(candidates) == 4
    # Each candidate has a zoom size between min and max
    for c in candidates:
        assert 3.0 <= c.zoom_nm[0] <= 30.0
        assert 0.5 <= c.char_dim_nm <= 20.0


def test_clusters_merged_into_one():
    # Two blobs separated by enough gap that Otsu segments them independently
    # at merge=0 (no closing), but the configured merge radius bridges them.
    img = _blob_image([(120, 128), (148, 128)], size=256, sigma=2.5)
    nm_per_px = 120.0 / 256  # ≈ 0.47 nm/px
    unmerged = discover_features(
        img, nm_per_px,
        min_feature_nm=0.3, max_feature_nm=30.0,
        merge_distance_nm=0.0, edge_margin_px=8,
    )
    merged = discover_features(
        img, nm_per_px,
        min_feature_nm=0.3, max_feature_nm=30.0,
        merge_distance_nm=15.0, edge_margin_px=8,
    )
    assert len(unmerged) == 2
    assert len(merged) == 1


def test_size_filter_rejects_tiny_and_huge():
    img = _blob_image([(128, 128)], size=256, sigma=4.0)
    nm_per_px = 120.0 / 256
    # The blob is ~4-5 nm across — outside [0.1, 1] nm window
    rejected = discover_features(
        img, nm_per_px,
        min_feature_nm=0.1, max_feature_nm=1.0,
        merge_distance_nm=0.0, edge_margin_px=8,
    )
    assert rejected == []


def test_edge_features_rejected():
    img = _blob_image([(8, 128)], size=256, sigma=4.0)  # 8 px from left edge
    nm_per_px = 120.0 / 256
    out = discover_features(
        img, nm_per_px,
        min_feature_nm=0.5, max_feature_nm=20.0,
        merge_distance_nm=0.0, edge_margin_px=32,
    )
    assert out == []


def test_max_features_caps_results():
    positions = [(40 + 30 * i, 40 + 30 * j) for i in range(5) for j in range(5)]
    img = _blob_image(positions, size=256, sigma=3.0)
    nm_per_px = 120.0 / 256
    out = discover_features(
        img, nm_per_px,
        min_feature_nm=0.5, max_feature_nm=20.0,
        merge_distance_nm=0.0, edge_margin_px=8,
        max_features=5,
    )
    assert len(out) == 5


def test_survey_manifest_roundtrip(tmp_path: Path):
    m = SurveyManifest(
        name="Test", timestamp="2026-05-14T10:00:00",
        wide_scan_path="/data/wide.dat",
        wide_preview_path="/data/wide.png",
        wide_size_nm=(120.0, 120.0),
        wide_pixels=(512, 512),
        features=[
            FeatureRecord(
                index=1,
                centroid_pixels=(100.0, 200.0),
                centroid_nm_offset=(-5.0, 12.0),
                char_dim_nm=1.4,
                zoom_size_nm=(3.0, 3.0),
                bias_V=0.1,
                setpoint_A=5e-11,
                scan_paths=["/data/f01_iter1.dat", "/data/f01_iter2.dat"],
                preview_paths=["/data/f01_iter1.png"],
                drift_log_angstrom=[(0.6, -0.2), (0.1, 0.05)],
                final_residual_angstrom=(0.1, 0.05),
            )
        ],
    )
    path = tmp_path / "survey.json"
    m.save(path)
    loaded = SurveyManifest.load(path)
    assert loaded.name == "Test"
    assert len(loaded.features) == 1
    f = loaded.features[0]
    assert f.index == 1
    assert f.centroid_nm_offset == (-5.0, 12.0)
    assert f.drift_log_angstrom == [(0.6, -0.2), (0.1, 0.05)]
    assert f.final_residual_angstrom == (0.1, 0.05)


def test_pptx_export_writes_file(tmp_path: Path):
    # Build a tiny manifest pointing to a dummy PNG so the writer succeeds end-to-end.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    img = _blob_image([(128, 128)], size=256, sigma=4.0)
    png = tmp_path / "feature.png"
    fig, ax = plt.subplots()
    ax.imshow(img)
    ax.set_axis_off()
    fig.savefig(png, bbox_inches="tight")
    plt.close(fig)

    overview = tmp_path / "wide_annotated.png"
    fig, ax = plt.subplots()
    ax.imshow(img)
    ax.set_axis_off()
    fig.savefig(overview, bbox_inches="tight")
    plt.close(fig)

    manifest = SurveyManifest(
        name="Demo",
        timestamp="2026-05-14T10:00:00",
        wide_preview_path=str(tmp_path / "wide.png"),  # resolver looks for wide_annotated.png
        wide_size_nm=(120.0, 120.0),
        wide_pixels=(256, 256),
        features=[
            FeatureRecord(
                index=1,
                centroid_pixels=(128.0, 128.0),
                centroid_nm_offset=(0.0, 0.0),
                char_dim_nm=2.0,
                zoom_size_nm=(5.0, 5.0),
                bias_V=0.1,
                setpoint_A=5e-11,
                scan_paths=[str(tmp_path / "f1.dat")],
                preview_paths=[str(png)],
                drift_log_angstrom=[(0.4, 0.1)],
                final_residual_angstrom=(0.4, 0.1),
            ),
        ],
    )

    from scanflow.io.pptx_export import export_pptx
    out = tmp_path / "deck.pptx"
    export_pptx(manifest, out)
    assert out.exists()
    assert out.stat().st_size > 1000  # non-trivially sized
