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
| **AC2** boot delivery (detect pending records, enqueue wake) | T2.1–T2.4 | `tests/unit/services/test_post_restart_arm_notify_sweep.py` |
| **AC3** ROUTING (wake's report → confirming chat) | T3.1–T3.4 | `tests/job_queue/test_post_restart_arm_notify_routing.py` |
| **AC4** terminal-state gating (no wake while pipeline could roll back) | T4.1–T4.6 | `tests/unit/tools/test_post_restart_arm_notify_journal.py` (continuation) |
| **AC5** edge cases (missing instance, multiple records, idempotency, never-wedge, live refusal) | T5.1–T5.12 | `tests/unit/services/test_post_restart_arm_notify_sweep.py` (continuation) + `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` |
| **AC6** reuse existing machinery (no parallel messaging subsystem) | T6.1–T6.3 | structural test (see §4.2) |
| **AC7** tests following `tests/unit/` + `tests/job_queue/` conventions | THIS FILE | — |

---

## 2. Test Packs (file-by-file)

### 2.1 `tests/unit/tools/test_post_restart_arm_notify_journal.py` (AC1 + AC4)

**Convention precedent:** `tests/unit/tools/test_upgrade_journal.py`
(file-backed fixtures, `tmp_path`, journal + lib.sh interop where
relevant; sync + asyncio helpers; the existing `PendingOp` test
group as a direct precedent).

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
  same `journal_write` call), not behavioral (no
  double-write logic).

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

#### Group 4 — terminal-state predicate (T4.1, T4.2, T4.3)

* **T4.1** `test_is_pipeline_terminal_returns_event_name` —
  a journal with history `[..., {"name": "commit",
  "run_id": "r-..."}]` returns `"commit"` for that
  `run_id`. Mirrors the existing
  `_terminal_outcome` semantics at
  `upgrade_tools.py:1022-1075` (terminal-class-FILTERED).
* **T4.2** `test_is_pipeline_terminal_returns_none_when_pending` —
  a journal with no matching history event returns
  `None` — the wake is held `pending`. Mirrors the
  `PENDING` return of `_terminal_outcome`.
* **T4.3** `test_is_pipeline_terminal_tolerates_torn_journal` —
  a `JournalTorn` from `journal_read` returns `None`
  (best-effort; the sweep's caller logs and continues
  to the next tick).

#### Group 5 — non-interference with the existing journal (T4.4, T4.5, T4.6)

* **T4.4** `test_clear_pending_op_does_not_touch_pending_wakes` —
  calling `clear_pending_op` (the existing
  restart.sh-completion path) does NOT remove entries
  from `pending_wakes`. The wake is the load-bearing
  reason the wake record MUST live on a separate
  journal key.
* **T4.5** `test_restart_sh_journal_simulated_does_not_touch_pending_wakes` —
  the same, but the test simulates the restart.sh
  terminal event (`{"name": "restart", ...}`) AND
  the `pending_op` clear (the call sequence
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

**Coverage groups:**

#### Group 1 — boot pass (T2.1, T2.2)

* **T2.1** `test_sweep_wake_records_boot_pass_enqueues_wake` —
  with a journal containing one `pending` wake for
  `run_id=r-aaa`, a history event `{"name": "commit",
  "run_id": "r-aaa"}`, and an `InstanceManager` mock
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

#### Group 7 — kill-switch (T5.11)

* **T5.11** `test_sweep_wake_records_disabled_by_env` —
  with `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`, the
  sweep's `_is_enabled()` returns `False`; the sweep
  is a no-op. The arm-side write is also a no-op
  (separate test in the tools test pack — see T1.7
  in §3.1 below).

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

### 2.3 `tests/job_queue/test_post_restart_arm_notify_routing.py` (AC3)

**Convention precedent:** `tests/job_queue/test_a2_autopromote_notify.py`
(autopromote end-to-end, MessageQueue + worker_pool + source
adapter seam) and `tests/job_queue/test_idempotent_enqueue.py`
(idempotency on the enqueue path).

**Coverage groups:**

#### Group 1 — source routing (T3.1, T3.2, T3.3)

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

* **T3.3** `test_wake_message_user_origin_window_re_stamped` —
  after the wake is delivered, the
  `manager._user_origin_windows[arming_instance_id]`
  dict has an entry with `source="discord:user123"`
  and `expires_at` set. A follow-up `upgrade_status`
  call within the same wake turn passes the
  3-factor gate's factor-2 (the user-origin window
  is set). AC3 + ADR-041 enforcement.

#### Group 2 — empty source fall-back (T3.4)

* **T3.4** `test_wake_message_empty_source_uses_api_sentinel` —
  with a wake record whose `source=""` (the
  `""` sentinel from the arm-time capture for a
  non-user-origin arm), the wake's
  `enqueue_message` is called with
  `source="api"`. The response is delivered to
  the default chat (the API path). The user
  receives the report (best-effort routing).

### 2.4 `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` (AC5 — additional)

**Convention precedent:** `tests/job_queue/test_a4_f14_orphan_detection.py`
(orphan detection on the worker-pool path) and
`tests/job_queue/test_dead_letter_*` (dead-letter
recovery path).

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

### 2.5 Structural Test (AC6) — `tests/unit/test_post_restart_arm_notify_no_parallel.py`

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
