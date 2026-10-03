# Test Strategy — Post-Restart Arm-Notify

- **Date:** 2026-10-03 · **Author:** architect (controller) — feature analysis
- **Base:** branch `feature/post-restart-arm-notify` (the planning-authoritative tip)
- **Siblings (do not author):** `plan-overview.md` (single-author), `architecture-recommendation.md` (single-author, this worker's decision source), `decisions.md` (single-author, this worker's ADR source), `risk-register.md` (single-author)
- **Conventions:** `tests/unit/` (sync + asyncio unit tests with `tmp_path` fixtures, `pytest-asyncio`, `unittest.mock.AsyncMock` / `MagicMock`, file-backed SQLite for repo tests, mock for journal/manager); `tests/job_queue/` (integration-shaped tests against the boot + sweep + enqueue_message + worker_pool seam, with the Postgres variants in `tests/postgres/`); the existing test packs in `tests/unit/tools/test_upgrade_journal.py` and `tests/unit/services/test_maintenance_run_lock_and_capture.py` are the canonical precedents for **journal-level** and **service-level** tests respectively.

> **HARD CONSTRAINT (inherited from Phase 2, governs every test below):** every test runs against `tmp_path` fixtures, sandbox installs, or a sandbox daemon (own port + throwaway PG) — NEVER live. The live-outright-refusal at `upgrade_tools.py:2051-2058` is a refusal-test only; a live-arming wake is structurally impossible by construction.

---

## 1. Acceptance-Criteria → Test-Case Map (the one-pager)

