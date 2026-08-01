# Tracking: Turn-Reconciler Named Transitions Migration

## Iteration 001 — 2026-08-01 13:24

**Verdict: REJECTED**

### Workers Dispatched (section-parallel, sequential)
| Worker | Scope | Skill | Verdict |
|--------|-------|-------|---------|
| approve-worker-foundations | plan-overview + Inc 1 + Inc 2 | plan-approval | REJECTED (5 blocking) |
| approve-worker-named-trans | Inc 3 | plan-approval | APPROVED (0 blocking, 9 notes) |
| approve-worker-schema-dec | Inc 4 + decisions.md | plan-approval | APPROVED (0 blocking, 7 notes) |

### Blocking Issues (from Worker 1 — Inc 1 & Inc 2)

1. **Fast-path probe only checks 2/8 mirrors** — `increment1-plan.md` §4.1 (lines 111–150), mirror rules (lines 296–317). The fast path returns after checking admission/lock only, leaving 6 other mirror tables uninspected → orphan class survives. Contradicts the reconciler's own 8-table ownership contract.

2. **WAITING_CHILDREN exception missing from Inc 1** — `increment1-plan.md` §4 JobItem rule (lines 171–195) vs `increment2-plan.md` §6 (lines 246–302) + `plan-overview.md` (lines 47, 98). Inc 1 unconditionally maps terminal JobItems to `done`; Inc 2/D13 require leaving WAITING_CHILDREN JobItems active. Cross-increment contradiction.

3. **Claim-time reconciliation ordering** — `increment1-plan.md` §5 (lines 338–345) vs `increment2-plan.md` §4.1/§4.2 (lines 140–153). Reconciler called after claim commit cannot unblock the claim query when an orphan causes it to return no Task → recreates F1/Bug A deadlock.

4. **Inc 2 rollback incoherence** — `increment2-plan.md` §10 (lines 500–524). Partial hotfix (re-add `_terminal_orphan_active_sql` only) doesn't restore queued-orphan or admitted-task protections also deleted by Inc 2.

5. **Property test gap** — `increment1-plan.md` §7 (lines 393–440), §8 (lines 482–498). Tests execute valid lifecycle commands then re-run reconciliation; do NOT generate arbitrary one-table/subset corruption. Would not detect Issue #1.

### Non-Blocking Notes (selected highlights)
- Inc 3: SUSPEND_TURN↔cascade coordination underspecified (N1); `_status_write_guard` class-level flag not thread-safe under asyncio.to_thread (N6); no mid-crash transition test (N4); mirror-table defense beyond task.status is manual-only (N3).
- Inc 4/decisions: B3 partially redundant with migration runner (Note 1); B2 backfill doesn't cover `cancelled` rows (Note 2); OQ8 (report-lane scope) unresolved (Note 3); typo at decisions.md:480 (Note 6).

### Aggregation Notes
- Worker 2 and Worker 3 APPROVED their sections — Inc 3, Inc 4, and decisions.md are architecturally sound.
- All 5 blocking issues are in Inc 1 (reconciler core) and Inc 2 (carve-out deletion) — the foundational increments.
- The named-transitions layer (Inc 3) and schema/routing (Inc 4) depend on a correct reconciler; they cannot proceed safely until Inc 1/Inc 2 blocking issues are resolved.
- No judgment-band downgrades applied: all 5 blocking issues are genuine correctness/safety defects with section/line references.

---

## Iteration 002 — 2026-08-01 13:47

**Verdict: REJECTED**

### Workers Dispatched (section-parallel)
| Worker | Scope | Skill | Verdict |
|--------|-------|-------|---------|
| approve-worker-inc1 (294d7343) | increment1-plan.md (805 lines) | plan-approval | REJECTED (3 blocking, 5 notes) |
| approve-worker-inc2 (8731af1e) | increment2-plan.md (635 lines) | plan-approval | APPROVED (0 blocking, 7 notes) |

### Prior Issue Resolution Check (Iteration 001 → 002)
All 5 prior blocking issues RESOLVED at the plan-artifact level:
1. Fast-path probe REMOVED from plan text (§4 contract). ✓
2. WAITING_CHILDREN exception added to job_queue_items SQL (lines 161–195). ✓
3. Claim-time ordering rationale documented (§5.2). ✓
4. Inc 2 rollback rewritten to coherent two-tier. ✓
5. CORRUPT_MIRROR + 6 directed scenarios added to property tests. ✓

### New Blocking Issues (Worker inc1 — fresh-eyes SQL inspection)

