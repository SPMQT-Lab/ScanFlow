# ScanFlow Review Plan

**Repository:** `SPMQT-Lab/ScanFlow`  
**Review focus:** architecture, safe instrument connection, motion/tracking modules, and reliability of unattended STM workflows.  
**Intended audience:** lab developers, instrument users, and supervisors reviewing whether ScanFlow is ready for routine use on the CreaTec STM.

---

## 1. Review goal

ScanFlow is not simply an image-analysis or plotting program. It sits close to a live STM and can change bias, setpoint, scan frame, spectroscopy state, XY offset, and tip-forming state. The review should therefore treat it as **instrument-control software**, with a higher standard than normal desktop analysis code.

The goal of the review is to answer four questions:

1. **Can the architecture remain maintainable as more instrument modes are added?**
2. **Are CreaTec/STMAFM connections handled safely across GUI and worker threads?**
3. **Are motion, drift correction, atom tracking, survey, and mosaic routines safe enough for unattended or semi-unattended operation?**
4. **Do the docs, tests, mock instrument, and sidecar metadata support reproducible lab use?**

The review should produce a short written outcome with three classes of findings:

- **Blockers:** issues that could damage the tip, sample, instrument state, or corrupt an overnight run.
- **High-priority maintainability issues:** issues that make future changes risky.
- **Useful improvements:** documentation, UI, dependency, or workflow improvements.

---

## 2. High-level architecture review

### 2.1 Current architectural picture

Start by drawing a simple dependency map of the program. A useful first-pass structure is:

```text
scanflow/
├── core/              # CreaTec COM facade and instrument controllers
├── automation/        # recipes, runners, survey/mosaic workflows
├── gui/               # PySide6 panels and windows
├── io/                # sidecars, manifests, session metadata
├── tools/             # lab PC sync/monitor utilities
└── tests/             # mock-based tests
```

The important distinction to check is:

```text
GUI widgets
   ↓ should only collect user input and display state

Automation runner / workers
   ↓ should coordinate recipe execution and expose progress

Core controllers / motion / safety
   ↓ should be the only layer that performs direct instrument operations

CreaTec COM object
```

The review should identify places where that layering is clean and places where it leaks.

### 2.2 Questions to ask

- Are direct COM calls confined mostly to `scanflow.core`?
- Are GUI panels doing instrument policy, or only collecting parameters and launching workers?
- Is `AutomationRunner` becoming too large and responsible for too many scientific/instrument routines?
- Are survey, mosaic, preview-followup, spectroscopy, and tip-forming routines implemented as separable algorithms or embedded in the runner?
- Are Qt-free executor classes being used consistently, or are they only a partial refactor?
- Can each automation mode be tested without launching the full GUI?

### 2.3 Files to inspect first

- `scanflow/gui/main_window.py`
- `scanflow/automation/runner.py`
- `scanflow/automation/executors.py`
- `scanflow/automation/recipe.py`
- `scanflow/core/stm_client.py`
- `scanflow/core/scan.py`
- `scanflow/core/motion.py`
- `scanflow/core/safety.py`

### 2.4 Likely architectural finding

`AutomationRunner` should be reviewed carefully. It currently appears to own thread lifecycle, recipe iteration, safety response, scan execution, spectroscopy, approach, tip forming, survey, mosaic, live-frame signals, logging, and artifact writing. That is understandable during fast development, but it is a risk for future reliability.

A reasonable design target is:

```text
AutomationRunner
    = QThread + signals + pause/stop + exception boundary

StepExecutor classes
    = scan, spectroscopy, survey, mosaic, approach, wait, tip-form

Core controllers
    = small wrappers around instrument functions

Motion/Safety managers
    = policy gates shared by all movement-capable routines
```

This would make it much easier to review new capabilities before they touch the real STM.

---

## 3. Instrument connection and COM safety review

### 3.1 Why this deserves its own review

CreaTec COM access is not normal Python I/O. The repository already recognises that COM proxies are thread-bound and that worker threads must obtain their own proxy before using `setp` / `getp`. This is a good sign, but it should be stress-reviewed because many instrument crashes in automation software come from thread/context mistakes, stale handles, retries of non-idempotent commands, or cleanup failures.

### 3.2 Review checklist

Check that the following are true:

