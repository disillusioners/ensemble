# Phase 5: auto_load Prompt Section (Post-Cache Append)

## Objective

Add a new `append_auto_load_skills()` post-cache function — matching the existing `append_context_key` / `append_current_time` / `append_user_language` pattern — that injects `auto_load=true` skills from the project-scoped `skills` table into the system prompt **after** the cached prompt is retrieved. This avoids PromptCache key collisions in multi-project deployments.

## Coupling

- **Depends on**: Phase 2 (`auto_load` column), Phase 4 (clone produces skills with auto_load flag)
- **Coupling type**: tight — reads `auto_load` column, depends on clone to populate skills
- **Shared files with other phases**: `daemon/services/instance_lifecycle.py` (two call sites)
- **Shared APIs/interfaces**: New `append_auto_load_skills()` function
- **Why this coupling**: Without cloned skills in the DB (P4), the auto_load query returns nothing

## Context

### Why NOT Modify compose_system_prompt()

The `PromptCache` in `daemon/loader.py` caches composed prompts keyed on `(agent_id, mcp_tool_names)` + file mtimes. The cache key does **NOT** include `project_id`. Different projects have different auto_load skills.

If auto_load content were baked into `compose_system_prompt()`, the cache would return whichever project's skills were composed first — **wrong skills for all other projects**.

### The Existing Post-Cache Pattern

The codebase already handles per-instance / per-project content via **post-cache append functions** that run AFTER `load_and_cache_prompt()` returns:

```python
# instance_lifecycle.py:854-874 (spawn path)
system_prompt, token_count = load_and_cache_prompt(...)  # ← cached

# Post-cache appends (per-instance, NOT cached)
system_prompt = append_context_key(system_prompt, instance_id, ...)
system_prompt = append_shared_context_metadata(system_prompt, instance_id, ...)
system_prompt = append_current_time(system_prompt)
system_prompt = append_user_language(system_prompt, user_language)
```

`append_auto_load_skills()` follows this exact pattern — it's a post-cache append that runs per-spawn with the current `project_id`.

### Where auto_load Skills Appear in the Prompt

Since they're appended after cache, they go **after all cached sections** (after project experience, at the very end of the prompt). This is acceptable because:
- auto_load skills are self-contained markdown sections
- The separator `\n---\n\n` maintains visual section breaks
- Order within the prompt matters less than presence

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `append_auto_load_skills()` function | Post-cache append function | `daemon/services/instance_lifecycle.py` |
| 2 | Implement `load_auto_load_skills()` DB query helper | Query skills table for auto_load=true | `daemon/services/instance_lifecycle.py` |
| 3 | Wire into spawn path call site | After `append_user_language()`, before tools creation | `daemon/services/instance_lifecycle.py:~875` |
| 4 | Wire into restore path call site | After `append_user_language()`, before tools creation | `daemon/services/instance_lifecycle.py:~2148` |
| 5 | Trigger clone-on-miss before loading | Ensure auto_load skills exist in project scope before querying | `daemon/services/instance_lifecycle.py` (both call sites) |
| 6 | Write tests | Verify append function, empty case, project isolation | `tests/unit/test_append_auto_load_skills.py` (NEW) |

## Detailed Design

### 5.1 append_auto_load_skills() Function

**File**: `daemon/services/instance_lifecycle.py` (new function, near `append_user_language()`)

```python
def append_auto_load_skills(
    system_prompt: str,
    agent_id: str,
    project_id: str | None,
    manager: Any,
) -> str:
    """Append auto_load skills to a system prompt (post-cache).

    Post-processing step (like ``append_context_key`` and
    ``append_current_time``) — runs AFTER the cached prompt is loaded,
    so auto_load skill changes do NOT invalidate the prompt cache.

    Queries the skills table for active skills where ``auto_load=true``
    for the given ``project_id``. If found, formats them as a prompt
    section and appends to the system prompt. If none found, returns
    the original prompt unchanged.

    Before querying, triggers clone-on-miss to ensure the project has
    auto_load skills cloned from the skill_bank templates.

    Args:
        system_prompt: The base system prompt to append to.
        agent_id: The agent identifier (e.g. 'tester'). Used for
            clone-on-miss template lookup.
        project_id: Current project ID. None or empty = no auto_load
            skills (returns prompt unchanged).
        manager: InstanceManager reference — used to access
            ``_skill_repo``, ``_skill_clone_service``,
            ``_skill_bank_repo``.

    Returns:
        The system prompt with auto_load skills section appended,
        or the original system_prompt unchanged when no skills found
        or skill_evolution not configured.
    """
    if not project_id:
        return system_prompt

    skill_repo = getattr(manager, "_skill_repo", None)
    if skill_repo is None:
        # skill_evolution not configured — auto_load not available
        return system_prompt

    # ── Clone-on-miss: ensure auto_load skills exist in project scope ──
    # Before querying, clone any missing auto_load skills from skill_bank
    # templates. This is the bridge between the isolated bank and the
    # evolution system.
    clone_service = getattr(manager, "_skill_clone_service", None)
    if clone_service is not None:
        try:
            clone_service.ensure_auto_load_skills_sync(
                agent_id=agent_id,
                project_id=project_id,
            )
        except Exception as e:
            logger.warning(
                f"Clone-on-miss for auto_load skills failed "
                f"(agent={agent_id}, project={project_id[:8]}...): {e}"
            )

    # ── Query auto_load skills for this project ──
    try:
        skills_list = skill_repo.get_auto_load_skills(project_id)
    except Exception as e:
        logger.warning(
            f"Failed to load auto_load skills for project "
            f"{project_id[:8]}...: {e}"
        )
        return system_prompt

    if not skills_list:
        return system_prompt

    # ── Format skills as prompt sections ──
    sections: list[str] = []
    for skill in skills_list:
        content = (skill.content or "").strip()
        if content:
            sections.append(content)

    if not sections:
        return system_prompt

    auto_load_section = (
        f"\n---\n\n## Auto-Loaded Skills (Evolvable)\n\n"
        f"These foundational skills are always available. They evolve "
        f"over time via feedback and A/B testing.\n\n"
        + "\n\n---\n\n".join(sections)
    )

    logger.info(
        f"Appended {len(skills_list)} auto_load skills to prompt "
        f"(agent={agent_id}, project={project_id[:8]}...)"
    )
    return system_prompt + auto_load_section
```

