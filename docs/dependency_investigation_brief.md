# ScanFlow Dependency and Architecture Investigation

**Repository:** `SPMQT-Lab/ScanFlow`  
**Focus:** whether analysis and ML dependencies are incorrectly coupled to simple instrument-control actions such as bias/current sweeps.  
**Motivation:** the package was developed by a student and appears to work, but working code can still have an inefficient or fragile architecture. This review should distinguish practical functionality from good software boundaries for an instrument-control program.

---

## 1. Executive summary

The current package structure should be reviewed carefully because ScanFlow appears to mix three distinct concerns:

1. **Instrument control**
   - Connect to CreaTec/STMAFM.
   - Set scan parameters.
   - Start/stop/save scans.
   - Run bias/current sweeps.
   - Monitor current and abort safely.
   - Write sidecar metadata.

2. **Interactive GUI operation**
   - PySide6 panels.
   - Sweep setup.
   - Logs.
   - Instrument status.
   - Survey/mosaic/preview controls.

3. **Analysis and ML-like workflows**
   - ProbeFlow-backed preview.
   - Feature discovery.
   - Segmentation.
   - Particle classification.
   - OpenCV / scikit-image / possible CLIP/Torch-related workflows.

These concerns should not have the same dependency footprint. A simple bias sweep should not require OpenCV, scikit-image, Torch, CLIP, scikit-learn, or ProbeFlow analysis modules. If those packages are useful for survey or preview workflows, they should be optional extras and imported only when those features are used.

The review should not assume that the current package layout is correct just because the program launches or works on one development machine. In lab software, an oversized dependency graph can create real operational problems: hard-to-reproduce installations, slow startup, DLL conflicts, brittle lab-PC deployment, and unnecessary coupling between instrument safety code and analysis experiments.

---

## 2. Current concern

The present `pyproject.toml` declares the following as core dependencies:

```toml
dependencies = [
    "PySide6>=6.6",
    "matplotlib>=3.9,<4",
    "numpy>=1.26,<3",
    "openai-clip>=1.0",
    "opencv-python>=4.10,<5",
    "pillow>=10.2,<12",
    "scipy>=1.13,<2",
    "torch>=2.7,<3",
    "torchvision>=0.22,<1",
    "scikit-learn>=1.6,<2",
    "scikit-image>=0.21,<1",
    "pyqtgraph>=0.13,<1",
    "pyyaml>=6.0,<7",
]
```

Only `pywin32` is currently separated into an optional `createc` extra.

This means that a user who only wants to run:

```bash
python -m scanflow bias --start -1.0 --end 1.0 --step 0.01 --setpoint 50
```

is still expected to install heavy image-analysis and ML packages. That is a red flag.

The issue is not that analysis tools are bad. Preview, segmentation, classification, survey, and mosaic tools may be valuable. The issue is that they should not be part of the minimal dependency path for safe instrument control.

---

## 3. Evidence to collect

The investigation should collect evidence in two categories.

### 3.1 Dependency declaration evidence

Inspect:

```text
pyproject.toml
```

Record which packages are mandatory and which are optional. Then classify each dependency:

| Package | Current status | Likely role | Should simple sweep need it? |
|---|---:|---|---:|
| `numpy` | mandatory | arrays, mock data, simple calculations | maybe |
| `pyyaml` | mandatory | recipes | yes |
| `PySide6` | mandatory | GUI and current QThread runner | GUI yes, CLI ideally no |
| `pyqtgraph` | mandatory | GUI plotting/live display | no for CLI sweep |
| `matplotlib` | mandatory | previews/plots | no |
| `opencv-python` | mandatory | preview/segmentation | no |
| `pillow` | mandatory | image/screenshot tools | no |
| `scipy` | mandatory | analysis/tracking support | not for sweep |
| `scikit-image` | mandatory | feature discovery/segmentation | no |
| `scikit-learn` | mandatory | classification/ML support | no |
| `torch` | mandatory | ML/classification | no |
| `torchvision` | mandatory | ML/classification | no |
| `openai-clip` | mandatory | CLIP-like classification | no |
| `pywin32` | optional | live CreaTec COM | yes for real STM, no for mock |

This table should be converted into actual packaging changes.

### 3.2 Import-path evidence

The review should determine not only what is installed, but what gets imported during each action.

Check import paths for these commands:

