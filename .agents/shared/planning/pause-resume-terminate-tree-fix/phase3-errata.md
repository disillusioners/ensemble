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

## E4. P2 unit-test aggregate: 38 passed across 8 shipper files (scope-anchored) · 🟡 paperwork

**Source:** P2 closure-council report, reconciled with the committed
test inventory in this session (2026-08-25).

**Finding (scope-anchored, committed artifacts as authority):** the
P2 unit-test aggregate across the **8 P2-shipped files** is
**38 tests passed**, with the per-file breakdown anchored to the
committed ``def test_*`` counts in each file:

| File (under `tests/unit/services/`) | Tests |
|---|---|
| `test_child_outcome_payload_surfacing.py` | 5 |
| `test_compact_fired_watchers_deliver_before_compact.py` | 10 |
| `test_dependency_bus_fire_for_terminated.py` | 6 |
| `test_parent_completion_idempotency_terminated.py` | 2 |
| `test_resume_cascade_drift_guard.py` | 6 |
| `test_revive_non_replay.py` | 2 |
| `test_terminate_downside_row_drain.py` | 4 |
| `test_terminate_path_coverage_fire_with_outcome.py` | 3 |
| **Total (8 files)** | **38** |

Sum: 5+10+6+2+6+2+4+3 = **38** (verified against the committed
``def test_*`` count in each file at 2026-08-25). The previously-cited
"35/35" / "34 across 6 files" framings are **superseded** — the
38-across-8-files figure is the scope-anchored reality, with each
per-file count reproducible by `grep -cE '^\s*(async )?def test_'
<file>`.

**Action:** No plan edit. Future readers reconciling the P2 test
count should use **38 across 8 files** as the canonical P2 unit-test
aggregate, not the older 35/35 or 34/6 figures.

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

## E7. Claims-vs-reality addendum (record-keeping corrections, no code impact) · 🟢 paperwork

**Source:** P3 errata delta pass (2026-08-25). Reconciling documentation
claims against the committed test inventory and the live source
under `daemon/routers/`. These are **record-keeping corrections** —
no production code or test code is changed by this entry.

**(a) B7(b) report "11 tests" claim vs committed reality of 10 tests**

The b7b manual-evidence report (`tests/manual/b7b_rearm_admission_history.md`)
references the test suite in `tests/unit/repositories/test_job_queue_atomic_transition.py`
and (in summary form) implies / claims 11 tests. The **committed
reality** (counted via `grep -cE '^\s*(async )?def test_'` at
2026-08-25) is **10 tests**:

| # | Test | Line |
|---|---|---|
| 1 | `test_rearm_recomplete_cycle_feasible` | 205 |
| 2 | `test_second_complete_without_rearm_raises` | 251 |
| 3 | `test_failed_at_re_stamps_to_last_failure` | 300 |
| 4 | `test_rearm_recancel_cycle_feasible` | 365 |
| 5 | `test_default_false_is_byte_identical` | 426 |
| 6 | `test_explicit_false_matches_default` | 457 |
| 7 | `test_true_branch_generates_coalesce_sql` | 494 |
| 8 | `test_repository_true_branch_emits_coalesce_sql` | 594 |
| 9 | `test_default_branch_does_not_emit_coalesce` | 658 |
| 10 | `test_no_callers_wire_true` | 691 |

The "11 tests" figure is **off-by-one** relative to the committed
test inventory. **No code impact**; this is a record-keeping
correction for future readers reconciling the b7b work scope.

**(b) B6 `probe1.md` "5 sites / 8 lines" surfacing-count claim vs measured reality of 12 lines**

`p3-b6-diagnosis-bundle/probe1.md` line 44 (and E3 above, which
inherits the same wording) characterizes the surfacing sites for the
``INSTANCE_NOT_FOUND`` `ErrorResponse` body as **"5 sites" in
`daemon/routers/instances.py` and "1 site" in
`daemon/routers/messages.py`** (6 sites total). The **measured
reality** (verified by `grep -nE 'INSTANCE_NOT_FOUND' daemon/routers/`
at 2026-08-25) is **12 surfacing sites across 2 files**:

| File | Sites | Lines |
|---|---|---|
| `daemon/routers/instances.py` | **9** | 502, 604, 649, 682, 962, 1220, 1400, 1483, 1509 |
| `daemon/routers/messages.py` | **3** | 171, 477, 527 |
| **Total** | **12** | (2 files) |

The claim understates the surfacing footprint by **6 sites** (12
measured vs 6 claimed). **No code impact** — the body shape
(`ErrorResponse(code=INSTANCE_NOT_FOUND, ...)`) is identical at all
12 sites; the count is purely a documentation-fidelity correction.
The E3 wording ("5 sites in instances.py and 1 site in
messages.py:171") is preserved as-is here for record-keeping; the
12-line reality is the authoritative count.

**(c) No code impact**

Both (a) and (b) are documentation/scope-fidelity corrections. The
committed production code, test code, and disposition logic are
unchanged. Future readers should anchor their cross-document
reconciliation to the measured counts (10 tests; 12 surfacing
sites), not the claim-flavoured summaries.

**Action:** No plan edit. No code edit. Errata lives here.

---

## Summary

| #   | Severity | Surface                                    | Disposition                                                            |
| --- | -------- | ------------------------------------------ | ---------------------------------------------------------------------- |
| E1  | 🟡        | Task-table re-stamp surface (B7b)          | FT-002 addendum; no plan edit                                          |
| E2  | 🟡        | B6 NOT-REPRODUCIBLE on HEAD                | FT-004 filed; no plan edit                                             |
| E3  | 🟢        | B5 Files-Touched + wire shape + composition| 9 tests not 8; no plan edit                                            |
| E4  | 🟡        | P2 unit-test aggregate                     | **38 across 8 files** (scope-anchored, committed `def test_*` count)  |
| E5  | 🟢        | Stale 3-tuple annotation + mock             | Out of scope (tidier cleanup candidate); no plan edit                  |
| E6  | 🟢        | phase2-plan.md Rev 2.2 overstatement        | ✅ In-place correction applied by W-C.c                                |
| E7  | 🟢        | Claims-vs-reality addendum                 | (a) b7b "11 tests" → 10; (b) probe1.md "5/1 sites" → 12 measured; no code impact |
