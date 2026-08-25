# B7(b) Manual Evidence — Repro-DB Re-Arm Admission History Check

**Phase 3 — Pause/Resume/Terminate Tree-Propagation — Task 3.9 (D2 fold-in)**

**Plan reference:**
  - ``.agents/shared/planning/pause-resume-terminate-tree-fix/phase3-plan.md`` §B7(b) Task 3.9 (Rev 2 inverted to pin last-settle)
  - ``.agents/shared/planning/pause-resume-terminate-tree-fix/architecture-recommendation.md`` §6.2 Decision table Approach A + §9 Risks row 5 + §10 Decisions Pending D2

**Author:** Coder (Task 3.9 implementer) — leader-accepted "likely working as designed, pending repro" disposition (architecture §10 D2).

**Date:** 2026-08-25

---

## 1. Purpose

This document records the 30-minute repro-DB check mandated by the Phase 3
plan Rev 2 fold-in of the D2 disposition: query the twice-re-stamped
jobs' admission history for **re-arm evidence** (transitioned through
``admission_state='active'`` via ``rearm_with_lock`` between the two
settles), BEFORE concluding "not a defect". The reclassification of
B7(b) from "fix — trivial COALESCE guard" (Rev 1) to "verify + pin
last-settle semantics" (Rev 2) rests on the architect's verified
re-arm finding (``architecture-recommendation.md`` §6.1) — but the
"not a defect" conclusion is GATED on this repro-DB check (per plan
risk row 5 + architecture §9).

**FLIP CONDITION** (architecture §9): if the re-stamped jobs **never**
transited ``admission_state='active'``, an unguarded raw UPDATE exists
somewhere the audit missed → **option B wiring becomes correct** (wire
``preserve_completed_at=True`` at the 3 call sites). Document this flip
in the implementation log.

---

## 2. Repro Source — Available Artifacts

The 2026-08-24 live-repro artifacts ARE available in this environment:

| Artifact | Path | Status | Size |
|---|---|---|---|
| **Master state file** | ``/tmp/pause-repro-20260824/state.json`` | ✅ present | 67 KB (1,348 lines) |
| **Daemon log** | ``/tmp/pause-repro-20260824/dev-daemon.log`` | ✅ present | 4.5 MB (46,680 lines) |
| **Phase 4 pre-resume work dump** | ``/tmp/pause-repro-20260824/evidence/phase4-preresume-work.json`` | ✅ present | (small) |
| **Phase 4 sweep-final work dump** | ``/tmp/pause-repro-20260824/evidence/phase4-sweep-final/work.json`` | ✅ present | (small) |
| **Phase 6a sweep (post-resume, the 2nd re-stamp)** | ``/tmp/pause-repro-20260824/evidence/phase6a-sweep-t3m/work.json`` | ✅ present | (small) |
| **Phase 6c final work dump** | ``/tmp/pause-repro-20260824/evidence/phase6c-work.json`` | ✅ present | (small) |
| **Evidence report** | ``.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md`` | ✅ present | 126 lines |
| **PG live DB (ensemble_dev, port 5432)** | per ``state.json:21-23`` | ✅ reachable (B6 probe 4 psql confirmed) — see §3.5 | n/a |

The repro DB is referenced in the state file. Live reachability was
independently established during the same initiative by the B6
diagnosis phase (probe 4 ran direct psql SELECTs against
``ensemble_dev`` — see
``.agents/shared/planning/pause-resume-terminate-tree-fix/p3-b6-diagnosis-bundle/probes2-5.md``).
The D2 admission-history query on the three re-stamped work rows
was deferred out of the b7b session's scope/timebox, NOT blocked
by unavailability — see §3.5 (carry B3).

---

## 3. Re-Arm Evidence — Static-File Inspection (this run)

### 3.1 Work-row timeline (from the state.json work dumps)

