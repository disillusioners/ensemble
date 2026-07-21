# Scope Decision: Instance UI Prefs (Pin + Color Tag)

**Branch:** `feature/instance-ui-pins-tags`
**Date:** 2026-07-22

## Blast Radius Assessment
- **Change shape:** Small / isolated
- **Files touched:** New `instance_ui_prefs` table + new repository + 2 new API endpoints + FE InstancePrefsService + instance-list component
- **Modules:** Instance UI prefs feature only (one bounded feature)
- **Architecture impact:** None — follows NEW_TABLE_CREATION_PATTERN, UI-vs-data isolation pattern (separate table, merge at router layer only, `Instance.to_dict()` untouched)

## Scope Decision
**REDUCED scope.** Full suite NOT warranted.
- Change is a self-contained feature in one bounded area
- No cross-module refactor, no architecture change
- Running the full 173-pack suite would burn ~hours for a non-architecture feature

## Packs to Run (focused)
1. `instance_ui_prefs_repo_unit_test` — existing repo tests (`tests/repositories/test_instance_ui_prefs.py`)
2. `instance_hard_delete_unit_test` — existing (`tests/test_instance_hard_delete.py`) — mentioned in task as dependency-adjacent
3. `instance_ui_prefs_api_integration_test` — NEW: API endpoints via httpx ASGITransport (8 scenarios)
4. `instance_ui_prefs_insulation_check` — NEW: verify `Instance.to_dict()` excludes UI prefs
5. `frontend_instance_ui_build_test` — `ng build` compilation check

## Packs Skipped
All other packs (core daemon, job queue, skill evolution, etc.) — no changed files in those modules.
