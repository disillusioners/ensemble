# Quick Fix: stale list_instances assertions in test_api.py

**Date:** 2026-07-29
**Branch:** bugfix/version-tag-tool-resolution
**Commit (fix):** 12d50860
**Pack:** api_unit_test

## Problem

4 tests in `tests/test_api.py` failed because their `assert_called_once_with(...)`
assertions on `manager.list_instances` did not include the `search=None` parameter
that production code now passes (added by the "instance search feature" merge).

## Affected Tests

- `test_list_instances_no_project_id_filter` (line 395)
- `test_list_instances_filter_by_project_id` (line 427)
- `test_list_instances_filter_by_nonexistent_project_id` (line 445)
- `test_list_instances_project_id_with_status_filter` (line 475)

## Root Cause

The "instance search feature" (merged into latest before this branch) added a `search`
query parameter to the `GET /api/instances` endpoint. Production code in
`daemon/routers/instances.py:289` passes `search=search` to `manager.list_instances(...)`.
The 4 stale assertions in `test_api.py` still used the old signature without `search`.

**Not caused by version-tag fix** — this is a pre-existing assertion drift from a prior
feature merge, exposed by running the api_unit_test pack.

## Fix

Updated all 4 stale `list_instances.assert_called_once_with(...)` assertions to include
`search=None` after `include_descendants=True`. Test-code only (4 lines); no production
changes.

## Pattern / Lesson

**Feature merges that change function signatures must update ALL assertion call sites.**
The instance search feature added a `search` param but missed 4 mock assertions in
test_api.py. These would have been caught by running the api pack at feature-merge time.
