# PM-Plane Sync Edge Case Coverage (2026-08-14)

## Feature
PM-Plane project sync: `daemon/clients/plane_http_client.py`, `daemon/services/plane_sync_service.py`, `daemon/tools/plane_sync.py`

## Context
Developer wrote 74 unit tests covering the happy path and primary error paths. Test leader requested edge case gap analysis for: concurrent sync, feature disabled, malformed responses, circuit breaker open, metadata update, special characters.

## Gaps Found in Original 74 Tests
1. **Malformed API responses**: Only 204 (no body) and well-formed JSON tested. Missing: 2xx with non-JSON body, null, non-dict responses, missing `id` field
2. **Circuit breaker at service layer**: Client-level tested, but service never-raises contract with breaker OPEN not tested
3. **Concurrent sync calls**: All tests sequential; no parallel call coverage
4. **Special characters**: No unicode/emoji/quotes/newlines test
5. **Metadata update with rename**: Basic update tested, but renamed-project path not

## New Tests Added (20 tests, commit `4e19a7fa`)
- `TestEdgeCaseMalformedResponses` (7) — non-JSON 2xx, null, missing id, non-dict
- `TestEdgeCaseCircuitBreakerOpenAtService` (2) — never-raises contract verified
- `TestEdgeCaseSpecialCharacters` (5) — unicode, emoji, quotes, newlines, control chars
- `TestEdgeCaseConcurrentSync` (4) — parallel calls don't crash
- `TestEdgeCaseMetadataUpdateWithNameChange` (2) — rename pushes new name to Plane

## Key Finding: Cooldown Enforcement Layer
- Cooldown (`_check_cooldown`) is enforced ONLY at the tool layer (`plane_sync.py`)
- The service layer (`PlaneSyncService.sync_project`) has no concurrency lock
- Concurrent direct service calls WILL create duplicate Plane projects
- **Risk**: Low — tool is the only public entry point
- **Recommendation**: Add per-project async lock in service for defense-in-depth
- New test `test_service_concurrent_calls_dont_crash` documents this behavior

## Result
All 94 tests (74 original + 20 new) PASS in 1.77s. No production bugs found.
