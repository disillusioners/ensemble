# Architecture Recommendation: chat-source-worker-lane

**Date:** 2026-09-19
**Author:** architect (controller) — aggregation of 3 verification workers
**Workers:** `architect-worker-claim-seam` (data-flow-design), `architect-worker-wake-resilience` (resilience-design), `architect-worker-source-gate` (security-design)
**Plan under review:** `.agents/shared/planning/chat-source-worker-lane/` (plan-overview, decisions D1–D12, research-findings, phase1/2/3) @ branch `feature/chat-source-worker-lane` `c8855e8a`
**Mode:** Standard Design (1-of-4 council criteria: cross-system met; irreversible ✗, contested-approaches ✗, blast-radius ✗ — strict two-way isolation bounds chat-lane defects to the chat lane; rollback = revert + rebuild)

---

## Verdict Summary

| # | Focus area | Verdict | One line |
|---|------------|---------|----------|
| 1 | Claim-seam routing (D1) | **ENDORSE + AMEND** | EXISTS approach validated (PK-driven, ms-scale, no migration); fix D1's PG-only `LIKE ANY` text; document pre-existing claim-skew; **reject** the proposed source-index migration |
| 2 | Wake/notify fan-out (D5) | **AMEND (critical)** | Plan's wake-site list misses ≥9 sites — including `instance_messaging.py:2086-2087`, the notify path **every chat row traverses**. As-written, chat lane silently degrades to 3s poll |
| 3 | Cross-lane invoke (D8) | **ENDORSE** | Cap-4 safety independently verified — degrade-only, no cycle; bounded ~5min by `timeout=300` + StaleTaskRecovery. Split rejection sound |
| 4 | Isolation semantics (D2/D10.2) | **ENDORSE + AMEND** | Poll granularity applies to idle-transition only; burst > pool_size waits one task duration (not one poll cycle) — document; no new fast-path needed (notify path covers it once D5 amended) |
| 5 | Lifecycle & boot ordering (D10.3/4) | **ENDORSE + AMEND (minor)** | 60s adapter autostart delay makes boot window benign; add hung-worker WARNING on stop-timeout |
| 6 | Evolution pressure (lane abstraction) | **ENDORSE** | String-keyed lane + prefix kwarg accept a 3rd lane with one slot + one list entry. Optional: iterate a pool list in `_notify_all_pools` from day one |
| 7 | Forged-source gate (D10.1) | **AMEND** | POST /api/jobs confirmed the ONLY user-forgeable surface; separate constant is right class-design but needs the 4-part pin pattern; **operator source_id misconfig vector unmitigated → registration-time validator required** |

**Bottom line:** the plan is architecturally sound and implementable; D8's deadlock reasoning, D2's strict isolation, D3's non-inheritance, D6/D7/D9 resource model, and D1's no-migration approach all survive independent verification. It must NOT ship as-written: D5's wake-site list and D10.1's gate-only mitigation each leave the chat lane's headline guarantee silently absent.

---

## 1. Claim-seam routing (D1 + phase1 tasks 5/6) — ENDORSE + AMEND

**Verified (evidence):**
- Actual claim shape: single `UPDATE … WHERE id = (SELECT id FROM task WHERE <gates> ORDER BY … LIMIT 1)` — a **scalar subquery**, not `WHERE id IN (…)` (`daemon/repositories/task/repository.py:1586-1878`).
- **No `FOR UPDATE SKIP LOCKED`** in the inner SELECT — two concurrent claimers can project the same candidate id; the outer UPDATE's row lock arbitrates; the loser gets 0 rows and returns `None` even when other eligible rows exist. **This property pre-exists** the lane change (single-pool code has it since the 2026-07 queue-awareness fix). The lane predicate preserves it; add-on candidates filtered before LIMIT 1 widen the skew window marginally. Document as a pre-existing concurrency invariant — do NOT attempt to fix in this PR.
- `message_queue.message_id` is **PRIMARY KEY** (`daemon/repositories/message_queue/models.py:60`); `task.message_id` is indexed (`task/models.py:179` `Field(index=True)`).
- Cost bound: the correlated EXISTS is **one PK probe per candidate row**; `source` is a residual filter on the single fetched row. Worst case (1000-deep default backlog, chat-lane claimer): ~1000 PK probes per claim ≈ ms-scale in PG; at ~2.3 claims/s idle-poll load this is noise. The existing queue-awareness EXISTS (`:1686-1691`, correlates `job_queue_items`) is the same cost-class precedent.
- Alternative (ii) JSONB lane-stamp correctly rejected: would require stamping at **~15 internal enqueue sites** (B.2 census: `job_queue.py:712-714/:1326`, `instance.py` ×7, scheduler dual shapes, API, `system:*`, watchdog re-enqueues) vs ONE source-column read at claim time. Post-claim requeue also correctly rejected (claim/release churn, double-claim exposure).

