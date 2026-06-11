"""Action planners: AnalysisResult in, ProposedAction suggestions out.

Planners are analysis-side (long_term_architecture §13.2): they may
PROPOSE follow-up work, but every proposal goes through the control
core's validation (scanflow.automation.proposals) and — by default —
operator approval before anything executes. Planners never import
control or hardware code.
"""

from __future__ import annotations

from typing import Optional

from scanflow.contracts import AnalysisResult, ProposedAction


class ZoomFeaturesPlanner:
    """Propose a zoom scan centred on each detected feature.

    Zoom sizing mirrors the survey heuristic: ``size_multiplier`` × the
    feature's bounding-box size, clamped to [min_zoom_nm, max_zoom_nm].
    ``bias_V`` / ``setpoint_A`` must be supplied (typically from the
    source ScanRecord's scan_parameters) — the control validator refuses
    unresolved 'inherit current' proposals by design.
    """

    name = "zoom_features"
    version = "1.0"

    def __init__(
        self,
        *,
        bias_V: float,
        setpoint_A: float,
        pixels: tuple[int, int] = (256, 256),
        speed_nm_s: float = 20.0,
        size_multiplier: float = 2.0,
        min_zoom_nm: float = 3.0,
        max_zoom_nm: float = 30.0,
        max_proposals: int = 10,
        min_confidence: Optional[float] = None,
        requires_operator_confirmation: bool = True,
    ) -> None:
        self.bias_V = float(bias_V)
        self.setpoint_A = float(setpoint_A)
        self.pixels = (int(pixels[0]), int(pixels[1]))
        self.speed_nm_s = float(speed_nm_s)
        self.size_multiplier = float(size_multiplier)
        self.min_zoom_nm = float(min_zoom_nm)
        self.max_zoom_nm = float(max_zoom_nm)
        self.max_proposals = int(max_proposals)
        self.min_confidence = min_confidence
        self.requires_operator_confirmation = bool(requires_operator_confirmation)

    def propose(self, result: AnalysisResult) -> list[ProposedAction]:
        actions: list[ProposedAction] = []
        for feature in result.features[: self.max_proposals]:
            if (self.min_confidence is not None
                    and feature.confidence is not None
                    and feature.confidence < self.min_confidence):
                continue
            if feature.bbox_nm is not None:
                w = feature.bbox_nm[2] - feature.bbox_nm[0]
                h = feature.bbox_nm[3] - feature.bbox_nm[1]
                char_dim = max(abs(w), abs(h))
            else:
                char_dim = self.min_zoom_nm
            zoom = min(self.max_zoom_nm,
                       max(self.min_zoom_nm, char_dim * self.size_multiplier))
            label = f" ({feature.label})" if feature.label else ""
            actions.append(ProposedAction(
                action_id=f"{self.name}:{result.analysis_id[:8]}:{feature.feature_id}",
                source_analysis_id=result.analysis_id,
                kind="scan_region",
                reason=f"zoom feature {feature.feature_id}{label} "
                       f"from {result.algorithm} v{result.algorithm_version}",
                confidence=feature.confidence,
                target_nm=(feature.x_nm, feature.y_nm),
                frame=feature.frame,
                size_nm=(zoom, zoom),
                bias_V=self.bias_V,
                setpoint_A=self.setpoint_A,
                pixels=self.pixels,
                speed_nm_s=self.speed_nm_s,
                requires_operator_confirmation=self.requires_operator_confirmation,
                source=f"{self.name} v{self.version}",
            ))
        return actions