| AC | Test case(s) | Where |
|---|---|---|
| **AC1** arm-time durable record (transactional with arm) | T1.1–T1.6 | `tests/unit/tools/test_post_restart_arm_notify_journal.py` |
| **AC2** boot delivery (detect pending records, enqueue wake) | T2.1–T2.4 + T13.1 (promote-lane fire test, r4 fold C1) | `tests/unit/services/test_post_restart_arm_notify_sweep.py` |
| **AC3** ROUTING (wake's report → confirming chat) | T3.1–T3.5 (T3.5 = Site 1 progressive dispatch, architecture delta #6) | `tests/job_queue/test_post_restart_arm_notify_routing.py` |
| **AC4** terminal-state gating (no wake while pipeline could roll back) | T4.1–T4.6 + T4.8 (mutation guard, delta #1) | `tests/unit/tools/test_post_restart_arm_notify_journal.py` (continuation) |
| **AC5** edge cases (missing instance, multiple records, idempotency, never-wedge, live refusal, kill-switch abandon-on-off) | T5.1–T5.12 + T5.16 (abandon-on-switch-off, delta #2) + T5.17 (re-enable-no-stale, delta #2) + T5.18 (manager-wired, r4 fold C2) + T5.19 (install_dir None no-op) | `tests/unit/services/test_post_restart_arm_notify_sweep.py` (continuation) + `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` |
| **AC6** reuse existing machinery (no parallel messaging subsystem) | T6.1–T6.4 (T6.4 = `arm_pending_wake` lock-position pin, delta #3) + T6.5 (manager-wired structural pin, r4 fold C2) + T6.6 (sweep-method structural pin, r4 fold C2) + T6.7 (real-journal-shape fixture pin, r4 fold C1) | structural test (see §4.2) |
| **AC7** tests following `tests/unit/` + `tests/job_queue/` conventions | THIS FILE | — |

---

## 2. Test Packs (file-by-file)

> **Pack-mapped validation (r4 fold W3):** every test in this
> file belongs to a registered pack under `test/packs/`. The
> convention is `test/packs/<feature>_<scope>_unit_test.sh`
> (the existing `tests/packs/release_journal_unit_test.sh`
> shape). Per `.agents/tester/rules/ensure.md` Core #1,
> "PACK-MAPPED validation, never bare pytest". The Phase 3
> implementation task **T13 (phase3-plan.md)** registers the
> five new packs in `.agents/tester/PACKS.md`; the per-phase
> verification in each `phaseN-plan.md` cites the pack by
> path, not by a bare `pytest` invocation.
>
> **r4 fold C1 note (test fixture shape):** all wake-reader
> tests use the REAL journal history entry shape:
> `{"ts": <iso>, "event": <event_name>, "detail": <prose>}` —
> NOT a fictional `{"name": ..., "run_id": ...}` shape. The
> real shape is at `upgrade_journal.py:326` (Python
> `journal_history_append`) and `lib.sh:663,666` (shell
> counterpart). Promote-lane terminal events carry NO
> `run_id` field at all (`promote.sh:366`; `rollback.sh:
> 203,209,211`); only the RESTART lane embeds a
> `run_id=<id>` substring inside the `detail` PROSE
> (`restart.sh:252,262`), used as an OPTIONAL tie-breaker on
> the RESTART lane only. See `decisions.md` ADR-042
> addendum (r4 fold C1) for the rationale.

### 2.0 Test-case → Pack Map (the r4 fold W3 deliverable)

| Test ID | Pack (file) | Pytest source file | Pack invocation |
|---|---|---|---|
| **T1.1, T1.2, T1.3, T1.4, T1.5, T1.6** | `test/packs/post_restart_arm_notify_journal_unit_test.sh` | `tests/unit/tools/test_post_restart_arm_notify_journal.py` | `timeout 300 bash test/packs/post_restart_arm_notify_journal_unit_test.sh` |
| **T4.1, T4.2, T4.3, T4.4, T4.5, T4.6, T4.8** | (same) | (same) | (same) |
| **T5.12** | (same — the kill-switch arm-side test rides the journal pack) | (same) | (same) |
| **T2.1, T2.2, T2.3, T2.4** | `test/packs/post_restart_arm_notify_sweep_unit_test.sh` | `tests/unit/services/test_post_restart_arm_notify_sweep.py` | `timeout 300 bash test/packs/post_restart_arm_notify_sweep_unit_test.sh` |
| **T5.1, T5.2, T5.3, T5.4, T5.5, T5.6, T5.7, T5.8, T5.9, T5.10, T5.11, T5.16** | (same) | (same) | (same) |
| **T13.1, T5.18, T6.6, T6.7** (new — promote-lane fire test, kill-switch sweep wiring test, manager-wiring test, install_dir no-op test) | (same) | (same) | (same) |
| **T3.1, T3.2, T3.3, T3.4, T3.5** | `test/packs/post_restart_arm_notify_routing_unit_test.sh` | `tests/job_queue/test_post_restart_arm_notify_routing.py` | `timeout 300 bash test/packs/post_restart_arm_notify_routing_unit_test.sh` |
| **T5.13, T5.14, T5.15, T5.17** | `test/packs/post_restart_arm_notify_edge_cases_unit_test.sh` | `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` | `timeout 300 bash test/packs/post_restart_arm_notify_edge_cases_unit_test.sh` |
| **T5.18, T6.5, T5.19** (r4 fold W1+W3 — ari-fallback implementation, manager-wiring structural, install_dir no-op) | (same) | (same) | (same) |
| **T6.1, T6.2, T6.3, T6.4** | `test/packs/post_restart_arm_notify_structural_unit_test.sh` | `tests/unit/test_post_restart_arm_notify_no_parallel.py` | `timeout 300 bash test/packs/post_restart_arm_notify_structural_unit_test.sh` |
| **D1, D2, D3, D4, D5, D6** (drill — Phase 4) | `test/drills/post_restart_arm_notify_drill.sh` (NOT a `tests/packs/` pack; this is a bash drill, run manually) | (drill) | `bash test/drills/post_restart_arm_notify_drill.sh` |
| **T4.7** (banner regression) | `test/packs/post_restart_arm_notify_banner_unit_test.sh` (Phase 4 registers a new pack — distinct from the five) | `tests/unit/tools/test_post_restart_arm_notify_banner.py` | `timeout 300 bash test/packs/post_restart_arm_notify_banner_unit_test.sh` |

**Pack registration (planning-side ONLY — the implementation-lane
commit adds the entry to `.agents/tester/PACKS.md`):** the five
unit packs above are listed in the per-phase acceptance
sections of `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`,
`phase4-plan.md` by their path; the per-phase verification
sections in each phase plan cite the pack by path (not bare
`pytest`). The implementation-lane commit
(`phase3-plan.md` T13 + `phase4-plan.md` T11) registers the
five packs in `.agents/tester/PACKS.md` per the existing entry
format. **The Phase 3 + Phase 4 commits MUST NOT edit
`.agents/tester/PACKS.md`** (tester-owned file with unrelated
uncommitted state per the r4 dispatch); the registration
happens in a follow-up commit after the planning commit
lands, OR the test-author uses the dispatcher's tester lane
to register the packs (the dispatcher's choice per its
lanes-not-mine rule).

### 2.1 `tests/unit/tools/test_post_restart_arm_notify_journal.py` (AC1 + AC4)

**Convention precedent:** `tests/unit/tools/test_upgrade_journal.py`
(file-backed fixtures, `tmp_path`, journal + lib.sh interop where
relevant; sync + asyncio helpers; the existing `PendingOp` test
group as a direct precedent).

**Pack:** `test/packs/post_restart_arm_notify_journal_unit_test.sh`
(transparent wrapper following the
`test/packs/release_journal_unit_test.sh` precedent: `set -u`,
120s internal watchdog, outer `timeout 300` wrap, exit-code
propagated, `RESULT: PASS/FAIL` tail line).

**Coverage groups:**

#### Group 1 — `PendingWake` dataclass (T1.1, T1.2)

* **T1.1** `test_pending_wake_round_trip` — write a
  `PendingWake` via `arm_pending_wake`, read it back via
  `list_pending_wakes`. Every field preserved. The
  `from_json` filter discipline (`upgrade_journal.py:738`)
  is preserved — known fields round-trip, unknown
  fields drop silently.
* **T1.2** `test_pending_wake_from_json_garbage_tolerant` —
  a `pending_wakes` value of `null`, `[]`, `"<str>"`, or
  `123` does NOT raise `from_json`; the journal reader
  returns `[]` (the empty-list sentinel). Mirrors the
  existing `PendingOp.from_json` garbage tolerance.

#### Group 2 — atomicity with `pending_op` (T1.3)

* **T1.3** `test_wake_record_atomic_with_pending_op` —
  the call sequence `write_pending_op(install_dir, op);
  arm_pending_wake(install_dir, wake)` produces a single
  journal write (the same `journal_write` envelope —
  verified by counting `tmp` file creations via a
  monkeypatched `os.replace` spy). A simulated crash
  (kill -9 in a child writer thread, bounded < 2s) leaves
  EITHER the pre-arm state OR the post-arm state — never
  a half-state. The atomic guarantee is structural (the
  same `journal_write` call UNDER the caller-acquired
  journal lock — `journal_write`
  (`upgrade_journal.py:254-294`) has no internal lock;
  serialization is the arm site's `journal_lock_acquire`
  at `upgrade_tools.py:2167`/`:2719`; architecture delta
  #3 reword of the original (incorrect) self-atomic
  journal_write claim; placement pinned by Phase 1 T12 +
  Phase 3 T6.4), not behavioral (no double-write logic).

#### Group 3 — helpers (T1.4, T1.5)

* **T1.4** `test_mark_wake_delivering_cas` — the
  `mark_wake_delivering` helper transitions
  `pending → delivering` ONLY when the journal lock is
  acquired by the caller. A concurrent caller without
  the lock observes the prior state and the call returns
  `None` (no exception). Mirrors the existing
  `journal_lock_acquire` failure semantics.
* **T1.5** `test_mark_wake_delivered_removes_from_dict` —
  the `mark_wake_delivered` helper writes
  `status=delivered` AND removes the record from the
  `pending_wakes` dict (the structural removal is the
  idempotency key — see T5.7).
* **T1.6** `test_mark_wake_abandoned_journals_reason` —
  the `mark_wake_abandoned` helper writes
  `status=abandoned` (then removes from dict) AND
  appends a `wake_abandoned` history event with the
  `reason` field. The history event uses the same
  `journal_history_append` helper as the existing
  pipeline events.

#### Group 4 — terminal-state predicate (T4.1, T4.2, T4.3) + mutation guard (T4.8)

* **T4.1** `test_wake_terminal_walker_returns_event_name` —
  a journal with history `[..., {"ts": <iso>,
  "event": "commit", "detail": "..."}]` returns `"commit"`
  for that `run_id` from the parameterized walker
  (`latest_matching_event`, Phase 1 T7 — called with an
  inline event tuple; the WAKE event-set is owned by
  Phase 2, delta #1). Mirrors the existing
  `_terminal_outcome` semantics at
  `upgrade_tools.py:1022-1075` (terminal-class-FILTERED).
  **Real journal shape — `{ts, event, detail}` per
  `upgrade_journal.py:326`; the `run_id` parameter is
  accepted by the walker but is NOT used for the
  event-class match on the promote lane (r4 fold C1).**
* **T4.2** `test_wake_terminal_walker_returns_none_when_pending` —
  a journal with no matching history event returns
  `None` — the wake is held `pending`. Mirrors the
  `PENDING` return of `_terminal_outcome`.
* **T4.3** `test_wake_terminal_walker_tolerates_torn_journal` —
  a `JournalTorn` from `journal_read` returns `None`
  (best-effort; the sweep's caller logs and continues
  to the next tick).
* **T4.8** `test_wake_terminal_events_mutation_guard` —
  (architecture delta #1, MUST) pins BOTH directions:
  `"restart" in WAKE_TERMINAL_EVENTS` (the wake fires for
  intentional restarts — the dominant case;
  `restart.sh:262` journals `"restart"` which
  `_TERMINAL_EVENTS` rejects) AND
  `"restart" not in _TERMINAL_EVENTS` with the 6-member
  set intact (the PROMOTE-only reconcile at
  `upgrade_journal.py:1016` depends on it). Also asserts
  the sibling-derivation shape
  `set(WAKE_TERMINAL_EVENTS) == set(_TERMINAL_EVENTS) |
  {"restart"}`. A mutation of the shared constant or a
  deletion of the sibling FAILS loudly.

#### Group 5 — non-interference with the existing journal (T4.4, T4.5, T4.6)

* **T4.4** `test_clear_pending_op_does_not_touch_pending_wakes` —
  calling `clear_pending_op` (the existing
  restart.sh-completion path) does NOT remove entries
  from `pending_wakes`. The wake is the load-bearing
  reason the wake record MUST live on a separate
  journal key.
* **T4.5** `test_restart_sh_journal_simulated_does_not_touch_pending_wakes` —
  the same, but the test simulates the restart.sh
  terminal event (`{"ts": <iso>, "event": "restart",
  "detail": "run_id=<id> ..."}` — the real shape at
  `restart.sh:262`; the `run_id=<id>` substring in the
  `detail` is the OPTIONAL restart-lane tie-breaker)
  AND the `pending_op` clear (the call sequence
  `journal_update_field(in_flight, None);
  clear_pending_op`). The wake record is preserved.
* **T4.6** `test_reconcile_pending_op_does_not_touch_pending_wakes` —
  the existing `reconcile_pending_op` (PROMOTE-kind
  only) does not touch `pending_wakes`. The wake
  sweep is the only consumer that knows the wake
  record exists.

### 2.2 `tests/unit/services/test_post_restart_arm_notify_sweep.py` (AC2 + AC5)

**Convention precedent:** `tests/unit/services/test_maintenance_run_lock_and_capture.py`
(`MaintenanceRunLock` in-process; `MaintenanceRunsRepository`
file-backed SQLite; `tmp_path`; `unittest.mock.AsyncMock` /
`MagicMock` for the manager seam; `pytest-asyncio`).

**Pack:** `test/packs/post_restart_arm_notify_sweep_unit_test.sh`
(transparent wrapper per §2.1's pattern).

**r4 fold C2 note (manager wiring):** the `InstanceManager` is
a constructor param OR a setter on the service (decided in
`phase2-plan.md` T18 — wiring at `api.py:1489-1496`); the
tests' `AsyncMock` seam is the wired attribute, NOT a
module-level mock. The test in T5.18 (new) asserts the wiring
path is reachable — i.e. the service attribute is set after
construction AND the sweep's `_deliver_wake` calls
`self._manager.enqueue_message(...)` (NOT
`manager.enqueue_message(...)` at module scope).

**Coverage groups:**

#### Group 1 — boot pass (T2.1, T2.2)

* **T2.1** `test_sweep_wake_records_boot_pass_enqueues_wake` —
  with a journal containing one `pending` wake for
  `run_id=r-aaa`, a history event `{"ts": <iso>,
  "event": "commit", "detail": "..."}` (the real
  `promote.sh:366` shape — no `run_id` field on the
  history entry), and an `InstanceManager` mock
  whose `enqueue_message` is an `AsyncMock`, calling
  `sweep_wake_records()` on the service results in
  exactly one `enqueue_message` call with
  `instance_id=<recorded arming_instance_id>`,
  `source=<recorded source>`, `priority=2`,
  `metadata={"system_context": {"kind": "post_restart_arm_notify",
  "run_id": "r-aaa", "terminal_outcome": "commit", ...}}`.
  The wake record is removed from the dict after
  delivery.
* **T2.2** `test_sweep_wake_records_boot_pass_runs_even_when_no_wakes` —
  with a journal containing no `pending_wakes`, the
  sweep returns a clean result (`pending_at_start=0,
  pending_at_end=0, delivered=0`) and does NOT call
  `enqueue_message`. The fast-path empty case.

#### Group 2 — terminal-state gating (T2.3, T2.4)

* **T2.3** `test_sweep_wake_records_held_when_pipeline_pending` —
  with a journal containing one `pending` wake for
  `run_id=r-aaa` but NO matching history event, the
  sweep returns the wake as still-pending
  (`pending_at_end=1, delivered=0`). The wake is NOT
  delivered. AC4 enforcement.
* **T2.4** `test_sweep_wake_records_delivered_only_for_terminal` —
  the sweep iterates a mixed batch (some terminal,
  some not) and delivers only the terminal ones;
  pending wakes remain for the next tick.

#### Group 3 — missing instance (T5.1, T5.2)

* **T5.1** `test_sweep_wake_records_missing_instance_ari_fallback` —
  with a journal containing a wake for
  `arming_instance_id=<missing>` and an
  `InstanceManager` mock whose `enqueue_message` raises
  `InstanceNotFound` (mocked), the sweep falls back
  to the ari instance in the same project
  (`ari_id=ari-1`). The fallback enqueue uses
  `source=<recorded>` and a body with a
  "(arm-notice: original arming instance not found;
  reporting via ari fall-back)" annotation.
* **T5.2** `test_sweep_wake_records_missing_instance_no_ari_journals_notice` —
  same as T5.1, but the project has no ari instance.
  The sweep journals an `arm_notify_no_instance`
  history event with the `run_id`,
  `arming_instance_id`, and the resolution that no
  fall-back was available. The wake record is
  transitioned to `abandoned` (and removed from dict).

#### Group 4 — multiple records / coalesce (T5.3, T5.4, T5.5, T5.6)

* **T5.3** `test_sweep_wake_records_coalesces_by_instance` —
  with a journal containing 3 `pending` wakes for the
  same `arming_instance_id` (different `run_id`s),
  the sweep delivers ONE wake with a run-list payload
  (newest-first by `armed_at`).
* **T5.4** `test_sweep_wake_records_coalesce_separate_instances` —
  with 3 wakes for 3 different `arming_instance_id`s,
  the sweep delivers 3 separate wakes (no coalesce
  across instances). The coalesce is by instance, not
  global.
* **T5.5** `test_sweep_wake_records_coalesce_cap_drops_overflow` —
  with 17 wakes for the same instance, the sweep
  delivers ONE coalesced wake (16 entries) and
  journals a `wake_coalesce_overflow` history event
  with the dropped count and the dropped `run_id`s.
  The user can query the rest interactively.
* **T5.6** `test_sweep_wake_records_run_list_payload_format` —
  the coalesced body matches the documented format
  (see `architecture-recommendation.md` §FA5.2):
  ```
  Post-restart arm-notify (coalesced — N arms pending for this
  instance). The most recent armed <kind> <run_id> completed
  ...
  Outcomes (newest first):
    - <run_id_a>: <terminal_outcome_a> at <armed_at_a>
    - <run_id_b>: <terminal_outcome_b> at <armed_at_b>
    - ...
  ```

#### Group 5 — idempotency (T5.7, T5.8)

* **T5.7** `test_sweep_wake_records_skips_delivered` —
  with a journal containing one `pending_wakes` entry
  with `status=delivered` (a stale record from a prior
  sweep that crashed before the dict removal), the
  sweep skips it. (In practice, the structural dict
  removal prevents this state; the test covers the
  defensive case.)
* **T5.8** `test_sweep_wake_records_cas_loser_skips` —
  two sweep ticks race on the same wake. Tick-A
  acquires the lock and transitions
  `pending → delivering`. Tick-B's
  `mark_wake_delivering` returns `None` (lock not
  acquired). Tick-B skips the wake. The next tick
  after Tick-A completes observes the wake as
  `delivered` and skips it.

#### Group 6 — boot never wedges (T5.9, T5.10)

* **T5.9** `test_sweep_wake_records_journal_torn_skips_tick` —
  a `JournalTorn` from `journal_read` is caught; the
  sweep returns a result with `errors=1` and continues
  to the next tick. The boot is not aborted.
* **T5.10** `test_sweep_wake_records_enqueue_failure_continues` —
  `enqueue_message` raising an exception is caught
  per-wake; the sweep increments `errors` and
  continues to the next wake. The boot is not
  aborted.

#### Group 7 — kill-switch (T5.11) + abandon-on-switch-off (T5.16)

* **T5.11** `test_sweep_wake_records_disabled_by_env` —
  with `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`, the
  sweep's `_is_enabled()` returns `False`; the DELIVERY
  branch is a no-op. The arm-side write is also a no-op
  (separate test in the tools test pack — see T1.7
  in §3.1 below).
* **T5.16** `test_sweep_abandons_records_when_kill_switch_off` —
  (architecture delta #2, MUST) with the env OFF AND
  `pending_wakes` records present, the sweep's one-time
  abandon-pass marks each record `abandoned` with
  `reason=kill_switch_off` and journals exactly ONE
  `wake_abandoned` history event per record; a second
  tick with the drained dict journals NOTHING new; zero
  `enqueue_message` calls are ever made while OFF.
  This is the sweep-side half of ADR-044's
  abandon-on-switch-off semantics (Phase 2 T14).

#### Group 8 — live-outright-refusal inheritance (T5.12)

* **T5.12** `test_arm_side_live_refusal_writes_no_wake` —
  invoking `system_restart` with `self_env=live`
  returns the live-outright-refusal error and writes
  NO journal record (no `pending_op`, no
  `pending_wakes`). Mirrors the existing
  `upgrade_tools.py:2051-2058` test
  (the existing test pack already covers this; the
  new assertion is that the wake record is also
  absent — no second surface to forget).

#### Group 9 — r4 fold additions (C1, C2, W1, W3)

* **T5.18** (r4 fold C2) `test_sweep_manager_wired_via_constructor`
  — assert the `UpgradeJournalSweepService` exposes a
  `manager` attribute (constructor param OR setter, per
  `phase2-plan.md` T18) AND the sweep's `_deliver_wake`
  calls `self._manager.enqueue_message(...)` (NOT
  `manager.enqueue_message(...)` at module scope). The
  assertion is structural: a test that fails to find
  the wired attribute is a fail-loud, not a NameError.
* **T5.19** (r4 fold W1) `test_sweep_install_dir_none_is_noop`
  — `install_dir=None` → `WakeSweepResult()` all-zeros,
  zero journal reads attempted. Precedent
  `UpgradeJournalSweepService.__init__ :99-103`. Asserts
  the no-op seam is preserved (the manager wiring in T18
  is a no-op when `install_dir` is None).
* **T6.5** (r4 fold C2) `test_arm_pending_wake_call_site_is_reachable`
  — the `arm_pending_wake` call site uses the wired
  helper, not a module-level seam. AST-level inspection
  + the wired `arm_pending_wake` callable. This is a
  structural pin (complements T6.4's lock-position pin).
* **T6.6** (r4 fold W3) `test_sweep_sweep_wake_records_is_method_on_service`
  — the `sweep_wake_records` method is reachable as a
  bound method on `UpgradeJournalSweepService` (i.e. it
  is NOT a module-level function that requires a
  hidden global). Mirrors the manager-wiring structural
  pin (T5.18).
* **T6.7** (r4 fold C1) `test_wake_terminal_walker_uses_real_journal_shape`
  — the wake reader's fixtures are the REAL journal
  history shape `{"ts": <iso>, "event": <name>,
  "detail": <prose>}` (per `upgrade_journal.py:326`),
  NOT a fictional `{"name": ..., "run_id": ...}` shape.
  A test that imports a fixture with the fictional shape
  is a fail-loud (the fixture import itself raises on a
  schema check).
* **T13.1** (r4 fold C1, MUST) `test_wake_fires_on_promote_lane_no_run_id`
  — a journal with one `pending` wake + a history
  ending `{"ts": <iso>, "event": "commit", "detail":
  "..."}` (no `run_id` field at all, as is the
  real `promote.sh:366` shape) — the sweep observes
  the terminal event via the `armed_at` TS-scope
  reader (no `run_id` matching needed) and delivers
  the wake. **This test is the r4 fold C1 acceptance
  gate** — without it, the system_upgrade wake would
  never fire (the original planning's strict `run_id`
  matching returned `None` for every promote-lane
  entry, breaking AC2/AC4 on the promote lane).

### 2.3 `tests/job_queue/test_post_restart_arm_notify_routing.py` (AC3)

**Convention precedent:** `tests/job_queue/test_a2_autopromote_notify.py`
(autopromote end-to-end, MessageQueue + worker_pool + source
adapter seam) and `tests/job_queue/test_idempotent_enqueue.py`
(idempotency on the enqueue path).

**Pack:** `test/packs/post_restart_arm_notify_routing_unit_test.sh`
(transparent wrapper per §2.1's pattern).

**Coverage groups:**

#### Group 1 — source routing (T3.1–T3.3) + Site 1 dispatch (T3.5)

* **T3.1** `test_wake_message_source_equals_recorded_source` —
  end-to-end: arm with `source=discord:user123`; the
  recorded `pending_wakes.source` is `"discord:user123"`;
  the wake's `enqueue_message` call has
  `source="discord:user123"`. The MessageQueue row's
  `source` column is `"discord:user123"`. The
  response-message's `source` is
  `"discord:user123"`. AC3 enforcement.

* **T3.2** `test_wake_message_routes_to_correct_chat` —
  end-to-end with a stub source adapter: the
  arming turn comes in via `discord:user123`, the
  daemon restarts, the wake is delivered, the
  agent's response is delivered to `discord:user123`
  via the source adapter's `deliver_response` (the
  stub captures the call). AC3 enforcement at the
  full path.

* **T3.3** `test_wake_message_user_origin_window_set_after_delivery` —
  (REWORDED per architecture delta #7) after the wake is
  delivered, the `manager._user_origin_windows[arming_instance_id]`
  dict has an entry with `source="discord:user123"`
  and `expires_at` set. A follow-up `upgrade_status`
  call within the same wake turn passes the
  3-factor gate's factor-2 (the user-origin window
  is set). The assertion is "window is SET after wake
  delivery" — NEVER "exactly one stamp call":
  double-stamping (the defensive pre-enqueue stamp +
  the natural `manager.py:8112` stamp) is STRUCTURAL.
  AC3 + ADR-041 (+ addendum) enforcement.
* **T3.5** `test_site1_progressive_dispatch_source_verbatim` —
  (architecture delta #6) unit-level Site 1 test: the
  in-graph progressive dispatch at
  `instance_messaging.py:3053-3128` with
  `message_source="discord:user123"` → the `else`
  branch sets `dispatch_source = message_source` and
  uses it VERBATIM; the progressive chunk goes out via
  `source_dispatcher.dispatch_message`
  (`dispatcher.py:189-266`) with
  `external_user_id="user123"` on the `discord`
  adapter. The routing stub captures BOTH
  `dispatch_message` AND `dispatch_completed`
  (existing T3.1–T3.4 cover Site 2 —
  `message_processing_pipeline.py:720-795` — only).

#### Group 2 — empty source fall-back (T3.4)

* **T3.4** `test_wake_message_empty_source_uses_api_sentinel` —
  with a wake record whose `source=""` (the
  `""` sentinel from the arm-time capture for a
  non-user-origin arm), the wake's
  `enqueue_message` is called with
  `source="api"`. The response is delivered to
  the default chat (the API path). The user
  receives the report (best-effort routing).

### 2.4 `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` (AC5 — additional + delta #2 re-enable)

**Convention precedent:** `tests/job_queue/test_a4_f14_orphan_detection.py`
(orphan detection on the worker-pool path) and
`tests/job_queue/test_dead_letter_*` (dead-letter
recovery path).

**Pack:** `test/packs/post_restart_arm_notify_edge_cases_unit_test.sh`
(transparent wrapper per §2.1's pattern).

**r4 fold W1 note (ari fall-back + body variant + journal
branch):** the r4 fold surfaced that T5.1 + T5.2 cover the
two branches of the missing-instance case but had NO
implementation task. The implementation task is `phase2-plan.md`
T19 (NEW), and the test body annotations are updated:

* **T5.1** body annotation: "(arm-notice: original arming
  instance not found; reporting via ari fall-back) —
  run_id=<recorded> upgrade_status pointer — the
  arming instance row is gone, this is the project's
  front-door ari. **No user action required.**"
* **T5.2** body annotation: the `arm_notify_no_instance`
  history event carries the recorded `run_id` + the
  recorded `arming_instance_id` + the project id + a
  "no ari available" reason.

**Coverage groups:**

#### Group 1 — long-downtime double-arm (T5.13)

* **T5.13** `test_sweep_handles_two_arms_in_long_downtime` —
  end-to-end: two arms happen within a single
  daemon lifecycle (e.g. arm A, restart, arm B,
  restart, arm C); the journal after the second
  restart has 2 `pending_wakes` entries. The boot
  pass coalesces them into ONE wake with a
  run-list payload. Mirrors the multi-arm
  long-downtime scenario in FA5.2.

#### Group 2 — wake-during-agent-busy (T5.14)

* **T5.14** `test_sweep_skips_paused_instance_defers_to_resume` —
  with an arming instance that is `PAUSED` at
  boot, the wake's `enqueue_message` is held by
  the existing claim-side pause gate
  (`instance_messaging.py:2154-2161, :1925-1934`).
  The wake is delivered (the `MessageQueue` row
  is created) but the `Task` is held `PENDING`
  until the instance is resumed. The wake
  record's `status=delivered` is set after
  `enqueue_message` returns; the resume of the
  instance drains the held Task.

#### Group 3 — arming-instance revival (T5.15)

* **T5.15** `test_sweep_revives_terminal_instance_for_wake` —
  with an arming instance that was `COMPLETED`
  / `TERMINATED` / `ERROR` / `FAILED` at the
  time of the arm (a normal completion of a
  prior turn), the wake's `enqueue_message`
  triggers the existing terminal→RUNNING flip
  (`instance_messaging.py:1954-1976`). The wake
  is delivered to the revived instance. AC5
  (missing instance vs terminal instance is
  distinct) — terminal is revive-able, missing
  is fall-back.

#### Group 4 — kill-switch re-enable (T5.17)

* **T5.17** `test_re_enable_after_off_delivers_nothing_stale` —
  (architecture delta #2, MUST) temporal scenario: (1)
  arm → record present; (2) env OFF for a period
  covering the record's terminal transition → the
  sweep's one-time abandon-pass marks it `abandoned`
  with `reason=kill_switch_off` (Phase 2 T14 / T5.16);
  (3) env re-enabled → the sweep runs, finds NO
  `pending` records, delivers NOTHING (zero
  `enqueue_message` calls). Proves the late-deliver
  alternative (rejected in ADR-044) is structurally
  impossible — a re-enable after an off-period can
  never flood the user with stale wakes.

### 2.5 Structural Test (AC6) — `tests/unit/test_post_restart_arm_notify_no_parallel.py` (incl. delta #3 lock pin)

**Pack:** `test/packs/post_restart_arm_notify_structural_unit_test.sh`
(transparent wrapper per §2.1's pattern; runs against the
merged code, not the per-commit diff).

**Coverage groups:**

#### Group 1 — no new file (T6.1)

* **T6.1** `test_no_new_journal_file` — at the
  integration level, the only journal file in
  the install dir is `releases/state.json`. The
  test enumerates the install dir after a wake
  arm + a boot sweep and asserts the file list
  is exactly the expected pre-feature list (no
  new file). Structural AC6 enforcement.

#### Group 2 — no new HTTP endpoint (T6.2)

* **T6.2** `test_no_new_http_endpoint` — the
  HTTP router's URL list (the union of all
  router prefixes) does NOT include any
  `/post-restart-arm-notify` or
  `/wake` prefix. Structural AC6 enforcement.

#### Group 3 — no new SQLModel table (T6.3)

* **T6.3** `test_no_new_sqlmodel_table` —
  the SQLModel metadata's table list does NOT
  include `arm_wake_records` or any new table
  for the wake. Structural AC6 enforcement.

#### Group 4 — `arm_pending_wake` lock-position pin (T6.4)

* **T6.4** `test_arm_pending_wake_inside_lock_holding_try` —
  (architecture delta #3, MUST) static source-inspection
  test (same shape as the Phase 4 banner regression
  test): the `arm_pending_wake(` call site sits INSIDE
  the lock-holding `try` block at BOTH arm sites —
  `system_restart` (lock acquired
  `upgrade_tools.py:2167`, call ~`:2209`, AFTER
  `write_pending_op`) and `system_upgrade` (lock
  `:2719`, call ~`:2850`, AFTER `write_pending_op`).
  `journal_write` has NO internal lock — a future
  refactor pulling the call outside the `try` is a
  SILENT TORN-WRITE risk (arm without wake or wake
  without arm under crash). A regression FAILS with the
  delta #3 rationale in the assertion message.

### 2.6 Drill Coverage — `test/drills/post_restart_arm_notify_drill.sh`

A drill runbook variant following the Phase 2
restart/upgrade drill precedent
(`test/drills/p21_upgrade_pipeline_drill.sh`):

* **D1** — arm a `system_restart`, kill the
  daemon BEFORE the restart completes, restart
  the daemon, observe a wake delivered to the
  arming instance with `terminal_outcome=restart`.
* **D2** — arm a `system_upgrade`, kill the
  daemon BEFORE the promote completes, restart
  the daemon, observe a wake with
  `terminal_outcome=committed` (or the actual
  terminal event).
* **D3** — arm with `source=discord:user123`
  (sandbox mock), kill the daemon, restart,
  observe the wake's `source=discord:user123`
  in the MessageQueue row.
* **D4** — long-downtime double-arm: arm A,
  kill, restart, arm B, kill, restart; observe
  ONE coalesced wake with run-list.
* **D5** — kill-switch: arm with
  `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`, kill,
  restart; observe NO wake record in the
  journal.
* **D6** — live-outright-refusal: attempt to
  arm a `system_restart` against
  `self_env=live` (sandbox), observe the
  refusal and NO journal write.

---

## 3. Pass/Fail Criteria (the contract for green)

### 3.1 Unit / job-queue tests

A test is GREEN when:
1. The test asserts the documented post-condition
   (the AC's required behavior).
2. The test does NOT rely on a flaky external
   dependency (the journal is a `tmp_path` fixture;
   the manager is a mock; the source adapter is a
   stub).
3. The test's assertion message is grep-able for
   the AC number (e.g. `assert ... # AC1: ...`).
4. The test passes on a fresh `tmp_path` and on a
   `tmp_path` with a pre-existing journal (no
   cross-test pollution).

### 3.2 Drill

A drill is GREEN when:
1. The drill's bash exit code is `0` (the
   `drill.sh` convention).
2. The drill's structured log shows the wake
   delivered, the source routed, the coalesce
   applied (or no coalesce if N=1), the live
   refusal inherited.
3. The drill's post-condition is asserted in
   bash: the journal's `pending_wakes` is
   empty post-delivery (one-shot); the
   MessageQueue has the wake; the source
   adapter delivered the response (or the
   sandbox stub captured the call).

### 3.3 Phase 2 verification gate (the existing 30/30 pack)

The Phase 2 post-merge acceptance gate
(`.agents/tester/RESULTS/2026-09-22-post-merge-acceptance-gates-ca4ab125.md`)
runs the full `tests/` directory (the lesson: full-dir-per-merge).
This feature's new tests are added to the pack; the pack
remains green (no existing test is broken by the new code
path; the live-outright-refusal assertion is preserved).

---

## 4. Test Authoring Conventions (the rules a test author must follow)

### 4.1 Naming

* `test_<feature>_<behavior>` for unit tests.
* `test_<feature>_<scenario>_<expected_outcome>` for
  job-queue / integration tests.
* Test classes: `class TestWakeRecord:`, `class
  TestSweepWakeRecords:`, etc. — group by FA.

### 4.2 Fixtures

* `tmp_path` for any file-backed state.
* `monkeypatch` for env-var changes (the
  `ENSEMBLE_POST_RESTART_ARM_NOTIFY` kill-switch).
* `AsyncMock` for the `InstanceManager.enqueue_message`
  / `stamp_user_origin_window` calls.
* `MagicMock` for the source-adapter stub (the
  `deliver_response` capture).
* `JournalTorn` simulated via a monkey-patched
  `journal_read` that raises.

### 4.3 Assertions

* `assert <post-condition>, f"AC{N}: <message>"`
  — the AC number is grep-able.
* `pytest.raises(<expected>)` for the boot-never-wedge
  tests — but the **sweep itself does not raise**;
  the test asserts the sweep RETURNS a result with
  `errors=N`.
* `caplog` for the structured-log assertions
  (`post_restart_arm_notify armed`, `delivered`,
  `abandoned`).

### 4.4 Skipped / xfail

* No test in this pack is xfail.
* The live-outright-refusal test is **NOT skipped**
  — it runs in the sandbox (no live is touched).
* The Postgres-variant tests live in
  `tests/postgres/test_post_restart_arm_notify_pg.py`
  (mirror the existing `tests/postgres/test_report_delivery_recovery_pg.py`
  pattern); the sandbox daemon is a sandbox-only
  construct.

### 4.5 Cross-reference

Every test docstring carries the AC reference:

```python
def test_pending_wake_round_trip(self):
    """T1.1 — ``PendingWake`` round-trip via ``arm_pending_wake``
    + ``list_pending_wakes``. Mirrors the existing
    ``PendingOp`` round-trip test. AC1: arm-time durable
    record carries the documented fields; the
    ``from_json`` filter discipline preserves unknown
    fields as silent drops.
    """
    ...
```

---

## 5. Regression / Non-Regression (the contract with Phase 2)

The feature MUST NOT break:

* **D-FA1.1 pending-op semantics** — `pending_op`
  write/read/clear is unchanged; the wake is on
  a separate key.
* **D-FA1.3 daemonized executor** — restart.sh /
  promote.sh are unchanged; the wake does not
  touch the executor seam.
* **D-FA1.4 post-turn trigger** —
  `drain_pending_system_execution` is unchanged;
  the wake is a separate boot-time concern.
* **D-FA1.5 in-flight semantics** — in-flight
  freeze-at-checkpoint is unchanged; the wake
  is held `pending` until a terminal event.
* **Live-outright-refusal** — preserved (T5.12).
* **Env-self-match / env-marker-absent** —
  preserved (the wake does not touch the
  arm-side gate).
* **The 3-factor gate** — preserved (the
  re-stamp is the additive change; the gate
  itself is unchanged).

The existing `tests/unit/tools/test_upgrade_journal.py`
pack (the 30/30 Phase 2 pack) MUST remain green
post-feature. A regression in the existing pack is
a release blocker; a new test in this pack is the
correct path for new behavior.
