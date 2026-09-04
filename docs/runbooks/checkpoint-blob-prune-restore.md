# Runbook: checkpoint_blobs Reference-Aware Prune — Pre-Enable Checklist + Restore

**Component:** Phase 1 C3 of the LangGraph Checkpoint Persistence Performance
plan (`.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md` §C3).
**Code owners:** `daemon/services/checkpoint_prune.py` (algorithm + fail-safe
+ destructive gate), `daemon/checkpoint_adapter.py` (the anti-join SQL arms).
**Risk class:** DATA-DESTROYING when destructive is enabled. The prune ships
**dry-run only**; this runbook is the mandatory gate between "shipped" and
"deleting for real".

## How the ladder works

| Mode | Condition | Behavior |
|------|-----------|----------|
| **Dry-run (default)** | env unset, or `CHECKPOINT_BLOB_PRUNE_DRY_RUN` ≠ `0` | SELECT-only: reports `would_delete` counts + bytes per (thread, ns); deletes NOTHING |
| **Destructive (opt-in)** | `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` **AND** `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0` | Executes the anti-join DELETE |

Both flags must be set TOGETHER; either missing/wrong-value leaves the
destructive call path structurally unreachable (see
`tests/unit/services/test_maintenance_prune_direct_anti_join.py::test_delete_call_is_structurally_gated_by_destructive_flag`).

The prune runs inside the maintenance cycle (`CheckpointCleanupJob.execute`,
Operation E) immediately after the 50-cap retention prune (Operation D), on
the same 15-minute idle-gated cadence. Per-(thread, ns) failures are isolated
and logged; they never break Operations A–D.

Log signature (one line per pair, gated by `CHECKPOINT_PERF_LOGS`):

```
[CheckpointPerf] op=blob_prune thread=<8 chars> dry_run=1 deleted=<would_delete> bytes=<bytes_would_free> refs_seen=<n> observed_blob_count=0
```

---

## PRE-ENABLE CHECKLIST

