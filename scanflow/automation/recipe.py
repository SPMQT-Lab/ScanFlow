"""Measurement recipe: a sequence of typed steps with configurable parameters.

A recipe describes everything ScanFlow needs to run an unattended session —
scan parameters, spectroscopy positions, approach commands, wait blocks —
in any order. Recipes serialise to/from YAML so they can be saved,
shared, and reloaded.

Step types
----------
ScanStep        run a single image
SpectroscopyStep  run one or more I/V spectra (single, multi-point, line, grid)
ApproachStep    re-approach the tip
WaitStep        sleep for N seconds (useful for thermal settling)

Recipes built only from ScanSteps remain backwards-compatible with the
v1 YAML format that earlier sessions produced.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Union

from scanflow.automation.survey import SurveyConfig


DEFAULT_CHANNELS = ("TOPOGRAPHY", "CURRENT")

# Minimum |bias| (V) allowed in constant-current mode. The feedback loop drives
# the tip into the surface near 0 V because the tunnelling current cannot reach
# the setpoint — leaving such a step in a ramp would crash the tip.
MIN_CONST_CURRENT_BIAS_V = 5e-3  # 5 mV


def _tuples_to_lists(obj):
    """Recursively convert tuples to lists so PyYAML's safe_dump can handle them."""
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_tuples_to_lists(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Step types
# ---------------------------------------------------------------------------

@dataclass
class ScanStep:
    bias_V: float
    setpoint_A: float
    size_nm: tuple[float, float] = (50.0, 50.0)
    speed_nm_s: float = 50.0
    pixels: tuple[int, int] = (256, 256)
    rotation_deg: float = 0.0
    const_height: bool = False
    channels: tuple[str, ...] = DEFAULT_CHANNELS
    preamp_exponent: int = 9
    settling_s: float = 0.0
    label: str = ""
    memo: str = ""
    kind: str = "scan"

    def estimate_duration_s(self) -> float:
        """Estimate wall-clock duration for this scan in seconds.

        Time per line = 2 × size_x / speed (forward + backward trace).
        Total = lines × line_time + settling + a few seconds of overhead
        (save, repositioning).
        """
        line_time_s = 2.0 * self.size_nm[0] / max(self.speed_nm_s, 0.01)
        n_lines = self.pixels[1]
        overhead_s = 4.0
        return line_time_s * n_lines + overhead_s + self.settling_s


@dataclass
class SpectroscopyStep:
    """Run spectroscopy at one or more pixel positions in the current frame."""
    positions: list[tuple[int, int]] = field(default_factory=lambda: [(128, 128)])
    bias_start_V: float = -0.7
    bias_end_V: float = 0.7
    points: int = 1024
    duration_s: float = 10.0
    repeat_count: int = 1
    average_count: int = 1
    backward_sweep: bool = True
    channels: tuple[str, ...] = ("Current(filtered)", "Lock-in X", "Lock-in Y")
    lat_speed_nm_s: float = 1.0
    preamp_exponent: int = 9
    settling_s: float = 0.0
    label: str = ""
    kind: str = "spectroscopy"


@dataclass
class ApproachStep:
    """Re-approach the tip mid-recipe (e.g. after a slider move)."""
    bias_V: float = 2.0
    setpoint_A: float = 1e-9
    burst_count: int = 1
    retry_count: int = 1
    period_s: float = 1.5
    timeout_s: float = 600.0
    label: str = ""
    kind: str = "approach"


@dataclass
class WaitStep:
    """Pause for thermal settling, tip stabilisation, etc."""
    seconds: float = 60.0
    label: str = ""
    kind: str = "wait"


@dataclass
class SurveyStep:
    """Wide scan + auto feature discovery + per-feature zoom campaign."""
    config: "SurveyConfig" = field(default_factory=lambda: SurveyConfig())
    label: str = ""
    kind: str = "survey"

    def estimate_duration_s(self) -> float:
        cfg = self.config
        wide_t = (2.0 * cfg.wide_size_nm[0] / max(cfg.wide_speed_nm_s, 0.01)
                  * cfg.wide_pixels[1] + 4.0)
        zoom_t = (2.0 * cfg.min_zoom_nm / max(cfg.zoom_speed_nm_s, 0.01)
                  * cfg.zoom_pixels[1] + 4.0)
        n_zooms = cfg.max_features * cfg.zoom_iterations
        # One pre-wide settle + one settle per zoom iteration
        settle_t = cfg.settling_s * (1 + n_zooms)
        return wide_t + n_zooms * zoom_t + settle_t


RecipeStep = Union[ScanStep, SpectroscopyStep, ApproachStep, WaitStep, SurveyStep]

_STEP_CLASSES = {
    "scan": ScanStep,
    "spectroscopy": SpectroscopyStep,
    "approach": ApproachStep,
    "wait": WaitStep,
    "survey": SurveyStep,
}


def _step_from_dict(d: dict) -> RecipeStep:
    kind = d.get("kind", "scan")
    cls = _STEP_CLASSES.get(kind, ScanStep)
    if cls is ScanStep:
        d["size_nm"] = tuple(float(v) for v in d.get("size_nm", (50.0, 50.0)))
        d["pixels"] = tuple(int(v) for v in d.get("pixels", (256, 256)))
        d["channels"] = tuple(d.get("channels", DEFAULT_CHANNELS))
        for k in ("bias_V", "setpoint_A", "speed_nm_s", "rotation_deg", "settling_s"):
            if k in d:
                d[k] = float(d[k])
    elif cls is SpectroscopyStep:
        d["positions"] = [tuple(int(v) for v in p) for p in d.get("positions", [(128, 128)])]
        d["channels"] = tuple(d.get("channels",
                                    ("Current(filtered)", "Lock-in X", "Lock-in Y")))
        for k in ("bias_start_V", "bias_end_V", "duration_s", "lat_speed_nm_s", "settling_s"):
            if k in d:
                d[k] = float(d[k])
    elif cls is ApproachStep:
        for k in ("bias_V", "setpoint_A", "period_s", "timeout_s"):
            if k in d:
                d[k] = float(d[k])
    elif cls is WaitStep:
        if "seconds" in d:
            d["seconds"] = float(d["seconds"])
    return cls(**d)


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------

@dataclass
class MeasurementRecipe:
    """Ordered list of steps with shared automation settings."""

    name: str = "Untitled recipe"
    steps: list = field(default_factory=list)

    # Drift correction
    drift_correction: bool = True
    drift_channel: int = 0
    drift_reposition_delay_s: float = 3.0
    drift_template: str = ""
    # "phase" | "features" | "hybrid" — see scanflow.drift.detector.DriftDetector.
    drift_method: str = "hybrid"
    # When True, the alignment scan runs at half the data-scan pixel count;
    # the reference is downsampled on the fly to match. Roughly halves the
    # alignment scan's wall-clock time, ~33% faster sweep overall.
    fast_alignment: bool = False

    # Execution
    repetitions: int = 1
    inter_step_delay_s: float = 0.0
    save_folder: str = ""

    # Safety / overnight
    suppress_dst_change: bool = True
    stop_on_error: bool = True

    # Tip-crash safety
    safety_max_current_A: float = 1e-9        # 1 nA — tip-crash indicator
    safety_enable: bool = True
    safety_retract_nm: float = 10.0
    safety_poll_interval_s: float = 0.5

    # ------------------------------------------------------------------

    def add_step(self, step: RecipeStep) -> None:
        self.steps.append(step)

    def total_steps(self) -> int:
        return len(self.steps) * self.repetitions

    def estimate_duration_s(self) -> float:
        """Sum estimated durations of every step across all repetitions.

        Includes an extra alignment-scan worth of time per step when
        drift correction is enabled (the runner takes a quick alignment
        image before each data scan).
        """
        per_iter = 0.0
        for step in self.steps:
            if hasattr(step, "estimate_duration_s"):
                t = step.estimate_duration_s()
            elif getattr(step, "kind", "") == "wait":
                t = float(getattr(step, "seconds", 0.0))
            elif getattr(step, "kind", "") == "approach":
                t = float(getattr(step, "timeout_s", 30.0)) * 0.1  # typical
            else:
                t = 0.0
            per_iter += t
            if self.drift_correction and getattr(step, "kind", "scan") == "scan":
                # Alignment scan budget: ~50% of data scan time at full
                # resolution, ~25% (half pixels → half lines) with fast mode.
                per_iter += t * (0.25 if self.fast_alignment else 0.5)
            per_iter += self.inter_step_delay_s
        return per_iter * self.repetitions

    def to_yaml(self) -> str:
        data = asdict(self)
        # Ensure each step has a `kind` discriminator
        for i, s in enumerate(self.steps):
            data["steps"][i]["kind"] = getattr(s, "kind", "scan")
        return yaml.dump(_tuples_to_lists(data),
                         default_flow_style=False, sort_keys=False)

    def save(self, path: Path) -> None:
        path.write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def from_yaml(cls, text: str) -> "MeasurementRecipe":
        data = yaml.safe_load(text)
        steps_raw = data.pop("steps", [])
        steps = [_step_from_dict(dict(s)) for s in steps_raw]
        for k in ("drift_reposition_delay_s", "inter_step_delay_s",
                  "safety_max_current_A", "safety_retract_nm", "safety_poll_interval_s"):
            if k in data:
                data[k] = float(data[k])
        return cls(steps=steps, **data)

    @classmethod
    def load(cls, path: Path) -> "MeasurementRecipe":
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Convenience builders (back-compat — produce all-ScanStep recipes)
    # ------------------------------------------------------------------

    @classmethod
    def bias_ramp(
        cls,
        start_V: float,
        end_V: float,
        steps: int,
        setpoint_A: float,
        size_nm: tuple[float, float] = (50.0, 50.0),
        speed_nm_s: float = 50.0,
        pixels: tuple[int, int] = (256, 256),
        drift_correction: bool = True,
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
        const_height: bool = False,
        settling_s: float = 0.0,
        fast_alignment: bool = False,
    ) -> "MeasurementRecipe":
        import numpy as np
        recipe = cls(name=f"Bias ramp {start_V:.2f}–{end_V:.2f} V",
                     drift_correction=drift_correction,
                     fast_alignment=fast_alignment)
        for bias in np.linspace(start_V, end_V, steps):
            # Constant-current scans at 0 V can never reach the setpoint —
            # the feedback loop pushes the tip into the surface. Skip
            # silently so the rest of the ramp still runs.
            if not const_height and abs(float(bias)) < MIN_CONST_CURRENT_BIAS_V:
                continue
            recipe.add_step(ScanStep(
                bias_V=float(bias),
                setpoint_A=setpoint_A,
                size_nm=size_nm,
                speed_nm_s=speed_nm_s,
                pixels=pixels,
                channels=channels,
                const_height=const_height,
                settling_s=settling_s,
                label=f"{bias*1000:.1f} mV",
            ))
        return recipe

    @classmethod
    def overnight(
        cls,
        bias_V: float,
        setpoint_A: float,
        repetitions: int = 100,
        size_nm: tuple[float, float] = (50.0, 50.0),
        speed_nm_s: float = 50.0,
        pixels: tuple[int, int] = (256, 256),
        drift_correction: bool = True,
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
    ) -> "MeasurementRecipe":
        recipe = cls(name="Overnight scan",
                     drift_correction=drift_correction,
                     repetitions=repetitions,
                     suppress_dst_change=True)
        recipe.add_step(ScanStep(
            bias_V=bias_V,
            setpoint_A=setpoint_A,
            size_nm=size_nm,
            speed_nm_s=speed_nm_s,
            pixels=pixels,
            channels=channels,
        ))
        return recipe

    @classmethod
    def current_ramp(
        cls,
        start_pA: float,
        end_pA: float,
        steps: int,
        bias_V: float,
        size_nm: tuple[float, float] = (50.0, 50.0),
        speed_nm_s: float = 50.0,
        pixels: tuple[int, int] = (256, 256),
        drift_correction: bool = True,
        settling_s: float = 0.0,
        fast_alignment: bool = False,
    ) -> "MeasurementRecipe":
        import numpy as np
        recipe = cls(name=f"Current ramp {start_pA:.1f}–{end_pA:.1f} pA",
                     drift_correction=drift_correction,
                     fast_alignment=fast_alignment)
        for c_pA in np.linspace(start_pA, end_pA, steps):
            recipe.add_step(ScanStep(
                bias_V=bias_V,
                setpoint_A=float(c_pA) * 1e-12,
                size_nm=size_nm,
                speed_nm_s=speed_nm_s,
                pixels=pixels,
                settling_s=settling_s,
                label=f"{c_pA:.1f} pA",
            ))
        return recipe

    @classmethod
    def scan_then_spec(
        cls,
        scan_bias_V: float,
        scan_setpoint_A: float,
        spec_positions: list[tuple[int, int]],
        spec_bias_range_V: tuple[float, float] = (-0.7, 0.7),
        spec_points: int = 1024,
        spec_duration_s: float = 10.0,
        size_nm: tuple[float, float] = (50.0, 50.0),
        pixels: tuple[int, int] = (256, 256),
    ) -> "MeasurementRecipe":
        """Build a mixed recipe: overview scan → multi-point dI/dV → overview scan."""
        recipe = cls(name="Scan + multi-point dI/dV + scan")
        recipe.add_step(ScanStep(
            bias_V=scan_bias_V, setpoint_A=scan_setpoint_A,
            size_nm=size_nm, pixels=pixels, label="Overview before",
        ))
        recipe.add_step(SpectroscopyStep(
            positions=spec_positions,
            bias_start_V=spec_bias_range_V[0],
            bias_end_V=spec_bias_range_V[1],
            points=spec_points,
            duration_s=spec_duration_s,
            label=f"dI/dV ({len(spec_positions)} points)",
        ))
        recipe.add_step(ScanStep(
            bias_V=scan_bias_V, setpoint_A=scan_setpoint_A,
            size_nm=size_nm, pixels=pixels, label="Overview after",
        ))
        return recipe


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as e.g. '2 h 14 min' or '45 s'."""
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m} min {s} s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h} h {m} min"
