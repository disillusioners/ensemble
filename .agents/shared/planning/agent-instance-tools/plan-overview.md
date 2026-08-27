# Plan Overview: agent-instance-tools

Date: 2026-08-26T21:04:44Z
Author: planner[v2] via plan-creation worker
Status: Architect-enriched, implementation-ready (all §7 changes applied per `architecture-recommendation.md`; O1-O7 RESOLVED; D12-D14 added; §8 follow-ups explicit)
Base branch: `feature/agent-instance-tools` @ 6ca9541c (cut from `latest`)

## Objective

Enable agent instances to safely message live siblings (via injection that respects tool-call↔tool-message pairing guards) and to revive from any terminal state, AND to read messages across their own subtree via a new subtree-scoped read-only tool — without regressing the recently-fixed pairing invariants (commits 84fd8018 / 58260f35).

## Scope

### In Scope

- **Phase 1 — Upgrade the existing `send_message` agent tool** (`daemon/tools/instance.py:1576-1780`):
  - **1a.** Allow sending to RUNNING (and WAITING_CHILDREN, per `decisions.md` R-O4 RESOLVED) targets via injection — route through the existing `Manager.set_injection` → `_pending_injections` FIFO so the single delivery point in `daemon/graph.py:2871-2911` continues to enforce `_ensure_tool_result_pairing`.
  - **1b.** Allow sending to all four terminal states (ERROR, TERMINATED, COMPLETED, FAILED) with revive — lift the tool-layer rejection at `instance.py:1695-1709` and let the existing `_prepare_enqueued_message` revive path (`instance_messaging.py:1522-1540`) handle the transition. Tool result text explicitly names the prior status.
  - **1c.** PAUSED-target behavior per `decisions.md` R-O1 RESOLVED: reject + corrected guidance text (`"Instance '{id}' is PAUSED. Paused instances cannot receive messages; delivery is rejected to respect the pause (operator/lifecycle intent). Wait for it to be resumed via the API/UI, or proceed with other work."`). No reference to nonexistent `resume_instance` tool; no auto-resume; deliberate asymmetry with user-API auto-resume (`messages.py:211-329` frontend contract C4).
  - **Hoist** `_INJECTION_ELIGIBLE_STATUSES` to a single named constant in `daemon/constants.py:INJECTION_ELIGIBLE_STATUSES` (LOCKED — no Manager-attr option; the module already exists). Update both `daemon/routers/messages.py:39-42` (named constant — replace local definition with import) AND `daemon/tools/job_queue.py:1787-1790` (INLINE TUPLE — replace with the same import) to consume — NO third fork.
  - **Empty-content trim-check** mirroring S4 at `messages.py:181-188` — blank messages rejected before routing.
  - **Provenance INFO logging** at tool call site (`event="agent_send_message"` with caller/target/content-len) — closes injection anonymity for v1. Proper `set_injection(..., source=None)` is a §8 follow-up.
  - **Document W5 ordering** in docstring + `_full_doc_` + result text: "Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue. Injections land before child reports in the same wake-up turn."
  - Update docstring (`:1609-1626`) and `_full_doc_` (`:1756-1780`).
