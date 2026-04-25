# Architecture Decisions: Innate-Skills Refactoring

## Decision 1: Skill Loading Strategy

**Decision**: When `innate_skills` is present in `meta.json`, load ONLY from centralized `agents/innate-skills/`. Do NOT merge with local `skills/`.

**Rationale**: Mixing sources creates ambiguity about precedence and ordering. Clean cut is simpler and produces predictable results.

**Impact**: Once an agent has `innate_skills`, its local `skills/` directory is ignored entirely.

---

## Decision 2: `innate_skills` Array Ordering

**Decision**: The loader applies `sorted()` to the `innate_skills` array regardless of declaration order.

**Rationale**: Matches current behavior where `sorted(skills_dir.iterdir(), key=lambda p: p.name)` produces alphabetical order regardless of filesystem ordering. Ensures identical prompts even if meta.json array order varies.

**Impact**: Deterministic prompt composition.

---

## Decision 3: `load_agent_skills()` Signature Change

**Decision**: Add `meta: dict | None = None` parameter to `load_agent_skills()`.

**Rationale**: The function needs access to `innate_skills` from `meta.json`. Passing it as a parameter keeps the function pure and testable without introducing global state or re-reading the file.

**Impact**: The single call site in `load_and_cache_prompt()` must be updated to read `meta.json` and pass it:
```python
meta_path = agent_dir / "meta.json"
meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
skills = load_agent_skills(agent_dir, meta)
```
This is safe — `meta.json` is already tracked in the cache key, so no additional I/O or cache risk.

---

## Decision 4: `AgentMetadata` Model Extension

**Decision**: Add `innate_skills: list[str] = []` field to `AgentMetadata` in `daemon/registry.py`.

**Rationale**: 
- `find_skill()` needs to know which agents have which skills without re-reading `meta.json` from disk
- The model already has `extra="ignore"`, so existing serialized data is compatible
- Default `[]` means agents without the field work fine

**Alternative considered**: Read `meta.json` on-the-fly in `find_skill()`. Rejected because it adds I/O per call and is inconsistent with how other metadata is handled.

**Population (W4)**: The field is populated in the registry's `discover()` method, which already reads `meta.json` for each agent directory. Add:
```python
innate_skills=meta.get("innate_skills", [])
```
to the `AgentMetadata(...)` constructor call in `discover()`.

---

## Decision 5: Base Path Resolution

**Decision**: 
- **In loader** (`daemon/loader.py`): Use `agent_dir.parent / "innate-skills"`. `agent_dir` is `agents/{agent_name}/`, so `.parent` gives the absolute `agents/` path. This works because the loader receives an already-resolved absolute `agent_dir`.
- **In registry** (`daemon/registry.py`): Use `self._agents_dir / "innate-skills"`. The registry already stores the resolved agents directory — use it directly. **Never use `Path("agents") / ...`** which resolves against `os.getcwd()` and breaks under PyInstaller/frozen states.

**Rationale**: Two different contexts require two different resolution strategies. Both produce the same absolute path at runtime.

**Risk**: If agent directories are ever moved or symlinked, `agent_dir.parent` may not point to `agents/`. Current structure makes this unlikely.

**Mitigation**: Could also resolve relative to a configured `AGENTS_DIR` constant. Worth considering as a follow-up.

---

## Decision 6: Empty Array Edge Case

**Decision**: Use truthy check `meta.get("innate_skills")` instead of membership check `"innate_skills" in meta`.

**Rationale**: If someone sets `"innate_skills": []` in `meta.json`, the membership check would enter the innate-skills branch and load zero skills — silently bypassing any existing `skills/` directory. The truthy check treats an empty array the same as a missing field, falling through to legacy behavior.

**Impact**: Safer migration path. Empty arrays are treated as "not yet migrated."