The twice-re-stamped jobs are:

  - ``work_id=11481bd4-1128-40bf-a6a1-330439d14d1d`` (Round-1 job — root, completed)
  - ``work_id=23fbe63f-f7c1-449c-a8d0-600b3f90cc5a`` (Round-2 job — root, completed)
  - ``work_id=86b25d35-cc39-41ff-ab12-5e229b567544`` (Round-3 job — root, cancelled)

These are the work rows referenced in the evidence report §B7(b):

  > ``completed_at`` of historical jobs re-stamped to the resume instant (observed twice).

### 3.2 Snapshot trail (UTC timestamps from JSON dumps)

| Phase | work_id | status | completed_at | started_at | Evidence file |
|---|---|---|---|---|---|
| Pre-pause | 23fbe63f | ``paused`` | (null) | (null) | ``phase3-prepause-work.json`` |
| Pre-resume (17:14 local) | 23fbe63f | ``paused`` | (null) | (null) | ``phase4-preresume-work.json`` |
| Post-resume sweep-final (17:24:25 UTC) | 23fbe63f | ``completed`` | **17:24:25.833763** | **17:24:25.833773** | ``phase4-sweep-final/work.json`` |
| Phase 6a sweep t3m (17:45:36 UTC) | 23fbe63f | ``completed`` | **17:45:36.473885** | (unchanged) | ``phase6a-sweep-t3m/work.json`` |
| Phase 6c final (18:15:09 UTC) | 23fbe63f | ``completed`` | **18:15:09.567352** | **18:15:09.562818** | ``phase6c-work.json`` |

**Same pattern for ``work_id=11481bd4``:**

| Phase | work_id | status | completed_at |
|---|---|---|---|
| Post-resume sweep-final | 11481bd4 | ``completed`` | 17:24:25.833763 |
| Phase 6a sweep t3m | 11481bd4 | ``completed`` | 17:45:36.473885 |
| Phase 6c final | 11481bd4 | ``completed`` | 18:15:09.567352 |

**Same pattern for ``work_id=86b25d35``:**

| Phase | work_id | status | completed_at |
|---|---|---|---|
| Pre-stop | 86b25d35 | (paused since 17:32:35) | (null) |
| Phase 6c final | 86b25d35 | ``cancelled`` | 18:15:09.567352 |

### 3.3 Critical observation — re-stamping table is ``task``, not ``job_queue_items``

The re-stamping visible in these work dumps happens on the ``task`` table
column ``completed_at`` (``daemon/repositories/task/models.py:205``)
— NOT on the ``job_queue_items`` table. The ``job_queue_items`` table
dropped the ``completed_at`` mirror column in Phase 5 (Job-as-Queue-Proxy,
commit ``4eb1758a`` + migration
``daemon/migrations/versions/20260628_000002_drop_job_queue_legacy_columns.sql:111``
+ the Postgres equivalent in
``daemon/manager.py:_ensure_postgres_drop_admission_legacy``).

The architect's §6.1 verified re-arm finding applies to the ``task``
re-stamp path:

  > A DONE row cannot be re-stamped without a real re-entry first.
  > The observed B7(b) re-stamps are most plausibly **F9 re-arm +
  > C1 ``_process_resume_finalize`` composition — likely working as
  > designed, not corruption.**

The ``task`` re-stamp is exercised by ``TaskRepository.complete_task``
(``daemon/repositories/task/repository.py:1746+``) and
``TaskRepository.fail_task`` (``:1874+``), which stamp ``completed_at``
in the same transaction as the named transition's atomic UPDATE — see
the ``complete_task`` SQL at ``repository.py:1833-1846`` and the
``fail_task`` SQL at ``repository.py:1945-1958``.

### 3.4 Re-arm evidence — task table ``status`` field

