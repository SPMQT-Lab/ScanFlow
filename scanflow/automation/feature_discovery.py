"""Bright-feature discovery for the survey workflow.

Given a wide STM image and its physical scale, segment bright features,
optionally merge nearby pieces (so a cluster reads as one feature),
filter by physical size and edge margin, and return sorted candidates
with auto-sized zoom frames.

Reuses the same Otsu + level-correct pipeline as :mod:`scanflow.drift.detector`,
but returns bounding-box information so the runner can scale each zoom to the
actual feature size — handles 1.2 nm molecules and 10 nm clusters with the
same code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from scanflow.drift.detector import _level_correct


@dataclass
class FeatureCandidate:
    cx_px: float                                   # centroid x in pixels
    cy_px: float                                   # centroid y in pixels
    bbox_px: Tuple[int, int, int, int]             # min_row, min_col, max_row, max_col
    char_dim_nm: float                             # characteristic size = max(W, H)
    zoom_nm: Tuple[float, float]                   # auto-sized zoom frame
    mean_intensity: float


def discover_features(
    image: np.ndarray,
    nm_per_pixel: float,
    *,
    min_feature_nm: float = 0.8,
    max_feature_nm: float = 20.0,
    size_multiplier: float = 2.0,
    min_zoom_nm: float = 3.0,
    max_zoom_nm: float = 30.0,
    merge_distance_nm: float = 0.5,
    edge_margin_px: int = 16,
    max_features: int = 30,
) -> List[FeatureCandidate]:
    """Segment and rank bright features in an STM topography image."""
    if image.ndim != 2 or image.size == 0:
        return []

    from skimage.filters import threshold_otsu, gaussian
    from skimage.exposure import rescale_intensity
    from skimage.morphology import binary_closing, disk
    from skimage.measure import label, regionprops

    levelled = _level_correct(image)
    smoothed = gaussian(rescale_intensity(levelled.astype(float)), sigma=1.0)
    try:
        thresh = threshold_otsu(smoothed)
    except ValueError:
        return []
    mask = smoothed > thresh
    if not mask.any():
        return []

    # Morphologically close the mask so the pieces of a cluster fuse into a
    # single labelled region. Without this, a 5-monomer cluster would generate
    # 5 separate slides instead of one zoom of the whole aggregate.
    r = max(1, int(round(merge_distance_nm / max(nm_per_pixel, 1e-9))))
    if r > 1:
        mask = binary_closing(mask, disk(r))

    labels = label(mask)
    ny, nx = image.shape
    candidates: List[FeatureCandidate] = []

    for region in regionprops(labels, intensity_image=smoothed):
        min_row, min_col, max_row, max_col = region.bbox

        # Reject features touching or near the frame edge — they're likely
        # truncated and the zoom would clip part of the feature anyway.
        if (min_row < edge_margin_px or min_col < edge_margin_px or
                max_row > ny - edge_margin_px or max_col > nx - edge_margin_px):
            continue

        w_nm = (max_col - min_col) * nm_per_pixel
        h_nm = (max_row - min_row) * nm_per_pixel
        char_dim_nm = float(max(w_nm, h_nm))
        if not (min_feature_nm <= char_dim_nm <= max_feature_nm):
            continue

        zoom = float(np.clip(char_dim_nm * size_multiplier, min_zoom_nm, max_zoom_nm))
        cy, cx = region.centroid
        candidates.append(FeatureCandidate(
            cx_px=float(cx),
            cy_px=float(cy),
            bbox_px=(int(min_row), int(min_col), int(max_row), int(max_col)),
            char_dim_nm=char_dim_nm,
            zoom_nm=(zoom, zoom),
            mean_intensity=float(region.mean_intensity),
        ))

    candidates.sort(key=lambda c: c.mean_intensity, reverse=True)
    return candidates[:max_features]
