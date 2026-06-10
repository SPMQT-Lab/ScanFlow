"""Control-core validation of proposed actions.

This is the authority boundary from docs/long_term_architecture.md §13:
analysis/ML/GUI code emits :class:`scanflow.contracts.ProposedAction`;
THIS module — control layer code — decides whether it may execute and
what it becomes. Nothing outside the control layer may construct a
:class:`scanflow.contracts.ValidatedAction`.

Static checks only: parameter ranges, the 0 V constant-current guard,
coordinate-frame sanity. Dynamic checks (scan idle, live current, move
distance from the *current* position, readback) remain where they already
live — :class:`scanflow.core.motion.TipMotionManager` at execution time.
Validation here is a gate, not a substitute for the motion policy layer.
"""

from __future__ import annotations

import logging

from scanflow.contracts import (
    CREATEC_SCAN_OFFSET_NM,
    IMAGE_CENTER_RELATIVE_NM,
    ProposedAction,
    ValidatedAction,
    ValidationResult,
)
from scanflow.automation.recipe import MIN_CONST_CURRENT_BIAS_V, ScanStep

log = logging.getLogger(__name__)

#: Kinds this validator can currently turn into recipe steps. Other
#: ACTION_KINDS are recognised contract-wise but rejected here until a
#: control-side implementation exists — rejection is explicit, never silent.
EXECUTABLE_KINDS = frozenset({"scan_region", "ignore"})

#: Frames a scan_region target may use. IMAGE_PIXELS is rejected: pixel
#: targets are only meaningful next to one specific image and must be
#: converted to physical units by the proposer.
_POSITION_FRAMES = frozenset({CREATEC_SCAN_OFFSET_NM, IMAGE_CENTER_RELATIVE_NM})

MAX_ABS_BIAS_V = 10.0
MAX_SETPOINT_WARN_A = 100e-9


def validate_proposed_action(
    action: ProposedAction,
    *,
    max_target_offset_nm: float = 500.0,
) -> ValidationResult:
    """Check a proposal against static control-layer policy.

    ``max_target_offset_nm`` bounds how far from the current frame a
    relative (image-centre) target may sit — a coarse typo guard; the
    real per-move limit is enforced by TipMotionManager when executing.
    """
    errors: list[str] = []
    warnings: list[str] = []
    confirmations: list[str] = []

    if action.kind == "ignore":
        return ValidationResult(ok=True, proposed_action_id=action.action_id)

    if action.kind not in EXECUTABLE_KINDS:
        errors.append(
            f"action kind {action.kind!r} is not executable yet "
            f"(supported: {sorted(EXECUTABLE_KINDS)})"
        )
        return ValidationResult(False, action.action_id, errors, warnings, confirmations)

    # --- scan_region ---------------------------------------------------
    if action.target_nm is None:
        errors.append("scan_region requires target_nm")
    elif action.frame not in _POSITION_FRAMES:
        errors.append(
            f"scan_region target frame {action.frame!r} not accepted; "
            f"use one of {sorted(_POSITION_FRAMES)}"
        )
    elif action.frame == IMAGE_CENTER_RELATIVE_NM:
        dx, dy = action.target_nm
        if max(abs(float(dx)), abs(float(dy))) > max_target_offset_nm:
            errors.append(
                f"relative target ({dx:+.1f}, {dy:+.1f}) nm exceeds "
                f"{max_target_offset_nm:.0f} nm sanity bound"
            )

    if action.bias_V is None:
        errors.append("scan_region requires bias_V (proposer must resolve "
                      "'inherit current' before submitting)")
    else:
        bias = float(action.bias_V)
        if abs(bias) < MIN_CONST_CURRENT_BIAS_V:
            errors.append(
                f"|bias| = {abs(bias) * 1000:.2f} mV is below the "
                f"{MIN_CONST_CURRENT_BIAS_V * 1000:.1f} mV constant-current "
                "guard — the feedback loop would crash the tip at 0 V"
            )
        if abs(bias) > MAX_ABS_BIAS_V:
            errors.append(f"|bias| {abs(bias):.2f} V exceeds ±{MAX_ABS_BIAS_V:.0f} V")

    if action.setpoint_A is None:
        errors.append("scan_region requires setpoint_A")
    elif float(action.setpoint_A) <= 0:
        errors.append("setpoint must be positive")
    elif float(action.setpoint_A) > MAX_SETPOINT_WARN_A:
        warnings.append(
            f"setpoint {float(action.setpoint_A) * 1e9:.1f} nA is unusually high"
        )

    if action.size_nm is not None and min(float(v) for v in action.size_nm) <= 0:
        errors.append("size_nm must be positive")
    if action.pixels is not None and min(int(v) for v in action.pixels) <= 0:
        errors.append("pixels must be positive")

    if action.requires_operator_confirmation:
        confirmations.append("operator")

    return ValidationResult(
        ok=not errors,
        proposed_action_id=action.action_id,
        errors=errors,
        warnings=warnings,
        required_confirmations=confirmations,
    )


def build_validated_action(
    action: ProposedAction,
    validation: ValidationResult,
) -> ValidatedAction:
    """Turn a passing proposal into recipe steps.

    Raises ValueError when validation failed or confirmations are still
    outstanding — callers must not be able to "accidentally" execute.
    The motion to ``target_nm`` is intentionally NOT a recipe step:
    executors perform it through TipMotionManager, which owns the dynamic
    safety checks.
    """
    if validation.proposed_action_id != action.action_id:
        raise ValueError("validation does not belong to this action")
    if not validation.ok:
        raise ValueError(
            f"cannot build executable action from failed validation: "
            f"{'; '.join(validation.errors)}"
        )
    if validation.required_confirmations:
        raise ValueError(
            "confirmations outstanding: "
            f"{', '.join(validation.required_confirmations)} — clear "
            "requires_operator_confirmation only after actual approval"
        )

    steps: list[object] = []
    if action.kind == "scan_region":
        steps.append(ScanStep(
            bias_V=float(action.bias_V),
            setpoint_A=float(action.setpoint_A),
            size_nm=(tuple(float(v) for v in action.size_nm)
                     if action.size_nm is not None else (50.0, 50.0)),
            pixels=(tuple(int(v) for v in action.pixels)
                    if action.pixels is not None else (256, 256)),
            speed_nm_s=(float(action.speed_nm_s)
                        if action.speed_nm_s is not None else 50.0),
            label=f"proposed:{action.action_id}",
            memo=action.reason or f"proposed action {action.action_id}",
        ))
    # kind == "ignore" produces no steps by design.

    log.info(
        "proposal %s (%s, source=%s) validated -> %d step(s)",
        action.action_id, action.kind, action.source or "unknown", len(steps),
    )
    return ValidatedAction(
        action_id=f"validated:{action.action_id}",
        proposed_action_id=action.action_id,
        recipe_steps=steps,
        validation=validation,
    )
