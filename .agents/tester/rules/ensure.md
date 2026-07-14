# Quality Requirements

## How to use this file
- **Pack-mapped**: requirements reference packs in PACKS.md (or static checks), NOT bare `pytest` commands. Resolve each requirement to its pack before validating.
- **Scoped by blast radius**: validate only requirements relevant to the change set. Never run the full list unless blast-radius determines the change is big/critical/architecture (then also run the Release Gate).
- **Run as packs**: every validation executes as a pack (or ad-hoc pack) with the dual-layer 5-min timeout — NEVER as a bare, unbounded `pytest` command. Even the release-gate full suite runs via packs (parallel, each ≤ 5 min), not `pytest tests/`.
- **Quarantine-aware**: tests in `.agents/tester/QUARANTINE.md` are skipped and do not fail a requirement. Pre-existing failures must be quarantined, not left to permanently red the gate.
- **No `-x`**: never use pytest `-x` (stop-on-first-failure) for suite runs — it hides the full picture. Use `--tb=short -q` and review all failures.

## Core (always-on, fast, pack-mapped)
Validated on every test request, scoped to the change set's packs.

### Critical
- [ ] No regressions in changed packs — every pack in the blast-radius change set returns PASS
  - Validation: run the scoped packs (see PACKS.md); all PASS
- [ ] Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS (includes `test_deadlock_fix.py`, cascade races, observer race, instance/project atomic locks)
  - Validation: `timeout 300 bash <concurrency pack>` or equivalent pack run
- [ ] No sync DB calls on the asyncio event loop — covered by `concurrency_atomic_unit_test` (thread-identity tests verify `asyncio.to_thread` wrapping for all DB helpers)
  - Validation: pack PASS
- [ ] `dev.sh` includes `--timeout-graceful-shutdown 10`
  - Validation: static file check (grep `dev.sh`) — fast, no pytest

### Important
- [ ] All callers of converted async functions properly await (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`)
  - Validation: grep / static check
- [ ] Original deadlock scenario (parent→child→complete) works without blocking
  - Validation: covered by `concurrency_atomic_unit_test`

### Nice-to-have
- [ ] No dead code from the fix (deleted code was truly unused)
  - Validation: import check / grep

## Release Gate (slow — big/critical/architecture changes ONLY)
Run ONLY when blast-radius determines the change is big/critical (cross-module, architecture refactor, release). Each item must still run under a `timeout` wrapper; prefer converting each to a mock-test pack (daemon mocked) so it runs under the 5-min cap without `./dev.sh`.

### Prerequisites
- Daemon running: `./dev.sh` (health at `localhost:8079`)
- SSL certs clean: `unset SSL_CERT_FILE SSL_CERT_DIR` before each run
- Timeout override: `PYTEST_TIMEOUT=280` + `--override-ini="timeout=280"` (pyproject default `timeout=30` kills E2E prematurely)
- Run tests **one by one** (each makes real LLM calls; combined exceeds 5-min cap)

### Critical (release-gate)
- [ ] Full non-integration suite green (excluding QUARANTINE.md)
  - Validation: run ALL non-integration packs (see PACKS.md) in parallel, each with the 5-min cap; quarantined tests skipped. NOT a bare `pytest tests/` — run via the packs.
- [ ] E2E: Normal parent→child workflow completes (happy path)
  - Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` or `PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_parent_child_workflow_happy_path" --tb=short -q`
- [ ] E2E: Pause after spawn, then resume works correctly
  - Validation: same pattern, `-k "test_pause_after_spawn_then_resume"`
- [ ] E2E: Terminate after spawn, then revive documented
  - Validation: same pattern, `-k "test_terminate_after_spawn_then_revive"`
- [ ] E2E: Wave spawn (2 children) + defer queue ordering + cross-system
  - Validation: same pattern, `-k "test_wave_spawn_with_defer_queue"`