- GUI-thread connection and worker-thread binding are separated intentionally.
- Every worker thread that calls the instrument invokes `STMClient.bind_thread()` before instrument access.
- Every worker thread releases COM state with `unbind_thread()` in a `finally` block.
- Physical actions are not blindly retried. Retrying `START`, `STOP`, `MOVE`, `PULSE`, or `SAVE` can duplicate real actions.
- Idempotent parameter writes can retry transient COM failures, but physical action commands should generally not.
- Disconnect is safe if the STM is already disconnected or in mock mode.
- Force quit is treated as a last resort and communicates that the instrument software may need restarting.
- Mock mode follows the same high-level code paths as live mode wherever possible.

### 3.3 Failure modes to test manually

Use the mock first, then only carefully on the real rig:

1. Start a run, stop once, confirm graceful stop.
2. Start a run, stop twice, confirm emergency-stop path.
3. Start a run, force quit, confirm warning and cleanup behaviour.
4. Disconnect during idle.
5. Disconnect during run, confirm the runner reports a controlled error.
6. Simulate or force a transient COM failure on a read.
7. Simulate or force a transient COM failure on a physical command.
8. Run GUI monitoring panels while an automation worker is active.
9. Confirm no GUI timer calls COM from the wrong thread during active automation.
10. Confirm STMAFM remains usable after failed runs.

### 3.4 Specific review questions

- Does every class that starts a `QThread` bind/unbind the CreaTec COM proxy?
- Are exceptions during `bind_thread()` handled explicitly?
- Could a GUI panel call a direct instrument method at the same time as the automation worker?
- Is there a single place to reason about physical-action keys?
- Are logs sufficient to reconstruct the last successful and failed COM calls?

---

## 4. Safety review: bias, current, Z-limit, and tip forming

### 4.1 Hard safety rules that should be treated as invariants

These should be checked in code and tests:

- Constant-current scans near 0 V are blocked or skipped.
- Tip-crash current threshold defaults to a conservative value.
- Safety monitor can stop the scan and retract the tip.
- Automated XY motion refuses to move while a scan is running.
- Tip forming cannot run unattended by accident.
- Any step that may move the tip has a safety gate before motion.
- Failed readback should stop motion-dependent workflows rather than proceeding blindly.

### 4.2 Review checklist

Inspect the following behaviours:

- 0 V guard in recipe construction.
- 0 V guard again in the runner before scan execution.
- Current-monitor threshold and units.
- Preamp exponent use in current conversion.
- Z-limit retraction behaviour.
- Handling of `None` or failed current readings.
- Whether safety checks are performed during long waits and long scans.
- Whether safety checks are performed during spectroscopy steps, survey steps, mosaic steps, and preview follow-up scans.
- Whether tip forming requires a fresh operator confirmation scoped to a specific run.
- Whether pre/post tip-form quality snapshots are required and logged.

### 4.3 Manual safety tests

These should be done with mock mode before any real instrument test:

```text
test: bias ramp through 0 V
expected: 0 V point is skipped in constant-current mode

test: constant-height ramp through 0 V
expected: 0 V allowed only if feedback is not active

test: simulated current spike
expected: runner stops, emits safety violation, triggers emergency stop path

test: XY move while scan running
expected: motion manager refuses

test: tip-form step without approval
expected: recipe stops and logs refusal

test: stale tip-form approval from previous run
expected: approval rejected
```

### 4.4 Suggested additional safety improvements

- Add a visible “instrument state” panel showing connection, scan status, feedback mode, bias, setpoint, preamp, and latest current.
- Log every safety-relevant decision in the acquisition log, not only GUI logs.
- Add a `dry_run_validate(recipe)` function that checks all steps for impossible or unsafe combinations before a run starts.
- Consider a global “armed” state for live instrument automation, distinct from mock/offline use.
- Add an optional maximum total run time or maximum number of scans guard for overnight recipes.

---

## 5. Motion, coordinate, and tracking review

### 5.1 Why this is high risk

The tracking modules are where image analysis becomes physical motion. A sign error, half-frame error, stale calibration, or wrong coordinate convention can move the tip to the wrong feature or outside the intended safe frame. This is more serious than a normal image-processing bug.

### 5.2 Coordinate conventions

Review `scanflow/core/scan_geometry.py` early. It should remain the single source of truth for coordinate conversion. In particular, check that all survey, mosaic, preview-followup, and atom-tracking code respects:

