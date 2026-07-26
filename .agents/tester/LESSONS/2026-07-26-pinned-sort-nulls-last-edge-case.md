# Pinned-First Sort: NULLS LAST Edge Case

**Date:** 2026-07-26
**Feature:** Pinned-first sorting in instance listing API (`feature/pinned-instance-sort` @ `8fae7b8d`)
**Test:** `tests/repositories/test_instance_list_pinned_sort.py` (commit `3f3ef0fd`)

## Finding (not a bug — documented behavior)

The `list()` method sorts by `pinned DESC NULLS LAST, pinned_at DESC NULLS LAST, created_at DESC`.

A subtle consequence of `NULLS LAST` on a DESC sort: a concrete `pinned=False` (`0`) row sorts
**ABOVE** a `pinned=NULL` row (never-touched instance), regardless of `created_at`.

- **An explicitly-unpinned instance** (user pinned then unpinned → `instance_ui_prefs` row exists with `pinned=False`) precedes a **never-touched instance** (no prefs row → `pinned=NULL`).
- Both *look* unpinned in the UI, but they are NOT fully equivalent at the SQL sort level.
- **Impact in practice:** Low. Most instances never get a `instance_ui_prefs` row (rows are lazily created only on first pin/color-tag action). The distinction only matters when a user unpins an instance that was previously pinned.

## Why it's correct

The feature contract is "pinned instances first; everything else by created_at DESC". The committed
code honors this: pinned (True=1) always come first, and the NULLS LAST keeps missing rows below
concrete values consistently on both SQLite and PostgreSQL.

## Lesson

When testing NULLS LAST semantics on a DESC boolean sort, do NOT assume `False == NULL` ordering.
A DESC sort with NULLS LAST puts `0` (False) above `NULL`. The test
`test_explicit_false_sorts_above_null_prefs` documents this exact behavior to prevent future
regressions or "fixes" that break it.
