# D7 — Backfill Proposal for Skewed Timestamp Rows (PROPOSE ONLY)

**Feature:** `feature/fix-job-queue-timestamps-tz` (Phase 3) · **Date:** 2026-09-15
**Scope:** PROPOSAL ONLY — no DDL, no migration edits, no execution in this
phase (D8 forbids touching published migrations / `_ensure_postgres_columns`).
A maintenancer agent ran READ-ONLY prod forensics in parallel; facts it
covered are marked **[FORENSICS-CONFIRMED]** and are no longer
PENDING-FORENSICS. Anything still open is marked **[PENDING-FORENSICS]**.

---

## 0. Forensics baseline (2026-09-15, ensemble_prod, read-only)

- **[FORENSICS-CONFIRMED]** Session `TimeZone` = **Asia/Ho_Chi_Minh (+07)**,
  sourced from the SERVER CONFIG FILE (postgresql.conf);
  `pg_db_role_setting` has NO timezone override (only search_path) → the
  current-state repair-tz precondition is ANSWERED: **+07**.
- **[FORENSICS-CONFIRMED]** `now()::timestamp` ≡ HCM local digits; producing
  UTC digits requires an explicit `AT TIME ZONE 'UTC'`.
- **[FORENSICS-CONFIRMED]** Zero `timestamptz` columns in the censused set.
- **[FORENSICS-CONFIRMED]** Column census:
  - `task` = naive `created_at` / `started_at` / `completed_at` /
    `last_heartbeat_at` + VARCHAR `next_retry_at` / `cancel_requested_at`.
    **NO `updated_at` column.**
  - `instances` = **exactly ONE naive column: `last_activity_at`**;
    `created_at` / `updated_at` / `paused_at` are VARCHAR (+00:00, correct).
  - `message_queue` = naive ×5 (`enqueued_at`, `processing_started_at`,
    `last_activity_at`, `completed_at`, `next_retry_at`).
  - `job_queue_items` / `job_queues` / `job_locks` / `message_metadata` =
    all ts carriers VARCHAR.
  - `instance_execution_leases` = naive `acquired_at` / `heartbeat_at`
    but **0 rows** (no repair surface).
  - DLQ table is **`dead_letter_items`** (NOT dead_letter_queue):
    VARCHAR `failed_at` + `moved_to_dlq_at` (both NOT NULL). The SQLModel
    model already names it `dead_letter_items` (models.py:531) — code and
    DB agree; no code fix needed.
- **[FORENSICS-CONFIRMED]** Scale: `task` = **395 rows** (all ≤30d old);
  `instances` = **6,850 rows** (last_activity_at is its single naive
  column). `message_queue` row count NOT sampled — counting it is an
  execution-time step (below).

## 1. The defect being repaired (recap)

Pre-fix writers bound AWARE datetimes into naive columns (or wrote SQL
`CURRENT_TIMESTAMP`). PG rendered those in the session TimeZone (+07) and
stored LOCAL digits — so historical rows carry `2026-09-15 21:10:01.441308`
where the true instant is `14:10:01.441308Z`. Post-fix writers store
naive-UTC digits. **The database therefore now contains TWO frames:**

| Frame | Rows | Digits | True instant = digits |
|---|---|---|---|
| Legacy (skewed) | everything written before the fix deploy | HCM local (+07) | digits − 7h |
| New (correct) | everything written after | UTC | digits ± 0h |

Reads via `to_utc_iso`'s assume-UTC policy render legacy rows **7h LATER**
than their true instants (direction pinned by
`tests/unit/test_tz_sort_since.py::test_legacy_plus07_digits_read_7h_off_documented`).

## 2. Skewed-row detection heuristics

### H1 — µs-twin match (HIGH precision, instances.last_activity_at)
The child_reports / observer finalize paths mint ONE aware instant and
write `updated_at` (TEXT, aware ISO `+00:00`) and `last_activity_at`
(naive col) as µs-twins — pre-fix AND post-fix (the Phase-3 conversion
preserved the twin property deliberately).

```sql
-- Skewed twins: the naive digits equal the +07 RENDERING of the TEXT twin.
SELECT instance_id
FROM instances
WHERE updated_at LIKE '%+07:00'            -- aware TEXT twin (always +00:00 format; see H1b)
   OR ABS(EXTRACT(EPOCH FROM (
        (updated_at::timestamp AT TIME ZONE 'UTC')      -- true instant from TEXT twin
        - (last_activity_at AT TIME ZONE 'Asia/Ho_Chi_Minh')  -- legacy frame claim
      ))) < 1                                        -- µs-twin ⇒ ~0s when legacy
;
```