### 5.2 Why clone_service.ensure_auto_load_skills_sync() is Needed Here

The `ensure_auto_load_skills_sync()` method (from Phase 4) clones any missing auto_load skills from `skill_bank` to the `skills` table for this project. Without it, the first spawn in a project would have no auto_load skills (they haven't been cloned yet).

The sync method is **idempotent** — if skills already exist, it returns immediately. So the per-spawn call is a fast no-op after the first clone.

### 5.3 Call Site Wiring — Spawn Path

**File**: `daemon/services/instance_lifecycle.py:~875` (after `append_user_language`)

```python
        # Append user language preference (post-cache; does not invalidate PromptCache)
        user_language = get_language_preference(project_repository)
        system_prompt = append_user_language(system_prompt, user_language)

        # Append auto_load skills (post-cache; does not invalidate PromptCache).
        # Queries project-scoped skills where auto_load=true and appends
        # them to the prompt. Triggers clone-on-miss to ensure skills
        # exist in project scope before querying.
        system_prompt = append_auto_load_skills(
            system_prompt,
            agent_id=resolved_agent_id,
            project_id=project_id,
            manager=self._manager,
        )

        # Create tools with this manager reference
```

The `project_id` is already available in the spawn path (parameter to `spawn_instance_with_mcp` at line ~808: `project_id = normalize_project_id(project_id)`).

### 5.4 Call Site Wiring — Restore Path

**File**: `daemon/services/instance_lifecycle.py:~2148` (after `append_user_language`)

```python
        # Append user language preference (post-cache; does not invalidate PromptCache)
        user_language = get_language_preference(project_repository)
        system_prompt = append_user_language(system_prompt, user_language)

        # Append auto_load skills (post-cache; does not invalidate PromptCache).
        # Uses meta.project_id for the project scope.
        system_prompt = append_auto_load_skills(
            system_prompt,
            agent_id=resolved_agent_id,
            project_id=meta.project_id,
            manager=self._manager,
        )

        # Create tools with this manager reference
```

`meta.project_id` is available on the `Instance` model (`daemon/repositories/instance/models.py:52`).

### 5.5 What Does NOT Change

- `compose_system_prompt()` — **unchanged**. No new parameter.
- `load_and_cache_prompt()` — **unchanged**. No new parameter.
- `PromptCache` — **unchanged**. No project_id in cache key.
- `daemon/loader.py` — **no changes at all**.

This is the key advantage of the post-cache pattern: zero risk to the existing caching infrastructure.

### 5.6 Fallback Behavior Matrix

| Condition | Behavior |
|-----------|----------|
| `project_id` is None | Return prompt unchanged |
| `skill_repo` is None (skill_evolution not configured) | Return prompt unchanged |
| `skill_clone_service` is None | Skip clone, query existing skills only |
| Clone fails (DB error, etc.) | Log warning, query existing skills only |
| No auto_load skills in DB | Return prompt unchanged |
| Skills found | Append formatted section |

## Key Files

- `daemon/services/instance_lifecycle.py` — new `append_auto_load_skills()` + two call sites
- `daemon/services/skill_clone_service.py` — `ensure_auto_load_skills_sync()` (from P4)
- `daemon/repositories/skill/repository.py` — `get_auto_load_skills()` (from P2)

## Constraints

- `compose_system_prompt()` and `PromptCache` MUST NOT be modified — all auto_load content is post-cache
- The append function MUST be graceful — any DB/clone failure returns the original prompt unchanged
- auto_load skills are **project-scoped** — the function receives project_id explicitly
- The function runs on **every spawn** and **every restore** — must be fast (clone is idempotent no-op after first run; DB query is indexed)
- The `manager` parameter is passed via `self._manager` from the InstanceLifecycleService

## Test Strategy

**File**: `tests/unit/test_append_auto_load_skills.py` (NEW)

Test cases:
1. **No project_id** → returns prompt unchanged
2. **skill_repo is None** → returns prompt unchanged (skill_evolution not configured)
3. **No skills in DB** → returns prompt unchanged
4. **Skills found** → prompt has "## Auto-Loaded Skills" section with skill content
5. **Clone triggers before query** → verify `ensure_auto_load_skills_sync` was called
6. **Clone fails** → prompt still returned with whatever skills exist
7. **DB query fails** → returns prompt unchanged with warning logged
8. **Multiple skills** → all skills concatenated with `\n\n---\n\n` separator
9. **Empty content skill** → skipped (not included in section)

## Deliverables

- [ ] `append_auto_load_skills()` function exists in `instance_lifecycle.py`
- [ ] Spawn path call site wired (after `append_user_language`)
- [ ] Restore path call site wired (after `append_user_language`, uses `meta.project_id`)
- [ ] Clone-on-miss triggered before query
- [ ] `compose_system_prompt()` and `PromptCache` unchanged
- [ ] All fallback paths return prompt unchanged gracefully
- [ ] Unit tests pass
