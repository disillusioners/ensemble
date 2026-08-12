# Approach Comparison: Concurrency Gate Approaches

**Date:** 2026-08-12
**Feature:** Concurrency Gate Review

---

## Weighted Comparison

| Approach | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%, inverted) | Cost (15%, inverted) | Weighted Total |
|---|---|---|---|---|---|---|
| **A: Single Canonical Predicate** | 3 | 4 | **5** | 4 | 4 | **4.05 ✅** |
| B: DB-Level Mutex | 2 | 4 | 2 | 3 | 3 | 2.75 |
| C: Expand claim guard only | 5 | 3 | 3 | 3 | 5 | 3.50 |
| D: Queue Unification | 1 | 5 | 1 | 1 | 2 | 1.95 |

**Winner: Approach A** — wins decisively on Maintainability (heaviest axis at 25%) and Complexity.

---

## Approach Details

### Approach A: Single Canonical Predicate ✅

**Mechanism:** Add `TaskRepository.has_instance_busy(instance_id) -> bool` returning `PENDING OR RUNNING OR PAUSED`. Fold it into `claim_pending_task`'s atomic SQL as an `AND NOT EXISTS` subquery — same pattern as the existing defer gate (task/repository.py:986-1000) and background gate (1028-1042).

**Pros:**
- Eliminates predicate drift — one definition everywhere
- No extra DB round-trip (folded into existing atomic claim)
- Proven SQL pattern already in the codebase
- Low blast radius — additive change

**Cons:**
- Does not address the ResumeTurn race window (needs separate fix)
- Requires call-site audit to replace `has_inflight_task` usages

**Verdict:** Recommended for the minimal fix. Maximum blast radius, minimum code change.

### Approach B: DB-Level Mutex (Advisory Lock / Row Lock)

**Mechanism:** PostgreSQL advisory lock or dedicated lock table row per instance. Dispatch path acquires before starting work, releases on completion.

**Pros:**
- Cross-process safe by construction
- No status-set ambiguity — the lock IS the gate

**Cons:**
- Two systems-of-truth (predicate + lock) — maintainability hit
- Advisory locks invisible to `is_busy` checks — deadlock risk
- Extra DB round-trip per claim
- Lock cleanup on crash is complex (advisory locks are session-scoped)

**Verdict:** Not recommended. Adds complexity without solving the maintainability problem.

### Approach C: Expand `claim_pending_task` SQL Guard Only

**Mechanism:** One-line EXISTS addition to the per-instance guard in `claim_pending_task`.

**Pros:**
- Cheapest possible change
- Fixes the claim path directly

**Cons:**
- Does not fix the other 5+ predicates — divergence persists
- The bug recurs in a different shape at `api.py:1189`, `tools/job_queue.py:874`, `instance_lifecycle.py`, `job_queue_service.py:1418`
- Partial fix — other paths remain divergent

**Verdict:** Insufficient alone. This is a subset of Approach A (A includes this + call-site convergence).

### Approach D: Queue Unification

**Mechanism:** Redesign so each instance has exactly ONE queue. Concurrency is implicit: one queue = one worker at a time.

**Pros:**
- Eliminates the multi-queue divergence entirely
- Cleanest theoretical model

**Cons:**
- Deep rewrite of 5 reserved queue types, defer/background semantics, JobLockManager, JobQueueRepository
- 10+ files touched
- Breaks tier-0/1/2/3 concurrency semantics (defer queue, background queue c=1)
- High risk — defer/background lane semantics lost
- Large migration cost

**Verdict:** Too expensive for the minimal fix. This is the north-star architecture, not the immediate remedy.

---

## ResumeTurn Alternative Comparison

| Path | Mechanism | Race Window | Crash Recovery | Queue Fairness | Recommendation |
|---|---|---|---|---|---|
| **C: Current (Cancel + Recreate)** | PAUSED→CANCELLED + new task async | YES (T2–T4) | Gap: no recovery for cancelled-no-successor | Preserved | Migrate away |
| **A: Direct PAUSED→PENDING** | WorkerPool re-claims same row | NONE — atomic | Safe: task stays in known state | Preserved | **✅ RECOMMENDED** |
| B: PAUSED→RUNNING + checkpoint reload | Skip claim path | High | Risky | **BROKEN** — bypasses queue ordering | Not recommended |

---

## Decision Matrix

| If the constraint is... | Choose... | Because... |
|---|---|---|
| Fix the leak NOW with minimum risk | **A + C (claim guard)** | One EXISTS subquery, proven pattern, closes the primary leak |
| Fix the leak + eliminate the amplifier | **A + C + ResumeTurn migration** | Belt-and-suspenders; closes both the predicate divergence and the race window |
| Long-term architectural goal | **D (north star) + instance leases** | Eliminates the structural issue (multi-queue concurrency) but requires significant investment |
