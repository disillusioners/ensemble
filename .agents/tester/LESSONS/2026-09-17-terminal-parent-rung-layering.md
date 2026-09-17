# 2026-09-17 — Terminal-parent rung layering: TERMINATED ≠ db_terminal_parent

**Context**: verification gate for `d5e6adee` (Stage-0 child_report_check strand fix), while closing the terminal-suppression coverage gap (only COMPLETED was pinned; reviewer/tidier flagged ERROR/FAILED).

**Lesson**: when writing terminal-parent suppression tests in `daemon/services/child_reports.py`, do NOT assume all four terminal statuses flow through the same rung.

- `db_dead_parent` (`child_reports.py:3100`, `parent is None or status == TERMINATED`) fires BEFORE `db_terminal_parent` (`:3241-3249` = COMPLETED/ERROR/FAILED + a defensive TERMINATED leg).
- A TERMINATED parent therefore asserts: outcome `dead_parent_skip` (not `regular_child_completed`), report row persisted **FAILED** (not READY), **no** PROCESS_REPORT task, log `reason=dead_parent` (not `terminal_parent`) — pinned by pre-existing `test_dead_parent_skips_note_insert`.
- Parametrized terminal-status tests must branch assertions per rung; uniform assertions across the four statuses would be wrong on TERMINATED (fail misleadingly or pass vacuously).
- The TERMINATED leg of the `db_terminal_parent` tuple is **defensive redundancy** — deleting it is NOT catchable via a TERMINATED-parent test (suppression happens upstream at dead_parent). Production comment `:3240` states this verbatim; grep it before writing terminal-status assertions.

**Applied in**: `tests/unit/test_child_terminal_contradiction.py::test_terminal_parent_suppresses_note_mint` (commit `9bff3629`) — common suppression contract asserted for all four; rung shape asserted per status. See `RESULTS/2026-09-17-child-report-check-strand-verification.md` §4.
