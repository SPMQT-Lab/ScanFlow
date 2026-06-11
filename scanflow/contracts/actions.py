"""Action contracts: the only path from analysis/ML to the instrument.

Authority model (docs/long_term_architecture.md §6–8):

    analysis/ML emit ProposedAction      (a suggestion — NOT executable)
        ↓
    control core emits ValidationResult  (safety/range/coordinate checks)
        ↓
    operator or policy approves
        ↓
    control core builds ValidatedAction  (recipe steps — executable)

Analysis and ML code constructs :class:`ProposedAction` and nothing else.
Validation lives in the control layer
(:mod:`scanflow.automation.proposals`), and only a
:class:`ValidatedAction` may reach an executor. Nothing in this module
performs validation or execution — these are inert data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .coordinates import require_known_frame

#: Action kinds the control layer understands. Proposals with other kinds
#: are rejected at validation, not silently ignored.
ACTION_KINDS = frozenset({
    "scan_region",            # move to target_nm and image size_nm
    "run_spectroscopy",       # dI/dV at spectroscopy_points_nm
    "rescan_current_region",  # repeat the current frame
    "track_feature",          # hand target to the atom tracker
    "ignore",                 # explicit "do nothing" (useful for ranking UIs)
})


@dataclass
class ProposedAction:
    """A suggestion from analysis/ML. Never executable as-is."""

    action_id: str
    source_analysis_id: str
    kind: str                       # one of ACTION_KINDS
    reason: str = ""
    confidence: Optional[float] = None

    target_nm: Optional[tuple[float, float]] = None
    #: Coordinate frame of target_nm / spectroscopy_points_nm.
    #: Mandatory whenever a position is given.
    frame: Optional[str] = None
    size_nm: Optional[tuple[float, float]] = None

    bias_V: Optional[float] = None
    setpoint_A: Optional[float] = None
    pixels: Optional[tuple[int, int]] = None
    speed_nm_s: Optional[float] = None

    spectroscopy_points_nm: Optional[list[tuple[float, float]]] = None

    requires_operator_confirmation: bool = True
    source: str = ""                # who proposed it (panel, planner, model)

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(
                f"ProposedAction {self.action_id!r}: unknown kind {self.kind!r}; "
                f"expected one of {sorted(ACTION_KINDS)}"
            )
        has_position = (self.target_nm is not None
                        or self.spectroscopy_points_nm)
        if has_position:
            if self.frame is None:
                raise ValueError(
                    f"ProposedAction {self.action_id!r}: a position is given "
                    "but 'frame' is None — every position must name its "
                    "coordinate frame"
                )
            require_known_frame(self.frame,
                                context=f"ProposedAction {self.action_id!r}")

    # ------------------------------------------------------------------
    # JSON payloads — cross-process hand-off format (an external planner
    # may write proposals to disk for the operator to review in ScanFlow).
    # ------------------------------------------------------------------

    def to_payload(self) -> dict:
        def _pair(v):
            return list(v) if v is not None else None

        return {
            "action_id": self.action_id,
            "source_analysis_id": self.source_analysis_id,
            "kind": self.kind,
            "reason": self.reason,
            "confidence": self.confidence,
            "target_nm": _pair(self.target_nm),
            "frame": self.frame,
            "size_nm": _pair(self.size_nm),
            "bias_V": self.bias_V,
            "setpoint_A": self.setpoint_A,
            "pixels": _pair(self.pixels),
            "speed_nm_s": self.speed_nm_s,
            "spectroscopy_points_nm": (
                [list(p) for p in self.spectroscopy_points_nm]
                if self.spectroscopy_points_nm is not None else None
            ),
            "requires_operator_confirmation": self.requires_operator_confirmation,
            "source": self.source,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "ProposedAction":
        def _tuple(key):
            v = payload.get(key)
            return tuple(v) if v is not None else None

        spec_points = payload.get("spectroscopy_points_nm")
        return cls(
            action_id=payload.get("action_id", ""),
            source_analysis_id=payload.get("source_analysis_id", ""),
            kind=payload["kind"],
            reason=payload.get("reason", ""),
            confidence=payload.get("confidence"),
            target_nm=_tuple("target_nm"),
            frame=payload.get("frame"),
            size_nm=_tuple("size_nm"),
            bias_V=payload.get("bias_V"),
            setpoint_A=payload.get("setpoint_A"),
            pixels=_tuple("pixels"),
            speed_nm_s=payload.get("speed_nm_s"),
            spectroscopy_points_nm=(
                [tuple(p) for p in spec_points]
                if spec_points is not None else None
            ),
            requires_operator_confirmation=bool(
                payload.get("requires_operator_confirmation", True)
            ),
            source=payload.get("source", ""),
        )


@dataclass
class ValidationResult:
    """Control-core verdict on one ProposedAction."""

    ok: bool
    proposed_action_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Confirmations still outstanding, e.g. ["operator"]. An action with
    #: pending confirmations is not executable even when ok is True.
    required_confirmations: list[str] = field(default_factory=list)


@dataclass
class ValidatedAction:
    """An approved action plus the recipe steps that implement it.

    Only the control layer constructs these (see
    scanflow.automation.proposals); ``recipe_steps`` holds recipe step
    objects (e.g. ScanStep) typed as ``object`` so contracts stays free of
    control-layer imports.
    """

    action_id: str
    proposed_action_id: str
    recipe_steps: list[object] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
