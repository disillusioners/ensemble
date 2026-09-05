# Phase 2: Scanner + Gate (R2 inputs) + Both-Branches Composition + In-Graph Nudge

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (revised in reconciliation pass)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase1-plan.md`](./phase1-plan.md), [`phase3-plan.md`](./phase3-plan.md), [`phase4-plan.md`](./phase4-plan.md), [`phase5-plan.md`](./phase5-plan.md), [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md), [`research-findings.md`](./research-findings.md)

---

## Objective

Ship the scanner (pure function over `state.values['messages']`), the gate decision logic (pure function over R2 inputs `(attestation, pending_children, queued_or_expected_wakeups, denied_count, bound, scope, mode)`), the **D1=B in-graph deny nudge** (checkpoint-durable `HumanMessage` injected on deny, routed back to `agent` — NO `manager.enqueue_message` on deny per R1/C1b/C5 fork ruling), and the **both-wiring-branches composition** (independent `attestation_enabled` flag active in BOTH return paths of `create_should_continue(language_check_enabled)` — C2). The scanner and decision logic are D1-independent; only the wiring site is gated on `language_check_enabled` shape (C2).

Entry criterion: D3, D4, D9, D10 are decided by the architect; Phase 1 (tool + prompt contract) is merged; compaction-spike result from Phase 1 task 1.7 is in hand.

Exit criterion: scanner passes all unit tests (window bounds, text-only-claim-doesn't-count, tool-name match, summary-message handling); gate decision function is unit-testable in isolation; **parameterized activation test over `language_check_enabled ∈ {True, False}` PASSES (C2 exit criterion)**; in-graph deny nudge path is implemented and integration-tested; the access path to subtree/pending state held by manager is specified and wired.

---

## Entry Criteria

- Phase 1 (tool + prompt contract) is merged
- Compaction-spike result from Phase 1 task 1.7 is in hand (D10(b) decision)
- D3 (scope: leader-only — RESOLVED but still listed as architect confirmation), D4 (window N), D9 (finalize ordering — moot for D1=B), D10 (visibility edge cases) are decided by the architect
- Default behavior on unresolved: D3=leader-only, D4=N=3, D9=no-blocking (moot for D1=B), D10(a3)+(b1)+(c1)
- **R1 / R2 / C2 / C3 are RESOLVED at plan level** — this phase implements them without further architect decision

---

## Tasks

### 2.1 — Implement the scanner pure function

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_scanner.py` (new) |
| **Description** | Pure function over `list[BaseMessage]`. Default signature: `scan_for_attestation(messages: list[BaseMessage], window: int, tool_name: str = "attest_completion") -> tuple[bool, list[dict]]`. Walks the LAST `window` `AIMessage`s (D10(a3) recommended: any AIMessage in the window counts; if D10 picks (a1) or (a2), adjust the algorithm). For each AIMessage, check `tool_calls[i].name == tool_name` for any tool_call in the list. Return `(attested, diagnostic_detail)` where `diagnostic_detail` is a list of `{index, tool_call_names, attestation_present}` per AIMessage inspected. The function MUST NOT scan beyond the window (AC-2.5: 1000-message state scanned in N=3). |
| **Decision tags** | [D4] (window N), [D10(a)] (window semantics — see 2.5 for D10(b) compaction handling) |
| **Test notes** | Unit test file `tests/unit/test_attestation_scanner.py`. Cases: AC-2.1 (attested within window), AC-2.2 (attested outside window), AC-2.3 (text-only claim), AC-2.4 (non-attestation tool calls), AC-2.5 (window bounds with 1000-message state), summary-message handling (D10(a) edge case), `additional_kwargs` markers (e.g., `language_check_reminder=True`) excluded. |

