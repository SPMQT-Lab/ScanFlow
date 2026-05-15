"""Survey campaign data model.

A campaign is: one wide-area scan, automatic bright-feature discovery,
then per-feature zoom scans with iterative drift refinement. The campaign
manifest is a JSON file ProbeFlow can later open for post-processing
and PPTX re-export with polished images.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class SurveyConfig:
    """All knobs for an auto-discover-and-zoom campaign.

    Defaults are tuned for molecule surveys: 120 nm wide field, 3 nm minimum
    zoom (clean view of a 1.2 nm molecule), 20 nm ceiling for clusters.
    """
    # Wide scan
    wide_size_nm: Tuple[float, float] = (120.0, 120.0)
    wide_pixels: Tuple[int, int] = (512, 512)
    wide_speed_nm_s: float = 100.0

    # Per-feature zoom
    zoom_pixels: Tuple[int, int] = (256, 256)
    zoom_speed_nm_s: float = 20.0
    zoom_iterations: int = 3
    size_multiplier: float = 2.0
    min_zoom_nm: float = 3.0
    max_zoom_nm: float = 30.0

    # Discovery filters
    min_feature_nm: float = 0.8
    max_feature_nm: float = 20.0
    merge_distance_nm: float = 0.5
    max_features: int = 30
    edge_margin_px: int = 16

    # Shared scan parameters
    bias_V: float = 0.1
    setpoint_A: float = 50e-12

    # Pre-scan settle time — pauses after applying scan params and before
    # acquiring data, letting the piezo / feedback / tip stabilise. Applied
    # before the wide scan and before *each* zoom iteration of every feature.
    settling_s: float = 5.0

    # Output
    output_folder: str = ""
    name: str = "Survey"

    kind: str = "survey"


@dataclass
class FeatureRecord:
    """Outcome of a single feature in a survey — what was scanned and how well it centered."""
    index: int
    centroid_pixels: Tuple[float, float]
    centroid_nm_offset: Tuple[float, float]   # offset from wide-scan centre, nm
    char_dim_nm: float                        # characteristic dimension (max of bbox W/H)
    zoom_size_nm: Tuple[float, float]
    bias_V: float = 0.0
    setpoint_A: float = 0.0
    scan_paths: List[str] = field(default_factory=list)             # one per iteration
    preview_paths: List[str] = field(default_factory=list)          # PNG previews
    drift_log_angstrom: List[Tuple[float, float]] = field(default_factory=list)
    final_residual_angstrom: Tuple[float, float] = (0.0, 0.0)
    z_stability_per_iter: List[dict] = field(default_factory=list)  # one dict per scan


@dataclass
class SurveyManifest:
    """Per-campaign manifest. ProbeFlow opens this to drive its survey browser."""
    name: str = "Survey"
    timestamp: str = ""
    wide_scan_path: str = ""
    wide_preview_path: str = ""
    wide_size_nm: Tuple[float, float] = (120.0, 120.0)
    wide_pixels: Tuple[int, int] = (512, 512)
    features: List[FeatureRecord] = field(default_factory=list)
    manifest_version: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SurveyManifest":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        feats_raw = d.pop("features", [])
        features = []
        for f in feats_raw:
            f["centroid_pixels"] = tuple(f.get("centroid_pixels", (0.0, 0.0)))
            f["centroid_nm_offset"] = tuple(f.get("centroid_nm_offset", (0.0, 0.0)))
            f["zoom_size_nm"] = tuple(f.get("zoom_size_nm", (5.0, 5.0)))
            f["drift_log_angstrom"] = [tuple(p) for p in f.get("drift_log_angstrom", [])]
            f["final_residual_angstrom"] = tuple(f.get("final_residual_angstrom", (0.0, 0.0)))
            f.setdefault("z_stability_per_iter", [])
            features.append(FeatureRecord(**f))
        d["wide_size_nm"] = tuple(d.get("wide_size_nm", (120.0, 120.0)))
        d["wide_pixels"] = tuple(d.get("wide_pixels", (512, 512)))
        return cls(features=features, **d)
