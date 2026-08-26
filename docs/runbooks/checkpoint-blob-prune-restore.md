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
deleted in the overlap window.

```bash
export CHECKPOINT_BLOB_PRUNE_DRY_RUN=0
export CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1
# restart the daemon with both vars in its environment
```

**Expected first-cycle log signature:** `dry_run=0` lines with real
`deleted=N bytes=B`, then the summary line
`blob_prune scanned=K pairs backend=postgres dry_run=False total_deleted=…`.

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
