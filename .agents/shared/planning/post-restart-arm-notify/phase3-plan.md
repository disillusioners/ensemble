# Phase 3: Terminal-State Gating + Edge Cases + Structural AC6 Tests

> **⛔ HARD CONSTRAINT (inherited from Phase 2, governs every task below):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**ADR basis:** ADR-042 (Terminal-state gating & abandonment policy), ADR-043 (Edge cases & coalescing — front-door ari fall-back, coalesce by arming_instance_id, bounded coalesce, one-shot delivery), ADR-044 (Kill-switch & boot-never-wedge). Cross-cutting invariants 1, 2, 3, 4, 6, 7, 12 of `architecture-recommendation.md` §8 are asserted by this phase (1/2/3/4 are the AC6 structural non-regressions; 6/7/12 are the edge-case guarantees).

**Scope of environment:** all implementation work targets **demo** (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) and **sandboxes** (own port + throwaway PG). Live target paths exist in the scripts behind guards but their execution is **USER-GATED** and never performed by this initiative.

**Convention note:** any new Python module this phase adds uses `from __future__ import annotations` for Python 3.13 import safety. The structural tests (T6.1–T6.3) are run against the merged code, not the per-commit diff — they enforce the AC6 "no new file / no new endpoint / no new table" structural invariants at the integration level.

---

## Objective

Close the long-tail edge cases that Phase 2 deferred: the
**long-downtime double-arm** scenario (two arms in a single
daemon lifecycle, coalesced to one wake), the **paused instance
defer** (the wake is delivered but the `Task` is held `PENDING`
until the instance resumes — the existing claim-side pause
gate), and the **terminal-instance revival** (the wake's
`enqueue_message` triggers the existing terminal→RUNNING flip
for an instance in `COMPLETED` / `TERMINATED` / `ERROR` /
`FAILED`). Also add the **structural tests** that enforce AC6
(no new file, no new HTTP endpoint, no new SQLModel table) at
the integration level — these are the load-bearing "we did
not silently add a parallel subsystem" assertions.

**Exit in one sentence:** on demo, two arms in a single daemon
lifecycle coalesce to ONE wake with a run-list; a wake for a
`PAUSED` instance delivers the `MessageQueue` row but holds
the `Task` until resume; a wake for a `COMPLETED` instance
revives the instance to `RUNNING` and delivers; and the
integration-level structural tests assert the journal file
list, the HTTP router URL list, and the SQLModel metadata
table list are all UNCHANGED by this feature — all proven by
the new test pack (T5.13, T5.14, T5.15, T6.1, T6.2, T6.3) at
100% green.

---

## Verified Starting Point (do not re-derive)

- **The `PENDING`-exempt claim gate:** `instance_messaging.py:2154-2161`
  and `:1925-1934` are the existing gates that hold a `Task` in
  `PENDING` until a `PAUSED` instance resumes. The wake's
  `enqueue_message` rides the same gate; no change is required
  (R-15 in `risk-register.md`).
- **The terminal-revive semantics:** `instance_messaging.py:1954-1976`
  is the existing `send_message` path that auto-flips a
  `COMPLETED` / `TERMINATED` / `ERROR` / `FAILED` instance to
  `RUNNING`. The wake's `enqueue_message` is on the same path;
  no change is required. The wake's `source` re-stamp is
  preserved across the revive (D-FA3.3).
- **The structural surfaces to assert:**
  - The journal file list at `<install_dir>/releases/` is exactly
    `{current, previous, state.json, ...}` — the wake's
    `pending_wakes` is a JSON key on `state.json`, not a new file
    (D-FA1.1 / D-FA1.3 explicit rejection of `pending_actions.json` /
    `wake_records.json` parallels).
  - The HTTP router at `daemon/api.py` exposes a fixed set of
    URL prefixes; the wake does not add a new prefix
    (D-FA3.1 explicit rejection of a new wake endpoint).
  - The SQLModel metadata in `daemon/persistence.py` /
    `daemon/migrations/` declares a fixed set of tables; the
    wake does not add a new table (D-FA1.1 explicit rejection
    of `arm_wake_records`).
