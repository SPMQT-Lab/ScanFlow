# ScanFlow Long-Term Architecture: Separating STM Control, Analysis, and ML

> **STATUS (2026-06-10): this is the PRIMARY planning reference for
> ScanFlow2.** All other plans are subordinate to it or archived
> (`docs/archive/` — historical, not guidance). Current working rules and
> priorities: [`ROADMAP.md`](../ROADMAP.md).
>
> Phase progress (§14): Phase 1 (package boundaries) ✅ done ·
> Phase 2 (contracts) ✅ done — `scanflow/contracts/` +
> `scanflow/automation/proposals.py` · Phase 3 (Qt-free execution) ⏳ next,
> gated on the `FIXME(B2-frame-resize)` rig experiment · Phases 4–7 planned.

**Purpose:** propose a clean long-term architecture for ScanFlow and related lab software so that STM control remains safe, simple, and maintainable, while image analysis and ML modules can evolve independently.

**Context:** ScanFlow is becoming more than a simple sweep runner. It now touches instrument control, GUI workflows, feature detection, survey/mosaic automation, ProbeFlow integration, and potentially ML-based image interpretation. This is useful, but it creates an architectural risk: methods for analysing images may change often, while the hardware-control layer must remain conservative, testable, and safe.

The goal is not to remove ML or analysis from the ecosystem. The goal is to make sure they connect through stable interfaces and never become entangled with the safety-critical control path.

---

## 1. Core principle

The central architectural rule should be:

> **Only the control layer is allowed to command the STM. Analysis and ML layers may observe, analyse, rank, and propose actions, but they must not directly move the tip, change bias, change setpoint, start scans, run spectroscopy, or perform tip-forming operations.**

This gives the program a clear division of authority:

```text
ML / analysis:
  "I think this object is a monomer."
  "I think this region is interesting."
  "I suggest a 5 nm zoom scan here."
  "I suggest dI/dV on these three points."

Control core:
  "Is this action safe?"
  "Is the instrument idle?"
  "Is the requested movement allowed?"
  "Does this require operator confirmation?"
  "Can this be converted into a valid recipe step?"
  "Should the STM execute it?"
```

This distinction is essential. ML models can change quickly. Instrument-control safety rules should not.

---

## 2. Why this matters for ScanFlow

ScanFlow sits close to a real instrument. It can change parameters and potentially move the tip. This makes it different from ProbeFlow-style offline image analysis.

A failed image-classification model might produce a bad label. That is inconvenient.

A failed instrument-control routine might:

- crash the STM tip,
- damage the sample,
- leave STMAFM in an inconsistent state,
- ruin an overnight experiment,
- overwrite or mislabel data,
- move to the wrong part of the sample,
- run spectroscopy at the wrong point,
- disable or bypass safety checks.

For that reason, control code must be treated as a more conservative layer than analysis or ML code.

The long-term design should allow the lab to improve or completely replace the ML method without rewriting the STM control suite.

---

## 3. Recommended high-level architecture

Use a layered architecture:

```text
┌─────────────────────────────────────────────────────────────┐
│ GUI / Operator Suite                                        │
│ Human-facing controls, recipe editor, logs, status panels    │
│ Shows analysis results and asks operator to approve actions   │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Control Core                                                │
│ Recipes, runners, safety, motion policy, sidecars, schemas   │
│ The only layer allowed to command the STM                    │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Instrument Driver Layer                                     │
│ CreaTec/STMAFM COM adapter, mock instrument backend          │
│ Instrument-specific keys and hardware communication          │
└─────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────┐
│ Analysis Layer                                              │
│ Classical image analysis, feature finding, segmentation      │
│ Consumes scan records, emits analysis results                │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ ML Layer                                                    │
│ Learned feature detectors, classifiers, ranking models       │
│ Consumes images/results, emits labels/confidence/proposals   │
└─────────────────────────────────────────────────────────────┘
```

The important rule is that the analysis and ML layers do **not** directly import the hardware-control layer.

They should speak to ScanFlow through data contracts.

---

## 4. Proposed package split

There are two reasonable ways to implement the split:

1. **Single repository, multiple Python packages**
2. **Multiple repositories**

For now, a single repository with multiple packages or clearly separated subpackages is probably easier. Multiple repositories can come later if the boundaries become stable.

