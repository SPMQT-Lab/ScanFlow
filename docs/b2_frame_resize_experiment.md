# B2 frame-resize experiment — protocol and decision tree

**Question:** when `SCAN.IMAGESIZE.NM.Y` changes, what does STMAFM keep
fixed — the frame's **top edge** or its **centre**?

**Why it matters:** ScanFlow's survey zoom path assumes *centre preserved*;
the mosaic path, the preview workers, and `scan_geometry.py` assume *top
edge preserved* (readback = top edge). At most one is right; whichever is
wrong mis-positions its zoom frames by half a frame in Y (~57 nm for
default survey settings). Tracked as `FIXME(B2-frame-resize)` in
`runner.py`, `scan_geometry.py`, `mock_dispatch.py`; guarded by
`tests/test_open_findings.py`. This one experiment gates all positioning
work (ROADMAP §3.1) and the Phase 3 executor extraction.

**Time at the rig:** ~10 minutes, while in tunnelling on any surface with
one recognisable feature. No XY motion is commanded; only the frame
height changes (and is restored automatically). Two ordinary scans are
taken, each behind an explicit confirmation.

## Running it

```cmd
python -m scanflow diag frame-resize
```

(Dry-run anywhere first: `python -m scanflow diag frame-resize --mock --no-input`.)

The command walks through:

1. You position the frame over a recognisable feature, ideally
   off-centre **vertically** (upper third of the frame is ideal).
2. BEFORE scan (confirmed; safety current check first).
3. Frame height is halved — width untouched, no motion commands.
   Offset readbacks (`SCAN.OFFSET.{X,Y}.NM` and `.VOLT`) are recorded
   before and after.
4. AFTER scan (confirmed).
5. You record where the feature appears in the AFTER image
   (`t`/`c`/`g`/`u` prompt).
6. Frame height is restored (also on error/abort), and a JSON report is
   written to `frame_resize_diag/`.

## Interpreting the result

Two independent signals; they should agree.

**Signal 1 — the image** (primary, physical truth). With the feature in
the upper third of the BEFORE frame:

| AFTER image shows | Meaning |
|---|---|
| Feature at the same distance from the **top edge** (bottom of the field of view lost) | **TOP EDGE preserved** |
| Feature at the same *relative* position / frame shrank symmetrically (top and bottom both lost) | **CENTRE preserved** |
| Feature gone from a half-height frame after sitting in the upper third | Inconsistent with both — repeat with a cleaner feature before concluding anything |

**Signal 2 — the `SCAN.OFFSET.Y.NM` readback shift** (in the report's
`derived` block, also printed by the CLI):

| Readback shift on halving height H | Meaning (given readback = top edge) |
|---|---|
| ≈ 0 | TOP EDGE preserved |
| ≈ +H/4 | CENTRE preserved |
| Anything else / readback shifts but image says otherwise | The readback is **not** a top edge — record everything and re-derive the convention before touching code |

`SCAN.OFFSET.{X,Y}.VOLT` should not change in any outcome (the piezo was
not commanded). If it does change, stop and investigate — resizing is
moving the physical anchor, which invalidates more than this experiment.

## Decision tree — what to fix in each outcome

**Outcome A: TOP EDGE preserved** (scan_geometry's documented model is right)
1. The **survey zoom path is wrong**: rewrite `_do_feature_zoom`'s
   centering (runner.py) to compute absolute targets through
   `scan_geometry.feature_target_xy_nm` (as `preview_followup.py` does),
   instead of relative pixel moves across a frame-size change.
2. Mosaic, preview workers, `scan_geometry.py` are correct — no change.

**Outcome B: CENTRE preserved**
1. The **survey path is right**; `scan_geometry.py`'s docs and the
   `feature_target_xy_nm` half-frame term are wrong for resize-crossing
   moves: fix the helper + docstrings, then re-verify **mosaic tile
   placement** and **preview follow-up targets** on the rig (they used
   the top-edge model and appeared to work — understand why before
   changing them; the answer may be that their moves never cross a
   resize in the order they perform operations).

**Both outcomes, same PR:**
3. Encode the verified behaviour in `MockDispatch.setxyoffvolt` /
   the mock's IMAGESIZE handling so survey/mosaic centring is testable
   (mock currently has no frame-size dependence — that is why this bug
   class was invisible to 177 passing tests).
4. Add regression tests: zoomed feature lands at frame centre in a mock
   survey; mosaic tiles tile exactly.
5. Delete the three `FIXME(B2-frame-resize)` markers **and**
   `tests/test_open_findings.py::test_b2_frame_resize_markers_present`
   in the same commit.
6. Attach the JSON report (and the two `.dat` files) to the commit
   message or `docs/archive/` for provenance, and update ROADMAP §3.1.

## Report contents

`frame_resize_report_<stamp>.json` (schema
`scanflow.diagnostic.frame_resize.v1`): pre/post/restored frame state
(size, pixels, offset nm + volt readbacks, scan status), scan file paths,
the operator observation, derived shift vs. the two predictions, and any
notes (skipped/refused steps, restore failures).
