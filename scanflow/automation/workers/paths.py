"""Filename helpers for scan-data files (.dat).

These were previously module-level helpers inside preview_panel.py.
Moved here so worker modules and automation runners can share them
without dragging GUI imports along.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def latest_dat_in_folder(folder: Path) -> Path | None:
    """Return the most recently modified .dat in ``folder``, or None."""
    if not folder.exists():
        return None
    candidates = sorted(folder.glob("*.dat"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def unique_dat_path(folder: Path, stem: str) -> Path:
    """Return a non-colliding .dat path for ``folder/{stem}.dat``.

    If the bare name exists, appends a UTC timestamp; if that also
    collides, appends an incrementing index.
    """
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{stem}.dat"
    if not candidate.exists():
        return candidate
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = folder / f"{stem}_{stamp}.dat"
    if not candidate.exists():
        return candidate
    idx = 2
    while True:
        candidate = folder / f"{stem}_{stamp}_{idx}.dat"
        if not candidate.exists():
            return candidate
        idx += 1
