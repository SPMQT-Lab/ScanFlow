"""Per-scan quality metrics — currently Z stability.

Z stability is computed from the just-acquired topography array: each scan
line is linearly detrended (removes the local slope from topography), then
we report the median per-line RMS residual in picometres. A handful of
extra fields flag suspicious lines:

* ``rms_pm``     : median per-line RMS residual (pm) — the headline number
* ``max_pm``     : worst single line's RMS (pm)
* ``jumps``      : count of lines whose RMS exceeds 3× the median (likely tip jumps)
* ``rating``     : "excellent" | "good" | "noisy" | "unstable" — heuristic label
"""

from __future__ import annotations

from typing import Dict

import numpy as np


def compute_z_stability(topo_nm: np.ndarray) -> Dict[str, float]:
    """Return Z-stability metrics for a 2-D topography array (units: nm).

    A scan with only smooth topographic features will have small per-line
    residuals after slope removal. Tip noise, sample contamination, or
    feedback oscillations all inflate the residual.
    """
    if topo_nm is None or topo_nm.ndim != 2 or topo_nm.size < 4:
        return _empty()

    arr = np.asarray(topo_nm, dtype=float)
    ny, nx = arr.shape
    if nx < 4 or ny < 2:
        return _empty()

    x = np.arange(nx, dtype=float)
    line_rms = []
    for row in arr:
        finite = np.isfinite(row)
        if finite.sum() < 4:
            continue
        # Linear detrend on the finite samples, evaluate on the whole row
        rx = x[finite]
        ry = row[finite]
        m, b = np.polyfit(rx, ry, 1)
        resid = ry - (m * rx + b)
        line_rms.append(float(np.std(resid)))

    if not line_rms:
        return _empty()

    arr_rms = np.asarray(line_rms)
    med = float(np.median(arr_rms))
    mx = float(arr_rms.max())
    jumps = int(np.sum(arr_rms > 3.0 * med)) if med > 0 else 0

    rms_pm = med * 1000.0  # nm → pm
    return {
        "rms_pm": rms_pm,
        "max_pm": mx * 1000.0,
        "jumps": jumps,
        "rating": _rate(rms_pm, jumps),
    }


def format_z_stability(metrics: Dict[str, float]) -> str:
    """Human-readable one-liner for the log panel."""
    if not metrics or "rms_pm" not in metrics:
        return "Z stability: unavailable"
    return (
        f"Z stability: {metrics['rms_pm']:.1f} pm RMS  "
        f"(max {metrics['max_pm']:.1f} pm, "
        f"{int(metrics['jumps'])} line jump(s)) "
        f"[{metrics.get('rating', '?')}]"
    )


def _empty() -> Dict[str, float]:
    return {"rms_pm": 0.0, "max_pm": 0.0, "jumps": 0, "rating": "n/a"}


def _rate(rms_pm: float, jumps: int) -> str:
    """Coarse quality label. Tuned for LT-STM on metals — adjust if needed."""
    if rms_pm <= 5.0 and jumps == 0:
        return "excellent"
    if rms_pm <= 15.0 and jumps <= 1:
        return "good"
    if rms_pm <= 50.0 and jumps <= 5:
        return "noisy"
    return "unstable"