- **The drill precedent:** Phase 2's restart/upgrade drill
  (`test/drills/p21_upgrade_pipeline_drill.sh`) is the existing
  shape; the wake drill is a variant following the same bash
  convention. The wake drill is **authored in Phase 4** (drill
  execution is a docs + runbook concern); Phase 3 only adds the
  test harness for the edge cases (the drill is the
  end-to-end counterpart of these unit tests).
- **The `pending_wakes` dict bounds:** a healthy daemon with
  N arms in a single lifecycle produces N wake records, all
  coalesced at delivery time (T5.13). A daemon that never
  reboots never fires the wake sweep; the records sit in the
  journal until the next boot. The `abandon_after` clock
  (`expires_at + 600s` default) bounds the surface
  (D-FA4.1, ADR-042, R-5 / R-9 / R-12 mitigations).

---

## Design Decisions (this phase)

**D1 — Long-downtime double-arm coalesces to ONE wake (D-FA5.2
+ R-2, ADR-043).** Two arms in a single daemon lifecycle (arm
A, restart, arm B, restart, arm C — the boot pass after the
second restart sees two `pending_wakes` records) coalesce to ONE
wake with a run-list payload, delivered to the same arming
instance (assuming the arms were from the same instance — the
common case for a long-running Ari session). The run-list is
newest-first by `armed_at`. The user's mental model is "what
happened during downtime" not "deliver N separate
notifications" (D-FA5.2 explicit decision).

**D2 — Wake for a PAUSED instance: delivered but held (D-FA3.1
+ R-15).** The wake's `enqueue_message` creates the
`MessageQueue` row (the wake is "delivered" in the sweep's
sense); the `Task` is held `PENDING` by the existing claim-side
pause gate (`instance_messaging.py:2154-2161, :1925-1934`) until
the operator resumes the instance. The wake record is removed
from the dict on the structural removal pass (T7 step (h) in
Phase 2). When the instance resumes, the held `Task` is
claimed and the agent's first turn runs. **Accepted** —
pausing an instance is an explicit operator action; the wake
deferral is consistent with every other wake in the system.

**D3 — Wake for a terminal instance revives it (D-FA3.1 + R-15
complement).** The wake's `enqueue_message` triggers the
existing terminal→RUNNING flip
(`instance_messaging.py:1954-1976`). The wake is delivered to
the revived instance. The `source` re-stamp is preserved
across the revive (D-FA3.3). **The arming instance's identity
is the durable `instance_id` (a UUID row) — the wake is
target-scoped, not agent-scoped, so a co-deployed agent
swap is not a wake concern (R-19 mitigation).**

**D4 — AC6 structural tests assert "no parallel machinery" at
the integration level (D-FA6.1, invariants 1/2/3/4).** Three
tests:

- **T6.1** enumerates `<install_dir>/releases/` after a
  `system_restart` arm + a boot sweep, and asserts the file
  list is exactly the pre-feature list (no new file).
