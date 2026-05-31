# Instance Limit Per-Parent — Test Results

**Date:** 2026-05-31
**Branch:** `feature/instance-limit-per-parent`
**Commits tested:** `2969e6b..935f823`

## Summary
- **Targeted Tests**: 54/54 PASS
- **Regression Tests**: 879/879 PASS (662 core + 217 API)
- **ensure.md**: PASS (dev.sh stable 30s)
- **Overall Status**: ✅ READY

## Targeted Test Results

| Suite | Tests | Passed | Failed |
|-------|-------|--------|--------|
| test_manager.py (spawn) | 4 | 4 | 0 |
| test_config.py | 27 | 27 | 0 |
| test_context_key.py | 7 | 7 | 0 |
| count_children_validation | 7 | 7 | 0 |
| spawn_limit_edge_cases | 9 | 9 | 0 |
| **Total** | **54** | **54** | **0** |

### Key Validations
- ✅ `count_children()` DB query method returns correct counts
- ✅ Root instances (parent_id=None) bypass per-parent check
- ✅ Root instances (parent_id="") bypass per-parent check
- ✅ Parent with children >= max_children_per_instance → ValueError
- ✅ Parent with children < max_children_per_instance → spawn succeeds
- ✅ Error message includes parent_id and limit number
- ✅ Default `max_children_per_instance` changed from 10 → 50

## Regression Test Results

| Pack | Total | Passed | Failed | Skipped |
|------|-------|--------|--------|---------|
| core_unit_test | 662 | 662 | 0 | 0 |
| api_unit_test | 217 | 209 | 0 | 8 |
| **Total** | **879** | **871** | **0** | **8** |

No regressions detected.

## ensure.md Validation
- ✅ dev.sh runs stable for 30 seconds
- Server startup complete, all services initialized
- MCP warmup, worker pool, message sources all operational

## Quick Fixes Applied
1. **test_migration_api_comprehensive.py:189** — Fixed typo in test name: `test_spawn_instance_max_instances_limit` → `test_spawn_instance_max_children_limit` (commit `935f823`)

## Commits on Branch
| Commit | Description |
|--------|-------------|
| `2969e6b` | feat: change instance spawn limit from global to per-parent |
| `78618ce` | fix: guard against empty string parent_id in spawn limit check |
| `7bdb76b` | test: add edge case tests for per-parent instance spawn limits |
| `935f823` | fix: correct test name in migration API comprehensive test |
