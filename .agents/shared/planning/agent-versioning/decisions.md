# Key Design Decisions: Agent Versioning / Tagging

## D1: Square-Bracket Suffix Format

**Decision**: The version tag is a trailing `[tag]` suffix in the directory name.

- `agents/developer/` → base version (no tag, `version_tag = None`)
- `agents/developer[v2]/` → version "v2"
- `agents/developer[test]/` → version "test"

**Parsing rule**: Match ONLY a trailing `[tag]` at end of the directory name. The regex is:

```
^(.+?)\[([A-Za-z0-9_-]+)\]$
```

> **Non-blocking fix applied**: Tightened character class from `[^\[\]]+` to `[A-Za-z0-9_-]+` to prevent path traversal via `/`, `\`, `..` in tags.

- Group 1 = base_agent_id (parsed from directory name)
- Group 2 = version_tag

**Rationale**: Square brackets are illegal in most agent ID validation (the `create_agent` endpoint already restricts IDs to alphanumeric + hyphen + underscore). This makes tagged dirs visually distinct and unambiguous.

**Edge cases handled**:
- `developer[[v2]]` → does NOT match (inner brackets rejected)
- `developer[v2]extra` → does NOT match (must be trailing)
- `[v2]developer` → does NOT match (must be trailing)
- `developer[../etc]` → does NOT match (`.` and `/` not in allowed charset)
- `developer[v2/sub]` → does NOT match (`/` not allowed)
- Plain `developer` → no match, treated as base agent (current behavior)

**`create_agent` API**: The `POST /api/agents` endpoint (agents.py:80) currently rejects bracket characters in agent IDs. Tagged directory creation is **manual** (copy base dir + rename + modify) for now. The API does NOT need to support bracket-suffixed IDs in this phase.

---

## D2: Registry Keying Strategy — Separate Version Dict (v3 Architecture)

**Decision**: The registry uses TWO separate dicts. Base agents live in `_agents` (unchanged from current behavior). Tagged versions live in a NEW `_versioned_agents` dict. Resolvers and all existing methods NEVER encounter composite keys.

> **v3 Revision (approver issue #2)**: The previous design stored composite keys (`"developer[v2]"`) inside `_agents`. This leaked composite keys through `resolve_pure_id()`, `resolve_path_to_id()`, `find_skill()`, and `validate_tool_configs()` — all of which iterate `_agents` or return its keys. The separate-dict approach eliminates this entire class of bugs.

**Final internal structure**:

```python
class AgentRegistry:
    _agents: dict[str, AgentMetadata]              # BASE agents only, keyed by agent_id
    _versioned_agents: dict[str, AgentMetadata]    # TAGGED versions only, keyed by "agent_id[tag]"
    _versions: dict[str, list[str | None]]         # base_agent_id → sorted version_tags (None = base)
```

**Key invariants**:
1. `_agents` keys are ALWAYS plain agent_ids (`"developer"`, `"leader"`) — never composite. All existing methods (`get`, `resolve_pure_id`, `resolve_to_id`, `resolve_path_to_id`, `list_all`, `find_skill`, `validate_tool_configs`, `exists`) work UNCHANGED because they only see `_agents`.
2. `_versioned_agents` keys are ALWAYS composite (`"developer[v2]"`) — internal only.
3. `_versions` maps base_agent_id → list of tags. `None` in the list means a base version exists.

**discover() logic**:

```python
base_agent_id, version_tag = _parse_agent_dir_name(agent_path.name)

agent_meta = AgentMetadata(
    id=meta.get("id", base_agent_id),  # display id from meta or dir
    version_tag=version_tag,
    path=agent_path,
    # ... other fields ...
)

if version_tag is None:
    # Base version → goes into _agents (keyed by base_agent_id)
    self._agents[base_agent_id] = agent_meta
else:
    # Tagged version → goes into _versioned_agents (keyed by composite)
    composite_key = f"{base_agent_id}[{version_tag}]"
    self._versioned_agents[composite_key] = agent_meta

# Populate _versions map (always keyed by base_agent_id)
if base_agent_id not in self._versions:
    self._versions[base_agent_id] = []
if version_tag not in self._versions[base_agent_id]:
    self._versions[base_agent_id].append(version_tag)
