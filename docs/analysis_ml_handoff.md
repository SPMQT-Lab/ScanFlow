# Plugging analysis or ML into ScanFlow

How to add a feature detector, classifier, or planner — without ever
touching the instrument. The worked, executable example of everything
below is `tests/test_analysis_handoff.py`.

## The chain

```
ScanFlow acquires a scan
    -> .dat + <scan>.scanflow.json sidecar        (= contracts.ScanRecord)
your detector reads the image + ScanRecord
    -> contracts.AnalysisResult                   (features in nm + frame)
    -> <scan>.analysis.json                       (atomic, next to the scan)
a planner reads the AnalysisResult
    -> contracts.ProposedAction(s)                (suggestions, inert)
ScanFlow's control core validates
    -> ValidationResult -> operator approval -> ValidatedAction
only then does anything execute, through TipMotionManager
```

Your code occupies the middle three rows. It can run in a completely
separate Python environment: the only ScanFlow import you need is
`scanflow.contracts`, which is stdlib-only by enforced rule.

## Writing a detector

Implement the `FeatureDetector` interface
(`scanflow/analysis/detectors.py`):

```python
class MyMoleculeDetector:
    name = "my_molecule_cnn"
    version = "0.3"          # ALWAYS version your model/algorithm

    def detect(self, image, nm_per_px, *, input_scan_id="") -> AnalysisResult:
        ...
```

Requirements (the contracts enforce most of them at construction):

1. **Physical units + explicit frame.** Feature positions are nm in a
   named coordinate frame — use `IMAGE_CENTER_RELATIVE_NM` (dx/dy from
   the image centre, +y downward). A `Feature` without a valid frame
   raises. Never emit pixel positions across the boundary: validation
   rejects pixel-frame targets.
2. **Provenance.** `algorithm`, `algorithm_version`, and per-feature
   `source`/`source_version` are how an overnight run's decisions are
   reconstructed afterwards.
3. **Honest confidence.** `confidence` is optional; only set it if it is
   calibrated (0–1). The threshold detector sets `None` deliberately.
4. **Fail empty, never hallucinate.** Found nothing? Return zero
   features and a warning string. A false positive moves the tip to
   noise.
5. **No hardware/control imports.** Your package must not import
   `scanflow.core`, `scanflow.automation`, or Qt (boundary tests fail
   the build if `scanflow.analysis` does; hold your package to the same
   rule). Heavy deps (torch/CLIP/OpenCV) stay in your package or the
   `[ml]`/`[analysis]` extras.

Before any rig use, run your detector against the synthetic tracking
scenes (`tests/synthetic_surfaces.py`) and compare with the baselines in
`tests/test_tracking_dataset.py` — ten scenes with known ground truth,
including the documented hard cases (low contrast, step edges, inverted
polarity, partial frames).

## Files on disk

* Read scans: the `.dat` plus `ScanRecord.from_payload(json.loads(
  sidecar))` from `<scan>.scanflow.json` — don't parse sidecar JSON
  ad hoc.
* Write results: `scanflow.io.analysis_artifacts.write_analysis_result`
  → `<scan>.analysis.json` (atomic). If you can't import scanflow.io in
  your environment, write `AnalysisResult.to_payload()` as JSON to the
  same path yourself — atomically (temp file + rename).

## Proposing actions

Use or imitate `ZoomFeaturesPlanner` (`scanflow/analysis/planners.py`):
emit `ProposedAction` objects with concrete `bias_V`/`setpoint_A`
(resolve "inherit current" yourself — the validator refuses unresolved
proposals), `kind` from `contracts.ACTION_KINDS`, and
`requires_operator_confirmation=True` unless a written, logged policy
says otherwise. Proposals are inert: the control core
(`scanflow.automation.proposals`) validates them, approval clears the
confirmation, and only a `ValidatedAction` can become recipe steps.

What you may NOT do — the one absolute rule
(docs/long_term_architecture.md §1): analysis/ML never imports
`STMClient`, never calls `set_offset_nm`/`setp`, never starts scans.
If you find yourself wanting to, you are writing a `ProposedAction`.

## Checklist for review (from long_term_architecture §17)

1. No control/CreaTec imports? 2. GUI starts without it? 3. Simple sweep
runs without it? 4. Emits structured results, not commands? 5. Proposals
validated by the control core? 6. Frames explicit? 7. Units explicit?
8. Version recorded? 9. Confidence honest? 10. Operator can reject?
11. Logged? 12. Testable on saved scans?
