# User Language Preference Feature — Testing Findings

Date: 2026-07-12
Branch: feature/user-language-preference
Commits: 05e73dea, 0191299a, e4b5f9db, 028ec5ab, 50c6c45a, d2aac4d7

## Feature Summary
3-phase feature: (1) Backend API for language preference, (2) LangGraph integration with language detection + deferred dispatch, (3) Angular frontend settings page. Config flag `language_check_enabled` defaults to False (opt-in).

## Test Coverage
- **test_language_check.py**: 111 tests — detection (CJK, Spanish), graph node, counter, skip, config, prompt injection, append_user_language
- **test_settings_api.py**: 12 PostgreSQL-backed tests — GET/PUT API, validation, error handling
- **test_nudge_behavior.py**: 36 tests — regression (should_continue unmodified)
- **Frontend specs**: 41 tests — settings component (36) + service (5)

## Key Findings

### 1. Bug Found: Sync DB calls in settings router (FIXED)
**Root cause**: `daemon/routers/settings.py` called sync DB functions directly from async endpoint handlers:
- `get_language_preference(_project_repo)` in `async def get_language()` — blocks event loop on DB read
- `repo.set_metadata(...)` in `async def set_language()` — blocks event loop on DB write+commit

**Fix**: Wrapped both calls in `await asyncio.to_thread(...)` (commit 6ebd3f25, +9/-2 lines)
**Verification**: Re-ran settings_api (12/12 PASS) + language_check (111/111 PASS) after fix

**Lesson**: New API endpoints with async handlers must wrap ALL sync DB/repository calls in `asyncio.to_thread`. This is an ensure.md critical requirement ("No sync DB calls remain on the asyncio event loop thread"). The code review missed this — testing caught it.

### 2. Deferred Dispatch Coverage Gap
The `test_language_check.py` file (111 tests) does NOT cover paths 14-19 from the task spec:
- Deferred dispatch: wrong-language message buffered
- Buffer overwritten on retry
- Buffer dispatched at END
- Buffer dropped on CancelledError
- SSE: deferred message IDs tracked/emitted post-loop
- ainvoke path unaffected

These are integration-level concerns. If they're covered by pack-level integration tests, that's fine. If not, this is a coverage gap that should be addressed.

### 3. Settings API Injection Test Gap
The regex pattern `^[A-Za-z\u00C0-\u017F \-()]+$` is enforced by Pydantic schema, but `test_settings_api.py` only tests empty string and >100 chars for 422 — no explicit injection pattern tests (newlines, `<script>`, SQL). The `test_language_check.py` file DOES test injection payloads against the regex (10 cases), so the security invariant is covered, just not in the API test file.

### 4. Pre-existing Core Failures (10 tests)
All pre-existing, NOT caused by language feature:
- 2 tests: test_manager.py — Phase 4 dispatch refactor (commit 4eb1758a)
- 5 tests: test_memory_system.py — registry.get → registry.get_resolved mock mismatch
- 3 tests: test_project_store.py/test_queue.py — admission_state field missing on models

### 5. Pack Script Fix
`test/packs/core_unit_test.sh` used bare `pytest` from PATH, which resolved to broken system pytest (Python 3.14, ImportError). Fixed to use `.venv/bin/pytest` (commit 818b785c). This matches the pattern in `integration_test.sh`.

## Quick Fixes Applied
1. **6ebd3f25** — `fix: wrap settings router DB calls in asyncio.to_thread` (daemon/routers/settings.py, +9/-2)
2. **818b785c** — `test: fix core_unit_test.sh to use project venv pytest` (test/packs/core_unit_test.sh)

## Recommendations
- Add explicit injection-pattern tests to test_settings_api.py
- Add deferred dispatch / SSE buffering tests if not covered elsewhere
- Fix the 10 pre-existing core_unit_test failures (separate PR)
- Ensure all new async API endpoints wrap sync DB calls in asyncio.to_thread