```

**Lookup methods**:

| Method | Dict consulted | Behavior |
|--------|---------------|----------|
| `get(agent_id)` | `_agents` only | Returns base version. UNCHANGED. |
| `get_version(agent_id, version_tag)` | `_agents` + `_versioned_agents` | Returns specific version via fallback chain (D8). |
| `resolve_pure_id(agent_id)` | `_agents` only | UNCHANGED — never sees composite keys. |
| `resolve_to_id(agent_id_or_path)` | `_agents` only | UNCHANGED — never sees composite keys. |
| `list_all()` | `_agents` only | Returns base versions. UNCHANGED — no dedup needed (C5 fix is now architectural). |
| `list_all_grouped()` | `_agents` + `_versioned_agents` | Returns full versioned view for API. |
| `list_versions(agent_id)` | `_versions` | Returns available tags for an agent. |
| `find_skill(skill_name)` | `_agents` + `_versioned_agents` | Must check BOTH dicts (tagged agents may have different skills). Returns base agent_ids only. |
| `validate_tool_configs()` | `_agents` + `_versioned_agents` | Must check BOTH dicts. Uses `meta.id` for display. |

**Why this works**: By keeping composite keys OUT of `_agents`, every existing consumer (spawn_instance's `resolve_to_id`, `team_members` checks, `find_skill`, `validate_tool_configs`) continues to work exactly as before. The only new code paths are `get_version()` and `list_all_grouped()`, which are consumed only by the versioning feature.

---

## D3: Version Selection at Instance Creation

**Decision**: Instance creation accepts an optional `version_tag` parameter. If omitted, the **base version** is used (if it exists), otherwise the **first available** tagged version.

**Flow**:
```
POST /api/instances { agent_id: "developer", version_tag: "v2" }
  → spawn_instance(agent_id="developer", version_tag="v2")
    → registry.get_version("developer", "v2") → metadata (path=agents/developer[v2])
    → resolved_agent_dir = metadata.path
    → INSERT instance(agent_id="developer", agent_tag="v2", agent_dir="...")
```

**Backward compat**: When `version_tag` is None/omitted, behavior is identical to current. The `agent_tag` column stores NULL.

---

## D4: Database Column Name — `agent_tag` (NOT `version`)

**Decision**: The new DB column is named `agent_tag`, type `String` (VARCHAR), nullable.

**Why not `version`?**
The `instances` table already has a `version` column (Integer) used for **optimistic locking** (concurrency control). Reusing it would cause confusion and bugs.

**Why not `agent_version`?**
Could be confused with the existing `AgentMetadata.version` field (which is a semver string like "1.0.0" from meta.json, unrelated to directory tags).

`agent_tag` is unambiguous and directly maps to the `[tag]` suffix concept.

---

## D5: `team_members` and `spawn_instance` Tool — No Version Tags

**Decision**: `team_members` arrays in meta.json reference the **base agent_id only**. The spawn_instance tool does NOT accept a version_tag parameter.

**Rationale**:
- Agent-to-agent spawning is programmatic; version selection is a user-facing concern.
- Keeping team_members simple avoids cascading complexity (version resolution in permission checks, etc.).
- If a versioned agent needs to spawn a specific version of another agent, it can use the full directory path via existing path resolution.

**Implication**: When `spawn_instance` tool resolves `team_members`, it always uses the base version. The `version_tag` parameter exists ONLY on the HTTP API `POST /instances` endpoint and the internal `spawn_instance()` method.

---

## D6: Unify Agent Listing (Registry vs Router)

**Decision**: Phase 2 refactors `daemon/routers/agents.py:list_agents()` to use the `AgentRegistry` instead of its own independent filesystem scan.

**Current state**: `routers/agents.py` has its own FS scan logic that duplicates the registry's discovery (skips, meta.json parsing, etc.). This is a maintenance burden and the two can diverge.

**Rationale**: With versioning, having two scan paths would double the work. The registry is the single source of truth for agent discovery.

---

## D7: API Response Shape — Flat List with Version Metadata

**Decision**: `GET /api/agents` returns a **flat list** of `AgentInfo` objects (one per discovered directory), each with optional `version_tag` and `available_versions` fields. The frontend groups them.

**Alternative considered**: Nested structure `{ agents: [{ id, versions: [...] }] }`. Rejected because it's a bigger breaking change to the API contract.

**New AgentInfo fields**:
```python
class AgentInfo(BaseModel):
    # ... existing fields ...
    version_tag: str | None = None        # This entry's tag (None = base)
    available_versions: list[str | None] = []  # All tags for this agent_id (None = base exists)
