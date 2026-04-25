# Phase 3: Modify Loader & Registry

## Objective

Update `daemon/loader.py` and `daemon/registry.py` to load skills from the centralized `agents/innate-skills/` directory when an agent's `meta.json` declares `innate_skills`. Maintain backward compatibility with the old `skills/` directory approach.

## Coupling

- **Depends on**: Phase 1 (requires `agents/innate-skills/` to exist with skill files) + Phase 2 (requires `innate_skills` field in agent meta.json files)
- **Coupling type**: tight (loader code directly references `agents/innate-skills/` paths)
- **Shared files with other phases**: `daemon/loader.py` (Phase 4 updates tests), `daemon/registry.py`
- **Shared APIs/interfaces**: `load_agent_skills()` function signature unchanged (returns `dict[str, str]`), `find_skill()` method signature unchanged (returns `list[str]`)
- **Why this coupling**: The loader must resolve the new paths; if the directory structure differs, loading breaks

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `load_agent_skills()` | Read `innate_skills` from `meta.json`, load from `agents/innate-skills/{name}/skill.md`. Fall back to old `skills/` directory if field absent or empty. | `daemon/loader.py` lines 188-211 |
| 2 | Update call site in `load_and_cache_prompt()` | Caller must read `meta.json` and pass `meta` dict to `load_agent_skills()`. This is the only call site. | `daemon/loader.py` lines 450-529 |
| 3 | Update `compose_system_prompt()` | No changes needed — already receives `skills: dict[str, str]` and sorts by key. The loading function change is sufficient. | `daemon/loader.py` lines 247-331 |
| 4 | Update `load_and_cache_prompt()` cache keys | Add `agents/innate-skills/{name}/skill.md` file mtimes to the cache key when `innate_skills` is used. Track both old and new paths during transition. | `daemon/loader.py` lines 450-529 |
| 5 | Update `find_skill()` in registry | Check `innate_skills` field on `AgentMetadata` as primary source. Fall back to per-agent `skills/` for backward compat. Use `self._agents_dir` for path resolution. **Note (W7)**: `find_skill()` has no production callers — only test mocks reference it. | `daemon/registry.py` lines 292-311 |
| 6 | Update `AgentMetadata` model + `discover()` | Add `innate_skills: list[str] = []` field. Populate in `discover()` via `meta.get("innate_skills", [])`. | `daemon/registry.py` lines 49-87 |

## Detailed Implementation

### Task 1: `load_agent_skills()` rewrite

**Current behavior** (lines 188-211):
```python
def load_agent_skills(agent_dir: Path) -> dict[str, str]:
    skills_dir = agent_dir / "skills"
    skills: dict[str, str] = {}
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if skill_dir.is_dir():
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                skills[skill_dir.name] = skill_file.read_text(encoding="utf-8")
    return skills
```

**New behavior**:
```python
def load_agent_skills(agent_dir: Path, meta: dict | None = None) -> dict[str, str]:
    """Load agent skills from centralized innate-skills or local skills/ directory."""
    skills: dict[str, str] = {}

    # New path: load from innate_skills registry
    # NOTE: truthy check (not just "in") ensures empty array [] falls through to legacy
    if meta and meta.get("innate_skills"):
        innate_skills_dir = agent_dir.parent / "innate-skills"
        for skill_name in sorted(meta["innate_skills"]):
            skill_file = innate_skills_dir / skill_name / "skill.md"
            if skill_file.exists():
                skills[skill_name] = skill_file.read_text(encoding="utf-8")
            else:
                # Log warning: declared skill not found in innate-skills
                ...
        return skills

    # Legacy fallback: load from agent's own skills/ directory
    skills_dir = agent_dir / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if skill_dir.is_dir():
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                skills[skill_dir.name] = skill_file.read_text(encoding="utf-8")
    return skills
```

**Important: Caller must be updated (C2)**. The only call site is in `load_and_cache_prompt()`. It currently does NOT read `meta.json`. It must be updated to:
```python
# Inside load_and_cache_prompt(), before calling load_agent_skills():
meta_path = agent_dir / "meta.json"
meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
skills = load_agent_skills(agent_dir, meta)
```

This is safe because `meta.json` is already read elsewhere and its mtime is already tracked in the cache key. No additional I/O risk.

**Key decision**: `innate_skills` array is already sorted alphabetically (Phase 2 ensures this), so `sorted()` is a safety net that preserves identical ordering to the old filesystem-based sort.

**Edge case (W5)**: Using `meta.get("innate_skills")` (truthy check) instead of `"innate_skills" in meta` ensures that `"innate_skills": []` falls through to legacy fallback rather than silently loading zero skills.

### Task 3: Cache key changes

**Current** (lines 450-529): Tracks mtimes of `skills/{name}/skill.md` files found by scanning the agent's `skills/` directory.

