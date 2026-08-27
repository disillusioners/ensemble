# Phase 2: `subtree_messages` Tool (Read-Only Subtree Query)

## Objective

Add a new read-only agent-facing tool `subtree_messages` (proposed name — see `decisions.md` O5) that lets a parent agent query messages of its descendant tree (children, grandchildren, …), strictly scoped to the caller's own subtree, with filtering, pagination, and token-safe output modeled on the existing `job_messages` tool.

## Files to Touch

| File | Change | Lines (verify drift at impl) |
|------|--------|-----------------------------|
| `daemon/tools/instance.py` | Define `subtree_messages()` inside `create_instance_tools()`; append to factory closure list; set `_full_doc_` | factory starts ~`:920`; closure list append at ~`:2240-2250` |
| `daemon/manager.py` | **NEW additive facade method** `Manager.get_tree_ids_permanent(caller_instance_id)` that delegates to `InstanceRepository.get_tree_ids_permanent(...)` at `daemon/repositories/instance/repository.py:428-492`. This is the **leader-approved seam** — exactly this one thin method, nothing else. The facade preserves the Manager-as-facade boundary (tool layer calls `manager.get_tree_ids_permanent(...)`; it never reaches into `manager._instance_repository` — consistent with D14) | append after existing facade methods; verify exact insertion point at impl |
| `agents/{name}/meta.json` (per agent opt-in) | Add `"subtree_messages"` to `tools.allow` for agents that need subtree introspection | per-agent |
| `tests/unit/tools/test_instance_tools.py` | Extend with subtree scoping tests, filter tests, pagination tests, cap tests, registration tests | full file |
| `tests/unit/graph/test_injection_tool_pairing.py` | No change (read-only tool, no injection path) | — |

