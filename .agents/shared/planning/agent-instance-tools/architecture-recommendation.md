# Architecture Recommendation: agent-instance-tools

Date: 2026-08-26T21:35Z
Architect instance: this controller; analysis workers: `architect-worker-structural` (structural-design, 6ed0872c), `architect-worker-resilience` (resilience-design, 8ffabe63), `architect-worker-dataflow` (data-flow-design, 1221b4d6).
Plan base: `feature/agent-instance-tools` @ 6ca9541c (worktree verified on-branch during analysis).
Mode: Standard Design (multi-dimensional parallel fan-out — structural + resilience + data-flow; not approach-competitive).

---

## 1. Executive Summary

The plan's core mechanisms are **sound**: `set_injection` reuse for agent-tool sends is structurally correct (single FIFO writer, single drain site, pairing guard upstream of the HumanMessage append), the revive-lift for all four terminal states is safe, and `parent_id`-lineage subtree scoping is the right authorization model. **6 of 7 open decisions resolve to the plan's recommended defaults** — but two defaults ship broken as written: **O1's guidance text references a `resume_instance` tool that does not exist**, and **O6's defer recommendation is overridden** (summary mode should ship in v1). Phase 2 as written **fails at runtime**: `manager.graph.aget_state(thread_config)` is not a real API (canonical read is `await manager.get_messages(iid)`), and the role-filter schema uses non-canonical role names. The plan also cites `_INJECTION_ELIGIBLE_STATUSES` in the wrong module — the set is already forked in two places and Phase 1 would mint a third copy unless hoisted. **10 blocking corrections + 8 refinements are required before implementation locks** (§7).

---

## 2. Decision Verdicts (O1–O7)

| ID | Question | Verdict | vs. plan default |
|----|----------|---------|------------------|
| O1 | PAUSED-target policy | **Reject + rewritten guidance** (no state mutation) | ✅ same option, **text must change** |
| O2 | Stranding-race exposure | **Accept parity + document**, escalation trigger defined | ✅ confirmed |
| O3 | RUNNING + queue idle | **Always inject when eligible** | ✅ confirmed |
| O4 | WAITING_CHILDREN | **Inject (user-API parity)** | ✅ confirmed |
| O5 | Tool name | **Keep `subtree_messages`** | ✅ confirmed |
| O6 | Summary mode | **Include in v1 — metadata-only** | 🔄 **OVERRIDES defer** |
| O7 | Deterministic placeholder ids | **Defer — safe by construction** | ✅ confirmed, now proven |

### O1 — PAUSED: reject, with corrected guidance text 🔴

**Verdict: Option 1 (reject), but the planned text is unusable.** `resume_instance` does not exist as an agent tool — grep across `daemon/tools` returns zero hits; only Manager methods `pause_instance_cascade` (manager.py:7954) / `resume_instance_cascade` (manager.py:7994) exist. An agent following the planned text calls a nonexistent tool → retry-loop/burn hazard.

- **Complexity:** reject = one status check + text (lowest). Auto-resume = lifting the router's ~120-line PAUSED cascade (messages.py:211-329, incl. resume-handle fallback) into agent authority. Queue = new parked-inbox state interacting with the 1h TTL sweep (manager.py:2340) — no precedent.
- **Risk:** auto-resume is an **authority inversion** — pause-first-then-quiesce is an operator/lifecycle flow (watchover, upgrades deliberately pause instances); pause paths call `clear_injection` (instance_lifecycle.py:2501) precisely because injections are meant to be dropped, not banked; the deliberate PAUSED exemption at instance_messaging.py:1513-1517 exists for this reason. Queue risks TTL loss + invisible backlog.
- **Maintainability:** reject keeps resume single-owner (HTTP/operator). Auto-resume forks resume semantics across two authority models.
- **Future evolution:** reject leaves room for an explicit `resume_instance` tool under its own auth/audit decision; auto-resume forecloses clean semantics; queue prematurely duplicates O2's DB-backed-inbox design space.

**Replacement text:** `"Instance '{id}' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."`

Note the deliberate asymmetry vs. the user API: `POST /instances/{id}/messages` **auto-resumes** PAUSED targets (messages.py:211-329) as a load-bearing frontend UX contract (C4). The agent tool must NOT inherit that branch — human authority resumes; agent sends wait.