**H1b (simpler form, preferred):** because the twin is written in the SAME
transaction, a row where
`(updated_at::timestamp AT TIME ZONE 'UTC') <> (last_activity_at AT TIME ZONE 'UTC')`
by ≈ 7h (or exactly 0) cleanly partitions frames:
- legacy row: twin true-instant vs `last_activity_at` assumed-UTC ⇒ Δ=+7h…+7h59m;
- new row: Δ≈0 (µs tolerance).

### H2 — Absolute window heuristic (task, message_queue — no twins there)
Legacy rows predate the fix deploy; new rows postdate it. With the deploy
timestamp `T_deploy`:

```sql
-- Rows whose created_at (frame-unknown) is BEFORE T_deploy are legacy
-- with high confidence; AFTER is new. Boundary-window rows need H3.
SELECT COUNT(*) FROM task
WHERE created_at < (:T_deploy_local_digits);  -- compare in the LEGACY frame
```

Caveat: a row written pre-deploy carries +07 digits; `T_deploy` must be
rendered as HCM-local digits for the comparison (or the whole comparison
run as `created_at AT TIME ZONE 'Asia/Ho_Chi_Minh' < :T_deploy_utc`).

### H3 — Cross-source consistency (specimen-verified shape)
Evidence-4 from forensics: for job `28864eab` — `job_queue_items.created_at`
TEXT `'2026-09-15T14:09:59.500140+00:00'` (CORRECT frame) vs the linked
task row `created_at '2026-09-15 21:10:01.435315'` (+07 digits). The
JobItem TEXT and the Task naive digits are written ~seconds apart in the
same dispatch flow, so:

```sql
SELECT t.id
FROM task t
JOIN job_queue_items j ON j.job_id = t.work_id
WHERE ABS(EXTRACT(EPOCH FROM (
      (j.created_at::timestamp AT TIME ZONE 'UTC')
      - (t.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')
    ))) < 120          -- same dispatch flow ⇒ <2min apart
;
```

Rows matching ⇒ the task digits are in the +07 frame (legacy). Rows where
the Δ is ≈ 7h with the ASSUME-UTC reading instead ⇒ already-correct.

### H4 — Sentinel-format probe (zero-risk, run first)
The pre-fix `next_retry_at` TEXT values carry explicit `+0700` offsets
(`strftime('%z')` on aware datetimes under a +07 process-local clock is
`+0700` only if process tz was +07 — **[PENDING-FORENSICS]** the app
process's local timezone history; if the app ran UTC-process, `%z` emitted
`+0000` and the string frame was always explicit-UTC — see §3
precondition P2).

## 3. Preconditions (gates before ANY repair runs)

- **P1 [FORENSICS-CONFIRMED]** Current session tz = Asia/Ho_Chi_Minh (+07)
  from server config. Repair `AT TIME ZONE` literals can be pinned.
- **P2 [PENDING-FORENSICS — OPEN]** **Historical-tz question:** was the PG
  server EVER configured UTC (or another zone) during this data's
  lifetime? Unanswerable from current config; needs ops knowledge or
  log-mining. If yes, single-`AT TIME ZONE 'Asia/Ho_Chi_Minh'` repair is
  UNSAFE for rows written during the other-zone era — the repair must be
  windowed per era (H2 partitions by deploy time; era boundaries multiply
  the windows). This is the single largest open risk.
- **P3** The daemon must be QUIESCED (or the specific writer paths proven
  idle) during repair — otherwise a pre-fix-shaped write landing mid-repair
  is misclassified. Pause-first-then-quiesce convention applies.
- **P4** Fix deploy must be live and verified (post-fix writers emit UTC
  digits) BEFORE backfill, else new skewed rows keep appearing behind the
  repair cursor.

## 4. Repair SQL sketches

**Decision frame:** repair-in-place (convert legacy digits to UTC digits)
vs schema migration to `timestamptz` (kills the frame problem forever).
D8 forbids DDL in Phase 3, but the PROPOSAL should weigh both for the
follow-up:

### Option A — In-place digit normalization (no DDL)
```sql
-- instances.last_activity_at (single naive column; 6,850 rows)
BEGIN;
-- 4a. Freeze writers (P3).
-- 4b. Normalize: reinterpret legacy digits as +07, re-render as UTC digits.
UPDATE instances
SET last_activity_at = (last_activity_at AT TIME ZONE 'Asia/Ho_Chi_Minh')
                        AT TIME ZONE 'UTC'   -- → naive UTC digits
WHERE <H1b legacy predicate>;                 -- ONLY legacy rows
-- 4c. Verify counts (§6) then COMMIT.
```
Same shape for `task.{created_at,started_at,completed_at,last_heartbeat_at}`
(395 rows) and `message_queue` naive ×5 (count at execution time).

