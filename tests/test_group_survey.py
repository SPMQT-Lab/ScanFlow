"""Unit tests for scanflow.automation.group_survey.group_features()."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from scanflow.automation.group_survey import FeatureGroup, group_features


# ---------------------------------------------------------------------------
# Helpers — build minimal PreviewFeatureRow-like objects without importing
# the full ProbeFlow stack.
# ---------------------------------------------------------------------------

def _row(index: int, dx_nm: float, dy_nm: float,
         bbox_px=None, area_nm2: float = 1.0):
    """Return a mock PreviewFeatureRow with the fields group_features() reads."""
    r = MagicMock()
    r.index = index
    r.dx_nm = dx_nm
    r.dy_nm = dy_nm
    r.x_px = 256.0 + dx_nm  # not important for grouping
    r.y_px = 256.0 + dy_nm
    r.bbox_px = bbox_px      # (x0, y0, x1, y1) in pixels, or None
    r.area_nm2 = area_nm2
    r.score = 1.0
    r.label = ""
    r.source = "mock"
    return r


# scan_range_m = (100e-9, 100e-9)  → 512 px image → ≈ 0.195 nm/px
SCAN_RANGE = (100e-9, 100e-9)
SCAN_SHAPE = (512, 512)  # (ny, nx)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGroupFeaturesEmpty:
    def test_empty_input_returns_empty(self):
        result = group_features([], SCAN_RANGE, SCAN_SHAPE)
        assert result == []


class TestGroupFeaturesSingle:
    def test_single_feature_makes_one_group(self):
        rows = [_row(0, 0.0, 0.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0,
                                feature_padding_nm=3.0, min_group_nm=3.0)
        assert len(groups) == 1
        assert len(groups[0].members) == 1
        assert groups[0].members[0] is rows[0]

    def test_single_feature_frame_at_least_min_group_nm(self):
        rows = [_row(0, 0.0, 0.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                min_group_nm=5.0, feature_padding_nm=1.0,
                                max_group_nm=30.0)
        assert groups[0].frame_nm[0] >= 5.0
        assert groups[0].frame_nm[1] >= 5.0

    def test_single_feature_frame_is_square(self):
        rows = [_row(0, 0.0, 0.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE)
        w, h = groups[0].frame_nm
        assert abs(w - h) < 1e-9


class TestGroupFeaturesAllInOne:
    def test_three_nearby_features_form_one_group(self):
        """Three features within 5 nm of each other should land in one group."""
        rows = [_row(0, 0.0, 0.0), _row(1, 2.0, 0.0), _row(2, 1.0, 2.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0,
                                feature_padding_nm=2.0)
        assert len(groups) == 1
        assert len(groups[0].members) == 3

    def test_two_features_same_position_form_one_group(self):
        rows = [_row(0, 5.0, 5.0), _row(1, 5.1, 5.1)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0)
        assert len(groups) == 1
        assert len(groups[0].members) == 2


class TestGroupFeaturesForced:
    def test_max_per_group_respected(self):
        """5 nearby features with max_per_group=3 → at least 2 groups."""
        rows = [_row(i, float(i) * 0.5, 0.0) for i in range(5)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=3, max_group_nm=50.0,
                                feature_padding_nm=1.0)
        assert sum(len(g.members) for g in groups) == 5
        for g in groups:
            assert len(g.members) <= 3

    def test_all_features_assigned(self):
        """No feature should be dropped."""
        rows = [_row(i, float(i) * 3.0, 0.0) for i in range(10)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=20.0,
                                feature_padding_nm=2.0)
        assigned = [m for g in groups for m in g.members]
        assert len(assigned) == 10


class TestGroupFeaturesSeparate:
    def test_two_far_apart_features_form_two_groups(self):
        """Features 50 nm apart with max_group_nm=30 → 2 groups."""
        rows = [_row(0, -25.0, 0.0), _row(1, 25.0, 0.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0,
                                feature_padding_nm=2.0)
        assert len(groups) == 2
        assert len(groups[0].members) == 1
        assert len(groups[1].members) == 1

    def test_two_close_one_far(self):
        """Close pair stays together; far outlier gets its own group."""
        rows = [
            _row(0, 0.0, 0.0),
            _row(1, 2.0, 0.0),
            _row(2, 40.0, 0.0),  # far outlier
        ]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=20.0,
                                feature_padding_nm=2.0)
        # Close pair in one group, outlier alone
        assert len(groups) == 2
        sizes = sorted(len(g.members) for g in groups)
        assert sizes == [1, 2]


class TestGroupFeaturesFrame:
    def test_frame_capped_at_max_group_nm(self):
        """Frame size must never exceed max_group_nm."""
        rows = [_row(i, float(i) * 2.0, 0.0) for i in range(3)]
        mx = 20.0
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=mx,
                                feature_padding_nm=1.0)
        for g in groups:
            assert g.frame_nm[0] <= mx + 1e-9
            assert g.frame_nm[1] <= mx + 1e-9

    def test_centre_is_bbox_midpoint_for_symmetric_pair(self):
        """Two features symmetric around origin → group centre at (0, 0)."""
        rows = [_row(0, -5.0, 0.0), _row(1, 5.0, 0.0)]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0,
                                feature_padding_nm=1.0)
        assert len(groups) == 1
        assert abs(groups[0].center_dx_nm) < 1e-6
        assert abs(groups[0].center_dy_nm) < 1e-6


class TestGroupFeaturesNullRange:
    def test_works_without_scan_range(self):
        """When scan_range_m is None, centroid-only mode must not crash."""
        rows = [_row(i, float(i) * 5.0, 0.0) for i in range(4)]
        groups = group_features(rows, None, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=30.0,
                                feature_padding_nm=2.0)
        assert sum(len(g.members) for g in groups) == 4


class TestGroupFeaturesWithBbox:
    def test_bbox_used_for_frame_calculation(self):
        """Features with large bboxes should produce a bigger frame than
        features with the same centroid but no bbox."""
        # Two features 2 nm apart, with bboxes 4 nm wide each
        # bbox_px: (x0, y0, x1, y1) — use pixel coords at nm_per_px ≈ 0.195
        # 4 nm / 0.195 nm/px ≈ 20 px
        nm_per_px = SCAN_RANGE[0] * 1e9 / SCAN_SHAPE[1]  # ≈ 0.195
        cx, cy = SCAN_SHAPE[1] / 2.0, SCAN_SHAPE[0] / 2.0
        half_w_px = int(4.0 / nm_per_px / 2)

        rows_with_bbox = [
            _row(0, -2.0, 0.0,
                 bbox_px=(int(cx - 2 / nm_per_px) - half_w_px,
                          int(cy) - half_w_px,
                          int(cx - 2 / nm_per_px) + half_w_px,
                          int(cy) + half_w_px)),
            _row(1,  2.0, 0.0,
                 bbox_px=(int(cx + 2 / nm_per_px) - half_w_px,
                          int(cy) - half_w_px,
                          int(cx + 2 / nm_per_px) + half_w_px,
                          int(cy) + half_w_px)),
        ]
        rows_no_bbox = [_row(0, -2.0, 0.0), _row(1, 2.0, 0.0)]

        g_with = group_features(rows_with_bbox, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=4, max_group_nm=50.0,
                                feature_padding_nm=1.0)
        g_no = group_features(rows_no_bbox, SCAN_RANGE, SCAN_SHAPE,
                              max_per_group=4, max_group_nm=50.0,
                              feature_padding_nm=1.0)
        # Both end up in 1 group (features are close enough)
        assert len(g_with) == 1
        assert len(g_no) == 1
        # Bbox-informed frame should be larger (accounts for feature extent)
        assert g_with[0].frame_nm[0] >= g_no[0].frame_nm[0] - 1e-6


class TestGroupFeaturesOrdering:
    def test_groups_top_to_bottom_order(self):
        """Groups are returned in top-to-bottom (increasing dy), left-to-right order."""
        rows = [
            _row(0,  0.0, 20.0),   # bottom
            _row(1,  0.0, -20.0),  # top
            _row(2,  0.0, 0.0),    # middle
        ]
        groups = group_features(rows, SCAN_RANGE, SCAN_SHAPE,
                                max_per_group=1, max_group_nm=5.0,
                                feature_padding_nm=1.0)
        assert len(groups) == 3
        dy_values = [g.center_dy_nm for g in groups]
        assert dy_values == sorted(dy_values)

    def test_deterministic_output(self):
        """Same input always produces same output (no randomness)."""
        rows = [_row(i, float(i % 5) * 3.0, float(i // 5) * 3.0) for i in range(12)]
        g1 = group_features(rows, SCAN_RANGE, SCAN_SHAPE)
        g2 = group_features(rows, SCAN_RANGE, SCAN_SHAPE)
        assert len(g1) == len(g2)
        for a, b in zip(g1, g2):
            assert len(a.members) == len(b.members)
            assert abs(a.center_dx_nm - b.center_dx_nm) < 1e-9
            assert abs(a.center_dy_nm - b.center_dy_nm) < 1e-9
