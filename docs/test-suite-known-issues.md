# E2E / Integration Test Suite — Known Issues

Snapshot of the full integration (E2E) suite state, captured after commit
`7d38714e` (job-watch notification fix). Run command per `ensure.md`:

```bash
.venv/bin/pytest --override-ini="addopts=" -m integration
```

**Result: 71 passed, 30 failed, 8 skipped (~9.5 min, 109 selected).**

The 30 failures are **not caused by the `7d38714e` fix** — verified by running
the suspect subset against the previous commit `76344dcd`, where they fail
identically. They fall into three groups.

---

## Group 1 — Test-ordering / `sys.modules` pollution (test-infra, not code bugs)

These tests **skip/pass in isolation** but fail only when the full suite runs,
because earlier files mutate `sys.modules` (mock injection) or global state at
import time. Already documented in `ensure.md` "Known Issues".

| Test | Why it fails in-suite |
|------|----------------------|
| `tests/integration/test_completion_report.py` (`test_leader_spawns_developer_and_receives_report`, `test_completion_report_message_format`) | Polluted by earlier collection; skip cleanly when run alone. |
| `tests/integration/test_agent_bootstrap.py` (`test_agent_bootstrap_and_hello`, `test_agent_bootstrap_with_instance_manager`) | Same — 4 skipped in isolation, fail in-suite. |
| `tests/integration/test_message_queue_e2e.py` (3 tests) | Mutates `sys.modules` at import; the documented polluter. Also asserts `Expected 1 LLM call, got 0` — environmental. |

**Fix direction:** run the polluters in isolation (separate pytest invocation),
or remove the import-time `sys.modules` mutation. Not a daemon code bug.

---

## Group 2 — Environment / LLM-dependent (inconclusive)

Depend on live LLM quota, a running OpenCode server, or `psycopg` state. Their
failure here is environmental and not necessarily a code defect.

| Test | Dependency |
|------|-----------|
| `test_inner_soul*` (5) | Live LLM responses; fixture-sensitive. |
| `tests/opencode/test_integration.py` (2) | Live OpenCode server. |
| `tests/integration/test_multi_turn_resume.py` (3) | LLM failure injection / transient errors. |
| `tests/e2e/test_migration_e2e.py` (3), `test_migration_e2e_comprehensive.py` (3) | `psycopg` + migration state. |

---

## Group 3 — RESOLVED (stale test fixtures, not code bugs)

**Investigated and fixed.** Both 3a and 3b traced to the same root cause:
a stale test-local helper. **No production code was changed** — all fixes
are in test fixtures.

### Root cause

`status_to_admission(status)` expects a *legacy status* string
(`"processing"`, `"paused"`, …). But the failing seeds call it with an
*admission-state* value, e.g. `status_to_admission(AdmissionState.ACTIVE.value)`
where `AdmissionState.ACTIVE.value == "active"`. Since `"active"` is not a
key in the legacy-only dict, it falls through to the default `"queued"` —
so every seeded "PROCESSING/PAUSED job" actually persisted with
`admission_state='queued'`.

The canonical helper (used in ~12 other test files, e.g.
`tests/unit/services/test_jq_proxy_phase2_dualwrite.py:86`) already
includes an `AdmissionState` identity map (`"active": "active"`, …). The
broken files predated that addition.

### 3a. `admission_state` `'queued'` vs `'active'` — FIXED

Consequence of the root cause: seeded jobs were `'queued'`, so
`assert admission_state == 'active'` failed and the atomic
`UPDATE ... WHERE admission_state='active'` matched 0 rows.

**Fix:** aligned the `status_to_admission` helper in
`tests/integration/test_cold_resume_ttl.py`,
`tests/integration/test_crash_recovery_paused.py`, and
`tests/test_report_lane_phase2.py` with the canonical version
(added the `AdmissionState` identity map).

A second, previously-masked stale-fixture bug also surfaced once 3a
cleared: two fixtures referenced the `status` column on `job_queue_items`,
which was **dropped in Phase 5** (`admission_state` is the sole write
authority). Fixed:
- `tests/test_report_lane_phase2.py::test_finalize_is_idempotent_via_atomic_transition`
  — raw SQL `SET status`/`WHERE status` → `admission_state`.
- `tests/integration/test_cold_resume_ttl.py::test_resume_db_sync_is_idempotent`
  — `.status` → `.admission_state`.

### 3b. Crash-recovery sweep finds nothing to recover — FIXED

`JobRecoveryService.recover_on_startup` sweeps for `admission_state='active'`,
but the seed persisted `'queued'` (root cause) → `total: 0`. Fixed by the
same `status_to_admission` alignment in
`tests/integration/test_crash_recovery_paused.py`.

### Verification

All 17 tests across the three files now pass in isolation:

```bash
.venv/bin/pytest --override-ini="addopts=" \
  tests/test_report_lane_phase2.py tests/integration/test_cold_resume_ttl.py \
  tests/integration/test_crash_recovery_paused.py -v   # 17 passed
```

### Follow-up (not blocking)

Several *other* test files still ship the legacy-only `status_to_admission`
and happen to call it only with legacy status strings, so they pass today.
They will break the moment a caller passes an `AdmissionState` value
(see `tests/message_queue_redesign/test_task_repository.py`, which already
calls `status_to_admission(AdmissionState.ACTIVE.value)` 9× through the
broken variant). Aligning the remaining ~25 copies to the canonical helper
is recommended cleanup.

---

## What IS green (the job-watch notification path)

All tests exercising the `7d38714e` fix pass, both here and in isolation:

- `tests/e2e/test_e2e_jober_orchestration.py` — both (new) `job_create` + `job_continue` phases.
- `tests/e2e/test_e2e_workflows.py` — parent-child, pause/resume, terminate/revive, wave/defer, pause-blocks-defer.

---

## Reproducing / triaging

```bash
# Full suite (slow; includes the polluters)
.venv/bin/pytest --override-ini="addopts=" -m integration

# Isolate a suspect test (avoids pollution — passes if it's Group 1)
.venv/bin/pytest --override-ini="addopts=" tests/integration/test_completion_report.py -m integration

# Confirm a failure pre-dates a change: revert the touched files to a prior commit
git checkout <prior-sha> -- daemon/services/<files>
.venv/bin/pytest --override-ini="addopts=" <test> -m integration
```

## Open follow-ups

1. **Group 3** — RESOLVED (see above). Optional cleanup: align the remaining
   ~25 legacy-only `status_to_admission` copies to the canonical helper
   before another caller passes an `AdmissionState` value.
2. **Group 1** — make the `sys.modules` polluters isolation-safe (or quarantine
   them) so the full suite is reliable.