```text
SCAN.OFFSET.X.NM = centre of scan frame in X
SCAN.OFFSET.Y.NM = top edge / first scanline in Y
```

Reviewers should search for inline coordinate arithmetic. Any conversion between feature pixel positions, ProbeFlow centre-based offsets, and CreaTec scan offsets should call helper functions rather than reimplementing the arithmetic in workers.

### 5.3 Motion manager review

`TipMotionManager` should be treated as the policy gate for all automated XY motion. Review whether all high-level modules use it rather than calling `scan.set_offset_nm()` or low-level CreaTec offset commands directly.

Check:

- calibration strategy: current, delta, current-then-delta
- maximum single-move limit
- scan-idle check before motion
- current safety check before motion
- readback verification after motion
- structured `MotionResult` logging
- behaviour when readback is unavailable
- behaviour when readback misses tolerance
- behaviour after failed motion in survey/mosaic routines

### 5.4 Atom tracker review

The atom tracker uses a 5-point Z-gradient measurement around a reference location. This is a clever approach, but it has physical risk because it intentionally probes nearby positions and then applies a correction.

Review questions:

- Is the reference feature selection robust?
- Is the probe radius small enough for the expected feature and scan frame?
- Does it refuse to track if feature strength is too low?
- Are corrections clamped to a safe maximum?
- Is every probe move made through the motion safety layer?
- Does it bind/unbind COM in the worker thread?
- Does it always return the tip to the reference or a safe known position after failure?
- Does it behave sensibly on flat terraces, noisy images, clusters, and multiple nearby molecules?
- Are the sign conventions experimentally validated?

### 5.5 Survey and mosaic tracking review

Survey and mosaic routines should be reviewed as “image-analysis-driven motion”. The main concerns are:

- feature segmentation creates false positives
- feature size estimates create too-small or too-large zoom frames
- edge features are clipped
- clusters are split or merged incorrectly
- coordinate conversion from wide scan to zoom scan is wrong
- drift accumulates across a long campaign
- failed zoom scans do not leave the run in a clear state
- output manifests remain useful after partial failure

For `SurveyConfig`, review whether defaults are appropriate for common molecule surveys:

```text
wide scan: 120 nm
wide pixels: 512
zoom pixels: 256
zoom iterations: 3
min feature: 0.8 nm
max feature: 20 nm
max features: 30
settling: 5 s
```

These defaults are plausible, but they should be validated on several representative surfaces.

### 5.6 Tracking test data set

Create a small set of saved `.dat` files or synthetic images for repeatable tests:

1. sparse monomers on flat terrace
2. dimers / trimers / small clusters
3. high-density adsorbates
4. noisy low-contrast molecules
5. step edges
6. atomic lattice with no molecules
7. molecules near image edge
8. drifted repeated scans of the same region
9. deliberately wrong contrast polarity
10. partial scan or corrupted scan file

For each file, record expected behaviour:

```text
image: sparse_monomers_001.dat
expected:
  - detect 8–12 isolated features
  - reject edge molecules
  - zoom frame 3–6 nm
  - no correction > 2 nm
```

This will make future tracking changes much safer.

---

## 6. Recipe and unattended-run review

### 6.1 Recipe design questions

The recipe system is a good foundation. The review should check whether recipes are expressive enough without being too permissive.

Ask:

- Is every step type serialisable and reloadable?
- Are old recipe formats still handled safely?
- Are unknown/stale fields dropped intentionally and logged if needed?
- Are units explicit in names, e.g. `_V`, `_A`, `_nm`, `_s`, `_px`?
- Can a recipe be validated before execution?
- Does a recipe capture enough metadata to reproduce the run later?
- Are mixed scan/spectroscopy/approach/wait recipes clear to a student user?

### 6.2 Suggested `validate()` method

Add a method such as:

```python
issues = recipe.validate(mode="live")
```

It should return structured warnings/errors before execution:

```text
ERROR: constant-current scan at |V| < 5 mV
ERROR: setpoint exceeds configured safe range
ERROR: scan size exceeds configured instrument limit
ERROR: step requires XY motion but no position readback is available
WARNING: total run time exceeds 24 h
WARNING: safety disabled
WARNING: recipe contains tip-form step
```

This would make the GUI confirmation dialog much more meaningful.

---

## 7. Metadata, sidecars, and reproducibility review

### 7.1 What should be preserved