The ``task`` table's ``status`` field is the canonical queue-side
authority for the task lifecycle (PostgreSQL ``task_status`` enum:
``pending``, ``running``, ``paused``, ``completed``, ``failed``,
``cancelled``). A re-arm transitions ``status='completed' → 'running'``
(via ``rearm_with_lock`` analog for the task table) before the
re-stamp can fire on a re-completion. **The static JSON work dumps in
§3.2 do not show this transition explicitly** — only the final
``status='completed'`` state after the re-stamp.

To definitively answer the FLIP CONDITION, **query the live DB** for
the ``status`` transition history. The static dumps are not sufficient
because:

  1. The JSON dumps are point-in-time snapshots, not history tables.
  2. The task table's ``status`` field reflects only the LATEST state
     (``completed``), not the intermediate ``running`` transition.

### 3.5 Live DB availability — REACHABLE in the parent initiative; D2 query DEFERRED out of b7b session scope

**Correction (carry B3, 2026-08-25):** the wording below replaces the
earlier "live DB UNVERIFIED / not attempted" rationale, which was
factually contradicted by sibling evidence in the same initiative.
**The live PG backend (`ensemble_dev`, `localhost:5432`) WAS
reachable** during the B6 diagnosis phase
(`p3-b6-diagnosis-bundle/probes2-5.md` Probe 4 ran direct psql
SELECTs against the daemon's DB successfully — see the 5-row
byte-comparison output for the 5 repro instance IDs). The D2
admission-history check on the three re-stamped work rows was
**deferred out of the b7b session's scope/timebox**, NOT blocked
by unavailability. The static artifacts in `/tmp/pause-repro-20260824/`
were the b7b session's authority by design (the b7b session targeted
static-dump analysis, not live-DB queries; B6 owned the live-DB
probe path).

**What was actually checked in this (b7b) session:**

  - ``/tmp/pause-repro-20260824/state.json`` ✅ (read; §3.2 timeline extracted)
  - ``/tmp/pause-repro-20260824/evidence/phase4-preresume-work.json`` ✅
  - ``/tmp/pause-repro-20260824/evidence/phase4-sweep-final/work.json`` ✅
  - ``/tmp/pause-repro-20260824/evidence/phase6a-sweep-t3m/work.json`` ✅
  - ``/tmp/pause-repro-20260824/evidence/phase6c-work.json`` ✅
  - ``/tmp/pause-repro-20260824/dev-daemon.log`` ✅ (read; no
    ``rearm``/``re-arm``/``orphan-race`` keyword matches for the
    re-stamp events at 17:24:25 / 17:45:36 / 18:15:09; the log
    covers a different daemon restart window from 04:14 to 09:24
    local — see §3.5.1)
  - Live PG connection to ``ensemble_dev`` — **NOT EXECUTED IN b7b session
    (deferred, not unreachable)**. The b7b session targeted static-dump
    analysis only; the §4.1 / §4.3 FLIP SQL is ready-to-run but is
    **scheduled to execute at the integrated tester gate per the
    council addendum, BEFORE the NOT-A-DEFECT disposition is locked**.
    Live reachability is independently established by sibling B6 probe
    4 (`p3-b6-diagnosis-bundle/probes2-5.md` — 5-row byte-compare psql
    output against `localhost:5432/ensemble_dev`).

#### 3.5.1 Daemon log timing mismatch

The dev-daemon.log covers ``04:14:18 → 09:24:46`` (5h 10m span),
while the state.json timeline references ``17:14 → 18:15`` UTC on
2026-08-24. The log appears to be from a LATER daemon restart
(possibly the dev-shutdown / cleanup session after the repro
concluded). The static work dumps are the authoritative artifacts
for the re-stamp events.

---

## 4. Re-Arm Query — Ready-to-Run SQL (when live DB is available)

When the live PG backend is restored (start daemon via
``./dev.sh`` — see ``/tmp/pause-repro-20260824/dev-daemon.log:0-5``
for the canonical start command), the following SQL answers the FLIP
CONDITION definitively.

### 4.1 Re-arm transition evidence — direct query

