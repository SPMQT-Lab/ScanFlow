"""Createc scan-frame coordinate conventions, in one obvious place.

Createc's SCAN.OFFSET keys do **not** use a single convention:

* ``SCAN.OFFSET.X.NM`` — **CENTRE** of the scan frame in X (positive = right)
* ``SCAN.OFFSET.Y.NM`` — **TOP EDGE** (first scanline) of the scan frame in Y
  (positive = down — the Y axis increases moving downward through the image)

That asymmetry has caused at least three positioning bugs in ScanFlow's
preview workers and mosaic runner. The functions in this module are the
single source of truth for converting between Createc offsets, image
centres, and external frame coordinates (e.g. ProbeFlow's
``dx_nm`` / ``dy_nm`` offsets, which are measured from the **image
centre**).

Use these helpers instead of writing the arithmetic inline; the wrong
sign or a missing ``/2`` is easy to miss in a worker.
"""

from __future__ import annotations


def image_center_y_nm(home_y_nm: float, scan_range_y_nm: float) -> float:
    """Y coordinate of the wide-scan image centre.

    ``home_y_nm`` is SCAN.OFFSET.Y.NM (top edge); the centre is half a
    scan height below it.
    """
    return home_y_nm + scan_range_y_nm / 2.0


def frame_top_edge_y_nm(
    image_center_y_nm: float,
    feature_dy_nm: float,
    frame_height_nm: float,
) -> float:
    """Top-edge Y (the value to write to SCAN.OFFSET.Y.NM) for a new frame
    centred on a feature at offset ``feature_dy_nm`` below the wide-scan
    image centre.

    Equivalent to ``image_center_y + dy − height/2``.
    """
    return image_center_y_nm + feature_dy_nm - frame_height_nm / 2.0


def feature_target_xy_nm(
    home_x_nm: float,
    home_y_nm: float,
    scan_range_y_nm: float,
    feature_dx_nm: float,
    feature_dy_nm: float,
    frame_height_nm: float,
) -> tuple[float, float]:
    """Absolute (X_centre, Y_top_edge) target for a feature follow-up scan.

    Inputs:
      * ``home_x_nm`` — wide-scan SCAN.OFFSET.X.NM (already the centre, no shift)
      * ``home_y_nm`` — wide-scan SCAN.OFFSET.Y.NM (top edge)
      * ``scan_range_y_nm`` — physical height of the wide scan
      * ``feature_d{x,y}_nm`` — feature offset from the wide-scan image centre
        (ProbeFlow convention: positive dy = below centre = larger Createc Y)
      * ``frame_height_nm`` — height of the *new* (smaller) scan frame

    Output is what should be written to SCAN.OFFSET.{X,Y}.NM.
    """
    target_x = home_x_nm + feature_dx_nm
    center_y = image_center_y_nm(home_y_nm, scan_range_y_nm)
    target_y = frame_top_edge_y_nm(center_y, feature_dy_nm, frame_height_nm)
    return target_x, target_y


def clamp_frame_to_wide_bounds(
    target_x_centre: float,
    target_y_top_edge: float,
    *,
    home_x_nm: float,
    home_y_nm: float,
    scan_range_x_nm: float,
    scan_range_y_nm: float,
    frame_width_nm: float,
    frame_height_nm: float,
) -> tuple[float, float]:
    """Clamp a (X_centre, Y_top_edge) target so the new frame stays
    inside the wide-scan boundary.

    For X (centre convention): clamp the centre so that
    ``centre ± frame_width/2`` is within ``home_x ± scan_range_x/2``.

    For Y (top-edge convention): clamp the top edge so that
    ``[top_edge, top_edge + frame_height]`` is within
    ``[home_y, home_y + scan_range_y]``.

    If the new frame is larger than the wide scan on an axis, no clamping
    is applied for that axis (caller decides what to do).

    Returns the clamped ``(x, y)`` — equal to the input if no clamp was needed.
    """
    half_fx = frame_width_nm / 2.0
    if frame_width_nm <= scan_range_x_nm:
        clamped_x = max(
            home_x_nm - scan_range_x_nm / 2.0 + half_fx,
            min(home_x_nm + scan_range_x_nm / 2.0 - half_fx, target_x_centre),
        )
    else:
        clamped_x = target_x_centre

    if frame_height_nm <= scan_range_y_nm:
        clamped_y = max(
            home_y_nm,
            min(home_y_nm + scan_range_y_nm - frame_height_nm, target_y_top_edge),
        )
    else:
        clamped_y = target_y_top_edge

    return clamped_x, clamped_y
