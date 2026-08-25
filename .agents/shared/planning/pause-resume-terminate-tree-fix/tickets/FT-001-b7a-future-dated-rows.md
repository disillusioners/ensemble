# FT-001 — B7(a): Future-dated work rows (+7h)

**Source spec:** `phase3-plan.md` §Out of Scope → Ticket FT-001 (Task 3.10 deliverable)
**Filed:** 2026-08-25 (P3 documentation pass) · **Effort class:** SMALL–MEDIUM
**Status:** OPEN (deferred to future batch)

---

## Repro

3 work rows observed with `created_at` or `paused_at` stamped `2026-08-25T00:0x+00:00` (local clock +07 with UTC offset applied). Likely from `datetime.now()` without `timezone.utc`.

## Suspected sites (not located in research scan)

- All `datetime.now()` call sites in work insertion paths: grep `daemon/repositories/work/` and `daemon/services/work_insertion*` for `datetime.now()` (NOT `.utcnow()` and NOT `datetime.now(timezone.utc)`).
- Cross-check: `paused_at` stamping at `daemon/services/instance_lifecycle.py:2061` is correct (`datetime.now(timezone.utc)`), so the leak is in a DIFFERENT insert path.

## Effort class

SMALL–MEDIUM (grep + 1–2 line fix per site + unit test).

## Recommended approach

- Add a `created_at = :now_utc` parameter pattern to ALL work insertion paths (force UTC).
- OR add a `pytest --strict-timezone` static check that fails any `datetime.now()` without `timezone.utc` in work/job/task write paths.
- Unit test: insert work row, assert `created_at` is within 5s of `datetime.now(timezone.utc)` regardless of system TZ.
