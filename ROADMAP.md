# ScanFlow Roadmap

This document describes the planned evolution of ScanFlow beyond the current
phase. Each phase has a clear goal, exit criteria, and concrete tasks.

The current state (post-refactor) covers:

- New `setp`/`getp` API with SI units
- Coarse approach + Z-limit + slider panels
- Scan control panel with channel selector
- Lock-in + I/V point spectroscopy
- Cryo temperature readout
- Drift detection + correction during automation
- Overnight-safe recipes (DST suppression, configurable save folder)

---

## Phase 2 — Live awareness (next, ~1 week)

**Goal:** the user can see what the STM is doing without leaving ScanFlow.

### 2.1 Live scan viewer
- Embed a `pyqtgraph.ImageView` in a new **Live View** tab.
- After each scan saves, load the `.dat` via `createc.Createc_pyFile.DAT_IMG`
  and update the viewer.
- Add a channel selector (TOPOGRAPHY / CURRENT / DF / Lock-in X) and an
  auto-clip percentile slider.
- Overlay the cumulative drift trail.

### 2.2 Watchdog + notifications
- `scanflow/notify/` module with three back-ends: `sound`, `email`, `desktop`.
- Trigger on: scan finished, recipe finished, error, drift confidence < threshold.
- Configurable per recipe.

### 2.3 Approach-status integration
- Couple the approach result back to the **Scan Control** tab — auto-refresh
  parameters when the tip enters tunnelling.
- Disable scan controls while approach is in progress.

**Exit criteria:** a user can run an overnight recipe, watch scans appear in
real time, and get a phone notification when the run finishes or errors out.

---

## Phase 3 — Spectroscopy maturity (~1–2 weeks)

**Goal:** ScanFlow can drive every spectroscopy mode the CreaTec supports,
saving raw `.VERT` plus a sidecar JSON with metadata + lock-in state.

### 3.1 Multi-point and line spectroscopy
- Wrap `btn_vertspec_mult` and `btn_vertspec_line` in dedicated panels.
- Pick positions visually on the live image (click to add a marker).
- Export marker lists as YAML, alongside the recipe.

### 3.2 Spectroscopy on a grid
- Wrap `btn_spectraongrid` with a UI for defining the grid (origin, spacing,
  N×M).
- Auto-name files: `<datestamp>_grid_<i>_<j>.VERT`.