**New**: When `innate_skills` is present, track mtimes of `agents/innate-skills/{name}/skill.md` instead. The cache key construction must:

1. Detect which loading mode is active (innate vs legacy)
2. Track the correct file paths for mtime comparison
3. Include `meta.json` mtime (already tracked) so changes to `innate_skills` array invalidate cache

**Critical**: `meta.json` is already in the cache key, so adding/removing skills from `innate_skills` will auto-invalidate. Only the skill file path resolution needs updating.

**Explicit pseudocode (W6)**:
```python
# Inside load_and_cache_prompt(), when building the cache key:

if meta and meta.get("innate_skills"):
    # Innate-skills mode: track centralized skill files
    innate_skills_dir = agent_dir.parent / "innate-skills"
    for skill_name in sorted(meta["innate_skills"]):
        skill_file = innate_skills_dir / skill_name / "skill.md"
        if skill_file.exists():
            cache_key[f"innate-skills/{skill_name}/skill.md"] = str(skill_file.stat().st_mtime)
else:
    # Legacy mode: scan agent's own skills/ directory (current behavior)
    skills_dir = agent_dir / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if skill_dir.is_dir():
                skill_file = skill_dir / "skill.md"
                if skill_file.exists():
                    cache_key[f"skills/{skill_dir.name}/skill.md"] = str(skill_file.stat().st_mtime)
```

### Task 4: `find_skill()` update

**Current** (lines 292-311): Checks `metadata.path / "skills" / skill_name / "skill.md"` for each agent.

**New behavior** (uses `self._agents_dir` for absolute path — **C1 fix**):
```python
def find_skill(self, skill_name: str) -> list[str]:
    if '/' in skill_name or '\\' in skill_name or '..' in skill_name:
        return []
    agents_with_skill = []
    innate_skill_path = self._agents_dir / "innate-skills" / skill_name / "skill.md"
    innate_exists = innate_skill_path.exists()
    for agent_id, metadata in self._agents.items():
        # Check innate-skills registry (via AgentMetadata.innate_skills)
        if innate_exists and metadata.innate_skills and skill_name in metadata.innate_skills:
            agents_with_skill.append(agent_id)
            continue
        # Legacy fallback: check per-agent skills/ directory
        skill_path = metadata.path / "skills" / skill_name / "skill.md"
        if skill_path.exists():
            agents_with_skill.append(agent_id)
    return agents_with_skill
```

**Note (W7)**: `find_skill()` currently has **no production callers** — only test mocks reference it. This refactoring is low-risk but still required for correctness and future-proofing.

**Note**: Uses `metadata.innate_skills` (from `AgentMetadata` model) instead of reading `meta.json` from disk. This requires Decision 4 (`AgentMetadata` extension) to be implemented first (Task 6).

## Key Files

- `daemon/loader.py` — `load_agent_skills()` (188-211), `load_and_cache_prompt()` (450-529)
- `daemon/registry.py` — `find_skill()` (292-311), `AgentMetadata` (49-87)

## Constraints

- **Function signature change**: `load_agent_skills()` gets a new `meta` parameter — must be optional with `None` default for backward compat
- **Call site update required**: `load_and_cache_prompt()` must read `meta.json` and pass it to `load_agent_skills()` — this is the only call site
- **Same output dict**: The returned `dict[str, str]` must have the same keys and values as the old implementation for each agent
- **Sorted keys**: Both old and new paths produce alphabetically sorted keys
- **No changes to `compose_system_prompt()`**: It already handles skills correctly via the dict it receives
- **AgentMetadata model**: Add `innate_skills` field, maintain backward compat with existing serialized data
- **Empty array edge case (W5)**: Use truthy check (`meta.get("innate_skills")`) not membership check (`"innate_skills" in meta`) — empty array must fall through to legacy path
- **Path resolution (C1)**: Use `agent_dir.parent` in loader (already correct), use `self._agents_dir` in registry — never use relative `Path("agents")` which breaks under PyInstaller/frozen states
- **`innate-skills/` not discovered as agent (W8)**: The registry's `discover()` already skips directories without `meta.json`. No additional guard needed for the `innate-skills/` directory.

## Deliverables

- [ ] `load_agent_skills()` loads from `agents/innate-skills/` when `innate_skills` field present and non-empty
- [ ] Call site in `load_and_cache_prompt()` updated to read `meta.json` and pass `meta` dict
- [ ] Legacy `skills/` directory loading still works when `innate_skills` absent or empty
- [ ] Cache keys correctly track `innate-skills/` file mtimes (with explicit pseudocode implemented)
- [ ] `find_skill()` resolves skills from `AgentMetadata.innate_skills` using `self._agents_dir` for paths
- [ ] `AgentMetadata` has `innate_skills` field, populated in `discover()` via `meta.get("innate_skills", [])`
- [ ] All existing tests still pass