A sensible package layout would be:

```text
scanflow-core/
  Core control abstractions
  Recipes
  Safety model
  Motion policy
  Sidecar schemas
  Mock instrument interfaces
  Qt-free recipe execution

scanflow-createc/
  CreaTec/STMAFM backend
  pywin32 / pythoncom
  COM key mapping
  Thread-bound proxy handling
  CreaTec-specific scan, feedback, spectroscopy, AFM wrappers

scanflow-gui/
  PySide6 GUI
  Sweep panel
  Recipe editor
  Status panels
  Log panels
  Operator approval dialogs
  Optional plugin panels for analysis/ML results

scanflow-analysis/
  Classical analysis tools
  Feature detection
  Segmentation
  Drift estimation
  Grouping
  Survey/mosaic planning helpers
  ProbeFlow bridge if needed

scanflow-ml/
  Learned detectors/classifiers
  Torch / CLIP / sklearn / model-specific dependencies
  Model versioning
  Inference wrappers
  Confidence calibration

scanflow-contracts/
  Shared dataclasses or schema definitions
  ScanRecord
  AnalysisResult
  Feature
  ProposedAction
  ValidatedAction
  Coordinate conventions
  Schema versions

probeflow/
  Offline image analysis and review
  Batch processing
  Publication-quality exports
  Manual analysis workflows
```

This does not mean everything must be physically separated immediately. It means the architecture should move toward this separation.

---

## 5. Responsibility boundaries

### 5.1 `scanflow-core`

This package owns the instrument-control logic that should be independent of the particular GUI and independent of ML.

It should contain:

- recipe schema,
- recipe validation,
- scan-step definitions,
- spectroscopy-step definitions,
- wait/approach/tip-form step definitions,
- safety configuration,
- safety state,
- motion-policy interfaces,
- sidecar writing,
- acquisition logs,
- mock instrument interfaces,
- Qt-free recipe execution.

It should not contain:

- PySide6 widgets,
- OpenCV feature detection,
- Torch/CLIP inference,
- ProbeFlow GUI integration,
- CreaTec COM implementation details, except through interfaces.

Possible dependencies:

```text
stdlib
numpy, if needed
pyyaml
```

The control core should remain small.

### 5.2 `scanflow-createc`

This package owns the CreaTec/STMAFM implementation.

It should contain:

- `STMClient` or equivalent CreaTec client,
- COM proxy binding/unbinding,
- CreaTec `setp` / `getp` wrappers,
- scan controller,
- feedback controller,
- spectroscopy controller,
- lock-in controller,
- AFM controller,
- temperature monitor,
- CreaTec-specific error handling,
- mock-compatible backend interface.

It should not contain:

- ML logic,
- feature classification,
- ProbeFlow preview code,
- PySide6 widgets, except possibly optional diagnostics,
- high-level experiment-planning logic.

Possible dependencies:

```text
scanflow-core
pywin32
pythoncom
```

### 5.3 `scanflow-gui`

This package owns the operator-facing application.

It should contain:

- main window,
- sweep panel,
- status panel,
- log panel,
- recipe editor,
- safety display,
- preview/result browser,
- action approval dialogs,
- plugin loader for optional analysis panels.

It should not directly perform low-level hardware actions. It should ask the control core to validate and execute actions.

Possible dependencies:

```text
scanflow-core
scanflow-createc, optional
PySide6
pyqtgraph
```

Optional GUI panels may depend on `scanflow-analysis` or `scanflow-ml`, but those panels should be lazy-loaded.

### 5.4 `scanflow-analysis`

This package owns classical image analysis.

It should contain:

- plane/background correction,
- feature detection,
- segmentation,
- drift estimation,
- molecule/cluster grouping,
- scan comparison,
- non-ML classifiers,
- conversion of analysis results to proposed follow-up actions.

It should not command the STM.

It may propose actions, but the control core decides whether they can execute.

Possible dependencies:

```text
scanflow-contracts
numpy
scipy
scikit-image
opencv-python
matplotlib, optional
ProbeFlow, optional
```

### 5.5 `scanflow-ml`

This package owns learned models.

It should contain:

- model loading,
- inference,
- learned detectors,
- learned classifiers,
- confidence calibration,
- model metadata,
- model versioning,
- conversion of ML outputs to `AnalysisResult`.