```

**Example response**:
```json
{
  "agents": [
    {"id": "developer", "name": "Developer", "version_tag": null, "available_versions": [null, "v2"]},
    {"id": "developer", "name": "Developer", "version_tag": "v2", "available_versions": [null, "v2"]},
    {"id": "leader", "name": "Leader", "version_tag": null, "available_versions": [null]}
  ]
}
```

The frontend deduplicates by `id` and uses `available_versions` to render the picker.

---

## D8: Default Version When Base Is Missing

**Decision**: If an agent_id has tagged versions but NO base directory (e.g., only `agents/custom[v1]/` exists, no `agents/custom/`), the `available_versions` list will NOT contain `None`. The first tagged version becomes the effective default.

**Resolution fallback chain**:
1. `version_tag` explicitly provided → use that exact version
2. `version_tag` is None AND base exists → use base
3. `version_tag` is None AND base missing → use first available tagged version (sorted)
4. No versions at all → `ValueError` (agent not found)

---

## D9: Migration Naming and Approach

**Decision**: New SQLite migration file `20260724_000001_add_agent_tag_to_instances.sql` + corresponding `ALTER TABLE` in `_ensure_postgres_columns()`.

Pattern follows `20260514_000001_add_project_id_to_instances.sql` (the `project_id` column addition).

> **W9 Revision**: Drop the `CREATE INDEX` — queries filtering by `agent_tag` are rare and the index adds migration overhead. Column-only migration.

```sql
-- SQLite migration (column only, no index per W9)
ALTER TABLE instances ADD COLUMN agent_tag VARCHAR;
```

```python
# manager.py _ensure_postgres_columns() (column only, no index per W9)
"ALTER TABLE instances ADD COLUMN IF NOT EXISTS agent_tag VARCHAR",
```

No backfill needed — existing rows have NULL `agent_tag` (correct: they used base version).

---

## D10: Explicit Error on Invalid Version Tags (C2 Fix)

**Decision**: When `version_tag` is explicitly provided (not None) and the requested version does not exist, the system MUST raise a `ValueError` listing available versions. Silent fallback to base is ONLY allowed when `version_tag is None`.

> **v3 Revision (approver issue #4)**: The resolution block must normalize path→base-id BEFORE calling `get_version()`. The frontend sends `./agents/developer` as `agent_id`, not a bare `"developer"`. `get_version()` expects a base agent_id, not a path.

**Resolution logic in `spawn_instance()`**:

```python
registry = get_registry()

# Step 1: Normalize agent_id — resolve paths/aliases to base agent_id
# (approver issue #4: frontend sends "./agents/developer", not "developer")
resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id

# Step 2: Version-aware resolution
if version_tag is not None:
    # Explicit version requested — must match exactly, no fallback (C2)
    metadata = registry.get_version(resolved_agent_id, version_tag)
    if metadata is None:
        available = registry.list_versions(resolved_agent_id)
        raise ValueError(
            f"Version tag '{version_tag}' not found for agent '{resolved_agent_id}'. "
            f"Available: {available}"
        )
else:
    # No tag specified — use fallback chain (base → first tagged)
    metadata = registry.get_version(resolved_agent_id, None)
    if metadata is None:
        metadata = registry.get(resolved_agent_id)
    if metadata is None:
        raise ValueError(f"Agent not found: {agent_id}")

