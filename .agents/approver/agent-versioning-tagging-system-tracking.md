# Tracking: Agent Versioning / Tagging System

## Iteration 001 (2026-07-24) — REJECTED

### Evaluation Method
- Independent fresh-eyes review of all 6 plan files (plan-overview, decisions, phase1-4)
- Direct codebase verification of registry.py, instance_lifecycle.py, manager.py, loader.py, tools/instance.py, routers
- Council-mode multi-model evaluation with 8 critical questions + blind-spot hunt

### Blocking Issues Found

1. **PromptCache key collision (SHOWSTOPPER)** — `loader.py:511-526`
   - `PromptCache._make_key = f"{agent_id}::{normalized_mcp}"` — keyed ONLY by agent_id
   - Plan's flow resolves `resolved_agent_id = metadata.id` which is base "developer" for BOTH base and v2
   - Result: base and v2 instances corrupt each other's cached prompts
   - Plan completely misses this — no mention of PromptCache anywhere
   - **Fix**: cache key must incorporate version_tag or agent_dir

2. **`resolve_pure_id` / `resolve_to_id` CANNOT stay "UNCHANGED" (HIGH)**
   - Plan claims these are UNCHANGED, but composite keys `developer[v2]` will be in `_agents`
   - `resolve_path_to_id("./agents/developer[v2]")` → `resolve_pure_id("developer[v2]")` returns composite key
   - `spawn_instance` would store `agent_id = "developer[v2]"` with `agent_tag = NULL` in DB
   - **Fix**: resolvers must normalize to base agent_id, rejecting/decoding composite keys

3. **`list_all()` dedup uses `meta.id`, not `base_agent_id` (HIGH)**
   - D11 dedups by `meta.id`, but D13 explicitly says tagged dir's meta.json id may differ from base
   - If `developer[v2]/meta.json` has `"id": "developer-v2"`, dedup fails — two entries
   - **Fix**: dedup key must be parsed base_agent_id, not meta.id

4. **Path→id resolution ordering in spawn_instance (MEDIUM)**
   - D10's resolution block calls `get_version(agent_id, version_tag)` before path normalization
   - Frontend sends `./agents/developer` — get_version receives a path, not an agent_id
   - **Fix**: normalize path→base-id before any get_version call

5. **`find_skill()` and `validate_tool_configs()` iterate `_agents` directly (MEDIUM)**
   - Both would return/process composite keys alongside base keys
   - Plan only addresses `list_all()` — these two are unaddressed
   - **Fix**: normalize output to base agent_id in both methods

