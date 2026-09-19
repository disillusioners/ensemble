# Plan Overview: chat-source-worker-lane

**Date:** 2026-09-19
**Author:** planner[v2] via plan-creation worker
**Status:** Draft (Ready for Review)
**Repo:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
**Branch:** `feature/chat-source-worker-lane` @ `c8855e8a`
**Plan output dir:** `.agents/shared/planning/chat-source-worker-lane/`

> A SEPARATE worker pool ("chat lane", DEFAULT 2 WORKERS) for work originating from chat sources (Telegram, Slack, Discord). The chat lane does NOT share the current default worker pool (WORKER_POOL_SIZE=5). The lane is reserved for the interactive human-facing conversation loop; agent fan-out remains on the default lane. The decisive mid-flight user clarification (record in `decisions.md` D3): lane assignment is PER QUEUED ITEM, keyed on the row's source provenance, NOT inherited from the parent instance.

---

## Objective

Ship a dedicated worker pool of 2 threads that claims exclusively from `message_queue` rows whose `source` matches `telegram:`, `slack:`, or `discord:` — leaving the existing 5-worker default pool to serve all other provenance. Chat users get bounded real-time latency even when the default pool is saturated by heavy background work; agent fan-out from chat originators still executes on the default lane (non-inheritance).

**Testable sentence:** "A user sends a slack message while the default pool is fully saturated by 5 long-running jobs; the chat pool picks up the message within ≤3s of arrival, executes it on a chat worker, and any subsequent `job_create` from that chat agent's response is processed by a default worker (not a chat worker)."

---

## Scope

### In Scope

1. New `CHAT_WORKER_POOL_SIZE=2` and `CHAT_SOURCE_PREFIXES=("telegram:","slack:","discord:")` hardcoded constants in `daemon/constants.py`, with `test_constants` value + completeness pins.
2. Source-prefix predicate at the claim seam (`daemon/repositories/task/repository.py`) — `claim_pending_task(worker_id, lane="default")` with correlated `EXISTS` on `message_queue.source` filtered by prefix. Two-way strict isolation (no overflow).
3. `WorkerPool` extension: lane parameter plumbed through `TaskProcessor.claim_task` (`daemon/services/task_processor.py`); distinct `worker_id_prefix` constructor kwarg; shared `TaskProcessor` singleton.
4. Manager wiring: second pool constructed in `setup_worker_pool` (`daemon/manager.py`); `shutdown_worker_pool` teardown entry; `_notify_all_pools` wake fan-out helper; `USE_WORKER_POOL` kill-switch respected.
5. API late-wire widening: `set_work_resolver` + `set_watcher_repo` on both pools (`daemon/api.py:447-449`).
6. Forged-source gate extension at `daemon/routers/jobs_crud.py:478-495` to also reject `source ∈ {telegram:, slack:, discord:}` from HTTP (D10.1, coordinates with the 2026-09-18 BACKLOG LEDGER TOP item).
7. Boot-line observability: `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"` (matches `api.py:771-775` convention).
8. Test surface: unit (predicate, constants, gate, pool seam), facade-forwarding guard tests modeled on `test_manager_enqueue_message_work_id_required.py` + `test_job_driven_enqueue_work_id_facade.py`, integration (default-saturated chat pickup, non-inheritance e2e, mixed-provenance, chat-saturation, boot/shutdown), watchdog-regression regression pins.

### Out of Scope (with reason)

