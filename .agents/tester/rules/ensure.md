# Quality Requirements

## How to use this file
- **Pack-mapped**: requirements reference packs in PACKS.md (or static checks), NOT bare `pytest` commands. Resolve each requirement to its pack before validating.
- **Scoped by blast radius**: validate only requirements relevant to the change set. Never run the full list unless blast-radius determines the change is big/critical/architecture (then also run the Release Gate).
- **Run as packs**: every validation executes as a pack (or ad-hoc pack) with the dual-layer 5-min timeout — NEVER as a bare, unbounded `pytest` command. Even the release-gate full suite runs via packs (parallel, each ≤ 5 min), not `pytest tests/`.
- **Quarantine-aware**: tests in `.agents/tester/QUARANTINE.md` are skipped and do not fail a requirement. Pre-existing failures must be quarantined, not left to permanently red the gate.
- **No `-x`**: never use pytest `-x` (stop-on-first-failure) for suite runs — it hides the full picture. Use `--tb=short -q` and review all failures.
- **Gate tiers**: any dual-side regression leg runs per the **Gate Tiers** section below — tier chosen BEFORE the run; recipes loaded from `.agents/tester/skills/` (deterministic path, never `skill_search` discovery).

## Gate Tiers (tiered gate doctrine — user-approved 2026-10-26/31)

Every regression gate picks its tier BEFORE running. Escalation Tier 1→2 is MECHANICAL (seam list below), never a judgment call. This section is the contract; execution detail lives in the recipe skill bodies under `.agents/tester/skills/` — those dirs are THE gate recipes.

- **Tier 0 — no regression leg.** Docs-only / `.agents/` / test-only diffs. Changed-pack PASS covers the gate.
- **Tier 1 — DEFAULT for feature lanes.** Blast-radius leg: scoped dual-side FAILED-list diff over the declared blast radius.
  - Budget: **≤ 30 min HARD, fail-loud** — abort + report + ONE auto re-scope attempt; never silent grinding.
  - Recipe: `.agents/tester/skills/blast-radius-leg/` — READ the skill body before executing.
- **Tier 2 — whole-tree dual-side.** Trigger: the diff touches a **core seam** — mechanical path-prefix list: `daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` (list grows only by amendment here) — OR the gate is promote-blocking (release gate).
  - Budget: ≤ 90 min advisory; fail-loud reporting still applies.
  - Recipes: `.agents/tester/skills/whole-tree-base-proven-diff/` or `.agents/tester/skills/regression-ab-partition-run/` — fast execution is baked into the bodies (xdist `-n auto` within a leg, or mrpr-style measured-rate shard sizing); consumers must NOT hand-roll slow runners.

All tiers — non-negotiable:
- **Mandatory recipe load:** before executing any gate leg, READ the recipe skill body for that tier. Gate-critical recipes must not depend on probabilistic `skill_search` discovery.
- **Node-cap + measured-rate projection guard:** before legs, project wall-clock from measured collection/test rate; projected OR actual breach → STOP + report + one re-scope.
- **Pre-exclusion registry** (excluded by default, WITH compensating pin coverage — wedge-capture pattern): `tests/performance/test_message_api_cost.py` (>300s solo, rc=124 at every bisection level); e2e cells requiring a live `:8079` daemon; `tests/packs/g7` sys.exit(0) collection trap.
- **Proof form (unchanged):** mechanical derivation → declared set → `--collect-only` slicing proof (executed == declared, counts recorded) → identical commands both legs → red-at-base ≠ net-new → 3× flaky isolation on diff nodes only. FAILED-digest comparison per `.agents/tester/skills/failed-digest-equality-gate/`.
- **Budget tension resolved:** the 5-min cap stays for individual test PACKS; gate-level budgets are the Tier budgets above (30/90 min). A gate leg composes packs/shards — each shard still runs under its own explicit timeout.

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
- **Queue cleanup before each test**: check `GET /api/jobs?status=pending` for leftover jobs. If any exist, clean them up (cancel/delete) before running — leftover pending jobs from previous runs block defer queue admission and cause false failures.
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
- [ ] E2E: 3-level cascade (leader→tester→staggered workers): reports delivered, no premature completion, no stuck completion, state switching
  - Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` or `PYTEST_TIMEOUT=280 timeout 320 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_three_level_cascade_reports" --tb=short -q`