### Non-Blocking Observations
- `Instance.to_dict()` needs `agent_tag` added (B4)
- Tag charset regex `[^\[\]]+` too permissive — allows `/`, `\`, `..` (B5)
- No API for creating/managing tagged dirs — `create_agent` rejects brackets (B1)
- 4 non-HTTP spawn paths cannot select versions (B2)
- Need contract test: "resolve_pure_id never returns composite key" (B7)
- Migration dual-path (.sql + _ensure_postgres_columns) ordering invariant not stated as success criterion

### Verdict: REJECTED
Three blocking issues (PromptCache collision, resolver leak, dedup inconsistency) are correctness bugs that will silently serve wrong prompts. The plan's claim that `resolve_pure_id` stays UNCHANGED is a false premise.

---

## Iteration 002 (2026-07-24) — APPROVED

### Evaluation Method
- Independent fresh-eyes re-review of all 6 plan files (plan-overview, decisions, phase1-4) — v3 revision
- Direct codebase verification of every claim: registry.py (lines 60-504), loader.py (508-669), instance_lifecycle.py (1003-1101, 2402-2450), manager.py (4072-4190), routers/agents.py, repositories/instance/models.py (47-95), frontend call sites
- Council-mode multi-model evaluation with 7 verification questions + comprehensive 68-call-site explorer audit

### Verification of Previous Blocking Issues

1. **PromptCache key collision (RESOLVED)** — D15 fix adds `version_tag` to `_make_key()`. Key format: `f"{agent_id}[{version_tag}]::{normalized_mcp}"` for tagged, `f"{agent_id}::{normalized_mcp}"` for base. Both spawn (line 1101) and restore (line 2434) thread `version_tag`. Backward compatible — all other callers pass `version_tag=None` (default). ✓

2. **Resolver composite-key leak (RESOLVED via architecture)** — D2 v3 separate-dict design: `_agents` holds base-only, `_versioned_agents` holds tagged-only. All resolver methods (`resolve_pure_id`, `resolve_to_id`, `resolve_path_to_id`, `get`, `get_resolved`, `list_all`, `exists`) only consult `_agents`. Composite keys structurally cannot appear in `_agents`. D16 keystone invariant + contract test enforces this. ✓

3. **`list_all()` dedup (RESOLVED via architecture)** — D11 v3: `list_all()` returns `sorted(self._agents.values())` directly — no dedup needed because `_agents` only contains base entries. The `meta.id` vs `base_agent_id` discrepancy is moot since tagged entries aren't in `_agents`. ✓

4. **Path normalization (RESOLVED)** — D10 v3: `spawn_instance()` calls `resolve_to_id(agent_id)` FIRST (normalizes `./agents/developer` → `"developer"`), THEN calls `get_version(resolved_agent_id, version_tag)`. ✓

5. **find_skill/validate_tool_configs (RESOLVED)** — Phase 1 Tasks 9-10: both methods now iterate both `_agents` and `_versioned_agents`. `find_skill` returns base agent_ids only (dedup via set membership check). ✓

### Non-Blocking Observations (Notes)

N1. **PromptCache invalidate() callers not explicitly listed** — `inner_soul.py` (5 call sites) and `agent_mother.py:380` call `prompt_cache.invalidate(agent_id)` without `version_tag`. After D15 fix, `invalidate()` signature gains an optional `version_tag` param (defaults None). These callers will only invalidate the BASE cache entry, not tagged entries. For `inner_soul` (which modifies an agent's own memory/soul), this means a tagged instance's memory change won't invalidate its specific cache entry — but since memory/soul files are SHARED between base and tagged dirs (tagged is a full copy), the base invalidation is effectively correct for the base prompt. The tagged cache entry will miss on next access due to mtime change. **Non-blocking: mtime comparison in load_and_cache_prompt catches this.** But a note in the plan would help implementers understand why `version_tag` isn't threaded to invalidate callers.

N2. **instance_messaging.py:387 direct cache.get()** — `_get_system_prompt_tokens()` calls `self._prompt_cache.get(meta.agent_id, mcp_tool_names)` without `version_tag`. This retrieves token count for context compaction decisions. After D15, this returns the BASE version's token count for a tagged instance (different key). Since tagged and base prompts differ in size, this could cause imprecise context compaction thresholds for tagged instances. **Non-blocking: affects only compaction timing, not correctness.** Token count is a heuristic, not a hard limit.

N3. **Frontend line numbers will drift** — The plan references exact line numbers (home.component.ts:114, 170, 187; chat.component.ts:394; instances.component.ts:100). These should be re-verified at Phase 3 implementation time.

N4. **`_ensure_postgres_columns()` ordering** — Not specified as a success criterion. Should be documented that the new `agent_tag` statement must be appended AFTER existing statements in the list (not prepended or inserted in the middle).

### Why Approved

- All 4 blocking issues from iteration 001 are soundly resolved, primarily through the separate-dict architectural change (D2 v3) which makes the resolver invariant structurally guaranteed rather than convention-enforced
- PromptCache fix (D15) is correct and backward compatible
- Plan code references verified accurate against actual codebase (line numbers, method signatures, dict structure)
- Error handling for invalid tags (C2), instance restore (C1), and frontend threading (C3) are all comprehensively covered
- Test plan (Phase 4) includes keystone invariant tests, backward compat, and dual-DB verification
- No internal contradictions found
- No new blocking issues introduced by v3 changes

### Verdict: APPROVED