- **T6.2** enumerates the HTTP router's URL prefixes
  (`daemon/api.py`'s union of all router prefixes) and asserts
  the list does NOT include any `/post-restart-arm-notify` or
  `/wake` prefix.
- **T6.3** enumerates the SQLModel metadata's table list and
  asserts the list does NOT include `arm_wake_records` or any
  new table for the wake.

These tests are the load-bearing "the architect did not slip a
parallel subsystem in" assertions. A regression in any of them
is a release blocker — the AC6 guarantee is structural, not
behavioral.

**D5 — The edge-case tests are test-strategy.md T5.13, T5.14,
T5.15 (AC5 — additional); the structural tests are T6.1, T6.2,
T6.3 (AC6).** No new code in Phase 3; the tests ride the
existing Phase 1 + Phase 2 surface. The "additional" edge cases
(long-downtime, paused, revival) are not in Phase 2 because
they require multi-record journal state and instance-state
transitions that are best exercised in a dedicated test pack
following the `tests/job_queue/` convention
(`test_a4_f14_orphan_detection.py` for orphans, `test_dead_letter_*`
for dead-letter recovery).

**D6 — The kill-switch is operator opt-out, not default-off
(D-FA6.2, ADR-044, invariant 11).** The sweep's `_is_enabled()`
short-circuits the entire wake branch. The arm-side write is
also gated on the same env (Phase 1). A future T5.11-style
test for the kill-switch on the boot-pass call site (T9 in
Phase 2) is already GREEN — Phase 3 does not duplicate it. The
Phase 3 tests are additive.

---

## Components (file-level touch list)

### Source files modified

**None.** Phase 3 is a test-only phase — the long-downtime
double-arm, the paused-instance defer, and the terminal-revival
all ride the existing Phase 1 + Phase 2 surface. The coalesce
helper (`_coalesce_wakes` from Phase 2 T4) handles the
long-downtime case; the existing claim-side pause gate
(`instance_messaging.py:2154-2161, :1925-1934`) handles the
paused-instance defer; the existing terminal→RUNNING flip
(`instance_messaging.py:1954-1976`) handles the revival. The
structural tests assert AC6 surface invariants without
modifying any source.

### Test files created

| File | What it covers | Test-strategy case IDs |
|---|---|---|
| `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` | Long-downtime double-arm (two arms in a single daemon lifecycle coalesce to ONE wake); paused-instance defer (wake delivered, `Task` held `PENDING` until resume); terminal-instance revival (wake's `enqueue_message` revives `COMPLETED` / `TERMINATED` / `ERROR` / `FAILED` to `RUNNING` and delivers). Convention precedent: `tests/job_queue/test_a4_f14_orphan_detection.py` and `tests/job_queue/test_dead_letter_*` | T5.13, T5.14, T5.15 |
| `tests/unit/test_post_restart_arm_notify_no_parallel.py` | Structural AC6: no new file in `<install_dir>/releases/`; no new HTTP endpoint; no new SQLModel table. The tests enumerate the respective surfaces and assert the pre-feature baseline. Convention precedent: the existing `tests/unit/test_p21_release_journal_no_extra_files.py` shape (the Phase 2 file-list assertion) — adapt the same pattern | T6.1, T6.2, T6.3 |

### Files NOT touched (explicit non-modification)

- `daemon/instance_messaging.py` — no change. The wake reuses
  the existing pause-gate and terminal-revive semantics.
- `daemon/api.py` URL router — no new HTTP endpoint
  (invariant 3). The T6.2 test asserts the router's URL
  prefix list is UNCHANGED.
- `daemon/persistence.py` / `daemon/migrations/` — no new
  SQLModel table (invariant 4). The T6.3 test asserts the
  metadata's table list is UNCHANGED.
- `daemon/tools/upgrade_journal.py` — Phase 1's surface is
  the only durable surface; Phase 3 tests consume it.
- `daemon/services/upgrade_journal_sweep.py` — Phase 2's
  surface handles the coalesce internally (`_coalesce_wakes`).
  Phase 3 tests exercise the coalesce on a multi-record
  journal.
- `scripts/upgrade/*` — the executor is unchanged; the wake
  is a daemon concern.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Add long-downtime double-arm test (T5.13) to `tests/job_queue/test_post_restart_arm_notify_edge_cases.py`** — two arms in a single daemon lifecycle: arm A (run_id `r-A`, `armed_at=t1`), restart, arm B (run_id `r-B`, `armed_at=t2`), restart. The journal after the second restart has 2 `pending_wakes` entries. The boot pass coalesces them into ONE wake with a run-list payload, delivered to the same arming instance (the test sets the same `arming_instance_id` for both). Assertions: (a) the `enqueue_message` `AsyncMock` is called exactly ONCE for the arming instance; (b) the message body is the coalesced body, with both `run_id`s in the run-list; (c) the body is newest-first (B then A); (d) the `pending_wakes` dict is empty post-delivery. Convention precedent: `tests/job_queue/test_a4_f14_orphan_detection.py` (multi-record journal state) | Phase 1 + Phase 2 GREEN | `pytest tests/job_queue/test_post_restart_arm_notify_edge_cases.py::test_sweep_handles_two_arms_in_long_downtime -v` exits 0; the assertion messages reference AC5 + D-FA5.2 + ADR-043 |
| **T2** | **Add paused-instance defer test (T5.14) to the same file** — set up a journal with a `pending` wake for an arming instance whose `InstanceManager` mock returns the instance in `PAUSED` state. The sweep's `_deliver_wake` calls `enqueue_message`; the mock's `enqueue_message` simulates the existing claim-side pause gate by returning a fake `message_id` and creating a `MessageQueue` row + a `Task` in `PENDING`. Assertions: (a) `enqueue_message` is called; (b) the `MessageQueue` row is created (the wake is "delivered"); (c) the `Task` is in `PENDING` (the existing pause gate held it); (d) the `pending_wakes` record is `delivered` (the sweep marks it so on the structural removal pass); (e) on a subsequent instance `resume`, the held `Task` is claimed and the agent's first turn runs (the test asserts the task is no longer `PENDING` after resume). Convention precedent: the existing `test_pause_claim_gate_*` tests in `tests/unit/services/test_pause_resume_seam.py` | Phase 1 + Phase 2 GREEN | Test GREEN; assertion messages reference AC5 + R-15 |
| **T3** | **Add terminal-instance revival test (T5.15) to the same file** — set up a journal with a `pending` wake for an arming instance whose `InstanceManager` mock returns the instance in `COMPLETED` (or `TERMINATED` / `ERROR` / `FAILED` — parametrized). The sweep's `_deliver_wake` calls `enqueue_message`; the mock's `enqueue_message` simulates the existing terminal→RUNNING flip (`instance_messaging.py:1954-1976`) by changing the instance's state to `RUNNING` and creating the `MessageQueue` row. Assertions: (a) `enqueue_message` is called; (b) the instance state is now `RUNNING` (the revive fired); (c) the `MessageQueue` row is created (the wake is delivered to the revived instance); (d) the `source` is the recorded source (the re-stamp survived the revive — D-FA3.3); (e) the `pending_wakes` record is `delivered`. Convention precedent: the existing `test_revive_*` tests in `tests/unit/services/test_send_message_revives.py` | Phase 1 + Phase 2 GREEN | Test GREEN for each of the four terminal states (parametrized); assertion messages reference AC5 + D-FA3.1 |
| **T4** | **Add no-new-file structural test (T6.1) to `tests/unit/test_post_restart_arm_notify_no_parallel.py`** — at the integration level, enumerate the `<install_dir>/releases/` directory after a `system_restart` arm + a boot sweep (the integration harness uses a `tmp_path` install dir; no live contact). Assert the file list is exactly the pre-feature baseline: `current` (symlink) + `previous` (symlink) + `state.json` + (optional) `manifest.json` for the staged release. The wake's `pending_wakes` is a JSON key on `state.json`, not a new file. The test imports the pre-feature baseline from a constant (captured at PR-time — the test runner reads the pre-PR tree, not the working tree, to avoid self-reference) | Phase 1 + Phase 2 GREEN | Test GREEN; a regression that adds a new file (e.g. a leaked `wake_records.json`) FAILS the test loudly |
| **T5** | **Add no-new-endpoint structural test (T6.2) to the same file** — at the integration level, enumerate the HTTP router's URL prefixes (the union of all `APIRouter` includes in `daemon/api.py`). Assert the list does NOT include any `/post-restart-arm-notify`, `/wake`, `/arm-notify`, or `/pending-wakes` prefix. The test imports the router list dynamically (the api.py lifespan imports the routers) and compares to a pre-feature baseline. The pre-feature baseline is the union at the pre-PR tip — captured at PR-time by `git show origin/HEAD:daemon/api.py | grep -E "@.*router\."` (or the equivalent static scan) | Phase 1 + Phase 2 GREEN | Test GREEN; a regression that adds a new endpoint (e.g. a leaked `/wake` route) FAILS the test loudly |
| **T6** | **Add no-new-table structural test (T6.3) to the same file** — at the integration level, enumerate the SQLModel metadata's table list (`SQLModel.metadata.tables.keys()`). Assert the list does NOT include `arm_wake_records`, `pending_wake`, `wake`, or any new table for the wake. The pre-feature baseline is the table list at the pre-PR tip — captured at PR-time by `git show origin/HEAD:daemon/persistence.py | grep -E "class.*Table"` (or the equivalent static scan). The test uses a sandbox DB (the test's `conftest` provides a `tmp_path` SQLite fixture) | Phase 1 + Phase 2 GREEN | Test GREEN; a regression that adds a new table (e.g. a leaked `arm_wake_records`) FAILS the test loudly |
| **T7** | **Non-regression check: full Phase 1 + Phase 2 packs remain green** — Phase 3 adds tests but does not modify any source. The new test files (T1, T2, T3) use the same `tmp_path` + `monkeypatch` + `AsyncMock` fixture pattern as Phase 1 + Phase 2. The structural tests (T4, T5, T6) use the same `tmp_path` + sandbox-DB fixture pattern. None of the new tests touch the source files | T1–T6 | `pytest tests/unit/tools/test_post_restart_arm_notify_journal.py tests/unit/services/test_post_restart_arm_notify_sweep.py tests/job_queue/test_post_restart_arm_notify_routing.py tests/job_queue/test_post_restart_arm_notify_edge_cases.py tests/unit/test_post_restart_arm_notify_no_parallel.py -v` exits 0 with all T1.* + T2.* + T3.* + T4.* + T5.1–T5.15 + T6.1–T6.3 GREEN |
| **T8** | **Drill integration (test-only) — add a fixture-only invocation in `test/drills/post_restart_arm_notify_drill.sh` SKETCH** — the drill is authored in Phase 4 (the runbook is a docs + runbook concern); Phase 3 only adds a `pytest`-level drill smoke that the bash drill's contract is reachable. The smoke invokes the bash drill in a sandbox install dir and asserts the bash exit code is `0` (per the Phase 2 drill convention in `test-strategy.md` §3.2). The drill body is a Phase 4 deliverable; Phase 3's smoke is a placeholder that exits SKIPPED until Phase 4 lands | T1–T6 | Smoke is registered (the `conftest` sees the path); on Phase 4 land, the smoke becomes GREEN |

---

## Coupling

- **Tight with Phase 1 (ADR-039)** — the long-downtime double-arm
  test (T1) consumes the `pending_wakes` dict that Phase 1
  defines; the structural test T4 asserts the dict is on the
  existing `state.json` file, not a new file.
- **Tight with Phase 2 (ADR-040 + ADR-043)** — the
  `_coalesce_wakes` helper from Phase 2 T4 is the coalesce
  primitive; the long-downtime test exercises it on a
  multi-record journal. The paused-instance defer test
  exercises the existing claim-side pause gate via the
  Phase 2 `_deliver_wake` entry point. The terminal-revival
  test exercises the existing terminal→RUNNING flip via the
  same entry point.
- **Tight with the existing claim-side pause gate
  (`instance_messaging.py:2154-2161, :1925-1934`)** — the
  paused-instance defer test (T2) is the wake's
  characterization of the existing gate. If the gate's
  semantics change in a future PR, the test must follow.
- **Tight with the existing terminal-revive semantics
  (`instance_messaging.py:1954-1976`)** — the
  terminal-revival test (T3) is the wake's characterization
  of the existing flip. If the flip's semantics change, the
  test must follow.
- **Tight with the AC6 structural surfaces (D-FA1.1,
  D-FA3.1, D-FA6.1; invariants 1/2/3/4)** — the three
  structural tests (T4, T5, T6) are the load-bearing
  "no parallel subsystem" assertions. If a future PR
  legitimately needs a new file / endpoint / table for the
  wake (the architect should reject this; D-FA1.1 is
  unanimous), the corresponding test must be updated WITH
  the PR's reasoning — a silent relaxation is a release
  blocker.
- **Loose with the drill (`test/drills/post_restart_arm_notify_drill.sh`)**
  — the drill is the bash counterpart of these tests; Phase 3
  only adds a smoke placeholder. Phase 4 authors the drill.

---

## Per-Phase Verification (test-strategy.md mapping)

| Test ID | Description | Where | Verifies |
|---|---|---|---|
| **T5.13** | Long-downtime double-arm: 2 arms in single lifecycle coalesce to 1 wake with run-list, newest-first | `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` | AC5 + D-FA5.2 + ADR-043; R-2 mitigation |
| **T5.14** | Paused instance defer: wake delivered (MessageQueue row created), `Task` held `PENDING` until resume; resume drains the held task | same | AC5 + D-FA3.1; R-15 |
| **T5.15** | Terminal instance revival: wake's `enqueue_message` triggers terminal→RUNNING flip for `COMPLETED` / `TERMINATED` / `ERROR` / `FAILED` (parametrized); wake delivered to revived instance with the re-stamped source | same | AC5 + D-FA3.1 |
| **T6.1** | No new file: `<install_dir>/releases/` file list is the pre-feature baseline after a wake arm + boot sweep | `tests/unit/test_post_restart_arm_notify_no_parallel.py` | AC6 + D-FA1.1 / D-FA1.3 + invariant 1; release-blocker regression pin |
| **T6.2** | No new HTTP endpoint: router URL prefix list is the pre-feature baseline (no `/post-restart-arm-notify`, `/wake`, `/arm-notify`, `/pending-wakes`) | same | AC6 + D-FA3.1 + invariant 3; release-blocker regression pin |
| **T6.3** | No new SQLModel table: metadata table list is the pre-feature baseline (no `arm_wake_records`, `pending_wake`, `wake`) | same | AC6 + D-FA1.1 + invariant 4; release-blocker regression pin |

**Pre-Phase-3 baseline:** the Phase 1 pack at
`tests/unit/tools/test_post_restart_arm_notify_journal.py` (T1.* +
T4.1–T4.6 + T5.12) + the Phase 2 packs at
`tests/unit/services/test_post_restart_arm_notify_sweep.py` (T2.*
+ T5.1–T5.11) + `tests/job_queue/test_post_restart_arm_notify_routing.py`
(T3.*) remain green (T7).
**Post-Phase-3 invariant:** every new test docstring carries the
AC + ADR-042/043/044 reference (test-strategy.md §4.5).

---

## Risks (phase-specific — full register: sibling `risk-register.md`)

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R3.1 | The structural test's "pre-feature baseline" is captured at PR-time — a future PR that adds a new file / endpoint / table to the wake surface (rejected by D-FA1.1 / D-FA3.1 but architecturally possible) silently passes the test if the baseline is updated in the same PR | **High** | The baseline is captured as a CONSTANT in the test file, not a runtime git lookup. A PR that adds a new file / endpoint / table must update the constant AND the architectural analysis (D-FA1.1 / D-FA3.1 explicit rejection). Reviewer at PR-time must reject any PR that updates the constant without a corresponding ADR amendment |
| R3.2 | The paused-instance defer test (T2) uses an `AsyncMock` to simulate the existing claim-side pause gate — the mock may not faithfully reproduce the real gate's behavior (e.g. the gate's edge cases on PAUSED-with-pending-tasks) | Medium | The mock's behavior is documented in the test docstring; the test asserts only the wake-specific contract (the wake is delivered; the task is held). The gate's own test pack (the existing `tests/unit/services/test_pause_resume_seam.py`) is the source of truth for the gate's behavior |
| R3.3 | The terminal-revival test (T3) uses an `AsyncMock` to simulate the existing terminal→RUNNING flip — the mock may not faithfully reproduce the real flip's behavior (e.g. the revive's edge cases on instance-tree revival, parent-revive ordering) | Medium | Same as R3.2 — the mock's behavior is documented; the test asserts only the wake-specific contract. The flip's own test pack (the existing `tests/unit/services/test_send_message_revives.py`) is the source of truth |
| R3.4 | The long-downtime double-arm test (T1) does not cover N=3 or N=4 arms — the coalesce helper's cap is 16, and the body format is the same regardless of N | Low | The cap test (Phase 2 T5.5) covers the N=17 case at the unit level; T1 covers the N=2 case at the integration level. The intermediate N's are covered by the helper's own logic. The integration smoke is enough |
| R3.5 | The drill smoke (T8) is a placeholder until Phase 4 lands — a CI run that includes the placeholder may report a spurious SKIPPED | Low | The smoke is registered as `pytest.mark.skip(reason="Phase 4 drill body")` until the drill is authored. A CI run sees the skip; a Phase 4 completion flips the skip |

