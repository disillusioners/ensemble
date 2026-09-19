# Flaky test: chat saturation A2.2 `workers_woken_by_timeout delta==0` — load-context jitter (pre-existing, NOT branch-caused)

- **Date:** 2026-09-19 (gate for `feature/chat-source-live-injection` @ 90385689, base `306468f8`)
- **Test:** `tests/integration/test_chat_source_saturation_isolation.py::TestSaturationIsolation::test_chat_message_claimed_by_chat_worker_under_saturation`
- **Registered pack:** `test/packs/regression_chat_source_integration_test.sh` (chat slice, ex-P-12)
- **Failure mode:** `AssertionError: A2.2 violation: workers_woken_by_timeout DELTA over [enqueue, claim] = 1 (expected 0). The notify path did NOT deliver the claim; the chat worker woke via the 3s poll fallback.` Claim latency itself healthy (observed 0.042s vs ≤3.0s bound) — the assertion trips when ONE 3s poll-loop tick lands inside the [enqueue, claim] measurement window under CPU/scheduler contention.

## Retry budget (protocol 3×, no code change)
| Run context | Result |
|---|---|
| 2-file ad-hoc pairing (enqueue_wake_e2e + saturation_isolation), run immediately after 12-file chat pack | **F** (delta=1) |
| Same 2-file pairing, solo ×3 @ HEAD 90385689 | P / P / P (4.2–4.4s) |
| Same 2-file pairing, solo ×3 @ BASE 306468f8 (detached worktree /tmp, removed after) | P / P / P (4.2–5.2s) |
| Full 12-file chat pack @ HEAD (57 tests) | P (node green in-pack) |
| Prior gates (chat-lane-followups, chat-source-worker-lane) | P (3/3 and 50/50) |

→ ≥1P + ≥1F ⇒ **FLAKY**. Adjudication: **PRE-EXISTING load-sensitive flake** — base reproduces clean (3/3) AND HEAD pairing reproduces clean (3/3); the single failure correlates with post-pack machine load, not with the branch delta (which does not touch the enqueue→wake path).

## Root cause (read-only inspection)
- NO shared test state: `integration_config(tmp_path)` function-scoped; `build_live_pool_manager(engine)` builds a fresh `InstanceManager` (own default+chat pools, `WorkerPool.stop(timeout=10)`) per test; `chat_lane_flag_reset` function-scoped autouse; per-test file-backed SQLite (NullPool+WAL); no module-level singletons (`pool_orchestrator.py`, `worker_pool.py` define classes only).
- Genuine mechanism = pure OS-scheduling contention: saturation blocks the default pool, then asserts the chat worker is woken by NOTIFY (counter delta==0) within [enqueue, claim]. Under load the 3s poll tick can fire inside that window → counter +1 → red despite a healthy notify path.

## Quarantine decision: **NOT quarantined (MONITORED)**
Rationale: the node has never failed inside its REGISTERED pack context (chat pack green at 3 consecutive gates incl. this one); quarantining would remove the A2.2 notify-path headline pin from the regression net — a coverage loss out of proportion to a 1-of-7 load-context flake. 
**Monitoring condition:** quarantine immediately (per protocol) the moment this node fails INSIDE `regression_chat_source_integration_test.sh` or any registered pack.

## Follow-up candidates (test-architecture, tester-owned backlog; NOT applied during the frozen-branch gate)
1. Process isolation: run the saturation node in its own pytest invocation (own pack or forked) so it never shares a scheduler with sibling harness tests.
2. Assertion hardening (needs owner decision — weakens a pin): allow one grace tick (`delta<=1` / `timeouts_after<=1`) or replace counter check with `elapsed<=3.0` + relative latency threshold.
3. Do NOT relax without a decision — `delta==0` is the A2.2 notify-path proof; relaxation trades a real signal for stability.