### O2 — Stranding race: accept parity, with a measured escalation trigger

**Verdict: Option 1.** The verified loss profile is *narrower than the plan assumed*:

| Event | Fate of FIFO entry | Evidence |
|-------|--------------------|----------|
| Normal operation | Delivered in ms (next `agent_node` drain) | graph.py:2871-2911 |
| Target transitions to terminal | **SURVIVES — FIFO is not cleared on terminal transitions; delivers on revive** | verified: only pause/clear_all/TTL/drain touch `_pending_injections` |
| Pause | **Lost immediately** — `clear_injection(node_id)` at instance_lifecycle.py:2501, no requeue | W3 |
| `clear_all` | Discarded | instance_lifecycle.py:3383-3384 |
| Daemon crash | **Total loss** — RAM-only dict (manager.py:630) | W7 |

- **Complexity:** LOW (zero schema/code). **Risk:** identical to user API. **Maintainability:** HIGH (no new paths). **Future evolution:** DB-backed store (mirroring the `report_injections` PENDING/INJECTED/TASK_DELIVERED pattern) is a clean Phase 1d follow-up — note it would inherit dead-parent cleanup duties analogous to the `report_injections` deletion in `DeadLetterTurn`.
- **Escalation trigger:** if post-launch metrics show >2% of agent-tool sends landing in the pause/clear_all/crash window, schedule Phase 1d.

### O3 — RUNNING + queue idle: always inject

**Verdict: Option 1.** The counter-argument is weak once verified: `instances.status` stays RUNNING until finalization, so an injection near turn-end targets the completing turn's last LLM step — exactly what the user API does (it trusts `agent_node` as the canonical delivery point). Dropping the queue-busy guard for the injection branch (D11) is correct: status is the source of truth; queue stats are advisory. **The guard must STAY for the enqueue branch** (it is what serializes COMPLETED/FAILED revives against in-flight child reports today).

### O4 — WAITING_CHILDREN: inject (parity)

**Verdict: Option 1.** Mechanism verified: a WAITING_CHILDREN instance has ended its turn; the injection **sits in the FIFO until the next dispatch** (typically a child report waking the instance via the dependency bus) — it does not corrupt mid-turn state. Ordering within the wake-up turn: user/agent FIFO drain (graph.py:2871) runs **before** report injection (graph.py:3021), so injected messages land before child reports. This is a *useful* semantic (the agent can incorporate new instructions before processing the report) but must be documented (see W5).

### O5 — Tool name: keep `subtree_messages`

Matches the job toolset's `[scope]_[noun]` pattern (`job_messages`/`job_tree`/`job_progress`/`job_inject`, job_queue.py:1411/1532/1646/1757); unambiguous against `job_tree` (different prefix domain). Low stakes — rename is cheap pre-release.

### O6 — Summary mode: INCLUDE in v1, metadata-only 🔄

**Verdict: Option 2 — this overrides the plan's defer.** Three findings force the flip:

1. **Compaction destroys pre-compaction content anyway** (daemon/compaction.py:1036-1070 replaces messages with `RemoveMessage` sentinels + a `SystemMessage` summary). "Full content" mode is already lossy — a metadata mode loses little additional information while cutting ~80% of output.
2. The ~8000-char ceiling forces heavy truncation at 20-instance scale regardless; metadata mode makes cost *predictable* instead of truncation-shaped.
3. Implementation cost is small: `summary=True` → keep `instance_id`/`agent_id`/`role`/`created_at`/`tool_call_names`, content → first 80 chars.

### O7 — Deterministic placeholder ids: defer — now safe *by construction*

**Verdict: Option 1, upgraded from "unlikely" to "cannot occur."** The drain is **single-pass per `agent_node` entry**: `injection_slot.get` returns the full FIFO batch (graph.py:2872), the guard runs **once** on the batch (:2892-2894), and drain #1's synthesized ToolMessages become the new persisted tail (C2 return :3386-3397) — so a second drain sees a resolved tail and the guard's O(1) happy-path check skips (:315). Re-encountering the same poisoned `AIMessage(tool_calls)` would require the drain to UNDO its synthesis — impossible. Multiple sources (user API + agent tool) in the same FIFO batch share the single guard pass with `existing_tool_call_ids` dedupe (graph.py:341-344, 361-362). Defer without residual risk; no Phase 1d scheduling needed unless the drain becomes multi-pass someday.