**REJECTED in aggregation — Worker A's `idx_message_queue_source` index migration (A rated HIGH):** an index on `message_queue.source` cannot accelerate this query — the subquery is driven by the `message_id` PK probe; `source` is filtered on the one row already fetched by that probe. The index would be dead weight AND violate the no-migration constraint. Superseded by the cost-bound documentation above.

**Amendments:**
- **A1.1 (fix plan text):** `decisions.md` D1 line 16 writes `source LIKE ANY(ARRAY['telegram:%', …])` — **PG-only, breaks the SQLite test fixtures** (phase1 exit criterion #2; file-backed SQLite harness per C.4/C.5). It also contradicts `phase1-plan.md` task 6, which already specifies the portable SQLAlchemy form. Rewrite D1 to the portable shape: `EXISTS (SELECT 1 FROM message_queue WHERE message_id = task.message_id AND (source LIKE 'telegram:%' OR source LIKE 'slack:%' OR source LIKE 'discord:%'))` — i.e., `sqlalchemy.or_([MessageQueue.source.like(p) for p in CHAT_SOURCE_PREFIXES])` inside `exists()`. Production claim is PG-only (`factory.py:245-260`); tests are SQLite — the portable form satisfies both.
- **A1.2 (document invariant):** add a "Concurrency invariants" note: claim-skew under concurrent claimers (no SKIP LOCKED) is pre-existing and unchanged; two pools add a second concurrent claimer class but no new failure mode.
- **A1.3 (strengthen D1 rationale):** cite the ~15 mint-site stamping cost as the quantitative reason JSONB lane-stamping was rejected.

**D1 alternatives, five axes** (aggregated):

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|-----------|-------------|-----------------|------|------|----------------|
| (a) Correlated EXISTS on source prefix (plan D1) | Low | Good (PK-probe bound, ms-scale) | High (1 read site, precedents in-file) | Low (pre-existing skew only) | None | **Winner** |
| (b) Task-row lane column at enqueue | Medium (migration + 2 write paths) | Good | Medium (stamp-drift at re-enqueue sites) | Medium (derived-cache vs authority divergence) | Migration | Rejected — correct |
| (iii) JSONB lane stamp in message_metadata | Medium-high (~15 stamp sites) | Good | Low (every new enqueue site is a drift hazard) | Medium (missed stamp = silent misroute) | None | Rejected — correct, cite count |
| Post-claim requeue | Low | Poor (churn under mismatch) | Medium | High (double-claim exposure) | Runtime churn | Rejected — correct |

---

## 2. Wake/notify fan-out (D5) — AMEND (CRITICAL — highest-severity finding)

**The plan's wake-site list under-enumerates by ≥9 sites.** Full census (read-only grep `notify_work(` across `daemon/`), diffed against the plan:

| Site | In plan? | Note |
|---|---|---|
| `manager.py:6444` (`on_pending_task` lambda) | ✅ | enqueue pulse |
| `manager.py:715` (discard_on_startup lambda, same shape) | ❌ **missed** | |
| `manager.py:7929/:7993/:8060/:8583/:8647/:8704` | ✅ | child-report carrier |
| `child_reports.py:4198-4204` | ✅ | |
| `job_recovery_service.py:3609-3611` | ✅ (via C.1) | |
| `instance_lifecycle.py:3750-3756` | ✅ (via C.1) | |
| **`instance_messaging.py:2086-2087`** | ❌ **missed — CRITICAL** | **the notify path EVERY registry-minted chat row traverses** (`registry.py:857` → `enqueue_message_job` → `:2087`) |
| `eligible_pending_sweep.py:287-294` | ❌ **missed** | holds direct `self._worker_pool` ref (ctor-widening needed) |
| `waiting_children_watchdog.py:1595-1597` | ❌ **missed** | |
| `job_feedback_observer.py:3400-3405` | ❌ **missed** | |
| `job_feedback_observer.py:3680-3685` | ❌ **missed** | |
| `job_feedback_observer.py:4279-4283` | ❌ **missed** | |
| `job_processor.py:1262-1266` | ❌ **missed** | |
| `long_tool_nudge.py:1184-1187` | ❌ **missed** | defense-in-depth notify |

**Impact if unamended:** every chat message wakes only the default pool; the chat lane rides the 3.0s poll (`worker_pool.py:356`). The testable sentence still passes by accident (3s poll < 3.5s assert) — the plan's success criterion #3 **cannot distinguish notify-path from poll-path pickup**. Given this project's wake/notify defect history (emit_terminal wrong-id → 15-min latency under saturation, `7807e521`; stranded READY notes without Task/notify, `421c6a3d`), this is exactly the class that ships silently.

**Race analysis (all clear — evidence):**
- **Boot window:** source adapters autostart with `AUTOSTART_DELAY_SECONDS = 60.0` (`daemon/sources/registry.py:58/:224-225`); `setup_worker_pool` runs at `api.py:369`, ~750 lines before `manager.start_sources` (`api.py:1119`). Benign — both pools exist before any chat row can arrive.
- **Check-then-wait window:** NONE — proper Condition discipline. `wait_for_work` holds `self._condition` across the whole loop including `wait(timeout=remaining)` (`worker_pool.py:1321-1351`, `:1335/:1345`); `notify_work` acquires the same condition before `notify()` (`:1300-1306`); `_notification_count` (`:1337/:1346-1347`) carries wakes across iterations. No lost wakeup.
- **Claim-in-flight:** notify during a synchronous claim increments the count; the worker sees count>0 on return to `wait_for_work` and re-claims. Safe.
- **Thundering herd:** not material — per-pool `Condition`, one waiter woken per `notify_work()`, sub-µs ops, 2-3 notifies per typical lifecycle. Doubling notifies across two pools is noise.

**Per-pool Condition vs shared CV vs notify_all (resolved):** keep **per-pool Condition with `notify()`**. Worker A suggested `notify_all` per pool for bursts; Worker B argued it forces wasted claim round-trips. Resolution: unnecessary — the `_notification_count` accumulator means N rapid enqueues → N notifies → both idle chat workers wake on the first two, and the (N−pool_size)th message is picked up on worker-return-and-continue (see §4). Changing wake semantics is risk without benefit; the existing discipline is verified sound.

**Amendments:**
- **A2.1 (mandatory):** D5 / phase2 task 3 — extend the replacement list to ALL sites above. For `eligible_pending_sweep.py` (constructor holds `worker_pool` directly): widen the ctor to take the manager (or both pools) rather than reaching for a singleton attribute; for `job_feedback_observer` / `job_processor` / `long_tool_nudge`: they already hold `self._instance_manager` — route through `manager._notify_all_pools()`.
- **A2.2 (mandatory test):** phase3 task 1 — add assertion "no `wait_for_work(3.0s)` timeout-expiry wake observed during the chat-claim window" (i.e., prove the notify path, not the poll path, delivered the claim). Without this the headline guarantee is untestable.
- **A2.3 (shape):** implement `_notify_all_pools` as iteration over a list (`self._pools`) rather than an inline 2-tuple — same cost, one-entry extension for a future lane.

---

## 3. Cross-lane invoke_agent_and_wait (D8) — ENDORSE

**Independently verified:**
- `_get_invoke_semaphore` (`daemon/utils.py:557-569`) is a lazy loop-local `asyncio.Semaphore(4)`; both pools run graphs on the **same event loop** (`manager.py:6435-6436` `MainLoopBridge.set_loop`) → the cap is genuinely global across pools.
- A worker blocked in `invoke_agent_and_wait` necessarily **holds** its slot (acquire at `:647` precedes the child enqueue; release follows completion) **and** its worker thread.
- **Deadlock ladder closes — no cycle:** at most 4 holders across BOTH pools → at most 4 blocked parents → the 5th default worker can never be an invoke-parent → children (all default-lane per D3 mint census, verified: `job_queue.py:711-715` stamps `agent:{caller}` server-side, source param deprecated+ignored at `:680` — the ignore claim verified in code) are always drainable by ≥1 default worker. Verdict: **degrade, not deadlock**.
- **Worst-case latency chain:** 4 default parents' children serialized through the 5th default worker + FIFO semaphore queue for a chat parent — multi-minute tail under 4× simultaneous chat invokes; each recursion level consumes a slot, self-bounding at cap depth 4.
- **No-timeout await at `:647` is double-bounded:** (a) `invoke_agent_and_wait(timeout=300.0)` inner bound (`utils.py:596`); (b) `StaleTaskRecovery` (`manager.py:6482-6508`, heartbeat threshold ~5min) + `JobRecoveryService` drift reconcile (300s). A hung child resolves at ~5min scale, not never. **Cite both bounds in the plan's D8 risk row** — the plan currently calls the await unbounded; it isn't.
- **Per-lane split (default 4 / chat 1):** deadlock-safe in principle (research A.6(iii) is correct) but requires a lane-aware selector at every `invoke_agent_and_wait` call site for zero safety gain under current traffic. **Rejection sound.** Re-evaluate trigger: chat agents recursively invoking chat-lane agents.

---

## 4. Isolation semantics (D2 / D10.2) — ENDORSE + AMEND

**Verified:** workers re-claim **immediately** after finishing a task (`continue` at `worker_pool.py:343`); the 3s wait applies only to the empty-claim idle transition. `notify_work()` wakes ONE waiter (`:1300-1306`); `_notification_count` decrements one per wake (`:1347`); `on_pending_task` fires once per enqueue (`manager.py:6444`).

**Consequence the plan underspecifies:** under a burst of N > pool_size chat arrivals before any worker frees, messages 1..2 wake both idle workers; message 3+ waits for **the next worker return-and-continue** — i.e., **one task duration (~30-300s), not one poll cycle**. This is saturation semantics (identical to the default pool today) and matches D2's accepted envelope, but D10.2's "~3s worst case" conflates the idle-pickup case with the saturation case.

**Amendments:**
- **A4.1:** D10.2 — split the two cases explicitly: (i) idle-chat-worker pickup: notify-path, sub-second once A2.1 lands; (ii) chat-lane saturation with backlog > 2: pickup = next worker-return, latency ceiling = one task duration. The plan's success criterion #6 ("3rd waits ≤3s" for queued chat rows) is only valid if the first two tasks are SHORT — pin the test fixtures accordingly or the criterion is unmeetable as written.
- **A4.2:** No new notify-on-enqueue fast-path is needed — the fast path already exists (`on_pending_task` → `notify_work`); it merely must fan out to the chat pool (A2.1). State this in D10.2 to preempt a redundant mechanism.

---

## 5. Lifecycle & boot ordering (D10.3 / D10.4) — ENDORSE + AMEND (minor)

**Verified:**
- Boot window benign: 60s adapter autostart delay vs pools constructed at `api.py:369` (see §2).
- Late-wire: `api.py:447-449` runs after `setup_worker_pool` → both pools exist before wiring. A claimed-but-unwired task would see `worker._work_resolver = None` — benign no-op in terminal handlers (existing precedent; `worker_pool.py:1457-1479` docstring documents the single-writer guarantee inherited by the second pool).
- Teardown: `shutdown_worker_pool` wrapped in `asyncio.to_thread` (`manager.py:11314`); two sequential `stop(timeout=30)` → worst 60s, accommodated by the outer sequence (pre-cancelled requests `:11312` + inflight grace `:11313` → total ~120s worst). No added timeout needed.
- Mid-invoke stop: a chat worker blocked in `invoke_and_wait` is not interrupted by `_stop_event` (fires only inside `wait_for_work`, `:1339-1340`); the daemon thread dies with the process (`:246`) — no deadlock, but a possible 30s hung join.
- `USE_WORKER_POOL=false` guard placement (`manager.py:6424-6427`) verified coherent — kills both pools.

**Amendment:**
- **A5.1:** phase2 task 5 — add a WARNING log when a chat worker is still alive after `stop(30)` elapses (mirror the StreamWatchdog pattern at `api.py:1601-1605`). Cheap observability for the hung-shutdown case above.

---

## 6. Evolution pressure — lane abstraction (focus 6) — ENDORSE

- `lane: str = "default"` (string-keyed, open vocabulary) + `worker_id_prefix: str` (same) accept a third lane (priority, per-tenant) with ONE manager slot + ONE list entry in `_notify_all_pools`. No 2-pool assumption is load-bearing in the seam design.
- `TaskProcessor` shared-singleton composition **confirmed**: `claim_task` is a thin passthrough (`task_processor.py:1268-1279`); `Worker.run` (`worker_pool.py:289`) is the ONLY caller in `daemon/` — lane flows Worker → processor → repository as an argument, never as processor state. No singleton/lane conflict.
- Only pragmatic nit: A2.3 (list-based `_notify_all_pools`). Do NOT build a pool registry/config surface now — nothing in the plan closes that door; speculative generality would violate the project's own conventions.

---

## 7. Forged-source gate extension (D10.1 + phase1 task 4) — AMEND

**Census verdict: POST /api/jobs is the ONLY user-supplied-source ingest surface** (endorsed): messages routes hardcode `"api"` / `"api_resume_fallback"` (`routers/messages.py:557/:345`); no PUT/PATCH source path; SSE is read-only; `/api/sources` manages adapters (no ingest); in-process dispatch (`sources/registry.py:857`) is the legit mint; scheduler stamps exact `"scheduler"` (`scheduler.py:765`); agent tools stamp server-side (`job_queue.py:711-715`, param ignored — verified).

**Amendments:**
- **A7.1 (pin pattern — mandatory):** the coupled-reservation 4-part contract applies structurally even though chat prefixes are a NEW class (user-forgeable external namespaces vs server-only reserved). Phase1 task 4 must enumerate, ALL in the SAME commit: (1) provenance doc-bullet at `CHAT_SOURCE_PREFIXES` in `daemon/constants.py` (mirror `:455-480` style, naming `sources/registry.py:857` as the legit mint); (2) exact-equality + helper-parity pins in `tests/unit/routers/test_source_reservation.py` (new `TestChatSourcePrefixesConstant` mirroring `:87-124`; extend helper tests at `:126-189` — None/empty → False, `telegram:user:1` → True, `webhook:gh` → False, case-sensitive); (3) gate-behavior pin — parametrized `test_create_job_rejects_chat_prefix` in `TestCreateJobSourceBoundary` (`:245+`, mirror `:252-299`); (4) e2e matrix — 3 `gate_422` cases in `test/packs/origin_contract_e2e_probe_test.py` PART 1 (`:151+`; NO census change — chat prefixes are not reserved members; say so in the case comment); fix the stale gate reference at `:33` (`:299-316` → actual `:478-495`); (5) constants completeness pin (already phase1 task 3).
- **A7.2 (registration-time validator — mandatory):** the gate closes the HTTP-user vector but NOT the operator vector. `SourceCreate.source_id` is free-form (`daemon/models/source.py:31-37`, `^[a-zA-Z0-9_-]+$`): a Telegram adapter with `source_id="tg-prod"` mints `tg-prod:user` → **not** chat-prefixed → real chat traffic silently rides the default lane (feature inert, no log, no error); `source_type="discord"` with `source_id="telegram"` mints into the chat lane. Detection difficulty is HIGH (zero runtime mismatch signal; already flagged 🟠 in `.agents/tester/LESSONS/2026-08-30-origin-census-reverse-scan-scheduler-gap.md:16`). Minimal fix, no migration: validator at `create_source` (`daemon/routers/sources.py:98+`) — when `source_type ∈ {telegram, slack, discord}`, require `source_id.lower()` to equal the type name (i.e., the minted prefix lands in `CHAT_SOURCE_PREFIXES`); reject with the standard envelope. Add to Phase 1 scope.
- **A7.3 (documented asymmetry):** `_USER_ORIGIN_PREFIXES` (`daemon/tools/upgrade_journal.py:1077-1079`) lists FIVE prefixes (adds `webhook:`, `whatsapp:`); `CHAT_SOURCE_PREFIXES` lists THREE. The exclusion is a deliberate scope decision (webhook ≈ CI/automation, not interactive chat) but MUST be documented at the constant AND pinned (`webhook:gh-hook` → `is_chat_source() == False`) so the gate/lane symmetry is an invariant, not an accident. (Correction carried from worker C: `USER_ORIGIN_SOURCES` lives in `daemon/tools/upgrade_journal.py`, not `daemon/services/`.)
- **A7.4 (breaking change):** clean — exhaustive grep found ZERO tests POSTing chat-prefixed sources expecting 201 (existing `telegram:` strings in tests are in-process internal values, unaffected). Same-commit updates = the pins in A7.1 only.
- **Orthogonality confirmed:** the deferred msg-type seam (`instance_messaging.py:1613-1629`) classifies chat prefixes as `MessageType.HUMAN` — same as `"api"`; classification does not interact with lane routing. No amendment.

---

## Hard-constraint validation

| Constraint | Verdict | Note |
|---|---|---|
| No schema migration | ✅ **VALIDATED** — and actively defended: Worker A's proposed `idx_message_queue_source` migration is REJECTED in aggregation (cannot accelerate a PK-driven correlated EXISTS — `source` is a residual filter on the row the PK probe already fetched; dead weight + constraint violation). True cost bound: PK probes per candidate, ms-scale (§1). |
| Hardcoded constants, no new ENSEMBLE_* flags (D7) | ✅ VALIDATED | `USE_WORKER_POOL` kill-switch placement coherent (`manager.py:6424-6427`); `worker_poll_interval` confirmed dead code — do not rely on it. |
| 7 workers ≤ 15 connections (D9) | ✅ for workers, **AMEND framing** | LangGraph checkpointer uses its OWN asyncpg pool (`checkpoint_adapter.py:433` — confirmed not shared); PlaneSync is HTTP-only. BUT the 15-conn engine also serves concurrent HTTP handlers + 5 sweep/watchdog services: realistic worst ≈ 12-15 **at cap** under sustained API load; failure mode is 30s pool `TimeoutError` absorbed by existing TaskProcessor retry (ms-scale holds, `with engine.begin()` release discipline). Amend D9's "comfortable margin" wording to name concurrent HTTP load; no code change. |
| Facade-forwarding discipline | ✅ VALIDATED | No new kwargs cross the InstanceManager→InstanceMessagingService facade (lane derives at claim time; enqueue paths untouched — `enqueue_message`/`enqueue_message_job` signatures unchanged). Phase2 task 7's conditional is correctly scoped; keep the phase3 #9 watchdog regression pin as belt-and-suspenders. |

---

## Top 3 architectural risks (ranked)

1. 🟡 **D5 wake-site under-enumeration → silent 3s-poll degradation on the primary chat ingress path.** The plan's list misses ≥9 sites, most critically `instance_messaging.py:2086-2087` (the notify path every chat row traverses) and `eligible_pending_sweep.py` (direct pool ref). Unamended, the feature's headline guarantee exists only via the poll fallback, and success criterion #3 cannot detect the difference. Fix: A2.1 site list + A2.2 notify-not-poll test assertion. Blast radius if shipped as-written: chat latency silently unbounded under exactly the default-saturation scenario the feature exists for — the documented `7807e521` defect class.
2. 🟡 **Operator source_id misconfiguration → silent misroute with zero detection signal.** `SourceCreate.source_id` free-form vs hard-coded prefix set: `tg-prod`-style ids make the chat lane silently inert for real chat traffic; cross-type ids mint into the wrong lane. Not mitigated by D10.1 (HTTP gate) at all. Fix: A7.2 registration-time validator (Phase 1, same PR).
3. 🟢 **D9 connection budget framed per-workers-only.** 7 workers + concurrent HTTP handlers + 5 sweeps can reach the 15-connection cap under sustained API load → 30s claim `TimeoutError`s (absorbed by retry, degraded latency, no corruption). Fix: A-documented reframe in D9; no code change. (Ranked below 1-2 because the failure mode is degraded-not-broken and self-healing.)

Not ranked but note-worthy: the D1 plan-text bug (`LIKE ANY` PG-only vs phase1's portable form) is a cheap pre-implementation fix (A1.1); the claim-skew (no SKIP LOCKED) property is pre-existing and explicitly out of scope.

---

## Decisions pending (for the leader/user)

1. Accept A7.2 registration-time validator into Phase 1 scope (recommended: yes — it is the only mitigation for risk #2).
2. Accept the amended wake-site list (A2.1) as the canonical D5 contract, including ctor-widening for `eligible_pending_sweep.py`.
3. Confirm success criterion #6's fixture assumption (short first-two tasks) per A4.1.

## Open questions

- None blocking. Re-evaluate D8 (per-lane semaphore split) only if chat agents begin recursively invoking chat-lane agents.

## Gaps

None — all 3 dispatched workers reported with file:line evidence (fan-in 3/3).

**Confidence: HIGH.** Flip assumption: if `message_queue.message_id` were NOT the driving key of the EXISTS (e.g., a future rewrite filtering `message_queue` by `source` directly), the index rejection in the constraints table would need revisit — as specified, the PK-driven shape holds.
