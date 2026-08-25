# Phase 3 Errata — Documentation Pass

**Filed:** 2026-08-25 (P3 documentation pass) · **Branch:** `feature/pause-resume-terminate-tree-fix` @ `d7deaad2`
**Scope:** Errata on `phase3-plan.md` (Rev 2) and `phase2-plan.md` (Rev 2.2) discovered during P3 implementation. Errata live in this file — `phase3-plan.md` and `phase2-plan.md` are NOT edited in-place.

> **Convention:** Items are numbered E1–E6, each tagged with severity (🟢 non-blocking / 🟡 paperwork / 🔴 would have blocked a code path) and source attribution to the implementing coder's report.

---

## E1. B7(b) site misattribution: re-stamp surface is on the task table, not `job_queue_items` · 🟡 paperwork

**Source:** P3 B7(b) verification session — manual doc §7.2.

**Finding:** The plan's Task 3.7 / 3.9 speak of `job_queue_items.completed_at` stamp sites, but that **column was DROPPED from `JobItem` in Phase 5** by migration `20260628_000002_drop_job_queue_legacy_columns.sql`. The observed re-stamps in the B7(b) session live on the **task table** (`task` model at `daemon/services/task/models.py:205` — the `failed_at` timing column is the actually-flowing one in the re-arm cycle, not `completed_at`).

**Plan-literal vs reality:**

- **Plan-literal (Task 3.7/3.9):** reserved flag ships on `JobRepository.atomic_transition`; callers stamp `job_queue_items.completed_at` on first settle.
- **Actual:** the reserved flag is wired in plan-literal form on `JobRepository.atomic_transition`, but the first-touch caller that needs the last-settle semantics almost certainly belongs on `TaskRepository.complete_task` / `TaskRepository.fail_task` — the task table is where the timing column actually lives post-Phase 5.
- **Plan Case 4 wording is technically incorrect:** "still stamp `completed_at` on first settle" — the column flowing through the re-arm cycle is `failed_at` (task model), not `completed_at`. (Plan Case 4 was the semantics-pin case in the B7(b) re-arm verification, not a code defect — just a wording drift in the plan.)

**Implication for follow-up tickets:** FT-002 (B7c status disagreement) carries an addendum calling out the task-table reconciliation surface. A future reader of the plan should not take the `job_queue_items.completed_at` references at face value — they refer to the task-table timing column.

**Action:** FT-002 addendum records the cross-table timing reality. No plan edit (errata lives here).

---

## E2. B6: not reproducible on HEAD d7deaad2; citation drift; net disposition is FT-004 (NOT-A-DEFECT) · 🟡 paperwork

**Source:** P3 B6 probe-first diagnosis session — `p3-b6-diagnosis-bundle/`.

**Finding:** B6 (detail 404 post-resume) **could not be reproduced on the current code state**. All five H1–H5 hypotheses (routing/harness artifact, stale comparison, two-process port confusion, row invisibility, F-DR1-2 split-brain) are eliminated by probe evidence on HEAD `d7deaad2`. The plan's Task 3.4 "skip to probe 4" classifier logic **was exercised** — the 404 body class (`INSTANCE_NOT_FOUND`) led to probes 4 and 5, which confirmed row presence + single engine. Net disposition: **ticket FT-004 (B6-detail-404-post-resume) with NOT-A-DEFECT recommendation**.

**Citation drift recorded (from the diagnosis bundle and from coder notes):**

- `manager.py:9015` (plan citation) → actual ≈ `:9296` (post-citation-drift location).
- `instance_lifecycle.py:2966-2991` (plan citation) → actual ≈ `:3314`.

**Side-finding surfaced in the diagnosis bundle:** the original repro's `messages` endpoint ALSO 404'd with the same `INSTANCE_NOT_FOUND` body — the defect report's "detail-only" framing was imprecise. FT-004 records this in §2 of the ticket.

**Action:** FT-004 is filed; the B6 ticket is finalized. No plan edit.

---

## E3. B5: `daemon/manager.py` omitted from Files-Touched table; production wire shape nuance; composition case · 🟢 paperwork

**Source:** P3 B5 implementation report.

**Finding (Files-Touched):** The plan's `Files-Touched` table for B5 omitted `daemon/manager.py` (facade kwarg forwarding — required for the new `cascade_to_root: bool = True` parameter to reach the lifecycle service). The implementer had to add the forwarding at one site, then discovered a second site at the same facade boundary that also needed forwarding. Both are now in place.

