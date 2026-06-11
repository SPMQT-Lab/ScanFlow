# ScanFlow Code Review — 2026-06-10

**Scope:** full pass over `scanflow/` (~15k lines), tests, docs, and packaging, following
the review brief (archived: `docs/archive/2026-06_review_plan.md`). Reviewed statically plus mock-mode test runs
(`pytest`: 120 passed; 2 failures were a missing `pyqtgraph` in the review
environment, not code defects). No live-rig validation was possible — items
marked **[needs rig validation]** must be checked on the CreaTec before the
next unattended run.

> **Status update (2026-06-10, same day):** the following findings were fixed
> in this working copy and are covered by new tests (suite now 136 passing):
> **B1** (mosaic clamp — tile targets moved to `mosaic.tile_targets_nm()`
> using the `scan_geometry` clamp, warning emitted if a clamp fires),
> **B3** (safety monitor now fails closed after consecutive readback
> failures, with an early warning), **B4** (atom tracker routes every move
> through `TipMotionManager`, returns to reference on failure, unbinds COM
> in `finally`), **H1** (safety polled during waits/settles; approach wait
> is stop-responsive), **H3** (runner control flags reset per run),
> **H4** (`stop_on_error` implemented; safety/disconnect always fatal),
> **H7** (GUI status poll throttled to one detail read per 15 s; was ~10
> COM reads per 3 s), **H7-adjacent / STMAFM lag** (the runner pulled a
> full DATA.SCAN frame every 0.5 s safety poll and dropped it — no GUI
> code consumed `live_frame`; frame pulls are now opt-in via
> `enable_live_frames()` with conflated latest-frame-wins delivery, and
> the safety monitor's full-frame fallback read is cached for 1.5 s —
> every COM call executes on STMAFM's GUI thread, so this was the main
> source of controller-window sluggishness during automation),
> **U1** (README rewritten to match the current
> program), **U2** (torch/CLIP/OpenCV/scikit-learn moved to extras;
> missing `python-pptx` dependency declared), **U3** (`recipe.validate()`
> added and wired into the CLI), **U4** (survey manifests written
> atomically), **U6** (stale/contradictory comments corrected).
>
> **Dependency-boundary pass (2026-06-10, later same day):** following the
> dependency/architecture investigation brief: `scanflow.core` is now
> Qt-free (Qt helpers lazy via PEP 562 in `core/__init__.py`), the CLI
> imports Qt only when a run actually executes (`estimate` runs without
> PySide6), and GUI startup no longer requires ProbeFlow — `PreviewWindow`
> is lazy-loaded by the Preview button with an actionable install message.
> Boundaries are documented in `docs/dependency_architecture.md` and
> enforced by `tests/test_import_boundaries.py` + `tools/dev/import_audit.py`.
> The Qt-free blocking runner and a `[gui]` packaging extra are deferred
> into the H2 executor extraction (see the doc's *Deferred* section).
>
> **NaN / bad-readback robustness pass (2026-06-11):** audit of the data
> paths for the historical "NaN crashes the program / freezes the
> controller" failures. Found and fixed: a NaN ADC reading passed the
> safety check as `ok=True` **and reset the fail-closed counter** (NaN
> compares False against any threshold) — now counted as a failed
> reading; the plane fit (`_level_correct`) fitted through NaNs —
> silently zeroing ALL detections on macOS LAPACK and raising "SVD did
> not converge" on Windows (the likely historical crash) — now fitted on
> finite pixels only, with median fill before thresholding so
> partial-NaN frames still yield valid-region detections; phase
> correlation could return `ok=True` with garbage on NaN frames — now
> sanitised and refuses non-finite results; `''`/garbage parameter
> readbacks from a busy STMAFM crashed `scan.read()` mid-automation —
> now parsed defensively with logged defaults; one NaN Z sample poisoned
> the Z-stability statistics — now skipped; temperature refill-event
> list bounded. `scan_metrics` and `events.py` audited clean (already
> NaN-safe / bounded). Coverage: tests/test_nan_robustness.py.
>
> **H5 closed (2026-06-11):** tip-form arming now has its GUI and CLI
> paths — a Tip Form tab (zero pollers) with a typed-`ARM` confirmation
> dialog that calls `approve_next_tip_form()` for exactly one pulse, and
> `scanflow run --arm-tip-form` for recipes. Both surface a motion
> pre-flight (`core.tipform.assess_tip_form_motion`) for the Createc
> quirk where the tip travels at TIP-FORM.LATSPEED rather than the scan
> speed (fast jump / uninterruptible crawl); the runner logs the same
> assessment to the acquisition log before each pulse, and
> `recipe.validate()` flags static speed mismatches.
>
> Still open: **B2 [needs rig validation]** (survey vs mosaic frame-resize
> convention — one manual experiment required, then unify on
> `scan_geometry` and upgrade the mock, see U5; flagged in code with
> `FIXME(B2-frame-resize)` markers in runner.py / scan_geometry.py /
> mock_dispatch.py, guarded by `tests/test_open_findings.py`), **H2** (executor
> extraction), **H6** (remaining inline
> survey coordinate math — blocked on B2), **U5**, **U8**, **U9**.

## Answers to the four review questions

1. **Architecture maintainable?** Mostly yes at the core layer (`STMClient`,
   controllers, `TipMotionManager`, `scan_geometry` are clean and well
   documented). The automation layer is not: `AutomationRunner` is a 1,500-line
   QThread that owns survey and mosaic science; `executors.py` is an
   acknowledged but empty refactor.
2. **COM handled safely?** The thread-binding design is sound and consistently
   applied in workers (`bind_thread`/`unbind_thread`, action keys excluded from
   retry, careful stop/teardown sequencing). Two gaps: the atom tracker can
   leak its COM apartment on unexpected exceptions, and GUI-thread timers do
   non-trivial COM traffic during active automation.
3. **Safe enough for unattended operation?** Not yet. Two positioning defects
   (one verified numerically, one strongly suspected), a fail-open safety
   monitor, and an unguarded atom tracker are blockers below.
4. **Docs/tests/mock/sidecars support reproducible use?** Sidecars and the
   acquisition log are genuinely good and should be protected. The mock is
   excellent for workflow testing but too simple to catch coordinate bugs. The
   README describes a program that no longer exists.

---

## Blockers

### Finding B1: Mosaic bottom-row tiles are silently mispositioned by the Y excursion clamp

**Severity:** blocker (silent data corruption)

**Area:** motion / mosaic — `scanflow/automation/runner.py:1051-1056`

**Evidence:** `dy_nm` is computed in the **top-edge** convention
(`(target_y_px − wpy/(2·grid_n)) · nm_per_px`, correct), but then clamped to
`±wide_size/2` — a **centre**-convention bound. For the default 90 nm / 3×3
mosaic, row-2 tiles need `dy = 60 nm` but `60 > 45` so all three are clamped
to 45 nm. Verified numerically against the actual code:

```
tile 7: intended top-edge dy=60.0 → actual 45.0, spans [45, 75]  CLAMPED
tile 8: intended top-edge dy=60.0 → actual 45.0, spans [45, 75]  CLAMPED
tile 9: intended top-edge dy=60.0 → actual 45.0, spans [45, 75]  CLAMPED
```

This happens for **every** 3×3 mosaic regardless of size (2w/3 > w/2 always).
For 5×5, rows 3–4 (10 of 25 tiles) are wrong. The bottom row overlaps the
middle row by half a tile, the bottom strip of the field is never imaged, and
the saved `tile_07..09` files claim positions they don't have. No warning is
emitted because the readback verification checks against the *clamped* target.

**Why it matters:** overnight mosaic campaigns produce mislabeled, gap-ridden
data with no error in the log.

**Recommendation:** clamp Y in top-edge convention to `[0, wide_h − tile_h]`
(X clamp to `±(wide_w − tile_w)/2` is also tighter than the current one), or
better, route the computation through
`scan_geometry.clamp_frame_to_wide_bounds()`, which already implements both
conventions correctly. Log a warning whenever a clamp actually fires.

**Suggested tests:** unit test asserting tile top edges for 3×3 and 5×5 equal
`row · tile_h`; regression test that no clamp fires for an exactly-tiling grid.

---

### Finding B2: Survey feature centering omits the half-frame Y correction every other workflow applies

**Severity:** blocker until proven otherwise **[needs rig validation]**

**Area:** motion / survey — `scanflow/automation/runner.py:698-767` (`_do_feature_zoom`)

**Evidence:** `scan_geometry.py` documents that `SCAN.OFFSET.Y.NM` is the
**top edge** of the frame and that converting a feature position into a new,
smaller frame requires the `− frame_height/2` term. The preview follow-up
worker (`workers/preview_followup.py`), the group worker
(`workers/preview_groups.py`), and the mosaic path all apply it. The survey
path does not: it moves by the wide-pixel delta
(`move_relative_pixels(cand.cx − prev_x, …)`) with the wide frame applied,
then shrinks to the zoom frame, with no half-frame term anywhere.

These two schemes contradict each other:

* Survey is correct only if resizing the frame preserves the frame **centre**.
* Mosaic and the `scan_geometry` helpers are correct only if resizing
  preserves the **top edge**.

At most one is right on the real instrument. If the top-edge model is right
(as the module docstring insists, citing "at least three positioning bugs"),
survey zooms land `(wide_h − zoom_h)/2` too high — ~57 nm for the default
120 nm wide / 5 nm zoom, i.e. the feature isn't in the zoom frame at all. The
mock cannot catch this: its `setxyoffvolt` model has no frame-size dependence.

**Why it matters:** either the survey or the mosaic workflow mis-positions
zooms; both cannot be correct.

**Recommendation:** establish on the rig what STMAFM preserves on frame
resize (one manual experiment: set a frame over a visible feature, halve the
height, observe). Then make **all** workflows express targets through
`scan_geometry` helpers and delete the inline arithmetic. Encode the verified
behaviour in the mock so this class of bug becomes testable.

**Suggested tests:** mock-mode survey + mosaic runs against an upgraded mock
that models top-edge semantics; assert the zoomed feature lands at frame
centre.

---

### Finding B3: Safety monitor fails open when the current cannot be read

**Severity:** blocker

**Area:** safety — `scanflow/core/safety.py:101-107`

**Evidence:** `measure_current_A()` returns `None` if both the ADC read and
the live-data fallback fail; `check()` then returns `SafetyStatus(ok=True)`.
Nothing is logged, nothing is counted. A run whose current readback is broken
(stale proxy, COM fault, preamp key renamed) executes the entire overnight
recipe with tip-crash protection silently disabled, while the GUI's last
"Live |I|" value keeps implying protection is active.

**Why it matters:** the plan's invariant — "failed readback should stop
motion-dependent workflows rather than proceeding blindly" — is violated in
the single most safety-critical component.

**Recommendation:** track consecutive `None` readings in the runner. After N
consecutive failures (e.g. 5, ≈2.5 s at the default poll), emit a prominent
warning to the acquisition log and GUI; after a larger N or when
`safety_enable` is on for a live (non-mock) instrument, treat it as a safety
violation and stop. Also surface "safety reading unavailable" distinctly in
the GUI rather than leaving the last good value on screen.

**Suggested tests:** mock test where `getadcvalf` raises and `DATA.SCAN`
returns None — assert the run aborts (or warns, per chosen policy) rather
than completing silently.

---

### Finding B4: Atom tracker moves the tip outside every safety/policy layer

**Severity:** blocker (for use on the live rig)

**Area:** motion / tracking — `scanflow/core/atom_tracker.py`

**Evidence:**

* All five probe moves and the correction call `stm.scan.set_offset_nm()`
  directly — bypassing `TipMotionManager`: no scan-idle check (it can drag
  the frame while a scan is running), no current check before motion, no
  single-move limit on the probe excursions, no `MotionResult` logging in the
  acquisition log.
* On a probe failure mid-sequence the worker emits an error and returns —
  **without attempting to return the tip to the reference**. The tip is left
  at the failed probe position. (Plan §5.4 asks exactly this question; the
  answer is no.)
* `unbind_thread()` is called on each explicit exit path but not in a
  `finally:` — any unexpected exception (e.g. the 5-element unpack of
  `z_readings`) leaks the COM apartment.
* Correction clamping exists (good), and the feature-strength gate exists
  (good), but the gradient normalisation
  (`z_range = max(|gx|, |gy|, 1.0)`) means pure noise on a flat terrace with
  strength just above threshold still produces a full `gain·delta` step.

**Recommendation:** construct a `TipMotionManager` in the worker and route
every probe/correction/return move through it (`assert_safe_to_move` +
limits + logging). Wrap the body in `try/finally` with return-to-reference
in the cleanup and `unbind_thread()` in the `finally`. Log each probe and the
applied correction to the acquisition log, not just the GUI log.

**Suggested tests:** mock test that a probe exception still returns the tip
to the reference; test that tracking refuses to start while
`scan.is_running`; test correction ≤ clamp under adversarial Z readings.

---

## High-priority maintainability / safety issues

### Finding H1: No safety polling during waits, settles, spectroscopy, or approach

**Severity:** high

**Area:** safety / runner — `runner.py:1295-1303` (`_sleep_with_progress`),
`runner.py:476-501` (`_do_spec_step`), `runner.py:1280-1293` (`_do_approach_step`)

**Evidence:** `_check_safety()` runs only inside
`_wait_for_scan_with_live_emit()`. Wait steps (60 s default), settling
periods (5 s default, many per survey/mosaic), spectroscopy steps (10 s × N
positions, fire-and-forget), and approach waits (up to 600 s) perform no
current checks. A tip contact during a spectroscopy grid or a long wait is
undetected until the next scan starts.

**Recommendation:** fold a safety check into `_sleep_with_progress`'s 1 Hz
loop, and poll during `spec` and `approach` waits. This is a small change
with a large coverage gain.

### Finding H2: `AutomationRunner` owns the science; executors are empty placeholders

**Severity:** high (the plan's predicted finding — confirmed)

**Area:** architecture — `runner.py` (1,494 lines), `executors.py`

**Evidence:** thread lifecycle, signals, pause/stop, safety reaction, scan
execution, spectroscopy, approach, tip forming, the full survey algorithm
(~270 lines), the full mosaic algorithm (~300 lines), artifact/sidecar
writing, and even matplotlib preview rendering all live in one QThread class.
`ScanExecutor` exists and is tested, but `SurveyExecutor` / `MosaicExecutor`
are placeholders and the runner does not use `ScanExecutor` either —
duplicate scan/wait/save logic now exists in two places and has already
drifted (the executor lacks live-frame emission and safety checks).

**Recommendation:** finish the extraction before adding any new mode: move
survey and mosaic loops into Qt-free executors operating on
`ExecutorContext`, make the runner consume them, and delete the duplicated
scan path. This is also the precondition for testing B1/B2 fixes properly.

### Finding H3: Runner state does not reset between runs on the same instance

**Severity:** high (latent), low in current GUI usage

**Area:** automation — `runner.py:188-205`

**Evidence:** `run()` resets `_stop_requested` but not `_stop_count`,
`_emergency_stop_requested`, or `_pause_requested`. A reused runner that was
emergency-stopped starts its next run with the emergency flag already set
(first scan poll fires `emergency_stop`), and any prior single stop makes the
next run's first Stop click escalate straight to emergency retract. The GUI
constructs a fresh runner per run, so this is currently masked; the CLI and
any future "restart" button would hit it.

**Recommendation:** reset all control flags (and `_stop_count`) at the top of
`run()`, or assert the instance is single-use.

### Finding H4: `stop_on_error` is dead and per-step error policy is inconsistent

**Severity:** high for unattended runs

**Area:** automation / recipe — `recipe.py:275`, throughout `runner.py`

**Evidence:** `MeasurementRecipe.stop_on_error` is never read. Behaviour on
failure varies by step: 0 V scan skip → emit error, continue; approach
timeout → emit error, continue; spectroscopy exception → propagates and kills
the run; survey positioning failure → sets `_stop_requested`; tip-form
problems → set `_stop_requested`. None of this is configurable or documented.

**Recommendation:** implement `stop_on_error` in the step loop (catch
step-level exceptions, decide continue/halt per the flag), and document the
per-step semantics. An overnight ramp probably *should* continue past one
failed scan; a survey should halt on positioning failure — make that an
explicit table, not an accident.

### Finding H5: Tip-form approval gate has no caller — and that's only half-good

**Severity:** medium-high

**Area:** safety / GUI — `runner.py:170-178`, no GUI references

**Evidence:** `approve_next_tip_form()` is invoked only by tests. No panel
builds `TipFormStep` recipes or arms approval, so any recipe containing a
tip-form step (e.g. via `scanflow run recipe.yaml`) refuses and halts —
fail-safe, good. But the approval mechanism the runner was built around is
unreachable, and `TipFormStep.require_confirmation` is serialised but never
consulted.

**Recommendation:** either wire a GUI confirmation dialog (scoped to one run,
as the generation check already supports) or remove the step type from the
documented surface until it's operational. Delete or honour
`require_confirmation`.

### Finding H6: Coordinate arithmetic is still inlined in the runner; conventions duplicated

**Severity:** high (process), root cause of B1/B2

**Area:** motion / geometry

**Evidence:** `scan_geometry.py` exists, is correct, and is used by the
preview workers — but the survey path, the mosaic `dy` formula
(`runner.py:1046-1051`), and `atom_tracker.find_reference_nm()` each
re-derive the centre/top-edge conversion locally. B1 is precisely the failure
mode the module docstring warns about.

**Recommendation:** adopt the plan's Priority 4 as a hard rule: any
conversion between pixels, ProbeFlow offsets, and Createc offsets must call a
`scan_geometry` helper; add the missing helpers (tile top-edge for an N×N
grid; wide-pixel→absolute target) plus unit tests, then delete the inline
math.

### Finding H7: GUI-thread COM traffic during active automation

**Severity:** medium-high

**Area:** connection / GUI — `main_window.py:146-210`, `core/z_monitor.py:114-122`

**Evidence:** while a worker runs, the main-window status timer performs
`connected` (a getp), `is_running`, `get_offset_nm`, and a full
`scan.read()` (~8 getps) every 3 s on the GUI thread, and `ZMonitor` calls
`raw.getdacvalfb()` at 1 Hz. The proxies are correctly per-thread, but the
STMAFM server serialises calls; this is sustained contention against the
automation thread, masked by the transient-retry policy. `raw.getdacvalfb()`
bypasses retry entirely. It is also the answer to plan §3.3.9: yes, GUI
timers do call COM during automation.

**Recommendation:** pause or thin the status poll while a runner is active
(the runner already emits everything the status bar needs), and have
`ZMonitor` skip polls during automation or subscribe to runner signals.

---

## Useful improvements

### U1: README describes a program that no longer exists
`README.md` documents the removed drift-correction feature (`drift_method`,
`--no-drift`, `scanflow/drift/detector.py` — none exist; `from_yaml` actively
strips those keys), claims "two tabs" (there are eight plus a preview
window), and says "29 tests" (there are ~122). Rewrite per plan §10.2 —
this is Priority 1 in the plan and I agree.

### U2: Dependency bloat — heavy ML packages required but never imported
`torch`, `torchvision`, `openai-clip`, `opencv-python`, `scikit-learn` are
hard dependencies in `pyproject.toml`; **none are imported anywhere in
`scanflow/`**. Meanwhile `probeflow` *is* imported
(`preview_panel.py:53`) but is not declared at all. Move the heavy set to
`[analysis]`/`[ml]` extras, declare `probeflow` (optional extra with a
guarded import), and keep the lab-PC core install lean (plan §11).

### U3: `recipe.validate(mode=...)` does not exist
The 0 V guard is enforced in three places (builder, runner scan step, mosaic
iteration) but there is no pre-run structured validation (setpoint range,
scan-size limit, total-duration warning, "contains tip-form" warning) as
sketched in plan §6.2. The `SweepConfirmDialog` covers sweeps only; survey/
mosaic/CLI recipes get no pre-flight check. Also: no max-total-runtime or
max-scan-count guard for overnight recipes (plan §4.4).

### U4: Survey/mosaic manifests are not written atomically
`SurveyManifest.save()` uses `Path.write_text` while sidecars and the session
manifest correctly use `write_json_atomic`. A crash mid-write corrupts
`survey.json` — the one file a partial campaign needs. One-line fix.

### U5: Mock cannot represent the coordinate convention
`MockDispatch.setxyoffvolt` maps volts→nm with a fixed 10 nm/V and no
frame-size/top-edge semantics, and `setxyoffpixel` uses an arbitrary 0.1
nm/px. Upgrading the mock to model the (rig-validated) offset semantics would
have caught B1 and would make B2-class bugs testable. Also worth adding: a
mock surface with placeable features so survey/zoom centering is assertable.

### U6: Stale/contradictory comments in safety-relevant code
`mosaic.py` module docstring describes rows in centre convention ("Y =
wide_centre.Y − wide_size/3") and says drift correction "uses
cross-correlation against iteration 1" — no such code exists in the tile
loop. `runner._do_mosaic_step` has a comment block describing a calibration
policy ("prefer delta unconditionally") that doesn't match the
`"current_then_delta"` call below it. `stm_client._require()` contains an
explicitly unreachable code block. In instrument-control code, wrong comments
are hazards — clean these when touching the files.

### U7: Two independent XY calibration caches
`ScanController` caches `_volts_per_nm_*` and `TipMotionManager` caches its
own `XYCalibration`; `set_offset_nm()` self-calibrates "from current" even
when the motion manager deliberately calibrated by delta. Consolidate to one
owner (the motion manager) and have `set_offset_nm` take/require calibration
rather than deriving its own.

### U8: Operator-facing state visibility (plan §8)
Mock vs real is shown only as a small status-bar label; safety
enabled/threshold, feedback mode, and "what Stop does next" (the button does
relabel to "Emergency Stop" — good) are scattered. The plan's suggested
instrument-state panel and explicit wording ("Connected to **mock** STM",
"Stop: finish current safe stop / Second click: emergency retract") would
cost little. The `PositioningDiagPanel` sends raw `setxyoffvolt` commands
with no confirmation and no mock-only gate — at minimum require a
confirmation when connected to the real instrument.

### U9: Test gaps to close before refactoring
Strengths: 120 passing mock-based tests, good coverage of recipes, safety
threshold, motion limits, scan geometry helpers, tip-form gating. Missing:
mosaic tile-position assertions (would have caught B1), survey centering
end-to-end (needs U5), safety-`None` behaviour (B3), atom-tracker failure
cleanup (B4), runner reuse (H3), and the tracking test-image dataset of plan
§5.6. `tests/data/` does not exist yet.

---

## Suggested order of work

1. **B1** (one-line-class fix + tests) and **B3** (small, high value) — immediately.
2. **B2**: one manual rig experiment to pin the resize semantics, then unify
   all paths on `scan_geometry` (H6) and upgrade the mock (U5).
3. **B4**: route atom tracker through `TipMotionManager` with `finally` cleanup.
4. **H1, H3, H4** — small runner changes, big unattended-run payoff.
5. **H2** executor extraction, with U9 tests written first.
6. **U1 README + U2 dependencies** before wider lab rollout.