Execute IN ORDER. ALL steps must be green before flipping destructive.
Any red ⇒ STOP, do not enable, re-spec the anti-join against what you
actually found (the plan's LD-OQ1 rule: do not assume the shape).

### [ ] 1. Verify the installed saver's schema in THIS environment

Confirm `checkpoint_blobs` columns are exactly
`(thread_id, checkpoint_ns, channel, version, type, blob)` and that the
reader join uses `checkpoint -> 'channel_versions'`:

```bash
python -c "import langgraph.checkpoint.postgres.aio as m; print(m.__file__)"
# then inspect CREATE TABLES + SELECT_SQL in that package's base.py:
grep -n "CREATE TABLE IF NOT EXISTS checkpoint_blobs" -A 8 \
  "$(python -c 'import langgraph.checkpoint.postgres.base as b, os; print(os.path.dirname(b.__file__))')/base.py"
grep -n "channel_versions" \
  "$(python -c 'import langgraph.checkpoint.postgres.base as b, os; print(os.path.dirname(b.__file__))')/base.py" | head
```

**Expected:** the `checkpoint_blobs` DDL shows PK
`(thread_id, checkpoint_ns, channel, version)`; `SELECT_SQL` contains a
subquery joining `jsonb_each_text(checkpoint -> 'channel_versions')` with
`checkpoint_blobs` on all four keys.
**Abort if:** the PK or the reader join shape differs — the anti-join's
reference relation no longer mirrors the reader.

### [ ] 2. Verify the ACTUAL `channel_versions` shape in PROD (§36-style layout check — MANDATORY)

Run against the PROD checkpoints database, one representative active thread:

```sql
SELECT jsonb_pretty(checkpoint->'channel_versions') AS cv
FROM checkpoints
WHERE thread_id = '<representative-thread-id>'
ORDER BY checkpoint_id DESC
LIMIT 5;
```

Also confirm blob rows actually match those refs (the reader-relation
round-trip):

```sql
-- Do remaining checkpoints' refs actually resolve to blob rows?
SELECT c.checkpoint_id,
       e.key AS channel, e.value AS version,
       (SELECT count(*) FROM checkpoint_blobs bl
         WHERE bl.thread_id = c.thread_id
           AND bl.checkpoint_ns = c.checkpoint_ns
           AND bl.channel = e.key
           AND bl.version = e.value) AS blob_rows
FROM checkpoints c
CROSS JOIN LATERAL jsonb_each_text(
        CASE WHEN jsonb_typeof(c.checkpoint->'channel_versions') = 'object'
             THEN c.checkpoint->'channel_versions' ELSE '{}'::jsonb END) e
WHERE c.thread_id = '<representative-thread-id>'
ORDER BY c.checkpoint_id DESC
LIMIT 50;
```

**Expected:** `cv` is a flat JSONB object mapping channel name → version
string (values look like `00000000000000000000000000000007.4829ab…`). Every
`(channel, version)` from the second query resolves to `blob_rows >= 1`
(except channels whose values are primitives — those live inline, no blob).
**Abort if:** the object is nested under another key, uses
`versions_seen` instead, values are non-scalar, or refs systematically
resolve to 0 blob rows — the anti-join must be re-specified first
(LD-OQ1 — do not assume).

### [ ] 3. Dry-run one full retention cycle in dev/staging (≥ 7 days at the 15-min cadence)

Leave the daemon in default (dry-run) mode and inspect the log stream:

```bash
grep "\[CheckpointPerf\] op=blob_prune" <log-file> | tail -50
```

**Expected:** `dry_run=1` on every line; `deleted=` (would-delete) > 0 on
threads where retention (Operation D) has actually deleted old checkpoints;
`refs_seen` > 0 on pruned pairs.
**Abort if:** `deleted=0 AND refs_seen > 0` on pairs that HAVE old
checkpoints deleted (anti-join never-deleting ⇒ predicate drift), or any
`skipped_reason=ZERO_REFS_FAIL_SAFE` storm on healthy threads (extraction
broken ⇒ shape drift).

### [ ] 4. Real-saver integration suite GREEN

```bash
uv run pytest tests/integration/checkpoint_prune_real_saver.py -v
uv run pytest tests/integration/checkpoint_prune_restore_rehearsal.py -v
uv run pytest tests/unit/checkpoint_adapter/test_direct_anti_join.py tests/unit/services/test_maintenance_prune_direct_anti_join.py -v
```

**Expected:** all pass, zero skips of the PG-backed tests (a
`PostgreSQL not available` skip means the gate did NOT run — treat as red).

### [ ] 5. Restore rehearsal confirmed (automated + manual knowledge)

The automated roundtrip (`checkpoint_prune_restore_rehearsal.py`) proves
backup → destructive prune → restore → byte-equality. Manually confirm you
know the backup-table convention: `checkpoint_blobs_<YYYYMMDD>_backup`
(operator chooses the suffix), restore via
`INSERT INTO checkpoint_blobs SELECT * FROM <backup> ON CONFLICT DO NOTHING`,
and the post-restore count check `SELECT COUNT(*) FROM checkpoint_blobs`
must equal the pre-prune count.

### [ ] 6. Snapshot PROD `checkpoint_blobs` BEFORE the first destructive cycle

```sql
CREATE TABLE checkpoint_blobs_prune1_backup AS SELECT * FROM checkpoint_blobs;
-- sanity: row counts must match
SELECT (SELECT count(*) FROM checkpoint_blobs) AS live,
       (SELECT count(*) FROM checkpoint_blobs_prune1_backup) AS backup;
```

Hold the backup ≥ 7 days after the first destructive cycle.

### [ ] 7. Flip the ladder (only after 1–6 are ALL green)

**Single-process only:** arm the flags ONLY while a single daemon process
serves the database — no blue-green/overlap windows. A stale in-flight
writer from another process could commit a checkpoint referencing a blob
deleted in the overlap window. **Honest scope of this rule:** it
mitigates the CROSS-process variant of the race. It does NOT by itself
eliminate the intra-process window described below — that window exists
even with exactly one daemon process.

**Residual intra-process race disclosure (PR4 external review, 2026-08-26).**
The DEFAULT `AsyncPostgresSaver` path on PG14+ (psycopg autocommit +
pipeline) commits each `aput`'s blob upsert and checkpoint upsert as
SEPARATE implicit transactions — a µs-scale gap
(`langgraph-checkpoint-postgres` `aio.py:82`, `aio.py:280-304`; the
non-pipeline fallback at `aio.py:393-399` IS atomic, but it is not the
path this daemon runs). If the prune's anti-join DELETE takes its
snapshot inside that gap — even single-process, because the maintenance
task and graph turns share the process — it can see the new blob
without the checkpoint row referencing it and delete the blob; every
subsequent `aget` then silently reconstructs WITHOUT that channel. The
idle gate is a PRECONDITION, not a lock: it makes this overlap rare, not
impossible. Recovery is the step-6 backup (restore + count + liveness
checks below).

**DB-level hardening shipped with this disclosure:** the destructive
DELETE now runs inside a SERIALIZABLE transaction with bounded retry
(`daemon/checkpoint_adapter.py::delete_blobs_anti_join`,
`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES`). When PostgreSQL's SSI detects
a dangerous structure involving the DELETE (SQLSTATE 40001) or a
deadlock (40P01), the DELETE aborts and is retried on a fresh snapshot,
on which the now-visible referencing checkpoint row rescues its blob —
one side aborts, the prune yields and re-evaluates. Verified
empirically on PG 14.22 that this abort-and-retry works when SSI's
two-edge condition holds. **Equally verified: a lone READ COMMITTED
aput racing the DELETE does NOT trip SSI** (a single rw-out-edge is not
a dangerous structure), so the µs-gap window above is narrowed and
conflict-covered, not eliminated — the §6 backup remains the recovery
of record. Do not arm destructive without it.

```bash
export CHECKPOINT_BLOB_PRUNE_DRY_RUN=0
export CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1
# restart the daemon with both vars in its environment
```

**Expected first-cycle log signature:** `dry_run=0` lines with real
`deleted=N bytes=B`, then the summary line
`blob_prune scanned=K pairs backend=postgres dry_run=False total_deleted=…`.

---

## §9 Planner-cache / statistics note (prod ops — read-latency regime on long-lived saver connections)

> Added 2026-09-04 in response to the Phase5 T5.5 depth-growth
> diagnosis (`phase5-perf-depth-diagnosis.md` §Executive Root-Cause +
> H1). Operator-terse; the full evidence base lives in the linked
> doc.

**The trap.** `AsyncPostgresSaver` runs on a long-lived psycopg
connection with `prepare_threshold=0` (production topology,
mirrored by `tests/helpers/checkpoint_prune_pg.py::real_pg_checkpointer`).
Server-side prepared statements on that connection are still cached —
after PG's 5-execution custom→generic boundary, the cached generic
plan is re-elected from whatever statistics (`pg_stats`) were current
at election time. With stale/absent statistics on `checkpoint_blobs`,
the cached generic plan's `channel_values` subplan becomes a **Seq
Scan over the entire `checkpoint_blobs` table per read** — cost
grows linearly with history depth.

**Measured impact (depth 10000, pre-fix).** `EXPLAIN (ANALYZE,
BUFFERS) EXECUTE` of the `SELECT_SQL` latest-checkpoint query on the
saver connection: `Seq Scan on checkpoint_blobs … rows=14330 est
(actual rows=20001)`, subplan 3.371 ms, **8.557 ms total execution
per read**. The fresh-plan equivalent is 0.064 ms (a ~133×
collapse after a single `ANALYZE`).

**Symptom signature.** Read latency grows with thread history depth
even though the v2 read-flip path is O(tip) under a sane plan.
Per-cell v2 wall-clock at depth 10000 sits 5–13× ms (versus
0.3–1.5 ms at depth 150) when stats are stale.

**Operational guidance.**

* Periodic `ANALYZE` on the saver tables, OR rely on
  `autovacuum`/`autoanalyze` running at a tighter-than-default
  `autoanalyze_scale_factor` for `checkpoints` and `checkpoint_blobs`:
  ```sql
  ANALYZE checkpoints;
  ANALYZE checkpoint_blobs;
  ANALYZE checkpoint_writes;
  ```
  Issuing these on a low-traffic cadence (e.g. hourly via cron or a
  lightweight job, NOT inside the read hot path) keeps the
  plancache honest.
* On-call checklist when "deep threads feel slow":
  ```sql
  SELECT relname, last_autoanalyze, last_analyze, n_live_tup
  FROM pg_stat_user_tables
  WHERE relname IN ('checkpoints','checkpoint_blobs','checkpoint_writes');
  ```
  If `last_autoanalyze` is `NULL` or older than the longest
  populate interval, run `ANALYZE` manually.
* After any large `INSERT` / `DELETE` batch (including the
  `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0` destructive cycle above),
  issue `ANALYZE checkpoint_blobs` — the new row distribution
  changes the index-vs-seqscan breakeven and the cached plan may
  need re-election.

**What this does NOT change.** The honest failure mode remains the
same: the read path is O(tip) when statistics are current; a stale
plancache is environmental noise, not a read-path defect. No
`daemon/**` change is implicated by the depth-growth signature —
the v2 read-flip is correct.

**Symptom-vs-fix at a glance.**

| Symptom | Check | Fix |
|---------|-------|-----|
| `GET /instances/{id}/messages` latency grows with thread history depth | `pg_stat_user_tables.last_autoanalyze` for `checkpoints` / `checkpoint_blobs` | `ANALYZE checkpoints; ANALYZE checkpoint_blobs;` |
| Single-tenant deep-thread regression after a bulk prune | Same | `ANALYZE checkpoint_blobs;` after each destructive prune cycle |

**Pointer.** Full evidence (H1–H4, M3/M3b plan-regime introspection,
E5/E6 numbers): `phase5-perf-depth-diagnosis.md`. Perf matrix
methodology (ANALYZE precondition + component-gated variance):
`tests/performance/test_message_api_cost.py::_analyze_after_populate`
+ `phase5-perf-results.md` §AC-3.2 RESOLUTION.

---

## §10 Pinned-drill `.env` clobber + persistence.py checkpointer log misleading-print (prod ops — pre-flight hygiene)

> Added 2026-09-04 per tester Note A (`.agents/tester/RESULTS/2026-09-04-cpv2-tester-validation.md:87`).
> Operator-terse; both items are environment hygiene, not runbook-logic changes.

**The trap (dev.sh + `.env` clobber).** `./dev.sh` cannot carry `POSTGRES_*`
DSN pins. `dev.sh:58-63` runs `set -a; source .env` and `.env:57` declares
`POSTGRES_DB=ensemble_dev`. The `set -a` flag auto-exports every `.env`
variable, which **clobbers** the operator's exported `POSTGRES_*` pins
before uvicorn starts — the result is split-brain: `POSTGRES_URL`
(operator-set) survives in the environment, but `POSTGRES_DB` (operator-set)
gets overwritten by `.env:57`'s `ensemble_dev`. Because the
`PostgreSQLEngineFactory` (`daemon/repositories/factory.py:189-198`) reads
the `POSTGRES_*` *parts* independently of `POSTGRES_URL` (F-DR1-2 — the
factory honors `POSTGRES_URL` env directly but builds the DSN from the
parts otherwise), a clobbered `POSTGRES_DB` lands the daemon on
`ensemble_dev`, not the operator's pinned `ensemble_cpv2_test`. **Pinned
drills MUST boot uvicorn directly** (e.g.
`POSTGRES_URL=... POSTGRES_DB=... uv run uvicorn daemon.__main__:app
--port 8079`) — `daemon` core never loads `.env`; the clobber is purely a
`dev.sh` concern.

**The misleading-print (persistence.py checkpointer log).** The
checkpointer-boot log line at `daemon/persistence.py:199-202` prints the
file-config DB name (`config.postgres.db` from `ensemble.json`) — NOT
the DSN actually landed. Because `_build_pg_connection_string`
(`daemon/persistence.py:136-151`) can resolve via the `POSTGRES_URL`
shortcut OR compose from env vars with `config.postgres.*` as fallback,
the DSN actually LANDED on may differ from what the log line announces
(either F-DR1-2 split-brain direction: log announces file-config, real
DSN came from `POSTGRES_URL`; or vice-versa). **Do NOT gate
DB-landing on the `daemon/persistence.py` checkpointer log line.** Gate
on `pg_stat_activity` instead:

```sql
SELECT datname, usename, application_name, state, query_start
FROM pg_stat_activity
WHERE datname = 'ensemble_cpv2_test'   -- replace the literal with YOUR pinned DB
  AND (application_name LIKE '%uvicorn%' OR query LIKE '%ensemble%');
```

A row with `datname = <pinned DB>` and a recent `query_start` is the
positive landing signal. The `daemon/persistence.py` log line is
adjudication data, not a gate.

**Symptom-vs-fix at a glance.**

| Symptom | Check | Fix |
|---------|-------|-----|
| Drill boots, `daemon/persistence.py` log announces `<pin>`, but PG `pg_stat_activity` shows no connection to `<pin>` (only `ensemble_dev` or similar) | `pg_stat_activity.datname` for the uvicorn session | Boot uvicorn directly; `unset POSTGRES_*` first if `.env` was sourced |
| Drill "succeeds" per the log line but destructive cycle deletes rows from the WRONG DB | Same | Same — log line is misleading (F-DR1-2); gate on `pg_stat_activity` |

**Pointer.** F-DR1-2 split-brain fence (root-cause):
`daemon/persistence.py:136-151` vs `daemon/repositories/factory.py:189-198`;
incident 2026-08-28 (Critical Notes row 10, this repo's standing
ledger); `daemon/persistence.py:199-202` is the misleading-print artifact.

---

## ROLLBACK (post-enable breakage)

1. **Unset both flags** (`CHECKPOINT_BLOB_PRUNE_DRY_RUN`,
   `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE`) and restart the daemon — the prune
   reverts to dry-run; with neither flag set it is structurally a no-op for
   deletion.
2. **Restore blobs from the step-6 backup:**

   ```sql
   INSERT INTO checkpoint_blobs SELECT * FROM checkpoint_blobs_prune1_backup
   ON CONFLICT DO NOTHING;
   ```

3. **Verify count:** `SELECT COUNT(*) FROM checkpoint_blobs;` must equal the
   pre-prune count (survivors conflict-skip; only pruned rows re-insert).
4. **Verify liveness:** resume an active instance (`aget` its thread) and
   confirm message history + tool outputs reconstruct; see
   `tests/integration/checkpoint_prune_real_saver.py` for the exact
   reconstruction assertions used in CI.
5. Drop the backup table only after the restore is verified.