# metadata.path now points to the correct directory (base or tagged)
resolved_agent_dir = str(metadata.path)
```

**Rationale**: Silent fallback on an explicit user choice is a UX bug — a user typing "v3" (typo for "v2") expects an error, not a silently different agent version. The `resolve_to_id()` call first ensures we have a clean base agent_id regardless of whether the caller passed a path, alias, or bare id.

---

## D11: `list_all()` Backward Compatibility (C5 Fix)

**Decision**: `list_all()` continues to return ONLY base versions (one entry per agent_id). This is now **architecturally guaranteed** by D2's separate-dict design — `_agents` only contains base entries, so `list_all()` iterating `_agents` naturally returns no duplicates.

> **v3 Revision (approver issue #3)**: With D2's separate-dict design, `list_all()` doesn't need any dedup logic. It simply returns `self._agents.values()` sorted by id. The composite-key leak that caused duplicate entries is eliminated at the source. The previous `meta.id`-based dedup was also wrong (approver issue #3) because a tagged dir's `meta.json["id"]` may differ from base — but this is now moot since tagged entries aren't in `_agents` at all.

```python
def list_all(self) -> list[AgentMetadata]:
    """List all registered agents — base versions only (backward compatible).
    
    With D2's separate-dict design, _agents only contains base entries.
    No dedup needed — returns sorted _agents values directly.
    """
    return sorted(self._agents.values(), key=lambda a: a.id)
```

A new `list_all_grouped()` method provides the full versioned view for API consumers by combining `_agents` + `_versioned_agents`.

---

## D12: Router Skip-Rule Divergence (C4 Fix)

**Decision**: Before refactoring `routers/agents.py:list_agents()` to use the registry, audit and reconcile the skip rules.

**Current divergence** (verified in code):

| Rule | `routers/agents.py` | `registry.discover()` |
|------|---------------------|----------------------|
| Hidden dirs (`.` prefix) | ✅ Skipped | ✅ Skipped |
| Non-directories | ✅ Skipped | ✅ Skipped |
| Symlinks | ❌ Not checked | ✅ Skipped (security) |
| `_trash`, `_baby_template` | ✅ Skipped (explicit) | ✅ Skipped (in SKIP_DIRS) |
| `_prompt_system`, `_inner_soul` | ✅ Skipped (via `_` prefix rule) | ✅ Skipped (in SKIP_DIRS) |
| **All `_`-prefixed dirs** | ✅ **Skipped** (broad rule) | ❌ **NOT skipped** (only SKIP_DIRS members) |
| Missing `meta.json` | ✅ Silently skipped | ⚠️ Logs warning, then skips |
| JSON parse error | ✅ Silently skipped | ⚠️ Logs warning, then skips |

**Key risk**: The registry's `discover()` does NOT skip all `_`-prefixed directories — only the 4 in `SKIP_DIRS`. If a new internal dir like `_skills_cache` is added, the router would skip it but the registry would try to load it (and fail on missing meta.json).

**Resolution for refactor**: After refactoring, the registry-backed `list_agents()` should apply the same broad `_`-prefix skip as the current router. This can be done either by:
- (A) Adding `_`-prefix check to `discover()` — but this changes registry behavior for ALL consumers
- (B) Filtering in the router's `list_agents()` after calling `registry.list_all_grouped()` — safer, localized

**Recommended**: Option B — filter in the router. The registry stays a pure discovery layer; the API layer applies its own visibility rules.

---

## D13: Separate-Dict Design Eliminates Composite-Key Leaks (W3 + Approver Issue #2)

**Decision**: The registry's `_agents` dict NEVER contains composite keys. Tagged versions are stored in a separate `_versioned_agents` dict keyed by `"base_agent_id[tag]"`.

> **v3 Revision (approver issue #2)**: The previous design stored composite keys in `_agents`. This leaked composite keys through `resolve_pure_id()` (which does `if agent_id in self._agents`) — `resolve_path_to_id("./agents/developer[v2]")` would return `"developer[v2]"`, a composite key that would be stored as `agent_id` in the DB. The separate-dict design makes this impossible.

**The base_agent_id parsing rule** (W3):
- The composite key and `_versions` map use the **parsed `base_agent_id`** (from `_parse_agent_dir_name(dir_name)`), NOT `meta.json["id"]`.
- A `developer[v2]/` dir with `"id": "dev"` in meta.json still gets composite key `"developer[v2]"` and `_versions` maps under `"developer"`.

**Why resolvers are safe**: `resolve_pure_id`, `resolve_to_id`, `resolve_path_to_id` only consult `_agents`. Since `_agents` has no composite keys, they can NEVER return a composite key. This is the **keystone invariant** — see D16.

---

## D14: `agent_tag` in `InstanceInfo` is REQUIRED (S9 Fix)

**Decision**: The `agent_tag` field MUST be added to `InstanceInfo` (the API response model), not optional. The frontend needs it to display which version an instance is running (Phase 3 version badge).

```python
class InstanceInfo(BaseModel):
    # ... existing fields ...
    agent_tag: str | None = Field(default=None, description="Agent version tag (None = base)")
