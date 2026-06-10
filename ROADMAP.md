# ScanFlow2 — Roadmap and Working Rules

**Last revised:** 2026-06-10.
**Primary planning reference:**
[docs/long_term_architecture.md](docs/long_term_architecture.md) — the
layered control/analysis/ML architecture this program is converging on.
This file records *current status, working rules, immediate priorities,
placeholders, and explicit non-goals*. Older plans live in
[docs/archive/](docs/archive/) and must not guide new work.

---

## 1. What works today

Verified on the rig and/or covered by the mock-STM test suite (177 tests):

- **Createc/STMAFM connection** — thread-safe COM proxy handling, retry
  policy for transient faults, careful stop/teardown sequencing. COM load
  was recently reduced substantially (opt-in conflated live frames,
  throttled GUI polling) to address controller-window lag.
- **Safety** — tip-crash current abort (fails closed on broken readback),
  0 V constant-current guard, emergency-stop path, Z-limit retract, and a
  motion policy gate (`TipMotionManager`) for all automated XY movement.
- **Mosaic campaigns** — wide overview → N×N tiles with per-tile
  parameters (including bias sweeps per tile) → wide overview, with
  absolute, clamped tile positioning.
- **Survey campaigns** — wide scan → feature discovery → per-feature
  zooms (see the open positioning question in §3.1 before trusting zoom
  centring unattended).
- **Bias/current sweeps** — GUI and CLI, with validation and time estimates.
- **Temperature monitoring** and **Z-stability monitoring**.
- **Acquisition logging** — JSONL event log plus per-scan
  `.scanflow.json` sidecars (schema `scanflow.acquisition.v1` via
  `scanflow.contracts.ScanRecord`) and session manifests; everything a
  run did is reconstructable afterwards.
- **Mock STM** — full offline development and CI without the instrument.

## 2. Architecture rules (must hold — enforced, not aspirational)

These are the rules the supervisor requires the architecture to maintain.
They are enforced by `tests/test_import_boundaries.py` and
`tools/dev/import_audit.py`; PRs that break them should not merge.

1. **Module separation.** Layer dependency arrows point one way only
   (see [docs/dependency_architecture.md](docs/dependency_architecture.md)):
   `contracts` (stdlib-only) ← `core` ← `automation` ← `gui`. Analysis
   and ML are optional extras and never imported by control paths.
2. **Only the control layer commands the STM.** Analysis/ML/GUI code
   proposes; the control core validates
   (`scanflow/automation/proposals.py`); only validated actions execute.
   All automated XY motion goes through `TipMotionManager`.
3. **One schema per artefact.** Scan metadata is
   `scanflow.contracts.ScanRecord` = the sidecar. No parallel models.
4. **Coordinates carry their frame.** Positions crossing a module
   boundary use the `scanflow.contracts.coordinates` frame identifiers —
   never bare tuples with an implicit convention.
5. **Performance / memory discipline.** Every COM call executes on
   STMAFM's GUI thread, so COM traffic *is* controller lag:
   - no full-frame (`DATA.SCAN`) transfers inside polling loops — frame
     pulls are opt-in, rate-limited, and conflated (latest-frame-wins);
   - no Qt queued signals carrying large payloads (they accumulate when
     the GUI falls behind — use a slot + notification instead);
   - new pollers/timers must state their COM cost and be bounded;
     run `tools/dev/import_audit.py` and watch the acquisition-log volume
     when adding any periodic task.

## 3. Immediate priorities (in order)

### 3.1 Resolve the frame-resize convention question (gates everything positional)
One manual experiment on the rig: place the frame over a visible feature,
halve `SCAN.IMAGESIZE.NM.Y`, observe whether the top edge or the centre
is preserved. Survey and mosaic currently embody *contradictory*
assumptions; at most one is right. Flagged in code as
`FIXME(B2-frame-resize)` (runner / scan_geometry / mock_dispatch),
guarded by `tests/test_open_findings.py`. Afterwards: unify all
positioning on `scan_geometry` helpers and encode the verified behaviour
in the mock.

### 3.2 Drift handling for mosaic/survey campaigns at 77 K
The active hard problem — see §4 for the policy and inventory.