Every saved scan should have enough context to answer:

- What was the recipe?
- Which step produced this scan?
- What were the scan parameters?
- Where was the scan frame?
- Was the tip moved before this scan?
- Was safety enabled?
- What was the maximum observed current?
- Were there quality metrics?
- Was this a data scan, alignment scan, survey scan, tile scan, or follow-up scan?

The existing sidecar direction is very good and should be protected. It is the bridge between instrument operation and ProbeFlow analysis.

### 7.2 Review checklist

- Are sidecars written atomically?
- Are sidecars written for all scan-producing routines, not only simple sweeps?
- Are partial survey/mosaic runs still recoverable?
- Are paths relative where useful and absolute where necessary?
- Is the schema version explicit?
- Is the ScanFlow version recorded?
- Are units explicit in field names?
- Does ProbeFlow rely on stable sidecar fields?

---

## 8. GUI and operator workflow review

### 8.1 Review principle

The GUI should help the operator understand state and risk. It should not simply expose every internal knob.

### 8.2 Operator-facing checks

- Is it always clear whether ScanFlow is connected to the real STM or the mock STM?
- Is scan status visible?
- Is latest current visible?
- Is safety enabled visibly?
- Is the current recipe summary visible before pressing Start?
- Is the difference between Stop, second Stop, and Force Quit obvious?
- Are unsafe actions disabled while scanning?
- Are long-running actions moved off the GUI thread?
- Can a student recover after an error without guessing the state of STMAFM?

### 8.3 UI wording suggestions

Use direct lab language:

```text
Connected to real STM
Connected to mock STM
Safety enabled: abort if |I| > 1.0 nA
0 V constant-current points will be skipped
Stop: finish current safe stop
Second Stop: emergency retract
Force Quit: last resort; restart STMAFM if needed
```

Avoid ambiguous terms such as “tracking active” unless it says what is being tracked and whether it can move the tip.

---

## 9. Testing review

### 9.1 Current strengths to preserve

The mock STM makes it possible to test ScanFlow without the real microscope. That should be treated as a core part of the project, not a convenience.

### 9.2 Test categories to add or strengthen

#### Unit tests

- recipe construction and YAML roundtrip
- 0 V guard
- safety threshold
- coordinate helper functions
- feature discovery on synthetic images
- motion limit and readback failure
- sidecar schema

#### Integration tests using mock STM

- GUI can launch all panels
- sweep run completes
- sweep run stops gracefully
- safety abort during run
- survey campaign produces manifest
- mosaic campaign produces manifest
- preview follow-up scan creates expected sidecar
- atom tracker refuses weak feature
- atom tracker clamps correction

#### Hardware-adjacent manual tests

- connect/disconnect from STMAFM
- start/stop simple scan
- read current and preamp gain
- save scan to expected folder
- read scan offset before and after a small movement
- verify coordinate convention using a visible feature
- verify Z-limit emergency retract

### 9.3 Test data policy

Add a small `tests/data/` directory with synthetic arrays or tiny representative files. For real `.dat` files, use anonymised/non-critical examples and keep file sizes manageable.

---

## 10. Documentation review

### 10.1 Main issue

The README should be updated to reflect the current program. It currently reads like a two-tab sweep tool, while the code has grown into a broader automation portal with survey, mosaic, temperature, Z-stability, positioning, atom tracking, and ProbeFlow preview integration.

### 10.2 Recommended documentation structure

```text
README.md
  - What ScanFlow is and is not
  - Safety warning
  - Quick start: mock mode
  - Quick start: real STM
  - Main workflows
      - simple sweep
      - current ramp
      - survey
      - mosaic
      - preview follow-up
      - atom tracking
      - spectroscopy
  - Output files and sidecars
  - Emergency stop behaviour
  - Developer setup
  - Tests

docs/
  architecture.md
  instrument_connection.md
  safety_model.md
  motion_and_coordinates.md
  tracking_modules.md
  recipe_format.md
  lab_pc_deployment.md
```

### 10.3 Documentation rule

Any change that alters one of the following must update docs in the same pull request:

- physical motion
- safety behaviour
- recipe format
- sidecar schema
- GUI run controls
- CreaTec COM keys
- coordinate conventions

---

## 11. Dependency and deployment review

### 11.1 Concern

The project currently mixes instrument control, GUI, image processing, and possibly ML-style analysis dependencies. This is acceptable during development but may make the lab PC installation fragile.

