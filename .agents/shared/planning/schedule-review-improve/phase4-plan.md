# Phase 4: Clean Up (Tier P4)

**Branch:** `feature/schedule-review-improve`
**Date:** 2026-08-24
**Author:** planner[v2] via plan-creation worker (Phase 4 of 4)
**Phase tier:** P4 (Clean Up)
**Locked decisions in force:**
- `decisions.md` §D2 + §D7.3 (INV-13 — **RE-SCOPED to verify-and-document + regression pin**; the three call-sites `cancel_task`, `complete_task`, `fail_task` are ALREADY thin named-transition wrappers — verified at `repository.py:3102, 1746, 1874` calling `transition._write()` per `:1813-1827, :1930-1941, :3169-3175`; the ≤4-LOC ceiling and first-site hard-exit gate are MOOT; reword the success criterion to "3 call-sites verified + regression-pinned" — keeps 15/15 honest)
- `decisions.md` §D3 + §D7.7 (INV-15 → QUARANTINE — **HOISTED to cycle pre-flight** (was Phase-4 Task 4.4); pack statically false-PASSes today so Phase-1/2/3 gate runs currently include a false-PASSING pack in the pipeline — quarantine is doc-only, do it FIRST; add to `.agents/tester/QUARANTINE.md` with the rationale text)
- `decisions.md` §D6 + §D7.3-amend (INV-12 — REMOVE the dead `job_id` overloads; verify no consumer outside the 9 known sites before removal)

---

## Objective

Sweep the dead code, the deferred named-transition stubs, the docstring drift, and the false-passing mock harness that this cycle's three earlier phases (P1–P3) left in their wake — without expanding scope. Specifically:
**(a)** remove the F13 `job_id` overloads at 9 known sites (INV-12), **(b)** VERIFY + REGRESSION-PIN the three core task-status setters as named-transition wrappers under `reconcile_turn_mirror` (INV-13 — re-scoped per architect §2c / decisions D7.3; the migration is already done — this phase pins the contract), **(c)** fix the legacy-JobStatus docstring wording (INV-14), and **(d)** quarantine the broken mock harness (INV-15 — hoisted to cycle pre-flight per decisions D7.7; runs BEFORE Phase-1 dispatch, not in Phase 4).

Outcome: `daemon/services/job_feedback_observer.py` no longer carries a `job_id` parameter that the direct event path does not consume; the three call-sites `cancel_task`/`complete_task`/`fail_task` (`daemon/repositories/task/repository.py:3102, 1746, 1874`) are verified as thin `transition._write()` wrappers over `AbortTurn`/`CompleteTurn` and pinned by regression tests; `daemon/services/job_state_machine.py:3` no longer says "legacy"; `tests/mock_test_job_queue_api.py` + `test/packs/mock_job_queue_test.sh` are QUARANTINED in `.agents/tester/QUARANTINE.md` (added in cycle pre-flight) so the gate no longer false-PASSes.

---

## Component Inventory

