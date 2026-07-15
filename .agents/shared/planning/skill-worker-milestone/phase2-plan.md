# Phase 2: Auto_load Metrics Tracking

## Objective
Make auto_load skills visible to the metrics system by writing their skill IDs to `last_injected_skill_ids` during `append_auto_load_skills()`. This enables auto_load skills to receive usage records, trigger evaluations, and participate in A/B testing — they are currently completely invisible to all metrics.

## Coupling
- **Depends on**: None (code is independent of Phase 1)
- **Coupling type**: loose (different file than Phase 1, but C3 ordering invariant must be coordinated)
- **Shared files with other phases**: `instance_lifecycle.py` (only this phase modifies it)
- **Shared APIs/interfaces**: Writes to `INJECTED_SKILLS_METADATA_KEY` (same metadata key as Phase 1)
- **Why this coupling**: Phase 1 and Phase 2 both write to `last_injected_skill_ids` in the same message lifecycle. C3 canonical ordering must be respected: **explicit `<meta>` injection (Phase 1, REPLACE) runs FIRST; auto_load DEDUP-MERGE (Phase 2) runs SECOND**. They operate at different lifecycle stages (Phase 1: message processing in `instance_messaging.py`; Phase 2: prompt composition in `instance_lifecycle.py`), so the ordering is naturally enforced by lifecycle stage, not explicit code sequencing. The key invariant: *explicit skills are authoritative; auto_load is additive.*

## Context
- `append_auto_load_skills()` is at `instance_lifecycle.py:549-645`
- Current signature: `append_auto_load_skills(system_prompt, agent_id, project_id, manager) -> str`
- **Missing**: No `instance_id` parameter — needed to write to instance metadata
- The calling helper `_apply_post_cache_appends()` (line 648) ALREADY has `instance_id` in scope
- `last_injected_skill_ids` is read by `SkillMetricsService.record_task_completion()` at line 354 via `instance_repo.get(instance_id)` → `instance_metadata.get(INJECTED_SKILLS_METADATA_KEY)`
- The dedup-merge pattern: `list(dict.fromkeys(existing + new_ids))`
- **C3 INVARIANT**: Auto_load uses DEDUP-MERGE (additive), never REPLACE. This preserves explicit skills set by Phase 1's meta-tag injection. If a worker instance has both an explicit `<meta>` skill AND auto_load skills, both are tracked.

## C3 — Canonical Ordering with Phase 1

The two injection paths operate at **different lifecycle stages**, which naturally enforces the canonical order:

```
┌─────────────────────────────────────────────────────────────────┐
│ INSTANCE SPAWN/RESTORE (instance_lifecycle.py)                  │
│                                                                 │
│  _apply_post_cache_appends()                                    │
│    └─ append_auto_load_skills()  ← Phase 2: DEDUP-MERGE         │
│       Runs during prompt composition (BEFORE any message)       │
│       If last_injected_skill_ids already has explicit skills    │
│       (from a prior message's <meta> tag on a reused instance), │
│       auto_load IDs are MERGED in (additive, never replace).    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ MESSAGE PROCESSING (instance_messaging.py)                      │
│                                                                 │
│  _process_message_with_tracking()                               │
│    └─ <meta> explicit injection  ← Phase 1: REPLACE             │
│       Runs per-message. Replaces skill scope (with C2 finalize).│
│       Explicit skills are AUTHORITATIVE.                        │
└─────────────────────────────────────────────────────────────────┘
```

**Key**: For a fresh spawn, auto_load runs first (no existing metadata → merge starts empty). For a reused instance, if a `<meta>` tag triggered on a prior message, that explicit set is in metadata — auto_load merge preserves it.

**The DANGER case (why C3 matters)**: If auto_load used REPLACE (not merge), it would silently drop explicit skills set by Phase 1's meta-tag injection. Using DEDUP-MERGE prevents this.

## Issue 2 — Checkpoint Restore Safety

**Problem**: When an instance is restored after a crash, `_apply_post_cache_appends()` re-runs `append_auto_load_skills()` with DEDUP-MERGE. If the pre-crash state had an explicit `<meta>` REPLACE that removed auto_load skills, the restore silently re-introduces them — corrupting the REPLACE semantics.

**Fix**: Phase 1 persists `explicitly_replaced_ids` in instance metadata whenever an explicit REPLACE drops skills. Phase 2's auto_load DEDUP-MERGE reads this set and SKIPS any auto_load skill IDs that are in it.

