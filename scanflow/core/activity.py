"""Global automation-activity flag (Qt-free, thread-safe).

STMAFM is a single-threaded COM server: every getp/getdacvalfb from any
ScanFlow thread is serviced by the Createc GUI thread, serialised with
its own display updates. While an AutomationRunner is active the runner
already produces everything the GUI needs (progress, per-scan Z
stability, safety readings), so background pollers consult this flag and
skip or thin their COM traffic to keep the Createc window responsive
(REVIEW.md finding H7).

Reference-counted so overlapping runners (e.g. sweep + a future
concurrent worker) cannot clear each other's flag.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_count = 0


def begin() -> None:
    """Mark an automation run as active. Pair with :func:`end`."""
    global _count
    with _lock:
        _count += 1


def end() -> None:
    """Mark an automation run as finished."""
    global _count
    with _lock:
        _count = max(0, _count - 1)


def is_active() -> bool:
    """True while at least one automation run is in progress."""
    with _lock:
        return _count > 0
