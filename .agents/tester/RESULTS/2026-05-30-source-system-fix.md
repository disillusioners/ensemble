# Source System Fix Test Results

**Date**: 2026-05-30
**Branch**: `feature/source-system-fix`
**Commits tested**: `b8bc9c4`, `6e8d62c`
**Test commit**: `19e3fa0`

## What Was Tested

4 bug fixes in the source system (`daemon/sources/`):

1. **mapper.py** — `spawn_instance_with_mcp()` now receives a generated UUID as `instance_id` (was missing, crashing all source instance creation)
2. **registry.py** — `images` and `metadata` from incoming messages are now forwarded to `enqueue_message()` (were dropped)
3. **registry.py** — Priority is now configurable via metadata with safe int coercion (was hardcoded to 1)
4. **base.py** — `IncomingMessage` dataclass now has `images: list[str] | None = None` field

## Test Results

### New Tests: 29/29 PASS

| Test Class | Tests | Coverage |
|------------|-------|----------|
| `TestMapperInstanceIdFix` | 4 | UUID generation in `spawn_instance_with_mcp(instance_id=...)` |
| `TestMetadataForwarding` | 4 | `images` and `metadata` forwarded to `enqueue_message()` |
| `TestPriorityExtraction` | 8 | Priority extraction from metadata with int coercion |
| `TestIncomingMessageImagesField` | 6 | `images: list[str] \| None` field in dataclass |
| `TestEdgeCases` | 5 | Cached mappings, no-crash scenarios, full pipeline |

### Regression: 137/137 PASS (0 regressions)

Existing sources test pack (circuit breaker, dispatcher, mapper, persistence, rate limiter, registry) — all passing.

### ensure.md: Not run (source-only changes, no server startup impact)

## Quick Fixes Applied

The opencode session applied one additional fix during test writing:
- Fixed Python dataclass field ordering in `daemon/sources/base.py` where `images` (with default) was placed before `source_id` (without default).

## Overall Status: ✅ READY (29 new tests, 137 regression tests, 0 regressions)