```
Pre-crash state:
  last_injected_skill_ids = ["explicit_skill_id"]      ← from <meta> REPLACE
  explicitly_replaced_ids = ["autoload_skill_id"]      ← was dropped by REPLACE

Crash + Restore:
  _apply_post_cache_appends() → append_auto_load_skills()
    → reads explicitly_replaced_ids = ["autoload_skill_id"]
    → auto_load skills = ["autoload_skill_id"]
    → SKIP autoload_skill_id (it's in explicitly_replaced_ids)
    → DEDUP-MERGE: existing ["explicit_skill_id"] + [] = ["explicit_skill_id"]
    → ✓ REPLACE semantics preserved
```

## Tasks

### Task 2.1: Add `instance_id` + `instance_repository` to `append_auto_load_skills()`

**File**: `daemon/services/instance_lifecycle.py`

**Change signature** (line 549):
```python
# BEFORE:
def append_auto_load_skills(
    system_prompt: str,
    agent_id: str,
    project_id: str | None,
    manager: Any,
) -> str:

# AFTER:
def append_auto_load_skills(
    system_prompt: str,
    agent_id: str,
    project_id: str | None,
    manager: Any,
    instance_id: str | None = None,           # NEW
    instance_repository: Any = None,           # NEW
) -> str:
```

**Add metadata tracking** inside the function, after the skills are successfully queried and formatted (after line ~635, before the return):

```python
    # ── Track auto_load skills in instance metadata (C3: DEDUP-MERGE) ──
    # Write skill IDs to last_injected_skill_ids so the metrics
    # service can attribute usage records at task completion.
    #
    # C3 INVARIANT: Uses DEDUP-MERGE (not replace). This preserves
    # explicit skills set by Phase 1's <meta> tag injection. If both
    # an explicit skill AND auto_load skills are active, ALL are tracked.
    # Explicit skills are authoritative; auto_load is additive.
    #
    # Issue 2 FIX: Read explicitly_replaced_ids from metadata. Any
    # auto_load skill that was explicitly REPLACED via <meta> tag
    # (and thus is in this set) is SKIPPED — do not re-introduce it.
    # This preserves REPLACE semantics across checkpoint restores.
    if instance_id and instance_repository and skills_list:
        try:
            # Issue 2: Read explicitly_replaced_ids set
            _replaced_ids: set[str] = set()
            _replaced_inst = instance_repository.get(instance_id)
            if _replaced_inst is not None and _replaced_inst.instance_metadata:
                _raw_replaced = _replaced_inst.instance_metadata.get(
                    "explicitly_replaced_ids"
                ) or []
                if isinstance(_raw_replaced, list):
                    _replaced_ids = {str(x) for x in _raw_replaced if x}

            # Filter out skills that were explicitly replaced (Issue 2)
            skill_ids = [
                str(s.id)
                for s in skills_list
                if getattr(s, "id", None) and str(s.id) not in _replaced_ids
            ]
            if _replaced_ids:
                _skipped = [
                    s.name for s in skills_list
                    if getattr(s, "id", None) and str(s.id) in _replaced_ids
                ]
                if _skipped:
                    logger.info(
                        f"Auto_load skipped {len(_skipped)} explicitly-replaced "
                        f"skill(s): {_skipped} (instance={instance_id[:8]}...)"
                    )
            if skill_ids:
                # Read existing metadata (may contain explicit <meta> skills)
                inst = instance_repository.get(instance_id)
                existing: list[str] = []
                if inst is not None and inst.instance_metadata:
                    raw = inst.instance_metadata.get(
                        "last_injected_skill_ids"
                    ) or []
                    if isinstance(raw, list):
                        existing = [str(x) for x in raw if x]
                # Dedup-merge preserving order — existing (explicit) first,
                # then auto_load IDs appended
                merged = list(dict.fromkeys(existing + skill_ids))
                instance_repository.set_metadata(
                    instance_id,
                    "last_injected_skill_ids",
                    merged,
                )
                logger.info(
                    f"Tracked {len(skill_ids)} auto_load skill(s) in "
                    f"instance metadata (instance={instance_id[:8]}..., "
                    f"existing={len(existing)}, merged={len(merged)})"
                )
        except Exception as e:
            logger.warning(
                f"Failed to track auto_load skills in metadata "
                f"(instance={instance_id[:8]}...): {e}"
            )
            # Soft-fail — auto_load skills are still in the prompt,
            # just not tracked. Don't break prompt composition.
```

### Task 2.2: Update `_apply_post_cache_appends()` Call Site

**File**: `daemon/services/instance_lifecycle.py`

**Change** the `append_auto_load_skills()` call inside `_apply_post_cache_appends()` (line ~710):

```python
# BEFORE:
    return (
        append_auto_load_skills(
            system_prompt,
            agent_id=agent_id,
            project_id=project_id,
            manager=manager,
        ),
        user_language,
    )

# AFTER:
    return (
        append_auto_load_skills(
            system_prompt,
            agent_id=agent_id,
            project_id=project_id,
            manager=manager,
            instance_id=instance_id,                         # NEW
            instance_repository=instance_repository,          # NEW
        ),
        user_language,
    )
```