- **Phase 2 — NEW read-only tool `subtree_messages`** (name per `decisions.md` R-O5 RESOLVED):
  - Strict subtree scoping via `manager.get_tree_ids_permanent(caller_instance_id)` — the leader-approved facade method on `daemon/manager.py` that delegates to a **Python-side BFS over `parent_id`, depth-capped 256** (`daemon/repositories/instance/repository.py:428-492`). NOT a recursive CTE as originally stated (lineage correction per architect §5). The tool layer MUST NOT reach into `manager._instance_repository` directly (consistent with D14).
  - Filters: child instance id, status, role/content type — role values MUST use **canonical post-serialization names** `"user" | "assistant" | "tool" | "system"` (`daemon/utils.py:96`); pre-serialization class names (`"human"`, `"ai"`) are rejected.
  - **Synthetic-message exclusion (D12):** when `target ≠ caller`, filter out all `is_synthetic=True` messages AND all real `role=="system"` messages (markers live as dict keys AND `message_id` prefixes `synthetic-system-` / `synthetic-context-` at `daemon/persistence.py:437, 669` — NOT in `additional_kwargs`).
  - **Read API:** `await manager.get_messages(iid)` per subtree instance — the canonical saver-based read (NOT `manager.graph.aget_state`, which does not exist; rides the read-flip perf work). Thread config built inside `get_instance_messages` (`persistence.py:309`).
  - **Global pagination** (offset/limit across the merged collection — NOT per-instance, matching `job_messages` at `job_queue.py:1447-1503`) + **`cap_first_N_per_instance`** param for breadth-first sampling.
  - **`summary=True` INCLUDED in v1** (per `decisions.md` R-O6 leader override) — metadata-only mode reduces output budget ~80%; full mode is already lossy due to compaction.
  - Registration per existing conventions (factory closure + `@register_tool_category("instance")` + `_full_doc_` + per-agent `meta.json` opt-in via `tools.allow` resolved through `registry.get_version/get_resolved`; `_check_team_membership` line-drift correction: `instance.py:418`, NOT `:747-847`).

### Out of Scope

