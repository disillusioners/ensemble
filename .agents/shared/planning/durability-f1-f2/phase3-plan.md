# Phase 3: Tests, Regression Gates, and Optional Demo E2E

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker
Status: Draft

> **Scope reminder.** This phase consolidates the test
> artefacts for F-1 + F-2 (the test files themselves were
> created in Phase 1 task 1.14 / 1.15 and Phase 2 task 2.11
> — the unit + integration test EXTENSIONS in
> `tests/unit/test_report_delivery_recovery_service.py` +
> `tests/postgres/test_report_delivery_recovery_pg.py` +
> `tests/integration/test_report_delivery_double_delivery_pg.py`
> + `tests/unit/services/test_report_delivery_ledger.py` —
> are being reworked by the developer in a separate
> dispatch; the obligations are encoded in `phase1-plan.md`
> + `phase2-plan.md` and bound to the real preserve SQL +
> real two-boot test per Issues 1 + 2), runs the full-dir
> pytest sweep as the final verification, and runs the
> UNCONDITIONAL F-2 demo E2E (per C-2 + the Issue-6
> single-position ruling).
>
> **Re-anchor notice.** All anchors in this phase plan are
> verified on the worktree at `18827dbd`. The R18 dev-E2E
> boot recipe referenced below lives ONLY in the main
> workdir at
> `.agents/tester/LESSONS/2026-10-04-r18-dev-e2e-boot-recipe-and-pairing-gotchas.md`
> and is read-only to this commission. The phase does NOT
> vendor the recipe into the worktree; it references it
> by path.
>
> **Repo gotchas (binding).** All NEW shell scripts in this
> phase follow the transparent-wrapper convention (per
> `decisions.md §8` and `research-findings.md §2` pack
> format). The demo E2E wrapper MUST scrub the
> `POSTGRES_*` / `ENSEMBLE_*` / `PORT` / `HOST` /
> `SSL_CERT_*` env vars per the R18 recipe.

---

## Objective

* Consolidate the test artefacts (F-1 unit file + pack; F-2
  EXTENDED existing RDRS test suites per C-2) into the
  project-level regression suite.
* Run the full-dir pytest sweep as the final verification
  gate (per the post-merge-gate lesson — never the unit
  subset alone).
* **F-2 demo E2E is UNCONDITIONAL for first landing per
  C-2 (leader call); F-1 demo stays optional per tester
  judgment.** When the F-2 demo runs, its evidence lands
  durably in the repo (not `/tmp`).
* **MANDATORY integration test on the REAL delivery seam
  (per C-2):** real service + real DB; seeded terminal
  child in straddle shape; assert a report row is actually
  minted AND the parent-wake path is exercised end-to-end.
  Mock-only F-2 tests no longer qualify as window-proof.

