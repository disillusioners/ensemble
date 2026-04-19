# Internal Source Log Level Fix — Testing Notes

## Date: 2026-04-19

## What was tested
Fix in `daemon/sources/dispatcher.py` that changes log level from ERROR to DEBUG for internal sources (source_id starting with `internal_`) when no adapter is found.

## Key findings
- Fix applies to both `dispatch_completed()` and `dispatch_message()` methods
- `startswith("internal_")` is the check used — prefix match only
- Sources containing "internal" but NOT starting with it still get ERROR logs (correct)
- Source ID exactly `"internal_"` (prefix only, no suffix) → DEBUG (correct)

## Tests added
- 12 new tests in `TestInternalSourceLogLevels` class in `tests/test_sources_dispatcher.py`
- Tests use `caplog` fixture to verify exact log levels
- Tests cover both dispatch_completed and dispatch_message paths
- Edge cases: exact prefix, non-prefix internal, valid adapter

## Quick fix applied
- `tests/test_api.py`: Version assertion updated from "0.1.0" to "0.1.1"
- Commit: `611ddcb`
