"""Spatial grouping of segmented features for the Preview → group-scan workflow.

Given a list of ``PreviewFeatureRow`` objects (from ProbeFlow's segmentation),
cluster them into groups whose union bounding box fits within a single STM
scan frame of at most *max_group_nm*.  Each group is then scanned in one
shot by ``_FeatureGroupScanWorker`` in ``preview_panel.py``.

The algorithm is intentionally simple and deterministic — no randomness, no
global optimum search, just a greedy nearest-neighbour loop that is easy to
reason about when debugging a live experiment at 2 am.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from probeflow.analysis.preview import PreviewFeatureRow


@dataclass
class FeatureGroup:
    """A spatial cluster of detected surface features to scan in one frame.

    Attributes
    ----------
    index
        1-based group number (for display/filenames).
    members
        The ``PreviewFeatureRow`` objects that belong to this group.
    center_dx_nm
        X offset of the group's scan-frame centre from the wide-scan centre,
        in nm.  Passed directly to ``TipMotionManager.move_absolute_nm``
        as ``home_x + center_dx_nm``.
    center_dy_nm
        Y offset, same convention as above.
    frame_nm
        (width_nm, height_nm) of the scan frame required to contain every
        feature in the group with padding on all sides.
    """
    index: int
    members: List["PreviewFeatureRow"]
    center_dx_nm: float
    center_dy_nm: float
    frame_nm: Tuple[float, float]


def group_features(
    rows: List["PreviewFeatureRow"],
    scan_range_m: Optional[Tuple[float, float]],
    scan_shape: Tuple[int, int],
    *,
    max_per_group: int = 4,
    max_group_nm: float = 30.0,
    feature_padding_nm: float = 3.0,
    min_group_nm: float = 3.0,
) -> List[FeatureGroup]:
    """Cluster *rows* into groups that each fit within *max_group_nm*.

    Parameters
    ----------
    rows
        Detected features from ProbeFlow's Preview segmentation (already
        filtered / selected by the user in the GUI).
    scan_range_m
        Physical size of the source scan in metres ``(range_x_m, range_y_m)``.
        Used to convert pixel bbox coordinates to nm.  When ``None`` the
        algorithm falls back to treating every feature as a point (centroid
        only, no bbox extent).
    scan_shape
        ``(ny, nx)`` pixel dimensions of the source scan plane.
    max_per_group
        Maximum number of features in one group (2–8 recommended).
    max_group_nm
        Maximum side length (nm) of the bounding box that contains all
        features in one group plus padding.  Groups whose features cannot all
        fit in this frame are split into smaller groups.
    feature_padding_nm
        Extra margin (nm) added on each side of the union bounding box when
        computing the required frame size and the "fits" test.
    min_group_nm
        Minimum side length (nm) for a group frame — even a single-feature
        group gets at least this frame size.

    Returns
    -------
    List[FeatureGroup]
        One entry per group, in top-to-bottom scan order.
    """
    if not rows:
        return []

    ny, nx = int(scan_shape[0]), int(scan_shape[1])

    # ── Physical scale ────────────────────────────────────────────────────
    if scan_range_m is not None:
        nm_per_px_x = float(scan_range_m[0]) * 1e9 / max(nx, 1)
        nm_per_px_y = float(scan_range_m[1]) * 1e9 / max(ny, 1)
        have_scale = True
    else:
        nm_per_px_x = 1.0
        nm_per_px_y = 1.0
        have_scale = False

    center_col = nx / 2.0
    center_row = ny / 2.0

    # ── Per-feature nm bounding box (xmin, ymin, xmax, ymax) ─────────────
    # All values are nm offsets from the scan centre, so they match
    # PreviewFeatureRow.dx_nm / dy_nm directly.
    def _bbox_nm(row: "PreviewFeatureRow") -> Tuple[float, float, float, float]:
        if have_scale and row.bbox_px is not None:
            # bbox_px = (x0_col, y0_row, x1_col, y1_row)
            bx0, by0, bx1, by1 = row.bbox_px
            return (
                (float(bx0) - center_col) * nm_per_px_x,
                (float(by0) - center_row) * nm_per_px_y,
                (float(bx1) - center_col) * nm_per_px_x,
                (float(by1) - center_row) * nm_per_px_y,
            )
        # No bbox or no scale — treat as a point at the centroid
        d, e = float(row.dx_nm), float(row.dy_nm)
        return (d, e, d, e)

    bboxes = [_bbox_nm(r) for r in rows]

    # ── Helper: union bbox and "fits" test ────────────────────────────────
    def _union(indices: list[int]) -> Tuple[float, float, float, float]:
        xs0 = [bboxes[i][0] for i in indices]
        ys0 = [bboxes[i][1] for i in indices]
        xs1 = [bboxes[i][2] for i in indices]
        ys1 = [bboxes[i][3] for i in indices]
        return min(xs0), min(ys0), max(xs1), max(ys1)

    def _fits(indices: list[int]) -> bool:
        x0, y0, x1, y1 = _union(indices)
        w = (x1 - x0) + 2.0 * feature_padding_nm
        h = (y1 - y0) + 2.0 * feature_padding_nm
        return max(w, h) <= max_group_nm

    # ── Sort top-to-bottom, left-to-right (dy ascending then dx ascending) ─
    order = sorted(range(len(rows)),
                   key=lambda i: (float(rows[i].dy_nm), float(rows[i].dx_nm)))

    unassigned: set[int] = set(range(len(rows)))
    groups: List[FeatureGroup] = []

    for seed_orig_idx in order:
        if seed_orig_idx not in unassigned:
            continue

        members: list[int] = [seed_orig_idx]
        unassigned.discard(seed_orig_idx)

        # Greedily add nearest features that keep the union bbox within limit
        while len(members) < max_per_group and unassigned:
            # Current group centroid (by dx_nm/dy_nm of centroids)
            gx = sum(float(rows[i].dx_nm) for i in members) / len(members)
            gy = sum(float(rows[i].dy_nm) for i in members) / len(members)

            # Rank remaining features by distance to centroid
            by_dist = sorted(
                unassigned,
                key=lambda i: (
                    (float(rows[i].dx_nm) - gx) ** 2
                    + (float(rows[i].dy_nm) - gy) ** 2
                ),
            )

            added = False
            for cand in by_dist:
                if _fits(members + [cand]):
                    members.append(cand)
                    unassigned.discard(cand)
                    added = True
                    break  # recompute centroid before trying the next candidate

            if not added:
                # No remaining feature fits inside this group's frame → done
                break

        # ── Compute final frame size and centre ──────────────────────────
        x0, y0, x1, y1 = _union(members)

        # Width / height with padding, at least min_group_nm, at most max_group_nm
        w = max((x1 - x0) + 2.0 * feature_padding_nm, float(min_group_nm))
        h = max((y1 - y0) + 2.0 * feature_padding_nm, float(min_group_nm))
        side = min(max(w, h), float(max_group_nm))

        # Centre = geometric midpoint of the union bbox (symmetric around all
        # features, not centroid of centroids, so the frame is not biased
        # toward clusters of tightly packed features)
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0

        groups.append(FeatureGroup(
            index=len(groups) + 1,
            members=[rows[i] for i in members],
            center_dx_nm=cx,
            center_dy_nm=cy,
            frame_nm=(side, side),
        ))

    return groups
