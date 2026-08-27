# Decisions: agent-instance-tools

This document captures (a) decisions baked into the plan with rationale, (b) architect/leader verdicts on the previously-open questions, and (c) the deferred follow-ups list. The original OPEN items O1-O7 are now RESOLVED with verdicts from `architecture-recommendation.md`. Decisions D1-D12 govern Phase 1 and Phase 2 implementation.

---

## DECIDED

The following choices are baked into `phase1-plan.md` and `phase2-plan.md`. Each is reversible with an explicit follow-up PR.

### D1 — Phase 1a routes via `set_injection` reuse (no new FIFO writer)

**Decision.** Phase 1a routes agent-tool sends to RUNNING / WAITING_CHILDREN targets via `manager.set_injection(instance_id, content)` — the same RAM-only FIFO the user-facing API uses (`daemon/routers/messages.py:348` → `Manager._pending_injections`). The agent-tool layer does NOT create a new injection site; it piggybacks on the single delivery point at `agent_node` (`daemon/graph.py:2871-2911`).

**Rationale.**
- Single chokepoint = single guard call. `_ensure_tool_result_pairing` already runs at `:2892-2894` BEFORE the HumanMessage is appended; no new guard site needed at the agent-tool layer.
- Persistence is automatic via the C2 return (`:3386-3397`).
- Pairing with synthesized ToolMessages is preserved across daemon restart (checkpoint is the source of truth).
- Verifiable at impl time via `grep -n "_pending_injections" daemon/` — only `manager.set_injection` should write.
- Single-batch drain per `agent_node` entry (`injection_slot.get` returns the entire FIFO, `graph.py:2872`; `InjectionSlot.get` at `:171-181`); atomicity holds (no await between get and clear at `:2901`).

### D2 — Phase 1b lifts the tool-layer terminal-state rejection; all four states revive

**Decision.** Remove the explicit ERROR / TERMINATED rejection at `daemon/tools/instance.py:1695-1709`. Let all four terminal states (COMPLETED, TERMINATED, ERROR, FAILED) flow into the existing `_prepare_enqueued_message` revive path (`instance_messaging.py:1522-1540`). The tool result text prepends `"Instance was {prior_status} — revived and message dispatched."`.

**Rationale.**
- The revive path already supports all four states (`:1522-1540`); the tool-layer rejection is a needless asymmetry.
- Lifting the rejection centralizes the revive logic in the service layer (single source of truth).
- Explicit prior-status text in the result lets the calling agent reason about state transitions.
- COMPLETED / FAILED were already flowing through this path; ERROR / TERMINATED were the asymmetry to fix.

### D3 — Tool-pairing safety is preserved without a new guard site

**Decision.** Phase 1a does NOT introduce a new `_ensure_tool_result_pairing` call site. We rely on the existing `:2892-2894` site.

**Rationale.**
- All FIFO sources (user API, future agent tool, future reports) funnel through the same delivery point.
- Adding a new site would risk double-synthesis or message reordering.
- Verified by extending `tests/unit/graph/test_injection_tool_pairing.py` to cover the agent-tool trigger path — the existing 16-case regression suite remains the canonical pairing test.
- `_TOOL_PAIRING_MAX_TRAVERSAL=8` blocks the backward walk — sufficient for the agent-tool case (no special tooling needed).
- **Multiple sources in same FIFO batch** share the single guard pass with `existing_tool_call_ids` dedupe at `graph.py:341-344, 361-362` — no double synthesis is possible (O7 safe by construction; see RESOLVED O7 below).

### D4 — Phase 2 subtree scoping uses `parent_id` permanent lineage (Python-side BFS, depth-capped 256)

**Decision.** `subtree_messages` enumerates the caller's subtree via `get_tree_ids_permanent(caller_instance_id)` — a **Python-side BFS over `parent_id`, depth-capped 256** (`daemon/repositories/instance/repository.py:428-492`). It is NOT a recursive CTE as the original plan stated (lineage correction per architect §5; behavior matches intent, but the description must be accurate). It does NOT use `instance_hierarchy` (transient) and does NOT use `get_cascade_tree_ids` kill-switch wrapper (deferred; permanent lineage is the source of truth).