Acceptance: the keep-green list (per `decisions.md §8`)
stays green; the F-1 pack is green; the F-2 unit extensions
+ the MANDATORY integration test on the real seam are
green; the full-dir sweep is green; the F-2 demo E2E
(UNCONDITIONAL) lands in
`.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | (Verification gate) Run the F-1 pack from Phase 1: `bash test/packs/discard_on_startup_dependency_bus_race_unit_test.sh`. Confirm exit 0 and `RESULT: PASS`. | Phase 1 done | Pack exits 0; `RESULT: PASS`. |
| 3.2 | (Verification gate) Run the F-2 unit + integration tests from Phase 2: the EXTENDED existing RDRS suites (`tests/unit/test_report_delivery_recovery_service.py`, `tests/postgres/test_report_delivery_recovery_pg.py`, `tests/integration/test_report_delivery_double_delivery_pg.py`, `tests/unit/test_report_delivery_self_heal_zero_row.py`, `tests/job_queue/test_report_delivery_bug_family_pins.py`). The MANDATORY integration test on the REAL delivery seam (C-2) MUST be green. Confirm exit 0 and `RESULT: PASS` on each pack. | Phase 2 done | All packs exit 0; `RESULT: PASS`; integration test on real seam passes. |
| 3.3 | (Verification gate) Run the keep-green pack from `decisions.md §8`: `bash test/packs/auto_continue_boot_pass_unit_test.sh`. Confirm exit 0 and `RESULT: PASS`. The pack must remain green through the F-1 + F-2 changes. | Phase 1 done, Phase 2 done | Pack exits 0; `RESULT: PASS`. |
| 3.4 | (Verification gate) Run the full-dir pytest sweep at the project level: `.venv/bin/pytest tests/ -q --tb=line` (or the project's standard full-dir invocation — verify with `ls test/packs/` for any pre-existing "full" packs). The sweep includes the four auto-continue unit files + the EXTENDED RDRS suites + the F-1 unit file + everything else under `tests/`. The result is recorded in the post-merge-gate file (see 3.8). | 3.1, 3.2, 3.3 | Sweep is green; zero new failures vs. baseline; baseline-counted failures (the pre-existing 5-test failure family on base, per the R18 recipe) are explicitly noted. |
| 3.5 | (Mandatory for F-2) Run the MANDATORY F-2 integration test on the REAL delivery seam: a seeded terminal child in straddle shape, asserting a report row is actually minted AND the parent-wake path is exercised end-to-end. The test EXTENDS the existing `tests/integration/test_report_delivery_double_delivery_pg.py` (or a new sibling if the existing test's scope is incompatible). Mock-only F-2 tests no longer qualify as window-proof per C-2. | 3.2 | Integration test passes against the real PG delivery seam; the test code is in the test file inventory. |
| 3.6 | **UNCONDITIONAL F-2 demo E2E (per C-2 leader call).** Execute the F-2 demo E2E. The wrapper script `/tmp/<tag>-wc-wedge-demo-boot.sh` scrubs `POSTGRES_*` / `ENSEMBLE_*` / `PORT` / `HOST` / `SSL_CERT_*` and sources `/home/nea/dev-daemon-8079-v0.16.11/boot.env` per the R18 recipe (read-only to this commission); `cd <worktree>`; `exec .venv/bin/python -m daemon`. The dev boot.env in the R18 recipe uses `QUEUE_DISCARD_ON_STARTUP=true` which directly exercises the wipe lane. The F-2 control legs from the brief are: (i) RDRS lane-2 kill-switch OFF → wedge persists; (ii) RDRS lane-2 kill-switch ON → wedge heals; (iii) non-straddle restart still passes (no regression). The demo kill-switch-OFF leg overlaps intentionally with the S26 unit test (per the W-5 🟢 suggestion — demo leg and unit test cover the same control from different angles). | 3.5 | The wrapper script runs; the dev daemon boots; the per-leg results are captured (next task). |
| 3.7 | Capture the F-2 demo E2E evidence: (i) copy the boot logs from `~/ac-durability-f1f2-demo/logs/` into `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/logs/` (so they survive the `/tmp` sweep); (ii) write a short `findings.md` listing the per-leg result (anchor-less child admitted / already-reported skip / RUNNING-children skip / multi-child mixed / run-twice idempotent / lane-2 kill-switch OFF / lane-2 kill-switch ON / non-straddle restart control); (iii) write a short `recipe-pointer.md` referencing the R18 recipe by path (read-only) so the next commission can re-run. | 3.6 | All three artefacts are present in `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`. |
| 3.8 | (Documentation) Update the post-merge-gate file
`.agents/tester/RESULTS/2026-10-04-durability-f1f2-merge-gate.md` with the per-pack + full-dir results, the integration test outcome, the F-2 demo results, and (if the F-1 demo ran) a one-line pointer to the F-1 EVIDENCE. The file follows the same shape as
`.agents/tester/RESULTS/2026-10-04-auto-continue-merge-gate.md` (125 lines, tracked, with the per-leg findings, the §9 follow-up items, and the T5.4c-style disclosure if any evidence is volatile). | 3.4, 3.5, 3.6, 3.7 | The merge-gate file is present; the per-pack results, integration test outcome, F-2 demo results, and (if applicable) the F-1 EVIDENCE pointer are recorded; any volatile evidence is disclosed. |

**Total Phase 3 tasks: 8** (no conditional tasks in this
cycle). Tasks 3.1-3.4 are verification gates; 3.5 is the
MANDATORY F-2 integration test on the real seam; 3.6, 3.7
are the UNCONDITIONAL F-2 demo E2E; 3.8 is documentation.
The F-1 demo E2E is OPTIONAL per tester judgment (no
required task in this cycle).

---

## Coupling

* **Loose with Phase 1 + Phase 2:** Phase 3 only references
  Phase 1 and Phase 2 test files and pack conventions; it
  does not touch production code. A regression in Phase 1
  or Phase 2 surfaces in Phase 3's verification gates
  (3.1, 3.2, 3.4), not in Phase 3 itself.
* **Loose with the auto-continue feature:** the keep-green
  list (per `decisions.md §8`) is the cross-feature
  regression gate; Phase 3 task 3.3 is the explicit
  keep-green run.
* **Independent of:** the F-2 design (a) follow-up
  (`decisions.md §3a`) — the S9 xfail marker (if design (a)
  is deferred) does not affect Phase 3's acceptance.

---

## Risks (Phase 3-specific; see `plan-overview.md` for the consolidated register)

* **R-P3-1: the full-dir pytest sweep exceeds 300s (per the
  R18 recipe gotcha).** **Mitigation:** the sweep is run
  as a background process via `proc_run` (or `nohup` /
  `setsid`) with a 600s cap; the foreground shell waits
  for the result. The post-merge-gate file records the
  wall-clock time.
* **R-P3-2: the demo E2E boots a dev daemon that races with
  the production daemon on a shared port.** **Mitigation:**
  the R18 recipe's `PORT=8079` (or per-tag variant) is
  used; the wrapper scrubs `PORT` / `HOST` /
  `SSL_CERT_*`; the 4-point isolation evidence (factory
  engine line, `/livez` version, `/readyz` components,
  `/proc/<pid>/environ`) is captured in the boot log
  copy. The revert procedure is: kill the `ss`-verified
  pid → re-source `boot.env` → `bash boot.sh` → `livez`. **N/A note (per the reviewer's 🟢 suggestion):** the R18 recipe is referenced ONLY for the demo harness (task 3.6 + the E2E scenario design below). The R18-context anchor is **N/A for the F-1 / F-2 boot-sequence coupling** (the recipe is a dev-daemon bootstrap pattern, not a coupling surface).
  engine line, `/livez` version, `/readyz` components,
  `/proc/<pid>/environ`) is captured in the boot log
  copy. The revert procedure is: kill the `ss`-verified
  pid → re-source `boot.env` → `bash boot.sh` → `livez`.
* **R-P3-3: the demo E2E evidence is volatile (`/tmp` sweep
  gotcha — the T5.4c PARTIAL disclosure from the
  auto-continue merge gate).** **Mitigation:** task 3.7
  copies the boot logs into
  `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`
  immediately after each leg; the EVIDENCE directory is
  inside the repo (committed alongside the feature
  branch).
* **R-P3-4: the post-merge-gate file is mis-formatted
  relative to the auto-continue precedent.** **Mitigation:**
  task 3.8 references the auto-continue merge-gate file by
  path and explicitly notes the shape to follow; the
  file is reviewed against the precedent before commit.
* **R-P3-5: the trigger evaluation mis-classifies the
  straddle as "proven" when the unit tests are not
  deterministic.** **Mitigation:** task 3.5 requires
  three consecutive local runs of the F-2 unit pack with
  deterministic results; if any run is flaky, the demo E2E
  is run regardless of the unit evidence.

---

## Exit Criterion

This phase is DONE when:

* All 8 tasks (3.1-3.8) are complete and each task's
  acceptance criterion is met.
* The keep-green list (per `decisions.md §8`) is green
  (task 3.3).
* The F-1 + F-2 packs are green (tasks 3.1, 3.2).
* The full-dir pytest sweep is green (task 3.4).
* The F-2 demo E2E runs UNCONDITIONALLY (per C-2;
  tasks 3.6, 3.7) and lands durable evidence in
  `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`.
  See the "Demo E2E Trigger Condition" section below
  (and `decisions.md §9`) for the UNCONDITIONAL framing
  + the F-1 demo's OPTIONAL framing + the Issue-2
  two-boot-test satisfiable skip condition.
* The post-merge-gate file is present and complete (task
  3.8).

The phase is independently revertable: delete the
EVIDENCE directory (the F-2 demo's per-leg results);
delete the merge-gate file; revert the per-pack
`RESULT: PASS` lines in the merge-gate file. No
production code touched (Phase 3 only RUNS the
F-1 + F-2 unit/integration tests created in Phase 1
+ Phase 2; no new test files created in Phase 3).

---

## File Inventory (Phase 3)

| Path | Change | Verified anchor |
|---|---|---|
| `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/` | UNCONDITIONAL F-2 demo E2E artifact directory. Created by tasks 3.6 / 3.7; the F-1 demo E2E is OPTIONAL but uses the same directory. Contains: `logs/` (boot logs, F-2 mandatory), `findings.md` (per-leg findings, F-2 mandatory), `recipe-pointer.md` (R18 recipe pointer, F-2 mandatory). | new dir |
| `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/logs/` | UNCONDITIONAL — boot logs (F-2 demo runs on every F-2 landing per C-2). | new dir |
| `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/findings.md` | UNCONDITIONAL — per-leg findings (F-2 demo runs on every F-2 landing per C-2). | new file |
| `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/recipe-pointer.md` | UNCONDITIONAL — recipe pointer (F-2 demo runs on every F-2 landing per C-2). | new file |
| `.agents/tester/RESULTS/2026-10-04-durability-f1f2-merge-gate.md` | Task 3.8 — NEW FILE. Per-pack + full-dir results + UNCONDITIONAL F-2 demo results + (if applicable) the F-1 EVIDENCE pointer. | new file |
| `/tmp/<tag>-wc-wedge-demo-boot.sh` | UNCONDITIONAL — NEW FILE (outside the worktree, in `/tmp` per the R18 convention; created by the F-2 demo run). | new file (outside worktree) |

**No changes to:** the four auto-continue unit files + two
packs (regression gates only); the F-1 + F-2 unit files
created in Phase 1 / Phase 2 (developer is reworking them
in a separate dispatch; Phase 3 only runs them).

---

## E2E Scenario Design (F-2, REVISION CYCLE 2 — RDRS lane-2 extension, per the W-4 PIVOT)

The F-2 scenario-4 mirror from the commission's brief is
the primary demo leg. The scenario (revised for the W-4
pivot):

1. **Pre-condition.** A parent instance is in
   `status='waiting_children'`; a child has just completed
   in `status='completed'`; the child has a surviving
   checkpoint with a non-empty last assistant message;
   the child's wake row was supposed to be enqueued but the
   daemon is killed before the wake task is claimed.

2. **Setup.** Start the dev daemon on the worktree; create
   the parent + child instances via the normal spawn path
   (`/spawn` or equivalent test harness); let the child
   run to completion; immediately before the wake task
   claims, kill the daemon (`kill -TERM <pid>`).

3. **Boot.** Restart the dev daemon. The
   `QUEUE_DISCARD_ON_STARTUP=true` setting in the dev
   boot.env directly exercises the wipe lane.

4. **Observation.** After boot, observe:
   * The parent is no longer in `status='waiting_children'`
     (it has been moved to `status='running'` or
     `status='completed'` by the RDRS lane-2 chain
     re-invocation).
   * The parent history contains exactly ONE
     `internal_report:<child>` injection (the
     synthesized one).
   * The child is NOT re-executed (its checkpoint is
     unchanged from the pre-restart state).
   * The boot log carries an `[RDRS_LANE2]` marker with
     the result counters.
   * Zero manual pings were sent to the user.

5. **Control legs.**
   * **RDRS lane-2 kill-switch OFF** (per
     `config.py:1302-1378`): the parent remains wedged in
     `status='waiting_children'`; the boot log carries
     `lane_2_skipped=1`; on the next boot with the switch
     ON, the parent heals (regression check).
   * **Non-straddle restart (no waiting_children parent
     with a straddled child):** the lane-2 query runs but
     the result counter is 0; the parent count of
     `waiting_children` is 0; no regression (regression
     check).
   * **Run-twice (lane-2 idempotent):** restart the daemon
     a second time (no new wedge); the lane-2 query
     produces zero new work (the obligation-triple unique
     index enforces idempotency). The
     `ensure_deferred` counter is unchanged.

6. **Multi-child mixed (regression check for the
   filter):** a parent with three children (anchor-less
   straddle, RUNNING, already-delivered). RDRS lane 2
   processes only the straddle child; the
   `skipped_already_reported` and the RUNNING-exclusion
   counters each increment by 1.

7. **🟢 S15 name collision fix (per C-2).** The
   `TestSweepParentHistoryRaceSkip` test (renamed from the
   prior cycle's S15 in the unit matrix; the plan-
   overview.md S15 keep-green is a different concept and
   stays as-is) covers the race scenario: between the
   lane-2 scan and the per-row pass's PREFIX-ledger
   re-check, a live delivery inserts a fresh
   `internal_report:<child>` message into the parent's
   `MessageQueue` (or stamps a new `source` on the
   parent's checkpoint); the lane-2 per-row pass MUST
   skip (increment `skipped_already_reported`) and NOT
   double-inject. The S15 (unit matrix) is now the
   renamed test in the EXTENDED existing RDRS suites.

The `findings.md` artefact (task 3.7) records the
observation + control leg results in a per-leg table.

---

## Demo E2E Trigger Condition (REVISION CYCLE 2)

**F-2 demo E2E is UNCONDITIONAL for first landing per C-2
(leader call).** The trigger is not "unit evidence leaves
W-A / W-B / W-C unproven" — the trigger is "F-2 ships". The
demo runs on every F-2 landing; the evidence lands in
`.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`
(per the unconditional status under the W-4 pivot).
**There is no F-2 demo "trigger" to satisfy** — the demo
runs regardless of unit-test outcomes.

**F-1 demo E2E remains OPTIONAL per tester judgment.** The
skip condition (when the F-1 demo can be skipped) is now
**satisfiable via the Issue-2 real two-boot test** (a real
file-backed-SQLite two-boot test using the F9-parity harness
`tests/unit/repositories/test_task_auto_continued_lifecycle.py`):
* the two-boot test exercises real `clear_all` SQL
  (both repositories, including the arm-3 `EXISTS
  instances` join + kill-switch gating) on a real DB;
* the two-boot test exercises the double-restart probe
  (candidates==1, already_resuming==0, no double-continue,
  boot-pass CAS stamp actually invoked);
* the two-boot test satisfies the C-2 evidence bar for the
  F-1 wipe seam.

**F-1 demo evidence bar (if the demo IS exercised):** the
boot logs + per-leg findings in the EVIDENCE directory +
the Issue-2 two-boot test as the unit-level window-proof.

The F-2 integration test (task 3.5) is the deterministic
window-proof; the F-2 demo E2E (task 3.6) is the
real-world evidence. The integration test runs on every
F-2 commit; the demo E2E runs on every F-2 landing. The
two are complementary, not redundant.

## F-1 Demo (if exercised) — Trigger Inventory (RESIDUAL from prior cycle)

The criteria below apply ONLY to the F-1 demo if it is
exercised (OPTIONAL per C-2). They are the residual
trigger language from the prior cycle; the F-2 demo is
unconditional and has no trigger. The inventory references
the F-1 S-criteria (S1-S6 + S7 kill-switch) which remain
the live F-1 inventory.

The criteria for "proven end-to-end" (sufficient to skip
the F-1 demo):

* All six core F-1 unit tests (S1, S2, S3, S4, S5, S6)
  pass deterministically on three consecutive local runs.
* The F-1 kill-switch test (S7 per W-3 — `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`)
  passes.
* No F-1 test is marked `xfail`.
* The double-restart probe test (S1) exercises the
  F-1 wedge scenario end-to-end.

The F-2 inventory (UNCONDITIONAL — runs regardless) is
documented separately in task 3.6 (per-leg results) +
task 3.7 (evidence capture). The F-2 success criteria
S21-S28 (in `plan-overview.md`) are the live F-2 inventory;
the SUPERSEDED S7-S14 from prior cycles are NOT in the
F-2 demo's trigger inventory.

If the F-1 demo is exercised, the evidence bar is the
per-leg table in `findings.md` plus the boot log copies
in `logs/`.
`findings.md` plus the boot log copies in `logs/`.