Both parameters are already available in `_apply_post_cache_appends()` scope (line 648: `instance_id: str` and `instance_repository: Any`).

### Task 2.3: Backward Compatibility Check

The new parameters `instance_id` and `instance_repository` default to `None`. If any other call site of `append_auto_load_skills()` exists that doesn't pass them, the function silently skips metadata tracking (existing behavior). This ensures rollback-safety.

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `daemon/services/instance_lifecycle.py` | MODIFY | Add `instance_id` + `instance_repository` params, DEDUP-MERGE metadata write (C3) |

## Constraints
- **C3**: Must use dedup-merge (not replace) — auto_load + explicit skills may coexist. Explicit skills are authoritative; auto_load is additive.
- Soft-fail — metadata write failure doesn't break prompt composition
- Only track skills that have non-empty content (consistent with the prompt append logic)
- `instance_id` and `instance_repository` are optional (backward compatible)

## Deliverables
- [ ] `append_auto_load_skills()` writes auto_load skill IDs to `last_injected_skill_ids` via DEDUP-MERGE
- [ ] `_apply_post_cache_appends()` passes `instance_id` + `instance_repository`
- [ ] Auto_load skills appear in `skill_usage_records` after task completion
- [ ] C3 invariant: existing explicit skills preserved when auto_load merges
- [ ] **Issue 2**: Auto_load skips skills in `explicitly_replaced_ids` set
- [ ] **Issue 2**: Unit test: crash after REPLACE → restore → assert replaced skill NOT re-introduced
- [ ] Unit test: auto_load skills tracked in metadata
- [ ] Unit test: C3 dedup-merge when both auto_load + explicit present
- [ ] Unit test: auto_load does NOT replace explicit skills

## Test Strategy

### Unit Tests
```python
def test_auto_load_skills_tracked_in_metadata():
    """append_auto_load_skills writes skill IDs to instance metadata."""
    # 1. Mock instance_repository with set_metadata
    # 2. Call append_auto_load_skills with instance_id + skills
    # 3. Assert set_metadata called with "last_injected_skill_ids" and skill IDs

def test_auto_load_dedup_merge():
    """C3: Auto_load dedup-merges with existing injected skills."""
    # 1. Mock instance_repository with existing ["explicit_skill_id"]
    # 2. Call append_auto_load_skills with skills ["auto_load_id"]
    # 3. Assert merged = ["explicit_skill_id", "auto_load_id"]
    # 4. Assert explicit_skill_id NOT dropped (C3 invariant)

def test_auto_load_no_instance_id_skips_tracking():
    """When instance_id is None, tracking is skipped (backward compat)."""
    # 1. Call without instance_id
    # 2. Assert set_metadata NOT called
    # 3. Assert prompt still gets skills appended
```

### Integration Test
```python
async def test_auto_load_skill_gets_usage_record():
    """Auto_load skill appears in metrics after task completes."""
    # 1. Spawn tester instance (has test-strategy auto_load)
    # 2. Send a task
    # 3. Task completes
    # 4. Assert: skill_usage_records has a row for test-strategy skill
    # 5. Assert: skill counters (total_selections, etc.) incremented

async def test_auto_load_preserves_explicit_skill():
    """C3: Auto_load merge preserves explicit <meta> skill."""
    # 1. Spawn worker (has auto_load skills in project)
    # 2. Send message with <meta>{"load_skill": "explicit-skill"}</meta>
    # 3. Assert: last_injected_skill_ids = ["explicit_skill_id"]
    # 4. Trigger restore/re-append (e.g. new session)
    # 5. Assert: last_injected_skill_ids = ["explicit_skill_id", "auto_load_id"]
    #    (auto_load merged in, explicit preserved)

async def test_auto_load_skips_explicitly_replaced_after_crash():
    """Issue 2: Restore after crash does NOT re-introduce replaced skills."""
    # 1. Worker has auto_load skills = ["autoload_a", "autoload_b"]
    # 2. Send <meta>{"load_skill": "explicit_x"}</meta>
    # 3. Assert: explicitly_replaced_ids = ["autoload_a", "autoload_b"]
    # 4. Assert: last_injected_skill_ids = ["explicit_x"]
    # 5. Simulate crash + restore (re-run _apply_post_cache_appends)
    # 6. Assert: last_injected_skill_ids STILL = ["explicit_x"]
    #    (autoload_a and autoload_b NOT re-introduced)
    # 7. Assert: explicitly_replaced_ids still = ["autoload_a", "autoload_b"]
```
