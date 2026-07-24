# Phase 1: Registry — Tag Parsing, Separate-Dict Versioning & PromptCache Fix

## Objective

Modify `AgentRegistry.discover()` to parse `[tag]` suffixes from directory names using a **separate-dict design** (base agents in `_agents`, tagged versions in `_versioned_agents`). Expose new lookup methods (`get_version`, `list_versions`, `list_all_grouped`). Fix the PromptCache key collision (D15). All existing methods remain structurally unchanged.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/registry.py` (Phase 2 reads from this), `daemon/loader.py` (PromptCache fix consumed by Phase 2 spawn/restore)
- **Shared APIs/interfaces**: New methods `get_version()`, `list_versions()`, `list_all_grouped()` — consumed by Phase 2. Modified `load_and_cache_prompt()` signature (D15).
- **Why this coupling**: Phase 2's API and instance creation call these registry methods; they must exist and be tested first. The PromptCache fix must be in place before Phase 2 modifies spawn/restore calls.

## Context

- Previous phase: N/A (this is the foundation)
- Key decisions: D1 (tag format + regex tightening), D2 (separate-dict design), D8 (fallback), D15 (PromptCache), D16 (resolver invariant)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `version_tag` field to `AgentMetadata` | New optional field `version_tag: str \| None = None`. `None` = base version. | `daemon/registry.py:69-123` |
| 2 | Create tag-parsing utility function | `_parse_agent_dir_name()` using regex `^(.+?)\[([A-Za-z0-9_-]+)\]$` (tightened charset — prevents path traversal). | `daemon/registry.py` (new function near top) |
| 3 | Add `_versioned_agents` + `_versions` dicts to `__init__` | NEW: `self._versioned_agents: dict[str, AgentMetadata] = {}` (tagged only, composite keys). `self._versions: dict[str, list[str \| None]] = {}`. `_agents` stays base-only. | `daemon/registry.py:140-148` |
| 4 | Modify `discover()` — separate-dict design | Parse tag from dir name. Base → `_agents[base_agent_id]`. Tagged → `_versioned_agents["base_agent_id[tag]"]`. Populate `_versions` using parsed `base_agent_id`. | `daemon/registry.py:149-218` |
| 5 | Add `get_version(agent_id, version_tag)` method | Consults `_agents` for base, `_versioned_agents` for tagged. Fallback chain per D8. | `daemon/registry.py` (new method) |
| 6 | Add `list_versions(agent_id)` method | Returns `list[str \| None]` from `_versions` map. | `daemon/registry.py` (new method) |
| 7 | Add `list_all_grouped()` method | Returns `dict[str, list[AgentMetadata]]` by merging `_agents` + `_versioned_agents`, grouped by base agent_id. Used by API layer. | `daemon/registry.py` (new method) |
| 8 | **Fix PromptCache key collision (D15)** | Add optional `version_tag` param to `PromptCache._make_key()`, `.get()`, `.set()`, `.invalidate()`, and `load_and_cache_prompt()`. Key format: `f"{agent_id}[{version_tag}]::{normalized_mcp}"` when tagged. | `daemon/loader.py:500-665` |
| 9 | Update `find_skill()` to check both dicts | Currently iterates `_agents.items()`. Must ALSO check `_versioned_agents` for tagged versions that may have different skills. Return base agent_ids only (never composite). | `daemon/registry.py:365-391` |
| 10 | Update `validate_tool_configs()` to check both dicts | Currently iterates `_agents.items()`. Must ALSO check `_versioned_agents`. Use `meta.id` for display. | `daemon/registry.py:393-467` |
| 11 | Write Phase 1 unit tests | Tag parsing (incl. tightened charset), discover, `get_version` fallback chain, `list_versions`, `list_all_grouped`, **resolver invariant (D16)**: resolve_* never returns composite keys, PromptCache collision prevention. | `tests/test_registry_versioning.py` (new) |

## Key Files

- `daemon/registry.py` — Core registry: `AgentMetadata`, `AgentRegistry.discover()`, resolution methods, `find_skill`, `validate_tool_configs`
- `daemon/loader.py` — `PromptCache`, `load_and_cache_prompt` (D15 fix)
- `tests/test_registry_versioning.py` — New test file

## Detailed Implementation Notes

### Task 2: Tag Parsing Function (Tightened Regex)

```python
import re