No changes to: `daemon/graph.py`, `daemon/services/instance_messaging.py`, `daemon/routers/messages.py`, `daemon/repositories/instance/repository.py`. **`daemon/manager.py`: ONLY the additive `Manager.get_tree_ids_permanent()` facade method described above — no other changes (no reach-in re-exports, no other new facade methods).** The tool READS via `await manager.get_messages(iid)` per subtree instance — the canonical pattern shared by `GET /instances/{id}/messages` (`daemon/routers/instances.py:1422-1489`) and `job_messages` (`daemon/tools/job_queue.py:1470`); thread config is built inside `get_instance_messages` (`daemon/persistence.py:309`). It does NOT use `manager.graph.aget_state` (does not exist — would raise `AttributeError`; rejected by architect §5 + §7 #1 in favor of the saver-based read which rides the read-flip perf work). It does NOT write.

## Tool Naming Proposal

**Proposed:** `subtree_messages`

**Rationale:** Matches job toolset naming convention (`job_messages`, `job_tree`, `job_progress`, `job_inject`) — verb implied by the noun "messages"; the `subtree_` prefix scopes the operation to the caller's subtree (not arbitrary instance trees).

**Alternatives** (for architect — see `decisions.md` O5):
- `instance_subtree_messages` — more verbose, explicit
- `tree_messages` — shorter, possibly ambiguous (which tree?)
- `messages_query` — generic, less informative

## Parameter Schema

```python
subtree_messages(
    target_instance_id: str | None = None,   # root of subtree to query; None = caller's own subtree (no root-walk)
    filters: dict | None = None,             # {"role": "user"|"assistant"|"tool"|"system", "child_instance_id": str, "status": str} — canonical role names per daemon/utils.py:96
    limit: int = 50,                          # global message cap across the merged collection
    offset: int = 0,                          # global pagination offset across the merged collection (compaction-unstable — see Notes)
    max_instances: int = 20,                  # total instance cap (matches job_messages:20)
    cap_first_N_per_instance: int = 0,         # breadth-first sampling cap per instance; 0 = off (default); when >0, take first N per instance before global pagination
    summary: bool = False,                    # metadata-only mode (INCLUDED in v1 — see decisions.md O6 verdict); saves ~80% output tokens
) -> str
```

Output: a single human-readable string formatted as a series of per-instance blocks. Format style mirrors `job_messages` for consistency.

**Role-name canonicalization (§7 #4):** filter values MUST use the post-serialization canonical names `"user" | "assistant" | "tool" | "system"` (`daemon/utils.py:96`). The pre-serialization LangChain class names (`"human"`, `"ai"`) are NOT accepted — they fail every filter call. Tests must pin all four names.

**Combined-filter semantics:** `child_instance_id` + `target_instance_id` together is an error UNLESS they are equal (target-as-its-own-descendant edge case).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Define tool signature** in `daemon/tools/instance.py` inside `create_instance_tools()`: see schema above | none | Signature stable; default args match `job_messages` pattern |
| 2 | **Implement subtree validation** — helper `_validate_subtree_target(caller_instance_id, target_instance_id) -> tuple[bool, set[str]]` returns `(allowed, subtree_ids)`; calls **`manager.get_tree_ids_permanent(caller_instance_id)`** — the leader-approved facade method on `daemon/manager.py` that delegates to `InstanceRepository.get_tree_ids_permanent(...)` at `daemon/repositories/instance/repository.py:428-492`. The tool layer MUST call `manager.get_tree_ids_permanent(...)` — it MUST NOT reach into `manager._instance_repository` directly (consistent with D14). | Task 1 | Returns `(False, …)` when `target ∉ subtree`; `(True, …)` when allowed; correct resolution of `target=None` to caller's own subtree (no root-walk per §7 #13) |
| 3 | **Implement message retrieval** — for each subtree instance, call `await manager.get_messages(iid)` (canonical saver-based read — same pattern as `GET /instances/{id}/messages` at `daemon/routers/instances.py:1422-1489` and `job_messages` at `daemon/tools/job_queue.py:1470`); collect the resulting message list; apply filters in code (role, content-type, child instance id). Each call returns a list of LangChain messages for ONE instance; merging into the global collection is the formatter's job (Task 4). | Task 2 | Messages fetched; filters applied correctly; per-instance errors caught and warned (do not fail entire query). **Fuzz test asserts EXACTLY ONE `get_messages` call per subtree instance** (catches the aget_state anti-pattern if it reappears). |
| 3b | **Synthetic-message exclusion (D12)** — when the resolved subtree target ≠ caller, filter out (a) every message with `is_synthetic=True` (synthetic markers live as dict keys `is_synthetic=True` AND as `message_id` prefixes `synthetic-system-` / `synthetic-context-` at `daemon/persistence.py:437, 669` — NOT in `additional_kwargs`), and (b) every real `role=="system"` message. When `target == caller`, keep system-role messages (the caller's own system prompt is part of its context). The filter must happen at retrieval time, not in the formatter, so synthetic token costs never reach the agent. | Task 3 | Test asserts zero synthetic messages and zero real system messages in any descendant result. Without D12, 20 descendants × full system prompts = token blowup AND persona leakage to parents. |
| 4 | **Token-safety + summary-mode formatting** — (a) Full-content mode: per-message content truncated to 200 chars; ToolMessage redacted to `name + first 100 chars of args`; total output ceiling ~8000 chars. (b) Summary mode (`summary=True`, INCLUDED in v1 per O6 verdict override): keep only `instance_id` + `agent_id` + `role` + `created_at` + `tool_call_names`; content → first 80 chars. Summary reduces output budget ~80% (full mode is already lossy due to compaction-induced `RemoveMessage` sentinels + `SystemMessage` summaries, `daemon/compaction.py:1036-1070`). Pagination is GLOBAL `offset/limit` across the merged collection (NOT per-instance) — matches `job_messages` (`job_queue.py:1447-1503`). Document compaction offset-instability in `_full_doc_`. | Task 3 + 3b | Format matches `job_messages` output style; tool calls redacted; truncation markers (`…`) present; summary mode emits ONLY the metadata tuple. Tests cover both modes. |
| 5 | **Cap enforcement** — `max_instances=20` default (matches `job_messages:20`); when subtree exceeds cap, return first 20 by `instance_id` sort + warning text indicating more available; per-instance `limit=50` default | Task 4 | Fuzz test: 100-instance subtree → output truncated; warning text present |
| 6 | **Registration** — `@tool` + `@register_tool_category("instance")` (verify exact decorator combo at impl by reading `daemon/tools/job_queue.py:create_job_tools()`); append to factory closure list (`:2240-2250`); set `_full_doc_` attribute | Task 1 | `tool_help("instance.subtree_messages")` returns non-empty doc |
| 7 | **meta.json opt-in** — add `"subtree_messages"` to agent `tools.allow` (narrow opt-in per `decisions.md` D9); verify via `_check_team_membership` (canonical anchor: `daemon/tools/instance.py:418`) using `registry.get_version(...)` (canonical anchor: `instance.py:408`) with `registry.get_resolved(...)` fallback (canonical anchor: `instance.py:2184-2186`). Line-drift correction per delta-fix #3: the older cluster `:747-847` / `:775-781` / `:808-815` / `:824` is wrong; use the three canonical anchors only. | Task 6 | Tool appears in agent's available tools when added; absent when not added; 1 integration test per direction |
| 8 | **Tests** — see test plan below | Tasks 2-7 | All cases pass |

## Subtree Scoping + Authorization Design

**Mechanism:** `manager.get_tree_ids_permanent(caller_instance_id)` — the leader-approved facade method on `daemon/manager.py` (delegating to `InstanceRepository.get_tree_ids_permanent(...)`) — returns the set of instance IDs in the caller's subtree via a **Python-side BFS over `parent_id`, depth-capped 256** (`daemon/repositories/instance/repository.py:428-492`). It is NOT a recursive CTE as the original plan stated (lineage correction per architect §5; behavior matches intent, but the description must be accurate). This IS the authorization — no separate per-instance ACL is needed because the caller's scope is mechanically bounded by the parent_id BFS. **The tool layer calls `manager.get_tree_ids_permanent(...)` ONLY — it MUST NOT reach into `manager._instance_repository` directly (consistent with D14).**

**Edge cases:**

- **Caller is a root** (parent_id IS NULL): subtree = {self}. Querying own subtree returns only own messages.
- **Subtree exceeds `max_instances`:** first 20 returned (sorted by instance_id), warning text indicates more available.
- **`target_instance_id=None`:** resolve to caller's OWN subtree (no root-walk — simpler and correct because the caller IS within its own subtree). Specified by architect §7 #13.
- **`target_instance_id` provided but ∉ subtree:** permission error returned by tool.
- **Missing/corrupt checkpoint/saver for a descendant:** catch per-instance exception; warn; skip; do NOT fail entire query.
- **BFS depth cap 256:** if the actual lineage depth exceeds 256 (defensive), deeper descendants are silently excluded. Log a WARN at tool-call time if the cap is reached.

**Do NOT use `instance_hierarchy`** — transient. Use `parent_id` — permanent and survives terminate-to-revive.

## Checkpoint Read Performance Considerations

- Each `await manager.get_messages(iid)` call rides the just-landed read-flip perf work (33-114× faster reads per the LangGraph perf PR3 milestone). The ~50-100ms/instance estimate from the original plan is **conservative** — re-measure at implementation and document the actual figure in the test suite (`@pytest.mark.timeout(N)` based on the measured budget).
- With `max_instances=20`, worst case ~2s per query at the conservative estimate; expect ~200-400ms after the read-flip perf work.
- **Sequential reads for v1** — acceptable. `asyncio.gather` with `Semaphore(5)` is also acceptable if needed (precedent: `job_messages` at `job_queue.py:1447-1503`), but sequential is simpler and sufficient for v1.
- **Summary mode (`summary=True`, INCLUDED in v1 per O6 verdict override):** keeps `instance_id`/`agent_id`/`role`/`created_at`/`tool_call_names`; content → first 80 chars. Reduces output budget ~80% — full mode is already lossy because compaction replaces pre-compaction messages with `RemoveMessage` sentinels + a `SystemMessage` summary (`daemon/compaction.py:1036-1070`, written at `graph.py:3256`), so summary mode loses little additional information.
- **Batching:** resolve all subtree `thread_ids` first, then `get_messages` per instance. No concurrent fanout required for v1.
- **Instance-status filter (when `filters.status` set):** N× `manager.get_instance_info(iid)` (each call returns `{"status": ...}` or raises `KeyError` if the instance is gone) via `asyncio.gather` (no bulk method exists at `repository.py:288`). The tool layer MUST go through the facade — it MUST NOT call `instance_repository.get(iid)` directly (consistent with D14 / success criterion #21). Acceptable for v1; `get_many_by_ids()` is an optional follow-up.

**Known limitation (documented behavior, not bug):** global `offset/limit` pagination is **unstable across compaction events** — pre-compaction messages are destroyed (`RemoveMessage` sentinels replace them). Offsets returned today may not correspond to the same messages tomorrow. Document this in `_full_doc_`.

## Registration Steps

1. Define `def subtree_messages(...) -> str:` inside `create_instance_tools()` factory closure (after `send_message`).
2. Decorate with `@tool` + `@register_tool_category("instance")` — verify the exact decorator combo at impl by reading `daemon/tools/job_queue.py:create_job_tools()` for the parallel pattern. Tool category is registered automatically via the factory closure + decorator.
3. Append the function to the factory closure list at `~:2240-2250`.
4. Set `_full_doc_` attribute on the function with full docstring content (powers `tool_help()`).
5. Update `agents/{name}/meta.json` `tools.allow` with `"subtree_messages"` for narrow opt-in (`decisions.md` D9) — do NOT add the `"instance"` category as a whole (broader blast radius).

`tools.allow` resolution flow (verified from research — line-drift correction per architect §7 #16):
- `_check_team_membership` lives at `daemon/tools/instance.py:418` (NOT `:747-847` as the original plan stated) — it delegates to `daemon/tools/_auth.py`.
- `registry.get_version(caller_agent_id, caller_version_tag)` falls back to `registry.get_resolved(...)` (the canonical fallback path; verify exact line at impl).
- Category name expands to all tools in the category; direct tool name selects a single tool.
- For narrow opt-in: explicit `"subtree_messages"` entry.

## Test Plan

### a. Subtree scoping — accept

- Setup: caller has 3 children.
- `subtree_messages(target=None)` → returns all 3 children's messages + caller's own.
- `subtree_messages(target=grandchild_id)` → returns grandchild's messages only.

### b. Subtree scoping — reject

- `subtree_messages(target=sibling_id)` (where sibling is NOT a descendant) → permission error in tool result.
- `subtree_messages(target=unrelated_id)` → permission error.
- Caller IS a root (parent_id NULL); `subtree_messages(target=None)` → returns only own messages.

### c. Filter behavior

- `filters={"role": "assistant"}` → returns only assistant messages (canonical name; NOT `"ai"`).
- `filters={"role": "user"}` → returns only user messages (canonical name; NOT `"human"`).
- `filters={"role": "tool"}` → returns only tool messages.
- `filters={"role": "system"}` → returns only system messages (real, not synthetic; see test d below for the synthetic-exclusion rule).
- `filters={"child_instance_id": "X"}` → returns only X's messages (subtree-scoped).
- `filters={"status": "RUNNING"}` → returns only messages from RUNNING instances.
- Combined filters: AND semantics.
- **`filters={"child_instance_id": "X", "target_instance_id": "Y"}` where `X ≠ Y`** → error.

### d. Synthetic-message exclusion (D12, new — §7 #6 + #18)

- Caller queries a descendant that has a system prompt + several real messages.
- Result MUST contain ZERO `is_synthetic=True` messages AND zero `message_id` prefixed `synthetic-system-` or `synthetic-context-` (per `daemon/persistence.py:437, 669`).
- Result MUST contain zero real `role=="system"` messages from descendant (only caller's own system prompt is kept, and only when `target == caller`).
- Counter-test: caller queries its OWN subtree (`target == caller`) → system messages are KEPT.
- Fuzz test: 20-descendant subtree, each with a synthetic system prompt → output token count ≤ 1.5× the no-synthetic baseline.

### e. Pagination + caps

- Subtree of 100 instances → first 20 returned (sorted by instance_id), warning text present.
- Single instance with 200 messages → GLOBAL `limit=50, offset=0` → 50 returned; `offset=50` → next 50; `offset=150` → empty + warning. (Global pagination, not per-instance.)
- `cap_first_N_per_instance=5` → each instance contributes at most 5 messages before global pagination.

### f. Token safety

- Message with 10KB content → truncated to 200 chars + ellipsis.
- ToolMessage → name + 100-char args only (no tool output content).
- Total output > 8000 chars → ceiling enforced (truncate tail + warning).
- `summary=True` → output contains ONLY `instance_id`, `agent_id`, `role`, `created_at`, `tool_call_names`, first 80 chars of content; token count ~20% of full mode.

### g. Performance / fixture

- Mock `manager.get_messages` (NOT `manager.graph.aget_state`) for 5 instances → all 5 called sequentially; output correct.
- Per-instance read error → skip + warn; remaining 4 still returned.
- **Fuzz test:** mock `get_messages` for 100-instance subtree → assert EXACTLY 20 calls (no double-reads, no skips beyond the cap).

### h. Compaction-instability smoke (§7 #18, documented behavior)

- Compact a target instance (force compaction → `RemoveMessage` sentinels + `SystemMessage` summary, per `daemon/compaction.py:1036-1070`).
- Re-query with `limit=50, offset=0` → observe that the message set differs from a pre-compaction query with the same params.
- This is a **documented behavior assertion** (the test passes if the result differs OR if a clear `compacted_at` warning is returned — it does NOT assert a bug). Add to the test docstring: "Compaction destroys pre-compaction content; pagination offsets are unstable across compaction events."

### i. Registration

- `tool_help("instance.subtree_messages")` returns doc.
- meta.json with `"subtree_messages"` in `tools.allow` → tool resolvable via `get_version`/`get_resolved`.
- meta.json WITHOUT the entry → tool NOT resolvable; `tool_filter` returns False.

## Coupling

- **Loose with Phase 1** — shares factory closure, registration plumbing, test conftest pattern, `mock_registry.get_version.return_value = None` fixture gotcha.
- **Independent of:** `daemon/graph.py`, `daemon/services/instance_messaging.py`, `daemon/routers/messages.py`. **`daemon/manager.py`: ONLY the additive `Manager.get_tree_ids_permanent()` facade method (leader-approved seam) — no other changes.**

## Risks (Phase 2 specific — see plan-overview for full list)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Scope/authz bug** — forgetting to validate `target ∈ subtree` | High | Medium | Single chokepoint `_validate_subtree_target`; integration tests cover cross-subtree rejection |
| 2 | **Performance cliff** on deep subtrees | Medium | Medium | `max_instances=20` cap; sequential reads; `get_messages` rides the read-flip perf work (~80% cheaper than aget_state); `Semaphore(5)` gather is available if needed |
| 3 | **Token blow-up** if caps not enforced | Medium | Low | Hard caps in formatter; redaction of ToolMessages; output ceiling ~8000 chars; `summary=True` mode for predictable cost (~80% reduction) |
| 4 | **Transient `instance_hierarchy` confusion** — agents might assume it's the source of truth | Low | Low | Doc explicitly states `parent_id` lineage (BFS via parent_id, depth-capped 256); notated in `_full_doc_` |
| 5 | **Read error** if saver/checkpoint is missing / corrupt | Medium | Low | Per-instance try/except; skip + warn; do not fail entire query |
| 6 | **meta.json drift** between agents (some opt-in, some not) | Low | Medium | Intentionally per-agent opt-in per `decisions.md` D9; documented in tool doc |
| 7 | **Pagination offset instability** across compaction events | Low | High | Documented in `_full_doc_`; compaction is destructive (not hidden); agent must query fresh after compaction — NOT a bug |
| 8 | **Synthetic-message leakage** to descendants (persona / token cost) | High | High | D12 filter at retrieval time; test asserts zero synthetic + zero real-system messages in any descendant result |

## Rollback Notes

- Phase 2 changes are isolated to `daemon/tools/instance.py` + test files + per-agent `meta.json` opt-in entries. `git revert` of Phase 2 commit(s) restores prior behavior.
- `meta.json` opt-in is per-agent — to roll back for one agent, remove the `"subtree_messages"` entry. The tool simply becomes unavailable for that agent.
- No DB migration, no schema change, no checkpoint mutation (read-only tool). Rollback is pure code + config revert.
- Feature flag option: wrap the new tool's body in `if settings.ENABLE_AGENT_INSTANCE_TOOLS_V1` (default ON for staging, OFF in prod until validated). Coordinate with implementer.

## Exit Criterion

- New tool `subtree_messages` registered (factory + category + `_full_doc_`).
- All test cases (a-i) pass — INCLUDING D12 synthetic-exclusion and compaction-instability smoke.
- Cross-subtree access rejected (integration test passes).
- Token safety verified (cap tests pass; truncation + redaction confirmed; summary mode emits only metadata tuple).
- `summary=True` mode ships in v1 (~80% output reduction documented in `_full_doc_`).
- Global pagination + `cap_first_N_per_instance` working; compaction offset-instability documented.
- meta.json opt-in works for at least one agent (per-agent test passes).
- `tool_help("instance.subtree_messages")` returns non-empty doc.
- Phase 2 changes touch ONLY `daemon/tools/instance.py`, per-agent `meta.json`, and `tests/unit/tools/test_instance_tools.py`.
- `grep -n "get_messages" daemon/tools/instance.py` confirms the new tool uses the canonical read; `grep -n "aget_state" daemon/tools/instance.py` returns zero hits (regression guard against the broken API reappearing).
