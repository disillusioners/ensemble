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

## Group 3 — Pre-existing, **possibly real code bugs** (NOT investigated)

These fail on the previous commit too (confirmed), but the assertions look like
they may reflect genuine defects — separate from the job-watch notification
subsystem. Worth a dedicated investigation.

### 3a. `admission_state` `'queued'` vs `'active'` default mismatch

Recurring pattern across multiple tests:

```
AssertionError: Job must transition PAUSED → PROCESSING (re-armed)
assert 'queued' == 'active'
  tests/integration/test_cold_resume_ttl.py:238
```

The same mismatch also breaks the standalone
`tests/test_report_lane_phase2.py::TestCrashRecovery::test_finalize_is_idempotent_via_atomic_transition`
(which fails even in isolation, on every commit including pre-`7d38714e`).

**Hypothesis:** a JobItem created in test fixtures (or by a real path) defaults
to `admission_state='queued'` but the code/test expects it to have been admitted
to `'active'`. Could be a stale test fixture, or a real admit-path regression.

### 3b. Crash-recovery sweep finds nothing to recover

```
assert stats == {"recovered": 1, "alive": 0, "total": 1}
AssertionError: assert {'recovered': 0, 'total': 0} == {'recovered': 1, 'total': 1}
  tests/integration/test_crash_recovery_paused.py (4 tests)
```

The recovery sweep returns `total: 0` — it is not finding the PROCESSING jobs
the fixture created. Could be a fixture issue (jobs not in the expected state)
or a real recovery-query bug.

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

1. **Group 3a/3b** — investigate whether the `queued`-vs-`active` default and the
   `recovered: 0` recovery sweep are real bugs or stale fixtures.
2. **Group 1** — make the `sys.modules` polluters isolation-safe (or quarantine
   them) so the full suite is reliable.
