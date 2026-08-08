# W5 Floor Padding in Report Repair Tests (2026-08-08)

## Context
Feature: unhappy path report repair in `daemon/services/child_reports.py`.

## Issue
`_is_likely_truncated_report()` has a W5 heuristic floor: `earlier_wc >= 20`. Test data in both PG parity tests (`tests/postgres/test_report_repair_pg.py`) and integration tests (`tests/integration/test_completion_report.py`) used short earlier messages (4-6 words), below the ≥20 word threshold. The heuristic returned `False`, so the function returned `last_content` directly (happy path) instead of triggering the repair/combine fallback.

## Fix
Padded earlier messages to ≥20 words in both test files.
- Commit `21381589` (PG parity)
- Commit `b711d8a9` (integration)

## Key Insight
When writing tests for `_is_likely_truncated_report()`, always ensure:
- Last message: < 5 words (triggers the "too short" detection)
- Earlier messages: ≥ 20 words (triggers the "was long before" comparison)
- Ratio: earlier_wc > 3.0 × last_wc (triggers the actual repair)

If any of these floors isn't met, the function returns `False` (happy path) and the repair path is never exercised.

## Also: PG Mock Checkpointer
The PG parity tests also needed a fix to `_build_service`: `manager._checkpointer = None` caused the `@property` `_checkpointer` on `ChildReportsService` to short-circuit and bypass the mock. Changed to `MagicMock(name="CheckpointerAdapter")`.
