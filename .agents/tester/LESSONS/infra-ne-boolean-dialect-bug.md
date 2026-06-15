# Infra Repository Bug — $ne Boolean Operator Dialect Bug

**Date:** 2026-06-15
**Branch:** `feature/infra-info`
**Commit:** `ef47856`
**Session:** `infra-edge-cases`

## Bug Description

The `$ne` (not-equal) operator on boolean attributes returned ALL rows, defeating the operator entirely.

## Root Cause

`_json_ineq_predicate` in `daemon/repositories/infra/repository.py` had the same dialect-bug class as the previously-fixed `_json_eq_predicate` (fixed in commit `e7c40f1`), but was NOT patched.

On SQLite:
- `json_extract(attrs, '$.active')` returns `1` for `True`
- Cast to TEXT → `"1"`
- The non-numeric, non-bool code path used `str(value)` → `"True"`
- Comparison `"1" != "True"` → `True` for EVERY row
- Result: filter matches everything instead of excluding

## Fix

Added boolean branch to `_json_ineq_predicate` mirroring `_json_eq_predicate`:
- PostgreSQL: `"true"/"false"` strings
- SQLite: `"1"/"0"` strings
- Also added symmetry path for `$gt/$gte/$lt/$lte` on booleans
- ~14 lines net, single file

## Lesson

When fixing a dialect-specific bug in ONE operator (`$eq`), check ALL sibling operators (`$ne`, `$gt`, `$gte`, `$lt`, `$lte`) for the same bug class. The boolean equality fix in `_json_eq_predicate` was incomplete — `_json_ineq_predicate` had identical structure but was missed.

This is the SECOND time a boolean dialect bug was found in this file. The first (`_json_eq_predicate`) was found in code review (commit `e7c40f1`). The second (`_json_ineq_predicate`) was found in independent edge case testing.

## Test That Surfaced It

`TestBooleanAttributes::test_ne_true_excludes_true` in `tests/repositories/infra/test_edge_case_verification.py` — failed initially, passes after fix.

## Remaining Risk

Both fixes (_json_eq_predicate and _json_ineq_predicate) are only tested on SQLite. PostgreSQL behavior remains unverified against a real PG instance.