```

**Also add to `Instance.to_dict()`**: Downstream consumers (`job_processor`, `child_reports`, `error_reporting`) read instance data via `to_dict()`, not just `InstanceInfo`. The `agent_tag` must be in `to_dict()` output as well.

---

## D15: PromptCache Key Must Include Version Tag (Approver Issue #1 — SHOWSTOPPER)

**Decision**: The `PromptCache._make_key()` method must include `version_tag` (or `agent_dir`) in the cache key to prevent base and tagged versions from colliding.

**The bug**: `loader.py:511-526` uses `f"{agent_id}::{normalized_mcp}"`. Both `spawn_instance` (line 1101) and `_restore_instance` (line 2434) call `load_and_cache_prompt(resolved_agent_id, agent_path, ...)`. Since `resolved_agent_id` is `"developer"` for BOTH base and v2 (D13 says the agent IS developer), base spawns cache under `"developer::"`, then v2 spawns hit that cache and get the **base prompt**. If mtimes differ, they corrupt each other's cache.

**Fix**: Add `version_tag` to the cache key:

```python
# loader.py — PromptCache._make_key() (MODIFIED)
def _make_key(self, agent_id: str, mcp_tool_names: list[str] | None, version_tag: str | None = None) -> str:
    if mcp_tool_names:
        normalized_mcp = ",".join(sorted(mcp_tool_names))
    else:
        normalized_mcp = ""
    # Include version_tag to prevent base/tagged cache collision (D15)
    tag_suffix = f"[{version_tag}]" if version_tag else ""
    return f"{agent_id}{tag_suffix}::{normalized_mcp}"
```

**Callers must be updated**: `load_and_cache_prompt()` needs a `version_tag` parameter, which it passes to `cache.get()`, `cache.set()`, and `cache._make_key()`. Both `spawn_instance` (line 1101) and `_restore_instance` (line 2434) must pass `version_tag`.

**Alternative considered**: Use `agent_dir` (filesystem path) as the cache key instead of `agent_id`. Rejected because it changes the cache semantics for ALL agents (not just versioned ones), risking broader regressions. The version_tag suffix is minimal and targeted.

**Scope of change**:
- `loader.py`: `PromptCache._make_key()`, `PromptCache.get()`, `PromptCache.set()`, `PromptCache.invalidate()`, `load_and_cache_prompt()` — add optional `version_tag` param
- `instance_lifecycle.py:1101`: `load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names, version_tag)`
- `instance_lifecycle.py:2434`: same in `_restore_instance()`
- All other callers of `load_and_cache_prompt` pass `version_tag=None` (default) — backward compatible

---

## D16: Keystone Invariant — Resolvers Never Return Composite Keys

**Decision**: The registry's resolver methods (`resolve_pure_id`, `resolve_to_id`, `resolve_path_to_id`) must NEVER return a composite key like `"developer[v2]"`. They always return a plain base agent_id.

**Why it's the keystone**: Every downstream consumer — `spawn_instance` storing `agent_id` in DB, `team_members` permission checks, `exists()` — trusts that resolved IDs are plain agent_ids. A composite key leaking through would store `"developer[v2]"` as `agent_id` in the `instances` table with `agent_tag=NULL` — a silent corruption.

**How it's enforced**: With D2's separate-dict design, `_agents` only contains base entries. All resolver methods only consult `_agents`. Therefore they structurally cannot return composite keys.

**Contract test**: A dedicated test must assert this invariant:

```python
def test_resolvers_never_return_composite_keys():
    """D16 keystone invariant: resolve_* methods return only base agent_ids."""
    registry = setup_registry_with_tagged_versions()
    
    # resolve_pure_id must not match composite keys
    assert registry.resolve_pure_id("developer[v2]") is None
    
    # resolve_to_id with a path containing composite must return base
    assert registry.resolve_to_id("./agents/developer[v2]") is None  # or "developer" if we choose to support it
    
    # All resolved IDs must not contain brackets
    for meta in registry.list_all():
        assert "[" not in meta.id, f"Composite key leaked: {meta.id}"
```
