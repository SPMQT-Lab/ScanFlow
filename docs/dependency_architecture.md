# ScanFlow Dependency Architecture

**Status:** boundaries enforced as of 2026-06-10 (see *Enforcement* below).
**Companion:** `docs/long_term_architecture.md` (the target layered
control/analysis/ML architecture — the primary planning reference),
`REVIEW.md` (full code review). The investigation brief that prompted this
work is archived at `docs/archive/2026-06_dependency_investigation_brief.md`.

## The rule

> A user must be able to install and run a safe bias/current sweep on the
> STM without installing any image-analysis or ML stack. Optional feature,
> optional dependency.

Layer rules (no arrow may point upward):

```
contracts   -> stdlib ONLY             (the shared data models — every layer
                                        may import it; it imports nothing)
core        -> contracts, stdlib, numpy   (NO Qt, NO analysis/ML)
automation  -> core, contracts, pyyaml, numpy  (Qt only in runner.py / workers/)
io          -> contracts, stdlib       (pptx lazy inside pptx_export)
gui         -> PySide6, pyqtgraph      (ProbeFlow/analysis lazy, on demand)
analysis    -> contracts + scikit-image (lazy), ProbeFlow (lazy) — extras
ml          -> contracts + torch/CLIP/sklearn — extras only, never imported
createc     -> pywin32                 — Windows lab PC only
```

**Contracts (Phase 2 of docs/long_term_architecture.md — done 2026-06-10):**
`scanflow/contracts/` holds the cross-layer data models — coordinate-frame
identifiers (every position names its frame; bare tuples with implicit
conventions are how the positioning bugs happened), `ScanRecord` (the
in-memory form of the `scanflow.acquisition.v1` sidecar — the sidecar
writer serialises it, so there is one schema, not two), `Feature` /
`AnalysisResult`, and the `ProposedAction` → `ValidationResult` →
`ValidatedAction` authority chain. Validation lives in the control layer
(`scanflow/automation/proposals.py`); only validated actions become recipe
steps. First consumer: the preview follow-up worker validates every
operator-selected target through this path before any motion.

## Dependency classification (pyproject.toml)

| Package | Status | Why | Needed for a simple sweep? |
|---|---|---|---|
| numpy | core | arrays, mock data, safety math | yes |
| pyyaml | core | recipe (de)serialisation | yes |
| PySide6 | core¹ | GUI + the QThread runner | GUI yes; CLI only at run time |
| pyqtgraph | core¹ | Z-stability / temperature / atom-tracker plots | no (GUI only) |
| matplotlib | core¹ | survey/mosaic PNG previews (lazy import) | no |
| pillow, scipy, scikit-image | core¹ | feature discovery, previews (skimage lazy) | no |
| python-pptx | core¹ | survey PPTX export (lazy import) | no |
| opencv-python, scikit-learn | `[analysis]` extra | ProbeFlow preview integration | no |
| torch, torchvision, openai-clip | `[ml]` extra | classification experiments; **never imported by scanflow** | no |
| pywin32 | `[createc]` extra | live COM on Windows | live yes, mock no |
| probeflow (external, undeclared) | runtime-optional | preview panel; loaded lazily with an install hint | no |

¹ Still mandatory in `pyproject.toml`. They are *import-time optional* on
the control paths (see measurements) — splitting them into a `[gui]` extra
is the natural next step but was deferred to keep this change reviewable;
see *Deferred*.

## Measured import footprint (2026-06-10, after the fixes)

What actually lands in `sys.modules` per entry path (fresh interpreter):

```
import scanflow                  -> (nothing heavy)
import scanflow.core             -> numpy
import scanflow.automation       -> numpy, yaml
import scanflow.cli              -> numpy, yaml
scanflow estimate bias …         -> numpy, yaml          (runs fully Qt-free)
import scanflow.gui.main_window  -> PySide6, pyqtgraph, numpy, yaml
                                    (NO probeflow, NO scipy, NO ML stack)
```

Before the fixes, `scanflow.core` (and therefore every path, including
recipe loading) imported PySide6, and **GUI startup hard-required
ProbeFlow** — a missing/broken probeflow install took down the whole GUI,
Sweep tab included.

## What creates (and prevents) the couplings

1. **`scanflow/core/__init__.py`** — the two Qt helper modules
   (`temp_poller`, `atom_tracker`) are re-exported lazily via module
   `__getattr__` (PEP 562). Importing *any* core submodule executes this
   `__init__`, so anything eager here lands in every entry path.
2. **`scanflow/cli.py`** — `AutomationRunner` (QThread) is imported inside
   `_run_blocking()` only; plan printing, validation, and `estimate` stay
   Qt-free.
3. **`scanflow/gui/main_window.py`** — `PreviewWindow` (→ ProbeFlow,
   scipy) is created lazily by the Preview toolbar button via
   `_ensure_preview_window()`. If the analysis stack is missing, the user
   gets a dialog naming the exact install command instead of a startup
   crash. Completed-scan paths are buffered so a late-opened preview
   starts on the latest scan.
4. **Already-lazy imports to preserve:** `skimage` inside
   `discover_features()`, `matplotlib` inside the runner's preview
   writers, `pptx` inside `pptx_export`, `probeflow` inside
   `group_survey` and the preview panel module.

## Enforcement

* `tests/test_import_boundaries.py` — subprocess-based smoke tests that
  fail CI if a boundary regresses (core/CLI Qt-free, estimate runs
  Qt-free, no ML stack on any control path, GUI startup ProbeFlow-free).
* `tools/dev/import_audit.py` — run manually before merging PRs; prints
  per-file heavy imports (top-level vs lazy) and the measured runtime
  footprint per entry path, and exits non-zero on violations.

## Installation profiles

```bash
pip install -e ".[createc]"            # lab PC, CLI-driven control
pip install -e ".[createc]"            # lab PC with GUI (PySide6 is core today)
pip install -e ".[analysis]"           # + ProbeFlow preview integration deps
pip install -e ".[analysis,ml,dev]"    # full development environment
```

## Deferred (intentionally out of scope for this pass)

* **`[gui]` extra** (PySide6/pyqtgraph optional): all GUI imports are
  already confined to `gui/`, the runner, workers, and the lazy core
  helpers, so the packaging split is mechanical — but ScanFlow today is
  primarily operated through the GUI, so core-vs-gui packaging should be
  decided together with the blocking-runner work below.
* **Qt-free `BlockingRecipeRunner`** (investigation §4.3/§7.3): CLI *runs*
  still construct `AutomationRunner` (QThread) at execution time. A
  blocking runner without Qt belongs to the planned executor extraction
  (REVIEW.md finding H2) — extracting survey/mosaic into Qt-free executor
  classes first avoids building the blocking runner against code that is
  about to move.
* **Declaring `probeflow`**: it is a sibling lab package not on PyPI;
  it stays an undeclared runtime-optional import with a guarded error
  message rather than an installable extra.
* **`tile_centers_in_wide_pixels`/`discover_features` re-exports** in
  `scanflow.automation.__init__` stay eager — they are numpy-only and
  several tests/panels import them from the package root.

## Rules for future PRs

1. New module needs OpenCV / scikit-image / ProbeFlow / torch? It lives
   behind an extra **and** imports lazily with an actionable error.
2. Nothing under `scanflow/core/` may import Qt eagerly; Qt helpers go
   through the lazy exports in `core/__init__.py`.
3. No instrument-control module (`core/`, `automation/` except the
   runner/workers, `io/`) may import from `gui/`.
4. Run `python tools/dev/import_audit.py` (or rely on
   `tests/test_import_boundaries.py`) before merging.
