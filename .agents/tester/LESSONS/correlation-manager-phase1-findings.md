# CorrelationManager Phase 1 — Testing Findings

## Date: 2026-06-16
## Branch: feature/correlation-manager (commits 78881a99, 888def68)

## Key Finding: api.py Size Limit
- CM lifecycle hooks (startup/shutdown) added ~71 lines to `daemon/api.py`
- This pushed it past the 700-line soft cap enforced by `test_api_module_is_small`
- **Fix**: Extracted lifecycle into `init_correlation_manager()` and `shutdown_correlation_manager()` helpers in `correlation_manager.py`
- api.py went from 715 → 688 lines
- **Lesson**: When adding lifecycle hooks to api.py, extract into helper functions to keep file size manageable

## CM Hook Guard Pattern
The shadow mode hooks follow a robust pattern:
1. `if cm is None: return` — short-circuit when CM not initialized
2. `try/except` — catch any errors, never propagate to caller
3. Belt-and-suspenders: call sites also wrap in try/except

This ensures shadow mode is truly inert — CM failures never affect control flow.

## Test Coverage Gaps (Non-blocking)
These edge cases are NOT explicitly tested but are acceptable for shadow mode:
- Register called twice for same message (idempotency)
- Message arrives after child completes (out-of-order)
- Large N=50 children per parent (stress)
- CM throws exception (only None case is tested, not exception propagation)

These should be addressed when CM moves from shadow mode to active mode.

## Shadow Mode Validation Approach
- Unit tests (`test_correlation_manager.py`): Mock-based, test internal CM logic
- Integration tests (`test_correlation_shadow.py`): Real SQLite, test CM vs DB state tracking
- Both use the same UUIDs and patterns as production code
- Shadow comparison tested with rate-limited logging (cap, interval, window reset)
