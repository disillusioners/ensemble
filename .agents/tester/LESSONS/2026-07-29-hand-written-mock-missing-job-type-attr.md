# Mock Gotcha Variant: Hand-Written Mock Class Missing New Attribute

Date: 2026-07-29
Branch: `bugfix/deferred-version-tag-fixes`
Commit: `6cdaf679`
Found by: Worker (retest-jobqueue, instance 2b18b379)

## Root Cause
`job_processor.py:741` checks `proc_job.job_type == "message"` as part of the ACTIVE-admission skip guard. The `MockJob` test fixture in `tests/job_queue/test_defer_queue.py` is a **hand-written mock class** (NOT a MagicMock) that never gained the `job_type` attribute when this guard was added.

When production code accesses `proc_job.job_type`, the hand-written `MockJob` raises `AttributeError: 'MockJob' object has no attribute 'job_type'`.

## Two Mock Gotcha Variants in This Codebase

1. **MagicMock auto-attr gotcha** (5 prior occurrences): `MagicMock.get_version()` returns truthy by default, bypassing `get_version() or get_resolved()` fallback. Fix: `mock.get_version.return_value = None`.
2. **Hand-written mock class missing attr** (this case): A `MockJob` class manually defined with specific attributes misses a newly-referenced production attribute. Fix: add the missing attribute to the mock class `__init__`.

Both are "test mock didn't track production changes" bugs, but the fix patterns differ.

## Fix
Added `self.job_type = "task"` to `MockJob.__init__` (matching `JobItem.job_type` default at `models.py:364`).

## Before/After
- Before: 3 tests failed (`AttributeError: 'MockJob' object has no attribute 'job_type'`)
- After: 1463 passed, 38 skipped, 0 failures in ~40s