1. **Default-lane HOL (head-of-line) fix (wedge-class IV, `9fe96dea`, OPEN).** Chat lane removes chat-side HOL exposure but does not address default-side HOL — that requires queue-aware scheduling (priority inheritance, concurrency_limit rebalancing) and is a separate wedge.
2. **Queue-keyed admission changes.** Admission stays per-queue (`job_queues.concurrency_limit`, `daemon/repositories/job_queue/models.py:213` [approver N8/S2 — synced from decisions.md D6; was stale `models:338`] + code `:3484-3538`). A dedicated chat queue row would touch router → service → repository → SQL and is not required given claim-side isolation (D6).
3. **Server-stamped lane authority on Task.** Rejected in D1 — correlated EXISTS is sufficient; no schema migration.
4. **New `ENSEMBLE_*` flags** (D7). Violates convention (o) / 7d5285aa. Hardcoded constants only.
5. **Per-lane invoke semaphore** (D8). Unchanged global cap 4 is deadlock-safe given non-inheritance.
6. **Wider forged-source gate (all 18 `RESERVED_SOURCE_PREFIXES`).** Already on backlog TOP. This plan scopes ONLY the chat-lane-needed extension; coordinated PR may include the rest.
7. **`/readyz` pool stats exposure.** Tests-only consumers today; defer until operators request.
8. **`_worker_pool_size` DEAD-attribute cleanup** (`manager.py:6709`, zero readers). Optional follow-up, not required.
9. **`emit_terminal/watcher` id-mismatch defect** (C.3). Pool-agnostic; orthogonal.

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 1 | constants + routing predicate + pool-service support + validator | Establish the routing seam at the claim boundary; ship constants and WorkerPool lane support; close the chat-prefix forgery gap (5-pin pattern + registration-time source_id validator per D10.1). | 8 | independent (Phase 2-3 depend on it) | pending |
| 2 | manager/api wiring + facade forwarding | Construct the second pool in `setup_worker_pool`; populate `_pools` list; replace the canonical 19 wake sites per D5 census (incl. ctor-widening); plumb shutdown teardown with A5.1 hung-worker WARNING; widen API late-wire. | 8 | tight with Phase 1 (shares seam); loose with Phase 3 (tests) | pending |
| 3 | tests incl. integration + saturation-isolation | Unit + facade-guard + integration tests proving strict two-way isolation under default saturation, non-inheritance e2e, mixed-provenance, chat saturation (with A4.1 fixture validation), boot/shutdown, watchdog regression; MANDATORY A2.2 notify-not-poll assertion in the headline saturation test. | 10 (incl. new Task #4a) | tight with Phase 1-2 (consumes seam) | pending |

---

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Phase 1 | — | tight (Phase 2 consumes `claim_pending_task(lane=...)`, `WorkerPool(worker_id_prefix=...)`, constants; Phase 2's manager wiring depends on the seam defined here) | tight (tests assert the seam) |
| Phase 2 | tight | — | tight (integration tests depend on both pools being constructed; shutdown tests depend on teardown) |
| Phase 3 | tight | tight | — |

**Tight contracts** (must agree across phases):
- `TaskRepository.claim_pending_task(worker_id: str, lane: str = "default")` — Phase 1 signature, Phase 2 call sites, Phase 3 asserts.
- `WorkerPool(worker_id_prefix: str = "worker-")` — Phase 1 constructor, Phase 2 instantiation sites, Phase 3 boot-log assertions.
- `manager._chat_worker_pool: WorkerPool | None` — Phase 2 slot, Phase 3 shutdown assertions.
- `manager._pools: list[WorkerPool]` (D5 list-shape, NOT inline 2-tuple) + `manager._notify_all_pools()` — Phase 2 helper iterates the list; canonical 19-site wake census per D5 (incl. CRITICAL `daemon/services/instance_messaging.py:2086-2087` and ctor-widening for `daemon/services/eligible_pending_sweep.py`); Phase 3 wake-pulse tests.
- `is_chat_source(source)` (Phase 1 helper) + 5-pin gate pattern (Phase 1 D10.1) + registration-time validator (Phase 1 Task #5) — together close both HTTP-user and operator-config forgery vectors.

**Cross-phase risks:**
- Phase 1 changes the claim signature — Phase 2 must update all `claim_pending_task` call sites (explorer-verified: 1 direct call in `task_processor.py:1279`; pool itself calls via processor — but explicit grep required in Phase 1).
- Phase 2's notify fan-out depends on `_chat_worker_pool` being initialized before any wake site fires — boot-order invariant must be testable.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | Phase 1's `claim_pending_task(lane=...)` signature change breaks other callers (e.g., stale test fixtures). | High | Low | Phase 1 task #5 includes a `grep -rn claim_pending_task` audit and value-default at all call sites; integration test at `:748` (S6 invariant) re-asserts. |
| 2 | Forged-source gate widening (D10.1) blocks legitimate HTTP callers that today submit `source="telegram:..."` and rely on the 201-accepted path. | Medium | Low | E2e check before widening (call out `.agents/tester/LESSONS/2026-08-30-origin-census-reverse-scan-scheduler-gap.md`); gate raises 422 with the existing `JobValidationError` envelope — operators can grep for rejections. |
| 3 | `_invoke_semaphore` global cap 4 is shared across pools — chat invokes from chat users block on default-lane children, could pile up. | Medium | Low | Phase 3 chat-saturation integration test exercises 2 simultaneous chat invokes; **P3 backlog** (approver S1 — synced from stale "Phase 8" reference; this plan has only Phases 1-3) notes this as watch item. If observed, defer the per-lane split (D8 rejected alternative). |
| 4 | `notify_work` fan-out wakes both pools every time — overhead increase (each pool's condition.notify wakes 1 waiter; idle pools get spurious wakes). | Low | Medium | Phase 3 measures wakeup_efficiency before/after on `get_stats` (`:1425-1455`); if efficiency drops >5%, narrow to per-pool fan-out only when relevant rows exist (P3 follow-up). |
| 5 | Chat pool workers pick up rows whose `message_queue.source` is `NULL` (legacy rows, deleted-source rows). | Medium | Low | Phase 1's prefix predicate uses `source LIKE 'telegram:%' OR source LIKE 'slack:%' OR source LIKE 'discord:%'` (rendered SQL; see D1) on non-null source; NULL matches no prefix → falls to default lane (correct: legacy/cleared sources should NOT get the chat lane). |
| 6 | Two pools share `manager.engine` — DB connection pressure rises (5→7 workers). | Low | Low | Phase 1 acceptance confirms `5+2=7 ≤ 15` pool budget (D9); 7×ms-scale ≪ 15. |
| 7 | `WorkerPool` non-restartable (`:1359-1360`) — second pool adds a second non-restartable state. Test teardown must not call `start()` twice. | Medium | Medium | Phase 3 boot/shutdown lifecycle tests assert single-start contract on both pools; `shutdown_worker_pool` test invokes stop twice (idempotent on already-stopped). |
| 8 | Cross-pool test isolation — tests running both pools can race wake pulses between them. | Low | Medium | Phase 3 integration harness per C.5 (`test_wc_wake_pure_hang.py:402-493`); construct default pool at `WORKER_POOL_SIZE=5` (chat pool at `CHAT_WORKER_POOL_SIZE=2`) with explicit `notify_work` assertions — see Implementer Note (e) about the num_workers confusion. |
| 9 | Server-stamped lane authority not used (D1 rejected); a hostile `SourceCreate` adapter could mint `telegram:` from an internal channel. | High | Very Low | Source adapters are configured by operators (`SourceCreate`); not user-supplied; minting site is `registry.py:857` which is admin-controlled. D10.1 closes the HTTP-API forge; internal forgery requires admin-level access — not in scope. |
| 10 🟡 | **Architect risk #1 — silent 3s-poll degradation on primary chat ingress** (if D5 wake-site list under-enumerates or Phase 2 misses a site). | High | Medium if D5 unamended; Low if D5 amendment lands | D5 canonical 19-site census (incl. CRITICAL `daemon/services/instance_messaging.py:2086-2087` and ctor-widening for `daemon/services/eligible_pending_sweep.py`) is the full enumeration; Phase 3 Task #1's A2.2 notify-not-poll assertion proves the fan-out works end-to-end. Without the assertion, SC#3 cannot detect the regression. |
| 11 🟡 | **Architect risk #2 — operator source_id misconfiguration → silent misroute with zero detection signal.** A Telegram adapter with `source_id="tg-prod"` mints `tg-prod:user` → real chat traffic silently rides default lane with no log, no error; the HTTP gate (D10.1 part 1) does NOT mitigate this operator vector. | High | Low after validator lands; otherwise Medium (depends on operator hygiene) | D10.1 part 2 — registration-time validator at `create_source` (`daemon/routers/sources.py:98+`) requires `source_id.lower() == source_type` for chat-type adapters. Pin: 3 test cases in `tests/unit/routers/test_sources.py`. Validator is mandatory in Phase 1, same PR. |
| 12 🟢 | **Architect risk #3 — full-engine connection budget under sustained API load.** 7 workers + concurrent HTTP handlers + 5 sweep/watchdog services can reach the 15-conn cap under sustained load → 30s claim `TimeoutError`s absorbed by TaskProcessor retry (degraded latency, no corruption). | Low (degraded-not-broken) | Low | D9 reframe documents the full-engine picture. No code change. If observed, operator increases `pool_size` / `max_overflow` via a follow-up PR (D7 says no env flag). |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|---|---|---|
| 1 | Chat messages on `telegram:`, `slack:`, `discord:` are claimed by chat-pool workers. | Integration test: enqueue a chat-prefixed `message_queue` row; assert `task.worker_id` starts with `chat-worker-`. | 100% of chat-prefixed rows under chat-pool workload. |
| 2 | Default messages (`agent:*`, `internal_agent:*`, `scheduler`, `api`, `system:*`) are claimed by default-pool workers. | Integration test: enqueue a default-prefixed row; assert `task.worker_id` starts with `worker-` (not `chat-worker-`). | 100% of non-chat-prefixed rows. |
| 3 | Default pool fully saturated → chat message picked up within ≤3s **AND** no `wait_for_work(3.0s)` timeout-expiry wake observed during the chat-claim window (architect A2.2 notify-not-poll assertion — proves D5 fan-out, not the poll fallback, delivered the claim). | Integration: fill default pool with 5 long jobs; enqueue 1 chat message; assert claim time ≤ 3.5s (poll interval + slack); hook `WorkerPool.get_stats["workers_woken_by_timeout"]` (existing metric, no new code) and assert it does NOT increment during the chat-claim window. | ≤3.5s claim latency under default saturation AND zero timeout-expiry wakes during the chat-claim window. |
| 4 | Non-inheritance (USER CLARIFICATION): slack message → ari executes on chat lane → ari spawns child via `job_create` → child executes on default lane. | Integration e2e: registry-mint slack row → process on chat-worker → internal job_create → child claim by default worker. | Two distinct worker_id prefixes across the lineage. |
| 5 | Mixed-provenance same-instance: an instance processes chat-prefixed then default-prefixed messages without corruption. | Integration: enqueue 1 chat + 1 default row for same `instance_id`; process both; assert no race, no checkpoint corruption. | Both tasks complete; per-instance RUNNING guard serializes. |
| 6 | Chat-lane saturation: subsequent chat messages queue cleanly under burst > pool_size. **Reviewer F10 — SINGLE interpretation, measured FROM ENQUEUE:** the 3rd chat message claims ≤3s from enqueue (the 4th ≤6s from enqueue). **CONDITIONAL on fixture-validation (architect A4.1, reviewer F10, N3)** — fixtures must pin/verify the first two chat tasks are SHORT; if fixtures cannot be made short, SC#6 escalates back to plan revision (the "OR reframe to from-worker-return" branch is STRUCK per reviewer F10). | Integration: enqueue 4 chat messages on 2-worker chat pool; assert 2 run concurrently, the 3rd waits ≤3s **measured from enqueue** (not from worker-return); fixture-validation task #4a must pass before SC#6 is asserted as written. | Bounded queueing, no deadlock. **SC#6 is conditional on fixtures — see Phase 3 Task #4a; reviewer F10 removes the in-test reframe branch.** |
| 7 | `USE_WORKER_POOL=false` disables both pools. | Env-var test: set `USE_WORKER_POOL=false`; boot; assert both `manager._worker_pool` and `manager._chat_worker_pool` are `None`; boot log line shows "Worker pool disabled". | Both pools None; boot succeeds. |
| 8 | Boot line `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"` appears on every successful boot. | Log-capture test: parse ensemble.log for the line; assert presence within first 60s of startup. | 100% boot success. |
| 9 | Shutdown: `shutdown_worker_pool` stops both pools cleanly; `_chat_worker_pool` is `None` after shutdown. | Shutdown test: boot 2 pools; invoke shutdown; assert both pools stopped, log line "Chat worker pool stopped" present. | Clean teardown, no zombie threads. |
| 10 | Forged-source gate: `POST /api/jobs` with `source="telegram:fake"` returns 422 with `JobValidationError`. | HTTP integration: post a job with forged source; assert 422 + envelope shape. | 100% forged-source rejection. |
| 11 | `WORKER_POOL_SIZE=5` and `CHAT_WORKER_POOL_SIZE=2` constant pins survive (`test_constants.py` value + completeness). | `uv run python -m pytest tests/unit/test_constants.py -v`. | All tests pass. |
| 12 | Watchdog-regression pins: existing recovery services (`JobRecoveryService`, `JobLockSweep`, `OrphanWatcherSweep`, waiting-children watchdog) remain unaffected. | Run existing watchdog regression suites (`tests/integration/test_boot_report_recovery.py`, etc.); assert all pass. | Zero new failures. |
| 13 | Engine budget: sum of worker pools ≤ pool_size + max_overflow - safety margin. | Static check: `WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE ≤ factory.pool_size + factory.max_overflow - 2` = `5+2 ≤ 15-2 = 13`. | `7 ≤ 13`. |
| 14 | Strict two-way isolation: default workers NEVER claim chat rows; chat workers NEVER claim default rows (even when one pool is empty). | Integration: enqueue 10 chat rows + 10 default rows; run with default pool sized to 5 and chat pool sized to 2; assert NO row processed on wrong pool. | Zero misrouting. |

---

## Research Insights

(For the full citation load, see `research-findings.md`.)

Key findings that shaped the plan:

1. **Claim is the seam** (`A.2`): `claim_pending_task(worker_id)` is a global singleton with no lane filter today. In-file precedents (the tiered `ORDER BY CASE` literal at `:1872-1875` and the gate `EXISTS/NOT-IN` predicates at `:1686-1691`) prove the seam is designed for additional filters. **Adding a `lane` parameter is the surgical, migration-free fix.**

2. **Mint-site census is closed** (`B.2`): only `registry.py:857` mints chat prefixes; every internal caller stamps `agent:` / `internal_agent:` / `scheduler` / `api` / `system:*`. **Default-lane classification MUST be "NOT chat-prefix" (prefix semantics)** — scheduler's dual shapes (`"scheduler"` and `"scheduler:{id}"`) both stay default.

3. **Non-inheritance is the linchpin** (`D3`): it makes strict two-way isolation deadlock-safe under unchanged `_invoke_semaphore` (`D8`). Children land on the default lane → blocked invoke-parents' children are claimable by the free default worker → no deadlock.

4. **Forgery gap is orthogonal** (`B.3`): `RESERVED_SOURCE_PREFIXES` deliberately excludes chat prefixes. The HTTP gate must widen for the chat lane to be non-forgeable. This is **already on the 2026-09-18 BACKLOG LEDGER TOP item** — the plan scopes the minimum needed.

5. **All recovery services are pool-agnostic** (`C.1`): only the `notify_work` wake seam needs fan-out. **Architect amendment A2.1 expanded the seam to 19 sites** (the original plan missed 9, including the critical `daemon/services/instance_messaging.py:2086-2087` path every registry-minted chat row traverses). `_notify_all_pools` iterates a `self._pools: list[WorkerPool]` (NOT inline 2-tuple) for clean future-lane extension. The `daemon/services/eligible_pending_sweep.py` site requires **ctor-widening** (singleton-attribute reach is forbidden). The mandatory A2.2 notify-not-poll assertion (Phase 3 Task #1) proves the fan-out works end-to-end.

6. **Engine budget is full-engine, not workers-only** (D9 reframe): `5 + 2 = 7` worker connections is comfortable against the 15-conn pool (`factory.py:245-260`), BUT the same engine also serves concurrent HTTP handlers + 5 sweep/watchdog services. Realistic worst ≈ 12-15 at cap under sustained API load. Failure mode = 30s SQLAlchemy pool `TimeoutError`, absorbed by existing TaskProcessor retry (degraded latency, no corruption). The LangGraph checkpointer uses its own asyncpg pool (`checkpoint_adapter.py:433`); PlaneSync is HTTP-only.

7. **Conventional patterns exist** (`C.4`, `C.5`): facade-forwarding guards (`test_manager_enqueue_message_work_id_required.py`) and integration harness (`test_wc_wake_pure_hang.py:402-493`) provide templates for the test surface.

---

## Open Questions

1. **Should the chat-pool boot line include the configured prefixes dynamically (e.g., from `CHAT_SOURCE_PREFIXES` tuple), or hardcode `telegram:,slack:,discord:` in the log string?** Recommended: dynamic via `','.join(CHAT_SOURCE_PREFIXES)` so a future change to the tuple updates the log without a code edit. (Affects Phase 2 task #6.)
2. **Should `_worker_pool_size` (DEAD attribute at `manager.py:6709`) be removed as part of this work?** Recommended: defer — zero readers, low priority, not required for the feature. (Affects Phase 2 optional task.)
3. **Should the chat pool also participate in the `_invoke_semaphore` per-lane split (D8 rejected)?** Recommended: NO — deadlock-safety proven given non-inheritance. Re-evaluate if chat users start invoking chat-pool agents recursively (out of scope today).
4. **Should the forged-source gate widening (D10.1) include ALL `RESERVED_SOURCE_PREFIXES` (backlog TOP), or only the chat-prefix subset?** Recommended: chat-prefix subset only (minimum needed for the chat lane). Coordinate the broader widen as a follow-up PR — keeps this PR scoped and reviewable.