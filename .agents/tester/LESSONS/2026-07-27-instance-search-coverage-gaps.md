# LESSON: Coverage Gaps in Feature Delivery — Instance Search

**Date:** 2026-07-27
**Branch:** feature/instance-search
**Commits:** `7609c197` (backend), `7f96a108` (frontend)

## Summary

The instance search feature was delivered with repository-layer tests (42 unit tests: 20 SQLite + 22 PostgreSQL) but had **two coverage gaps** that the testing session filled:

1. **No HTTP endpoint integration test** — the full router→manager→service→repo path for `GET /api/instances?search=...` was untested. Only the repository method `list(search=...)` had tests.
2. **Zero frontend component tests** — the search box, debounce logic, clear button, and signal contracts in `instance-list.component.spec.ts` had no test coverage despite the feature being implemented.

## Root Cause

Feature commits focused on repository-layer correctness (the core escape+ILIKE logic) and frontend implementation, but the test scope stopped at the repository boundary. The API endpoint wiring (router param → service → repo) and the frontend behavior (debounce, reset, template binding) were validated manually but not captured in automated tests.

## What Was Done (This Session)

- Created `tests/test_instance_search_api.py` (17 tests) — exercises the real HTTP path via a `MagicMock` manager + real repo/service against in-memory SQLite. Mirrors the `test_instance_hard_delete.py` pattern.
- Added 29 test cases to `instance-list.component.spec.ts` — signal contracts, debounce (Jest fake timers), instant reset, template binding, ngOnDestroy cleanup.

## Recommendation for Future Feature Delivery

When a feature spans multiple layers (repository → service → router → HTTP → frontend component), each layer boundary should have at least one integration/smoke test. The repository-layer tests validate the data logic; an API-level test validates the wiring; a component test validates the UI behavior. A feature with N layers should have ≥ N-1 boundary tests.

## Commits (Test Files Only — No Feature Code Modified)

- `e29fe8a6` — `tests/test_instance_search_api.py` (API integration)
- `adbf0896` — `instance-list.component.spec.ts` (frontend component, +29 tests)
