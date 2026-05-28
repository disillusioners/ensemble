# READY Messages Blocking Completion Report — Lessons

## Bug (2026-05-28)
- **File**: `daemon/services/child_reports.py` — `_should_send_completion_report()`
- **Root cause**: Function counted `READY` messages as "pending", blocking completion report after resume
- **Impact**: After pause/resume, child's original message stays in READY state → completion report skipped → parent never notified
- **Fix**: Only `PROCESSING` and `RETRYING` statuses block completion report. Removed READY from the check.

## Key Insight
After pause/resume, messages that haven't been processed yet are in READY state. These should NOT block completion reports — only actively processing (PROCESSING) or retrying (RETRYING) messages indicate work in progress.

## Test Patterns
- Test file: `tests/unit/test_ready_message_completion_report.py` (12 tests)
- Pattern: Create child instance with specific message statuses, verify `_should_send_completion_report` behavior
- Use mock repos and proper message status enums
- Test both positive (should proceed) and negative (should skip) cases
- Verify diagnostic logging strings for each skip path