# Tightened charset: [A-Za-z0-9_-]+ prevents path traversal via /, \, ..
_TAG_PATTERN = re.compile(r'^(.+?)\[([A-Za-z0-9_-]+)\]$')

def _parse_agent_dir_name(dir_name: str) -> tuple[str, str | None]:
    """Parse a directory name, extracting optional [tag] suffix.
    
    Examples:
        "developer" → ("developer", None)
        "developer[v2]" → ("developer", "v2")
        "developer[test_version]" → ("developer", "test_version")
        "dev[[v2]]" → ("dev[[v2]]", None)  # no match, inner brackets
        "dev[../etc]" → ("dev[../etc]", None)  # no match, path chars
    
    Returns:
        Tuple of (base_agent_id, version_tag or None)
    """
    match = _TAG_PATTERN.match(dir_name)
    if match:
        return match.group(1), match.group(2)
    return dir_name, None
```

### Task 4: discover() — Separate-Dict Design (D2 v3)

```python
base_agent_id, version_tag = _parse_agent_dir_name(agent_path.name)

agent_id_for_meta = meta.get("id", base_agent_id)

agent_meta = AgentMetadata(
    id=agent_id_for_meta,
    # ... all existing fields ...
    version_tag=version_tag,  # NEW
    path=agent_path,
)

if version_tag is None:
    # Base version → _agents (keyed by base_agent_id — NEVER composite)
    self._agents[base_agent_id] = agent_meta
else:
    # Tagged version → _versioned_agents (keyed by composite — internal only)
    composite_key = f"{base_agent_id}[{version_tag}]"
    self._versioned_agents[composite_key] = agent_meta

# _versions map always uses parsed base_agent_id (W3)
if base_agent_id not in self._versions:
    self._versions[base_agent_id] = []
if version_tag not in self._versions[base_agent_id]:
    self._versions[base_agent_id].append(version_tag)
```

### Task 5: get_version() — Uses Both Dicts

```python
def get_version(self, agent_id: str, version_tag: str | None = None) -> AgentMetadata | None:
    """Get agent metadata for a specific version.
    
    Fallback chain (D8):
    1. version_tag provided → exact match in _versioned_agents (returns None if not found)
    2. version_tag None + base exists → base from _agents
    3. version_tag None + base missing → first sorted tagged version
    4. Not found → None
    """
    if version_tag is not None:
        # Exact version lookup in _versioned_agents — NO fallback (C2)
        composite_key = f"{agent_id}[{version_tag}]"
        return self._versioned_agents.get(composite_key)
    
    # No tag specified — prefer base from _agents
    base_meta = self._agents.get(agent_id)
    if base_meta is not None:
        return base_meta
    
    # Base missing — use first available tagged version from _versioned_agents
    versions = self._versions.get(agent_id, [])
    tagged_versions = sorted([v for v in versions if v is not None])
    if tagged_versions:
        composite_key = f"{agent_id}[{tagged_versions[0]}]"
        return self._versioned_agents.get(composite_key)
    
    return None
```

### Task 8: PromptCache Key Collision Fix (D15 — SHOWSTOPPER)

```python
# loader.py — PromptCache._make_key() (MODIFIED)
def _make_key(self, agent_id: str, mcp_tool_names: list[str] | None, 
              version_tag: str | None = None) -> str:
    """Create a cache key from agent_id, version_tag, and MCP tool names.
    
    D15: version_tag is included to prevent base/tagged cache collision.
    Base "developer" and tagged "developer[v2]" get different keys.
    """
    if mcp_tool_names:
        normalized_mcp = ",".join(sorted(mcp_tool_names))
    else:
        normalized_mcp = ""
    tag_suffix = f"[{version_tag}]" if version_tag else ""
    return f"{agent_id}{tag_suffix}::{normalized_mcp}"