### 2.2 — Implement the gate decision pure function (R2 inputs)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (new) |
| **Description** | Pure function. Signature: `decide(attested: bool, pending_children: int, queued_or_expected_wakeups: int, denied_count: int, bound: int, scope_applicable: bool, mode: Literal["off","dry","enforce"], attestation_enabled: bool) -> Decision` where `Decision` is a dataclass / TypedDict with fields `decision: <canonical Decision enum — see phase4 task 4.5>` (the canonical enum is `allowed | denied | terminal_after_bound | dry_log | allowed_legitimate_pending_wakeup`; this task references the phase4 block VERBATIM and does NOT redefine the enum), `next_denied_count: int`, `attest_seen_outside_window: bool`, `should_inject_nudge: bool`, `scanner_window_truncated: bool` (diagnostic — surfaced into the canonical log schema's `scanner_window_truncated` field), `scanner_summary_seen: bool` (diagnostic — surfaced into the canonical log schema's `scanner_summary_seen` field). **R2 logic**: deny ONLY when `(not attested) AND (pending_children == 0) AND (queued_or_expected_wakeups == 0)`. `attestation_enabled=False` (C2 — wired by graph assembly) bypasses the gate entirely → `allow` regardless of inputs. `scope_applicable=False` (non-leader parent per D3) → `allow`. `mode="off"` → `allow` regardless. `mode="dry"` → ALWAYS `allow` (evaluation + decision logging only; ZERO side effects; no deny fires in dry). `mode="enforce"` → R2 deny path active. **Logic tree**: (1) attestation_enabled OR scope_applicable OR mode=="off" → `allowed` (gating meta-conditions); (2) mode=="dry" → `dry_log` (evaluation recorded but allow; Phase 4 task 4.4 spec); (3) attested AND mode=="enforce" → `allowed` + reset `denied_count` to 0 (architect addition); (4) not attested AND `(pending_children > 0 OR queued_or_expected_wakeups > 0)` → `allowed_legitimate_pending_wakeup` (R2 — legitimate delegation; the dedicated enum value is required for dry-log adjudication and per R2 — see requirements AC-3.3 and AC-E2E-1b); (5) not attested AND mode=="enforce" AND `denied_count + 1 > bound` → `terminal_after_bound`; (6) not attested AND mode=="enforce" AND `denied_count + 1 <= bound` → `denied` + `next_denied_count = denied_count + 1`. The function is the single source of truth for the decision value; BOTH return paths of `create_should_continue` (Phase 2 task 2.5) call it. **C3 fail-open**: the caller wraps this in `try/except Exception` and falls through to `allowed` on any exception (logged as `event=leader_completion_gate_error`). |
| **Decision tags** | [R2], [D3] (scope), [D2] (mode), [C2] (attestation_enabled wiring), [C3] (fail-open) |
| **Test notes** | Unit test file `tests/unit/test_attestation_gate.py`. Cases (parameterized): attestation_enabled=False → allow (regardless); scope_applicable=False → allow; mode=="off" → allow (regardless); mode=="dry" → dry_log + allow; attested + bound=3 + enforce → allow; not attested + pending_children>0 + enforce → allow (R2); not attested + queued_or_expected_wakeups>0 + enforce → allow (R2); not attested + bound=3 + denied=0 + enforce → deny next=1; not attested + bound=3 + denied=3 + enforce → terminal_after_bound; counter reset on allow-with-attest (architect addition). |

