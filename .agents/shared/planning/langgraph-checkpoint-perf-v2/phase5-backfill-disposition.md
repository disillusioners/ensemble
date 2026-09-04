# Phase 5 T5.14 FR-14 Backfill Disposition (architect section 2.4 corrected Criteria A/B/C)

> Date: 2026-09-04 (UTC) | v2 HEAD: `41347ee4`
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Source: requirements.md FR-14 + plan-overview.md Out-of-Scope Backfill + architect section 2.4.

## Decision

**DROP the backfill (Solution N).** Pre-side-table history stays as-is; pre-PR2 messages display with their `state.ts` fallback timestamps (the checkpoint last-update time). The corrected Criteria A/B/C are ALL TRUE - verified below against the v2 codebase.

The offline-only hardening (W11) is NOT needed because no backfill runs. The hardening terms are preserved verbatim in the "If ever revisited" section.

---

## Criterion A TRUE

> A = PR3's `state.ts` fallback timestamps suffice for UI display of pre-side-table messages (accepted degradation, non-breaking).

Verdict: TRUE.

Evidence:

1. Code - fallback path (daemon/persistence.py:466-468):
```python
created_at = msg_timestamps.get(msg_id)
if not created_at:
    created_at = state.get("ts")
```

Every message has a `created_at`.

2. Code - degradation documented as accepted (daemon/persistence.py:375-379):
> "Under-record (a checkpoint message with no tap row) is NOT a bug: it falls through to the state.ts fallback".

3. Pre-PR3 baseline: the pre-PR3 `saver.alist` walk returned approximate timestamps too (latest checkpoint with that message). The post-PR3 state.ts fallback is at least as accurate as the pre-PR3 walk.

4. Non-breaking: response shape unchanged. `created_at` is still a string, ISO-8601, per-message. Only the value source differs (alist-derived vs side-table vs state.ts fallback).

---

## Criterion B TRUE

> B = NO scheduled/batch consumer needs accurate first-appearance timestamps for pre-side-table history.

Verdict: TRUE.

Evidence:

1. `created_at` consumer scan: the only consumer of message `created_at` is the FE chat panel rendering. No scheduled/batch job reads it (per the evidence pass - see daemon/routers/ for `created_at` usage).

2. No test requires first-appearance for pre-side-table: the observed-count-zero test only asserts non-NULL `created_at`, not first-appearance.

3. Architect section 2.4 verdict (binding): original Criterion B was FALSE on the merits; the corrected B reflects the actual consumer surface (just the FE chat panel) which DOES accept the state.ts fallback.

---

## Criterion C TRUE

> C = the row-growth defect (architect section 3) is addressed by a `delete_for_thread` prune, NOT by backfill.

Verdict: TRUE.

Evidence:

1. T5.19 MERGE PRECONDITION already implemented: `MessageMetadataRepository.delete_for_thread` exists and is wired into `daemon/services/maintenance.py::_cleanup_instance` per the brief's anchor (AFTER `adelete_thread`, BEFORE the in-memory callback).

2. Backfill is NOT the row-growth fix: backfill ADDS rows; prune REMOVES rows. They solve DIFFERENT problems. C is about row growth (solved by prune); A/B are about historical timestamps (solved by the state.ts fallback).

---

## W11 Offline-Only Hardening Terms (If Ever Revisited)

If a future reviewer determines that A/B is FALSE, the ONLY acceptable backfill shape is the bounded, operator-initiated OFFLINE-only path via `daemon/migrations/checkpoint_migrator.py`:

- Operator sign-off REQUIRED: documented in the doc's sign-off with operator name + timestamp.
- Bounded batch size: <= 1000 rows per transaction.
- Time-window or row-count cap: explicit cap.
- Hard upper bound: `MAX_BACKFILL_ROWS` env or operator-set ceiling (default 10000).
- Batched logging: every batch start/end + row count logged at INFO.
- No binding-gate disposable PG: operator runs this on `ensemble_prod` (with sign-off) - NEVER on the binding-gate DB.
- Termination shape: `INSERT ... ON CONFLICT DO NOTHING` keyed on PK (idempotent).
- Schema change: NONE.

Live-path backfill remains HARD EXCLUDED: it would resurrect the O(N^2) alist walk as the backfill's own runtime cost.

---

## Disposition

- Backfill status: DROPPED.
- No commit lands: no new code in v2 implements the backfill.
- The W11 hardening above: preserved for v3 re-dispatch if a future criterion fails.
- Reviewer note: v1 D5/D9 + Solution N evaluation carried forward with the architect's section 2.4 corrections applied. Expected outcome DROP; achieved.