# load_and_cache_prompt() signature adds version_tag
def load_and_cache_prompt(
    agent_id: str, agent_dir: Path, cache: PromptCache, 
    mcp_tool_names: list[str] | None = None,
    version_tag: str | None = None,  # NEW — D15
) -> tuple[str, int]:
    # ... passes version_tag to cache.get() and cache.set() ...
    cached = cache.get(agent_id, mcp_tool_names, version_tag)
    # ...
    cache.set(agent_id, prompt, tokens, current_mtimes, mcp_tool_names, version_tag)
```

**Callers**: Both `spawn_instance` (line 1101) and `_restore_instance` (line 2434) must pass `version_tag`. Other callers pass `version_tag=None` (default, backward compatible).

### Task 9: find_skill() — Check Both Dicts

```python
def find_skill(self, skill_name: str) -> list[str]:
    # ... path validation ...
    agents_with_skill = []
    # Check base agents
    for agent_id, metadata in self._agents.items():
        if self._agent_has_skill(metadata, skill_name, innate_exists):
            agents_with_skill.append(agent_id)
    # Check tagged versions (may have different skills)
    for composite_key, metadata in self._versioned_agents.items():
        if self._agent_has_skill(metadata, skill_name, innate_exists):
            # Return base agent_id, not composite key
            base_id = _parse_agent_dir_name(composite_key)[0]
            if base_id not in agents_with_skill:
                agents_with_skill.append(base_id)
    return agents_with_skill
```

### Task 11: Keystone Invariant Test (D16)

```python
def test_resolvers_never_return_composite_keys():
    """D16 keystone invariant: resolve_* methods return only base agent_ids."""
    registry = setup_registry_with_tagged_versions()
    
    # resolve_pure_id must NOT match composite keys
    assert registry.resolve_pure_id("developer[v2]") is None
    
    # All list_all() entries must have plain ids
    for meta in registry.list_all():
        assert "[" not in meta.id, f"Composite key leaked into list_all: {meta.id}"
```

## Constraints

- **MUST NOT** change behavior of `get()`, `resolve_to_id()`, `resolve_pure_id()`, `resolve_path_to_id()`, `list_all()`, `get_resolved()`, `exists()` — they only consult `_agents` (now base-only).
- **D16 invariant**: Resolver methods must NEVER return composite keys. Enforced by separate-dict design + contract test.
- **D15**: PromptCache key must include `version_tag` to prevent base/tagged collision.
- **W3**: Composite key uses parsed `base_agent_id` from dir name, NOT `meta.json["id"]`.
- Tag regex charset is `[A-Za-z0-9_-]+` (no `/`, `\`, `.`, `[`, `]`).
- `version_tag` field on `AgentMetadata` must default to `None`.

## Deliverables

- [ ] `version_tag` field added to `AgentMetadata`
- [ ] `_parse_agent_dir_name()` with tightened regex `[A-Za-z0-9_-]+`
- [ ] **Separate-dict design: `_agents` (base only) + `_versioned_agents` (tagged only) + `_versions` map**
- [ ] `discover()` populates both dicts using parsed `base_agent_id` (W3)
- [ ] `get_version()`, `list_versions()`, `list_all_grouped()` methods added
- [ ] **PromptCache key includes `version_tag` (D15 fix)**
- [ ] `find_skill()` and `validate_tool_configs()` check both dicts
- [ ] **Resolver invariant: resolve_* never returns composite keys (D16)**
- [ ] All existing `get()`/`resolve_*`/`list_all()` methods structurally unchanged
- [ ] Unit tests pass for: tag parsing, discover, version lookup, fallback, **resolver invariant**, **PromptCache collision prevention**