1. **Missing-Task SQL contradiction (mirrors #3, #4, #5)** — `increment1-plan.md` §4, `job_locks` SQL (lines 222–226), `message_queue` (line 241), `dependency_watchers` (line 284). The prose contract says "missing-Task cleanup also deletes the lock" (line 198), and mirror #2 uses `(:task_exists = false OR EXISTS(...))` (line 193) for exactly this. But mirrors #3/#4/#5 use bare `AND EXISTS(...)` without the `(:task_exists = false OR ...)` wrapper. When the Task row is missing, EXISTS returns FALSE → DELETE silently no-ops → contradicts the stated contract. Direct SQL correctness bug.

2. **Mirror #6 (report_injections) has no SQL** — `increment1-plan.md` §4 (line 316). Plan says "Define the exact terminal state mapping from existing schema/constants before implementation" — a TODO. Implementation already has concrete SQL (repository.py:677–694). Property-test invariant (§7 step 2) cannot be verified against an undefined handler. Completeness gap; the plan specifies SQL for 6 of 8 mirrors but defers 2.

3. **Mirror #8 (job_watchers) has no SQL** — `increment1-plan.md` §4 (line 336). Plan says "delete only dangling watcher subscriptions" in prose. Implementation (repository.py:728–738) uses a different predicate (delete when NOT EXISTS the Task at all). If the plan's prose were followed literally, it would destroy watcher rows for in-flight Tasks. Completeness + correctness gap; property-test invariant untestable.

### Aggregation Notes
- No judgment-band downgrades applied: all 3 blocking issues are genuine SQL correctness/completeness defects with section/line references, verified against the plan's own prose contract and the deployed implementation.
- Inc 2 is sound (worker APPROVED); its 7 notes are implementation-hygiene items (stale line citations, bind-param expanding=True convention, LEFT JOIN alias comment, f-string alias guard).
- The 3 new issues are deeper than iteration-001's: iteration 001 found architectural/structural defects; iteration 002's fresh-eyes worker inspected the actual SQL fragments line-by-line and found correctness bugs in the handler logic. This is the iteration working as designed — progressively deeper inspection.
- Inc 2 cannot ship safely until Inc 1's handlers are correct, since Inc 2 deletes the old carve-out SQL that Inc 1's reconciler replaces.

---

## Iteration 003 — 2026-08-01 14:05

**Verdict: APPROVED**

### Workers Dispatched (sequential, single-worker)
| Worker | Scope | Skill | Verdict |
|--------|-------|-------|---------|
| approve-worker-plan (ec1b6c02) | increment1-plan.md v4 (850 lines), focus §4 mirror handlers | plan-approval | APPROVED (0 blocking, 4 notes) |

### Prior Issue Resolution Check (Iteration 002 → 003)
All 3 prior blocking issues RESOLVED:
1. Mirrors #3/#4/#5 missing-Task pattern — now `(:task_exists = false OR EXISTS(...))`, consistent with mirror #2. ✓
2. Mirror #6 report_injections — now concrete `UPDATE ... SET state='TASK_DELIVERED'` SQL. ✓
3. Mirror #8 job_watchers — now `DELETE ... WHERE NOT EXISTS(SELECT 1 FROM task ...)` (Task GONE, not terminal). ✓

### Blocking Issues
None.

### Non-Blocking Notes (worker)
- N1 — Mirror #2 `failed_at` clause: plan spec applies NOT EXISTS guard to all 3 SET clauses, but deployed code omits it on `failed_at`. NOT a plan defect (§8 test enforces correct behavior). Implementer should update deployed code to match plan.
- N2 — v4.1/v4.2 missing-Task fix is a logical no-op for mirrors #4/#5/#6 (snapshot-derived link keys bind to NULL when Task absent → UPDATE no-ops regardless of wrapper). SQL correct; v4 revision notes overclaim fix effect.
- N3 — Mirror #2 status literal hardcoding vs parameterized binding (minor style).
- N4 — §2 "uniformly" wording vs mirror #2 exception (cosmetic).

### Aggregation Notes
- No judgment-band downgrades needed: worker raised 0 blocking issues.
- No upgrades applied (Cardinal #4): all 4 notes genuinely non-blocking. N1 is a deployed-code discrepancy the plan's own test suite catches (not a plan defect). N2–N4 are reasoning/style clarifications requiring no SQL change.
- All 4 PRIMARY FOCUS criteria met: concrete SQL in all 8 mirrors; consistent missing-Task pattern; job_watchers uses NOT EXISTS(task) deliberately distinct; no new issues.
- Iteration 003 reached APPROVED on the final allowed iteration. Full migration plan (Inc 1–4) is now APPROVED.
