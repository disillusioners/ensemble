# Phase 3: Tests — Integration + Saturation-Isolation + Regression Pins

## Objective

Prove the chat lane works end-to-end: strict two-way isolation under default-pool saturation, non-inheritance e2e (slack → ari → child → default lane), mixed-provenance same-instance across lanes, chat-lane saturation queueing, and boot/shutdown lifecycle. Pin the watchdog regression suites to confirm the recovery services remain unaffected.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Integration test — **saturation isolation (headline)**: default pool FULLY saturated by 5 long-running jobs → enqueue 1 chat message → assert claim time ≤ 3.5s. **MANDATORY architect A2.2 assertion — reviewer F11 semantics:** read `WorkerPool.get_stats["workers_woken_by_timeout"]` as a **DELTA** over `[message.enqueued_at, task.claimed_at]` via **TWO reads** (a before-enqueue snapshot and an at-claim read; assert `delta == 0`). **FORBID pre-test zeroing** of the counter — a live worker may concurrently increment it (an idle worker timing out for unrelated reasons) and a zeroed baseline races real increments, masking the regression the assertion is designed to catch. The two-read DELTA is the only race-free assertion. Hooking point: `WorkerPool.get_stats` (`daemon/services/worker_pool.py:1425-1455`) already exposes `workers_woken_by_timeout`; no new code required. The assertion proves the **notify path** (D5 fan-out, not the 3s poll fallback) delivered the claim. Without this assertion, success criterion #3 cannot distinguish the two paths (3.5s assert passes by accident on the poll path) and the feature's headline guarantee is untestable. | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_saturation_isolation.py`. Test passes deterministically (no race-dependent outcomes). |
| 2 | Integration test — **non-inheritance e2e (mandatory, USER CLARIFICATION D3)**: register a slack source, mint a `telegram:alice` row, simulate ari-style processing on chat lane, simulate ari calling `job_create` (which stamps `agent:ari` per `job_queue.py:712-714`), enqueue that child row, assert the child is claimed by a DEFAULT worker (`task.worker_id` starts with `worker-` not `chat-worker-`). | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_non_inheritance_e2e.py`. The two `task.worker_id` strings in the lineage start with different prefixes. |
| 3 | Integration test — **mixed-provenance same-instance**: enqueue 1 chat-prefixed + 1 default-prefixed row for the same `instance_id`. Process both. Assert both tasks complete (per-instance RUNNING guard serializes them). Assert no checkpoint corruption (verify via `checkpoints` table or equivalent). | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_mixed_provenance.py`. |
| 4 | Integration test — **chat-lane saturation queueing (SC#6, architect amendment A4.1, reviewer F10)**: enqueue 4 chat-prefixed rows on a 2-worker chat pool. Assert 2 run concurrently, the 3rd waits ≤ 3s **measured FROM ENQUEUE** before being claimed, the 4th waits ≤ 6s **measured FROM ENQUEUE**. No deadlock. **CONDITIONAL on fixture-validation Task #4a** (next row) — the criterion measures queueing from enqueue, and Task #4a is the enforcing gate that makes the from-enqueue measurement meaningful (short first-two tasks). | Phase 1, Phase 2, Task #4a | New file `tests/integration/test_chat_source_lane_saturation.py`. |
| 4a | **NEW — Fixture-validation task (architect amendment A4.1, reviewer F10):** before the SC#6 chat-lane saturation test can pass as written (≤3s measured from enqueue), the test fixtures must be VALIDATED to satisfy the "first two chat tasks are SHORT" precondition. Add an explicit fixture-validation sub-task: the test fixtures must pin/verify that the first two concurrent tasks complete in ≤ 1s (or a similar short bound), so the 3rd-message pickup timing measures queueing latency, not the duration of the first two in-flight tasks. **If the fixtures cannot be made short (e.g., real agent graph takes > 1s to even start), SC#6 ESCALATES BACK TO PLAN REVISION** (not in-test reinterpretation — the reviewer F10 branch that allowed "OR reframe to from-worker-return" is STRUCK). The from-worker-return latency fact remains documented in D10.2 case (ii) for operator understanding, but does NOT alternate inside SC#6. | Task 4 | Fixture-validation pass: short-task assertion. If fixtures cannot satisfy the short-task bound, escalate as a plan revision before shipping. |
| 5 | Integration test — **strict two-way isolation**: enqueue 10 chat rows + 10 default rows; run with default pool=5 and chat pool=2; assert NO row is processed by the wrong pool (chat rows → chat worker_ids, default rows → default worker_ids). | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_two_way_isolation.py`. Assertable deterministically — each row's `task.worker_id` prefix must match its `message_queue.source` prefix class. |
| 6 | Integration test — **`USE_WORKER_POOL=false` disables both pools**: set env var, boot, assert both `manager._worker_pool` and `manager._chat_worker_pool` are None; assert the boot log line `"Worker pool disabled"` is present; assert subsequent chat messages do NOT execute (they remain in `message_queue.status='ready'`). | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_kill_switch.py`. |
| 7 | Integration test — **boot-line observability** (approver E4 — in-process log capture): boot the daemon; assert `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"` appears in the LOG HANDLER (in-process capture via `caplog`/`capfd` fixture — DO NOT parse `data/logs/ensemble.log` from the filesystem; the in-process capture is deterministic and avoids log-file race conditions across concurrent test runs). **Precedent: `tests/unit/job_queue/test_joblock_sweep_lifecycle.py`** uses the same in-process log capture pattern for boot-line assertions on JobLockSweep — reuse the same `caplog`/`capfd` idiom. Substring match is acceptable (e.g., assert `"ChatSourceWorkerPool started"` is in any captured record's formatted message). | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_boot_line.py` OR add to existing boot-line test suite. |
| 8 | Integration test — **shutdown teardown**: boot 2 pools; invoke `shutdown_worker_pool`; assert both pools stopped; assert log line `"Chat worker pool stopped"` present; assert threads joined within 30s. | Phase 1, Phase 2 | New file `tests/integration/test_chat_source_shutdown.py` (overlaps with Phase 2's file — Phase 3 adds the multi-pool assertion). |
| 9 | Regression pin — **watchdog services unaffected** (approver N5 — real paths verified via `find tests -name "*joblock*" -o -name "*job_recovery*" -o -name "*waiting_children*"`): `tests/integration/test_boot_report_recovery.py`, `tests/job_queue/test_job_recovery_service.py`, `tests/unit/job_queue/test_joblock_sweep_lifecycle.py`, `tests/unit/services/test_waiting_children_watchdog.py`, `tests/integration/test_wc_wake_pure_hang.py`. | Phase 1, Phase 2 | All existing watchdog/recovery tests pass. Document in PR that no behavior change was expected (C.1 pool-agnostic invariant). |

---

## Coupling

- **Tight with:** Phase 1 (predicate behavior), Phase 2 (wiring, boot-line, shutdown, late-wire). All Phase 3 tests exercise code shipped in Phase 1/2.
- **Loose with:** None.
- **Independent of:** Phase 1/2 only in the sense that Phase 3 doesn't introduce new code — only tests.

**Shared contracts (consumed from Phase 1/2):**
- `claim_pending_task(worker_id, lane)` — strict two-way guarantee.
- `manager._chat_worker_pool`, `manager._notify_all_pools()` — for boot/shutdown/wake tests.
- Boot-line string — for log-capture.
- `WorkerPool(worker_id_prefix=...)` — for worker_id lineage in non-inheritance test.
- `is_chat_source()` + forged-source gate — for HTTP-level tests (carried from Phase 1).

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Saturation test timing is racy — 5 long jobs may finish before chat message is enqueued. | High | The "long jobs" should be simulated via a `block` task type or a controlled-`asyncio.sleep`-inside-handler pattern; assert the chat message's `task.claimed_at - message_queue.enqueued_at ≤ 3.5s` (timing window, not assertion of order). |
| Notify-not-poll assertion (A2.2, reviewer F11) requires reading `workers_woken_by_timeout` as a DELTA via TWO reads (before-enqueue snapshot + at-claim read); a pre-test zeroing would race live increments from unrelated idle-worker timeouts and mask the regression. | Medium | The two-read DELTA over `[enqueued_at, claimed_at]` is the race-free assertion. `WorkerPool.get_stats["workers_woken_by_timeout"]` (`daemon/services/worker_pool.py:1425-1455`) is the existing metric — no new code. The metric is already pinned in D12's stat parity. |
| Chat-saturation SC#6 fails because fixtures don't satisfy the "first two tasks are SHORT" precondition (A4.1). | High | Task #4a (fixture-validation gate) catches this BEFORE the criterion fails; if fixtures cannot be short, reframe SC#6 to measure queueing latency from worker-return (D10.2 case ii) rather than from enqueue. Document the reframe in the test docstring. |
| Non-inheritance e2e requires simulating the full registry mint → enqueue_message_job → claim → process → job_create → claim. Long. | Medium | Use the same harness as `test_wc_wake_pure_hang.py:402-493` (FileBackedSQLite + DispatchEventBus + JobProcessor). Mock `job_create`'s internal call to enqueue directly without going through the agent's graph (test the seam, not the full execution). |
| Mixed-provenance test could race if per-instance RUNNING guard fails. | Medium | Assert both tasks complete sequentially; per-instance guard is the existing gate at `repository.py:1692-1756` — Phase 1 widens only the chat lane; guard is unchanged. |
| Chat-lane saturation test could deadlock if the chat pool's `_invoke_semaphore` interaction is wrong (D8). | High | The chat pool is sized 2, max chat invokes blocked = 1 (cap 4 minus at least 1 free default worker). Test asserts NO deadlock over a 30s wall-clock window. If observed, escalate as a Phase 3 bug, do NOT silently widen semaphore. |
| Two-way isolation test could miss a misrouted row if the row count is too small. | Low | 10+10 rows × strict prefix assertions = 20 distinct assertions; misrouting probability per row < 1/N. |
| `USE_WORKER_POOL=false` test could leak env state into other tests. | Medium | Use `monkeypatch.setenv` + `monkeypatch.delenv` in pytest fixtures; restore on teardown. |
| Watchdog regression pin could fail for reasons unrelated to this feature (pre-existing test flakiness). | Low | Run the full existing test suite BEFORE starting Phase 3 to establish baseline. If a watchdog test fails in baseline, document and skip (do NOT chase). |

---

## Exit Criterion

**Phase 3 is complete when:**

0. **B1 conditional fail-open cross-reference (approver S4 — no duplicate home):** Phase 3 does NOT re-implement the B1 fail-open test. The fail-open test lives in Phase 2 at `tests/integration/test_chat_source_pool_wiring.py` (function `test_b1_conditional_fail_open_chat_pool_absent_then_present`, case (a) chat pool ABSENT → default claims; case (b) chat pool PRESENT → strict two-way). Phase 3 cross-references it in the strict two-way isolation test (Task #5 below): the two-way assertion pins `worker_id` lineage AND the absence of misrouted rows under P1+P2 normal state (the flag-True case). The chat pool ABSENT case is the explicit phase2 responsibility.

1. ALL new integration tests pass (`uv run python -m pytest tests/integration/test_chat_source_*.py -v`).
2. ALL existing watchdog/recovery tests still pass — zero new failures attributable to this feature (`uv run python -m pytest tests/integration/test_boot_report_recovery.py tests/integration/test_wc_wake_pure_hang.py -v`).
3. End-to-end success criteria from `plan-overview.md` are met:
   - SC1: Chat messages on chat prefixes claimed by chat workers (Task #1, #5).
   - SC2: Default messages claimed by default workers (Task #5).
   - SC3: Default saturated → chat picked up ≤ 3.5s **AND** no `wait_for_work(3.0s)` timeout-expiry wake observed during the chat-claim window (Task #1 + A2.2 notify-not-poll assertion). The two assertions together prove the notify path delivered the claim — the 3.5s assert alone cannot distinguish from the poll fallback.
   - SC4: Non-inheritance e2e (Task #2).
   - SC5: Mixed-provenance same-instance (Task #3).
   - SC6: Chat saturation queueing — 3rd chat message claimed ≤3s **measured FROM ENQUEUE** (Task #4 **CONDITIONAL on fixture-validation Task #4a** — first two chat tasks must be SHORT, else SC#6 escalates back to plan revision per reviewer F10). The from-worker-return latency fact (D10.2 case ii) remains documented for operator understanding but does NOT alternate inside SC#6.
   - SC7: `USE_WORKER_POOL=false` disables both (Task #6).
   - SC8: Boot-line present (Task #7).
   - SC9: Shutdown clean (Task #8).
   - SC10: Forged-source gate (Phase 1 carry; re-verified here).
   - SC11: Constant pins (Phase 1 carry; re-verified here).
   - SC12: Watchdog regression (Task #9).
   - SC13: Engine budget (static check; no test needed).
   - SC14: Strict two-way isolation (Task #5).
4. A phase-level integration test gate (below) passes.

---

## Test Gate (phase-level)

A new commit on `feature/chat-source-worker-lane` containing ONLY Phase 3 changes (cumulative with Phase 1 + Phase 2) must pass:

```bash
# Unit + integration — full suite
uv run python -m pytest \
  tests/unit/test_constants.py \
  tests/unit/test_repository_claim_lane.py \
  tests/unit/test_jobs_crud_chat_source_gate.py \
  tests/unit/test_worker_pool_prefix.py \
  tests/integration/test_chat_source_pool_wiring.py \
  tests/integration/test_chat_source_shutdown.py \
  tests/integration/test_chat_source_late_wire.py \
  tests/integration/test_chat_source_saturation_isolation.py \
  tests/integration/test_chat_source_non_inheritance_e2e.py \
  tests/integration/test_chat_source_mixed_provenance.py \
  tests/integration/test_chat_source_lane_saturation.py \
  tests/integration/test_chat_source_two_way_isolation.py \
  tests/integration/test_chat_source_kill_switch.py \
  tests/integration/test_chat_source_boot_line.py \
  -v

# Watchdog regression pin (approver N5 — real paths)
uv run python -m pytest \
  tests/integration/test_boot_report_recovery.py \
  tests/integration/test_wc_wake_pure_hang.py \
  tests/job_queue/test_job_recovery_service.py \
  tests/unit/job_queue/test_joblock_sweep_lifecycle.py \
  tests/unit/services/test_waiting_children_watchdog.py \
  -v
```

---

## New Test Files (full list, with file-by-file intent)

| File | What it asserts |
|---|---|
| `tests/integration/test_chat_source_saturation_isolation.py` | Default pool FULLY busy → chat message claim ≤3.5s |
| `tests/integration/test_chat_source_non_inheritance_e2e.py` | Slack → ari chat lane → spawn child → default lane |
| `tests/integration/test_chat_source_mixed_provenance.py` | Same instance, two sources, both processed sequentially |
| `tests/integration/test_chat_source_lane_saturation.py` | Chat pool FULLY busy → 3rd, 4th chat messages queue cleanly |
| `tests/integration/test_chat_source_two_way_isolation.py` | No row misrouted, regardless of pool state |
| `tests/integration/test_chat_source_kill_switch.py` | `USE_WORKER_POOL=false` → both pools None |
| `tests/integration/test_chat_source_boot_line.py` | Boot-line log substring present within 60s |
| `tests/integration/test_chat_source_shutdown.py` | Both pools stop, log line present, threads joined |

(Phase 2 already created `test_chat_source_pool_wiring.py` + `test_chat_source_late_wire.py` — Phase 3 does not duplicate these.)

---

## Test Harness Pattern

The integration tests follow the canonical harness at `tests/integration/test_wc_wake_pure_hang.py:402-493`:

```python
# Per-test fixture pattern
@pytest.fixture
async def dual_pool_manager(file_backed_sqlite):
    manager = InstanceManager.__new__(InstanceManager)  # bypass __init__
    # ... wire engine, repos, event bus, job service, etc.
    manager.setup_worker_pool(num_workers=1)  # default pool (test-only size)
    # Phase 2's manager method also constructs the chat pool
    # ... or manual chat pool construction mirroring Phase 2 code
    yield manager
    manager.shutdown_worker_pool()
```

For the **non-inheritance e2e**, the harness additionally:
- Stubs a minimal `TaskProcessor` that processes messages by enqueueing a follow-up `job_create`-shaped row (skipping the full graph execution — test the seam, not the agent).
- Asserts `task.worker_id` lineage via DB queries.

---

## Out-of-phase notes (deferred)

These are explicitly NOT in Phase 3 — they are P3 backlog items:
- `/readyz` exposure of pool stats (decision D12 deferred).
- `_worker_pool_size` DEAD-attribute removal (Phase 2 Task #8 optional).
- Wider forged-source gate (all 18 `RESERVED_SOURCE_PREFIXES`; backlog TOP item).
- Default-lane HOL fix (wedge-class IV, `9fe96dea`).
- Per-lane invoke semaphore split (D8 rejected — only revisit if needed).
- Wakeup-efficiency regression monitoring (Task #4 risk — defer until measured).

---

## Implementer Notes (reviewer findings 13-22 — deferred, NOT design fixes)

These are notes for the executing coder. They are NOT changes to the plan design text — read them BEFORE coding Phase 3 so the tests assert the right thing.

- **(i) Saturation-test harness size coupling.** The headline saturation-isolation test (Task #1, default pool FULLY saturated by 5 long-running jobs) requires a default pool with `num_workers=5` to instantiate the SC#3 5-job saturation. The harness pattern at `test_wc_wake_pure_hang.py:411` uses `setup_worker_pool(num_workers=1)` — that pattern CANNOT instantiate the 5-job default saturation. The harness must override the default pool size to 5 (e.g., `setup_worker_pool(num_workers=WORKER_POOL_SIZE)`) AND construct the chat pool at `CHAT_WORKER_POOL_SIZE=2`. The saturation-test SC#3 job count MUST match the constructed default-pool size (flag the coupling). If the implementer wants to test with a smaller pool, the SC must be re-scoped.

- **(j) Task #2 prefix alignment.** The Task #2 scenario says "slack message" but the rows/fixtures in the same task use `telegram:` prefix. **Align source used vs prefix asserted** — pick ONE chat prefix and stick to it throughout the test (e.g., use `telegram:alice` consistently for the chat-prefixed row, and assert `chat-worker-` claim lineage). Mixing `slack:` in narrative and `telegram:` in fixtures creates a misleading test name; the canonical phase-3 plan uses `telegram:` for chat-source fixtures (the test filename `test_chat_source_non_inheritance_e2e.py` is general; the chat source used in the fixture should be `telegram:alice` or `slack:U123` — pick one, document in the test docstring).

- **(k) SC#13 vs D9 mental-model alignment.** SC#13 in `plan-overview.md` uses subtractive-margin wording (`5+2 ≤ 15-2 = 13`); D9 uses multiplicative framing (`5+2=7 ≤ 15`, 2.1× workers-only margin). **D9 is the AUTHORITATIVE full-engine picture** (15-conn engine also serves HTTP handlers + 5 sweeps; realistic worst ≈ 12-15 at cap). The implementer should keep ONE mental model — D9 full-engine framing. SC#13 subtractive margin is the conservative backstop for the workers-only view (margin shrinks to 13 under sustained API load), but it must be read in light of D9 full-engine cap.

- **(l) Non-inheritance e2e stub source stamping.** The non-inheritance e2e stub (Task #2 harness note) MUST **STAMP** `source="agent:stub_caller"` on the spawned child row at the enqueue the stub performs. **NEVER pass `source=` into the stubbed `job_create` call** — `job_create` source parameter is **DEPRECATED and IGNORED** per `job_queue.py:680` (DEPRECATED and IGNORED, NIT-7, P2.3 review cycle 1: the server derives source UNCONDITIONALLY since B3.5 — `agent:<caller>` for agent callers, `internal_agent:unknown` otherwise — any value passed here has no effect). The stamping MUST happen at the enqueue the stub performs (mirror the real `job_queue.py:712-714` server-stamp). Without correct stamping, the child row arrives with `source=None` or the stub local var — wrong lane; the test fails spuriously.

- **(m) Strict two-way isolation test — pin interleaved enqueue order.** The strict two-way isolation test (Task #5, 10 chat rows + 10 default rows) MUST **PIN** the interleaved enqueue order as `chat, default, chat, default, …` (or another deterministic pattern) so both pools demonstrably skip each other rows in a known order. A random order makes the test non-deterministic — a misrouted row could pass by chance; an interleaved pin ensures both pools see their own rows AND see the other pool rows arriving in a verifiable sequence. Document the chosen order in the test docstring.