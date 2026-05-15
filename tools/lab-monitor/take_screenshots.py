"""Take screenshots of ScanFlow, CreaTec STMAFM, and the whole desktop.

Runs on the LAB PC. Writes three PNG files per invocation into the
configured output folder, named with a UTC timestamp so the dev PC's
relay script can pick them up in order:

    20260515T143012Z_scanflow.png
    20260515T143012Z_createc.png
    20260515T143012Z_desktop.png

A window is grabbed by partial title match (case-insensitive). If the
target window isn't found or is minimised, that capture is skipped and
a warning is logged — the script never raises.

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
from typing import Optional, Tuple

log = logging.getLogger("scanflow.monitor")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def find_window(title_substring: str) -> Optional[int]:
    """Return the HWND of the first top-level window whose title contains
    ``title_substring`` (case-insensitive). None if no match."""
    import win32gui

    needle = title_substring.lower()
    matches: list[int] = []

    def _cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if needle in title.lower():
            matches.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    return matches[0] if matches else None


def grab_window(hwnd: int) -> "Optional[bytes]":
    """Capture the on-screen pixels of the given window into a PIL image.

    Returns None if the window is minimised / off-screen. Uses ImageGrab.grab
    on the window's screen rectangle — that's the simplest reliable path
    that also captures DirectX / GPU-composited frames (a PrintWindow
    fallback often returns black for hardware-accelerated UIs)."""
    import win32gui
    from PIL import ImageGrab

    if win32gui.IsIconic(hwnd):  # minimised
        return None
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    if right - left < 4 or bottom - top < 4:
        return None
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


def grab_desktop():
    """Capture all monitors as a single image."""
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True)


def save(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), format="PNG", optimize=True)
    log.info("wrote %s (%d KB)", path.name, path.stat().st_size // 1024)


def take_set(output_dir: Path, scanflow_title: str, createc_title: str) -> Tuple[int, int]:
    """Take all three captures for one cycle.

    Returns ``(n_taken, n_skipped)`` for log reporting.
    """
    stamp = _utc_stamp()
    taken, skipped = 0, 0

    # 1. Whole desktop — always succeeds
    try:
        save(grab_desktop(), output_dir / f"{stamp}_desktop.png")
        taken += 1
    except Exception as e:
        log.warning("desktop grab failed: %s", e)
        skipped += 1

    # 2. ScanFlow window
    hwnd = find_window(scanflow_title)
    if hwnd is None:
        log.warning("no window matching %r — ScanFlow open?", scanflow_title)
        skipped += 1
    else:
        try:
            img = grab_window(hwnd)
            if img is not None:
                save(img, output_dir / f"{stamp}_scanflow.png")
                taken += 1
            else:
                log.warning("ScanFlow window minimised or off-screen")
                skipped += 1
        except Exception as e:
            log.warning("scanflow grab failed: %s", e)
            skipped += 1

    # 3. CreaTec STMAFM window
    hwnd = find_window(createc_title)
    if hwnd is None:
        log.warning("no window matching %r — STMAFM open?", createc_title)
        skipped += 1
    else:
        try:
            img = grab_window(hwnd)
            if img is not None:
                save(img, output_dir / f"{stamp}_createc.png")
                taken += 1
            else:
                log.warning("STMAFM window minimised or off-screen")
                skipped += 1
        except Exception as e:
            log.warning("createc grab failed: %s", e)
            skipped += 1

    return taken, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture ScanFlow / STMAFM / desktop screenshots.")
    ap.add_argument("--output", type=Path,
                    default=Path(r"C:\ScanflowMonitor\screenshots"),
                    help="Output folder for PNGs (default: %(default)s)")
    ap.add_argument("--scanflow-title", default="ScanFlow",
                    help="Substring to identify the ScanFlow window")
    ap.add_argument("--createc-title", default="STMAFM",
                    help="Substring to identify the CreaTec window")
    ap.add_argument("--once", action="store_true",
                    help="Take one set and exit (for Task Scheduler)")
    ap.add_argument("--interval", type=float, default=300.0,
                    help="Seconds between captures when looping (default: 300 = 5 min)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.once:
        taken, skipped = take_set(args.output, args.scanflow_title, args.createc_title)
        log.info("done: %d captured, %d skipped", taken, skipped)
        return 0 if taken else 1

    log.info("polling every %.0fs into %s", args.interval, args.output)
    log.info("Ctrl-C to stop.")
    try:
        while True:
            taken, skipped = take_set(args.output, args.scanflow_title, args.createc_title)
            log.info("cycle: %d captured, %d skipped — sleeping %.0fs", taken, skipped, args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