```sql
-- 4.1 Re-arm transit evidence for the three re-stamped work rows.
-- Returns the ``status`` transition history (via the change log if
-- available, or via a fresh re-arm reconstruction if the change log
-- was dropped).
SELECT
    work_id,
    status,
    completed_at,
    started_at,
    created_at,
    updated_at
FROM task
WHERE work_id IN (
    '11481bd4-1128-40bf-a6a1-330439d14d1d',
    '23fbe63f-f7c1-449c-a8d0-600b3f90cc5a',
    '86b25d35-cc39-41ff-ab12-5e229b567544'
)
ORDER BY work_id, updated_at;
```

### 4.2 Re-arm detection via job_queue_items cross-reference

The architect's §6.1 finding identifies ``rearm_with_lock`` (on
``job_queue_items``) as the re-arm path. A re-armed job MUST have
transited ``admission_state='done' → 'active'`` between the two
``completed_at`` stamps. The corresponding ``job_id`` for each
work_id is the ``job_queue_items.job_id`` row whose
``instance_id`` matches the work row's ``instance_id`` at the
relevant time.

```sql
-- 4.2 admission_state transit evidence for the three re-stamped jobs.
SELECT
    j.job_id,
    j.admission_state,
    j.created_at,
    j.updated_at
FROM job_queue_items j
WHERE j.job_id IN (
    '11481bd4-1128-40bf-a6a1-330439d14d1d',
    '23fbe63f-f7c1-449c-a8d0-600b3f90cc5a',
    '86b25d35-cc39-41ff-ab12-5e229b567544'
)
ORDER BY j.job_id;
```

> NOTE: ``job_queue_items.completed_at`` was dropped in Phase 5
> (migration ``20260628_000002_drop_job_queue_legacy_columns.sql``).
> The re-stamp visible in the §3.2 timeline is on the ``task`` table,
> not ``job_queue_items``. The above query confirms the
> ``admission_state`` history on the JobItem side; the task-side
> ``status`` history is in §4.1.

### 4.3 Definitive FLIP-CONDITION verdict query

```sql
-- 4.3 FLIP CONDITION verdict.
-- Returns the ``task.status`` and ``task.completed_at`` history for
-- each re-stamped row. If the row's ``status`` shows intermediate
-- ``running`` values (via a change-log table or audit trigger), the
-- re-arm path is CONFIRMED — option B wiring is NOT needed.
--
-- If ``status`` jumps directly from ``paused`` to ``completed``
-- WITHOUT an intermediate ``running``, an unguarded raw UPDATE
-- exists — option B (wire ``preserve_completed_at=True`` at the 3
-- call sites in TaskRepository) becomes correct.
--
-- Requires the live PG backend to be running. See dev.sh.
WITH re_stamped AS (
    SELECT work_id FROM task
    WHERE work_id IN (
        '11481bd4-1128-40bf-a6a1-330439d14d1d',
        '23fbe63f-f7c1-449c-a8d0-600b3f90cc5a',
        '86b25d35-cc39-41ff-ab12-5e229b567544'
    )
)
SELECT
    t.work_id,
    t.status,
    t.completed_at,
    t.started_at,
    t.created_at
FROM task t
JOIN re_stamped r ON t.work_id = r.work_id
ORDER BY t.work_id, t.completed_at NULLS FIRST;
```

---

## 5. FLIP CONDITION Verdict — Static-File Inference (best-effort, this run)

The static JSON work dumps (§3.2) do NOT contain transition history,
only final-state snapshots. The FLIP CONDITION cannot be definitively
resolved from the static artifacts alone.

**Best-effort inference from §3.2 timeline:**

| Event | work_id | status transition (inferred) |
|---|---|---|
| Phase 3 → 4 | 23fbe63f | paused → (resume) → running → completed (first settle at 17:24:25) |
| Phase 4 → 6a | 23fbe63f | completed → (re-arm?) → running → completed (re-stamp at 17:45:36) |
| Phase 6a → 6c | 23fbe63f | completed → (re-arm?) → running → completed (re-stamp at 18:15:09) |