| Item | Class | E2E Gate | Frozen line refs | Locked decision |
|------|-------|----------|------------------|-----------------|
| **INV-12** | Core (dead-code removal) | Core gate (static check) | `daemon/services/job_feedback_observer.py` — overload sites 643, 684, 703, 764, 907, 942, 1959, 2010, 2012 (these are docstring/comment refs to F13); producer `daemon/manager.py:970` (`EventPublisherService`) | D6: REMOVE |
| **INV-13** | **Verification + regression pin** (was: bounded migration) | **Core gate only** (was: Core + Release gate) | `daemon/repositories/task/repository.py:3102` (`cancel_task`), `:1746` (`complete_task`), `:1874` (`fail_task`) — ALREADY thin wrappers over `AbortTurn`/`CompleteTurn` via `transition._write()` (`:1813-1827, :1930-1941, :3169-3175`; docstrings say "THIN WRAPPER"); the "stubs" at `daemon/services/turn_transitions.py:93-117` (`BeginTurn`) and `:119-137` (`ClaimTurn`) have zero production callers and remain stub-orphaned (out of cycle scope — Cycle-3 territory). The full Phase-4b/4c migration surface (`_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, permanent `_status_write_guard` enablement) is OUT of cycle — Cycle-3 follow-up. | D7.3: VERIFY + REGRESSION-PIN |
| **INV-14** | Core (static docstring) | Core gate (static) | `daemon/services/job_state_machine.py:3` (docstring "legacy" → "former") | (trivial) |
| **INV-15** | **HOISTED to cycle pre-flight** (was: Core pack quarantine in Phase 4) | Core gate (pack check) — runs in pre-flight, not Phase 4 | `tests/mock_test_job_queue_api.py:1027` (`pytest.main` exit swallowed); `test/packs/mock_job_queue_test.sh:16` (raw python invocation); 48/48 setup errors from `JobLockManager` signature drift | D3 + D7.7: QUARANTINE — pre-flight |

> **INV-13's gate class changes** (architect §2c; decisions D7.3): The original `research-findings.md` §E2E Gate Applicability claim that INV-13 touched `reconcile_turn_mirror` + `job_locks` and therefore escalated to Release gate was based on the stale premise that INV-13 would MIGRATE `_finalize_job_db_sync` / `_terminate_instance_db_sync`. Re-scoped to verify-and-document + regression pin: **zero production-code change is expected** — INV-13's actual change surface is regression tests only. Therefore **Core gate only** is the correct gate; the Release gate claim is corrected (see Verification §Release Gate correction below). INV-12, INV-14, INV-15 are static / non-runtime-change.

---

## Sub-Slice Map

> **Amendment (architect §5; decisions D7.7):** INV-15 is **HOISTED to cycle pre-flight** (was Phase-4 Task 4.4). It runs FIRST, before Phase-1 dispatch — not in Phase 4. The pack statically false-PASSes today (exit 0 with the inner `pytest.main` exit code swallowed at `mock_test_job_queue_api.py:1027`); Phase-1/2/3 gate runs currently include a false-PASSING pack in the pipeline. Quarantine is doc-only → do it first. See the **Pre-flight** section below for the hoisted task.

```
Cycle Pre-flight (HOISTED, runs before Phase 1)
└── Pre-flight quarantine  ── INV-15 mock harness QUARANTINE ── [preflight-worker]  (doc-only + pack marker)

Phase 4 (Clean Up)
├── Trivial slice (parallel, isolated)
│   ├── INV-12  ── F13 dead job_id overload removal ── [cleanup-worker-A]  (production code)
│   └── INV-14  ── job_state_machine.py:3 docstring wording ── [cleanup-worker-B]  (single-line doc edit)
└── Verification slice (parallel with INV-12 + INV-14; no longer gated on Phase 2 INV-5)
    └── INV-13  ── Turn-Reconciler **verify + regression-pin** ── [verify-worker-C]  (CORE GATE ONLY — gate dissolved per D7.3 + D7.7)
```

- **INV-12** and **INV-14** are fully independent (different files, no shared symbol). Both are trivial and can land in the same worker OR parallel workers.
- **INV-13 (re-scoped to verify-and-document + regression pin)** is **NO LONGER GATED** behind Phase 2 INV-5 (architect §2c + §5; decisions D7.3 + D7.7). The original D2 gate's stated justification — "INV-13 will reuse `reconcile_paused_task_on_resume` (A′1)" — is false: there is no A′1 in the redesigned INV-5 (A′1r uses `AbortTurn(reason='failed')` post-commit, not the original `reconcile_paused_task_on_resume` helper). INV-13's actual change surface is **regression tests pinning the three call-sites as named-transition wrappers** — zero production-code change. It can run ∥ INV-12 + INV-14 (and ∥ Phase 3 late-wave INV-9 + INV-11).
- **INV-15** lives in **Pre-flight** (see below), not in Phase 4.

---

## Ordered Task List

### Slice A — Trivial (INV-12 + INV-14, parallelizable)

#### Task 4.1 — INV-12 verification grep
- **Type:** static / verification step (no code change)
- **Depends on:** none
- **Acceptance criteria:**
  1. Run the verification grep per D6: `git grep -nE "(job_id: str \| None = None|job_id=None|job_id=job_id|job_id=ctx\.job_id)" daemon/` plus a manual review of the 9 cited lines (643, 684, 703, 764, 907, 942, 1959, 2010, 2012).
  2. Produce a verification table mapping each of the 9 cited "F13 overload sites" to either: **(a)** pure docstring/comment text → safe to delete with the comment, **(b)** function signature / parameter passing → requires a behavioral decision.
  3. Output the table to this phase-plan's commit-message body OR to `decisions.md` §D6-supplement if any site is NOT pure comment text.
  4. If the grep surfaces a non-comment consumer outside the 9 sites → STOP, route to planner via `decisions.md` amendment request (D6 was authored assuming "no other code path consumes the parameter").
- **Sub-slice ID:** `p4-inv12-verification-grep`
- **Why:** D6's "REMOVE" decision rests on the assumption that no production code outside the 9 sites consumes `job_id`. The verification grep is the gate. **Do not skip.**

#### Task 4.2 — INV-12 removal
- **Depends on:** Task 4.1 (verification grep PASSED)
- **Acceptance criteria:**
  1. Remove the F13 `job_id` parameter from `_get_processing_job_for_instance(self, instance_id: str)` signature (current `daemon/services/job_feedback_observer.py:633`).
  2. Remove the corresponding `job_id` arguments at the in-file call sites that pass `job_id=job.job_id` (lines 735, 754, 785, 806, 1200, 1472, 1648, 1887, 2022, 2089) — these are the actual code paths that were threading the overload.
  3. Remove the F13 commentary paragraphs at the 9 cited docstring/comment sites (643, 684, 703, 764, 907, 942, 1959, 2010, 2012).
  4. Remove `EventPublisherService` `job_id` parameter at `daemon/manager.py:970` and the corresponding `job_id` keyword at every producer site.
  5. `git diff daemon/` — reviewable in one pass; net LOC change negative (deletion-heavy).
  6. `python -c "from daemon.services.job_feedback_observer import JobFeedbackObserver"` — import check PASSES (no dangling references).
  7. `bash test/packs/sources_unit_test.sh` — PASS (verifies the source-layer path is unaffected).
  8. `bash test/packs/p3_job_orphan_recovery_unit_test.sh` — PASS (verifies the job-queue path is unaffected).
- **Why:** D6 LOCKED — REMOVE the overloads. Lower-risk than threading; smaller diff; removes a known footgun.
- **Sub-slice ID:** `p4-inv12-removal`

#### Task 4.3 — INV-14 docstring wording fix
- **Depends on:** none (independent of INV-12)
- **Acceptance criteria:**
  1. Edit `daemon/services/job_state_machine.py:3` — replace the word "legacy" with "former" (or equivalent wording per the surrounding sentence). Verify the surrounding 7-value enum narrative remains coherent.
  2. The change is single-line; `git diff daemon/services/job_state_machine.py` shows ≤3 lines changed.
  3. `python -c "import daemon.services.job_state_machine"` — import check PASSES.
  4. Optionally bundle with INV-12 commit (same file category, same tier) — but allowed to land standalone.
- **Sub-slice ID:** `p4-inv14-docstring-fix`

### Slice B — Pre-flight HOISTED (was Phase-4 Task 4.4 — INV-15 quarantine)

> **HOIST AMENDMENT (architect §5; decisions D7.7):** originally this lived in Phase 4 as Task 4.4. It is **HOISTED to cycle pre-flight** because the pack statically false-PASSes today — exit 0 with the inner `pytest.main` exit code swallowed at `mock_test_job_queue_api.py:1027` plus raw python invocation at `test/packs/mock_job_queue_test.sh:16` — so Phase-1/2/3 gate runs currently include a false-PASSING pack in the pipeline. Quarantine is doc-only → do it FIRST, before Phase-1 dispatch. Phase 4 no longer owns this task; it lives in a new "Pre-flight" section above the Phase 4 sub-slice map (the Sub-Slice Map already references it; this section holds the task body for the hoisted worker).

#### Pre-flight Task — INV-15 quarantine registry entry (was Phase-4 Task 4.4)
- **Type:** registry edit (no production code change)
- **Position:** Cycle pre-flight — runs FIRST, before Phase-1 dispatch
- **Depends on:** none
- **Acceptance criteria:**
  1. Append a new row to `.agents/tester/QUARANTINE.md` §Active with the following fields:
     - **Test:** `tests/mock_test_job_queue_api.py` (the entire file)
     - **Pack / File:** `test/packs/mock_job_queue_test.sh` (`mock_job_queue_test` pack)
     - **Date Quarantined:** 2026-08-24
     - **Reason:** (verbatim from `decisions.md` §D3) > `mock_job_queue_test` pack quarantined 2026-08-24 (Cycle 2). 48/48 tests error in setup due to `JobLockManager` signature drift (commit lost to prior session branch recreate). `pytest.main` exit code swallowed at `tests/mock_test_job_queue_api.py:1027` plus raw python invocation at `test/packs/mock_job_queue_test.sh:16` produces a FALSE PASSING gate signal (effective coverage 0). Pack removed from gate pipeline via QUARANTINE until repair is scoped. See `decisions.md §D3` from `schedule-review-improve` Cycle 2.
     - **Retry Budget:** N/A (quarantine, not retry)
     - **Status:** QUARANTINED (skip-markered)
  2. Update the pack script `test/packs/mock_job_queue_test.sh` to exit with a clear "QUARANTINED" marker (e.g., `echo "RESULT: QUARANTINED (see .agents/tester/QUARANTINE.md §D3)" ; exit 0`) so future operators see the rationale at invocation time.
  3. Verify the pack is no longer in any default Core / Release gate run (grep PACKS.md / CI configs).
  4. The Cycle-3 repair path remains contingent on recovering the signature-change commit (per `decisions.md §D3` alternative).
  5. **Side note (architect §9 risk):** `PACKS.md:346` records **FAIL** for the mock pack while the pack currently exits 0 — an operator-visible inconsistency that persists until this pre-flight lands. The pre-flight closes the gap: once `QUARANTINED` is in the registry, the pack is skipped at the registry level and `PACKS.md:346` FAIL no longer surfaces as an operator-actionable signal.
- **Why:** D3 LOCKED — QUARANTINE. The 48/48 setup-error + false-PASS combination is a worse signal than the missing coverage itself. Hoisting to pre-flight removes the false-PASSING signal from all gate runs immediately, ahead of any phase dispatch.
- **Sub-slice ID:** `preflight-inv15-quarantine`

### Slice C — INV-13 verify-and-document (re-scoped per architect §2c; decisions D7.3)

> **Re-scope amendment:** The original D2 bounded-migration set (`cancel_task`, `complete_task`, `fail_task`) has nothing left to migrate — all three call-sites are ALREADY thin wrappers over `AbortTurn` / `CompleteTurn` via `transition._write()` (call-sites at `repository.py:3102, 1746, 1874`; wrapper bodies at `:1813-1827, :1930-1941, :3169-3175`; docstrings say "THIN WRAPPER"). The ≤4-LOC ceiling, the first-site hard-exit, and the Phase-2 INV-5 sequencing gate are **all MOOT** — deleted. New Slice C tasks: regression-pin the three sites and document the cycle-3 tail.

#### Task 4.5 — [DELETED] Phase 2 INV-5 closure check (gate dissolved)

> **Original Task 4.5 deleted** (architect §5; decisions D7.7). The sequencing gate is dissolved: INV-13 no longer requires Phase 2 INV-5's reconciliation contract to stabilize before it starts. INV-13-as-verification can run ∥ INV-12, INV-14, Phase-3 INV-9, and Phase-3 INV-11.

#### Task 4.6 — [DELETED] First-site ceiling check (hard-exit)

> **Original Task 4.6 deleted** (architect §2c; decisions D7.3). The ≤4-LOC ceiling check and hard-exit rule are MOOT: the three "to-migrate" call-sites are already thin wrappers over named transitions (zero production-code change is needed in INV-13). There is no "first site" to migrate.

#### Task 4.7r — INV-13 verify + regression-pin (replacement)

- **Type:** verification + regression tests (zero production-code change)
- **Depends on:** none (no longer gated on Phase 2 INV-5)
- **Acceptance criteria:**
  1. **Verify (no code change)**: read `daemon/repositories/task/repository.py:3102` (`cancel_task`), `:1746` (`complete_task`), `:1874` (`fail_task`). Each must invoke a `transition._write()`-based wrapper body (`repository.py:1813-1827, :1930-1941, :3169-3175` respectively). Docstrings must say "THIN WRAPPER". Record the verification result in the commit message body OR in `decisions.md §D7.3-verification-log`.
  2. **Regression-pin `cancel_task → AbortTurn`**: write `tests/unit/repositories/task/test_task_status_named_transition_wrappers.py`. For `cancel_task` (`:3102`): assert the call invokes `AbortTurn(reason='failed')` (the correct discriminator per architect §2c — a separate `FailTurn` is unnecessary). Mock at the `transition._write()` seam; assert the transition instance carries `reason='failed'` (NOT a raw status UPDATE).
  3. **Regression-pin `complete_task → CompleteTurn`**: same test file. For `complete_task` (`:1746`): assert the call invokes `CompleteTurn(reason='completed')` (or the equivalent canonical terminal_reason). Mock at the `transition._write()` seam; assert the transition instance is `CompleteTurn`.
  4. **Regression-pin `fail_task → AbortTurn(reason='failed')`**: same test file. For `fail_task` (`:1874`): assert the call invokes `AbortTurn(reason='failed')` (NOT `CompleteTurn` — different transition vocabulary for the failure discriminator). Mock at the `transition._write()` seam; assert the transition instance is `AbortTurn` AND `reason == 'failed'`.
  5. **Pin behavior, not implementation where practical**: assertions should mock at the `transition._write()` seam and assert on the transition instance's class + kwargs (the public contract). Do NOT assert on internal line numbers or private helper call patterns. The wrapper-body file:line refs in `repository.py` are the audit anchor but the regression test pins the public seam.
  6. `git diff daemon/` reviewable in one pass — net change is test-file additions only, ZERO production-code change expected. If production code DID change, STOP — route to planner via `decisions.md §D7.3-execution-log` amendment before merge.
  7. `bash test/packs/turn_transitions_reconciler_unit_test.sh` — PASS (existing reconciler test pack, currently deselected per `.agents/tester/QUARANTINE.md` row at line 23 — verify the deselection still holds).
  8. `bash test/packs/p3_job_orphan_recovery_unit_test.sh` — PASS (verifies Phase 3's INV-9 net is unaffected).
  9. If the existing quarantined `test_state_machine` row is the only reconciler test that's quarantined, document a Cycle-3 un-quarantine path in the commit message body — DO NOT attempt un-quarantine this cycle.
- **Why:** D7.3 re-scoped — the migration is already done; the regression pins prevent future drift (a future refactor that converts the thin wrappers back to raw UPDATEs would fail these tests). The success criterion wording changes from "3 call-sites migrated" to "3 call-sites verified + regression-pinned" — keeps the 15/15 inventory honest.
- **Sub-slice ID:** `p4-inv13-verify-pin`

#### Task 4.8 — INV-13 deferred items registry note (Cycle-3 tail)

- **Type:** doc-only (route back to planner for Cycle-3 inclusion)
- **Depends on:** Task 4.7r (or skipped if 4.7r surfaces a regression)
- **Acceptance criteria:**
  1. Append a `decisions.md §D7.3-cycle3-tail` note listing the four REAL deferred items: `_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, permanent `_status_write_guard` enablement. **Do NOT pull these into this cycle** — they are migration-shaped work D2's hard-exit exists to stop, AND they conflict with INV-12's same-file edits (`test_observer_race1.py:91-119` mirrors `_finalize_job_db_sync`'s signature).
  2. Also note the stub-orphans at `daemon/services/turn_transitions.py:93-117` (`BeginTurn`) and `:119-137` (`ClaimTurn`) — zero production callers; cleanup decision deferred to Cycle-3.
  3. The note states the rationale (D7.3 verify-and-document scope; the migration was already complete; the remaining items are out-of-cycle scope) and the Cycle-3 owner responsibility.
  4. NO production code change in this task.
- **Why:** Makes the deferral visible to the next cycle's planner; prevents re-investigation of "why didn't INV-13 do X".
- **Sub-slice ID:** `p4-inv13-followup-note`

---

## Cross-Phase Dependency Notes

| Direction | What | How it surfaces in Phase 4 |
|-----------|------|----------------------------|
| Pre-flight → Phase 1/2/3 | INV-15 QUARANTINE row must land before any other phase's gate runs | Pre-flight Task acceptance criteria assert the row exists; without it, Phase-1/2/3 gates run with a false-PASSING pack in the pipeline |
| Phase 2 → Phase 4 (INV-13) | **DISSOLVED.** INV-13 no longer requires Phase 2 INV-5 to merge first | Original Task 4.5 sequencing gate deleted (architect §5; decisions D7.7). INV-13-as-verification can run ∥ INV-12 + INV-14. The constraint is now a **vocabulary freeze** (doc-level), not a phase gate |
| Phase 3 → Phase 4 (INV-12) | INV-12's removal must not break Phase 3's new tests | Tasks 4.1–4.2 run the Phase 3 packs (sources_unit_test.sh, p3_job_orphan_recovery_unit_test.sh) as part of acceptance; a removal-induced regression fails the task before merge |
| Phase 4 (intra) | INV-12, INV-14 are independent; can land in any order or in parallel | Sub-slice map encodes this |
| Phase 4 (intra) | INV-13 is no longer gated; can run with INV-12/INV-14 | Sequencing gate dissolved (D7.3 + D7.7); INV-13 runs ∥ trivial slice |
| Phase 4 → future | INV-13 deferred items + INV-11 swallow-design-deferral (Phase 3) → Cycle-3 plan | Task 4.8 + Phase 3 Task 3.5 produce the doc handoff; **the four REAL deferred items** (`_finalize_job_db_sync`, `_terminate_instance_db_sync`, `force_cancel_and_schedule_retry`, permanent `_status_write_guard`) + the stub-orphans at `turn_transitions.py:93-117, 119-137` are listed for Cycle-3 ownership |

---

## Verification

### Core Gate (always-on; scoped to Phase 4 packs)

Per `.agents/tester/rules/ensure.md` §Core:

```bash
# INV-12 verification
python -c "from daemon.services.job_feedback_observer import JobFeedbackObserver" && \
python -c "from daemon.manager import EventPublisherService"

# INV-12 regression sweep (Phase 3 packs must still PASS)
timeout 120s bash test/packs/sources_unit_test.sh
timeout 120s bash test/packs/p3_job_orphan_recovery_unit_test.sh

# INV-14 docstring check (static)
grep -n "legacy 7-value" daemon/services/job_state_machine.py && echo "FAIL: INV-14 docstring not fixed" || echo "PASS"

# INV-15 quarantine check — runs in PRE-FLIGHT (not Phase 4 Core gate). Listed here for traceability; the registry row is asserted before Phase-1 dispatch, not at Phase 4 close-out.
# Pre-flight already appended the QUARANTINED row to .agents/tester/QUARANTINE.md; this grep confirms the row landed.
grep -E "QUARANTINED.*Cycle 2" .agents/tester/QUARANTINE.md && echo "PASS (pre-flight artifact already in registry)"

# INV-13 reconciler pack (Core gate only after re-scope)
timeout 120s bash test/packs/turn_transitions_reconciler_unit_test.sh
```

> Per `ensure.md` §Core §Critical: "every pack in the blast-radius change set returns PASS". Quarantined tests (`.agents/tester/QUARANTINE.md`) are skipped and do not fail any pack.

### Release Gate correction (INV-13 — gate class change)

> **Architect §2c; decisions D7.3 amendment:** the original Release Gate claim for INV-13 was based on the premise that INV-13 would **MIGRATE** `_finalize_job_db_sync` / `_terminate_instance_db_sync` (these touch `reconcile_turn_mirror` + `job_locks` — `ensure.md §Release Gate` triggers Release gate for that surface). The premise is stale: INV-13 is **re-scoped to verify-and-document + regression pin** — **zero production-code change is expected**. The regression tests pin the three call-sites as named-transition wrappers; the actual production code (`:1813-1827, :1930-1941, :3169-3175`) is unchanged.
>
> **Corrected gate class for INV-13:** **Core gate only** — not Release gate. The Release gate block below is preserved as a Phase-4 E2E sweep but is no longer a hard prerequisite for INV-13's task acceptance. The corrected acceptance criterion: "INV-13's regression tests PASS under Core gate; the reconciler pack (`turn_transitions_reconciler_unit_test.sh`) either PASSES or remains deselected per the existing `.agents/tester/QUARANTINE.md` line 23 entry."

### Release Gate (Phase-4 E2E sweep — no longer gated on INV-13)

Phase 4's E2E coverage is driven by Phase 3 INV-9 + INV-11 (test-after mix; clock-injected contracts). INV-13 no longer escalates to Release gate on its own merit; the Release gate sweep below is still run as part of the final cycle close-out per `ensure.md §Release Gate §Prerequisites`.

```bash
# Phase-4 final Release-gate prerequisites (per ensure.md §Release Gate §Prerequisites)
./dev.sh  # daemon at localhost:8079
unset SSL_CERT_FILE SSL_CERT_DIR
PYTEST_TIMEOUT=280 .venv/bin/pytest \
  --override-ini="addopts=" --override-ini="timeout=280" \
  -m integration -k "test_three_level_cascade_reports" \
  --tb=short -q
# Plus the full non-integration suite (parallel packs, each ≤5min).
```

Per `ensure.md`: "Run tests **one by one** (each makes real LLM calls; combined exceeds 5-min cap)". The Release gate runs the E2E `e2e_workflows_ensure_test.sh` and the cascade test.

### Quarantine-Awareness

- **INV-15's QUARANTINE row is added in PRE-FLIGHT** (not Phase 4). The pre-flight task runs before Phase-1 dispatch; once it lands, `mock_job_queue_test` returns 0 (skipped) and does not fail any gate in any subsequent phase.
- INV-13's reconciler test pack (`turn_transitions_reconciler_unit_test.sh`) already has a quarantined row (`.agents/tester/QUARANTINE.md` line 23, `TestTurnReconcilerStateMachine::test_state_machine`) — it is deselected and does not fail the gate.

### E2E Gate Class

| Item | E2E Gate | Reason |
|------|----------|--------|
| INV-12 | **Core gate only** | Dead code removal; verified by import check + Phase 3 pack regression sweep |
| INV-13 | **Core gate only** (corrected from Core + Release — architect §2c; decisions D7.3) | Re-scoped to verify-and-document + regression pin; **zero production-code change** expected; gate class follows actual change surface, not the original D2 premise |
| INV-14 | **Core gate only (static)** | Single-line docstring fix; no execution behavior change |
| INV-15 | **Pre-flight gate only** (HOISTED from Phase 4) | Quarantine registry edit; runs before Phase-1 dispatch |

### Phase 4 Exit Criteria

1. **PRE-FLIGHT (HOISTED; runs BEFORE Phase-1 dispatch):** INV-15 quarantine row appended to `.agents/tester/QUARANTINE.md` with the D3 rationale text; pack script `test/packs/mock_job_queue_test.sh` emits QUARANTINED marker; `PACKS.md:346` FAIL no longer surfaces as operator-actionable (pack is registry-skipped).
2. INV-12 merged with verification grep table committed and Phase 3 packs PASS.
3. INV-14 docstring fix merged (or bundled with INV-12 commit).
4. **INV-13 verify-and-document + regression-pin:** Task 4.7r PASS — verification table for the three call-sites (`cancel_task → AbortTurn`, `complete_task → CompleteTurn`, `fail_task → AbortTurn(reason='failed')`) committed; `tests/unit/repositories/task/test_task_status_named_transition_wrappers.py` PASSES under Core gate; `git diff daemon/` shows ZERO production-code change (if production code DID change, STOP and route to planner via `decisions.md §D7.3-execution-log`). Task 4.8 follow-up note appended (Cycle-3 tail).
5. `git diff daemon/` reviewable in one pass — net change is INV-12 removal + INV-14 docstring (and any INV-15 quarantined-pack updates, though those are pre-flight).
6. No new quarantined tests added beyond INV-15 (success criterion #8 in `plan-overview.md`).

**Success-criterion wording for INV-13 (corrected per D7.3):** "3 call-sites VERIFIED + REGRESSION-PINNED" — NOT "3 call-sites migrated." The 15/15 inventory stays honest.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | INV-12 verification grep surfaces a non-comment consumer (the F13 `job_id` may be consumed by `_process_resume_finalize` at `daemon/services/job_feedback_observer.py:1911` — review during grep) | Medium | Medium | If surfaced → STOP, route to planner via `decisions.md §D6-supplement` amendment; do NOT silently thread the parameter or expand scope |
| 2 | **[DELETED]** INV-13 first-site ceiling blows (>4 LOC) | ~~High~~ | ~~Medium~~ | Task 4.6 is the explicit check; D2 hard-exit moves the entire INV-13 to Cycle-3 with no sunk-cost pressure. **DELETED: the ceiling check is moot — the three call-sites are already thin wrappers; no migration is needed.** |
| 3 | **[DELETED]** INV-13 migrates `complete_task` and `fail_task` to the same `CompleteTurn` but they have different transition semantics | ~~Medium~~ | ~~Low~~ | ~~Task 4.7 acceptance criterion #2 explicitly allows for a `FailTurn` named transition if the vocabulary requires it — read `daemon/services/turn_transitions.py` vocabulary before assuming.~~ **DELETED: INV-13 is verify-and-document; no migration. The discriminator is `fail_task → AbortTurn(reason='failed')` (per architect §2c), pinned by regression test.** |
| 4 | **[DELETED]** Phase 2 INV-5 merges late, blocking INV-13 indefinitely | ~~Medium~~ | ~~Medium~~ | ~~Task 4.5 sequencing gate; if Phase 2 INV-5 hasn't merged by Phase 4 start, INV-13 worker stays empty-handed — INV-12 + INV-14 + INV-15 still close.~~ **DELETED: the INV-5↔INV-13 sequencing gate is dissolved (architect §5; D7.7). INV-13 runs ∥ INV-12 + INV-14.** |
| 5 | INV-15 quarantine row format diverges from the existing `.agents/tester/QUARANTINE.md` columns | Low | Low | Pre-flight Task acceptance criterion #1 lists the exact column mapping; verify against existing rows. **NOTE:** this task is now Pre-flight, not Phase 4. |
| 6 | INV-13 regression test asserts wrapper internals (line refs, private helper call patterns) instead of pinning behavior at the public `transition._write()` seam — pins implementation not behavior | Medium | Medium | Task 4.7r acceptance criterion #5 explicitly directs the test to mock at the `transition._write()` seam and assert on the transition instance's class + kwargs (public contract). File:line refs in `repository.py` are the audit anchor but the regression test pins the public seam. |
| 7 | The redesigned INV-13 still relies on `_finalize_job_db_sync`'s signature in production code (which is unchanged by the re-scope), colliding with INV-12's removal of F13 `job_id` overloads at `_finalize_job_db_sync`'s call sites (verified safe per architect §5 — `test_observer_race1.py:91-119` mirrors the consumed positional `job_id` of `_finalize_job_db_sync`, unaffected by overload removal) | Low | Low | Phase-3 INV-9 + INV-11 packs (run before Phase 4) already exercise the same call sites; if Phase-4 INV-12 removal surfaces a regression, the Phase-3 packs fail first. Defense-in-depth via pack coverage. |

---

## Open Questions

1. **INV-12 verification grep outcome** — the F13 `job_id` parameter is documented as consumed by `_process_resume_finalize` (line 1911) for the Bug A active-orphan fallback path. D6's "REMOVE" decision may need refinement at execution time. **This is an execution-time verification question, not a phase-plan question** — Task 4.1 is the gate; if the grep surfaces `_process_resume_finalize` as a consumer, route to planner via `decisions.md §D6-supplement` before Task 4.2 begins.
2. **INV-13 reconciler test quarantine** — `test_state_machine` is quarantined (per `QUARANTINE.md` line 23) as a Cycle-1 deferral. The new regression-pin tests added in Task 4.7r may unblock un-quarantine; **defer the un-quarantine decision to Cycle-3** per Task 4.7r acceptance criterion #9.
3. **INV-15 Cycle-3 repair trigger** — `decisions.md §D3` flags the signature-change commit recovery as the trigger. No phase-plan action needed; the Cycle-3 planner picks it up.

**Resolved by amendment (no longer open):**
- "Phase 2 INV-5 merges late, blocking INV-13" — DISSOLVED (architect §5; D7.7). INV-13-as-verification runs ∥ INV-12 + INV-14; no Phase-2 INV-5 sequencing gate.
- "INV-13 first-site ceiling check" — MOOT (D7.3). The three call-sites are already thin wrappers; no migration is needed.
- "INV-13 migrate complete_task/fail_task vocabulary question" — RESOLVED (architect §2c). The correct discriminator is `fail_task → AbortTurn(reason='failed')` — a separate `FailTurn` is unnecessary. Pinned by regression test in Task 4.7r acceptance criterion #4.
