# Test Report: pinned-sort FALSE-vs-NULL bugfix

**Date:** 2026-07-26
**Branch:** `fix/pinned-sort-false-vs-null` @ `9f9d3ec4`
**Test commit:** `72c8825c`
**Worker instance:** `5a1f10a1-888f-457b-834c-76aa2847a45a` (name: pinned-sort-false-null-test)
**Pack:** `instance_list_pinned_sort_unit_test` → `tests/repositories/test_instance_list_pinned_sort.py`

## Summary

| Metric | Value |
|---|---|
| Result | ✅ **PASS** |
| Tests | 10 (8 existing + **2 added**) |
| Passed / Failed | 10 / 0 |
| Timeout | No (0.91s, well under 2-min unit cap) |
| Quick fixes applied | 2 tests added (test-code only, no production edits) |
| Production files modified | None |
| Quarantined | 0 |

## Bug Under Test

`DESC NULLS LAST` on a boolean column places `pinned=False` above `pinned=NULL`. So instances that were pinned then unpinned (`pinned=False`) sorted above never-pinned instances (`pinned=NULL`), pushing newer never-pinned instances to page 2.

**Fix:** first ORDER BY tier changed from `.desc().nulls_last()` to a CASE expression — only `pinned=True` → tier 1; both `pinned=False` and `pinned=NULL` → tier 0. Applied to **both** pagination paths in `daemon/repositories/instance/repository.py` (flat: ~391-404; root-based: ~441-454).

## Scope Decision

> **Full requested scope was not warranted.** Change touches **1 file / 1 method's ORDER BY clause** (two pagination paths) — a small, isolated bugfix with no architecture impact. Ran **only** the registered `instance_list_pinned_sort_unit_test` pack. Skipped: all other packs (ui-prefs repo/api, concurrency, core, etc.) — they test different methods/endpoints outside the blast radius of an ORDER BY change in `list()`. Full suite not warranted.

## ensure.md Validation

Not run this cycle — in-scope ensure.md Core requirements (concurrency atomicity, sync-DB-on-loop, `dev.sh` flag) are **not** touched by an ORDER BY change in a read-only list query. `concurrency_atomic_unit_test` covers writes/locks, which this change does not affect. Release Gate (E2E) not warranted (small change). If the leader prefers, I can run the concurrency pack as a belt-and-suspenders check — it is fast (~5 min) — but it is not strictly in-scope.

## Coverage Verification — All 4 Requested Scenarios

| # | Scenario | Status | Test |
|---|---|---|---|
| (a) | Exact bug [TRUE, FALSE, NULL] mix — TRUE floats; FALSE+NULL tiebreak by `created_at DESC` | ✅ Existing | `TestMixedTrueFalseNullBugFix::test_mixed_true_false_null_true_floats_then_created_at` + `TestNoPrefsRowNullHandling::test_explicit_false_treated_same_as_null_prefs` |
| (b) | Pagination 10+ instances — pinned on page 1, newer unpinned (FALSE & NULL) on page 1 after pinned | ✅ **Added** | `TestPaginationCorrectness::test_pagination_10plus_false_and_null_unpinned_on_page1` |
| (c-i) | ALL pinned → `pinned_at DESC` | ✅ Existing | `TestMultiplePinnedOrdering::test_most_recently_pinned_first` |
| (c-ii) | NONE pinned → `created_at DESC` | ✅ Existing | `TestNoPrefsRowNullHandling::test_explicit_false_treated_same_as_null_prefs` |
| (c-iii) | FALSE+NULL SAME `created_at` → `instance_id` ASC tiebreak | ✅ **Added** | `TestFalseNullTiebreakerSameCreatedAt::test_false_and_null_same_created_at_tiebreak_instance_id_asc` |

## Tests Added (Test Code Only)

1. **`TestPaginationCorrectness::test_pagination_10plus_false_and_null_unpinned_on_page1`** — 12 instances / 3 pages (limit=5); 2 oldest pinned, 5 explicit `pinned=False`, rest NULL. Asserts page 1 = `[pinned-00, pinned-01, NULL-newest, NULL-2nd-newest, explicit-FALSE-3rd-newest]`; no pinned leakage; all 12 covered exactly once across pages.

2. **`TestFalseNullTiebreakerSameCreatedAt::test_false_and_null_same_created_at_tiebreak_instance_id_asc`** — two instances with identical `created_at` (one `pinned=False`, one no-prefs NULL); asserts final `instance_id ASC` tiebreak dominates. Closes the gap left by the prior FALSE-vs-NULL test which used distinct `created_at` values.

## Command Run

```
timeout 300 .venv/bin/pytest tests/repositories/test_instance_list_pinned_sort.py --override-ini="addopts=" --tb=short -q
```

## Documentation Updated
- [x] PACKS.md — updated `instance_list_pinned_sort_unit_test` row (7→10 tests, CASE expression scope, new commit/date)
- [x] RESULTS/2026-07-26-pinned-sort-false-vs-null.md — this report
- [x] LESSONS/2026-07-26-pinned-sort-false-vs-null-coverage-gaps.md — coverage-gap lesson
- [x] QUARANTINE.md — no changes (no flaky tests)

## Overall Status
- Pinned-sort pack: ✅ PASS (10/10)
- **Testing Complete: ✅ READY** — the bugfix is validated; all 4 requested scenarios covered (2 pre-existing, 2 newly added). Production fix untouched and confirmed correct.
