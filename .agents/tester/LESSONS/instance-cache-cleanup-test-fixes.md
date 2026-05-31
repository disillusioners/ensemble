# Quick Fix: Instance Cache Cleanup Test Fixes

**Date**: 2026-05-31
**Branch**: feature/instance-cache-cleanup
**Commits**: `568d80e`, `757a2ee`

## Issues Found & Fixed

### 1. Two tests never executed the cleanup loop (commit `568d80e`)
- `test_cleanup_releases_expired_paused_instances`
- `test_cleanup_handles_multiple_instances`
- **Root cause**: Tests only asserted time comparison arithmetic, never called `_cleanup_cached_instances()`
- **Secondary issue**: Tests used `datetime.utcnow().isoformat()` (timezone-naive) but source uses `datetime.now(timezone.utc)` (timezone-aware). Fixed with `strftime('%Y-%m-%dT%H:%M:%S+00:00')`
- **Impact**: Tests were vacuously passing — asserted math instead of behavior

### 2. conftest.py mock injection race condition (commit `757a2ee`)
- **Root cause**: `if key not in sys.modules` guard prevented mocks from overriding modules that were imported during pytest collection phase
- **Fix**: Always inject mocks regardless of prior sys.modules presence
- **Impact**: Broader test suite more reliable

## Lessons
- When testing async background loops, always verify the method is actually CALLED, not just that the math works
- Timezone-aware vs naive datetime is a common gotcha in TTL/expiry tests — match the source code's timezone format exactly
- conftest.py mock injection ordering matters when pytest collects modules before fixtures run
