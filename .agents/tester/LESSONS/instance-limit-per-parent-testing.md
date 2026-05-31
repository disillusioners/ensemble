# Instance Limit Per-Parent — Testing Lessons

## Architecture
- `count_children(parent_id)` is a DB query on `instance_hierarchy` table, NOT in-memory `parent.children` list
- Root instances (parent_id=None or "") bypass the per-parent limit check via `if parent_id:` truthy guard
- Global `max_instances` config field is kept but unused — no deprecation warning

## Testing Pattern
- Test spawn limits by mocking `instance_repository.count_children.return_value = N`
- Edge cases to always test: None parent_id, empty string parent_id, exactly at limit, below limit
- Config default change (10→50) validated via `test_config.py` default assertion

## Quick Fix: Test Name Typo
- File: `tests/test_migration_api_comprehensive.py:189`
- Old: `test_spawn_instance_max_instances_limit` (referenced removed global limit)
- New: `test_spawn_instance_max_children_limit` (matches per-parent logic)
- Commit: `935f823`