---

## Rollback / Abandonment Notes

**If Phase 3 ships and Phase 4 does not:** the long-downtime,
paused-instance, terminal-revival, and AC6 structural
guarantees are tested but the runbook is not updated. The
feature is fully deliverable in the daemon code; the missing
artifacts are the operator-facing docs and the bash drill.
**No live impact.**

**Abandonment (kill-switch):** the same env-var flip as
Phase 1 + Phase 2
(`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`) disables the entire
feature. Phase 3 has no source changes; the kill-switch is
inherited.

**Code rollback (if a Phase 3 commit lands and is found
broken):** the change is three new test files only. A
`git revert` of the commit is the rollback; the daemon code
is unchanged. **No DB migration to reverse; no new file
to delete; no new endpoint to remove** (Phase 3 is
test-only by design).

**What does NOT work as a rollback:** deleting the test
files while leaving the AC6 invariant unmonitored. The
structural tests (T6.1–T6.3) are the load-bearing "no
parallel subsystem" assertions; deleting them is a
release-process regression. The correct rollback is the
code revert (or a fix-forward commit).

---

## Exit Criterion

**All of the following, objectively verifiable:**

1. **Long-downtime double-arm:** T5.13 GREEN; two arms in a
   single daemon lifecycle coalesce to ONE wake with a
   run-list, newest-first.
2. **Paused instance defer:** T5.14 GREEN; the wake is
   delivered (MessageQueue row created), the Task is held
   PENDING until resume, the resume drains the held task.
3. **Terminal instance revival:** T5.15 GREEN for each of
   the four terminal states; the wake's `enqueue_message`
   triggers the existing terminal→RUNNING flip and
   delivers to the revived instance.
4. **AC6 structural assertions:** T6.1, T6.2, T6.3 GREEN;
   the file list, the URL prefix list, and the table list
   are all UNCHANGED by this feature.
5. **Drill smoke placeholder:** T8 registered; the smoke
   is SKIPPED with a clear reason until Phase 4 lands.
6. **Non-regression:** T7 GREEN; the full Phase 1 + Phase 2
   packs pass byte-exact; the existing
   `tests/unit/tools/test_upgrade_journal.py` +
   `tests/unit/tools/test_upgrade_tools.py` +
   `tests/test_release_journal.sh` all pass.
7. **Live untouched:** no live pid verified-touched.

The Phase 3 commit is mergeable when 1–7 are green.
Phase 4 starts on the same `feature/post-restart-arm-notify`
branch; the commit that closes Phase 4 is the one that
authors the bash drill, the banner text updates, the
runbook, and the release notes line.
