"""Tripwires for documented-but-unresolved findings.

These tests fail if an open-finding FIXME marker disappears from the code,
e.g. during a refactor that rewrites the surrounding function. They are NOT
a license to keep the findings open — when a finding is properly resolved,
remove its markers AND its test here in the same commit.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "scanflow"


def test_b2_frame_resize_markers_present():
    """Finding B2 (REVIEW.md 2026-06-10): survey vs mosaic/preview disagree on
    what a Createc frame resize preserves (centre vs top edge). Until the rig
    experiment settles it, the FIXME must stay attached to all three sites:
    the suspect survey path, the convention source-of-truth, and the mock
    that cannot yet detect the bug.
    """
    marker = "FIXME(B2-frame-resize)"
    expected = [
        _ROOT / "automation" / "runner.py",
        _ROOT / "core" / "scan_geometry.py",
        _ROOT / "core" / "mock_dispatch.py",
    ]
    missing = [
        str(p) for p in expected
        if marker not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"{marker} marker missing from {missing} — if finding B2 is now "
        "resolved (frame-resize semantics verified on the rig and all "
        "workflows unified on scan_geometry), delete this test together "
        "with the markers; otherwise restore the FIXME comments."
    )