```bash
python -X importtime -c "import scanflow"
python -X importtime -c "import scanflow.cli"
python -X importtime -m scanflow estimate bias --start -1 --end 1 --step 0.1
python -X importtime -m scanflow bias --start -1 --end 1 --step 0.1 --setpoint 50 --mock --yes
python -X importtime -c "from scanflow.gui.app import main"
```

The result should be inspected for imports of:

```text
torch
torchvision
clip
sklearn
skimage
cv2
PIL
matplotlib
probeflow
PySide6
pyqtgraph
```

A simple estimate command should not import any of the ML or image-analysis stack. A headless sweep should ideally not require GUI plotting libraries either.

---

## 4. Current coupling paths to investigate

### 4.1 CLI sweep path

The simple sweep path starts from:

```text
scanflow/__main__.py
scanflow/cli.py
scanflow/automation/__init__.py
scanflow/automation/recipe.py
scanflow/automation/runner.py
scanflow/core/*
```

Important review question:

> Does importing the CLI bring in survey, mosaic, feature discovery, GUI, preview, or analysis packages?

The current `scanflow/automation/__init__.py` imports not only recipe classes, but also survey, mosaic, and feature discovery symbols. This is convenient, but it means `from scanflow.automation import MeasurementRecipe` is not as minimal as it looks.

This should be tested, not guessed.

Possible improvement:

```python
# Better for simple CLI paths
from scanflow.automation.recipe import MeasurementRecipe
from scanflow.automation.runner import AutomationRunner
```

or split public APIs:

```text
scanflow.automation.recipe
scanflow.automation.runner
scanflow.automation.survey
scanflow.automation.mosaic
scanflow.automation.feature_discovery
```

and keep `scanflow.automation.__init__` very small.

### 4.2 GUI startup path

The current main window imports many panels at startup. The most concerning is the preview panel, because the preview panel imports ProbeFlow analysis kernels and classification functions.

The current path to investigate is:

```text
scanflow/gui/main_window.py
    imports PreviewWindow
        imports probeflow.analysis.preview
        imports probeflow.analysis.helpers
        imports probeflow.analysis.features
        imports probeflow.core.scan_loader
        imports probeflow.processing.geometry
```

This means that simply launching the ScanFlow GUI may require the ProbeFlow analysis stack, even if the user only wants the Sweep tab.

This should be changed. Preview and classification should be lazy imports.

Possible target:

```python
def _show_preview_window(self) -> None:
    from scanflow.gui.panels.preview_panel import PreviewWindow
    if self._preview_window is None:
        self._preview_window = PreviewWindow(self._stm, self)
    self._preview_window.show_window()
```

In addition, the Preview button should display a helpful message if optional analysis dependencies are not installed:

```text
Preview tools require ScanFlow analysis dependencies.
Install with: pip install -e ".[analysis]"
```

### 4.3 Runner path

`AutomationRunner` currently subclasses `QThread`, so even command-line execution uses a Qt event loop. This may be acceptable temporarily, but architecturally it means CLI operation is not truly independent from the GUI stack.

Investigate whether a simple blocking runner can exist without Qt:

```text
scanflow.automation.blocking_runner
scanflow.automation.qt_runner
```

Target model:

```text
BlockingRecipeRunner
    no PySide6
    no GUI
    suitable for CLI and tests

QtAutomationRunner
    wraps BlockingRecipeRunner
    converts callbacks to Qt signals
```

This separation would allow CLI sweeps and tests to run without PySide6.

---

## 5. What a minimal sweep should require

A bias/current sweep should need only:

```text
Python standard library
numpy                  # optional but acceptable
pyyaml                 # recipe loading/saving
scanflow.core          # STM client, scan, feedback, safety
scanflow.automation    # recipe + simple runner
pywin32                # only for live CreaTec COM on Windows
```

A simple sweep should not require:

```text
torch
torchvision
openai-clip
scikit-learn
scikit-image
opencv-python
Pillow
matplotlib
ProbeFlow
pyqtgraph
```

PySide6 should be required for the GUI, but not necessarily for a CLI-only sweep.

This gives a clean pass/fail test:

```bash
# In a minimal environment:
pip install -e ".[createc]"
python -m scanflow estimate bias --start -1 --end 1 --step 0.1
python -m scanflow bias --start -1 --end 1 --step 0.1 --setpoint 50 --mock --yes
```

The above should succeed without analysis or ML packages.

---

## 6. Proposed package split

### 6.1 Recommended optional extras

Use optional extras to make the package reflect the actual feature layers.