It should not command the STM.

It should not know about CreaTec COM keys.

Possible dependencies:

```text
scanflow-contracts
scanflow-analysis, optional
torch
torchvision
openai-clip
scikit-learn
```

### 5.6 `scanflow-contracts`

This package is the most important stabilising element. It defines how all other layers communicate.

It should be lightweight and boring.

It should contain:

- dataclasses or schema models,
- units,
- coordinate conventions,
- scan record schema,
- analysis result schema,
- proposed action schema,
- validation result schema,
- schema-version definitions.

Possible dependencies:

```text
stdlib
typing_extensions, if needed
pydantic, optional
```

This package should not depend on GUI, CreaTec, analysis, or ML packages.

---

## 6. The control/analysis contract

The clean boundary is not “ML calls ScanFlow.”

The clean boundary is:

```text
ScanFlow acquires data
        ↓
ScanFlow writes ScanRecord / sidecar
        ↓
Analysis or ML reads ScanRecord
        ↓
Analysis or ML emits AnalysisResult
        ↓
Planner emits ProposedAction
        ↓
Control core validates ProposedAction
        ↓
Operator or recipe accepts action
        ↓
Control core executes safe ValidatedAction
```

This flow preserves authority. Analysis and ML never act directly on the instrument.

---

## 7. Key shared data models

The following are example models. They can be implemented as dataclasses initially.

### 7.1 `ScanRecord`

```python
@dataclass
class ScanRecord:
    schema: str
    scan_id: str
    session_id: str
    raw_path: str
    source_format: str

    created_at: str
    instrument: str
    backend: str

    bias_V: float
    setpoint_A: float
    size_nm: tuple[float, float]
    pixels: tuple[int, int]
    speed_nm_s: float
    rotation_deg: float
    channels: tuple[str, ...]

    position_nm: tuple[float, float] | None
    coordinate_system: str

    safety_enabled: bool
    safety_current_limit_A: float | None
    max_observed_current_A: float | None

    routine: str
    step_index: int | None
    step_label: str | None
```

This is the stable record of what was acquired.

### 7.2 `Feature`

```python
@dataclass
class Feature:
    feature_id: str
    x_nm: float
    y_nm: float
    frame: str

    bbox_nm: tuple[float, float, float, float] | None
    mask_path: str | None

    label: str | None
    confidence: float | None

    source: str
    source_version: str
```

Features should be described in physical units, not just pixels.

The `frame` field should say what coordinate system the feature uses, for example:

```text
image_center_relative_nm
createc_scan_offset_nm
absolute_sample_nm
```

This avoids hidden coordinate-convention bugs.

### 7.3 `AnalysisResult`

```python
@dataclass
class AnalysisResult:
    schema: str
    analysis_id: str
    input_scan_id: str

    algorithm: str
    algorithm_version: str
    created_at: str

    features: list[Feature]
    warnings: list[str]
    quality_metrics: dict[str, float]
```

The analysis result says what was found, how it was found, and how confident the algorithm is.

### 7.4 `ProposedAction`

```python
@dataclass
class ProposedAction:
    action_id: str
    source_analysis_id: str

    kind: str
    reason: str
    confidence: float | None

    target_nm: tuple[float, float] | None
    size_nm: tuple[float, float] | None

    bias_V: float | None
    setpoint_A: float | None
    pixels: tuple[int, int] | None
    speed_nm_s: float | None

    spectroscopy_points_nm: list[tuple[float, float]] | None

    requires_operator_confirmation: bool
    source: str
```

Examples of `kind`:

```text
scan_region
run_spectroscopy
rescan_current_region
track_feature
ignore
```

This object is still not executable. It is only a suggestion.

### 7.5 `ValidationResult`

```python
@dataclass
class ValidationResult:
    ok: bool
    proposed_action_id: str
    errors: list[str]
    warnings: list[str]
    required_confirmations: list[str]
```

The control core produces this after checking the proposed action against instrument state, safety policy, coordinate bounds, and recipe constraints.

### 7.6 `ValidatedAction`

```python
@dataclass
class ValidatedAction:
    action_id: str
    proposed_action_id: str
    recipe_steps: list[object]
    validation: ValidationResult
```

Only this can be executed.

---

## 8. Authority model

Every module should have a clear authority level.

