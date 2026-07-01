"""Coordinate-frame identifiers shared by control, analysis, and ML layers.

Every position that crosses a layer boundary (`Feature.frame`,
`ProposedAction.frame`, `ScanRecord.coordinate_system`) MUST name its
coordinate frame with one of these identifiers. Bare (x, y) tuples with an
implicit convention are how ScanFlow's positioning bugs happened — the
Createc X/Y conventions differ from each other and from ProbeFlow's, and a
missing half-frame term is invisible in a tuple.

These are identifiers only. The arithmetic for converting between frames
lives in :mod:`scanflow.core.scan_geometry` (the single source of truth) —
contracts stays dependency-free and does not perform conversions.

FIXME(B2-frame-resize) note: what STMAFM preserves when the frame *size*
changes (top edge vs centre) is still unverified on the rig — see
scan_geometry.py. ``CREATEC_SCAN_OFFSET_NM`` documents the readback
convention, which is not affected by that open question.
"""

from __future__ import annotations

#: Pixel coordinates in a specific image: (col, row), row 0 = first
#: scanline (top). Only meaningful next to the image that defines them.
IMAGE_PIXELS = "image_pixels"

#: Nanometre offsets measured from the centre of a specific image,
#: positive dx = right, positive dy = DOWN (toward later scanlines).
#: This is ProbeFlow's dx_nm/dy_nm convention.
IMAGE_CENTER_RELATIVE_NM = "image_center_relative_nm"

#: Absolute values as written to / read from Createc SCAN.OFFSET.{X,Y}.NM:
#: X = CENTRE of the scan frame, Y = TOP EDGE (first scanline) of the scan
#: frame, Y increasing downward. Note the X/Y asymmetry — see
#: scanflow/core/scan_geometry.py before doing any arithmetic with these.
CREATEC_SCAN_OFFSET_NM = "createc_scan_offset_nm"

#: All frames a `frame` / `coordinate_system` field may carry.
KNOWN_FRAMES = frozenset({
    IMAGE_PIXELS,
    IMAGE_CENTER_RELATIVE_NM,
    CREATEC_SCAN_OFFSET_NM,
})


def require_known_frame(frame: str, *, context: str = "") -> str:
    """Return ``frame`` if recognised, raise ValueError otherwise."""
    if frame not in KNOWN_FRAMES:
        where = f" in {context}" if context else ""
        raise ValueError(
            f"unknown coordinate frame {frame!r}{where}; "
            f"expected one of {sorted(KNOWN_FRAMES)}"
        )
    return frame