```toml
[project]
dependencies = [
    "numpy>=1.26,<3",
    "pyyaml>=6.0,<7",
]

[project.optional-dependencies]
gui = [
    "PySide6>=6.6",
    "pyqtgraph>=0.13,<1",
]

createc = [
    "pywin32>=306",
]

analysis = [
    "matplotlib>=3.9,<4",
    "opencv-python>=4.10,<5",
    "pillow>=10.2,<12",
    "scipy>=1.13,<2",
    "scikit-image>=0.21,<1",
]

ml = [
    "torch>=2.7,<3",
    "torchvision>=0.22,<1",
    "openai-clip>=1.0",
    "scikit-learn>=1.6,<2",
]

dev = [
    "pytest",
    "pytest-qt",
    "ruff",
]
```

If a combined `full` extra is desired, it can be documented in a `requirements-full.txt` or implemented according to the packaging backend's supported syntax.

### 6.2 Installation profiles

Use explicit installation profiles:

#### Lab PC: simple instrument control

```bash
pip install -e ".[gui,createc]"
```

or, for a pure CLI station:

```bash
pip install -e ".[createc]"
```

#### Development PC with analysis

```bash
pip install -e ".[gui,analysis,dev]"
```

#### Full development environment

```bash
pip install -e ".[gui,createc,analysis,ml,dev]"
```

#### Offline mock-only development

```bash
pip install -e ".[gui,analysis,dev]"
python -m scanflow
```

### 6.3 Guiding rule

> Optional feature, optional dependency.

If a module needs OpenCV, scikit-image, ProbeFlow, Torch, or CLIP, it must either live behind an optional extra or import those packages lazily with a clear error message.

---

## 7. Code refactor plan

### 7.1 Split public imports

Avoid broad imports in `__init__.py` files. They make innocent-looking imports heavy.

Current pattern to avoid:

```python
from scanflow.automation import MeasurementRecipe
```

if `scanflow.automation.__init__` imports survey, mosaic, and feature discovery.

Better pattern:

```python
from scanflow.automation.recipe import MeasurementRecipe
```

or keep `scanflow.automation.__init__` limited to truly core symbols only.

Suggested `scanflow/automation/__init__.py` target:

```python
from .recipe import MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep

__all__ = [
    "MeasurementRecipe",
    "ScanStep",
    "SpectroscopyStep",
    "ApproachStep",
    "WaitStep",
]
```

Survey, mosaic, feature discovery, and tip-forming can be imported from their explicit modules.

### 7.2 Lazy-load analysis panels

The GUI should not import preview/classification tools at startup.

Target:

```text
MainWindow startup:
  Sweep panel
  Log panel
  core status panels

Only on demand:
  PreviewWindow
  Survey panel
  Mosaic panel
  classification tools
```

If Survey/Mosaic are considered core GUI tabs, still ensure their analysis dependencies are lazy.

### 7.3 Split runner from Qt

Target structure:

```text
scanflow/automation/
├── recipe.py
├── blocking_runner.py       # no Qt
├── qt_runner.py             # QThread wrapper
├── executors/
│   ├── scan_executor.py
│   ├── spectroscopy_executor.py
│   ├── survey_executor.py
│   ├── mosaic_executor.py
│   └── tipform_executor.py
└── analysis/
    ├── feature_discovery.py
    └── grouping.py
```

A simple sweep should be executable through `blocking_runner.py`.

The GUI can use `qt_runner.py`, which wraps the same core execution engine and emits Qt signals.

### 7.4 Move preview and ProbeFlow integration into an explicit boundary

Target structure:

```text
scanflow/integration/probeflow_preview.py
scanflow/gui/panels/preview_panel.py
```

The preview panel may depend on ProbeFlow, but importing the rest of ScanFlow should not.

Add a helper:

```python
def require_analysis_dependencies():
    try:
        import cv2
        import skimage
        import probeflow
    except ImportError as exc:
        raise RuntimeError(
            "Preview and feature analysis require the analysis extras. "
            "Install with: pip install -e '.[analysis]'"
        ) from exc
```

### 7.5 Move ML/classification into a separate optional module

Anything that uses CLIP, Torch, torchvision, or scikit-learn should live in a module with an explicit name:

```text
scanflow/ml/
scanflow/integration/probeflow_classification.py
```

No instrument-control module should import it.

---

## 8. Investigation workflow

### Step 1: Static dependency inventory

Run:

```bash
python - <<'PY'
import ast
from pathlib import Path

root = Path("scanflow")
for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".")[0])
    heavy = sorted(set(imports) & {
        "torch", "torchvision", "clip", "sklearn", "skimage",
        "cv2", "PIL", "matplotlib", "scipy", "probeflow",
        "PySide6", "pyqtgraph"
    })
    if heavy:
        print(path, "->", ", ".join(heavy))
PY
```

Record every heavy import.

### Step 2: Runtime import timing

Run:

```bash
python -X importtime -c "import scanflow" 2> import_scanflow.txt
python -X importtime -c "import scanflow.cli" 2> import_cli.txt
python -X importtime -m scanflow estimate bias --start -1 --end 1 --step 0.1 2> import_estimate.txt
python -X importtime -c "from scanflow.gui.app import main" 2> import_gui.txt
```

Then search:

```bash
grep -E "torch|torchvision|clip|sklearn|skimage|cv2|PIL|matplotlib|probeflow|PySide6|pyqtgraph" import_*.txt
```

This reveals what is imported, not merely installed.

### Step 3: Minimal environment test

Create a fresh environment without analysis packages.

```bash
python -m venv .venv-min
.venv-min\Scripts\activate
pip install -e . --no-deps
pip install numpy pyyaml
python -c "from scanflow.automation.recipe import MeasurementRecipe; print('recipe ok')"
python -m scanflow estimate bias --start -1 --end 1 --step 0.1
```

This test will likely fail before the refactor, but the failure is useful. It shows where hidden dependencies enter.

### Step 4: GUI minimal test

After optional extras are introduced:

```bash
python -m venv .venv-gui
.venv-gui\Scripts\activate
pip install -e ".[gui]"
python -m scanflow
```

Expected:

- GUI opens.
- Sweep and Log panels work in mock mode.
- Preview/analysis tools are disabled or show an install message.
- No import error for Torch, CLIP, OpenCV, scikit-image, or ProbeFlow.

### Step 5: Analysis-enabled test

```bash
python -m venv .venv-analysis
.venv-analysis\Scripts\activate
pip install -e ".[gui,analysis]"
python -m scanflow
```

Expected:

- Preview panel works.
- Feature discovery works.
- Survey/mosaic analysis tools work if they do not require ML.

### Step 6: ML-enabled test

```bash
python -m venv .venv-ml
.venv-ml\Scripts\activate
pip install -e ".[gui,analysis,ml]"
python -m scanflow
```

Expected:

- Classification tools work.
- Torch/CLIP are imported only when classification is used.

---

## 9. Acceptance criteria

### 9.1 For a simple sweep

A simple bias/current sweep passes the architectural test if:

- It can be estimated without PySide6, OpenCV, scikit-image, Torch, CLIP, scikit-learn, ProbeFlow, matplotlib, or Pillow.
- It can run in mock mode without analysis/ML dependencies.
- It can run live with only the CreaTec extra added.
- It does not import preview, classification, survey, or mosaic modules unless requested.
- It uses the safety monitor and 0 V guard.
- It writes sidecar metadata.

### 9.2 For GUI startup

The GUI passes if:

- It can open in a minimal GUI environment.
- Sweep and Log work without analysis extras.
- Preview tools are disabled or lazy-loaded if analysis extras are absent.
- Error messages tell the user exactly which extra to install.
- No Torch/CLIP import occurs at startup.

### 9.3 For analysis tools

Analysis tools pass if:

- They are available when `analysis` extras are installed.
- They fail gracefully when missing.
- They do not change the safety or motion behaviour of core instrument routines.
- Their dependencies are documented.

### 9.4 For ML/classification tools

ML tools pass if:

- They are isolated behind an `ml` extra.
- They are never imported during basic GUI startup.
- They are never imported during simple sweep CLI use.
- They are optional and do not affect lab-PC deployment.

---

## 10. Risks of leaving the structure unchanged

Leaving the package as-is has several risks:

### 10.1 Lab PC fragility

Heavy binary packages can be difficult to install or update on a microscope PC, especially if the machine has limited internet access, older drivers, older Python, or strict permissions.

### 10.2 Version conflicts

Torch, OpenCV, PySide6, and scientific Python packages can impose constraints that are unrelated to CreaTec control. A conflict in an image-analysis package should not prevent the STM control software from launching.

### 10.3 Slow startup and hidden side effects

Large packages may slow imports, load DLLs, inspect GPU/CPU backends, or alter runtime state. This is undesirable for software used next to an instrument.