| Layer | Can read images? | Can classify? | Can propose actions? | Can validate safety? | Can command STM? |
|---|---:|---:|---:|---:|---:|
| ML | yes | yes | yes | no | no |
| Analysis | yes | yes, if non-ML | yes | no | no |
| GUI | yes | no, except display | submit to control | no | no direct low-level commands |
| Control core | yes, if needed | no | yes | yes | through driver |
| Driver | no, except live data | no | no | no policy decisions | yes, low-level only |
| Operator | yes | yes | yes | yes, via confirmation | through GUI/control only |

The key point is that the CreaTec driver can execute low-level commands, but it should not decide high-level safety policy. The control core decides policy. The driver implements the hardware-specific mechanism.

---

## 9. Proposed command flow examples

### 9.1 Simple bias sweep

```text
Operator enters sweep parameters
        ↓
GUI builds BiasRampRecipe
        ↓
Control core validates recipe
        ↓
Control core executes recipe through CreaTec backend
        ↓
Sidecars written
        ↓
Analysis/ML not involved
```

Dependencies needed:

```text
scanflow-core
scanflow-createc
scanflow-gui, if GUI is used
```

No ML or image-analysis packages are needed.

### 9.2 ML-assisted molecule survey

```text
Control core runs wide scan
        ↓
ScanRecord sidecar written
        ↓
Analysis layer loads image
        ↓
ML layer labels candidate molecules
        ↓
Planner creates ProposedAction objects:
    - zoom monomers
    - run spectroscopy on representative clusters
        ↓
GUI displays suggestions
        ↓
Operator accepts selected suggestions
        ↓
Control core validates each action
        ↓
Control core executes safe recipe steps
```

ML never calls the STM directly.

### 9.3 Automated but bounded follow-up

A more advanced future mode might allow automatic acceptance of ML suggestions, but only inside a strict sandbox:

```text
Allowed:
  - maximum move per action: 20 nm
  - maximum number of follow-up scans: 10
  - allowed bias range: ±1 V
  - safety current threshold: enabled
  - no tip forming
  - no spectroscopy unless explicitly armed
  - no actions outside parent scan frame
```

The control core enforces these limits. The ML model cannot override them.

---

## 10. Package dependency rules

### 10.1 Allowed dependencies

```text
scanflow-contracts:
  stdlib only, or very light validation library

scanflow-core:
  scanflow-contracts
  numpy
  pyyaml

scanflow-createc:
  scanflow-core
  pywin32
  pythoncom

scanflow-gui:
  scanflow-core
  PySide6
  pyqtgraph

scanflow-analysis:
  scanflow-contracts
  numpy
  scipy
  scikit-image
  opencv-python
  matplotlib, optional
  probeflow, optional

scanflow-ml:
  scanflow-contracts
  scanflow-analysis, optional
  torch
  torchvision
  openai-clip
  scikit-learn
```

### 10.2 Forbidden dependencies

The following should be forbidden:

```text
scanflow-core importing scanflow-ml
scanflow-core importing scanflow-analysis
scanflow-core importing PySide6
scanflow-core importing pyqtgraph
scanflow-core importing cv2
scanflow-core importing torch

scanflow-createc importing scanflow-ml
scanflow-createc importing scanflow-analysis
scanflow-createc importing PySide6

scanflow-ml importing scanflow-createc
scanflow-ml importing STMClient
scanflow-ml importing SafetyMonitor
scanflow-ml importing TipMotionManager
scanflow-ml importing pywin32/pythoncom

scanflow-analysis importing scanflow-createc
scanflow-analysis importing STMClient
scanflow-analysis importing pywin32/pythoncom
```

These rules should be tested automatically.

---

## 11. Enforcing the architecture

### 11.1 Import-boundary tests

Add tests such as:

```python
def test_core_does_not_import_gui_or_ml():
    import subprocess
    result = subprocess.run(
        ["python", "-c", "import scanflow_core, sys; print('\\n'.join(sys.modules))"],
        capture_output=True,
        text=True,
    )
    forbidden = ["PySide6", "torch", "cv2", "skimage", "sklearn", "probeflow"]
    for name in forbidden:
        assert name not in result.stdout
```

Better still, use a small import-audit tool that maps imports by package.

### 11.2 Static dependency tests

A script can parse imports from source files and fail if forbidden dependency arrows appear.

