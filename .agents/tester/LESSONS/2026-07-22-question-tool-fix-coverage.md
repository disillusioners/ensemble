# LESSON: question-tool-fix test coverage gaps + quick fix pattern

**Date**: 2026-07-22
**Branch**: feature/question-tool-fix
**Commits**: d41487cf..12403635

## Context

Testing the question tool rename + PAUSED state fix. The branch renames `question` → `ask_questions` (category preserved), adds a GET endpoint for pending questions, and fixes the PAUSED → COMPLETED overwrite race.

## Quick Fix Pattern: Bare-Manager Helper Missing Attribute

**Recurring issue**: Test helpers that build a mock manager via `__new__` (skipping `__init__`) must manually seed EVERY attribute that the method under test touches. `_loop_breaker_state` is popped in the 5-path cleanup pattern (`daemon/manager.py:2178`) and was missing from `_make_cleanup_ready_manager()` in `tests/unit/test_question_graph.py`.

**Same pattern as**: `cae11e6f` (prior bare-manager helper missing attribute)

**Fix**: Add `manager._loop_breaker_state = {}` to the helper. Test code only, < 5 lines.

**Takeaway**: When a test helper uses `__new__` + manual attribute seeding, any new attribute added to the cleanup path must also be seeded. The 5-path cleanup in `_cleanup_instance_state` is a frequent source of this.

## Coverage Gaps Found

1. **New test file not in pack** — `tests/unit/services/test_question_pause_completion_guard.py` (8 tests covering the PAUSED guard logic) is NOT registered in `c2_question_deferred_pause_unit_test.sh` or any other pack. Must run ad-hoc until added.

2. **Missing category mapping test** — `test_tool_filter.py` EXPECTED_TOOL_CATEGORIES does not include `"question": ["ask_questions"]`. The category resolution works (verified via static check) but has no regression test.

3. **GET endpoint lacks API test** — The new `GET /instances/{id}/question` endpoint (returns pending question pack or null) has no dedicated backend test. Returns 404 for non-existent instance, null for no pending pack, and the pack dict when pending.