### 2.3 — Implement the gate composition function (R2 inputs + access path to manager-held state + in-node seam)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (same file as 2.2); `daemon/manager.py` (NEW facade forwarding methods); `daemon/services/instance_messaging.py` (NEW `get_queued_or_expected_wakeups` helper); tests under `tests/unit/test_attestation_gate.py` + `tests/integration/test_attestation_c2_both_branches.py` |
| **Description** | Glue function that wires scanner + decision together with R2 inputs. **Seam decision (CR-1)**: factory-closure capture of `instance_id` / manager handles at graph-build time (precedent `daemon/graph.py:4596` `create_question_pause_node(manager)` factory closure; verified) — the wrapper composed by `create_attestation_should_continue` closes over the per-instance `manager` handle and `instance_id`, and the in-graph routing fn receives `state` only at run time. This is the natural fit with **frozen D1=B** (gate lives in `create_should_continue` routing at `daemon/graph.py:6459-6484`; both branches return a wrapped `should_continue` that internally calls `evaluate(instance_id, denied_count, state["messages"], ...)`). The node-variant alternative (precedent `daemon/graph.py:2616` `language_check_node(state)` in-graph node evaluation) would require reconciling with D1=B and is **rejected here** to avoid reopening frozen decisions. **MVP seam: in-node `state["messages"]`** — the wrapper reads `state["messages"]` directly from the run-time `state` argument (the LangGraph routing fn already receives the full state); NO `aget_state` call is made from MVP. The `aget_state` thread-id-only discipline (per `_compaction_persist_seam.py:139` recipe — no `checkpoint_ns`) is RELOCATED to phase6 only; the MVP does not need it because the gate reads `state["messages"]` from the in-progress execution. **O8 unit assertion (replaces manual grep)**: add a unit test asserting the gate's config shape carries NO `checkpoint_ns` key (the in-node pattern must NOT thread checkpoint_ns into the scanner config) — this is the unit-level O8 guard, not a grep. |

**R2 access path to subtree/pending state held by the manager** — sources grounded in `daemon/` code facts (not paraphrased):

- **(a) `pending_children`** is sourced from `count_pending_for_target_sync(target_instance_id) -> int` at `daemon/services/dependency_bus.py:996-1023` (sync method; delegates to the repository at `daemon/repositories/dependency_bus/repository.py:429-473`, count at `:465-473` — counts `dependency_watchers` rows WHERE `target_instance_id == X` AND `state == PENDING`). **No manager facade exists today** for the bus — current access is via the module singleton `get_dependency_bus` (singleton instance created at `daemon/api.py:1339-1363`, exported by `daemon/manager.py:83`). **NEW facade**: add `manager.count_pending_children(instance_id: str) -> int` on `daemon/manager.py`, forwarding to `bus.count_pending_for_target_sync(instance_id)` via `asyncio.to_thread` (the bus count is a sync repo read; the manager facade runs it via `asyncio.to_thread` per the existing `daemon/manager.py:4532-4554` `cancel_bus_watchers_for_task_async` thread→loop bridge precedent). The facade follows the existing `daemon/manager.py:8427-8433` `get_queue_stats` forwarding pattern (service impl `daemon/services/instance_messaging.py:4496-4563`).

- **(b) `queued_or_expected_wakeups`** is a NEW helper defined in `daemon/services/instance_messaging.py` with signature `get_queued_or_expected_wakeups(instance_id: str) -> int`. The helper counts the sum of due-gate-filtered `next_retry_at > now` PENDING rows across **THREE** tables (per `daemon/services/instance_messaging.py:1960-1973` `enqueue_message` having NO `scheduled_at` kwarg — there is NO scheduled-message API anywhere; every scheduled/expected wakeup is a `next_retry_at` row + due-gate filter):
  1. **Task** table (`daemon/repositories/task/models.py:158`; `next_retry_at` set via `TaskRepository.schedule_retry` at `task/repository.py:3206-3215`, atomic `RetryTurn` cancel+INSERT at `:3752-3796`; due-gate `claim_pending_task` at `:1348`),
  2. **message_queue** table (`models.py:69` via `message_queue/repository.py:374`; due-gates at `:144`, `:171`, `:241`, `:300-306`),
  3. **job_queue_items** table (`job_queue/models.py:378`; RETRY transition `job_queue/repository.py:1546-1688`).

  PLUS the **expected-not-scheduled** wakeups (held until a non-defer queue empties or until resume):
  - `enqueue_message` to a PAUSED instance writes a PENDING Task held until resume (`daemon/services/instance_messaging.py:2005-2012`),
  - `is_deferred=True` messages held until non-defer queues empty (`daemon/services/instance_messaging.py:1995-2003`).

  All four counts are summed; the helper returns the int. Real-test recipe for a scheduled wakeup is `TaskRepository.schedule_retry(task_id, max_retries>=1, next_retry_at=now+Δ)` (one txn: cancels parent `retry_scheduled=True` + INSERTs PENDING child with `next_retry_at`; fires when `claim_pending_task` passes `next_retry_at<=now` at `:1348`) — or `requeue_task_with_backoff` at `:1754` (jittered 0.5–2.0 s) for sub-second. **NEW facade**: add `manager.get_queued_or_expected_wakeups(instance_id: str) -> int` on `daemon/manager.py`, forwarding to the service helper via `asyncio.to_thread` (same thread→loop pattern as the pending_children facade).