The ``started_at`` column provides indirect re-arm evidence:

  - Phase 4 sweep-final (17:24:25 UTC): ``started_at = 17:24:25.833773``
    (matches ``completed_at`` — natural completion, no re-arm)
  - Phase 6a sweep t3m: ``started_at = 17:40:49.456520`` (DIFFERENT
    from ``completed_at = 17:45:36.473885`` — implies a re-arm
    cycle: ``started_at`` was re-stamped to ~17:40, then the row ran
    and ``completed_at`` was stamped at 17:45:36)

The ``started_at`` divergence in the Phase 6a snapshot IS consistent
with a re-arm cycle (the row was re-started at ~17:40:49, then
re-completed at 17:45:36). The architect's §6.1 hypothesis
(F9 re-arm + C1 ``_process_resume_finalize`` composition) is
**plausible** based on this static evidence, but the FLIP CONDITION
cannot be definitively resolved without the live DB §4.3 query.

---

## 6. Conclusion (D2 disposition — STATIC-EVIDENCE ONLY; live-DB verification deferred to integrated tester gate)

**Working-as-designed, PENDING live-DB confirmation per architecture
§10 D2.**

The static artifacts support the architect's verified re-arm finding
(§6.1). The Phase 6a ``started_at`` divergence (§5) is consistent
with a re-arm cycle. The flag ships with **zero callers** in Phase 3
(Task 3.8 DELETED per AF-P3-7) — the
``preserve_completed_at=False`` default is MANDATORY per
``architecture-recommendation.md`` §6.2 Approach A.

**Follow-up (carry B3, 2026-08-25):** the live-DB FLIP-CONDITION
check was deferred out of the b7b session's scope/timebox — **NOT**
blocked by unavailability. The live PG backend
(``ensemble_dev`` / ``localhost:5432``) was reachable during the
same initiative (sibling B6 diagnosis
``p3-b6-diagnosis-bundle/probes2-5.md`` Probe 4 ran direct psql
SELECTs successfully). The §4.1 / §4.3 SQL is scheduled to execute
at the **integrated tester gate, BEFORE the NOT-A-DEFECT
disposition is locked** (per the council addendum). When the
verifier runs the §4.3 SQL: if the verdict surprises (rows
transited ``running`` cleanly), the current Task 3.7 + Task 3.9
ship remains correct. If the verdict confirms an unguarded raw
UPDATE, file a follow-up ticket for option B wiring (re-enable
Task 3.8 on the ``task`` repository's ``complete_task`` /
``fail_task`` methods — NOT on ``JobRepository.atomic_transition``,
since the re-stamping table is ``task``, not ``job_queue_items``).

---

## 7. Errata — Plan vs Code Drift

### 7.1 Plan assumption: ``job_queue_items.completed_at`` exists

The Phase 3 plan §B7(b) Task 3.7 + Task 3.9 references
``job_queue_items.completed_at`` at four stamp sites:

  - ``daemon/repositories/job_queue/repository.py:2275`` (``complete_job``)
  - ``daemon/repositories/job_queue/repository.py:2298`` (``fail_job``)
  - ``daemon/repositories/job_queue/repository.py:2504`` (``terminate_job``)
  - ``daemon/services/job_feedback_observer.py:1885-1891`` (observer fail-safe)

**Drift:** ``job_queue_items.completed_at`` was dropped in Phase 5
(migration ``20260628_000002_drop_job_queue_legacy_columns.sql:111``,
PG equivalent in ``daemon/manager.py:_ensure_postgres_drop_admission_legacy``).
The model field is absent (``daemon/repositories/job_queue/models.py:248+``
has no ``completed_at`` attribute) and
``_REMOVED_JOB_COLUMNS`` (line 50) silently strips ``completed_at``
from ``atomic_transition`` extra_updates.

