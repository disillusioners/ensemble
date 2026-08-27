# Phase 1: `send_message` Tool Upgrade

## Objective

Upgrade the agent-facing `send_message` tool at `daemon/tools/instance.py:1576-1780` so that:
- (1a) Sending to RUNNING / WAITING_CHILDREN targets routes through injection (NOT enqueue) — reusing `Manager.set_injection` so the existing tool-pairing guard in `agent_node` continues to defend against AIMessage(tool_calls) without matching ToolMessage.
- (1b) Sending to all four terminal states (COMPLETED, TERMINATED, ERROR, FAILED) revives and dispatches, with explicit prior-status text in the tool result.
- (1c) PAUSED-target behavior follows the architect decision (default: reject + guidance — see `decisions.md` O1).
- Docstring (`:1609-1626`) and `_full_doc_` (`:1756-1780`) updated to match.

## Files to Touch

| File | Change | Lines (research-verified, verify drift at impl) |
|------|--------|-----------------------------------------------|
| `daemon/tools/instance.py` | Modify status gate + queue-busy guard routing; extract `_route_send_message` helper; add empty-content trim-check; mandate INFO logging on dispatch; update docstring + `_full_doc` | `:1576-1780` (send_message), `:1695-1709` (status gate), `:1711-1718` (queue-busy guard), `:1609-1626` (docstring), `:1756-1780` (`_full_doc_`), `:2240-2250` (closure list — no structural change) |
| `daemon/constants.py` | **NEW (LOCKED choice — no Manager-attr alternative)** — create the named constant `INJECTION_ELIGIBLE_STATUSES = {"RUNNING", "WAITING_CHILDREN"}` in `daemon/constants.py` (module already exists). This is the single source of truth (§7 #2 LOCKED per delta-fix #4). | append at end of `daemon/constants.py`; verify exact insertion point at impl |
| `daemon/routers/messages.py` | **Named-constant consumer.** Replace the existing LOCAL definition at `:39-42` with `from daemon.constants import INJECTION_ELIGIBLE_STATUSES` (no third fork). | `:39-42` |
| `daemon/tools/job_queue.py` | **INLINE-TUPLE consumer.** The hardcoded tuple at `:1787-1790` (`job_inject` status gate) is an INLINE TUPLE — NOT a named constant. Replace that inline tuple with `from daemon.constants import INJECTION_ELIGIBLE_STATUSES` (the same import the router uses). No third fork. | `:1787-1790` (verify the inline-tuple form at impl — e.g. `if status not in (...)`) |
| `tests/unit/tools/test_instance_tools.py` | Add unit tests for routing, revive, PAUSED, trim-check, INFO logging, UNKNOWN instance-id, IDLE/WAITING/QUEUED enqueue-parity; extend pairing regression | full file |
| `tests/unit/graph/test_injection_tool_pairing.py` | Add cases exercising the agent-tool-triggered injection source path (parametrize over `source="internal_agent:{caller}"`); add concurrent-source single-pass dedupe test | full file |

No changes to: `daemon/services/instance_messaging.py`, `daemon/graph.py`, `daemon/manager.py`. JAFP compliance preserved: no new JobItem allocation. (Note: Phase 2 adds ONE additive facade method `Manager.get_tree_ids_permanent()` to `daemon/manager.py` for the leader-approved seam — that is Phase 2's touch, not Phase 1's.)

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Audit current status gate and queue-busy guard** | none | Read confirms: gate rejects only TERMINATED/ERROR (string values); queue-busy guard blocks ALL targets with pending/processing work via `manager.get_queue_stats`. Note: `_INJECTION_ELIGIBLE_STATUSES` is currently forked at `routers/messages.py:39-42` AND `job_queue.py:1787-1790` — Task 2b fixes this. |
| 2 | **Extract routing helper** `_route_send_message(manager, target_instance_id, content, source) -> tuple[routed_via: str, prior_status: str] \| None` | Task 1 | Helper unit-testable in isolation. Uses `manager.get_instance_info(target_instance_id).get("status")` (line-drift correction per §7 #9 + #16 — `manager.get_instance_status()` does NOT exist). The status-gate helper MUST be shared with `job_inject` (which gates at `job_queue.py:1787-1790`); do NOT copy-paste. **UNKNOWN / not-found instance_id handling (delta-fix #1):** when the caller passes an unknown or typo'd `target_instance_id`, preserve the existing `_resolve_instance_id` not-found behavior — `manager.get_instance_info(...)` raises `KeyError`. The routing helper catches `KeyError`, the tool returns a friendly error like `"Instance '<id>' not found; no message dispatched."`, and NEITHER `set_injection` NOR `enqueue_message` is called. This preserves today's behavior (a friendly error on a bad id) without introducing a new code path. The `None` return from the helper signals "not routable" to the call site. |
| 2b | **Hoist `_INJECTION_ELIGIBLE_STATUSES`** to one shared named constant in `daemon/constants.py:INJECTION_ELIGIBLE_STATUSES` (§7 #2, LOCKED choice per delta-fix #4 — no Manager-attr alternative). The current code has two forks: (i) `routers/messages.py:39-42` is a **named constant** local to that module, (ii) `daemon/tools/job_queue.py:1787-1790` is an **INLINE TUPLE** (e.g. `if status not in ("RUNNING", "WAITING_CHILDREN"):` — NOT a named constant). The hoist creates the named constant in `daemon/constants.py`; `routers/messages.py:39-42` REPLACES its local definition with `from daemon.constants import INJECTION_ELIGIBLE_STATUSES`; `daemon/tools/job_queue.py:1787-1790` REPLACES its inline tuple with the SAME import. NO third fork. NO Manager-attr option. | Task 2 | `grep -n "INJECTION_ELIGIBLE_STATUSES" daemon/` shows exactly ONE definition (in `daemon/constants.py`) and TWO `from daemon.constants import` consumers (router + job tool); the agent tool consumes the same constant. Test k asserts BOTH consumers import from the hoisted location (grep-import assertions). |
| 2c | **Add empty-content trim-check** (§7 #7) — mirror S4 at `daemon/routers/messages.py:181-188`. If `content.strip()` is empty, return early with a tool result like `"Message content is empty; nothing to send."`. Do NOT enqueue / inject. A blank message injected into a live turn wastes an LLM turn. | Task 2 | Unit test: `content=""`, `content="   "`, `content="\n\t\n"` all rejected before routing; no `set_injection` / `enqueue_message` called. |
| 3 | **Implement injection routing with exhaustive enum coverage** (delta-fix #2) — the `InstanceStatus` enum has 10 states; routing must be EXHAUSTIVE (no silent fall-through). Mapping:<br>**• `prior_status ∈ INJECTION_ELIGIBLE_STATUSES` (RUNNING, WAITING_CHILDREN):** injection branch — call `manager.set_injection(instance_id, content)`. Drop the queue-busy guard for this branch (status is the source of truth per D11).<br>**• `prior_status ∈ TERMINAL_STATUSES` (COMPLETED, TERMINATED, ERROR, FAILED):** enqueue-with-revive branch (Task 4). The queue-busy guard STAYS here — it serializes terminal-revives against in-flight child reports.<br>**• `prior_status ∈ ENQUEUE_ONLY_STATUSES` (IDLE, WAITING, QUEUED, and any other non-eligible non-terminal state):** enqueue parity branch — call `manager.enqueue_message(...)` (same as today's behavior); the queue-busy guard STAYS (per D11). NO third routing path.<br>**• `prior_status == PAUSED`:** PAUSED-reject branch (Task 5).<br>**• Unknown `target_instance_id`:** not-found branch (Task 2 / delta-fix #1). | Task 2 + 2b + 2c | Routing helper returns `"injection"` for RUNNING/WAITING_CHILDREN, `"enqueue-revive"` for terminal states, `"enqueue"` for IDLE/WAITING/QUEUED, `"paused"` for PAUSED, `None` for not-found. **All enum states are covered — no silent fall-through.** Tool result text reads `"Message injected into {prior_status} target"` for the injection branch. |
| 3b | **Mandate provenance INFO logging** (§7 #8) — at the tool call site, log `INFO` with structured fields: `event="agent_send_message"`, `caller_iid`, `target_iid`, `routed_via` (injection/enqueue), `prior_status`, `content_len`, `source` (= `"internal_agent:{caller_iid}"`). This closes injection **anonymity** (not forgery — the origin defect at `instance_messaging.py:1337-1353` is enqueue-path-only and irrelevant to injection). The proper `set_injection(..., source=None)` → `entry["source"]` → drain stamps `additional_kwargs["source"]` work is an **explicit follow-up** (see "Out-of-scope follow-ups" below) — it touches `graph.py` and is intentionally scheduled, not silently omitted. | Task 3 | Every successful send emits exactly one INFO log line with the listed fields; log test asserts shape. |
| 4 | **Lift terminal-state rejection** at `:1695-1709` — remove the explicit ERROR/TERMINATED branch. Let all four terminal states flow into the existing `_prepare_enqueued_message` revive path (`instance_messaging.py:1522-1540`). Prepend tool result text with `"Instance was {prior_status} — revived and message dispatched."` | Task 2 | Parametric unit test over {COMPLETED, TERMINATED, ERROR, FAILED} passes; revive log at `:1535-1540` shows the transition |
| 5 | **Implement PAUSED handling** per architect verdict (§2-O1, RESOLVED — see `decisions.md` RESOLVED section R-O1). Return clear text: **`"Instance '{target_id}' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."`** Do NOT enqueue, do NOT inject, do NOT auto-resume. No reference to `resume_instance` (the tool does NOT exist — only `Manager.pause_instance_cascade` / `Manager.resume_instance_cascade` exist, and they are operator/lifecycle methods, not agent tools). **Cross-reference (leader decision b):** this is the PRE-SEND state check (target is already PAUSED at the moment of dispatch). It composes with the W3 POST-SEND stranding note in test f — both texts must appear in the implementation; an implementer cannot ship one without the other. | Task 1 | PAUSED branch returns the rejection text verbatim; no DB / state mutation observed; no follow-up tools named. |
| 6 | **Update docstring + `_full_doc_`** (`:1609-1626`, `:1756-1780`): document injection, revive-from-terminal, PAUSED rejection (with the corrected text from Task 5). Mention JAFP compliance (no JobItem). Document **W5 ordering semantics** in BOTH docstring AND result text: *"Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn."* Show 4 example outputs: trim-check reject, injection, revive, PAUSED reject. | Tasks 3-5 + 3b | `tool_help("instance.send_message")` shows updated docs; docstring + `_full_doc_` content matches verbatim |
| 7 | **Tests** — see test plan below (incl. new W3, W4, concurrent-source cases per §7 #17) | Tasks 3-6 | All new cases pass; existing 16 pairing tests remain green; CI `pytest tests/unit/graph/test_injection_tool_pairing.py` → 16/16 (or 16+N where N = new cases) |

### Out-of-scope follow-ups (explicit, not silent — §7 #8)

The following items are **deliberately deferred** to subsequent PRs and must NOT appear as silent omissions:

1. **`set_injection(..., source=None)` + drain stamps `additional_kwargs["source"]`** — closes injection anonymity. Touches `daemon/graph.py` (the drain site); additive, non-breaking. The current PR adds INFO logging at the call site as the v1 mitigation; this follow-up upgrades it to first-class provenance. (See `decisions.md` Deferred Follow-ups.)
2. **DB-backed injection store** (O2 escalation) — only if post-launch metrics show >2% agent-send loss in the pause/clear_all/crash window.
3. **Deterministic placeholder ids (O7)** — currently impossible by construction (single drain pass, `existing_tool_call_ids` dedupe at `graph.py:341-344, 361-362`).
4. **`resume_instance` agent tool** — only if a real use case emerges. Never smuggled into `send_message`.

## Approach Detail — Why Tool-Pairing Safety is Preserved Without a New Guard Site (Task 3)

The injection FIFO `Manager._pending_injections` (RAM-only) is drained at exactly one site: `agent_node` in `daemon/graph.py:2871-2911`. The drain sequence is:

1. `injection_slot.get()` at `:2872` (atomic read, single delivery point).
2. Convert to `HumanMessage(..., additional_kwargs={"injected_message": True})` at `:2877-2882`.
3. `_ensure_tool_result_pairing(messages, instance_short)` at `:2892-2894` — walks tail + bounded backward scan (`_TOOL_PAIRING_MAX_TRAVERSAL=8` blocks).
4. Append the new HumanMessage (and any synthesized ToolMessages) at `:2896`.
5. `injection_slot.clear(instance_id)` at `:2901` — no await between get/clear = atomicity invariant.
6. Persist via C2 return at `:3386-3397`.

The guard runs BEFORE the new HumanMessage is appended to `full_messages`. This means:

- An in-flight `AIMessage(tool_calls=[...])` with no `ToolMessage` response will trigger synthesis of a placeholder ToolMessage immediately after the AIMessage.
- The synthesized ToolMessage accumulates in `pairing_synthesized_msgs` and is persisted via the same C2 return — checkpoint is healed permanently.
- The new HumanMessage then lands AFTER all tool-pairing has been verified.

**Conclusion:** Routing agent-tool sends through `set_injection` requires NO new guard site at the agent-tool layer. The existing user-injection guard at `:2892-2894` covers all FIFO sources.

**Caveat (stranding race exposure — see `decisions.md` O2 RESOLVED + §8 Deferred Follow-ups):** verified loss profile per architect §4 race map:
- **Pause between inject and drain (W3):** message lost immediately via `clear_injection(node_id)` at `instance_lifecycle.py:2501`. NOT TTL-delayed.
- **Target RUNNING→terminal with FIFO populated (W4):** benign — FIFO is NOT cleared on terminal transitions; delivers on revive. Test required to lock this in.
- **Daemon crash with FIFO populated (W7):** total RAM loss.
- **Pause (`clear_all`) at `instance_lifecycle.py:3383-3384`:** discarded.
- **TTL sweep:** `_cleanup_instance_state` at `manager.py:3359-3396` (line-drift correction per §7 #16 — NOT `:3323-3393`).

Identical exposure to the user-API injection. v1 verdict: **accept parity + document**; INFO logging at the call site (Task 3b) provides observability; DB-backed store (mirroring `report_injections` PENDING/INJECTED/TASK_DELIVERED) is an explicit Phase 1d follow-up **escalation trigger: >2% agent-send loss rate in the pause/clear_all/crash window**.

**Additional safety verification at impl time:** confirm `manager.set_injection` is the only FIFO writer used (NOT a parallel queue) — this should be true by design per blueprint but is worth a `grep -n "_pending_injections" daemon/` audit before shipping. The audit MUST show: exactly one writer (`manager.set_injection`) + one drain (`agent_node` injection_slot.get/clear) + clear() call sites only (no direct manipulation).

## Test Plan

### 0. IMPLEMENTER CHECKLIST — verify before extending the pairing regression suite (delta-fix SHOULD-FIX)

Before adding any new case to `tests/unit/graph/test_injection_tool_pairing.py`:

- **Verify the "16 existing cases" claim.** Run `pytest tests/unit/graph/test_injection_tool_pairing.py --collect-only -q | wc -l` (or equivalent) to count the EXISTING case count on the worktree. The count may have drifted since this plan was drafted. If it differs from 16, use the actual count as the baseline in tests a / a-bis / f / g and in the success criteria (e.g. "All {N} existing cases pass + new cases...").
- **EXTEND, never replace.** New cases append to the existing parametrized cases; do NOT delete, rename, or re-number existing cases.
- **Confirm the `source="internal_agent:{caller}"` parametrize stamp matches the exact runtime stamp at `daemon/tools/instance.py:1729` (the enqueue-side source-stamping line, which today already emits `source=f"internal_agent:{iid}"`). If `instance.py:1729` has drifted, update the test parametrize to match. The verify is a one-line grep: `grep -n "internal_agent:" daemon/tools/instance.py`.
- **Confirm `existing_tool_call_ids` dedupe anchor at `graph.py:341-344, 361-362`** still applies before relying on it in test a-bis. One-line grep.
- **Confirm `_ensure_tool_result_pairing` call site at `graph.py:2892-2894` still applies** before relying on it in test a / a-bis / f. One-line grep.

### a. Tool-pairing regression — agent-tool-triggered injection path

Extend `tests/unit/graph/test_injection_tool_pairing.py`. Pattern:

- Setup: target instance in RUNNING with mid-flight tool_call (no ToolMessage yet).
- Action: caller invokes `send_message(target, content)` (the agent-tool path, NOT the user API).
- Expected: HumanMessage injection appended; guard synthesizes placeholder ToolMessage; checkpoint healed.
- Parametrize over `source="internal_agent:{caller}"` so the test fixture proves the agent-tool path exercises the SAME delivery point and guard as the user API.
- At least 3 new cases (different tool_call shapes, different tool_call_id formats).

### a-bis. Concurrent-source single-pass guard test (NEW — §7 #17)

Two injections land in the SAME drain cycle (user-API inject + agent-tool inject between drain cycles, both sources populating the FIFO before the next `agent_node`):

- Setup: target RUNNING with one in-flight tool_call lacking its ToolMessage.
- Action: user API injects message A; agent tool injects message B before next drain.
- Expected: drain consumes both messages in one batch; `_ensure_tool_result_pairing` runs ONCE; synthesized ToolMessages dedupe via `existing_tool_call_ids` (`graph.py:341-344, 361-362`) so only ONE placeholder is produced. No double-synthesis.
- This locks the O7-by-construction guarantee into the test suite.

### b. Revive from each terminal state

Extend `tests/unit/tools/test_instance_tools.py`. Pattern:

- Parametrize over {COMPLETED, TERMINATED, ERROR, FAILED}.
- For each: pre-set `instance.status`; call `send_message`; verify `is_terminal_revival` branch fires at `instance_messaging.py:1522-1527` and status transitions to RUNNING before enqueue.
- Verify tool result text contains `"Instance was {prior_status} — revived and message dispatched."`.

### c. PAUSED branch

- Pre-set PAUSED; call `send_message`; verify rejection text is the corrected `Instance '{id}' is PAUSED. Paused instances cannot receive messages; …` string from §2-O1 (NOT the old `resume_instance` text).
- Verify NO enqueue / injection / state mutation occurred (mock asserts not called).
- Verify the result text does NOT name any follow-up tool (the original bug was referencing a nonexistent `resume_instance`).

### c-bis. Empty-content trim-check (NEW — Task 2c, §7 #7)

- `content=""` → `"Message content is empty; nothing to send."`, no `set_injection` / `enqueue_message` called.
- `content="   "` → same.
- `content="\n\t\n"` → same.
- Counter-test: `content="  hello  "` → trimmed and routed normally (not rejected on whitespace alone).

### c-ter. UNKNOWN / not-found instance_id (NEW — Task 2 / delta-fix #1)

- Pass a typo'd / unknown `target_instance_id` to `send_message`.
- `manager.get_instance_info(target_instance_id)` raises `KeyError` (the existing `_resolve_instance_id` not-found behavior — preserved).
- Routing helper returns `None` (signals "not routable"); the tool returns a friendly error like `"Instance '<id>' not found; no message dispatched."`.
- **Mocks assert NEITHER `set_injection` NOR `enqueue_message` was called** — the not-found branch never reaches the routing decision.
- Counter-test: `target_instance_id=None` is REJECTED with a different error (None is not a valid instance id), distinct from the not-found text.

### d. RUNNING + queue idle (race window — see `decisions.md` O3 RESOLVED, §7 #10)

- Pre-set RUNNING with `pending_count=0, processing_count=0`.
- Verify injection path used (NOT enqueue); race documented in test docstring.
- Note: the queue-busy guard STAYS for the enqueue branch (it serializes terminal-revives against in-flight child reports).

### e. WAITING_CHILDREN + injection

- Pre-set WAITING_CHILDREN; verify injection (per `decisions.md` O4 RESOLVED, architect verdict confirms parity with user API; the FIFO sits until next dispatch typically a child report wake).
- Counter-assertion (W5 ordering): when a wake-up turn has both a child report AND an injected message, the injection lands BEFORE the report (`graph.py:2871` runs before `graph.py:3021`).

### e-bis. ENQUEUE-PARITY else-branch for IDLE / WAITING / QUEUED (NEW — Task 3 / delta-fix #2)

- Parametrize over `{IDLE, WAITING, QUEUED}` — the enqueue-eligible non-injection states that round out the `InstanceStatus` enum to a total of 10.
- For each: pre-set `instance.status`; call `send_message`; verify the routing helper returns `"enqueue"` and `manager.enqueue_message(...)` was called (NOT `set_injection`).
- Verify the queue-busy guard STAYS for this branch — it serializes terminal-revives against in-flight child reports (matches today's behavior). Counter-test: pre-set `pending_count > 0` → enqueue is REJECTED with the existing queue-busy error text.
- **Exhaustiveness assertion:** the test asserts the routing helper has no `else: pass` / silent fall-through branch. The test does this by enumerating ALL 10 enum states in a single parametrize and asserting each maps to exactly ONE of the five branches (injection / enqueue-revive / enqueue / paused / not-found). If a future enum value is added, this test will fail loudly — that's the point.
- Locks the delta-fix #2 exhaustive-enum invariant into the test suite.

### f. W3 — pause between inject and drain (NEW — §7 #17 + leader decision b verbatim text)

- Setup: target RUNNING.
- Action: agent-tool injects message → BEFORE drain, target transitions to PAUSED → `clear_injection` fires at `instance_lifecycle.py:2501`.
- Expected: FIFO cleared; message NOT delivered.
- **Verbatim W3 stranding text (leader decision b)** — the injection-path success result MUST include the following sentence (or one that is equivalent and includes the same three facts: pause-loss parity, daemon-restart loss, in-flight delivery caveat):

> **"Note: if the target is paused or the daemon restarts before delivery, an in-flight injected message may be dropped (pause-loss parity with the user messages API)."**

- The test asserts this sentence (or its verbatim equivalent per the implementation) appears in the tool result text returned to the caller. This composes with the PAUSED-reject text in Task 5 / test c — both texts MUST ship together; an implementer cannot ship one without the other.
- Locks the W3 race window in the test suite.

### g. W4 — RUNNING→terminal with populated FIFO (NEW — §7 #17)

- Setup: target RUNNING with FIFO = [injected_message_1, injected_message_2].
- Action: target transitions RUNNING → COMPLETED with FIFO still populated.
- Assert: FIFO is NOT cleared on terminal transition (verified: only pause/clear_all/TTL/drain touch `_pending_injections`).
- Then: target revived via `send_message` (forces COMPLETED → RUNNING).
- Expected: drain delivers BOTH injected messages IN ORDER on the next agent_node cycle.
- Locks the W4 benign-survival semantic into the test suite.

### h. JAFP compliance

- Source review only: `grep -n "JobItem" daemon/tools/instance.py` count before/after should be unchanged.
- No new `JobItem` allocation in send_message path.

### i. docstring / `_full_doc_` parity

- `tool_help("instance.send_message")` returns the new docstring content.
- String equality check between docstring and `_full_doc_` (after trimming).
- Verify the W5 ordering sentence appears in both: *"Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn."*

### j. INFO logging provenance (NEW — Task 3b, §7 #8)

- Use `caplog` to assert one `INFO` log line per successful send with fields: `event="agent_send_message"`, `caller_iid`, `target_iid`, `routed_via`, `prior_status`, `content_len`, `source="internal_agent:{caller_iid}"`.
- Trim-check rejects do NOT emit this log line (no successful send).
- Parametrize over injection / enqueue / paused-reject: log line emitted only for injection and enqueue.

### k. Eligibility-set constant uniqueness (NEW — Task 2b, §7 #2 + delta-fix #6)

- **`grep -n "INJECTION_ELIGIBLE_STATUSES" daemon/`** after the change: exactly ONE definition (in `daemon/constants.py`) and TWO consumers (router at `routers/messages.py:39-42` + job tool at `job_queue.py:1787-1790`); the agent tool consumes the same constant. NO Manager-attr alternative (LOCKED choice per delta-fix #4).
- **`grep-import assertion for BOTH consumers (delta-fix #6):** `grep -n "from daemon.constants import INJECTION_ELIGIBLE_STATUSES" daemon/routers/messages.py daemon/tools/job_queue.py daemon/tools/instance.py` returns THREE hits — one per consumer. The router replaces its local named constant (`:39-42`) with this import; the job tool replaces its INLINE TUPLE (`:1787-1790`) with this import. Both consumers end up with the SAME line of code; the test grep proves it.
- **Counter-test:** `grep -n "_INJECTION_ELIGIBLE_STATUSES\s*=\s*{" daemon/` returns EXACTLY ONE hit (the definition in `daemon/constants.py`); the router's local definition and the job tool's inline tuple are GONE.
- Importing the constant from `daemon.constants` in all three locations works without circular imports.

## Risks (Phase 1 specific — see plan-overview for full list)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Stranding race during pause (W3) / crash (W7) after agent injection | Medium | Medium | INFO logging at call site (Task 3b) provides observability; tool result text mentions stranding risk on PAUSED-after-inject (test f). O2 accepted parity; DB-backed store is the §8 follow-up (escalation trigger >2% loss). |
| 2 | Race window: target RUNNING at check, transitions IDLE before injection | Low | Low | Status at moment of routing is the source of truth; downstream handles transitions; queue-busy guard stays for enqueue branch. |
| 3 | **Eligibility-set fork** (`_INJECTION_ELIGIBLE_STATUSES`) if hoist fails | High | Low | Task 2b explicitly hoists; test k asserts exactly one definition + two consumers; do NOT mint a third copy. |
| 4 | **Empty content injection** wastes an LLM turn | Low | Medium | Task 2c trim-check; test c-bis covers all whitespace variants. |
| 5 | **Provenance anonymity** — injection entries carry zero source | Medium | Medium | Task 3b INFO log at call site (v1 mitigation); `set_injection(..., source=)` scheduled as named §8 follow-up. |
| 6 | **Reach-in coupling** — copying job_inject's `manager._instance_repository.get(...)` pattern | Medium | Medium | Task 2 mandates `manager.get_instance_info(iid).get("status")`; explicit FORBID note in Task 2; job_inject's private reach-in called out as coupling smell. |
| 7 | Doc drift between docstring and `_full_doc_` | Low | Medium | Update both in same edit; verify via `tool_help()`; test i asserts parity including the W5 ordering sentence. |
| 8 | `mock_registry.get_version.return_value` truthy-default bug | Low | Medium | Conftest sets it to `None` explicitly. |
| 9 | FIFO writer leak — accidentally writing to `_pending_injections` outside `manager.set_injection` | High | Low | Stage-time grep audit; only `manager.set_injection` is allowed; verify via `grep -n "_pending_injections" daemon/`. |

## Coupling

- **Tight with:** None.
- **Loose with:** Phase 2 (shares factory closure, registration plumbing, test conftest pattern). Phase 2 adds ONE additive facade method `Manager.get_tree_ids_permanent()` to `daemon/manager.py` (leader-approved seam) — that's Phase 2's touch, not Phase 1's. Phase 1 does not touch `daemon/manager.py`.
- **Independent of:** `daemon/graph.py`, `daemon/manager.py`, `daemon/services/instance_messaging.py`, `daemon/routers/messages.py` (we reuse, do not modify — except Task 2b updates the existing `_INJECTION_ELIGIBLE_STATUSES` consumer at `routers/messages.py:39-42` (local named constant → import) and `job_queue.py:1787-1790` (INLINE TUPLE → import) to import the hoisted constant from `daemon/constants.py` — one-line import swap each, behavior-preserving).
- **Convergence with `job_inject`** (§7 #9): the status-gate helper is shared (single definition); job_inject's private reach-in (`manager._instance_repository.get(...)` at `job_queue.py:1783`) is explicitly NOT imitated — we use `manager.get_instance_info(iid).get("status")`. Cite job_inject (`job_queue.py:1757-1816`) as the precedent for agent-tool injection.

## Rollback Notes

- Phase 1 changes are isolated to `daemon/tools/instance.py` + test files. `git revert` of the Phase 1 commit(s) restores prior behavior.
- No DB migration, no schema change, no config flip — rollback is pure code revert.
- If a pairing regression is detected post-deploy: immediately revert; the original guard site is unchanged, so no checkpoint corruption persists. (Synthesized ToolMessages persist via C2 return; if those were incorrectly produced, they remain in checkpoints. Verify at impl time whether a checkpoint scrub is needed on revert — research says no, since synthesis is idempotent and the next agent_node cycle will overwrite.)
- Feature flag option: wrap the new routing logic in `if settings.ENABLE_AGENT_INSTANCE_TOOLS_V1` (default ON for staging, OFF in prod until validated). Coordinate with the implementer.

## Exit Criterion

- All 4 terminal states revive (unit tests 4/4 pass).
- RUNNING + WAITING_CHILDREN inject with pairing guard respected (unit + integration).
- PAUSED branch returns the corrected `Instance '{id}' is PAUSED. …` text — NO `resume_instance` reference.
- **Both verbatim texts ship together (leader decision b):** PAUSED-reject text (Task 5) AND W3 stranding sentence (test f) both appear in the implementation; test c + test f both assert their respective text verbatim.
- Empty-content trim-check rejects `""`, `"   "`, `"\n\t\n"`; allows trimmed non-empty content (test c-bis).
- UNKNOWN / not-found `target_instance_id` returns a friendly error; NEITHER `set_injection` NOR `enqueue_message` is called (test c-ter).
- IDLE / WAITING / QUEUED route via enqueue parity with the queue-busy guard retained; routing is EXHAUSTIVE over all 10 enum states (test e-bis).
- Eligibility-set hoist verified: `grep -n "INJECTION_ELIGIBLE_STATUSES" daemon/` shows ONE definition (in `daemon/constants.py`) + TWO consumer-imports (router + job tool) (test k, delta-fix #6 grep-import assertion).
- INFO logging verified: one structured `event="agent_send_message"` line per successful send (test j).
- All 16 existing `test_injection_tool_pairing.py` cases still pass + new cases (a-bis concurrent-source, f W3 pause-between, g W4 terminal-survive) — locks the O7-by-construction guarantee.
- `tool_help("instance.send_message")` shows updated docs including the W5 ordering sentence.
- JAFP source review confirms no new JobItem allocation.
- Phase 1 changes touch `daemon/tools/instance.py`, `daemon/constants.py` (LOCKED choice — no Manager-attr alternative, per delta-fix #4), `daemon/routers/messages.py` (one-line import swap, replacing the local named constant at `:39-42`), `daemon/tools/job_queue.py` (one-line import swap, replacing the INLINE TUPLE at `:1787-1790`), and the test files listed above.
- Pairing audit grep (`grep -n "_pending_injections" daemon/`) confirms only `manager.set_injection` writes to it.
- `grep -n "_instance_repository" daemon/tools/instance.py` returns zero hits (FORBID reach-in pattern from §7 #9).

## Coupling note re: cross-phase (additional)

Phase 1's Task 2b (hoist `_INJECTION_ELIGIBLE_STATUSES`) also affects `daemon/tools/job_queue.py:1787-1790` — a file Phase 2 does NOT touch, but Phase 2 reads `job_messages` precedent. Confirm at impl time that the import swap does not break `job_inject`'s status gate.