The gate composition function does NOT introduce new state — it READS state the manager already owns. The function signature is `evaluate(instance_id: str, denied_count: int, messages: list[BaseMessage], mode_resolver: Resolver) -> Decision` — `messages` comes from `state["messages"]` in-node (no `aget_state`). The function: (i) calls `scan_for_attestation(messages, window=mode_resolver.window, tool_name="attest_completion")` (Phase 2 task 2.1); (ii) calls `manager.count_pending_children(instance_id)` and `manager.get_queued_or_expected_wakeups(instance_id)` (the two new facade methods) via `asyncio.to_thread.run` or a sync bridge appropriate to the calling context (the in-graph routing fn is synchronous so a sync bridge is used — the manager facade methods are `def`, not `async def`, returning the int directly); (iii) calls `decide(...)` (Phase 2 task 2.2); (iv) emits a structured log entry with the canonical schema (Phase 4 task 4.5 defines the schema VERBATIM; Phase 2 calls the logger with the schema fields populated); (v) computes `attest_seen_outside_window: bool` (was the tool call present anywhere in `messages` but NOT in the last N? — diagnostic for O3 stale-pre-revive watermark). **C3 fail-open wrapper**: `evaluate()` is wrapped in `try/except Exception` at the call site (Phase 2 task 2.5 wiring); on any exception, `allowed` is returned and `event=leader_completion_gate_error` is logged. This is the single function called from `create_should_continue` (Phase 2 task 2.5) under D1=B.

| **Decision tags** | [R2] (access path to manager-held state), [D4] (window from resolver), [D9] (gate at would-be-END pre-END), [C3] (fail-open wrapper), [CR-1] (data sources + manager facade) |
| **Test notes** | Unit test mocks the two NEW manager facade methods returning synthetic ints; integration test (Phase 5 task 5.5) runs the real graph. **No `aget_state` mock needed** for MVP — the in-node pattern reads `state["messages"]` directly. |