### 10.4 Harder debugging

If a simple sweep fails because of a missing or incompatible analysis dependency, students may waste time debugging the wrong layer.

### 10.5 Weak architectural discipline

Once unrelated dependencies coexist in the core path, it becomes easier for future code to casually import analysis modules into safety-critical instrument-control routines.

### 10.6 Unclear responsibility boundaries

Instrument control, image analysis, and ML classification have different failure modes. They should be tested and reviewed separately.

---

## 11. How to discuss this with the student

The tone should be constructive. The key point is that this is a normal stage in research software development: the package grows quickly, features are added, and then the architecture needs a cleanup pass.

Suggested framing:

```text
The program already does many useful things, and the mock instrument and safety logic are good signs. The next step is to separate the package into clear layers so that basic STM control remains small, reliable, and easy to deploy. Analysis and ML tools can remain part of the ecosystem, but they should be optional modules rather than mandatory dependencies for a simple sweep.
```

Avoid framing this as “the student did it wrong.” A better framing is:

```text
The current structure is understandable for rapid development, but it is not the structure we should accept for routine instrument use.
```

---

## 12. Recommended immediate actions

### Action 1: Dependency audit

Create a branch:

```bash
git checkout -b audit/dependency-boundaries
```

Add a document:

```text
docs/dependency_architecture.md
```

and record:

- each dependency
- why it is needed
- which module imports it
- which user-facing feature needs it
- whether it belongs in core, gui, analysis, ml, createc, or dev

### Action 2: Import graph script

Add a small script:

```text
tools/dev/import_audit.py
```

It should list heavy imports by file. This can be run in future PRs.

### Action 3: Move heavy dependencies to extras

Edit `pyproject.toml` so that only truly core dependencies remain mandatory.

### Action 4: Fix `automation.__init__`

Make the automation package import only core recipe types by default. Move survey/mosaic/feature-discovery exports to explicit imports.

### Action 5: Lazy-load PreviewWindow

Change `main_window.py` so PreviewWindow and ProbeFlow-backed modules are imported only when the user opens preview tools.

### Action 6: Separate Qt runner and blocking runner

Make simple CLI execution independent of PySide6 if feasible.

### Action 7: Add CI-style smoke tests

Add tests that prove the boundaries:

```text
test_core_import_without_gui
test_recipe_import_without_analysis
test_cli_estimate_without_analysis
test_gui_import_without_ml
test_preview_requires_analysis_extra
test_classification_requires_ml_extra
```

---

## 13. Suggested final architecture

A healthy final structure would look like this:

```text
scanflow/
├── core/
│   ├── stm_client.py
│   ├── scan.py
│   ├── feedback.py
│   ├── safety.py
│   ├── motion.py
│   └── mock_dispatch.py
│
├── automation/
│   ├── recipe.py
│   ├── blocking_runner.py
│   ├── qt_runner.py
│   └── executors/
│       ├── scan.py
│       ├── spectroscopy.py
│       ├── approach.py
│       └── tipform.py
│
├── analysis/
│   ├── feature_discovery.py
│   ├── grouping.py
│   └── previews.py
│
├── ml/
│   └── classification.py
│
├── integration/
│   └── probeflow.py
│
├── gui/
│   ├── app.py
│   ├── main_window.py
│   └── panels/
│       ├── sweep_panel.py
│       ├── log_panel.py
│       ├── preview_panel.py
│       ├── survey_panel.py
│       └── mosaic_panel.py
│
└── io/
    ├── sidecar.py
    └── acquisition_log.py
```

The dependency rule should be:

```text
core        -> stdlib, numpy if needed
automation  -> core, pyyaml, no GUI unless qt_runner
gui         -> PySide6, pyqtgraph
analysis    -> scipy, matplotlib, OpenCV, scikit-image, ProbeFlow bridge
ml          -> torch, torchvision, CLIP, scikit-learn
createc     -> pywin32
```

No arrows should point from `core` into `analysis`, `ml`, or `gui`.

---

## 14. Bottom line

The current structure should be treated as a successful prototype rather than a final architecture. It may work, but it appears to make too many packages mandatory and allows analysis/preview functionality to sit too close to basic instrument-control paths.

The standard should be simple:

> A user should be able to install and run a safe bias/current sweep on the STM without installing any image-analysis or ML stack.

If that is not true, the package boundaries need to be corrected before ScanFlow becomes a routine lab tool.
