# Lesson: Coverage Gaps in pinned-sort test pack (FALSE-vs-NULL bugfix)

**Date:** 2026-07-26
**Branch:** `fix/pinned-sort-false-vs-null` @ `9f9d3ec4`
**Test commit:** `72c8825c`
**Pack:** `instance_list_pinned_sort_unit_test`

## Context

The pinned-sort pack originally had 7 tests (later 8) and was reported green. A follow-up review against an explicit list of 4 requested scenarios revealed **two coverage gaps** — scenarios the existing tests did not actually exercise, even though the pack looked comprehensive.

## Gaps Found (and closed)

### Gap 1: Pagination scale (existing test used only 6 instances)
- **Existing:** `test_pinned_concentrated_on_page1` used 6 instances with limit=2 — exercises the 2-page boundary but not a realistic multi-page (3+) workload.
- **Risk:** a 2-page assertion can pass even if the `offset/limit` interaction with the CASE tier mis-places a pinned instance on page 2 in a 3+ page dataset.
- **Fix:** Added `test_pagination_10plus_false_and_null_unpinned_on_page1` — 12 instances / 3 pages (limit=5), mixing FALSE and NULL unpinned states. Asserts pinned concentration on page 1 AND that newer FALSE/NULL unpinned instances appear on page 1 ahead of older ones.

### Gap 2: FALSE-vs-NULL tiebreak used distinct `created_at` values
- **Existing:** `test_explicit_false_treated_same_as_null_prefs` used different `created_at` timestamps, so it validated "FALSE and NULL are the same tier" but never forced the **final tiebreaker** (`instance_id ASC`) to fire when `created_at` was identical.
- **Risk:** the 4th-tier tiebreaker is what guarantees pagination determinism when timestamps collide; without an explicit same-timestamp test, a regression there could go unnoticed.
- **Fix:** Added `test_false_and_null_same_created_at_tiebreak_instance_id_asc` — identical `created_at`, one `pinned=False` + one NULL; asserts `instance_id ASC` ordering.

## General Lesson

**"Green + comprehensive-looking" ≠ "covers the stated scenarios."** When a requester enumerates specific scenarios, map each one to a concrete test name (existing or to-add) rather than trusting a test count or class name. Two common blind spots:

1. **Scale assumptions** — a test that passes at N=6 may not exercise the same code path at N=12 (pagination `offset` math, tier boundary conditions).
2. **Tiebreaker coverage** — tests that distinguish two values via a higher-precedence sort key may never exercise the final tiebreaker. Always include at least one case where all higher-precedence keys are equal, forcing the last tier to decide.

## Follow-up

The PACKS.md row for `instance_list_pinned_sort_unit_test` has been updated to reflect 10 tests and the new commit. No production code was changed; the fix in `daemon/repositories/instance/repository.py` was verified correct via the passing tests.
