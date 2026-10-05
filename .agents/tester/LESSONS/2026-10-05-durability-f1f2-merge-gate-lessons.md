# Durability F-1/F-2 merge-gate lessons (2026-10-05)

Commission: merge gate for `feature/durability-f1-f2` @ code-state `fb0f4655` (base `18827dbd`). Full report: `RESULTS/2026-10-04-durability-f1f2-merge-gate.md`. Five transferable lessons:

## 1. Straddle E2Es: `kill -TERM` CANNOT capture the wedge — use SIGSTOP→verify→SIGKILL
`kill -TERM` graceful-drain DELIVERS pending wakes before death (observed: report_injections reached TASK_DELIVERED + parent healed via watchdog → the "straddle" wasn't one). Deterministic capture: fast DB poller (≤3ms), `kill -STOP` in the SAME poll iteration that observes the precondition, DB-verify while frozen, then `kill -KILL` (hard death = the crash-durability model). Disclose the TERM→KILL deviation with rationale. Sub-100ms windows (marker mint ~70ms; wake claim <1s idle) are otherwise uncatchable at tool speeds.

## 2. Live-LLM tests defeat thread-mode pytest-timeout — `--timeout-method=signal` is the sweep belt
Fused-judge tests (`leader_completion_gate_fused_judge`, `model=quick`) block in C-level `ssl.recv`; the project's `timeout_method=thread` fires banners but cannot interrupt the syscall → whole 1500s chunks stall on ONE test (twice: `test_turn_state_machine` [QUARANTINE row 66], `test_attestation_bound_escalation`). Invocation-level `--timeout-method=signal` (SIGALRM kills the recv at the 30s cap) unblocks the chunk; no file changes. Live-LLM-hang tests classify env-class (A/B-confirmed the attestation hangs identically at base).

## 3. A/B branch-caused verdicts with IMPORT errors need a dual-venv re-run before they're final
`test_mcp_tool_timeout` (ModuleNotFoundError langgraph._internal) passed base / failed branch in the sweep → per-A/B "branch-caused". Post-wrap: uv.lock diff EMPTY, file identical, 12/12 re-pass in BOTH venvs — transient venv state (the shared wt-auto-continue venv shadowing). Rule: import/error-class branch-caused entries get `git diff base..pin -- uv.lock` + a same-day dual-venv re-run before entering the final verdict.

## 4. Evidence commits advance HEAD — pin the CODE STATE, not the hash
The G4 lane's mandated evidence commits (3×) moved HEAD past the commissioned pin mid-sweep, hard-stopping lanes that checked hash equality. Protocol that survived three more evidence commits: preflight = `git diff --name-only <pin>..HEAD | grep -vE '^\.agents/' | wc -l` must be 0 → record actual HEAD as execution-HEAD-of-record. (Also: workers with a queued first message REJECT follow-up sends — let them stop-and-report, then correct.)

## 5. Inventory artifacts need existence checks + coverage reconciliation
The read-only inventory produced a PHANTOM file (`test_wanderer_orchestrator_e2e.py` — never committed; probably conflated with `test_e2e_jober_orchestration.py`) and under-counted tests/ root files (~3,500 unassigned to any chunk). Chunk plans must (a) `ls`-verify every explicitly listed path pre-dispatch, and (b) reconcile Σ(per-chunk collected) against the full-tree collect — the root-file lane (E) closed the gap only because the discrepancy was flagged at planning time.

## 6. Seeded-shape tests do not guard the live seam — the LIVE proof is the only truth (re-gate 2026-10-05)
The lane-6 fix shipped with a PG-seeded test (`test_stuck_wake_lane_heals_captured_wedge_on_pg`) that PASSES while the LIVE capture fails: the seed satisfies the code's join key (`ri.child_message_id = mq.message_id`) but real data does not (`mq.message_id ≠ ri.child_message_id`; the real correlation is the `source` pattern — phase1-plan §3 warned about exactly this id-semantics split). Rule: a recovery-path fix is NOT proven green until the LIVE SIGSTOP capture heals; seeded tests establish intent, not seam-truth. When a live proof contradicts a seeded test, suspect the JOIN/ID key first.

## 7. Serialize every PG-lane dispatch against the shared `ensemble_test` DB
Two concurrent PG-lane workers produced a mass teardown FK error (`cannot drop table projects … snapshots_project_id_fkey`) and one false test failure (`list_queues` IntegrityConstraintViolation) that cost a 3-run adjudication. `tests/postgres/conftest.py`'s xdist guard documents the race — it applies EQUALLY to concurrent agent-dispatched pytest runs, not just xdist workers. Rule: at most ONE worker touching `ensemble_test` at a time; queue PG batches behind a pg_stat_activity contention check (and check that a SECOND follow-up message can even be delivered — workers with a pending first message REJECT follow-ups; let them report first).