### 3.3 Spectroscopy recipes
- Extend `MeasurementRecipe` with a `SpectroscopyStep` type.
- Allow mixed image+spectroscopy recipes (e.g. "scan, then grid-spec, then
  scan again").

### 3.4 dI/dV imaging
- Add a panel that runs a normal scan with `Lock-in X` as a recorded channel,
  with the lock-in configured for bias modulation.

**Exit criteria:** a user can define a recipe that runs an overview scan,
records dI/dV grid spectroscopy at picked points, and returns to image scanning
— all unattended.

---

## Phase 4 — AFM / qPlus support (~1 week)

**Goal:** the AFM Mode in `stmafm.ini` is fully usable from ScanFlow.

### 4.1 PLL / qPlus tuning panel
- Wrap `AFMController.find_resonance` in a wizard:
  1. Broad scan → display amplitude vs frequency curve
  2. Auto-fit + zoom in
  3. Apply → set centre frequency, enable amplitude control
- Tune controller bandwidth sliders.

### 4.2 Feedback channel switcher
- A clearly labelled toggle between STM (current) and AFM (Δf) feedback,
  with safety prompts (Z-limit on/off, ramp setpoint slowly).

### 4.3 dF-Z spectroscopy
- New spec mode in the spectroscopy panel: ramp Z while logging Δf.

**Exit criteria:** the existing manufacturer `STM_AFM_operation.py` and
`AFM_STM_operation.py` example scripts can both be expressed entirely through
ScanFlow.

---

## Phase 5 — Sample mapping & navigation (~1–2 weeks)

**Goal:** track where the tip has been on the sample, and let the user revisit
previous locations.

### 5.1 Sample map widget
- A 2-D view of all scans taken in a session, plotted by their absolute
  offsets (slider position + scan-frame offset).
- Click a scan → reload its parameters into the **Scan Control** tab.

### 5.2 Coordinate bookkeeping
- Add an `XYPosition` accumulator that integrates slider pulses (with the
  user supplying a per-pulse nm calibration).
- Save the position log to disk so it survives restarts.

### 5.3 "Return to previous location"
- One-click reverse of slider motion to a prior bookmark.

**Exit criteria:** after moving across the sample for two hours, a user can
visually identify and re-approach to any earlier scan area.

---

## Phase 6 — Robustness & dev experience (~1 week)

**Goal:** ScanFlow can be developed and tested without the instrument, and
gracefully handles real-world failures.

### 6.1 Mock STM
- `scanflow.core.mock.MockSTMClient` that simulates the COM API.
- Generates synthetic images (with controllable drift, noise, atomic lattice).
- Used by tests and as an offline-mode toggle in the GUI.

### 6.2 Comprehensive tests
- `pytest-qt` integration to test GUI panels with the mock client.
- Property-based tests for the recipe builders.
- Smoke test for every panel (boot, click around, no exceptions).

### 6.3 Error handling
- Per-call retry policy for COM operations (transient errors are common).
- Recipe-level "on error" handler: stop / pause / retry / continue.
- Crash log with the last 1000 lines of the session log.

### 6.4 Settings & preferences
- `~/.scanflow/config.yaml` with all defaults user-tweakable.
- Settings dialog in the GUI.

**Exit criteria:** the test suite covers every public method on every
controller and every GUI panel, all using the mock client.

---

## Phase 7 — Smarter drift correction (~2 weeks, research-y)

**Goal:** beat the current phase-cross-correlation approach on tricky surfaces.

### 7.1 Feature-based tracking
- Optional ORB/SIFT feature matching path for highly textured surfaces.
- Compare cross-correlation vs feature shift; pick the higher-confidence.

### 7.2 Anisotropic drift model
- Fit drift rate per axis from the last N corrections.
- Predict the next drift instead of always doing an alignment scan.
- Skip alignment scans when prediction confidence is high.

### 7.3 Drift compensation via STM's internal mechanism
- Wire `Drift_X[A./sec]` and `Drift_Y[A./sec]` into the GUI.
- Let ScanFlow estimate these and push them to the instrument so the DSP
  does the correction inline — eliminates the need for alignment scans.

### 7.4 Atomic-resolution drift sub-pixel
- When the lattice is resolved, use lattice-vector tracking for sub-pixel
  accuracy (interface with `AiSurf` for lattice extraction).

**Exit criteria:** drift correction works on bias values where features are
weak, and overnight runs need 30–50% fewer alignment scans.

---

## Phase 8 — Integration with the wider ScanFlow ecosystem (~1 week)

**Goal:** ScanFlow feeds clean data into the existing lab tools without manual
file shuffling.

### 8.1 ProbeFlow handoff
- Optional folder-watcher that exports finished `.dat` files plus metadata as
  ProbeFlow-compatible JSON sidecars.
- Single "Open last scan in ProbeFlow" button.

### 8.2 SpmImageTycoon export
- Optional batch-export to the `.sxm` format used by SpmImageTycoon.

### 8.3 AiSurf trigger
- Per-scan checkbox: "auto-analyse lattice with AiSurf" — runs the analysis
  after each scan and writes the result alongside the `.dat`.

**Exit criteria:** a user can run an overnight session and wake up to scans
already lattice-analysed, organised by ProbeFlow, with the data ready for
review.

---

## Cross-cutting concerns (continuous)

- **Documentation:** every public method on a controller has a docstring; the
  README and ROADMAP are kept in sync with reality.
- **Logging:** every COM call logs at DEBUG; every user-visible action logs
  at INFO. Daily rotating log file at `~/.scanflow/logs/`.
- **Performance:** keep the GUI responsive under 1-Hz scan completion rates.
  Use QThreads for any operation that can block.
- **Versioning:** SemVer; the recipe YAML format gets a `schema_version` field
  before the first 1.0 release.