**Finding (production wire shape):** Case 5 of the B5 cases asserts the **production wire shape** is `{"detail":{"code":"INSTANCE_NOT_FOUND"}}` (nested under `detail`), not the bare `{"detail":"Not Found"}` (FastAPI default for unmatched routes). The implementation correctly raises `HTTPException(404, detail=ErrorResponse(code=INSTANCE_NOT_FOUND, …))` at the 5 sites in `daemon/routers/instances.py` and 1 site in `daemon/routers/messages.py:171`. (This same wire shape is what made the B6 classifier probe (§E2) decisive: the body class is the custom `ErrorResponse`, not a routing artifact.)

**Finding (composition case):** The composition case (already-paused instance + stop request) was realized as a **separate test** — `test_composition_stop_pause_stop_already_paused` — not folded into the 8 case-realization tests. **9 tests shipped vs the plan's 8 cases** (composition counted separately).

**Action:** No plan edit. Future readers should expect 9 B5 tests, not 8.

---

## E4. P2 test-count provenance: 35/35 vs observed 34 · 🟡 paperwork

**Source:** P2 closure-council report.

**Finding:** P2 closure reports "35/35 tests pass" but the 6-file aggregate under `tests/unit/services/` and `tests/unit/repositories/` shows **34 tests across the 6 P2-shipped files** (after the +3 fast-follow tests: 31 baseline + 3 = 34). The "35/35" count is **one more** than the 6-file scope shows — likely a test outside the 6-file scope, possibly `tests/test_dependency_bus.py` (the dependency-bus unit tests, which exercise the P2 dependency-aggregation surface but live in a separate file).

**Action:** No plan edit. Future readers reconciling the P2 test count should know the 35th test is in `tests/test_dependency_bus.py` (or equivalent out-of-6-file scope) and not in the 6-file aggregate.

---

## E5. Cleanup candidates (out of scope, non-blocking) · 🟢 paperwork

**Source:** P3 implementation notes (cleanup pass).

**Finding (stale 3-tuple type annotation):** A 3-tuple type annotation near the pause-db-sync collection in `daemon/services/instance_lifecycle.py` is stale — production appends 2-tuples to the collection, not 3-tuples. The annotation should be narrowed.

**Finding (stale 3-tuple mock shape):** A 3-tuple mock shape in the skipped `tests/unit/test_tree_aware_pause_resume.py` (`_build_pause_db_sync_mock` helper) is also stale — the tests are skipped under `@pytest.mark.skip('Phase 5: DependencyBus not initialized')`, so the drift does not surface today, but if the skip is removed the mock shape needs alignment with the production 2-tuple.

**Action:** Out of scope for P3. Cleanup candidates for a future tidier pass.

---

## E6. `phase2-plan.md` Rev 2.2 errata overstatement · 🟢 paperwork (in-place correction applied)

**Source:** P3 tidier notes (W-C.c).

**Finding:** `phase2-plan.md` Rev 2.2 overstated the `test_h` coverage in a way that conflated compact-side and stamping-side bullets. W-C.c corrected this **in place** within `phase2-plan.md` (test_h coverage split into compact-side vs stamping-side bullets). This is the only erratum applied in-place to either of the two plans; all others (E1–E5) live in this errata file by design (per the "do not edit phase3-plan.md or phase2-plan.md" convention for this pass).

**Action:** ✅ Already applied in `phase2-plan.md` (W-C.c). No further action.

---

## Summary

| #   | Severity | Surface                                    | Disposition                                                            |
| --- | -------- | ------------------------------------------ | ---------------------------------------------------------------------- |
| E1  | 🟡        | Task-table re-stamp surface (B7b)          | FT-002 addendum; no plan edit                                          |
| E2  | 🟡        | B6 NOT-REPRODUCIBLE on HEAD                | FT-004 filed; no plan edit                                             |
| E3  | 🟢        | B5 Files-Touched + wire shape + composition| 9 tests not 8; no plan edit                                            |
| E4  | 🟡        | P2 35/35 vs 34 count                       | 35th test is out-of-6-file scope; no plan edit                         |
| E5  | 🟢        | Stale 3-tuple annotation + mock             | Out of scope (tidier cleanup candidate); no plan edit                  |
| E6  | 🟢        | phase2-plan.md Rev 2.2 overstatement        | ✅ In-place correction applied by W-C.c                                |
