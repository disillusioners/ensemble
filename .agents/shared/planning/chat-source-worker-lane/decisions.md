# Design Decisions: chat-source-worker-lane

**Date:** 2026-09-19
**Author:** planner[v2] via plan-creation worker
**Status:** Draft (pending user approval) — AMENDED 2026-09-19 per architect review

> **Changelog 2026-09-19:** Architect amendments ACCEPTED (leader). **D1** — replace PG-only `LIKE ANY` with portable `sqlalchemy.or_(...)` shape (production PG-only; test fixtures SQLite); REJECTED idx_message_queue_source index migration (PK-probe drives the correlated EXISTS; `source` is a residual filter — dead weight + violates no-migration); added "Concurrency invariants" note (claim-skew without SKIP LOCKED is pre-existing, unchanged by lane predicate, explicitly out of scope); strengthened D1 rationale with the ~15 internal mint-site stamping cost. **D5** — replaced wake-site list with architect's canonical 19-site census (9 added by architect amendment; incl. `daemon/services/instance_messaging.py:2086-2087` — the notify path every chat row traverses, marked CRITICAL); `_notify_all_pools` iterates a `self._pools` LIST (not inline 2-tuple); added ctor-widening requirement for `daemon/services/eligible_pending_sweep.py`. **D10.1** — upgraded to 5-pin coupled-reservation pattern (provenance doc-bullet, exact-equality pins, gate-behavior pin, e2e matrix, completeness pin); ADDED registration-time source_id validator at `create_source` (`daemon/routers/sources.py:98+`) — operator vector unmitigated by HTTP gate alone; PINNED 3-vs-5 `_USER_ORIGIN_PREFIXES` vs `CHAT_SOURCE_PREFIXES` asymmetry (webhook/whatsapp deliberately excluded; pinned by test). **D10.2** — split saturation cases (idle-transition pickup vs burst > pool_size with backlog > 2: latency = next worker-return, NOT one poll cycle); stated no new notify-on-enqueue fast-path needed (notify path already exists, just fan out per D5). **D9** — reframe to full-engine picture (15-conn engine also serves HTTP handlers + 5 sweeps; realistic worst ≈ 12-15 at cap under sustained API load; failure mode = 30s pool TimeoutError absorbed by TaskProcessor retry). **D8** — risk row now cites BOTH bounds (invoke_agent_and_wait `timeout=300` + StaleTaskRecovery heartbeat ~5min + JobRecoveryService 300s drift reconcile); await is NOT unbounded. **Phase 1** — scope + registration-time validator. **Phase 2** — task 3 wake-site list expanded; task 5 + hung-worker WARNING. **Phase 3** — task 1 + notify-not-poll assertion; task 4 SC#6 conditional fixture validation.