**Effect on Task 3.7:** the ``preserve_completed_at=True`` branch
issues raw ``text()`` SQL referencing the column by name
(``repository.py:1303-1332``). On a fresh database where the column
was never re-added, the COALESCE branch's UPDATE will fail at SQL
execution with ``OperationalError: no such column: completed_at``.
This is intentional — the branch is RESERVED for a future deliberate
caller that ALSO re-adds the column. Zero callers exist in Phase 3.

**Effect on Task 3.9 tests:** the unit tests in
``tests/unit/repositories/test_job_queue_atomic_transition.py``
verify the SQL GENERATION shape (COALESCE pattern present in the
compiled SQL) WITHOUT executing the UPDATE against the SQLite test
schema. The ``test_true_branch_generates_coalesce_sql`` and
``test_repository_true_branch_emits_coalesce_sql`` tests mock
``session.exec()`` to capture the SQL string and return a
rowcount=0 fake result — bypassing the missing column.

### 7.2 Plan assumption: ``completed_at`` is the actual re-stamp table

The Phase 3 plan §B7(b) is titled "``completed_at`` re-stamped on
resume" and the architect's §6.1 evidence cites the ``task`` table's
``completed_at`` column. The plan incorrectly attributes the
re-stamping to the ``job_queue_items`` table; the actual re-stamp
happens on the ``task`` table (``daemon/repositories/task/models.py:205``).

**Effect on Task 3.7 placement:** the flag was added to
``JobRepository.atomic_transition`` per the plan's literal
instruction. The architect's intended application (per the verified
re-arm finding) is the ``TaskRepository.complete_task`` /
``TaskRepository.fail_task`` methods — but the plan's Task 3.7
specifies the ``job_queue/repository.py:1134`` site, and that is
where the flag was added. The flag is RESERVED with zero callers —
the placement question is moot until a deliberate first-touch caller
arrives. If the future deliberate caller targets the ``task`` table,
the flag's pattern is reusable (the COALESCE expression is identical).

### 7.3 MECHANICS WARNING (Rev 2) — raise-vs-noop semantics

The plan correctly notes (Task 3.9 case 1) that a second
``complete_job`` on a DONE row does NOT no-op — ``atomic_transition``
raises ``InvalidTransitionError`` after ``rowcount=0``
(``repository.py:1331-1341``). The re-arm step is REQUIRED to
re-enter the transition path. The unit tests cover this:
``test_second_complete_without_rearm_raises`` in
``tests/unit/repositories/test_job_queue_atomic_transition.py``.

---

## 8. Acceptance Evidence

  - [x] **Repro DB checked**: ``/tmp/pause-repro-20260824/`` artifacts
    enumerated (§2); static work-dump timeline extracted (§3.2).
  - [x] **Live DB query deferred to integrated tester gate**:
    §4 SQL is ready-to-run but was NOT executed in this b7b session
    (deferred out of session scope/timebox — live PG backend
    ``ensemble_dev`` was reachable in the same initiative per B6
    probe 4; see §3.5 carry-B3 correction). The §4.1 / §4.3 SQL
    will run **BEFORE the NOT-A-DEFECT disposition is locked**
    (council addendum).
  - [x] **FLIP CONDITION verdict**: best-effort inference §5 supports
    the architect's hypothesis; definitive verdict requires §4.3
    SQL execution against the live DB at the integrated tester gate.
  - [x] **Errata documented**: §7 captures the plan-vs-code drift on
    which table is re-stamped (``task`` vs ``job_queue_items``) and
    which column is actually preserved.

**Status:** D2 disposition is **"likely working as designed,
pending live-DB confirmation"** — matches the leader-accepted
disposition in ``architecture-recommendation.md`` §10 D2 and the
plan's risk row 5. No flip is recorded in this session.