**Rationale.**
- `parent_id` is permanent and survives terminate-to-revive (research D5).
- `instance_hierarchy` rows are transient and may not reflect current lineage.
- Per blueprint: "instances.parent_id is permanent for the child row (surviving terminate-to-revive); instance_hierarchy rows are transient, so query-time lineage survives revive while cascade cleanup drains correctly."
- **Lineage-correction note:** the original plan line 59 said "recursive CTE on `parent_id`". The implementation is a Python-side BFS — verified at `repository.py:428-492`. Update phrasing everywhere this lineage mechanism appears (`plan-overview.md` Research Insights, `phase1-plan.md` and `phase2-plan.md` references).

### D5 — Phase 2 authorization = subtree scoping (no separate ACL)

**Decision.** Subtree scoping IS the authorization. There is no separate per-instance ACL because the caller's scope is mechanically bounded by the BFS over `parent_id`.

**Rationale.**
- Agents can only query their own subtree — no foreign access path exists in the code.
- Simpler than maintaining a separate ACL table or per-instance permission check.
- Matches the JAFP principle: minimal surface area.
- Edge case (caller is a root, parent_id NULL): subtree = {self}. Querying own subtree returns own messages only.

### D6 — Phase 2 token safety modeled on `job_messages`

**Decision.** Apply the same token-safety design as `daemon/tools/job_queue.py:job_messages`:
- 200-char content snippets per message (full-content mode).
- ToolMessage redacted to `name + first 100 chars of args` (no tool output).
- 20-instance cap (`max_instances=20`).
- ~8000-char total output ceiling.
- **Global pagination** (offset/limit across the merged collection — NOT per-instance), matching `job_messages` at `job_queue.py:1447-1503`.
- **`cap_first_N_per_instance`** param (default 0 = off) for breadth-first sampling.

**Rationale.**
- Consistency with existing patterns (agents learn one model).
- Research-verified safety caps from the existing implementation.
- Predictable token cost for the calling LLM.
- Global pagination matches precedent and avoids the bookkeeping complexity of per-instance offsets.

### D7 — JAFP compliance: no new JobItem allocation

**Decision.** Phase 1 and Phase 2 do NOT create any new JobItem mirrors. The agent-tool layer continues to use `enqueue_message` (for terminal/PAUSED-rejected cases) and `set_injection` (for live cases). Per JAFP: agent-to-agent internal paths use `enqueue_message` only.

**Rationale.**
- Blueprint: "Message JobItems (job_type='message') are pure mirrors. PG trigger skips them (no job_locks needed). Only public entry points create JobItems; agent-to-agent uses enqueue_message."
- Avoiding JobItem mirrors keeps the queue topology clean and the PG trigger surface stable.
- Verifiable: `grep -n "JobItem" daemon/tools/instance.py` count before/after should be unchanged.

### D8 — Phase 2 read concurrency: sequential v1; `Semaphore(5)` gather acceptable later

**Decision.** Phase 2 reads checkpoints via `await manager.get_messages(iid)` per subtree instance — the canonical saver-based read (NOT `manager.graph.aget_state`, which does not exist). For v1: sequential reads. `asyncio.gather` with `Semaphore(5)` is acceptable if needed (precedent: `job_messages` at `job_queue.py:1447-1503`), but sequential is simpler and sufficient.

**Rationale.**
- `manager.get_messages` rides the just-landed read-flip perf work (33-114× faster reads per the LangGraph perf PR3 milestone). The ~50-100ms/instance estimate from the original plan is conservative; re-measure at implementation.
- Avoids checkpoint load storm from concurrent fanout.
- Sequential reads preserve deterministic test fixtures.

### D9 — Phase 2 narrow opt-in via `meta.json`

**Decision.** Phase 2 registers `subtree_messages` in the `"instance"` category (factory closure + decorator). meta.json opt-in is via the specific tool name `"subtree_messages"` (narrow), NOT the `"instance"` category (broad).

