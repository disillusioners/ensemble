## Test Report: Job List Sort Order Fix
Date: 2026-05-29T01:12:51+07:00
Session: ensemble/sort-order-test (ses_1903d1a04ffeHob4ClENkHfHuv)

### Summary
- New Tests: 16/16 PASS
- Job Queue Suite: 625+ passed (1 pre-existing port-in-use failure, unrelated)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- Quick Fixes Applied: 0
- Regressions: 0

### Bug Tested
Job list sorted incorrectly on "All Jobs" view — priority-first sorting caused older high-priority jobs to appear before newer lower-priority jobs.

### Fix Verified
In `daemon/repositories/job_queue/repository.py`, the `list()` method ORDER BY changed from `priority DESC, created_at DESC` to `created_at DESC, priority DESC`.

### New Test File
`tests/job_queue/test_job_list_sort_order.py` — 16 tests across 4 test classes:

#### Part 1: `list()` Sort Order (7 tests)
| Test | What It Verifies |
|------|-----------------|
| `test_list_returns_newest_first_when_same_priority` | Newest-first when priorities equal |
| `test_list_uses_priority_as_tiebreaker_when_same_timestamp` | Higher priority first within same timestamp |
| `test_list_newest_takes_precedence_over_priority` | **KEY TEST**: Newest job always first even if lower priority |
| `test_list_without_queue_filter_all_jobs_view` | Sort correct across multiple queues |
| `test_list_same_timestamp_same_priority_stable_ordering` | Edge case: identical timestamps+priority |
| `test_list_single_job` | Edge case: single job |
| `test_list_empty_result` | Edge case: empty result |

#### Part 2: `list_by_queue()` UNCHANGED (2 tests)
| Test | What It Verifies |
|------|-----------------|
| `test_list_by_queue_priority_takes_precedence` | Priority-first sort preserved |
| `test_list_by_queue_same_priority_newest_first` | Newest tiebreaker preserved |

#### Part 3: `list_pending_by_queue()` UNCHANGED (2 tests)
| Test | What It Verifies |
|------|-----------------|
| `test_list_pending_by_queue_priority_takes_precedence` | Priority-first sort preserved |
| `test_list_pending_by_queue_same_priority_oldest_first` | FIFO within same priority preserved |

#### Part 4: Regression Tests (5 tests)
| Test | What It Verifies |
|------|-----------------|
| `test_list_excludes_soft_deleted_jobs` | Soft delete still works |
| `test_list_filters_by_status` | Status filtering still works |
| `test_list_filters_by_queue_id` | Queue filtering still works |
| `test_list_by_queue_excludes_deleted_jobs` | list_by_queue soft delete |
| `test_list_pending_by_queue_only_returns_pending` | list_pending status filter |

### ensure.md Validation
- ✅ dev.sh runs stable for 30s (exit code 124 = timeout = PASS)
- Server started, all services initialized, graceful shutdown

### Commit
```
2fa7a71 test: add sort order tests for job list repository
```

---

### Overall Status
- Unit Tests: ✅ PASS (16/16 new, 625+ existing)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- **Testing Complete**: ✅ READY