- Cross-subtree queries (must be subtree-rooted at the caller; no foreign subtree access).
- Write/delete tools for messages (read-only only).
- Schema changes to cope with a messages table — checkpoints remain the source of truth.
- Any change to `instance_hierarchy` (transient); we use `parent_id` lineage.
- Direct manipulation of `_pending_injections` FIFO from agent code (must go through `manager.set_injection`).
- Auto-resume of PAUSED instances (architect verdict rejects this — see R-O1; pause-intent semantics + deliberate asymmetry with user API).
- DB-backed agent-injection queue (parity with user API for v1 per R-O2; explicit Phase 1d follow-up at >2% loss rate — see Deferred Follow-ups #1).
- `set_injection(..., source=None)` provenance param (deferred to next graph.py-touching PR per leader decision — see Deferred Follow-ups #2; v1 mitigation is INFO logging).
- Concurrent fanout of checkpoint reads for v1 (sequential; `Semaphore(5)` gather acceptable if needed).
- Deterministic placeholder-id hardening for tool-pairing (deferred per R-O7 — safe by construction, locked by test a-bis; see Deferred Follow-ups #4).
- `resume_instance` agent tool (only if a real use case emerges; never smuggled into `send_message` — see Deferred Follow-ups #3).
- Changes to `daemon/services/instance_messaging.py` core.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | `send_message` tool upgrade | Inject into live targets + revive from all terminals + PAUSED reject (corrected text) + trim-check + INFO logging + eligibility-set hoist + W5 ordering | 7 base + 2b/2c/3b = 10 | loose with Phase 2 (shared factory closure, registration plumbing, test fixture pattern) | pending implementation |
| 2 | `subtree_messages` tool | Subtree-scoped, token-safe read-only subtree message query (BFS, get_messages, global pagination, D12 synthetic exclusion, summary mode in v1) | 8 + 3b (synthetic exclusion) = 9 | loose with Phase 1 (same factory + meta.json opt-in pattern + mock_registry fixture gotcha) | pending implementation |

## Coupling Map

| | Phase 1 | Phase 2 |
|---|---|---|
| Phase 1 | — | loose (shared `create_instance_tools()` factory, shared `_check_team_membership` pattern, shared test conftest) |
| Phase 2 | loose | — |

Both phases share:
- `create_instance_tools()` factory closure (`daemon/tools/instance.py` ~:920-2250) — new entries APPENDED, not restructured.
- `@register_tool_category("instance")` registration path.
- `_full_doc_` discovery via `tool_help()`.
- `meta.json` `tools.allow` resolution via `_check_team_membership` (`daemon/tools/instance.py:418` per architect §7 #16 line-drift correction — NOT `:747-847`) using `registry.get_version(agent_id, version_tag)` with `registry.get_resolved(...)` fallback.
- Test fixture pattern in `tests/unit/tools/test_instance_tools.py` — `create_instance_tools(manager=mock_manager, current_instance_id=..., agent_id=..., version_tag=None, registry=mock_registry)` with `mock_registry.get_version.return_value = None` explicitly set (gotcha: MagicMock truthy default bypasses the fallback).
- Status-read API: `manager.get_instance_info(iid).get("status")` (`get_instance_status()` does NOT exist — was a hallucinated API name; resolved per D14).
- Subtree enumeration: `manager.get_tree_ids_permanent(iid)` — the **leader-approved facade method** on `daemon/manager.py` that delegates to `InstanceRepository.get_tree_ids_permanent(...)` at `daemon/repositories/instance/repository.py:428-492`. The tool layer MUST call the facade — never `manager._instance_repository` directly (consistent with D14).
- FORBIDDEN: `manager._instance_repository` reach-ins (coupling smell imitated from `job_inject` at `job_queue.py:1783`; the agent tool uses the facade only).

Contract-change mid-flight risk: if Phase 1 alters the closure return shape (not planned), Phase 2 registration breaks. Mitigation: Phase 2 plan explicitly APPENDS only.

Phase 1 also touches `daemon/routers/messages.py:39-42` and `daemon/tools/job_queue.py:1787-1790` (one-line import swap each) for the `_INJECTION_ELIGIBLE_STATUSES` hoist (D13) — behavior-preserving refactor.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Tool-pairing regression** during agent-tool-triggered injection (recent bug class — 84fd8018 / 58260f35) | High | Medium | **R1-RESOLVED (D1/D3 + D4 + D13).** Injection routes via `set_injection` → same FIFO → single delivery point at `agent_node` already calls `_ensure_tool_result_pairing` BEFORE appending HumanMessage (`graph.py:2892-2894`). NO new guard site at agent-tool layer. Regression tests extend `tests/unit/graph/test_injection_tool_pairing.py` patterns for the agent-tool trigger path (test a + a-bis concurrent-source + f W3 + g W4 lock the O7-by-construction guarantee). |
| 2 | **Stranding race** — agent-injected messages stranded during W3 (pause-between-inject-and-drain via `clear_injection` at `instance_lifecycle.py:2501`) or W7 (daemon crash, total RAM loss). | Medium | Medium | **R2-RESOLVED (R-O2).** Accept parity with user API; INFO logging at call site provides observability; PAUSED-after-inject result text mentions stranding risk. Escalation trigger: >2% loss rate post-launch → schedule Phase 1d (DB-backed store mirroring `report_injections`). W4 (RUNNING→terminal with FIFO) is BENIGN — FIFO is NOT cleared on terminal transitions; locked in by test g. |
| 3 | **Scope/authz bug** in `subtree_messages` — forgetting to validate `target ∈ caller's subtree` | High | Medium | Single chokepoint `_validate_subtree_target`; integration tests cover cross-subtree rejection; subtree = caller's own BFS via `parent_id`. |
| 4 | **Performance cliff** in `subtree_messages` on deep subtrees | Medium | Medium | `max_instances=20` cap (matches `job_messages`); sequential reads ride the read-flip perf work (~80% cheaper than the (non-existent) aget_state anti-pattern); `summary=True` mode for predictable cost (~80% reduction); `Semaphore(5)` gather acceptable if needed (job_messages precedent at `job_queue.py:1447-1503`). |
| 5 | ~~**PAUSED semantics ambiguity** without architect decision~~ | Medium | n/a | **RESOLVED (R-O1).** Reject + corrected text; deliberate asymmetry with user-API auto-resume. NO `resume_instance` reference (that tool does not exist). |
| 6 | **Eligibility-set fork** — `_INJECTION_ELIGIBLE_STATUSES` currently forked at `routers/messages.py:39-42` AND `job_queue.py:1787-1790`; Phase 1 must not mint a third copy | High | Low | **R6-RESOLVED (D13).** Phase 1 Task 2b explicitly hoists; test k asserts exactly one definition + two/three consumers via `grep -n "_INJECTION_ELIGIBLE_STATUSES" daemon/`. |
| 7 | **Synthetic-message leakage** to descendants (persona + token cost) | High | High | **R7-RESOLVED (D12).** Filter at retrieval time (NOT in formatter); test asserts zero synthetic + zero real-system messages in any descendant result. |
| 8 | **Doc drift** between docstring and `_full_doc_` | Low | Medium | Update both in same edit; verify via `tool_help()` after change; test i asserts parity including W5 ordering sentence. |
| 9 | **Test fixture fragility** — MagicMock truthy default for `mock_registry.get_version.return_value` | Low | Medium | Conftest sets it explicitly; test plans call out the gotcha. |
| 10 | **Branch drift** — base `latest` moved since 6ca9541c during plan execution | Low | Medium | Architect / implementer to rebase before merging; no source edits happen during plan creation itself. |
| 11 | **W5 ordering surprise** — concurrent senders may interleave injection vs enqueue | Medium | Low | **R11-DOCUMENTED (R-O4 + D10).** Sentence in docstring + result text: "Delivery is FIFO but may interleave with concurrent senders — do not assume order between injection and enqueue." Injections land BEFORE child reports in the same wake-up turn (graph.py:2871 before graph.py:3021). |
| 12 | **Provenance anonymity** — injection entries carry zero source | Medium | Medium | **R12-RESOLVED (Task 3b v1 + §8 follow-up #2).** INFO log at call site (v1 mitigation); `set_injection(..., source=None)` scheduled as named §8 follow-up. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `send_message` into RUNNING target injects without breaking tool-call↔tool-message pairing | New unit test: agent sends to RUNNING target mid-tool-call; verify guard synthesizes placeholder ToolMessage | All 16 existing `test_injection_tool_pairing.py` cases pass + ≥3 new cases for agent-tool trigger path |
| 2 | **Concurrent-source single-pass guard test** (R-O7 lock-in) | Test a-bis: user-API inject + agent-tool inject in same FIFO batch → guard runs once, `existing_tool_call_ids` dedupe (`graph.py:341-344, 361-362`) prevents double synthesis | 1 new test passes |
| 3 | `send_message` to all 4 terminal states revives and dispatches | Parametric unit test over {COMPLETED, TERMINATED, ERROR, FAILED} | 4/4 pass; tool result text contains `"Instance was {prior_status} — revived and message dispatched"` |
| 4 | `send_message` to PAUSED returns corrected guidance (per R-O1) | Unit test c: PAUSED target → rejection text matches §2-O1 verbatim | 1 test passes; no DB / state mutation observed; NO `resume_instance` reference |
| 5 | **W3 (pause-between-inject-and-drain)** test | Test f: inject → target PAUSED before drain → FIFO cleared (`clear_injection` at `instance_lifecycle.py:2501`) → result text mentions stranding risk | 1 test passes; locked-in behavior |
| 6 | **W4 (RUNNING→terminal with FIFO)** test | Test g: FIFO populated → terminal transition → FIFO survives → revive → drain delivers in order | 1 test passes; locked-in benign-survival behavior |
| 7 | **Empty-content trim-check** | Test c-bis: `""`, `"   "`, `"\n\t\n"` all rejected before routing; `set_injection`/`enqueue_message` NOT called | 3 tests pass + 1 counter-test for trimmed non-empty |
| 8 | **Eligibility-set hoist** verified | Test k: `grep -n "_INJECTION_ELIGIBLE_STATUSES" daemon/` shows ONE definition + 2/3 consumers | grep pattern returns expected count |
| 9 | **INFO logging** at call site | Test j: `caplog` asserts one `INFO` line per successful send with `event="agent_send_message"`, caller_iid, target_iid, routed_via, prior_status, content_len, source | Log test passes; shape asserts |
| 10 | `subtree_messages` rejects targets outside caller's subtree | Integration test: parentA calls `subtree_messages(parentB's subtree)` → permission error | 1 test passes; non-descendant access denied |
| 11 | `subtree_messages` respects `max_instances` cap | Fuzz test: 100-instance subtree → returns at most 20 | Cap enforced; per-message truncation ≤200 chars; total output ≤~8000 chars |
| 12 | **`subtree_messages` uses `get_messages` (NOT aget_state)** | Fuzz test: 100-instance subtree → exactly 20 `get_messages` calls (regression guard against the broken API reappearing) | Exactly 20 mock calls |
| 13 | **D12 synthetic-message exclusion** | Test d: 20 descendants each with a synthetic system prompt → result contains zero synthetic + zero real system messages from descendants | 1 fuzz test passes + counter-test (caller's own system prompt kept when `target == caller`) |
| 14 | **`summary=True` metadata-only mode** | Test f: `summary=True` → output contains only `instance_id`/`agent_id`/`role`/`created_at`/`tool_call_names` + first-80-char preview; token count ~20% of full mode | 1 test passes; ~80% reduction documented in `_full_doc_` |
| 15 | **Global pagination + `cap_first_N_per_instance`** | Test e: global `offset/limit` over merged collection; `cap_first_N_per_instance=5` limits each instance's contribution | 1 test per direction passes |
| 16 | **Compaction offset-instability** smoke | Test h: compact a target, re-query with same params, observe offset shift OR `compacted_at` hint — DOCUMENTED behavior | Test passes (not asserted as bug) |
| 17 | Both tools are registered and discoverable | `tool_help("instance.send_message")` and `tool_help("instance.subtree_messages")` return updated docs including W5 ordering sentence | Both return non-empty, non-default |
| 18 | `meta.json` opt-in via `get_version` / `get_resolved` works | Test: agent with no explicit allow-list → registered tool does NOT appear; with `"subtree_messages"` in `tools.allow` → appears | 1 integration test per direction |
| 19 | Tool-pairing regression suite remains green | `pytest tests/unit/graph/test_injection_tool_pairing.py` | 16/16 pass before + after (+ new a-bis, f, g) |
| 20 | No new JobItem mirrors created (JAFP compliance) | Source review: agent tool layer still calls only `enqueue_message` and `set_injection` | No `JobItem` allocation in `instance.py` send_message path; grep `-n "JobItem" daemon/tools/instance.py` shows pre-existing count only |
| 21 | No `_instance_repository` reach-ins (D14) | Source review: `grep -n "_instance_repository" daemon/tools/instance.py` returns zero hits | FORBIDDEN pattern absent |

## Research Insights

- **Single delivery point for injections:** `agent_node` in `daemon/graph.py:2871-2911` — every FIFO source funnels here, and the pairing guard runs at `:2892-2894` BEFORE the new HumanMessage is appended. Batch drain per `agent_node` entry (single guard pass); atomicity holds (no await between get `:2872` and clear `:2901`).
- **Pairing guard invariant:** O(1) tail check + bounded backward walk (`_TOOL_PAIRING_MAX_TRAVERSAL=8`); synthesized ToolMessages persisted via C2 return at `:3386-3397`. Multi-source dedupe via `existing_tool_call_ids` at `graph.py:341-344, 361-362`. No new guard site needed at the agent-tool layer — reuse is safe by construction.
- **Revive path already covers all 4 terminal states** in `_prepare_enqueued_message` (`instance_messaging.py:1522-1540`); the tool-layer rejection in `instance.py:1695-1709` is the sole blocker.
- **PAUSED exemption** exists at `instance_messaging.py:1513-1517` — pause gate intentionally bypasses enqueue; resume is explicit. Pause drops injections immediately via `clear_injection(node_id)` at `instance_lifecycle.py:2501` (NOT TTL-delayed) — W3 race window.
- **Subtree enumeration (lineage correction):** `get_tree_ids_permanent()` is a **Python-side BFS over `parent_id`, depth-capped 256** (`daemon/repositories/instance/repository.py:428-492`). NOT a recursive CTE as the original plan stated. NOT `instance_hierarchy` (transient).
- **Canonical read API for messages:** `await manager.get_messages(iid)` — same pattern as `GET /instances/{id}/messages` at `daemon/routers/instances.py:1422-1489` and `job_messages` at `daemon/tools/job_queue.py:1470`. Thread config built inside `get_instance_messages` at `daemon/persistence.py:309`. `manager.graph.aget_state` does NOT exist (rejected by architect §5 + §7 #1).
- **Synthetic-message markers** live as **dict keys `is_synthetic=True`** AND `message_id` prefixes `synthetic-system-` / `synthetic-context-` at `daemon/persistence.py:437, 669` — NOT in `additional_kwargs` (original plan assumed wrong location; corrected).
- **Canonical role names** (post-serialization) are `"user" | "assistant" | "tool" | "system"` (`daemon/utils.py:96`); pre-serialization class names (`"human"`, `"ai"`) fail filter calls.
- **JAFP compliance:** agent-to-agent internal path uses `enqueue_message` only (no JobItem mirror). Reference: blueprint note "Message API jobs use system_parallel_queue (concurrency=5), NOT system_fifo_queue".
- **tool-pairing test fixture gotcha:** `mock_registry.get_version.return_value` MUST be explicitly `None` — MagicMock truthy default bypasses the `get_resolved` fallback.
- **TTL sweep is `_cleanup_instance_state`** at `manager.py:3359-3396` (architect §7 #16 line-drift correction; NOT `:3323-3393`).
- **Origin defect** (enqueue-path forgery gap) at `instance_messaging.py:1337-1353` (NOT `:1310-1319`) — irrelevant to injection (which has anonymity, not forgery).
- **Reconciler suppression guard** at `task/repository.py:816/828/841` (NOT `:705` per architect §7 #16).

## Open Questions → Resolved

All 7 originally-open items are RESOLVED with architect verdicts. See `decisions.md` RESOLVED section (R-O1 through R-O7 + R-LEADER) for full verdicts and rationales.

**Highlights:**
1. **O1 PAUSED:** reject + corrected text (no `resume_instance` reference) — reject respects pause intent; deliberate asymmetry with user API's auto-resume.
2. **O2 Stranding race:** accept parity with escalation trigger (>2% loss → Phase 1d DB-backed).
3. **O3 RUNNING+idle:** always inject when eligible (queue-busy guard stays for enqueue branch).
4. **O4 WAITING_CHILDREN:** inject (parity); W5 ordering documented.
5. **O5 Tool name:** `subtree_messages` confirmed.
6. **O6 Summary mode:** **LEADER OVERRIDE — INCLUDE in v1** (metadata-only; ~80% reduction).
7. **O7 Deterministic placeholder ids:** defer — safe by construction (single drain pass + `existing_tool_call_ids` dedupe); locked by test a-bis.

**Deferred follow-ups** (per architect §8): DB-backed injection store, `set_injection(..., source=None)`, `resume_instance` tool, deterministic ids (only if drain multi-passes), `compacted_at` hint, `get_many_by_ids()` bulk fetch. See `decisions.md` Deferred Follow-ups section.

---

See:
- `phase1-plan.md` — Phase 1 detailed tasks, files-to-touch, test plan, rollback notes.
- `phase2-plan.md` — Phase 2 detailed tasks, parameter schema, registration steps, test plan, rollback notes.
- `decisions.md` — D1-D14 DECIDED items with rationale + R-O1..R-O7 + R-LEADER RESOLVED items with architect verdicts + Deferred Follow-ups section.
- `architecture-recommendation.md` — Architect analysis (verdicts, race map W1-W11, read-model recommendation, §7 change list, §8 deferred table, §9 flip conditions).
