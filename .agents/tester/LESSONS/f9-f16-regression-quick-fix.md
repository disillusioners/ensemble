# F9/F16 Regression Quick Fix — 2026-07-01

## Context
Testing F9+F16 fixes on branch `feature/f9-f16-fixes` (commits `7606a93e`, `bab4df6c`).

## Regression Found
**Test**: `tests/job_queue/test_jober_watch_integration.py::TestReconcileTerminalWatches::test_reconcile_terminal_jobs`

**Confirmed**: passes on `7606a93e~1` (pre-fix), fails on `7606a93e` (regression introduced by F16 fix).

## Root Cause
`_derive_legacy_status` accepted any truthy `terminal_reason`:
```python
if admission_state == "done" and terminal_reason:  # BUG: truthy check
```

When a test passes a MagicMock job object, the `terminal_reason` attribute auto-creates a truthy non-string MagicMock value. `canonicalize_status()` returned this verbatim. The resulting non-string `legacy_status` failed the `status in watcher.watch_events` membership check in `_notify_watchers_legacy`, silently swallowing the notification (`enqueue_message` never called).

## Fix (commit `89491465`, 1-line change)
```python
# Before
if admission_state == "done" and terminal_reason:
# After
if admission_state == "done" and terminal_reason in _STATUS_CANONICAL_MAP:
```

Gates the discriminator on canonical-map membership. Non-string truthy values now fall through to the lossy `done → completed` map fallback, preserving pre-F16 behavior for unknown discriminators.

## Lesson
**Always gate string-based discriminators on set membership, not truthiness.** Truthy checks (`if x:`) pass for non-string objects (MagicMock, lists, dicts) but produce incorrect downstream behavior when the value is used in string operations (membership checks, canonicalization).

This pattern (`in _STATUS_CANONICAL_MAP` instead of `if value:`) mirrors the defensive approach already used in the F3 canonical status fix.