### 2.3.1 — Watcher-registration TOCTOU race contract (CR-2)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (Phase 2 file — docstring-only addition); `daemon/manager.py` (docstring on the spawn helper); no new code |
| **Description** | **TOCTOU race contract (CR-2):** A leader dispatches a child via the dispatch tool and ENDs the turn in the same cycle. If the `dependency_watchers` PENDING row commits AFTER the gate's `pending_children` read, the gate false-denies a legitimately-delegating leader (up to bound 3). **Ordering contract (grounded in fact 3 — verified):** watcher registration at `daemon/tools/instance.py:604-699` (`_register_child_completion_watcher`, own `WriteGuardSession` at `:655`, `FollowUp(kind="child_complete")` at `:683-696`, `await _bus.watch(...)` at `:697-699`) runs **POST-COMMIT in a SEPARATE transaction**, sequenced inside the dispatch tool execution: `manager.spawn_instance` (`:2266`) → `manager.enqueue_message` (`:2291`) → register (`:2303`). The `WriteGuardSession` commits before the tool result returns to the LLM, hence **before the leader's next AIMessage and the gate's `pending_children` read**. The PENDING row is therefore visible to the gate when the same cycle's would-be END is evaluated — the TOCTOU window is closed at the gate's evaluation point within the same cycle. **Explicit note: same-txn-with-spawn atomicity does NOT exist.** The reviewer's original "child_reports.py:1205-1231/:2322" same-txn precedents are WRONG — those are completion-gate deferral reads (`set status=WAITING_CHILDREN`, commit `:2325`), not watcher registration. The plan and any future code review MUST NOT claim same-txn-with-spawn atomicity anywhere. **Gate read sequence (mandatory):** the gate evaluates `messages → pending_children → queued_or_expected_wakeups` in this order. This is the documented ordering; the `messages` read in-node precedes the manager facade calls (the in-node `state["messages"]` is the LangGraph routing-fn argument, evaluated synchronously before `evaluate(...)` returns). **Residual window (accepted + documented as FR/NFR risk, NOT closed):** (a) crash between tool completion and gate read (the tool returned the watcher PENDING row, but the gate read happens in a later superstep — a crash mid-superstep leaves the leader's `attestation_denied_count` at the value the gate decided on, which is the correct value); (b) silent registration failure (the `_register_child_completion_watcher` WriteGuardSession may fail without surfacing — the gate then false-denies). Both residual windows are covered by Phase 5 task 5.7 Scenario A's R2-allow test path (Scenario A asserts the gate ALLOWS when `pending_children > 0`; if the watcher PENDING row is missing, Scenario A's setup must guarantee registration succeeded — the test verifies the registration succeeded via DB read-back BEFORE asserting the gate's allow). **Hardening direction (non-normative note, no scope creep in MVP):** future hardening may add (i) registration-failure telemetry surfacing the silent-failure window, (ii) gate-side re-check that retries the `pending_children` read on a transient exception. Neither is in MVP scope. |
| **Decision tags** | [CR-2] (TOCTOU race contract) |
| **Test notes** | The contract is verified by Phase 5 task 5.7 Scenario A's R2-allow test (DB read-back confirms the PENDING row exists before the gate evaluates). No new test in this task. |

### 2.4 — (ARCHIVED — D1=A) Pre-commit gate hookup at child_reports — DISQUALIFIED

> **DISQUALIFIED 2026-09-05 by architect council ruling on `architecture-recommendation.md` §2.** A is bypassable: the parent-cascade completer (`_update_parent_on_child_complete`, def `child_reports.py:952`, inline twin `:3325`) and `error_reporting.py:319` stamp anyway; gating one surface leaves ≥3 open. The MVP uses D1=B (in-graph pre-END interception). This task is preserved for traceability only — DO NOT IMPLEMENT.

### 2.5 — In-graph `end_candidate` interception (D1=B, C2, R1) — THE wiring

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/graph.py:2707-2734`, `:6445-6470` (wrapper composition); `daemon/graph.py:6379-6383` (live wiring for auto-language leaders) |
| **Description** | Mirror the `create_should_continue` wrapper. New function `create_attestation_should_continue(attestation_enabled: bool, window: int, tool_name: str, mode_resolver: Resolver, scope_resolver: ScopeResolver, instance_id: str, manager_handle: object, denied_count_getter: Callable) -> Callable` that wraps the original `should_continue`. (The `aget_state` and the pre-CR-1 aget_state / subtree-state parameters are REMOVED — the MVP seam is in-node `state["messages"]` per Phase 2 task 2.3, and `pending_children` / `queued_or_expected_wakeups` are sourced via the two NEW manager facade methods `manager.count_pending_children` and `manager.get_queued_or_expected_wakeups` defined in Phase 2 task 2.3 — see that task for the facade contract.) The wrapper closes over `instance_id` and `manager_handle` at graph-build time (factory-closure pattern per `daemon/graph.py:4596` precedent). At run time, the routing fn receives `state` only and calls `evaluate(instance_id, denied_count, state["messages"], mode_resolver)` (Phase 2 task 2.3). **C2 — both-branches wiring (CRITICAL)**: `create_should_continue(language_check_enabled)` returns the ORIGINAL `should_continue` UNCHANGED when `language_check_enabled=False` (verify `graph.py:2718-2721`; live wiring for auto-language leaders `graph.py:6379-6383`). A single-branch gate is STRUCTURALLY INERT. This task wires an INDEPENDENT `attestation_enabled` flag active in BOTH return paths: (a) the language_check branch wraps `create_should_continue`; (b) the no-language_check branch returns a wrapper that applies `attestation_enabled` unconditionally — these are NOT the same wrapper. The composition is **explicit**: pick ONE of two shapes — either (X) both branches return a wrapped `should_continue` that internally gates on `attestation_enabled`, or (Y) the wrapper is applied at the call site that calls `create_should_continue` and is unconditional. Shape (Y) is recommended for clarity (single wrapper, single composition call site). **R1 — deny path is in-graph nudge ONLY**: when the gate returns `denied` (canonical enum value per Phase 4 task 4.5), the wrapper injects a checkpoint-durable in-state `HumanMessage` carrying the constant text `"The work is not yet finished — check current progress and continue."` (using the `additional_kwargs` marker pattern from `daemon/graph.py:2666-2685`), increments the counter via the ledger repository (Phase 3 task 3.3), and routes back to `agent`. **NO `manager.enqueue_message` is called on deny** (C1b / R1 / C5 fork ruling). The durable recovery injector is RELOCATED to Phase 6 (post-soak backstop). The instance remains RUNNING throughout — no revive, no terminal transition. **C3 — fail-open**: `evaluate()` itself is wrapped in `try/except Exception` at this wiring site; any exception ⇒ `allowed` (preserve original `should_continue` semantics; canonical enum per Phase 4 task 4.5) + `event=leader_completion_gate_error` log. Order of composition at `:6445-6470`: (a) language_check runs first (cheapest), (b) attestation gate runs second. The graph-build-time `agent_id == "leader"` check (D3) ensures non-leader graphs never reach this wrapper. |
| **Decision tags** | [D1=B] (RESOLVED), [R1] (in-graph nudge is THE deny path), [C1b] (no enqueue on deny), [C2] (both-branches wiring + independent `attestation_enabled`), [C3] (fail-open wrapper), [R2] (gate inputs), [CR-1] (in-node seam; manager facade) |
| **Test notes** | **Parameterized activation test (Phase 2 EXIT CRITERION — C2)**: `tests/integration/test_attestation_c2_both_branches.py` instantiates the leader graph with `language_check_enabled ∈ {True, False}` (parameterized), runs a synthetic would-be-END with missing attestation, asserts the gate is invoked (counter increments OR deny-nudge lands in messages) for BOTH branches. Mocks the NEW `manager.count_pending_children` and `manager.get_queued_or_expected_wakeups` facade methods to return `(0, 0)` so R2 evaluates as a denial. Additional tests: (i) `attestation_enabled=False` → gate NEVER invoked (counter unchanged, no nudge) regardless of inputs; (ii) `mode="dry"` → counter unchanged, nudge not injected, `event=leader_completion_gate` log with `decision=dry_log`; (iii) C3 fail-open: inject scanner exception → gate returns `allowed` (canonical enum per Phase 4 task 4.5), error log emitted; (iv) **integration E2E coverage for the in-graph deny nudge path is Phase 5 task 5.5**, not task 2.5 — task 2.5 only wires and unit-tests the wrapper. **No tests for in-graph nudge + enqueue combination** — that pattern is forbidden (C1b). |

### 2.6 — (RELOCATED — D1=C sweep) Async post-completion sweep

> **RELOCATED 2026-09-05 to Phase 6 (fast-follow, post-soak).** C is valuable as a backstop covering the OS-2 cascade class (leader completes without a leader turn — last child completes → cascade stamps parent). It cannot ship in MVP because (a) it is post-commit TOCTOU vs revive (b) it requires durable `manager.enqueue_message` which C5 constrains as the out-of-graph path. Phase 6 owns this work. See [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md). DO NOT IMPLEMENT IN PHASE 2.

### 2.7 — (ARCHIVED — D1=D) Tool-as-trigger (state flag) — DISQUALIFIED

> **DISQUALIFIED 2026-09-05 by architect council ruling on `architecture-recommendation.md` §2.** D pre-rejection was confirmed unanimously: a sticky checkpoint flag survives revive → a revived instance ENDs on a stale pre-revive attestation — *the very bug class this feature targets*; window scanning is self-correcting, the flag is not. Preserved for traceability only — DO NOT IMPLEMENT.

### 2.8 — (ARCHIVED — D1=E) Observer-path gate at `_finalize_job` Step 2 — DISQUALIFIED

> **DISQUALIFIED 2026-09-05 by architect council ruling on `architecture-recommendation.md` §2.** E is a double footgun: (a) `:1698` is `if db_result.gate_deferred: return` — NO re-arm; post-commit re-arm `:1572-1577` is conditioned on `not gate_deferred` → defer-starvation strands the job `active`; (b) Step 2 `:3740-3752` is a bare ORM terminal write, no WHERE guard → stomps revived RUNNING. Preserved for traceability only — DO NOT IMPLEMENT.

### 2.9 — (MOOT — D1 choice) Architect's D1 selection

> **MOOT 2026-09-05 — D1=B is RESOLVED at plan level.** Tasks 2.4–2.8 are archived as alternatives; only task 2.5 is implemented in MVP. Phase 6 owns task 2.6 (D1=C sweep backstop). See [`plan-overview.md` §Architecture Summary](./plan-overview.md#architecture-summary-post-reconciliation--authoritative).

---

## Coupling

- **Tight with:** Phase 1 (tool name resolution for scanner; tool name in contract); Phase 3 (gate reads/writes counter via the ledger repository; gate→reset path on allow); Phase 4 (mode + window + bound feed gate inputs); Phase 5 (C2 parameterized activation test).
- **Loose with:** Phase 6 (gate→recovery seam; recovery injector uses the same nudge text).
- **Independent of:** none — the gate is the central nervous system of the MVP feature.

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | **C2 — Single-branch gate is structurally inert** (`create_should_continue(language_check_enabled=False)` returns the ORIGINAL `should_continue` UNCHANGED — verify `graph.py:2718-2721`; live wiring for auto-language leaders `graph.py:6379-6383`). Piggybacking on `language_check` wiring silently disables the gate for most instances. | High | Phase 2 task 2.5 wires an INDEPENDENT `attestation_enabled` flag active in BOTH return paths of `create_should_continue(language_check_enabled)`; explicit composition choice documented (shape X or Y); **parameterized activation test over `language_check_enabled ∈ {True, False}` is a Phase-2 EXIT CRITERION**. |
| 2 | **C3 — Gate failure crashes every leader mission**: any unhandled exception in scanner/gate on the routing path errors the leader. | High | Fail-OPEN wrapper spec: `try/except Exception` around `evaluate()` ⇒ `allow` (preserve original `should_continue` semantics) + structured error log (`event=leader_completion_gate_error`). Integration test: inject scanner exception, assert allow + error log. |
| 3 | **Compaction folds attestation tool_call before scanner reads** (D10(b) unresolved) | High | Phase 1 task 1.7 precondition test; D10(b1) `aget_state` pre-compaction fallback if test reveals gap. |
| 4 | **R2 access path complexity** — `manager.count_pending_children` + `manager.get_queued_or_expected_wakeups` are two NEW facade methods (per Phase 2 task 2.3 — CR-1); if not implemented cleanly, the gate may read stale state. | Medium | Manager-facade method spec in Phase 2 task 2.3 (CR-1); integration test asserts `pending_children` and `queued_or_expected_wakeups` come from the live repository state (not cached). |
| 5 | **In-graph nudge is silently dropped** if checkpoint-durable pattern isn't applied | High (deny never persists; leader hallucination recurs) | Mirror `language_check` precedent `graph.py:2666-2685` (`additional_kwargs` marker pattern); integration test asserts the nudge is in `state.values['messages']` post-deny; verify the nudge survives restart. |
| 6 | **Scanner text-only false negative** (leader says "attesting" without calling) | Low (correct behavior — scanner returns False; gate denies per R2; nudge lands) | Document the conservative behavior; AC-2.3 verifies. |
| 7 | **Window N too small** (N=3 misses attestation if leader takes an extra turn) | Medium | N is configurable (D4); integration test with N=1 + N=10 characterizes behavior. |
| 8 | **Window N too large** (N=10 over-accepts stale attestations) | Low | Conservative scanner; default N=3 recommended; integration test with N=10 + stale attestation asserts over-acceptance is documented. |
| 9 | **D10(c) report-injection interleaving** (injected message confuses scanner) | Low | Injected messages are not AIMessages by default; scanner skips them; integration test asserts. |

---

## Rollback Story

This phase is reversible — D1=B is fully reversible:

1. **Wrapper composition rollback:** remove `create_attestation_should_continue` from `daemon/graph.py:6445-6470` (and `:6379-6383` for auto-language leaders). Revert to the un-wrapped `create_should_continue`. The attestation tool remains usable but the gate is no longer invoked. The deny-nudge seam disappears.
2. **Ledger column rollback:** drop `attestation_denied_count` + `completion_gate_escalated` (Phase 3 task 3.2 migration reverse). Gate's allow/deny paths no longer reference the counter.
3. **Resolver rollback:** remove `daemon/services/attestation_resolver.py` (Phase 4 task 4.1). Gate's mode/window/bound inputs revert to hardcoded constants (`mode="enforce"`, `window=3`, `bound=3`).
4. **Nudge-text removal:** remove the constant `RECOVERY_TEXT` from the gate's nudge path. The deny path becomes an empty inject (no-op continuation).
5. **Manager facade rollback:** remove `manager.count_pending_children` and `manager.get_queued_or_expected_wakeups` (Phase 2 task 2.3 — CR-1). Gate's R2 inputs revert to `(0, 0)` defaults — equivalent to the original R2 condition with no children/wakeups pending (which makes R2 equivalent to the old `(not attested)` condition; over-acceptance risk increases, but the gate is fully reversible).

**Restart-read:** all changes require daemon restart. No live flip. The scanner + decision pure functions remain in the codebase (pure, no side effects, no harm).

---

## Exit Criterion

This phase is done when:

- [ ] `tests/unit/test_attestation_scanner.py` passes (all window-semantics cases)
- [ ] `tests/unit/test_attestation_gate.py` passes (all decision cases — R2 inputs, mode tri-state, C2 `attestation_enabled` flag, fail-open wrapper semantics)
- [ ] `tests/integration/test_attestation_c2_both_branches.py` passes — **parameterized over `language_check_enabled ∈ {True, False}`** (Phase 2 EXIT CRITERION per C2)
- [ ] `tests/integration/test_attestation_d1b_intercept.py` (Phase 5 file) passes — in-graph deny nudge path implemented; NO `manager.enqueue_message` on deny
- [ ] `tests/integration/test_attestation_fail_open.py` passes — scanner exception → `allowed` + error log
- [ ] `manager.count_pending_children` + `manager.get_queued_or_expected_wakeups` facade methods exist (Phase 2 task 2.3 — CR-1); the underlying `bus.count_pending_for_target_sync` and the new `get_queued_or_expected_wakeups` helper exist; O8 unit assertion (no `checkpoint_ns` in gate config shape) passes
- [ ] The Phase 2 → Phase 3 call signature is stable and documented (gate reads/writes `attestation_denied_count`)
- [ ] The Phase 2 → Phase 4 config dependency is wired (scanner reads `resolver.window`; gate reads `resolver.mode`, `resolver.window`, `resolver.deny_bound`, `attestation_enabled`, `scope_applicable`)
- [ ] NFR-1 verified: gate decision overhead ≤ P95 20 ms (integration test timing)
- [ ] NFR-14 verified: gate decision is a pure function (unit test + import-time inspection)
- [ ] **No `manager.enqueue_message` reference exists in any deny path code** — grep guard verified

The phase is the precondition for Phase 3 (ledger repository methods needed for counter read/write) and Phase 4 (config feeds the gate).