"""Measurement recipe: a sequence of scan steps with configurable parameters.

A recipe describes everything ScanFlow needs to run an unattended session —
what bias/current to use per scan, image size/speed in physical units,
which channels to record, and how many repetitions. Recipes serialise
to/from YAML so they can be saved, shared, and reloaded.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


DEFAULT_CHANNELS = ("TOPOGRAPHY", "CURRENT")


def _tuples_to_lists(obj):
    """Recursively convert tuples to lists so PyYAML's safe_dump can handle them."""
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_tuples_to_lists(v) for v in obj]
    return obj


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
    label: str = ""
    memo: str = ""


@dataclass
class MeasurementRecipe:
    """Ordered list of scan steps with shared automation settings."""

    name: str = "Untitled recipe"
    steps: list[ScanStep] = field(default_factory=list)

    # Drift correction
    drift_correction: bool = True
    drift_channel: int = 0
    drift_reposition_delay_s: float = 3.0
    drift_template: str = ""           # path to a .dat used as alignment reference

    # Execution
    repetitions: int = 1
    inter_step_delay_s: float = 0.0
    save_folder: str = ""              # if empty, STMAFM default folder is used

    # Safety / overnight
    suppress_dst_change: bool = True   # avoid filename glitches across DST
    stop_on_error: bool = True

    # ------------------------------------------------------------------

    def add_step(self, step: ScanStep) -> None:
        self.steps.append(step)

    def total_steps(self) -> int:
        return len(self.steps) * self.repetitions

    def to_yaml(self) -> str:
        return yaml.dump(_tuples_to_lists(asdict(self)),
                         default_flow_style=False, sort_keys=False)

    def save(self, path: Path) -> None:
        path.write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def from_yaml(cls, text: str) -> "MeasurementRecipe":
        data = yaml.safe_load(text)
        steps_raw = data.pop("steps", [])
        steps = []
        for s in steps_raw:
            # Coerce list -> tuple for the size/pixels/channels fields
            s["size_nm"] = tuple(s.get("size_nm", (50.0, 50.0)))
            s["pixels"] = tuple(s.get("pixels", (256, 256)))
            s["channels"] = tuple(s.get("channels", DEFAULT_CHANNELS))
            steps.append(ScanStep(**s))
        return cls(steps=steps, **data)

    @classmethod
    def load(cls, path: Path) -> "MeasurementRecipe":
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Convenience builders
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
    ) -> "MeasurementRecipe":
        import numpy as np
        recipe = cls(name=f"Bias ramp {start_V:.2f}–{end_V:.2f} V",
                     drift_correction=drift_correction)
        for bias in np.linspace(start_V, end_V, steps):
            recipe.add_step(ScanStep(
                bias_V=float(bias),
                setpoint_A=setpoint_A,
                size_nm=size_nm,
                speed_nm_s=speed_nm_s,
                pixels=pixels,
                channels=channels,
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
    ) -> "MeasurementRecipe":
        import numpy as np
        recipe = cls(name=f"Current ramp {start_pA:.1f}–{end_pA:.1f} pA",
                     drift_correction=drift_correction)
        for c_pA in np.linspace(start_pA, end_pA, steps):
            recipe.add_step(ScanStep(
                bias_V=bias_V,
                setpoint_A=float(c_pA) * 1e-12,
                size_nm=size_nm,
                speed_nm_s=speed_nm_s,
                pixels=pixels,
                label=f"{c_pA:.1f} pA",
            ))
        return recipe
