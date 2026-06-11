"""Bright-feature discovery for the survey workflow.

Given a wide STM image and its physical scale, segment bright features,
optionally merge nearby pieces (so a cluster reads as one feature),
filter by physical size and edge margin, and return sorted candidates
with auto-sized zoom frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

def _level_correct(img: np.ndarray) -> np.ndarray:
    """Subtract a fitted plane from a 2-D image array (de-tilts the wide scan).

    NaN-safe by necessity: real rigs emit NaN rows in partial/interrupted
    frames. The plane is fitted on FINITE pixels only — fitting through
    NaNs silently poisons the whole result on some LAPACKs and raises
    "SVD did not converge" outright on others (Windows lab PC — the
    historical "NaN crashes the program" failure). Non-finite pixels stay
    non-finite in the output; callers decide how to fill them.
    """
    img = np.asarray(img, dtype=float)
    m, n = img.shape
    finite = np.isfinite(img)
    x1, x2 = np.mgrid[:m, :n]
    X = np.column_stack([np.ones(m * n), x1.ravel(), x2.ravel()])
    Y = img.ravel()
    mask = finite.ravel()
    if mask.sum() < 8:
        # Not enough data for a plane — best effort: remove the median.
        offset = float(np.nanmedian(img)) if mask.any() else 0.0
        return img - offset
    try:
        theta, *_ = np.linalg.lstsq(X[mask], Y[mask], rcond=None)
    except np.linalg.LinAlgError:
        return img - float(np.nanmedian(img))
    plane = (X @ theta).reshape(m, n)
    return img - plane


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
    # Fill non-finite pixels (NaN rows from partial/interrupted frames)
    # with the MEDIAN of the valid region: a median-filled block looks
    # like flat terrace to Otsu (filling with the minimum would skew the
    # histogram so far that the whole terrace thresholds as "bright"),
    # so features in the valid region remain detectable and the blank
    # region can never produce features of its own.
    finite = np.isfinite(levelled)
    if not finite.any():
        return []
    if not finite.all():
        levelled = np.where(finite, levelled, float(np.median(levelled[finite])))
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
