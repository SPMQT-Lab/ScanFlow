# Archived planning documents — HISTORICAL, DO NOT USE AS GUIDANCE

Everything in this folder is kept for provenance only. These documents
describe plans that have been superseded, completed, or deliberately
dropped. **Do not treat anything here as a to-do list, and do not let
these documents bias new code or new plans.**

Current planning lives in exactly two places:

* [`docs/long_term_architecture.md`](../long_term_architecture.md) —
  the primary architecture reference (layering, contracts, phases).
* [`ROADMAP.md`](../../ROADMAP.md) (repository root) — current status,
  working rules, immediate priorities, and explicit non-goals.

Contents and why each is archived:

| File | What it was | Status |
|---|---|---|
| `2025_roadmap_phases.md` | The original phase roadmap (Phase 3 = spectroscopy maturity, Phase 4 = AFM/qPlus, Phase 8 = `.sxm`/SpmImageTycoon export, …) | Superseded. Several phases were completed (mock STM, COM retry, spectroscopy steps); spectroscopy and AFM are now explicit *placeholders, not current work*; `.sxm` export is an explicit **non-goal** (the lab works with Createc `.dat` directly). |
| `2026-05_mosaic_week_plan.pdf` | Mosaic-week planning slide deck | Historical; mosaic functionality landed. |
| `2026-06_review_plan.md` | The instrument-control review brief | Executed in full — the outcome is `REVIEW.md` at the repository root. |
| `2026-06_dependency_investigation_brief.md` | The dependency-boundary investigation brief | Executed — the outcome is `docs/dependency_architecture.md` plus the enforced import-boundary tests. |