### 3.3 Executor extraction (long_term_architecture Phase 3)
Move survey/mosaic/scan execution out of the 1,500-line `AutomationRunner`
QThread into Qt-free executor classes; runner becomes a thread/signal
shell; a Qt-free `BlockingRecipeRunner` becomes possible for the CLI.
Do **after** 3.1 so positioning code only moves once.

## 4. Drift correction policy

Drift at 77 K is reduced but not gone, and several correction strategies
have been tried over the project's history. Until a best approach is
chosen, the rules are:

**Rules**

- All drift-estimation/correction logic lives in **one dedicated module
  area** (target: `scanflow/drift/` when consolidation happens). It must
  not be intermingled with runner logic, panels, or core controllers.
- Each strategy sits behind a common, small interface (estimate →
  proposed correction), so strategies are swappable and comparable.
- Corrections that move the tip go through `TipMotionManager` and are
  logged to the acquisition log — no exceptions.
- Experimental strategies are compared on saved/mock data first
  (the review's tracking test-dataset idea) before touching the rig.

**Inventory of current and former strategies** (so nobody re-invents or
resurrects one unknowingly):

| Strategy | Where | Status |
|---|---|---|
| 5-point Z-gradient atom tracker | `scanflow/core/atom_tracker.py` + Atom Tracker tab | Working; policy-gated; per-feature, not campaign-wide |
| Survey zoom re-centring (feature re-detection between zoom iterations) | `runner._do_feature_zoom` | Working, subject to §3.1 |
| Manufacturer cross-correlation | `STMClient.crosscorr()` passthrough | Available, unused by automation |
| Z-drift *measurement* (no correction) | `scanflow/core/z_monitor.py` | Working (monitoring only) |
| Hybrid feature/phase-correlation alignment scans | **removed** — `recipe.from_yaml` still strips its `drift_*` keys from old YAML | Deprecated; do not resurrect without the comparison harness |
| DSP-side drift feed (`Drift_X/Y[A./sec]` keys) | not implemented | Candidate: lets the instrument correct inline, no alignment scans |
| Inter-tile drift correction in mosaics | not implemented | The gap §3.2 is about |

## 5. Placeholders — deliberately NOT being developed now

Mark any future edits in these areas with `PLACEHOLDER(spectroscopy)` /
`PLACEHOLDER(afm)` comments and keep them minimal.

- **Spectroscopy.** Works minimally today (`SpectroscopyStep` in recipes,
  `core/spectroscopy.py` wrappers, `.VERT` saving). It stays at this
  level for now. When it is eventually developed: the controller work
  goes in `core/spectroscopy.py`, execution goes in a dedicated
  spectroscopy executor (after §3.3), and safety polling during spectra —
  a known gap noted in REVIEW.md — gets addressed at the same time.
- **AFM / qPlus.** `core/afm.py` exists as a thin wrapper and stays that
  way. Lower priority than spectroscopy; no panels, no wizards, no
  feedback-mode switching work until the lab needs it.

## 6. Explicit non-goals

- **No `.sxm` / SpmImageTycoon export, no format conversion.** The
  Createc instrument writes `.dat`; that is the lab's data format.
  Downstream analysis uses tools that read `.dat` directly (ProbeFlow,
  WSxM, Gwyddion). ScanFlow's job is `.dat` + sidecar metadata, nothing
  else.
- **No ML in the control path.** ML may eventually propose actions
  through the contracts chain (`ProposedAction` → validation), but torch/
  CLIP/sklearn never become imports of core/automation/gui (enforced).
- **No new GUI tabs ahead of the executor extraction** — every tab adds
  COM pollers and lag surface; consolidate first.

## 7. Document map

| Document | Role |
|---|---|
| [docs/long_term_architecture.md](docs/long_term_architecture.md) | **Primary** planning reference (phases: 1 ✅ boundaries, 2 ✅ contracts, 3 ⏳ executors, 4+ planned) |
| `ROADMAP.md` (this file) | Current status, rules, priorities, non-goals |
| [docs/dependency_architecture.md](docs/dependency_architecture.md) | Dependency boundaries: rules, measurements, enforcement |
| [REVIEW.md](REVIEW.md) | 2026-06-10 instrument-control review: findings + fix log |
| [docs/archive/](docs/archive/) | Historical plans — provenance only, never guidance |