### 11.2 Recommended split

Use optional dependency groups:

```text
scanflow
  core GUI + recipes + mock mode

scanflow[createc]
  pywin32 and live CreaTec COM support

scanflow[analysis]
  scikit-image, OpenCV, ProbeFlow integration

scanflow[ml]
  torch, torchvision, CLIP-like classification tools

scanflow[dev]
  pytest, pytest-qt, ruff
```

The lab PC should only install what it needs for the workflows being used.

---

## 12. Suggested review process

### Pass 1: Architecture and docs

- Read README, ROADMAP, and package structure.
- Compare documented features to actual GUI tabs.
- Identify stale or misleading documentation.
- Draw module dependency map.
- Identify large modules that need splitting.

### Pass 2: Instrument connection

- Review `STMClient`, thread binding, retry policy, raw COM passthroughs.
- Search for direct calls to `.raw`, `setp`, `getp`, and CreaTec command strings.
- Classify each as safe, acceptable, or should be wrapped.

### Pass 3: Safety

- Review safety monitor and emergency stop.
- Review 0 V guards.
- Review tip-forming approval.
- Review motion gating.
- Confirm safety logic is applied consistently across all routines.

### Pass 4: Motion and coordinates

- Review all frame-offset conversions.
- Search for inline coordinate arithmetic.
- Check survey/mosaic/preview/atom-tracker use central helpers.
- Validate against synthetic and known real images.

### Pass 5: Tracking modules

- Review feature discovery.
- Review atom tracker.
- Review survey and mosaic workflows.
- Check failure behaviour and partial-run recovery.

### Pass 6: Tests and mock

- Run the test suite.
- Identify which public methods lack tests.
- Add tests before refactoring risky modules.
- Confirm mock mode exercises the same paths as live mode.

### Pass 7: Operator workflow

- Launch GUI in mock mode.
- Walk through each tab as a student user.
- Note ambiguous controls, dangerous defaults, and unclear state displays.
- Confirm emergency actions are prominent and understandable.

---

## 13. Suggested output format for the review

Use this compact format for each finding:

```markdown
### Finding: AutomationRunner has too many responsibilities

**Severity:** high-priority maintainability issue

**Area:** architecture / automation

**Evidence:** scan, spectroscopy, approach, wait, tip-form, survey, mosaic, safety, logging, and artifact writing are coordinated in one QThread class.

**Why it matters:** changes to one automation mode risk breaking unrelated modes or safety handling.

**Recommendation:** extract step-specific Qt-free executors. Keep AutomationRunner as the thread/signal/error boundary.

**Suggested tests:** mock sweep, mock survey, mock mosaic, mock safety abort, recipe stop/resume.
```

---

## 14. Priority recommendations

### Priority 1: Update README and safety documentation

The documentation should match the actual program before broader lab use. It should clearly describe real vs mock mode, safety aborts, 0 V behaviour, stop/force-quit semantics, sidecars, and current GUI tabs.

### Priority 2: Refactor runner into executors

Continue the `executors.py` direction. Extract scan, spectroscopy, survey, mosaic, and tip-form execution into testable classes that do not depend directly on Qt.

### Priority 3: Make motion policy mandatory

All automated physical motion should go through `TipMotionManager`, or an equally explicit safety/policy wrapper. Direct offset calls should be rare and justified.

### Priority 4: Protect coordinate conventions

Keep `scan_geometry.py` as the single source of truth. Add tests for every helper and require survey/mosaic/preview workers to use these helpers.

### Priority 5: Strengthen tracking validation

Create a small tracking test dataset and expected-output table. Use it before changing feature discovery, grouping, atom tracking, survey, or mosaic code.

### Priority 6: Split dependencies

Avoid requiring heavy ML/analysis dependencies for basic instrument-control use on the lab PC.

---

## 15. Final review standard

ScanFlow should be judged by a stricter standard than ordinary lab analysis software:

> A failed analysis script wastes time.  
> A failed instrument-control script can crash a tip, damage a sample, leave STMAFM in a bad state, or ruin an overnight experiment.

The review should therefore favour boring, explicit, testable code over clever hidden behaviour. In particular, all modules that can move the tip, change feedback conditions, perform tip forming, or launch unattended scans should have clear safety gates, clear logs, and mock-mode tests.
