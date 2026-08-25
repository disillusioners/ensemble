# FT-002 — B7(c): Detail-vs-List Status Disagreement

**Source spec:** `phase3-plan.md` §Out of Scope → Ticket FT-002 (Task 3.10 deliverable)
**Filed:** 2026-08-25 (P3 documentation pass) · **Effort class:** SMALL–MEDIUM
**Status:** OPEN (deferred to future batch)

---

## Repro

Job `86b25d35` — `jobs-detail` endpoint says `completed`; `jobs-list` endpoint says `processing`. Detail uses `work_record.completed_at` directly (`daemon/routers/jobs_crud.py:123`); list uses legacy status derivation (likely `_derive_legacy_status` in `daemon/repositories/job_queue/work_status.py` or equivalent).

## Suspected sites

- `daemon/routers/jobs_crud.py:123` — detail endpoint direct read.
- `daemon/repositories/job_queue/repository.py:855` — list endpoint status derivation (verify exact line).
- `_derive_legacy_status` at `daemon/repositories/job_queue/work_status.py` (path TBD; legacy wrapper).

## Effort class

SMALL (likely 1–2 line normalization) to MEDIUM (if derivation paths diverge in semantic).

## Recommended approach

- Pick ONE status derivation path (canonical: `_derive_legacy_status`) as the single source.
- Both endpoints call the canonical path.
- Unit test: same row, both endpoints return identical status.

---

## B7(b)-Session Addendum: Re-stamp Surface Lives on the Task Table

> **Addendum source:** P3 B7(b) verification session — manual doc §7.2.
> **Context:** The `rearm_with_lock` EXISTS investigation (F9 closed, `job_queue/repository.py:1974-2167`) showed the re-stamp surface the plan was reasoning about is **not** on `job_queue_items`.

### The drift

The plan's Task 3.7 / 3.9 speak of `job_queue_items.completed_at` stamp sites, but that **column was DROPPED from `JobItem` in Phase 5** by migration `20260628_000002_drop_job_queue_legacy_columns.sql`. The observed re-stamps in the B7(b) session live on the **task table** (`task` model at `daemon/services/task/models.py:205` — the `failed_at` timing column is the actually-flowing one in the re-arm cycle, not `completed_at`).

### Plan-literal vs reality

- **Plan-literal (Task 3.7/3.9):** reserved flag ships on `JobRepository.atomic_transition`; callers stamp `job_queue_items.completed_at` on first settle.
- **Actual:** the reserved flag is wired in plan-literal form on `JobRepository.atomic_transition`, but the first-touch caller that needs the last-settle semantics almost certainly belongs on `TaskRepository.complete_task` / `TaskRepository.fail_task` — the task table is where the timing column actually lives post-Phase 5.
- **Plan Case 4 wording is technically incorrect:** "still stamp `completed_at` on first settle" — the column flowing through the re-arm cycle is `failed_at` (task model), not `completed_at`. (Plan Case 4 was the semantics-pin case in the B7(b) re-arm verification, not a code defect — just a wording drift in the plan.)

### Implication for FT-002

The derivation paths surfaced by this ticket may need **task-table-aware reconciliation** in addition to the canonical-path fix above. When implementing:

1. Audit `_derive_legacy_status` and the list path for ANY read of the dropped `job_queue_items.completed_at` (should be zero on HEAD `d7deaad2`, but verify).
2. Audit `jobs_crud.py:123` detail path for the same (the plan-literal reference — verify the actual column being read on HEAD).
3. If either path silently shadows to the task-table timing column, the disagreement may be a **cross-table timing mismatch** rather than a derivation-path divergence. The canonical-path fix is still correct, but the unit test should assert both endpoints agree on the same `task` timing column.

### Files referenced

- `daemon/services/task/models.py:205` — task model timing column (`failed_at`).
- `daemon/repositories/job_queue/repository.py:1974-2167` — `rearm_with_lock` EXISTS (F9 closed).
- `daemon/migrations/` migration `20260628_000002_drop_job_queue_legacy_columns.sql` — `job_queue_items.completed_at` dropped.
- P3 manual doc §7.2 (B7(b) session).
