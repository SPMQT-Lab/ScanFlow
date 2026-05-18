"""Take screenshots of the lab PC: whole virtual desktop + each monitor.

Runs on the LAB PC. Writes ``1 + N`` PNG files per invocation, where N is
the number of physical monitors detected, named with a UTC timestamp so
the dev PC's relay script can pick them up in order:

    20260518T143012Z_desktop.png    ← virtual desktop, both monitors combined
    20260518T143012Z_monitor1.png   ← primary monitor only
    20260518T143012Z_monitor2.png   ← secondary monitor only
    ...

A skipped capture (rare — only on permission errors) is logged as a
warning; the script never raises.

Dependencies (install on the LAB PC):
    pip install pillow pywin32
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

log = logging.getLogger("scanflow.monitor")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def grab_desktop():
    """Capture all monitors as one image (the Windows 'virtual screen')."""
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True)


def enumerate_monitors():
    """Return a list of (index, (left, top, right, bottom)) for each physical
    monitor, in the order Windows reports them (primary first).

    Coordinates are in the virtual-screen space, so any monitor positioned
    to the left of the primary will have negative ``left`` — that's fine,
    PIL's ImageGrab(bbox=…, all_screens=True) accepts negative coords.
    """
    import win32api
    rects = []
    for idx, (hmon, _hdc, rect) in enumerate(win32api.EnumDisplayMonitors(), start=1):
        rects.append((idx, tuple(rect)))
    return rects


def save(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), format="PNG", optimize=True)
    log.info("wrote %s (%d KB)", path.name, path.stat().st_size // 1024)


def take_set(output_dir: Path) -> Tuple[int, int]:
    """Take one trio (or more) of captures. Returns (n_taken, n_skipped)."""
    stamp = _utc_stamp()
    taken, skipped = 0, 0

    # 1. Whole virtual desktop (both monitors stitched together) ----------
    try:
        save(grab_desktop(), output_dir / f"{stamp}_desktop.png")
        taken += 1
    except Exception as e:
        log.warning("desktop grab failed: %s", e)
        skipped += 1

    # 2. Each physical monitor separately ---------------------------------
    try:
        from PIL import ImageGrab
        monitors = enumerate_monitors()
    except Exception as e:
        log.warning("monitor enumeration failed: %s", e)
        return taken, skipped + 1

    for idx, bbox in monitors:
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            save(img, output_dir / f"{stamp}_monitor{idx}.png")
            taken += 1
        except Exception as e:
            log.warning("monitor %d grab failed: %s", idx, e)
            skipped += 1

    return taken, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Capture full-desktop + per-monitor PNGs of the lab PC."
    )
    ap.add_argument("--output", type=Path,
                    default=Path(r"C:\ScanflowMonitor\screenshots"),
                    help="Output folder for PNGs (default: %(default)s)")
    ap.add_argument("--once", action="store_true",
                    help="Take one set and exit (for Task Scheduler)")
    ap.add_argument("--interval", type=float, default=600.0,
                    help="Seconds between captures when looping (default: 600 = 10 min)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.once:
        taken, skipped = take_set(args.output)
        log.info("done: %d captured, %d skipped", taken, skipped)
        return 0 if taken else 1

    log.info("polling every %.0fs into %s", args.interval, args.output)
    log.info("Ctrl-C to stop.")
    try:
        while True:
            taken, skipped = take_set(args.output)
            log.info("cycle: %d captured, %d skipped — sleeping %.0fs",
                     taken, skipped, args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