### Option B — timestamptz migration (DDL; kills the class)
```sql
ALTER TABLE task
  ALTER COLUMN created_at TYPE timestamptz
    USING created_at AT TIME ZONE 'Asia/Ho_Chi_Minh',   -- legacy rows
  ...
```
**Problem:** a single `USING` applies ONE interpretation to ALL rows —
mixed-frame tables (legacy +07 AND new UTC digits post-deploy) need a
CASE:
```sql
ALTER TABLE task
  ALTER COLUMN started_at TYPE timestamptz
  USING CASE WHEN <legacy predicate>
             THEN started_at AT TIME ZONE 'Asia/Ho_Chi_Minh'
             ELSE started_at AT TIME ZONE 'UTC' END;
```
The legacy predicate MUST be established per-row (H1–H3) BEFORE this
migration — same detection work as Option A, plus DDL. **Recommendation:
Option A first (smaller blast radius, reversible per-table), Option B as
the eventual end-state** once the table is frame-uniform.

## 5. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| P2 historical-tz era unknown | unknown | WRONG repair shifts repaired rows by the era delta | Window by era; H4 probe; start with the 395-row `task` table (all ≤30d — provably single-era if server config unchanged 30d) |
| Misclassified boundary rows (written during repair) | low | ≤ a handful of rows 7h-off | P3 quiesce; post-repair H1b re-scan should return ZERO |
| Sort/age consumers reading mixed frames pre-repair | certain (transition period) | missions panel ordering jitters 7h across the legacy/new boundary; SQL-side age computations (`EXTRACT(EPOCH FROM (now() - last_activity_at))`, instance/repository.py:~3040) inflate ages 7h for NEW rows | Acceptable transitional; full relief arrives with the repair; NOTE the watchdog threshold consumers when scheduling the backfill |
| Repair query locks hot tables | medium | brief write stalls | task (395) and per-batch instances updates in a maintenance window |
| Rollback needed | low | restored +07 digits | §7 |

## 6. Verification queries (post-repair)

```sql
-- V1: zero remaining legacy twins (µs-twin consistency, both frames agree)
SELECT COUNT(*) FROM instances
WHERE ABS(EXTRACT(EPOCH FROM (
      (updated_at::timestamp AT TIME ZONE 'UTC')
      - (last_activity_at AT TIME ZONE 'UTC') ))) > 1;   -- expect 0

-- V2: fresh writes are UTC digits (probe row written post-repair)
-- created/updated within the last minute must round-trip:
SELECT now() AT TIME ZONE 'UTC', now();  -- sanity: session tz still +07

-- V3: task scale unchanged
SELECT COUNT(*) FROM task;               -- expect 395 + post-deploy growth

-- V4: spot-check vs job_queue_items TEXT (H3 shape) — Δ ≈ 0..120s only
SELECT COUNT(*) FROM task t
JOIN job_queue_items j ON j.job_id = t.work_id
WHERE ABS(EXTRACT(EPOCH FROM (
      (j.created_at::timestamp AT TIME ZONE 'UTC')
      - (t.created_at AT TIME ZONE 'UTC') ))) NOT BETWEEN 0 AND 120;
```

## 7. Rollback story

Option A is reversible symmetrically: re-apply the OPPOSITE
reinterpretation (`AT TIME ZONE 'UTC'` → `AT TIME ZONE 'Asia/Ho_Chi_Minh'`)
**restricted to exactly the rows the repair touched** — which is why the
repair MUST first snapshot the affected primary keys into a side table:

```sql
CREATE TEMP TABLE _tz_repair_instances AS
SELECT instance_id FROM instances WHERE <H1b legacy predicate>;  -- pre-repair
```

Rollback = `UPDATE ... WHERE instance_id IN (SELECT ... FROM
_tz_repair_snapshot)` with the inverse expression. Keep the snapshot table
for ≥30d post-repair.

## 8. Execution-time steps still open

1. **[PENDING-FORENSICS]** message_queue row count (census skipped it).
2. **[PENDING-FORENSICS]** P2 era history (ops question).
3. T_deploy timestamp captured at fix-deploy time (H2 boundary input).
4. Maintenance-window scheduling aware of the §5 sort/age consumer note.
