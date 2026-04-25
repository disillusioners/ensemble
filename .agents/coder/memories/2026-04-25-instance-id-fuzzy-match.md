# Instance ID Fuzzy Matching Improvement

**Date:** 2026-04-25
**Commit:** bb54800
**Branch:** fix/instance-id-fuzzy-match

## What Changed
- Increased `max_distance` from 2 → 7 for instance ID fuzzy matching (Levenshtein edit distance)
- Applied fuzzy matching to ALL tools accepting `instance_id`: `send_message`, `terminate_instance`, `get_instance_info`
- Extracted shared `_resolve_instance_id()` helper in `daemon/tools/instance.py` for DRY pattern
- Limited search scope to most recent 50 instances (was 100)
- Improved error messages with helpful suggestions

## Key Files
- `daemon/utils.py` — `edit_distance()` and `find_near_instance()` functions
- `daemon/manager.py` — `find_near_instance()` method (delegates to utils)
- `daemon/tools/instance.py` — `_resolve_instance_id()` helper + all tools
- `tests/unit/test_find_near_instance.py` — 22 tests

## Review Fix (commit 71954ad)
- Changed `find_near_instance()` to return `list[str]` instead of `str | None` — handles multiple-match ambiguity
- Removed unused `operation` parameter from `_resolve_instance_id()`
- Added input validation for empty/None instance_id
- Fixed terminate_instance to return `{"error": ..., "terminated": False}` instead of bare `False`
- Added `TestResolveInstanceId` with 6 test cases
- 624 unit tests all passing

## Architecture Notes
- The `_resolve_instance_id()` helper tries exact match first (fast path), only falls back to fuzzy on `KeyError`
- The manager method gets recent instances from repository, passes to utils function
- The utils function iterates instances, calculates edit distance, returns first match within threshold