Example rules:

```yaml
scanflow_core:
  forbidden:
    - PySide6
    - pyqtgraph
    - cv2
    - skimage
    - torch
    - torchvision
    - sklearn
    - probeflow
    - scanflow_ml
    - scanflow_analysis

scanflow_ml:
  forbidden:
    - scanflow_createc
    - pywin32
    - pythoncom
```

### 11.3 Runtime tests

Use fresh virtual environments or CI jobs:

```text
core-only:
  install scanflow-core
  import recipes
  load/save recipe
  validate simple sweep

gui:
  install scanflow-core + scanflow-gui
  launch GUI in mock mode
  open Sweep tab

createc:
  install scanflow-core + scanflow-createc
  run mock backend tests
  run live backend tests only on lab machine

analysis:
  install scanflow-analysis
  run feature detection tests

ml:
  install scanflow-ml
  run model-loading/inference tests
```

### 11.4 No direct hardware imports in ML tests

Add a test that imports the ML package and checks:

```python
forbidden = [
    "pythoncom",
    "win32com",
    "scanflow_createc",
    "scanflow.core.stm_client",
]
```

None should appear in `sys.modules`.

---

## 12. GUI plugin model

The GUI should not need to know all possible analysis methods at import time.

Instead, use a plugin-like pattern:

```text
scanflow-gui starts
        ↓
loads core panels:
  - Sweep
  - Log
  - Status
        ↓
optional plugins register panels:
  - Preview
  - Survey
  - ML Suggestions
  - ProbeFlow Browser
```

If optional dependencies are missing, the GUI should still open. The missing feature should show a helpful message.

Example:

```text
The ML Suggestions panel requires scanflow-ml.
Install with:

    pip install -e ".[ml]"

or install the lab package:

    pip install -e ../scanflow-ml
```

The GUI should distinguish:

```text
core panel failed -> serious error
optional panel missing -> feature unavailable, not a startup failure
```

---

## 13. How ML should feed into control

ML should connect through an adapter, not through direct access to the STM.

### 13.1 Detector interface

```python
class FeatureDetector:
    name: str
    version: str

    def detect(self, scan: ScanRecord) -> AnalysisResult:
        ...
```

Implementations:

```text
ThresholdFeatureDetector
ProbeFlowFeatureDetector
TorchMoleculeDetector
CLIPFeatureClassifier
```

The control suite does not need to know which detector produced the result.

### 13.2 Planner interface

```python
class ActionPlanner:
    def propose(self, result: AnalysisResult) -> list[ProposedAction]:
        ...
```

Implementations:

```text
ZoomEveryMonomerPlanner
RepresentativeClusterSpectroscopyPlanner
TrackBrightestFeaturePlanner
HumanReviewOnlyPlanner
```

### 13.3 Control validation interface

```python
class ActionValidator:
    def validate(self, action: ProposedAction, state: InstrumentState) -> ValidationResult:
        ...
```

The validator is part of the control core, not ML.

It checks:

- scan status,
- feedback state,
- current safety state,
- motion limits,
- coordinate bounds,
- bias/setpoint ranges,
- spectroscopy constraints,
- operator confirmation requirements,
- allowed automation mode.

### 13.4 Execution interface

```python
class ControlExecutor:
    def execute(self, action: ValidatedAction) -> ExecutionResult:
        ...
```

Only validated actions execute.

---

## 14. Suggested repository evolution

### Phase 1: Stabilise current package boundaries

Within the existing repository:

- keep lazy imports,
- keep import-boundary tests,
- document optional dependencies,
- make ProbeFlow optional and guarded,
- prevent GUI startup from requiring analysis packages,
- make `scanflow.core` independent of PySide6.

This phase is mostly already underway.

### Phase 2: Introduce contracts

Add a small contracts module:

```text
scanflow/contracts/
  scan_record.py
  analysis_result.py
  proposed_action.py
  validation.py
  coordinates.py
```

Use these in sidecars, analysis output, and GUI suggestion panels.

### Phase 3: Extract Qt-free control execution

Separate:

```text
BlockingRecipeRunner
QtAutomationRunner
```

The CLI uses `BlockingRecipeRunner`.

The GUI uses `QtAutomationRunner`.

Both use the same core executors.

### Phase 4: Move analysis to its own namespace

