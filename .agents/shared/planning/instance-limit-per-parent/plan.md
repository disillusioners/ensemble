# Plan: Change Instance Spawn Limit from Global to Per-Parent

## Objective
Remove the global instance count limit and increase the per-parent child limit from 10 to 50, counting children from the database (not in-memory cache).

## Scope Assessment
**SMALL** — 2 files to modify, 1 config change, clear and localized.

## Current State
- **Global limit**: `len(self._manager.instances)` ≥ `max_instances` (100) — checked in `instance_lifecycle.py:226-231`
- **Per-parent limit**: Already exists! `max_children_per_instance` (default 10) — checked in `instance_lifecycle.py:233-241`
- **Problem**: Global limit counts from in-memory dict (lazy cache, not accurate). Per-parent limit reads from `parent_meta.children` which is also lazy-loaded.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Remove global limit check | Delete lines 226-231 in `spawn_instance()` (the `current_instance_count` block) | `daemon/services/instance_lifecycle.py` |
| 2 | Make per-parent check use DB count | Replace `parent_meta.children` count with a DB query via `instance_repository.count_children(parent_id)` (new method). This is more reliable than the lazy-loaded children list. | `daemon/services/instance_lifecycle.py`, `daemon/repositories/instance/repository.py` |
| 3 | Update config defaults | Change `max_children_per_instance` default from 10 → 50. Keep `max_instances` field but document it as unused (or remove if clean). | `daemon/config.py:69` |
| 4 | Update tests | Adjust mock configs that set `max_instances=100` / `max_children_per_instance=10` — either remove `max_instances` refs or update the child limit to 50. | `tests/unit/test_*.py` (4 files) |

## Key Files
- `daemon/services/instance_lifecycle.py` — spawn_instance() limit checks at lines 226-241
- `daemon/repositories/instance/repository.py` — add `count_children()` method
- `daemon/config.py` — LimitsConfig at line 64-69

## Design Decisions

### How to count children: DB query
The `instances` dict is a lazy cache — NOT authoritative. The per-parent check should query the DB directly:

```python
# New method on instance_repository
def count_children(self, parent_id: str) -> int:
    with SQLModelSession(self.engine) as session:
        stmt = select(func.count()).select_from(InstanceHierarchy).where(
            InstanceHierarchy.parent_id == parent_id
        )
        return session.exec(stmt).one()
```

This counts ALL children (any state) — matching the requirement that terminal children still count toward the limit.

### Edge cases
- **Root instances** (no parent_id): No limit check needed — they're top-level, the per-parent check is skipped when `parent_id is None`.
- **Nested children**: Only **direct** children count. The `instance_hierarchy` table stores direct parent-child pairs, so `count_children(parent_id)` naturally counts only direct children.
- **Children include terminal states**: The DB query counts all rows in `instance_hierarchy` regardless of child state — this is correct per requirements.

## Success Criteria
- [ ] Global instance limit check removed from `spawn_instance()`
- [ ] Per-parent limit uses DB query instead of in-memory children list
- [ ] Default `max_children_per_instance` = 50
- [ ] Existing tests pass with updated config

## Tracking
- Created: 2025-05-25
- Status: draft