---

## 3. Architecture Analysis — Phase 1 Mechanisms

### 3.1 `set_injection` reuse: sound, precedented, with three enumerable gaps

**Sound.** The FIFO (`Manager._pending_injections`, manager.py:630) has exactly one public append API — `set_injection(iid, content)` (manager.py:2342-2370, sync) — one drain site (`agent_node`, graph.py:2871-2911), and the pairing guard runs **before** the injected HumanMessage is appended. D1/D3 hold: no new writer, no new guard site.

**Precedented.** `job_inject` (job_queue.py:1757-1816) **already calls `manager.set_injection` from the agent tool layer** (:1801), gated RUNNING/WAITING_CHILDREN (:1787-1790). Phase 1 is not opening a new surface class — it adds a *better-gated* second entry (team-membership check vs. job_inject's project-scoped `_check_job_access`). The plan should cite this precedent and converge with it (shared status-gate helper), while **not** imitating job_inject's private reach-in (`manager._instance_repository.get(...)`, job_queue.py:1783 — coupling smell; use `manager.get_instance_info(iid).get("status")`).

**Three gaps vs. the user-API router path** (messages.py:140-394), each with a disposition:

| Skipped check | Disposition |
|---------------|-------------|
| S4 empty-content validation (messages.py:181-188) | **Add it** — a blank message injected into a live turn wastes an LLM turn. Trim-check in the tool before routing. 🔴 |
| `injection_pending` SSE emit (messages.py:351-358) | **Accept parity with job_inject** (also skips it); consumption-side `user_message`/`injection_consumed` events still fire (graph.py:2934-2983). Document as accepted. 🟢 |
| `source`/origin stamping | **Cannot do today** — `set_injection(iid, content)` has no source param; entries carry zero provenance (E3). The known origin defect (instance_messaging.py:1337-1353, drifted from plan's :1310-1319) is enqueue-path-only and irrelevant to injection; injection's problem is *anonymity*, not forgery. **Mandate INFO logging (caller/target/content-len) at the tool call site now**; schedule `set_injection(..., source=None)` → `entry["source"]` → drain stamps `additional_kwargs["source"]` as an explicit follow-up (touches graph.py — amend the out-of-scope list to name it rather than leaving provenance silently absent). 🟡 |

### 3.2 Boundary: Manager is the correct facade

The FIFO is Manager-owned state; `set_injection` is its public append API; the service layer never touches the FIFO (`instance_messaging` owns DB enqueue/revive semantics, not the RAM inbox — routing through it would be a false seam). Cross-layer callers already exist (router messages.py:348; tool job_queue.py:1801). The interface is stable (sync, additive-only entry schema, direct test coverage in `tests/test_injection_slot.py` / `tests/test_injection_cleanup.py`); adding an optional `source` kwarg later is non-breaking. The plan's prohibition on touching `_pending_injections` from agent code keeps the boundary clean — graph-drain changes stay behind the `get`/`clear` indirection.

### 3.3 Shared vs divergent code path: converge at the primitive, diverge at the policy seam

**Share:** the FIFO writer (`set_injection`), the pairing guard (single site), and the eligibility set (one hoisted constant — see 🔴 C2 below).

**Diverge (deliberately):**

| Dimension | User API (router) | Agent tool |
|-----------|-------------------|------------|
| Auth | loopback transport trust, no team check | `_check_team_membership` (instance.py:418 → `daemon/tools/_auth.py`) — **stricter** |
| Result surface | 202 + JSON | ToolMessage text |
| PAUSED | auto-resume (frontend contract C4) | reject (O1) |
| JobItem | public entry point mints mirror | internal path — `enqueue_message` only, JAFP-correct (instance.py:1726-1731 already stamps `source=f"internal_agent:{iid}"`) |

**Seam placement:** the planned `_route_send_message` helper in the tool layer is the right shape. Do **not** move routing into a service for v1 — the router's PAUSED branch is a load-bearing frontend contract the agent tool must not inherit, and a service-level merge would couple them.

---

## 4. Concurrency Race Map

Drain semantics (verified): **batch drain, single guard pass** — `injection_slot.get` returns the entire FIFO (graph.py:2872, InjectionSlot.get at :171-181), all entries become HumanMessages (:2875-2882), guard runs once (:2892-2894), atomicity holds (no await between get :2872 and clear :2901).

| # | Window | Outcome | Severity | Action |
|---|--------|---------|----------|--------|
| W1 | Two sources (user + agent) in same drain cycle | One guard pass; `existing_tool_call_ids` dedupe prevents double synthesis | 🟢 | none — invariant preserved |
| W2 | Injection lands mid-tool-call / mid-LLM-stream | Guard synthesizes placeholder ToolMessages if tail is dangling `AIMessage(tc)`; else direct append; C2 persists | 🟢 | covered by existing 16-case suite + new agent-path cases |
| W3 | RUNNING→PAUSED after `set_injection`, before drain | **Message lost immediately** (`clear_injection`, instance_lifecycle.py:2501) — not TTL-delayed | 🟡 | accept per O2; **test required**; document in result text |
| W4 | RUNNING→terminal with FIFO populated | **Benign** — FIFO survives terminal; delivers on revive | 🟢 | **test required** (locks the behavior in) |
| W5 | Revive (enqueue) racing a concurrent inject | Injection drains **before** the earlier enqueued message → sender ordering surprise across two senders | 🟡 | document: "senders must not assume order between injection and enqueue" |
| W6 | PAUSED→resume racing injection | No race — O1 reject closes the pre-resume path; post-resume sends see RUNNING | 🟢 | none |
| W7 | Daemon crash with FIFO populated | Total RAM loss | 🟡 | accept per O2 (user-API parity); escalation trigger defined |
| W8 | Revive while old graph task winding down | Two graph-task entries, last-writer-wins on `_graph_tasks[iid]` | 🟢 | **pre-existing** for user API; agent tool adds volume, not surface |
| W9 | Queue-busy guard dropped for injection branch | HumanMessage may land mid-turn of an existing task | 🟢 | D11-accepted parity; guard STAYS for enqueue branch |
| W10 | Revive × reconciler suppression guard | Old task's terminal write suppressed (status flipped to RUNNING) — correct per f5e4b79a design | 🟡 | pre-existing, narrow; accept |
| W11 | Revive idempotency × new PROCESS_MESSAGE task | New task drains FIFO correctly; old task already resolved | 🟢 | none |

**No new serialization or ordering mechanism is required.** The existing single-drain-site + status-at-routing (D11) design absorbs every window; the required work is *documentation and tests*, not new guards.

---

## 5. Phase 2 Read-Model Recommendation

**Read model: `await manager.get_messages(iid)` per subtree instance — NOT checkpoints via `manager.graph.aget_state`.**

The plan's proposed API (phase2-plan.md line 16) **does not exist** — the tool would raise `AttributeError` at runtime. The canonical read is `manager.get_messages(iid)`, the exact pattern of `GET /instances/{id}/messages` (daemon/routers/instances.py:1422-1489) and `job_messages` (job_queue.py:1470); thread config is built inside `get_instance_messages` (persistence.py:309). Comparison:

| Axis | A: `manager.get_messages` (saver-based) | B: graph `aget_state` |
|------|------------------------------------------|------------------------|
| Freshness | post-write visible | tie |
| Completeness | includes synthetic-message handling of the shared service | bypasses it |
| Cost | direct saver read; benefits from the just-landed read-flip perf work (33-114×) | expensive lazy graph restore |
| Coupling | tool → Manager facade (consistent with Phase 1) | tool → graph internals |
| Blast radius | none if checkpoint schema evolves | coupled to graph assembly |

**Convention composition:**

1. **Synthetic messages — skip ALL** (new decision **D12**): filter out `is_synthetic=True` entries AND real `role=="system"` messages when target ≠ caller. Synthetic markers live as `is_synthetic=True` dict keys and `message_id` prefixes `synthetic-system-`/`synthetic-context-` (persistence.py:437, 669) — NOT in `additional_kwargs`. Without D12, 20 descendants × full system prompts = token blowup **and** persona leakage to parents; the only real system-role messages post-compaction are the compaction summaries.
2. **Compaction — accept post-compaction-only reads, document offset instability.** Compaction *destroys* pre-compaction messages (`RemoveMessage` sentinels + `SystemMessage` summary, compaction.py:1036-1070, written at graph.py:3256). They are gone, not hidden — so any pagination offset is unstable across compaction events. Accept + document; optionally return a per-instance `compacted_at` hint later.
3. **Checkpoint adapter:** both paths normalize through `CheckpointerAdapter` (`.raw_saver`) — no extra version-skew handling needed for v1.
4. **`message_metadata`** (perf-work write side) is not a queryable read model today (repo empty) — irrelevant for v1.

**Pagination (firm):** GLOBAL offset/limit over the merged collection (matching `job_messages`, job_queue.py:1447-1503) — not per-instance offsets. Add `cap_first_N_per_instance` param (default 0 = off) for breadth-first sampling.

**Filters (firm):** AND semantics; role values MUST be canonical post-serialization names — `"user" | "assistant" | "tool" | "system"` (daemon/utils.py:96) — the plan's `"human"|"ai"|"tool"` schema (phase2-plan.md line 34) would fail every filter call. `status` filters the instance (N× `get(iid)` via gather is acceptable for v1; no bulk fetch exists — repository.py:288). `target_instance_id=None` → caller's own subtree (no root-walk — simpler and correct). `child_instance_id` + `target_instance_id` together is an error unless equal.

**Lineage correction:** `get_tree_ids_permanent` (instance/repository.py:428-492) is a **Python-side BFS with depth cap 256** — not a recursive CTE as the plan states. Behavior matches intent; description must be corrected (D4 + plan line 59).

---

## 6. Risks Summary

- 🔴 **R1 — Eligibility-set fork**: `_INJECTION_ELIGIBLE_STATUSES` lives at `routers/messages.py:39-42` (NOT manager.py as the plan cites) and is hardcoded AGAIN in `job_queue.py:1787-1790`. Shipping Phase 1 as written mints a third divergent copy.
- 🔴 **R2 — False O1 guidance text** referencing nonexistent `resume_instance` (E7) — agent-loop hazard.
- 🔴 **R3 — Phase 2 read API nonexistent** (`manager.graph.aget_state`) — runtime `AttributeError`.
- 🔴 **R4 — Role-name schema wrong** (`human|ai` vs canonical `user|assistant`) — every role filter fails.
- 🟡 **R5 — Injection provenance absent** (anonymity, not forgery): INFO-log now; `source` param as named follow-up.
- 🟡 **R6 — Missing empty-content check** on the injection branch.
- 🟡 **R7 — Pause-loss (W3) and crash-loss (W7)** documented parity; W5 ordering surprise documented.
- 🟡 **R8 — job_inject's private reach-in** (`manager._instance_repository`) must not be imitated.
- 🟢 R9 — SSE `injection_pending` skip = accepted job_inject parity. R10 — O7 safe by construction. R11 — W8/W10 pre-existing exposures.

---

## 7. Required Plan Changes (apply before implementation lock)

**Blocking — plan is wrong or broken without these:**

1. **Fix Phase 2 read API** (phase2-plan.md line 16, Tasks 3): read via `await manager.get_messages(iid)` per subtree instance; thread config built inside `get_instance_messages` (persistence.py:309). Update tests to mock `manager.get_messages` (not `manager.graph.aget_state`); fuzz test asserts exactly one call per instance.
2. **Hoist `_INJECTION_ELIGIBLE_STATUSES`** to one shared constant (Manager-level attr or `daemon/constants.py`); update `messages.py:39-42` and `job_queue.py:1787-1790` to consume it. Correct phase1-plan Task 2 and decisions O3/O4 references (currently cite `daemon/manager.py` — wrong module).
3. **Rewrite O1 rejection text** (phase1-plan Task 5): use the replacement text in §2-O1; remove the `resume_instance` reference. Keep Option-1 verdict with the four-axis rationale.
4. **Fix role-name schema** (phase2-plan.md line 34): `"user" | "assistant" | "tool" | "system"` (daemon/utils.py:96). Test plan already uses "assistant" — pin the rest to match.
5. **O6 → include in v1**: `summary=True` metadata-only mode (instance_id/agent_id/role/created_at/tool_call_names + first-80-char preview); fold into Phase 2 Tasks 3-4; document ~80% budget reduction in `_full_doc_`.
6. **Add D12 to decisions.md**: skip ALL `is_synthetic=True` messages AND `role=="system"` messages when target ≠ caller; add a test asserting synthetic messages never appear for descendant targets.

**Refinements — correctness/consistency:**

7. **Add empty-content trim-check** in `send_message` before routing (mirror S4, messages.py:181-188).
8. **Mandate provenance INFO logging** (caller/target/content-len) at the tool call site; add `set_injection(..., source=None)` + `additional_kwargs["source"]` drain stamping as an explicitly named follow-up task (amend phase1 out-of-scope list — graph.py touch is intentional follow-up, not silent omission).
9. **Converge with `job_inject`**: cite it as precedent in phase1-plan; share the status-gate helper; FORBID `manager._instance_repository` reach-ins (use `manager.get_instance_info(iid).get("status")`); note `manager.get_instance_status()` does not exist (phase1 Task 2 "verify" resolved).
10. **Document ordering semantics in docstring + result text**: "delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue" (W5); injections land before child reports in the same wake-up turn (O4).
11. **Global pagination + `cap_first_N_per_instance`** (replace per-instance offset/limit wording, phase2-plan lines 35-36); document compaction offset-instability.
12. **Instance-status filtering**: pin N× `instance_repository.get(iid)` via `asyncio.gather` for v1 (no bulk method exists — repository.py:288); optionally add `get_many_by_ids()` later.
13. **`target_instance_id=None` → caller's own subtree** (no root-walk) — phase2-plan line 65.
14. **Correct perf estimate** (phase2-plan lines 73-74): Path A avoids graph restore and rides the read-flip perf work — the ~50-100ms/instance estimate is conservative; re-measure at impl. Also correct "sequential with semaphore" → `Semaphore(5)` gather is acceptable (job_messages precedent, job_queue.py:1447-1503) if desired; sequential remains fine for v1.
15. **Correct "recursive CTE" → "BFS via `parent_id`, depth-capped 256"** (D4, plan line 59; instance/repository.py:428-492).
16. **Line-drift fixes across plan docs**: reconciler suppression guard at `task/repository.py:816/828/841` (not :705); `_check_team_membership` at `instance.py:418` (not :747-847); origin defect at `instance_messaging.py:1337-1353` (not :1310-1319); TTL sweep is `_cleanup_instance_state` manager.py:3359-3396 (not :3323-3393).

**New tests required:**

17. Phase 1: pause-between-inject-and-drain → FIFO cleared + result text mentions stranding risk (W3); RUNNING→COMPLETED with populated FIFO → revive → drain delivers contents in order (W4); concurrent-source single-pass guard test (user-API inject + agent-tool inject between drain cycles).
18. Phase 2: synthetic-message exclusion (D12); global-pagination behavior; compaction-instability smoke (compact a target, re-query, offsets differ — documented, not asserted as bug).

---

## 8. Deferred Follow-ups (explicit, not silent)

| Item | Trigger | Notes |
|------|---------|-------|
| DB-backed injection store (O2 escalation) | >2% agent-send loss rate post-launch | `report_injections` pattern; inherits dead-parent cleanup duties |
| `set_injection(..., source=)` provenance param | next graph.py-touching PR | additive, non-breaking; closes injection anonymity |
| `resume_instance` agent tool | only if a real use case emerges | own auth/audit decision — never smuggled into send_message |
| Deterministic placeholder ids (O7) | only if drain becomes multi-pass | currently impossible by construction |
| `compacted_at` hint per instance | if offset instability confuses agents | needs `get_instance_messages` plumbing |

## 9. Confidence

**High.** All file:line citations verified against the on-branch worktree (three independent workers, cross-consistent). The recommendation would flip only if: (a) the drain site becomes multi-pass (invalidates O7-by-construction), or (b) `manager.get_messages` semantics change re: synthetic messages (invalidates D12 filtering approach).