**Rationale.**
- Read-only access is broad in principle — but `subtree_messages` is a NEW capability, and narrow opt-in limits blast radius: only agents explicitly granted see the tool.
- Category registration is automatic via the factory closure.
- Resolution via `_check_team_membership` (lives at `daemon/tools/instance.py:418` per architect §7 #16 line-drift correction; the original plan's `:747-847` was wrong) using `registry.get_version(agent_id, version_tag)` with `registry.get_resolved(...)` fallback.

### D10 — Phase 1 docstring + `_full_doc_` updated in lockstep

**Decision.** When capability text changes in the docstring, `_full_doc_` MUST be updated in the same edit. Both are gated by a parity check (string equality after trimming) in the test suite.

**Rationale.**
- `tool_help()` reads `_full_doc_`; runtime docstrings may be read by introspective agents.
- Drift between the two is a known risk; explicit parity test prevents silent divergence.
- Both should show 4 example outputs (per §7 #10): trim-check reject, injection, revive, PAUSED reject — including the **W5 ordering sentence** *"Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn."*

### D11 — Phase 1 status-at-routing is the source of truth

**Decision.** The routing decision (inject vs enqueue) is made once, at the moment of `send_message` invocation, based on `target.status` at that instant. Subsequent status changes are handled by downstream logic (revive, FIFO drain, pause, terminate). The queue-busy guard is DROPPED for the injection branch (status is the source of truth) but STAYS for the enqueue branch (it serializes terminal-revives against in-flight child reports — verified at architect §2-O3).

**Rationale.**
- Avoids TOCTOU races between status check and dispatch.
- Existing `_prepare_enqueued_message` already handles IDLE→revive transitions.
- Simpler to reason about; race window documented in test (case d).

### D12 — Phase 2: skip ALL `is_synthetic=True` messages AND real `role=="system"` messages when `target ≠ caller`

**Decision.** When `subtree_messages` resolves a target that is NOT the caller, the result MUST filter out (a) every message with `is_synthetic=True` and (b) every real `role=="system"` message. When `target == caller`, system-role messages are KEPT (the caller's own system prompt is part of its context). The filter happens at retrieval time (NOT in the formatter), so synthetic token costs never reach the agent.

**Rationale.**
- Synthetic markers live as **dict keys `is_synthetic=True`** AND as `message_id` prefixes `synthetic-system-` / `synthetic-context-` (`daemon/persistence.py:437, 669`) — NOT in `additional_kwargs` (the original plan assumed wrong location). Filter checks both.
- The only real `role=="system"` messages post-compaction are compaction summaries (`daemon/compaction.py:1036-1070`); filtering them out prevents persona leakage and token bloat.
- Without D12, 20 descendants × full system prompts = token blowup AND persona leakage to parents.

#### D12 addendum (pre-merge security-council batch W1, INTERIM)

**Amendment (W1, INTERIM).** The D12 letter MUST ALSO exclude persisted `[SYSTEM CONTEXT: …]`-style `role="user"` context-injection messages when reading a descendant. The full fix — thread `injected_message` / `context_kind` through `daemon/utils.py:serialize_message` (line 158-170) and filter on the structured marker — is a deferred follow-up.

**Recon reason for INTERIM (NOT full fix in this batch).** `serialize_message` has 8+ call sites across `daemon/persistence.py` (lines 364, 737), `daemon/graph.py` (lines 2940, 3062), and `daemon/services/instance_messaging.py` (lines 3411, 3448, 3640, 3829). `daemon/graph.py` + `daemon/services/instance_messaging.py` are FROZEN by the Phase 2 plan (per `decisions.md` §FROZEN-files rule), so threading the structured marker through `serialize_message` is not contained in this batch. The interim filter is a literal-prefix match on the persisted `content` field (no trim / no normalize) to avoid false-positive drops of legitimate user messages that merely quote the marker mid-text.

**Implementation.** `daemon/tools/instance.py:_filter_subtree_messages` — in the `is_descendant=True` branch, AFTER the synthetic-marker check and AFTER the real-`role=="system"` check, ALSO drop `role=="user"` messages whose `content` (string) starts with the literal prefix `_SUBTREE_CONTEXT_INJECTION_PREFIX = "[SYSTEM CONTEXT:"`. The caller's OWN `[SYSTEM CONTEXT: …]`-prefixed messages are KEPT (caller sees its own injections — they are its own context). The check is retrieval-time (NOT formatter-time) to keep synthetic token costs off the agent.

**Test coverage.** Three new cases in `tests/unit/tools/test_instance_tools.py::TestD12SyntheticExclusion`:
1. descendant with `role="user"` and `content="[SYSTEM CONTEXT: Task Context]\n## Foo\nbar"` → DROPPED (no `"SYSTEM CONTEXT"` substring in the result);
2. SAME-shaped message on the caller's own instance (target == caller) → KEPT (`"Caller's own context"` substring in the result);
3. legitimate user message that contains `"[SYSTEM CONTEXT:"` MID-text (not prefix) → KEPT (the literal-prefix match does NOT trigger).

**Removal criterion.** When `serialize_message` threads `injected_message` / `context_kind` through and the descendant filter can use the structured marker, drop the literal-prefix check in `_filter_subtree_messages` and the prefix constant. Ticket the work as a follow-up.

### D13 — Hoisted `_INJECTION_ELIGIBLE_STATUSES` constant in `daemon/constants.py` (Phase 1 Task 2b, LOCKED — no Manager-attr alternative)

**Decision.** `_INJECTION_ELIGIBLE_STATUSES` is hoisted to ONE named constant in `daemon/constants.py:INJECTION_ELIGIBLE_STATUSES = {"RUNNING", "WAITING_CHILDREN"}` (LOCKED choice per delta-fix #4 — the module already exists; the Manager-attr alternative is DROPPED everywhere). `daemon/routers/messages.py:39-42` (the existing **named-constant** fork) REPLACES its local definition with `from daemon.constants import INJECTION_ELIGIBLE_STATUSES`. `daemon/tools/job_queue.py:1787-1790` (the existing **INLINE TUPLE** fork — e.g. `if status not in ("RUNNING", "WAITING_CHILDREN"):`) REPLACES the inline tuple with the SAME import. The agent tool (Phase 1) consumes the SAME constant. NO third fork.

**Rationale.**
- Originally cited as living in `daemon/manager.py` (wrong module per architect §7 #16 line-drift correction) — actually forked in `routers/messages.py:39-42` (named constant) AND `job_queue.py:1787-1790` (INLINE TUPLE — not a named constant; delta-fix #6 fork-site precision).
- Hoisting eliminates the third-copy hazard introduced by Phase 1.
- Fork-site precision matters: the two consumer sites are NOT both named constants — one is local to its module, the other is an inline tuple. The hoist handles each differently (delete local definition / delete inline tuple), but both end up importing from the same place.
- LOCKED home: `daemon/constants.py`. No "or" — no Manager-attr option.

### D14 — `manager.get_instance_info(iid).get("status")` for status reads; FORBID `manager._instance_repository` reach-ins

**Decision.** Status reads from the agent tool layer MUST use `manager.get_instance_info(target_instance_id).get("status")`. The `manager.get_instance_status()` API does NOT exist (verified — was listed as "verify at impl" in the original Task 2; this resolves the verify). `manager._instance_repository.get(...)` reach-ins (the pattern job_inject uses at `job_queue.py:1783`) are explicitly FORBIDDEN in the agent tool.

**Rationale.**
- Public facade only; preserves the Manager-as-facade boundary.
- Reach-ins couple the tool to repository internals and bypass any caching/observability the facade provides.
- `manager.get_instance_info` is the verified-working accessor; `manager.get_instance_status()` was a hallucinated API name.

---

## RESOLVED — formerly OPEN, now architect-verdicted

The original OPEN items O1-O7 have been resolved by the architect (`architecture-recommendation.md` §2) with one leader override on O6. Each carries the verdict, the rationale summary, and a pointer to the affected phase plan section.

### R-O1 — PAUSED behavior: **reject + corrected guidance text** ✅ (architect + plan default)

**Verdict.** Option 1 (reject with guidance), with the corrected text per architect §2-O1 (NOT the original `resume_instance` reference — that tool does not exist; only `Manager.pause_instance_cascade` / `Manager.resume_instance_cascade` exist as operator/lifecycle methods).

**Replacement text** (verbatim, must appear in tool result):
> `"Instance '{target_id}' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."`

**Rationale (four-axis).**
- *Complexity:* reject = one status check + text (lowest). Auto-resume = lifting the router's ~120-line PAUSED cascade (`messages.py:211-329`) into agent authority. Queue = new parked-inbox state interacting with the 1h TTL sweep — no precedent.
- *Risk:* auto-resume is an **authority inversion** — pause-first-then-quiesce is an operator/lifecycle flow (watchover, upgrades deliberately pause instances); pause paths call `clear_injection` (`instance_lifecycle.py:2501`) precisely because injections are meant to be dropped, not banked; the deliberate PAUSED exemption at `instance_messaging.py:1513-1517` exists for this reason.
- *Maintainability:* reject keeps resume single-owner (HTTP/operator). Auto-resume forks resume semantics across two authority models.
- *Future evolution:* reject leaves room for an explicit `resume_instance` tool under its own auth/audit decision; auto-resume forecloses clean semantics.

**Deliberate asymmetry:** the user API auto-resumes PAUSED targets (`POST /instances/{id}/messages` at `messages.py:211-329`, frontend contract C4). The agent tool MUST NOT inherit that branch — human authority resumes; agent sends wait.

**Composability with R-O2 (leader decision b):** this R-O1 text is the **PRE-SEND state check** (target is already PAUSED at the moment of dispatch). It composes with R-O2's **POST-SEND delivery caveat** — the W3 stranding sentence — which appears in the injection-path success result. Both texts MUST ship together in the implementation; an implementer cannot ship one without the other (test c + test f assert both). Verbatim R-O2 stranding text:

> **"Note: if the target is paused or the daemon restarts before delivery, an in-flight injected message may be dropped (pause-loss parity with the user messages API)."**

**Affects:** `phase1-plan.md` Task 5 + Tests c (R-O1); `phase1-plan.md` Test f (R-O2 verbatim); composability asserted in `phase1-plan.md` Exit Criterion.

### R-O2 — Stranding-race exposure: **accept parity + measured escalation trigger** ✅ (architect + plan default)

**Verdict.** Option 1 (accept parity with user-API), with an escalation trigger defined.

**Loss profile** (verified per architect §2-O2):
- Normal operation → delivered in ms (next `agent_node` drain, `graph.py:2871-2911`).
- Target RUNNING→terminal with FIFO populated → **SURVIVES** (FIFO is NOT cleared on terminal transitions; delivers on revive). Verified: only pause/clear_all/TTL/drain touch `_pending_injections`. (W4 benign — locked in by new test.)
- Pause (`clear_injection(node_id)` at `instance_lifecycle.py:2501`) → **lost immediately**, not TTL-delayed. (W3 — locked in by new test.)
- `clear_all` at `instance_lifecycle.py:3383-3384` → discarded.
- Daemon crash → **total loss** (RAM-only dict, `manager.py:630`). (W7.)

**Escalation trigger:** if post-launch metrics show **>2% of agent-tool sends landing in the pause/clear_all/crash window**, schedule Phase 1d (DB-backed injection store mirroring `report_injections` PENDING/INJECTED/TASK_DELIVERED).

**v1 mitigations:**
- INFO logging at the call site (Task 3b, `event="agent_send_message"` with caller/target/content-len) provides observability.
- Tool result text on the PAUSED-after-inject path mentions stranding risk (test f).

**Verbatim W3 stranding text (leader decision b)** — the injection-path success result MUST include the following sentence (or a verbatim equivalent covering the same three facts: pause-loss parity, daemon-restart loss, in-flight delivery caveat):

> **"Note: if the target is paused or the daemon restarts before delivery, an in-flight injected message may be dropped (pause-loss parity with the user messages API)."**

The test (test f) asserts this sentence (or its verbatim equivalent per the implementation) appears in the tool result text returned to the caller. **This composes with R-O1's PAUSED-reject text** — both texts MUST ship together; an implementer cannot ship one without the other. The R-O1 text is the PRE-SEND state check; the R-O2 stranding note is the POST-SEND delivery caveat.

**Affects:** `phase1-plan.md` Caveat + Risks #1 + Tests f (W3 — verbatim), g (W4); `phase1-plan.md` Exit Criterion (both verbatim texts ship together).

### R-O3 — RUNNING + queue idle boundary: **always inject when eligible** ✅ (architect + plan default)

**Verdict.** Option 1. Queue-busy guard is DROPPED for the injection branch (status is the source of truth per D11); the guard STAYS for the enqueue branch (it serializes terminal-revives against in-flight child reports).

**Rationale.** `instances.status` stays RUNNING until finalization, so an injection near turn-end targets the completing turn's last LLM step — exactly what the user API does. The counter-argument (injection near a near-completed turn) is weak once verified.

**Affects:** `phase1-plan.md` Task 3.

### R-O4 — WAITING_CHILDREN semantics: **inject (user-API parity)** ✅ (architect + plan default)

**Verdict.** Option 1. WAITING_CHILDREN instance has ended its turn; the injection sits in the FIFO until the next dispatch (typically a child report waking the instance via the dependency bus) — does NOT corrupt mid-turn state. Ordering within the wake-up turn: user/agent FIFO drain (`graph.py:2871`) runs BEFORE report injection (`graph.py:3021`), so injected messages land before child reports. This is a USEFUL semantic but must be documented (W5).

**Affects:** `phase1-plan.md` Task 6 (W5 ordering sentence in docstring + `_full_doc_`) + Test e.

### R-O5 — Phase 2 tool name: **keep `subtree_messages`** ✅ (architect + plan default)

**Verdict.** Matches the job toolset's `[scope]_[noun]` pattern (`job_messages` / `job_tree` / `job_progress` / `job_inject` at `job_queue.py:1411/1532/1646/1757`); unambiguous against `job_tree` (different prefix domain). Low stakes — rename is cheap pre-release.

**Affects:** `phase2-plan.md` Tool Naming Proposal.

### R-O6 — Summary mode: **INCLUDE in v1 — metadata-only** 🔄 (LEADER OVERRIDE — architect had recommended defer; leader flipped to include)

**Verdict.** Option 2 — include in v1 with metadata-only payload. **This overrides the original plan's defer recommendation.**

**Rationale (three findings forcing the flip):**
1. **Compaction destroys pre-compaction content anyway** (`daemon/compaction.py:1036-1070` replaces messages with `RemoveMessage` sentinels + a `SystemMessage` summary). "Full content" mode is already lossy — metadata mode loses little additional information while cutting ~80% of output.
2. The ~8000-char ceiling forces heavy truncation at 20-instance scale regardless; metadata mode makes cost *predictable* instead of truncation-shaped.
3. Implementation cost is small: `summary=True` → keep `instance_id` / `agent_id` / `role` / `created_at` / `tool_call_names`, content → first 80 chars.

**Implementation.** Phase 2 Tasks 3-4 folded with summary mode as part of the formatter (NOT a separate task). Test plan section f includes summary mode assertions. `_full_doc_` documents the ~80% budget reduction.

**Affects:** `phase2-plan.md` Task 4 + Parameter Schema + Risks #3 + Exit Criterion.

### R-O7 — Deterministic placeholder-id hardening: **defer — safe by construction** ✅ (architect + plan default; upgraded from "unlikely" to "cannot occur")

**Verdict.** Option 1. The drain is **single-pass per `agent_node` entry**: `injection_slot.get` returns the full FIFO batch (`graph.py:2872`), the guard runs ONCE on the batch (`:2892-2894`), and drain #1's synthesized ToolMessages become the new persisted tail (C2 return `:3386-3397`) — so a second drain sees a resolved tail and the guard's O(1) happy-path check skips (`:315`). Re-encountering the same poisoned `AIMessage(tool_calls)` would require the drain to UNDO its synthesis — impossible. Multiple sources (user API + agent tool) in the same FIFO batch share the single guard pass with `existing_tool_call_ids` dedupe at `graph.py:341-344, 361-362`.

**Lock-in test:** Phase 1 test a-bis (concurrent-source single-pass guard test) locks this guarantee into the suite.

**Flip condition** (architect §9): only if the drain site becomes multi-pass. Defer without residual risk; no Phase 1d scheduling needed.

**Affects:** `phase1-plan.md` Test a-bis.

### R-LEADER — `source=` provenance param: **DEFERRED to §8 Follow-ups** (leader decision)

**Verdict.** The `set_injection(..., source=None)` → `entry["source"]` → drain stamps `additional_kwargs["source"]` work is deferred to the §8 follow-ups list (it touches `daemon/graph.py` and is intentionally scheduled, not silently omitted). The current PR adds INFO logging at the call site as the v1 mitigation (`phase1-plan.md` Task 3b).

**Affects:** `phase1-plan.md` Out-of-scope follow-ups list + `decisions.md` Deferred Follow-ups #2.

---

## Deferred Follow-ups (per architect §8 — explicit, not silent)

These items are deliberately deferred to subsequent PRs and must NOT appear as silent omissions. Trigger conditions are explicit.

| # | Item | Trigger | Notes |
|---|------|---------|-------|
| 1 | **DB-backed injection store** (O2 escalation) | post-launch metrics show >2% agent-send loss rate in the pause/clear_all/crash window | Mirrors `report_injections` PENDING/INJECTED/TASK_DELIVERED pattern; inherits dead-parent cleanup duties analogous to the `report_injections` deletion in `DeadLetterTurn`. Would become Phase 1d. |
| 2 | **`set_injection(..., source=None)` provenance param** (R-LEADER) | next graph.py-touching PR | Additive, non-breaking; closes injection anonymity. Drain stamps `additional_kwargs["source"]`. v1 mitigation is INFO logging at the call site. |
| 3 | **`resume_instance` agent tool** | only if a real use case emerges | Own auth/audit decision — never smuggled into `send_message`. |
| 4 | **Deterministic placeholder ids** (O7) | only if drain becomes multi-pass | Currently impossible by construction; locked in by test a-bis. |
| 5 | **`compacted_at` hint per instance for `subtree_messages`** | if offset instability confuses agents | Needs `get_instance_messages` plumbing; relevant to D6 global pagination compaction-unstable behavior. |
| 6 | **`get_many_by_ids()` bulk fetch on `instance_repository`** | if status filtering becomes a hot path | Currently N× `manager.get_instance_info(iid)` (each call returns `{"status": ...}` or raises `KeyError`) via `asyncio.gather` is acceptable (no bulk method exists at `repository.py:288`); the tool layer MUST go through the facade — it MUST NOT call `instance_repository.get(iid)` directly (consistent with D14 / success criterion #21); see `phase2-plan.md` §5. |

---

## Summary Table

| ID | Status | Topic | Verdict / value | Architect / leader locks |
|----|--------|-------|-----------------|--------------------------|
| D1 | decided | Phase 1a FIFO reuse | `set_injection`, no new writer | — |
| D2 | decided | Phase 1b terminal revive | lift rejection, all 4 states revive | — |
| D3 | decided | Tool-pairing safety | no new guard site | — |
| D4 | decided | Phase 2 subtree lineage | `parent_id` BFS, depth-capped 256 | — |
| D5 | decided | Phase 2 authorization | subtree scoping = auth | — |
| D6 | decided | Phase 2 token safety | `job_messages` parity + global pagination | — |
| D7 | decided | JAFP compliance | no new JobItem | — |
| D8 | decided | Phase 2 read concurrency | sequential v1; Semaphore(5) gather acceptable | — |
| D9 | decided | Phase 2 meta.json opt-in | narrow per-agent | — |
| D10 | decided | docstring ↔ `_full_doc_` parity | lockstep + W5 ordering included | — |
| D11 | decided | status-at-routing source of truth | one-shot decision; queue-busy guard stays for enqueue | — |
| D12 | decided | synthetic-message exclusion | filter at retrieval when `target ≠ caller` | — |
| D13 | decided | eligibility-set hoist | `daemon/constants.py` (LOCKED — no Manager-attr alternative) | — |
| D14 | decided | status read API | `manager.get_instance_info(iid).get("status")`; FORBID `_instance_repository` | — |
| R-O1 | RESOLVED | PAUSED behavior | reject + corrected text (no `resume_instance`) | architect §2-O1 |
| R-O2 | RESOLVED | stranding-race exposure | accept parity + >2% escalation trigger | architect §2-O2 |
| R-O3 | RESOLVED | RUNNING + idle boundary | always inject when eligible | architect §2-O3 |
| R-O4 | RESOLVED | WAITING_CHILDREN semantics | inject (parity); W5 ordering documented | architect §2-O4 |
| R-O5 | RESOLVED | Phase 2 tool name | `subtree_messages` | architect §2-O5 |
| R-O6 | RESOLVED | summary mode | **INCLUDE in v1** (leader override) | leader + architect §2-O6 |
| R-O7 | RESOLVED | deterministic placeholder ids | defer — safe by construction | architect §2-O7 |
| R-LEADER | RESOLVED | `source=` provenance param | DEFERRED to §8 follow-ups | leader |
| — | DEFERRED | DB-backed injection store | trigger: >2% loss | §8 #1 |
| — | DEFERRED | `set_injection(..., source=None)` | trigger: next graph.py-touching PR | §8 #2 |
| — | DEFERRED | `resume_instance` tool | trigger: real use case | §8 #3 |
| — | DEFERRED | deterministic placeholder ids | trigger: drain becomes multi-pass | §8 #4 |
| — | DEFERRED | `compacted_at` hint | trigger: offset instability confuses agents | §8 #5 |
| — | DEFERRED | `get_many_by_ids()` bulk fetch | trigger: status filter hot path | §8 #6 |
