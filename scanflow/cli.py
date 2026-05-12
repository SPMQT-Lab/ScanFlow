"""ScanFlow command-line interface.

Run a bias or current ramp overnight from a single command — no GUI required.

Examples
--------
    python -m scanflow bias --start -1.0 --end 1.0 --step 0.01 --setpoint 50
    python -m scanflow current --start 10 --end 100 --step 5 --bias 0.1
    python -m scanflow run overnight.yaml
    python -m scanflow estimate bias --start -1.0 --end 1.0 --step 0.01

All commands print an estimated total run time before doing anything and
require an explicit y/N confirmation (use --yes to skip).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from scanflow.automation import MeasurementRecipe, AutomationRunner
from scanflow.automation.recipe import format_duration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ramp_step_count(start: float, end: float, step: float) -> int:
    """How many scan points span [start, end] at the given step size."""
    if step <= 0:
        raise ValueError("--step must be positive")
    return int(math.floor(abs(end - start) / step + 1e-9)) + 1


def _print_plan(recipe: MeasurementRecipe, what: str) -> None:
    """Print the run plan and estimated duration."""
    n = recipe.total_steps()
    total_s = recipe.estimate_duration_s()
    print()
    print(f"=== ScanFlow plan: {recipe.name} ===")
    print(f"  Mode      : {what}")
    print(f"  Scans     : {n}")
    if recipe.steps:
        s0 = recipe.steps[0]
        if hasattr(s0, "size_nm"):
            print(f"  Frame     : {s0.size_nm[0]:.1f} × {s0.size_nm[1]:.1f} nm, "
                  f"{s0.pixels[0]} × {s0.pixels[1]} px @ {s0.speed_nm_s:.1f} nm/s")
        per_s = s0.estimate_duration_s() if hasattr(s0, "estimate_duration_s") else 0.0
        if per_s:
            print(f"  Per scan  : ≈ {format_duration(per_s)}")
    print(f"  Drift     : {'on' if recipe.drift_correction else 'off'}"
          + ("   (adds ~50% alignment time)" if recipe.drift_correction else ""))
    print(f"  Safety    : "
          f"{'on, threshold ' + f'{recipe.safety_max_current_A*1e9:.3f} nA' if recipe.safety_enable else 'off'}")
    if recipe.save_folder:
        print(f"  Output    : {recipe.save_folder}")
    print(f"  Estimated total time: {format_duration(total_s)}")
    print()


def _confirm(skip: bool) -> bool:
    if skip:
        return True
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _connect(mock: bool):
    from scanflow.core import STMClient
    stm = STMClient()
    if mock:
        stm.connect_mock()
        print("Connected to MOCK STM (offline simulation)")
        return stm
    if not stm.connect():
        print("ERROR: Could not connect to STMAFM. Is the manufacturer "
              "software running on this PC?", file=sys.stderr)
        sys.exit(2)
    print("Connected to STMAFM")
    return stm


def _run_blocking(stm, recipe: MeasurementRecipe) -> int:
    """Execute a recipe and stream progress to stdout. Returns the exit code."""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    runner = AutomationRunner(stm, recipe)
    state = {"err": None}

    def on_progress(i, n, label):
        print(f"[{i:>4}/{n}] {label}")

    def on_saved(path):
        print(f"        saved: {path}")

    def on_error(msg):
        state["err"] = msg
        print(f"ERROR: {msg}", file=sys.stderr)

    def on_safety(msg, current_A):
        print(f"⚠ SAFETY ABORT: {msg}  (|I|={current_A*1e9:.3f} nA)",
              file=sys.stderr)

    def on_drift(result):
        try:
            print(f"        drift: dx={result.dx_angstrom:+.3f} Å  "
                  f"dy={result.dy_angstrom:+.3f} Å  |d|={result.magnitude_angstrom:.3f} Å")
        except AttributeError:
            pass

    runner.progress.connect(on_progress)
    runner.scan_completed.connect(on_saved)
    runner.error.connect(on_error)
    runner.safety_violation.connect(on_safety)
    runner.drift_measured.connect(on_drift)
    runner.finished.connect(app.quit)
    runner.start()

    try:
        app.exec()
    except KeyboardInterrupt:
        print("\nInterrupted — stopping runner…", file=sys.stderr)
        runner.stop()
        runner.wait(5000)

    if state["err"]:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Recipe builders driven by argparse
# ---------------------------------------------------------------------------

def _build_bias_recipe(a) -> MeasurementRecipe:
    n = _ramp_step_count(a.start, a.end, a.step)
    recipe = MeasurementRecipe.bias_ramp(
        start_V=a.start,
        end_V=a.end,
        steps=n,
        setpoint_A=a.setpoint * 1e-12,
        size_nm=(a.size, a.size),
        speed_nm_s=a.speed,
        pixels=(a.pixels, a.pixels),
        drift_correction=not a.no_drift,
    )
    recipe.save_folder = a.save_folder or ""
    recipe.safety_max_current_A = a.safety_nA * 1e-9
    recipe.safety_enable = not a.no_safety
    return recipe


def _build_current_recipe(a) -> MeasurementRecipe:
    n = _ramp_step_count(a.start, a.end, a.step)
    recipe = MeasurementRecipe.current_ramp(
        start_pA=a.start,
        end_pA=a.end,
        steps=n,
        bias_V=a.bias,
        size_nm=(a.size, a.size),
        speed_nm_s=a.speed,
        pixels=(a.pixels, a.pixels),
        drift_correction=not a.no_drift,
    )
    recipe.save_folder = a.save_folder or ""
    recipe.safety_max_current_A = a.safety_nA * 1e-9
    recipe.safety_enable = not a.no_safety
    return recipe


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_bias(a) -> int:
    recipe = _build_bias_recipe(a)
    _print_plan(recipe,
                f"Bias ramp {a.start:.3f} → {a.end:.3f} V  step {a.step*1000:.1f} mV  "
                f"@ {a.setpoint:.2f} pA")
    if not _confirm(a.yes):
        print("Aborted.")
        return 1
    stm = _connect(a.mock)
    try:
        return _run_blocking(stm, recipe)
    finally:
        stm.disconnect()


def cmd_current(a) -> int:
    recipe = _build_current_recipe(a)
    _print_plan(recipe,
                f"Current ramp {a.start:.2f} → {a.end:.2f} pA  step {a.step:.2f} pA  "
                f"@ {a.bias*1000:.1f} mV")
    if not _confirm(a.yes):
        print("Aborted.")
        return 1
    stm = _connect(a.mock)
    try:
        return _run_blocking(stm, recipe)
    finally:
        stm.disconnect()


def cmd_run(a) -> int:
    recipe = MeasurementRecipe.load(Path(a.recipe))
    _print_plan(recipe, f"Recipe from {a.recipe}")
    if not _confirm(a.yes):
        print("Aborted.")
        return 1
    stm = _connect(a.mock)
    try:
        return _run_blocking(stm, recipe)
    finally:
        stm.disconnect()


def cmd_estimate(a) -> int:
    """Print the plan without connecting or running."""
    if a.kind == "bias":
        recipe = _build_bias_recipe(a)
        what = f"Bias ramp {a.start:.3f} → {a.end:.3f} V  step {a.step*1000:.1f} mV"
    else:
        recipe = _build_current_recipe(a)
        what = f"Current ramp {a.start:.2f} → {a.end:.2f} pA  step {a.step:.2f} pA"
    _print_plan(recipe, what)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_common_scan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--size", type=float, default=50.0,
                   help="Scan side length in nm (square frame, default 50)")
    p.add_argument("--speed", type=float, default=50.0,
                   help="Scan speed in nm/s (default 50)")
    p.add_argument("--pixels", type=int, default=256,
                   help="Image resolution per side (default 256)")
    p.add_argument("--save-folder", default="",
                   help="Directory for .dat files (default: STMAFM default)")
    p.add_argument("--no-drift", action="store_true",
                   help="Disable drift correction")
    p.add_argument("--safety-nA", type=float, default=1.0,
                   help="Tip-crash current threshold in nA (default 1.0)")
    p.add_argument("--no-safety", action="store_true",
                   help="Disable tip-crash safety abort (NOT recommended)")
    p.add_argument("--mock", action="store_true",
                   help="Use mock STM (offline simulation)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip y/N confirmation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scanflow",
        description="ScanFlow — automated bias/current sweep companion for CreaTec STM.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # bias
    p_bias = sub.add_parser("bias", help="Bias ramp at constant current")
    p_bias.add_argument("--start", type=float, required=True, help="Start bias (V)")
    p_bias.add_argument("--end", type=float, required=True, help="End bias (V)")
    p_bias.add_argument("--step", type=float, required=True, help="Bias step (V, e.g. 0.01 = 10 mV)")
    p_bias.add_argument("--setpoint", type=float, default=50.0,
                        help="Constant tunneling current (pA, default 50)")
    _add_common_scan_args(p_bias)
    p_bias.set_defaults(func=cmd_bias)

    # current
    p_current = sub.add_parser("current", help="Current ramp at constant bias")
    p_current.add_argument("--start", type=float, required=True, help="Start current (pA)")
    p_current.add_argument("--end", type=float, required=True, help="End current (pA)")
    p_current.add_argument("--step", type=float, required=True, help="Current step (pA)")
    p_current.add_argument("--bias", type=float, default=0.1,
                           help="Constant bias voltage (V, default 0.1)")
    _add_common_scan_args(p_current)
    p_current.set_defaults(func=cmd_current)

    # run from YAML
    p_run = sub.add_parser("run", help="Run a YAML recipe")
    p_run.add_argument("recipe", help="Path to a recipe.yaml")
    p_run.add_argument("--mock", action="store_true", help="Use mock STM")
    p_run.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p_run.set_defaults(func=cmd_run)

    # estimate (no execution)
    p_est = sub.add_parser("estimate", help="Estimate run time without scanning")
    p_est_sub = p_est.add_subparsers(dest="kind", required=True)
    for kind in ("bias", "current"):
        p_k = p_est_sub.add_parser(kind)
        p_k.add_argument("--start", type=float, required=True)
        p_k.add_argument("--end", type=float, required=True)
        p_k.add_argument("--step", type=float, required=True)
        if kind == "bias":
            p_k.add_argument("--setpoint", type=float, default=50.0)
        else:
            p_k.add_argument("--bias", type=float, default=0.1)
        _add_common_scan_args(p_k)
        p_k.set_defaults(func=cmd_estimate, kind=kind)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
