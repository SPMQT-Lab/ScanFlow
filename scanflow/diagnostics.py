"""Operator-guided rig diagnostics (Qt-free).

Currently one experiment: **frame-resize semantics**, the open question
tracked as ``FIXME(B2-frame-resize)`` — when ``SCAN.IMAGESIZE.NM.Y``
changes, does STMAFM keep the frame's TOP EDGE or its CENTRE fixed?
Survey and mosaic currently embody contradictory answers; this experiment
settles it. Protocol and decision tree:
``docs/b2_frame_resize_experiment.md``.

Design constraints:

* **No XY motion commands.** The experiment only resizes the frame,
  reads offsets, and (with confirmation) runs ordinary scans. The
  original frame size is restored in a ``finally`` block.
* Operator-in-the-loop: every instrument-affecting step is confirmed
  unless ``interactive=False`` (mock/testing).
* Qt-free: usable from the CLI on a machine without PySide6.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from scanflow.core import STMClient, SafetyMonitor
from scanflow.core.scan import estimate_scan_timeout_s
from scanflow.io.acquisition_log import write_json_atomic

log = logging.getLogger(__name__)

FRAME_RESIZE_REPORT_SCHEMA = "scanflow.diagnostic.frame_resize.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_frame_state(stm: STMClient) -> dict[str, Any]:
    """Read everything relevant to the frame geometry, read-only."""

    def _pair(key_x: str, key_y: str) -> Optional[list[float]]:
        try:
            x = stm.getp(key_x, None)
            y = stm.getp(key_y, None)
            if x in (None, "") or y in (None, ""):
                return None
            return [float(x), float(y)]
        except Exception:
            return None

    try:
        status = int(stm.getp("STMAFM.SCANSTATUS", 0) or 0)
    except Exception:
        status = -1
    return {
        "timestamp": _utc_now(),
        "scanstatus": status,
        "size_nm": _pair("SCAN.IMAGESIZE.NM.X", "SCAN.IMAGESIZE.NM.Y"),
        "pixels": _pair("SCAN.NUM.X", "SCAN.NUM.Y"),
        "offset_nm": _pair("SCAN.OFFSET.X.NM", "SCAN.OFFSET.Y.NM"),
        "offset_volt": _pair("SCAN.OFFSET.X.VOLT", "SCAN.OFFSET.Y.VOLT"),
    }


def run_frame_resize_experiment(
    stm: STMClient,
    *,
    shrink_factor: float = 2.0,
    with_scans: bool = True,
    save_folder: Path | str = "frame_resize_diag",
    interactive: bool = True,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the guided frame-resize experiment; return (and write) the report.

    Sequence: record state → (operator positions frame over a feature) →
    optional BEFORE scan → shrink Y by ``shrink_factor`` → record state →
    optional AFTER scan → operator records where the feature appears →
    restore the original size (always, in ``finally``).
    """
    if shrink_factor <= 1.0:
        raise ValueError("shrink_factor must be > 1")
    folder = Path(save_folder)
    folder.mkdir(parents=True, exist_ok=True)

    def confirm(step: str) -> bool:
        if not interactive:
            return True
        answer = ask(f"{step} — proceed? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    scan = stm.scan
    if scan.is_running:
        raise RuntimeError("a scan is running — stop it before this experiment")

    report: dict[str, Any] = {
        "schema": FRAME_RESIZE_REPORT_SCHEMA,
        "created_at": _utc_now(),
        "mock": stm.is_mock,
        "shrink_factor": float(shrink_factor),
        "scans": {},
        "operator_observation": "not recorded",
        "notes": [],
    }

    out("Frame-resize semantics experiment (FIXME(B2-frame-resize)).")
    out("No XY motion will be commanded; only the frame HEIGHT changes.")
    if interactive:
        out(
            "\nIn STMAFM, position the scan frame over a clearly recognisable"
            "\nfeature, ideally OFF-CENTRE vertically (upper third of the"
            "\nframe works well). Do not start a scan."
        )
        ask("Press Enter when the frame is positioned... ")

    pre = _read_frame_state(stm)
    report["pre_resize"] = pre
    if pre["size_nm"] is None:
        raise RuntimeError("could not read SCAN.IMAGESIZE — aborting")
    original_h = float(pre["size_nm"][1])
    new_h = original_h / float(shrink_factor)
    out(f"\nCurrent frame: {pre['size_nm'][0]:.2f} × {original_h:.2f} nm, "
        f"offset readback {pre['offset_nm']} nm / {pre['offset_volt']} V")

    safety = SafetyMonitor()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _diag_scan(tag: str) -> None:
        if not with_scans:
            return
        if not confirm(f"Run the {tag.upper()} scan now"):
            report["notes"].append(f"{tag} scan skipped by operator")
            return
        status = safety.check(stm)
        if not status.ok:
            report["notes"].append(f"{tag} scan refused: {status.reason}")
            out(f"  {tag} scan refused: {status.reason}")
            return
        target = folder / f"frame_resize_{stamp}_{tag}.dat"
        timeout = estimate_scan_timeout_s(scan.read())
        out(f"  scanning ({tag}) -> {target}")
        saved = scan.scan_and_save(str(target), timeout_s=timeout)
        if saved is None:
            report["notes"].append(f"{tag} scan timed out")
        else:
            report["scans"][tag] = str(saved)

    resized = False
    try:
        _diag_scan("before")

        if not confirm(
            f"Change frame height {original_h:.2f} nm -> {new_h:.2f} nm "
            "(width unchanged, no motion)"
        ):
            report["notes"].append("resize declined by operator — aborted")
            return _finish(report, folder, out)
        stm.setp("SCAN.IMAGESIZE.NM.Y", float(new_h))
        resized = True
        post = _read_frame_state(stm)
        report["post_resize"] = post
        out(f"\nAfter resize: offset readback {post['offset_nm']} nm / "
            f"{post['offset_volt']} V")

        _diag_scan("after")

        if interactive:
            out(
                "\nCompare the AFTER image with the BEFORE image:"
                "\n  [t] the feature kept its distance from the TOP edge"
                "\n      (frame grew/shrank at the bottom -> TOP EDGE preserved)"
                "\n  [c] the feature stayed at the same relative CENTRE position"
                "\n      (frame shrank symmetrically -> CENTRE preserved)"
                "\n  [g] the feature left the frame / moved some other way"
                "\n  [u] unclear, will inspect the saved .dat files later"
            )
            report["operator_observation"] = {
                "t": "top_edge_preserved",
                "c": "centre_preserved",
                "g": "other_or_gone",
                "u": "unclear",
            }.get(ask("Observation [t/c/g/u]: ").strip().lower(), "unclear")

        # Derived numbers for the decision tree (see the experiment doc):
        # if the Y readback is a top edge and the resize preserves the
        # centre, the readback must shift by (H - H/k)/2; if the resize
        # preserves the top edge, the readback must not move at all.
        if pre["offset_nm"] and report.get("post_resize", {}).get("offset_nm"):
            dy = report["post_resize"]["offset_nm"][1] - pre["offset_nm"][1]
            report["derived"] = {
                "offset_y_readback_shift_nm": dy,
                "expected_shift_if_centre_preserved_nm": (original_h - new_h) / 2.0,
                "expected_shift_if_top_edge_preserved_nm": 0.0,
            }
    finally:
        if resized:
            try:
                stm.setp("SCAN.IMAGESIZE.NM.Y", float(original_h))
                report["restored"] = _read_frame_state(stm)
            except Exception as exc:  # pragma: no cover - rig-side failure
                report["notes"].append(
                    f"RESTORE FAILED: {exc} — set frame height back to "
                    f"{original_h:.2f} nm manually!"
                )
                out(f"WARNING: could not restore frame height: {exc}")

    return _finish(report, folder, out)


def _finish(report: dict[str, Any], folder: Path,
            out: Callable[[str], None]) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"frame_resize_report_{stamp}.json"
    write_json_atomic(path, report)
    report["report_path"] = str(path)
    out(f"\nReport written: {path}")
    out("Interpretation guide: docs/b2_frame_resize_experiment.md")
    return report
