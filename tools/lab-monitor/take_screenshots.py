"""Take screenshots of the lab PC: whole virtual desktop only.

Runs on the LAB PC. Writes one PNG per invocation, named with a UTC
timestamp so the dev PC's relay script can pick it up in order:

    20260518T143012Z_desktop.png    ← virtual desktop, all monitors combined

Per-monitor screenshots were removed because Windows' DPI-virtualised
bounding rectangles often crop the secondary monitor incorrectly; the
combined desktop image always shows the full picture.

Dependencies (install on the LAB PC):
    pip install pillow
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


def save(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), format="PNG", optimize=True)
    log.info("wrote %s (%d KB)", path.name, path.stat().st_size // 1024)


def take_set(output_dir: Path) -> Tuple[int, int]:
    """Take one whole-desktop screenshot. Returns (n_taken, n_skipped)."""
    stamp = _utc_stamp()
    try:
        save(grab_desktop(), output_dir / f"{stamp}_desktop.png")
        return 1, 0
    except Exception as e:
        log.warning("desktop grab failed: %s", e)
        return 0, 1


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