Move or wrap:

```text
feature_discovery
grouping
preview analysis
ProbeFlow adapters
```

under:

```text
scanflow.analysis
```

or a separate package:

```text
scanflow-analysis
```

### Phase 5: Move ML to its own package

Move all learned-model logic into:

```text
scanflow-ml
```

This package should have no hardware imports.

### Phase 6: Add plugin discovery

Allow optional panels and analyzers to register themselves.

Examples:

```text
scanflow-analysis registers:
  PreviewPanel
  FeatureDetector

scanflow-ml registers:
  MLSuggestionPanel
  TorchMoleculeDetector
```

The GUI loads available plugins, but does not require them.

### Phase 7: Formalise action approval

Add a clear operator approval path:

```text
Analysis result
  → proposed action
  → validation
  → human approval
  → execution
```

For high-trust bounded automation, allow pre-approved policies, but make the policy explicit and logged.

---

## 15. Immediate practical recommendations

### 15.1 Do not merge ML deeper into ScanFlow control

Avoid patterns such as:

```python
from scanflow.core import STMClient
from scanflow.ml import classify_current_image

if classify_current_image(...) == "interesting":
    stm.scan.set_offset_nm(...)
```

This is the architecture to avoid.

### 15.2 Create an `ActionProposal` path now

Even before the package split, create a simple internal object for suggested follow-up actions.

This prevents direct calls from preview/analysis panels into motion routines.

### 15.3 Require all motion from analysis to pass through `TipMotionManager`

If analysis suggests a target, it must be converted into a proposed action and then validated. It should not call low-level offset code.

### 15.4 Keep sidecars central

Sidecars are the natural bridge between acquisition and analysis. Strengthen them rather than inventing ad hoc paths between ML and control.

### 15.5 Add import-boundary tests to every PR

Any future PR that adds analysis/ML features should prove that:

```text
simple sweep path remains analysis-free
core remains GUI-free
ML remains hardware-free
GUI starts without optional analysis packages
```

---

## 16. Example future workflow

A mature ScanFlow workflow might look like this:

```text
1. Operator opens ScanFlow.
2. ScanFlow connects to STMAFM.
3. Operator runs a wide survey scan.
4. ScanFlow writes `.dat` and `.scanflow.json`.
5. Analysis plugin detects candidate molecules.
6. ML plugin classifies features as monomer/dimer/cluster/defect.
7. GUI displays candidates and confidence.
8. Planner suggests:
     - zoom top 10 monomers,
     - run dI/dV on 3 representative dimers,
     - ignore low-confidence edge features.
9. Operator accepts or edits the proposal.
10. Control core validates every step.
11. ScanFlow executes only the validated recipe.
12. Every scan and decision is logged.
```

This is powerful but safe because the model never directly controls the microscope.

---

## 17. Review questions for future development

Before accepting any new analysis or ML feature, ask:

1. Does it import any control or CreaTec module?
2. Can the GUI still start without it?
3. Can a simple bias sweep still run without it?
4. Does it produce structured results rather than directly commanding the STM?
5. Are proposed follow-up actions validated by the control core?
6. Are coordinate systems explicit?
7. Are units explicit?
8. Is the model/algorithm version recorded?
9. Are confidence values recorded?
10. Can the operator override or reject the suggestion?
11. Is the behaviour logged?
12. Can it be tested on saved scans without a live STM?

If the answer to any of the first four questions is no, the feature is probably in the wrong layer.

---

## 18. Bottom line

The long-term goal should be:

> **ScanFlow is the safe control suite. Analysis and ML are replaceable advisors.**

The control suite should be clean, easy to install, and reliable. It should be able to run basic STM workflows without any image-analysis or ML stack.

Analysis and ML should feed into the suite through stable contracts:

```text
ScanRecord → AnalysisResult → ProposedAction → ValidationResult → ValidatedAction
```

This gives the lab freedom to improve ML methods over time without rewriting the control program or weakening instrument safety.

The practical design standard is:

```text
Changing the ML model should not require changes to the CreaTec driver.
Changing the segmentation algorithm should not require changes to the safety monitor.
Changing the GUI preview panel should not affect the CLI sweep runner.
Changing the recipe runner should not require Torch, OpenCV, or ProbeFlow.
```

If those statements remain true, the architecture is healthy.