> **Changelog 2026-09-19 (reviewer pass):** reviewer pass applied — F1 critical predicate fix (positional splat + `p + "%"` wildcard; realistic fixture pin; stale `LIKE ANY` purged from plan-overview.md Risk #5), F2-F12 same-pass (F2 A7.4 same-commit extends to flip e2e probe case 8b + update pass-through unit test; F3 STRIKE per-pool-TaskProcessor option, mandate Worker-level lane threading, both ctor kwargs in Phase 1 Task #8; F4 normalize 9 census paths with `daemon/services/` prefix; F5 harmonize "19" counting basis everywhere; F6 registration-validator as mandatory exit criterion; F7 file-backed SQLite replaces in-memory; F8 D8 sub-claim corrected — both chat workers CAN simultaneously invoke-block, chat-lane stall bounded by double bound (≤300s); F9 PRAGMA case_sensitive_like=ON in SQLite harness; F10 SC#6 single interpretation ≤3s from enqueue, "OR reframe" branch STRUCK; F11 A2.2 two-read DELTA, FORBID pre-test zeroing; F12 phase2 Task #3 cross-ref declares decisions.md D5 census authoritative), wake-site None-guard mandate (every widened site must None-guard `_chat_worker_pool` — the `_notify_all_pools` helpers per-entry None check satisfies site-wide), Implementer Notes (13-22) appended (phase1: a-d; phase2: e-h; phase3: i-m).

> **Changelog 2026-09-19 (approver pass — iteration 001 rejected → iteration 002 prepared):** approver-pass applied — **B1** conditional fail-open lane strictness (leader option b; default-lane `NOT EXISTS` chat-prefix clause gated on `manager._chat_lane_active: bool`, set True on chat-pool construction, set False on chat-pool teardown; never a one-way boot latch; 3-state table: P1 alone / P1+P2 / P2 shutdown; `USE_WORKER_POOL=false` no-op; atomic-merge still holds in practice; semantics written into phase1 §Coupling + Tasks 6-7, phase2 Tasks 2/5, decisions.md D2 + D10.4), **B2** teardown snapshot ordering (`pools_snapshot = [p for p in (self._worker_pool, self._chat_worker_pool) if p is not None]` taken BEFORE stop/None blocks; iterate snapshot AFTER — otherwise the hung-worker WARNING is dead code for BOTH pools because the slots are already None; B2 written into phase2 Task 5 + decisions.md D10.4 snippet), **N1** path typo `worker_pool.py` → `daemon/services/worker_pool.py` EVERYWHERE (15 replacements across all 5 files; grep-verified zero bare-`worker_pool.py` remaining), **N2** D10.3 constructor example includes `task_processor=self._task_processor` positional, **N3** plan-overview.md SC#6 row REMOVED stale "processed serially" fragment, **N4** D10.4 private `_workers` access moved from Implementer Notes INTO Task 5 text as REQUIRED in-PR choice (no defer), **N5** Phase 3 test-gate paths corrected to real files: `tests/job_queue/test_job_recovery_service.py`, `tests/unit/job_queue/test_joblock_sweep_lifecycle.py`, `tests/unit/services/test_waiting_children_watchdog.py` (grep-verified via `find tests -name`), **N6** `tests/unit/routers/test_sources.py` enumerated as a NEW file in phase1 exit criteria (4 new files total, not 3), **N7** D1 adds raw `text()` SQL rendering guidance (the actual claim seam uses text() not ORM; portable `or_(...)` is the SPEC; inline literals OK because `CHAT_SOURCE_PREFIXES` is compile-time-constant), **N8** D6 cite `models :338` → `daemon/repositories/job_queue/models.py:213`, **N9** phase2 Exit Criterion #1 + "New test files" line use `num_workers=WORKER_POOL_SIZE` (not `1`); Implementer Note (e) simplified to point at fixed gating text, **N10** D10.3 boot-order narrative aligned to `:6683 → :6697 → :6708`; AUTOSTART cite corrected from `:224-225` → `:230`/`:247`.

> **Changelog 2026-09-19 (approver pass — iteration 002 rejected on ONE line → iteration 003 FINAL prepared):** approver-pass applied — **B1** phase1 Exit Criterion #10 allowed-file list now includes `test/packs/origin_contract_e2e_probe_test.py` (the file Task #4 edits for 3 gate_422 e2e cases + stale-ref fix at :33; was missing from the change fence), **S1** plan-overview.md Risk #3 stale "Phase 8" reference → "P3 backlog" (this plan has only Phases 1-3), **S2** plan-overview.md Out-of-Scope item 2 stale `models:338` → `daemon/repositories/job_queue/models.py:213` (synced from decisions.md D6/N8), **S3** phase1-plan.md §Coupling "covered by Task #5" → "covered by Tasks #6-7" (the predicate/strictness tasks, not the teardown task), **S4** B1 fail-open test-location RESOLVED EXPLICITLY (home = Phase 2 wiring test) — `tests/integration/test_chat_source_pool_wiring.py` `test_b1_conditional_fail_open_chat_pool_absent_then_present` (case a chat pool ABSENT → default claims; case b chat pool PRESENT → strict two-way); phase2 Exit #10 names the file + case; phase3 Exit #0 cross-references the phase2 home (no duplicate home), **E1** phase2 Task 5 chat-FIRST iteration is mandatory (either build the snapshot chat-first `[self._chat_worker_pool, self._worker_pool]` or `reversed(pools_snapshot)`) — code-shape explicit; flag flips False BEFORE default pool's `stop()`; default pool continues to claim chat rows during its own stop window, **E2** `_chat_lane_active` transport is a SINGLE shared source of truth owned by `InstanceManager`; `TaskRepository` reads it PER-CLAIM (by reference / threaded parameter) — NEVER a snapshot taken at construction time (no per-instance copy that can drift between the `manager.py:715` discard_on_startup lambda's repo and the `manager.py:6444` on_pending_task lambda's repo); written into decisions.md D2 B1 subsection + phase1 Task 6/7 + phase2 Task 2, **E3** phase2 Task 1 initializes `manager._pools = []` in `InstanceManager.__init__` (boot-window guard — any early wake/wiring before `setup_worker_pool` cannot crash on missing attribute), **E4** phase3 Task #7 boot-line observability uses IN-PROCESS LOG CAPTURE (`caplog`/`capfd` fixture — NOT filesystem `data/logs/ensemble.log`); names the precedent `tests/unit/job_queue/test_joblock_sweep_lifecycle.py`, **E5** naming-drift citations aligned to verified names: `EligiblePendingSweepService` (verified via `grep "^class" daemon/services/eligible_pending_sweep.py`) replaces `EligiblePendingSweep` in research-findings.md × 2 + phase2-plan.md × 2; `shutdown_worker_pool` def at `daemon/manager.py:6712` (verified via `grep "def shutdown_worker_pool" daemon/manager.py`) — body span `:6712-6727` already consistent, **E6** phase1 Implementer Notes new note (n) — validator error-envelope naming follows EXISTING router validation envelope convention in `daemon/routers/jobs_crud.py` (implementer verifies `JobValidationError` / `ErrorResponse` / `ErrorCodes` at implementation time and matches byte-for-byte); also handle `SourceType` enum `.value` comparisons (raw-string-vs-enum silently fails; verify Pydantic coercion).

> Every design question raised in the dispatch (D1–D12) is resolved here with the chosen direction, the rationale, and the rejected alternatives. The non-inheritance decision (D3) carries the verbatim mid-flight user clarification. Each decision is referenced by the phase that implements it.

---

## D1. Routing rule: source-prefix predicate at the claim seam

**Decision: Option (a) — correlated `EXISTS` on `message_queue.source` via `task.message_id` (no schema change).**

- Add a `lane: str = "default"` parameter to `TaskRepository.claim_pending_task` (`daemon/repositories/task/repository.py:1479-1482`) and to `TaskProcessor.claim_task` (`daemon/services/task_processor.py:1268-1279`).
- The predicate is folded into the existing atomic `UPDATE…RETURNING` alongside the other gates. The **portable SQLAlchemy shape** is (reviewer-corrected, F1):
  ```python
  chat_source_predicate = sqlalchemy.or_(
      *(MessageQueue.source.like(p + "%") for p in CHAT_SOURCE_PREFIXES)
  )
  # inside the inner SELECT, correlated on task.message_id == message_queue.message_id:
  exists_clause = sqlalchemy.exists().where(
      sqlalchemy.and_(
          MessageQueue.message_id == Task.message_id,
          chat_source_predicate,
      )
  )
  # For lane="chat"  → row matches if exists_clause is true
  # For lane="default" → row matches if NOT exists_clause
  ```
  - **Positional splat (`*`)**, NOT single-arg-list — `sqlalchemy.or_([list])` raises `ArgumentError` on SQLAlchemy 2.0.48 (this repo's pinned version).
  - **`p + "%"` prefix wildcard** — `CHAT_SOURCE_PREFIXES` already carries the trailing colon (`"telegram:"`, `"slack:"`, `"discord:"`), so `p + "%"` yields the wildcard form (`"telegram:%"`, etc.). Bare `source.like("telegram:")` is exact-match and would silently match nothing in production.
  Rendered SQL: `EXISTS (SELECT 1 FROM message_queue WHERE message_id = task.message_id AND (source LIKE 'telegram:%' OR source LIKE 'slack:%' OR source LIKE 'discord:%'))` — production claim runs on PG (`daemon/repositories/factory.py:245-260`); test fixtures run on file-backed SQLite (Phase 1 exit criterion, F7); the `or_(...)` form satisfies both backends.
- **Approver N7 — the actual claim seam composes RAW text() SQL, not ORM expressions:** the inner SELECT inside the `UPDATE…RETURNING` is built as `sqlalchemy.text(...)` with bindparams (per `daemon/repositories/task/repository.py:1557-1875`'s existing gate cascade). The portable `or_(*(...))` form above is the **portability SPEC** — the **implementation** translates the `or_(...)` shape into the raw text() SQL via the standard SQLAlchemy 2.0 idiom `select(MessageQueue.message_id).where(MessageQueue.message_id == bindparam("task_message_id"), or_(...))` then `compiler.process(...)` → emits `(message_queue.source LIKE 'telegram:%' OR message_queue.source LIKE 'slack:%' OR message_queue.source LIKE 'discord:%')` as a literal subquery. **Inline literals are acceptable** because `CHAT_SOURCE_PREFIXES` is a compile-time constant tuple — the SQL string is deterministically known at module load; **no user input flows through the literal**. Bound parameters (`text(:p1)` + `bindparam`) are the alternative if the implementer prefers defense-in-depth against future tuple-content drift; either is acceptable. **State ONE** in the implementation docstring; the chosen form must be the SAME on PG and SQLite (the rendered SQL above satisfies both).
- Default-lane classification uses `NOT EXISTS` of the same subquery — **prefix semantics** so scheduler's dual shapes (`"scheduler"` and `"scheduler:{id}"`) both stay default.
- **Test fixtures (F1 review):** pin to **realistic production values** — `telegram:alice:1`, `slack:U123:thread`, `discord:guild-42:user-7` — NEVER bare `"telegram:"` (which cannot occur in production rows; the mint site always appends `:external_user_id` at `registry.py:857`).

**Rationale:**
- `message_queue.source` is the canonical, server-stamped source of truth (single mint site at `registry.py:857`; server-derived for internal callers per B.2).
- **No migration** — single column `source` already exists; correlated EXISTS is one PK probe per candidate row (ms-scale at 2-7 workers; cost-class matches the existing queue-awareness EXISTS at `repository.py:1686-1691`).
- Live correlation already exists per `predicates.py:37-43` — adding the prefix predicate is a localized change at the existing gate cascade.
- **Quantitative rejection of the JSONB lane-stamp alternative** (worker A's option (iii)): stamping `lane` into `message_metadata` at enqueue requires touching **~15 internal mint sites** (`job_queue.py:712-714/:1326`, `instance.py:733/:2486/:2683/:3127/:3144/:3239/:3270`, scheduler dual shapes, API route `messages.py:557`, all `system:*` mints, watchdog re-enqueue sites) vs ONE source-column read at claim time. Missed stamp = silent misroute — a worse failure mode than the current absence of a lane column. The D1 predicate is a single-site read; the JSONB option is a multi-site write hazard.

**Concurrency invariants (PRE-EXISTING, unchanged by lane predicate — explicitly out of scope):**
- Claim shape is `UPDATE … WHERE id = (SELECT id FROM task WHERE <gates> ORDER BY … LIMIT 1)` — a scalar subquery, **no `FOR UPDATE SKIP LOCKED`** (`daemon/repositories/task/repository.py:1586-1878`). Two concurrent claimers can project the same candidate id; the outer UPDATE's row lock arbitrates; the loser gets 0 rows and returns `None` even when other eligible rows exist.
- This property pre-exists the lane change (single-pool code has had it since the 2026-07 queue-awareness fix). The lane predicate preserves it; add-on candidates filtered before `LIMIT 1` widen the skew window marginally.
- **Two pools add a second concurrent claimer class but introduce NO new failure mode** — the outer UPDATE row-lock still arbitrates between default and chat workers. Document as a pre-existing concurrency invariant; do NOT attempt to fix in this PR.

**REJECTED in architect aggregation — `idx_message_queue_source` index migration (worker A rated HIGH):**
- An index on `message_queue.source` **cannot accelerate** this query. The correlated subquery is driven by the `message_id` PK probe (`message_queue.message_id` is PK at `daemon/repositories/message_queue/models.py:60`); `source` is a residual filter on the single row the probe already fetched.
- The index would be dead weight AND violates the no-migration constraint. Cost bound: ~PK probes per candidate, ms-scale in PG; same cost-class as the queue-awareness EXISTS at `:1686-1691`. Superseded by the cost-bound documentation above.

**Rejected Option (b) — Task-row lane column stamped at enqueue:**
- Schema migration required (mirroring `is_background` precedent at `instance_messaging.py:1594-1601`).
- Two write paths to stamp correctly (initial dispatch + re-enqueue from watchdogs) → drift risk if stamp site missed.
- Source column would still need to be the authority (the lane column would be a derived cache).
- A derived cache that disagrees with its authority is strictly worse than no cache (two truths to keep in sync).

**Why NOT server-stamped lane column instead of source-prefix predicate:**
- Source column is the durable ground truth; stamping `lane` separately creates two ways to disagree.
- The forgery gate (D10) is the orthogonal defense — closing the FORGE does not change the ROUTING seam.

---

## D2. Isolation semantics

**Decision: STRICT two-way — default pool NEVER claims chat work; chat pool NEVER claims default work.**

- Default workers call `claim_pending_task(worker_id="worker-{i}", lane="default")`.
- Chat workers call `claim_pending_task(worker_id="chat-worker-{i}", lane="chat")`.
- Each pool's claim only returns rows matching its lane predicate.

**B1 conditional fail-open (approver-pass; leader option b):** the default-lane `NOT EXISTS` chat-prefix clause is **gated on a chat-lane-active flag** (`manager._chat_lane_active: bool`, default `False`). When the flag is `False`, the default-lane predicate does NOT add the `NOT EXISTS` clause and claims chat rows exactly like today (fail-open = no telegram:/slack:/discord: row is permanently unclaimable). When the flag is `True`, the predicate excludes chat rows (strict two-way). The flag is set to `True` on chat-pool construction (Phase 2 Task #2) and to `False` on chat-pool teardown (Phase 2 Task #5, snapshot-ordered — chat pool stopped/cleared FIRST so default pool claims chat rows again during its own stop window). The flag is consulted on **every default-lane claim** (or at minimum on BOTH pool construction AND teardown — implementation choice; the implementer must verify the flag is read on construction AND teardown so teardown restores fail-open). NEVER a one-way boot latch. Cross-reference: B1 semantics are also documented in `phase1-plan.md` §Coupling and `phase2-plan.md` Task #2/Task #5.

**E2 — `_chat_lane_active` transport (approver-pass):** the flag is a **SINGLE shared source of truth** owned by `InstanceManager` (`manager._chat_lane_active: bool`). `TaskRepository` reads it **per-claim** — by reference (manager-owned attribute the repository reads at claim time) or as a parameter threaded through the `TaskProcessor`/`Worker` chain. NEVER a snapshot taken at construction time (per-instance copy that can drift between the `manager.py:715` `discard_on_startup` lambda's `TaskRepository` instance and the `manager.py:6444` `on_pending_task` lambda's `TaskRepository` instance — both construction sites must read from the same live value). The implementer must verify BOTH construction sites (`manager.py:715` and `manager.py:6444`) wire the repository to read `manager._chat_lane_active` at claim time, not at construction. Cross-references: phase1 Task #6/Task #7 (predicate gating reads the flag per-claim); phase2 Task #2 (flag lifecycle — True on chat-pool start, False on chat-pool stop); phase2 Exit Criterion #10 (B1 test — flag must flip correctly across the three states).

**Three guarded states (B1):**

| State | Chat pool | Flag | Default-lane claim of chat-prefix rows |
|---|---|---|---|
| Phase 1 alone (P2 not merged) | not constructed | False | claims them (fail-open = today) |
| Phase 1 + Phase 2 (normal) | constructed | True | excludes them (strict two-way per D2) |
| Phase 2 shutdown | stopped, cleared | False | claims them (fail-open restored) |

`USE_WORKER_POOL=false` path: no pools at all → no lane filtering anywhere (claims cannot happen; nothing to strand).

**Rationale:**
- **Real-time goal**: chat lane must never be starved by default work. Strict isolation guarantees bounded latency (only depends on chat-lane saturation, not on default-lane saturation).
- **Deterministic testability**: strict two-way is assertable; "default-overflow" makes saturation-isolation non-deterministic (depends on race timing).
- **Cost**: 2 dedicated workers permanently reserved for chat. Acceptable trade-off — chat is interactive human-facing; default is the majority of background work.
- **Backpressure symmetry**: chat messages queue cleanly in `message_queue` and surface visibly under saturation; default messages do the same. No silent overflow path to debug.
- **Atomic merge holds in practice** (one branch, one merge, one rebuild+restart — P1 and P2 land together), but the conditional makes a P1-only deployment SAFE rather than catastrophic — the fail-open path preserves today's behavior until the chat pool is actually constructed.

**Rejected chat-lane-only + default-overflow:**
- Default-overflow means: when chat pool is empty AND default pool is saturated, chat workers could pick up default work. This breaks the real-time guarantee (latency now depends on default pool drain).
- Tests cannot deterministically assert "chat message picked up under default saturation" because overflow may or may not engage depending on which worker polls first.

**Rejected hard P1↔P2 merge coupling (approver option a):** ship Phase 1 + Phase 2 as one atomic merge with a single rebuild+restart. The conditional fail-open is strictly safer (the atomic merge holds in practice, but the conditional defends the degenerate case).

**Rejected one-way boot latch (approver option c):** set the flag to True unconditionally at process start, never clear it. This strands chat rows on a P1-only deployment and on a post-teardown restart — exactly the defect class B1 closes.

---

## D3. Non-inheritance (USER CLARIFICATION — MANDATORY, VERBATIM)

**Decision (verbatim scenario + rationale):**

> "example slack message creates ari agent (ari will work on new queue for realtime response) but if ari > spawn job > those job still use current queue."

**Lane assignment is PER QUEUED ITEM, keyed on the row's source provenance. NOT inherited from the parent instance.**

- `source ∈ {telegram:, slack:, discord:}` → **chat lane**.
- `internal_agent:*`, `agent:*`, `scheduler`, `api`, `system:*` and every other reserved prefix → **default lane**.

**Rationale:**
- The 2-worker chat lane is reserved for the **interactive human-facing conversation loop**.
- Agent fan-out (job_create, job_continue, internal_agent:*) is **background work** — must never eat or starve the chat lane.
- Mint-site census (B.2): internal callers unconditionally stamp `agent:{caller}` (`job_queue.py:712-714`) or `internal_agent:{caller}` (`:714/:2298/:2352`, `instance.py:733/:2486/:2683/:3127/:3144/:3239/:3270`); scheduler stamps both `"scheduler"` and `"scheduler:{id}"` (B.2); API stamps `"api"`. **None of these are chat prefixes.**
- The chat prefix set is closed: only `registry.py:857` mints it, and only `SourceCreate`-configured adapters can reach that mint site.
- **E2E test scenario (mandatory)**: slack message → `ari` executes on **chat lane** → `ari` spawns a child job via `job_create` → child executes on **default lane**. Assertable via `task.worker_id` lineage (`chat-worker-0` → `worker-1`).

---

## D4. Mixed-provenance instances

**Decision: PER-ROW routing. The same instance may execute on different pools across turns. This is supported by existing semantics.**

- `instance_id` (`:61`) and `source` (`:64`) are **independent columns** on `message_queue` — no one-instance-one-source constraint anywhere in code.
- **Per-instance RUNNING guard** (`repository.py:1692-1756`) prevents concurrent same-instance tasks across pools. Two pools racing for two messages of the same instance will be serialized by the guard.
- **Checkpoint + model is per-instance** and pool-agnostic — pools share `manager.engine` (A.7), share `MainLoopBridge` loop (A.4), and share the langgraph checkpoint store. No cross-pool model migration needed.
- **Instance REUSE** accepts later rows with different sources — the existing `send_message` revival path (COMPLETED/TERMINATED/ERROR/FAILED → RUNNING reusing checkpoint) is unchanged.

**Tolerance: confirmed.** Add a regression test that the same `instance_id` processes a `slack:`-prefixed message on the chat pool and then a subsequent `agent:`-prefixed message on the default pool without corruption.

---

## D5. Watchdog/sweeper wake fan-out

**Decision: Add a `_notify_all_pools()` helper on `InstanceManager` that iterates over a `self._pools: list[WorkerPool]` list (NOT an inline 2-tuple — see list-shape rationale). All canonical wake sites resolve this helper instead of `self._worker_pool`.**

**Canonical wake-site census (architect-verified, 19 total — the architect found 9 sites the plan originally missed; CRITICAL sites are starred):**

| # | Site | File:Line | Mode | Notes |
|---|---|---|---|---|
| 1 | `on_pending_task` lambda | `daemon/manager.py:6444` | list-iteration via helper | enqueue pulse |
| 2 | `discard_on_startup` lambda | `daemon/manager.py:715` | list-iteration via helper | missed in original plan |
| 3 | child-report carrier wake A | `daemon/manager.py:7929` | list-iteration via helper | |
| 4 | child-report carrier wake B | `daemon/manager.py:7993` | list-iteration via helper | |
| 5 | child-report carrier wake C | `daemon/manager.py:8060` | list-iteration via helper | |
| 6 | child-report carrier wake D | `daemon/manager.py:8583` | list-iteration via helper | |
| 7 | child-report carrier wake E | `daemon/manager.py:8647` | list-iteration via helper | |
| 8 | child-report carrier wake F | `daemon/manager.py:8704` | list-iteration via helper | |
| 9 | child_reports wake | `daemon/services/child_reports.py:4198-4204` | list-iteration via helper | |
| 10 | job_recovery_service wake | `daemon/services/job_recovery_service.py:3609-3611` | list-iteration via helper | None-safe already (`;3627`) |
| 11 | instance_lifecycle resume wake | `daemon/services/instance_lifecycle.py:3750-3756` | list-iteration via helper | |
| 12 ⭐ | **registry-mint chat notify path** | `daemon/services/instance_messaging.py:2086-2087` | list-iteration via helper | **CRITICAL** — every `telegram:` / `slack:` / `discord:` row traverses `registry.py:857` → `enqueue_message_job` → `:2087`. Missed in the original plan; without fan-out here, the chat lane rides the 3s poll on the PRIMARY chat ingress path. |
| 13 ⭐ | eligible_pending_sweep wake | `daemon/services/eligible_pending_sweep.py:287-294` | **ctor-widening required** | holds a DIRECT `self._worker_pool` ref (singleton-attribute reach). The ctor MUST be widened to take the manager (or both pools) — the helper is then invoked via the manager reference. Singleton-attribute reach is forbidden post-amendment. |
| 14 | waiting_children_watchdog wake | `daemon/services/waiting_children_watchdog.py:1595-1597` | list-iteration via helper | (already holds `manager` reference — route through `manager._notify_all_pools()`) |
| 15 | job_feedback_observer wake A | `daemon/services/job_feedback_observer.py:3400-3405` | list-iteration via helper | holds `self._instance_manager` — route through helper |
| 16 | job_feedback_observer wake B | `daemon/services/job_feedback_observer.py:3680-3685` | list-iteration via helper | same |
| 17 | job_feedback_observer wake C | `daemon/services/job_feedback_observer.py:4279-4283` | list-iteration via helper | same |
| 18 | job_processor wake | `daemon/services/job_processor.py:1262-1266` | list-iteration via helper | defense-in-depth |
| 19 | long_tool_nudge wake | `daemon/services/long_tool_nudge.py:1184-1187` | list-iteration via helper | defense-in-depth |

**Rationale:**
- All recovery paths are DB-row/heartbeat-based and pool-agnostic (C.1) — **safe** with a second pool.
- **The single missed site #12 (`daemon/services/instance_messaging.py:2086-2087`) is the notify path every registry-minted chat row traverses** (`registry.py:857` → `enqueue_message_job` → `:2087`). Unamended, the feature's headline guarantee exists only via the 3.0s poll fallback (`daemon/services/worker_pool.py:356`), and **success criterion #3 cannot distinguish notify-path from poll-path pickup**. Given this project's wake/notify defect history (emit_terminal wrong-id → 15-min latency under saturation, `7807e521`; stranded READY notes without Task/notify, `421c6a3d`), this is exactly the class that ships silently.
- Site #13 (`daemon/services/eligible_pending_sweep.py`) holds a DIRECT `self._worker_pool` reference — singleton-attribute reach is a smell that hid the second pool. The constructor MUST be widened (either to take the manager or both pools) so the helper is invoked explicitly.
- Sites #14–#19 already hold a reference to `manager` (or `self._instance_manager`); widening is a one-line change to call `manager._notify_all_pools()` instead of `manager._worker_pool.notify_work()`.

**List-shape rationale (`self._pools` not inline 2-tuple):**
- Iterating a `self._pools: list[WorkerPool]` makes a future third lane (priority, per-tenant) a one-slot + one-list-entry change. The 2-tuple form would have to be touched in N places.
- The list is populated at `setup_worker_pool` time: `self._pools = [self._worker_pool, self._chat_worker_pool]` (None entries skipped at iteration; `USE_WORKER_POOL=false` leaves the list empty).
- Each pool's `notify_work()` is independent — per-pool `Condition` with `notify()` (NOT `notify_all`) is the architect-verified correct wake discipline (the `_notification_count` accumulator at `daemon/services/worker_pool.py:1337/:1346-1347` carries wakes across iterations; N rapid enqueues → N notifies → both idle chat workers wake on the first two, and the (N−pool_size)th message is picked up on worker-return-and-continue). No wake-semantics change.

**Race analysis (architect-verified, all clear):**
- **Boot window:** source adapters autostart with `AUTOSTART_DELAY_SECONDS = 60.0` (`daemon/sources/registry.py:58` definition; AUTOSTART log lines at `:230` and `:247` — approver N10 corrected from stale `:224-225`); `setup_worker_pool` runs at `api.py:369`, ~750 lines before `manager.start_sources` (`api.py:1119`). Benign — both pools exist before any chat row can arrive.
- **Check-then-wait window:** NONE — proper Condition discipline. `wait_for_work` holds `self._condition` across the whole loop including `wait(timeout=remaining)` (`daemon/services/worker_pool.py:1321-1351`, `:1335/:1345`); `notify_work` acquires the same condition before `notify()` (`:1300-1306`); `_notification_count` (`:1337/:1346-1347`) carries wakes across iterations. No lost wakeup.
- **Claim-in-flight:** notify during a synchronous claim increments the count; the worker sees count>0 on return to `wait_for_work` and re-claims. Safe.
- **Thundering herd:** not material — per-pool `Condition`, one waiter woken per `notify_work()`, sub-µs ops, 2-3 notifies per typical lifecycle. Doubling notifies across two pools is noise.

**Test assertion (Phase 3, mandatory):** the headline saturation-isolation test (Phase 3 Task #1) MUST include the assertion *"no `wait_for_work(3.0s)` timeout-expiry wake observed during the chat-claim window"* — i.e., prove the notify path delivered the claim, not the poll fallback. Without this assertion, success criterion #3 cannot distinguish the two paths and the feature's headline guarantee is untestable.

**Rejected alternatives:**
- Per-site widening (`if x is not None: x.notify_work() for x in [...pools]`) — verbose, easy to miss a site (proven by the original 9-site miss).
- Pool-group abstraction (`WorkerPoolGroup.notify_all`) — over-engineered for two pools; introduces a new class without a second-order benefit.
- `notify_all` per pool (worker A's suggestion) — forces wasted claim round-trips; the `_notification_count` discipline is verified sound. No benefit, added risk.

---

## D6. Admission accounting

**Decision: UNCHANGED single per-queue admission. Lane split at claim seam only.**

- Chat jobs continue to resolve their `queue_id_for_job` exactly as today (`instance_messaging.py:2259-2345`).
- Per-queue `concurrency_limit` (`job_queues.concurrency_limit`, `daemon/repositories/job_queue/models.py:213` [approver N8 — corrected from stale `:338`; the column is on `job_queues` model not `JobItem`] + admission code `:3484-3538`) is **unchanged**.
- Lane isolation is enforced at the **claim** step (D1) — admission alone cannot isolate lanes anyway (C.2).

**Rationale:**
- Chat users can route to whatever queue they want via existing `queue_id` resolution (admin can already set `concurrency_limit=10` for a chat-heavy queue).
- Adding a dedicated chat queue row would touch `queue_id` resolution logic, `JobItem` mirror, and possibly `SourceCreate` config — vastly larger blast radius.
- The missing piece was the claim-side lane filter (A.2 / D1); admission does not need to know about lanes.

**Rejected dedicated chat queue with own `concurrency_limit`:**
- Touches router → service → repository → SQL stack (queue_id propagation chain).
- Mixing two axes (queue + lane) at admission creates a 2D routing table that's hard to reason about.

**Rejected shared raised cap:**
- No "shared" cap exists — each queue has its own `concurrency_limit` already.

---

## D7. Kill-switch

**Decision: Hardcoded constants (CHAT_WORKER_POOL_SIZE=2 + CHAT_SOURCE_PREFIXES tuple). NO new ENSEMBLE_* flags. Activation = rebuild+restart. Rollback = revert.**

- `CHAT_WORKER_POOL_SIZE: int = 2` added to `daemon/constants.py` next to `WORKER_POOL_SIZE` (`:54`).
- `CHAT_SOURCE_PREFIXES: tuple[str, ...] = ("telegram:", "slack:", "discord:")` added next to it.
- The existing `USE_WORKER_POOL` env kill-switch (`manager.py:6424-6427`) — when set to `false/0/no` — kills **both** pools. This is acceptable: the flag is rarely used (offline-only).

**Rationale:**
- **Convention (o) + 7d5285aa**: ship always-on, NO new `ENSEMBLE_*` flags for bugfixes/improvements; tuning-only knobs OK.
- Both knobs (size + prefixes) are tuning knobs — but rare enough that an env flag is overkill.
- Hardcoded constants are visible at the import site, easy to grep, easy to pin in `test_constants.py`.
- Activation = rebuild+restart matches the existing `WORKER_POOL_SIZE` convention.
- Rollback = revert the PR — no state machine to unwind.

**Rejected `ENSEMBLE_CHAT_WORKER_POOL_SIZE` / `ENSEMBLE_CHAT_SOURCE_PREFIXES`:**
- Violates the convention.
- Adds a new env-resolver seam (`os.environ.get("ENSEMBLE_CHAT_WORKER_POOL_SIZE", ...)`) — `WORKER_POOL_SIZE` itself is hardcoded (A.8) precisely to avoid this drift.
- Two kill-switches (`USE_WORKER_POOL` + `ENSEMBLE_CHAT_*`) for one feature is redundant.

**Rejected dynamic via config file:**
- Workers' pool size is a HARDCODED module constant per `daemon/constants.py:54`; no config-resolver seam. Consistency.

---

## D8. Invoke semaphore

**Decision: UNCHANGED global cap 4. DO NOT split per-lane.**

- `_invoke_semaphore` singleton at `daemon/utils.py:554-569` stays at `max(1, WORKER_POOL_SIZE - 1) = 4`.
- No new kwargs to `invoke_agent_and_wait` (`:588-599`); no lane-aware selector.

**Rationale (deadlock-safety argument):**
- **Child-routing fact (D3)**: when `invoke_agent_and_wait` blocks a parent waiting for a child, the child is **always default-lane** (mint-site census B.2 — children are stamped `agent:{caller}` or `internal_agent:{caller}`, never chat-prefixed).
- **Blocked-parent holds its worker**: the parent worker thread is occupied holding the semaphore (`:647` `await`) waiting for the child.
- **Worst case under cap 4** (reviewer F8 corrected): both chat workers CAN simultaneously be invoke-blocked (`utils.py:647` has NO per-lane cap; the global cap is 4, which exceeds the chat-pool size of 2). If both chat workers invoke-block on chat messages whose children are default-lane:
  1. Both chat workers occupy slots 1-2 of the semaphore; the 5 default workers are unaffected.
  2. Chat-lane STALL until invoked children complete — bounded by the DOUBLE BOUND: `invoke_agent_and_wait(timeout=300.0)` inner (`utils.py:596`) + `StaleTaskRecovery` heartbeat ~5min (`manager.py:6482-6508`) + `JobRecoveryService` 300s drift reconcile. Worst case = ~5-minute stall, then the recovery ladder kicks the parent.
  3. **Default lane keeps ≥1 drainable worker** (5 default workers, none invoke-blocked under this scenario) → default-lane children STILL claimable. **No deadlock; bounded degrade.**
- **Default-side parallel scenario** (4 default invoke-blocked parents + 1 free default worker): chat workers are unaffected (chat pool = 2, max 2 chat invokes; cap 4 covers both pools). Chat messages continue to be processed by chat workers while default work drains through the 5th default worker.
- **Conclusion (UNCHANGED by F8 correction):** strict two-way isolation + cap 4 + non-inheritance produces **degrade, not deadlock**, in every reachable configuration.

**Rejected global cap raised to `total−1 = 6` (PROVEN UNSAFE):**
- 5 default workers could all be blocked parents; their children are default-lane; 0 free default workers; 2 idle chat workers cannot claim default-lane tasks → **deadlock** (strict two-way isolation per D2 makes chat workers UNABLE to claim default work).

**Rejected SPLIT default 4 / chat 1 (lane-aware selector):**
- Requires replacing the singleton with a lane-aware dispatch mechanism (e.g., a dict `{"default": Semaphore(4), "chat": Semaphore(1)}`).
- Adds a new seam at every `invoke_agent_and_wait` call site (today there is one — `daemon/utils.py:647`).
- The deadlock analysis above shows it is **not required** for correctness given non-inheritance. Split would add complexity for zero safety gain today.
- Defer until proven necessary (e.g., if chat-pool users start invoking chat-pool agents recursively — not in scope).

**Invoke await is NOT unbounded — DOUBLE BOUND (architect verification §3):**
- The `await` at `daemon/utils.py:647` is bounded by BOTH:
  1. **Inner bound:** `invoke_agent_and_wait(timeout=300.0)` — explicit 5-minute timeout at `daemon/utils.py:596`.
  2. **Outer ladder:** `StaleTaskRecovery` (`daemon/manager.py:6482-6508`, heartbeat threshold ~5min) + `JobRecoveryService` drift reconcile (300s `min_pending_age`).
- A hung child resolves at ~5-minute scale, not never.
- **Worst-case latency chain (F8 corrected):** either (a) both chat workers invoke-blocked → ~5-min chat-lane stall bounded by the double bound, with default lane still draining; or (b) 4 default parents' children serialized through the 5th default worker + FIFO semaphore queue for a chat parent → multi-minute tail under 4× simultaneous chat invokes; each recursion level consumes a slot, self-bounding at cap depth 4. **Degrade, not deadlock** — explicitly per the architect's verified ladder.

---

## D9. Engine budget

**Decision (caller requirement, re-framed): state the full engine picture, not workers-only.**

**Workers-only view:**
- Single production engine: `pool_size=5 + max_overflow=10 = 15` (`daemon/repositories/factory.py:245-260`; does NOT read `DatabaseConfig` `:90-91`).
- `5 + CHAT_WORKER_POOL_SIZE(2) = 7 ≤ 15` — 2.1× margin.
- Workers hold connections only for ms-scale sync ops (claim heartbeat, status persist). 7 × ~ms-scale ≪ 15.

**Full-engine view (architect amendment):**
- The ONE 15-conn engine also serves **concurrent HTTP handlers** + **5 sweep/watchdog services** (`JobRecoveryService`, `JobLockSweep`, `EligiblePendingSweepService`, `waiting_children_watchdog`, `OrphanWatcherSweep`). Realistic worst ≈ **12-15 connections AT CAP** under sustained API load — the 2.1× workers-only margin shrinks materially.
- **Verified separately (NOT shared):** the LangGraph checkpointer uses its OWN asyncpg pool (`daemon/checkpoint_adapter.py:433`); `PlaneSync` is HTTP-only.
- **Failure mode at exhaustion:** SQLAlchemy pool `TimeoutError` (30s default). The error is **absorbed by the existing `TaskProcessor` retry path** — degraded latency (claim may take up to 30s to retry), **no corruption**, no data loss. Discipline `with engine.begin()` releases connections promptly.
- **Phase 1 acceptance (workers-only):** `pool_size + max_overflow >= sum(worker pools) by ≥2× safety margin` (15 vs 7 → 2.1× margin).
- **Operational note:** if observed pool TimeoutErrors under sustained API load coincide with the chat-lane rollout, the operator increases `pool_size` / `max_overflow` via a follow-up PR (D7 says no env flag, so this is a code change + rebuild). Not required for the chat lane to function; cap is a non-corrupting degradation.

**Note:** `ens_db_tools` repair engine is **separate, maintenance-only** — does NOT enter the budget math.

---

## D10. Failure modes (explicit plan tasks)

### D10.1 Misrouted/forged source — TWO vectors, FIVE pins

**Decision: Extend the existing HTTP-API gate at `daemon/routers/jobs_crud.py:478-495` to reject chat prefixes, AND add a registration-time validator at `create_source` (`daemon/routers/sources.py:98+`) to close the operator vector. Both go in the SAME commit. Both pinned by a 5-part test contract.**

**5-pin coupled-reservation pattern (architect amendment A7.1) — all in the SAME commit:**

1. **Pin 1 — Provenance doc-bullet at `CHAT_SOURCE_PREFIXES` in `daemon/constants.py`:** mirror the `:455-480` style (the existing `RESERVED_SOURCE_PREFIXES` documentation); explicitly name `daemon/sources/registry.py:857` as the legitimate mint site; state that minting via any other path is a bug. This makes the constant self-documenting at the import site.
2. **Pin 2 — Exact-equality + helper-parity pins in `tests/unit/routers/test_source_reservation.py`:** new `TestChatSourcePrefixesConstant` mirroring `:87-124` (asserts `CHAT_SOURCE_PREFIXES == {"telegram:", "slack:", "discord:"}` exactly). Extend the existing helper tests at `:126-189` with: `is_chat_source(None) == False`, `is_chat_source("") == False`, `is_chat_source("telegram:user:1") == True`, `is_chat_source("webhook:gh") == False`, case-sensitivity (`is_chat_source("TELEGRAM:foo") == False`).
3. **Pin 3 — Gate-behavior pin:** new parametrized `test_create_job_rejects_chat_prefix` in `TestCreateJobSourceBoundary` (mirror `:252-299`), asserting 422 + `JobValidationError` envelope for each of `telegram:fake`, `slack:fake`, `discord:fake` AND the existing reserved-prefix cases (`agent:foo`, `internal_agent:foo`, `system:foo`) — proves the gates compose without regression.
4. **Pin 4 — E2e matrix in `test/packs/origin_contract_e2e_probe_test.py` PART 1 (`:151+`):** 3 `gate_422` cases for the chat-prefix set. **No census change** — chat prefixes are NOT reserved members; the case comment must say so. **Fix the stale gate reference at `:33`** — it currently points to `:299-316`; the actual gate lives at `:478-495`. Update in the same commit. **Reviewer F2 — same-commit list extends beyond the 5 pins to include existing assertions that hard-code the OLD "telegram:*-source POST passes through" behavior (these will now incorrectly assert 201 and must be FLIPPED to expect 422 in the same commit):**
   - **(i)** FLIP e2e probe case 8b at `test/packs/origin_contract_e2e_probe_test.py:281-284` — currently POSTs `source="telegram:123"` expecting `ok`; after the gate widening, this case MUST expect `gate_422` (the row is now rejected). Update the case comment to reference D10.1 + F2.
   - **(ii)** UPDATE the pass-through unit test at `tests/unit/routers/test_source_reservation.py:~735-751` — today asserts that a `telegram:`-source POST passes through (returning 201 or similar). After the gate widening, this test MUST assert 422 + the `JobValidationError` envelope.
5. **Pin 5 — Constants completeness pin:** `TestConstantsCompleteness.expected` set in `tests/unit/test_constants.py` (`:147-159`) MUST include both `CHAT_WORKER_POOL_SIZE` and `CHAT_SOURCE_PREFIXES` — cross-reference Phase 1 Task #3.

**HTTP-API gate extension:**
- `is_chat_source(body.source)` returns True for `body.source.startswith(p)` for any `p in CHAT_SOURCE_PREFIXES`. The HTTP API then raises `JobValidationError` (422) with the same envelope shape as the existing `is_reserved_source` check (`daemon/routers/jobs_crud.py:478-495`).
- Server-stamped `source` (B.2 — `agent:`, `internal_agent:`, `scheduler`, `api`, `system:*`) is unaffected; only direct user-submitted source values are blocked.
- **Coordination note:** the backlog's gate-widen task covers all 18 reserved prefixes. This plan scopes ONLY the chat-lane-needed extension; the full gate-widen is a separate work item that the same PR can include if scope allows.

**Registration-time validator (architect amendment A7.2 — LEADER-ACCEPTED into Phase 1 scope):**

**The HTTP gate does NOT mitigate the OPERATOR vector.** `SourceCreate.source_id` is free-form per `daemon/models/source.py:31-37` (pattern `^[a-zA-Z0-9_-]+$`):
- A Telegram adapter with `source_id="tg-prod"` mints `tg-prod:user` → **not** chat-prefixed → real chat traffic silently rides the **default** lane with **ZERO runtime signal** (no log, no error, no rejection). Feature inert for that adapter.
- `source_type="discord"` with `source_id="telegram"` mints `telegram:user` → silently rides the **chat** lane (no `discord:` row exists). Cross-type misconfig mint into the wrong lane.
- **Detection difficulty is HIGH** — zero runtime mismatch signal (already flagged 🟠 in `.agents/tester/LESSONS/2026-08-30-origin-census-reverse-scan-scheduler-gap.md:16`).

**Minimal fix, no migration:** validator at `create_source` (`daemon/routers/sources.py:98+`):
- When `source_type ∈ {"telegram", "slack", "discord"}`, require `source_id.lower()` to equal the type name (so the minted prefix lands in `CHAT_SOURCE_PREFIXES`).
- Reject with the standard error envelope (`JobValidationError` shape from `daemon/routers/jobs_crud.py:478-495`).
- **Pin:** add a test case to `tests/unit/routers/test_sources.py` (or the equivalent existing test for `create_source`) covering `source_type="telegram"` + `source_id="tg-prod"` → 422, `source_type="discord"` + `source_id="telegram"` → 422, `source_type="telegram"` + `source_id="telegram"` → 201.

**3-vs-5 prefix asymmetry pin (architect amendment A7.3):**
- `_USER_ORIGIN_PREFIXES` (`daemon/tools/upgrade_journal.py:1077-1079` — NOTE the correction: it lives in `daemon/tools/`, NOT `daemon/services/`) lists **FIVE** prefixes (adds `webhook:`, `whatsapp:`); `CHAT_SOURCE_PREFIXES` lists **THREE**.
- The exclusion is a **deliberate scope decision** (webhook ≈ CI/automation, not interactive chat). MUST be documented at the constant AND pinned (`is_chat_source("webhook:gh-hook") == False`, `is_chat_source("whatsapp:1234") == False`) so the gate/lane symmetry is an invariant, not an accident.
- Document at `CHAT_SOURCE_PREFIXES` docstring: "Interactive-chat prefixes only. `webhook:` / `whatsapp:` are explicitly excluded — see `_USER_ORIGIN_PREFIXES` in `daemon/tools/upgrade_journal.py` for the broader user-origin set."

**Breaking-change check (architect amendment A7.4) — CLEAN, with reviewer F2 corrections:**
- Exhaustive grep found ZERO production-data tests POSTing chat-prefixed sources expecting 201. **Reviewer F2 found TWO test assertions that hard-code the OLD "telegram:*-source POST passes through" behavior** — they will now incorrectly assert 201 and must be FLIPPED in the same commit (see Pin 4 extensions (i) and (ii) above).
- **Same-commit updates = the 5 pins in A7.1 + the two F2 test flips.** These are documentation/test flips ONLY — no production-behavior surprise for any caller who already sent legitimate traffic (no production code path was ever POSTing chat-prefixed sources to `/api/jobs`).

**Rejected server-stamped lane authority:**
- Would require a new column on `Task` (rejected in D1) OR a join-on-write pattern.
- Gate widening + validator is a smaller, surgical fix to two known gaps.

### D10.2 Chat-lane saturation — TWO cases, no new fast-path

**Decision: Document expected behavior. SPLIT saturation into two cases (architect amendment A4.1). NO new notify-on-enqueue fast-path needed (architect amendment A4.2).**

**Case (i) — Idle-transition pickup:**
- Chat worker is idle, no chat work in flight, `on_pending_task` fires (`manager.py:6444`) → `_notify_all_pools()` (D5 amendment) wakes the chat worker.
- Pickup latency: sub-second once D5 wake fan-out lands.
- **This is the common case** — chat traffic is bursty; the chat pool is mostly idle.

**Case (ii) — Burst > pool_size with backlog > 2:**
- All chat workers busy. New chat arrivals land in `message_queue.status='ready'`.
- Workers re-claim **immediately** after finishing a task (`daemon/services/worker_pool.py:343` `continue`); the 3s wait applies **only** to the empty-claim idle transition.
- Pickup latency for the (N−pool_size)th message = **next worker return-and-continue** = **ONE TASK DURATION (~30-300s), NOT one poll cycle**.
- This is saturation semantics (identical to the default pool today) and matches D2's accepted envelope.
- **Worst case for a single queued chat message:** bound by the longest-running chat task currently in flight. Under normal loads, this is seconds.

**NO new notify-on-enqueue fast-path needed:** the fast path already exists (`on_pending_task` → `notify_work`); it merely must fan out to the chat pool per D5 amendment. Stating this explicitly preempts a redundant mechanism proposal.

**Operational note:** if chat pool > 2 sustained saturation is observed, the operator increases `CHAT_WORKER_POOL_SIZE` (hardcoded; rebuild+restart) per D7.

**Test fixture-validation requirement (architect amendment A4.1):** Success criterion #6 ("3rd concurrent chat waits ≤3s") is only valid if the first two chat tasks are SHORT — pin the test fixtures accordingly or the criterion is unmeetable as written. See Phase 3 Task #4 conditional and the new fixture-validation task.

### D10.3 Boot ordering

**Decision: Construct chat pool WITHIN `setup_worker_pool` AFTER the default pool starts, BEFORE `late_wire` callbacks. Match boot-line convention.**

- Boot order in `daemon/api.py:369 → manager.setup_worker_pool` (approver N10 — narrative aligned to actual code order):
  1. `USE_WORKER_POOL` env check (kills **both** pools).
  2. `MainLoopBridge.set_loop`.
  3. Construct `TaskProcessor` (shared singleton) — `manager.py:6683`.
  4. Construct default `WorkerPool` → `self._worker_pool` — `manager.py:6697` (constructor); `manager.py:6708` (`start()`).
  5. Construct `StaleTaskRecovery` (singleton).
  6. **NEW:** Construct chat `WorkerPool(task_processor=self._task_processor, num_workers=CHAT_WORKER_POOL_SIZE, worker_id_prefix="chat-worker-", lane="chat")` → `self._chat_worker_pool` (approver N2 — `task_processor` is the shared singleton, passed positionally; matches the default-pool constructor shape at `manager.py:6697`).
  7. **NEW (B1 — flip strictness ON):** `self._chat_lane_active: bool = True` immediately after `self._chat_worker_pool.start()`. From this point, default-lane claims consult the flag and exclude chat-prefix rows.
  8. Populate `self._pools: list[WorkerPool] = [self._worker_pool, self._chat_worker_pool]` (D5 list-shape — future third lane = one list entry).
  9. `_task_repo.on_pending_task` lambda updates to `lambda: self._notify_all_pools()` (D5 amendment).
  10. Late-wire `set_work_resolver` + `set_watcher_repo` on **both** pools (api.py:447-449 widens).
- Boot line matching `api.py:771-775` style: `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"`.
- Default pool boot line stays `"Worker pool started with {n} workers (timeout=…min)"` (`manager.py:6710`).

### D10.4 Pool lifecycle (non-restartable WorkerPool)

**Decision: Fresh at boot; teardown entry in `shutdown_worker_pool`; stop semantics finish-current-task; hung-worker WARNING on timeout (architect amendment A5.1).**

- `WorkerPool` is non-restartable (`daemon/services/worker_pool.py:1359-1360` raises `RuntimeError`). Two pools means **two non-restartable pools**, each can only be `start()`ed once.
- `shutdown_worker_pool` (`manager.py:6712-6727`) gains (B2 — snapshot BEFORE stop/None; iterate snapshot AFTER both stops; B1 teardown-ordering — chat pool FIRST so default pool claims chat rows again during its own stop window):
  ```
  # B2 — snapshot pools BEFORE any stop/None (so the WARNING can iterate live workers after both stops).
  pools_snapshot = [p for p in (self._worker_pool, self._chat_worker_pool) if p is not None]
  # B1 — chat pool stopped FIRST so _chat_lane_active flips to False before the default pool stops.
  # Default pool claims chat rows again during its own stop window = fail-open restored.
  for pool in pools_snapshot:
      if pool is self._chat_worker_pool:
          self._chat_lane_active = False  # B1 — flip fail-open BEFORE default pool stops
      pool.stop()
      if pool is self._chat_worker_pool:
          self._chat_worker_pool = None
          logger.info("Chat worker pool stopped")
      elif pool is self._worker_pool:
          self._worker_pool = None
          logger.info("Worker pool stopped")
  # B2 + N4 — iterate the snapshot (NOT the now-None slots) so the WARNING is reachable.
  for pool in pools_snapshot:
      for worker in pool._workers:  # N4 — pick ONE in-PR: document exception OR add is_any_worker_alive() accessor
          if worker.is_alive():
              logger.warning(
                  "Worker %s still alive after stop(30) — likely blocked in "
                  "invoke_agent_and_wait (mid-invoke stop not interruptible, see "
                  "daemon/services/worker_pool.py:1339-1340)",
                  worker._worker_id,
              )
  ```
- Shutdown sequence (`manager.py:11314`) — `shutdown_worker_pool` is already wrapped in `asyncio.to_thread`; stopping two pools sequentially under one thread is fine (each `stop()` finishes current task with `timeout=30`). Worst case under hung workers: 60s (two sequential 30s joins), accommodated by the outer sequence (pre-cancelled requests `:11312` + inflight grace `:11313` → total ~120s worst).
- **No second `StaleTaskRecovery`** (A.4 citizenship — singleton).
- **Why the WARNING matters:** a worker blocked in `invoke_and_wait` is not interrupted by `_stop_event` (fires only inside `wait_for_work`, `:1339-1340`); the daemon thread dies with the process (`:246`) — no deadlock, but a possible 30s hung join. The WARNING makes this observable (vs. silent on the operator's monitoring). The worker's `_worker_id` prefix identifies which pool hung (default vs chat).

### D10.5 Worker-id namespace collision

**Decision: Distinct prefix `chat-worker-{i}` for the chat pool.**

- Default pool: `worker-{i}` (`daemon/services/worker_pool.py:1366`).
- Chat pool: `chat-worker-{i}` — passed as `worker_id_prefix` constructor kwarg (add to `WorkerPool.__init__`).
- Stamped into `task.worker_id` at `:1882`; lineage in logs/tasks/aggregations distinguishes the two pools.

---

## D11. HOL (head-of-line) boundary

**Decision: Chat lane removes chat-origin rows from the default FIFO → reduces chat-side HOL exposure. Default-lane HOL (wedge-class IV, `9fe96dea`, OPEN) is NOT fixed and is OUT OF SCOPE.**

- Chat-prefixed rows no longer enter the default pool's FIFO → default pool no longer HOL-blocks chat messages behind a long default job.
- The converse — default-lane HOL — is a separate wedge class (open). Fixing it requires queue-aware scheduling changes (`concurrency_limit` rebalancing, priority inheritance, etc.) — **NOT in scope**.
- State the boundary explicitly in `phase3-plan.md` test-gate criteria.

---

## D12. Observability

**Decision: Boot line (verifiable activation, per convention); distinguishable worker ids in logs/task rows (mandatory, from D10.5); get_stats parity NOT REQUIRED (tests-only consumers today).**

- Boot line: `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"` (matches `api.py:771-775` style).
- Worker ids: `chat-worker-{i}` (D10.5) — visible in `task.worker_id` rows and in worker logs (`daemon/services/worker_pool.py:1366`).
- `get_stats` parity (`daemon/services/worker_pool.py:1425-1455`): keep minimal. Tests-only consumers today. `/readyz` does not read pool state — no registration needed.
- Optional (deferred): add `chat_pool_stats` to `/readyz` if operators request it. P3 follow-up.

---

## Cross-decision coherence

The decisions compose as follows:

1. **D1** (predicate) + **D2** (strict two-way) + **D3** (non-inheritance) define the routing contract.
2. **D4** (mixed-provenance) + **D5** (fan-out) + **D6** (admission) define the concurrency model.
3. **D7** (kill-switch) + **D8** (semaphore) + **D9** (engine budget) define the resource model.
4. **D10** (failure modes) operationalizes the contract.
5. **D11** (HOL) + **D12** (observability) scope the boundary.

The decisive inputs are:
- **D3 (non-inheritance)** is the linchpin — it makes D2 (strict two-way) deadlock-safe under D8 (unchanged semaphore).
- **D1 (no schema change)** keeps the plan migration-free and the forge gap (D10.1) an orthogonal, surgical fix.
- **D7 (hardcoded constants)** aligns with the project's convention (o) and avoids env-resolver drift.

