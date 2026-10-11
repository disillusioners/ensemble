# Runbook: checkpoint_blobs Reference-Aware Prune — Production Operations

**Component:** Phase 1 C3 of the LangGraph Checkpoint Persistence Performance
plan (`.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md` §C3).
**Code owners:** `daemon/services/checkpoint_prune.py` (algorithm + fail-safe
+ the per-pair ``if not destructive: ... continue`` guard), `daemon/checkpoint_adapter.py`
(per-pair LIMIT-batched chunk loop + chunk-method SERIALIZABLE retry wrap).
**Risk class:** DATA-DESTROYING — destructive pruning is now the
**unconditional default** (no env-var, no config-toggle, no on/off switch
disables it). The auto cycle, the manual ``/execute`` confirmation, and
the recovery reclaim-runbook all invoke the destructive arm by
construction. The ONLY opt-out is the manual ``/dry-run`` preview path,
which stays SELECT-only.

> Supersedes the pre-feature/blob-prune-default-on version that
> described a conservative dual-arm env-flag ladder. The historical
> runbook is preserved as audit-trail evidence; this rewrite is the
> **operative** doc and is the version any on-call should consult.

## How the prune runs

The reference-aware prune executes Operation E of the maintenance
cycle (`CheckpointCleanupJob.execute`, `daemon/services/maintenance.py::execute`).
It runs after the 50-cap retention prune (Operation D) on the same
15-minute cadence; per-(thread, ns) failures are isolated and
logged — they never break Operations A–D.

```
[per pair]                  ┃ SEQUENCE
─────────────────────────── ┃ ───────────────────────────────────────
fail-safe pre-check        ┃ ZERO_REFS / MAX_REFS caps (pairs skipped
                           ┃   with the corresponding reason and
                           ┃   zero rows deleted)
chunk loop                 ┃ while True: chunk = one DELETE … LIMIT $batch
                           ┃   until chunk returns 0 rows
                           ┃ each chunk is its own SERIALIZABLE
                           ┃   transaction with bounded retry on
                           ┃   SQLSTATE 40001 / 40P01
                           ┃ (on retry exhaustion: SKIP that chunk,
                           ┃  zero rows deleted for it; outer loop
                           ┃  exits; the next cycle retries whatever
                           ┃  remains)
```

Log signature (one line per pair, gated by `CHECKPOINT_PERF_LOGS=1`):

```
[CheckpointPerf] op=blob_prune thread=<8 chars> dry_run=<0|1> deleted=<n> bytes=<bytes> refs_seen=<n>
```

Per-sweep summary (one line per cycle; replaces the per-pair INFO
line that used to spam the log):

```
[CheckpointPerf] op=blob_prune_summary threads=<N> deleted=<n> bytes=<n> would_delete=<n> would_free=<n> dry_run=<0|1> duration_ms=<n> scanned_pairs=<n> skipped=<n> backend=postgres
```

(The `would_*` fields are non-zero only on the manual preview path —
they are zero on the auto cycle and the manual execute confirmation.)

---

## Operational guardrails

The dual-arm env-flag ladder
(`CHECKPOINT_BLOB_PRUNE_DRY_RUN=0` AND `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`)
is GONE. The destructive prune is the unconditional default. These
safety invariants still hold and continue to gate the destructive
arm:

1. **Per-pair row cap** — `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD`
   (default 100_000). Pairs whose (channel, version) ref-count
   exceeds the cap are SKIPPED with `MAX_REFS_EXCEEDED` in the
   per-pair log line. The cap guards against schema-drift /
   unexpected-shape scenarios where the anti-join would otherwise
   nuke every blob of the pair.

2. **Zero-refs fail-safe** — pairs with ZERO refs after extraction
   (despite having remaining checkpoint rows) are SKIPPED with
   `ZERO_REFS_FAIL_SAFE` logged at ERROR level. The anti-join
   would nuke every blob of the pair; we do not run DELETE on
   that signal.

3. **Per-pair SERIALIZABLE wrap + bounded retry**
   (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES`, default 3). Each
   chunk is its own SERIALIZABLE transaction. SQLSTATE 40001
   (serialization failure) and 40P01 (deadlock detected) abort
   the chunk; the retry re-acquires a fresh pool connection
   (fresh snapshot) and re-evaluates the unchanged predicate.
   On exhaustion, the chunk is SKIPPED with an ERROR log; the
   outer loop exits with whatever it accumulated. The next
   cycle retries whatever's left.

4. **LIMIT-batching within a pair**
   (`CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`, default 1000).
   The per-pair DELETE is bound to chunks of at most this many
   rows; each chunk is its own SERIALIZABLE transaction so the
   pool's connection footprint stays bounded even when a pair's
   pair-excess blob count is large. The first production run
   after promote faces the accumulated pair-excess backlog
   (~23 GB across the prod env); the chunk loop prevents a
   single statement from holding an asyncpg pool connection
   for the entire per-pair payload (pool-hazard class).

The `if not destructive: ... continue` structural gate in
`daemon/services/checkpoint_prune.py::prune_unreferenced_blobs`
remains — the SELECT-only `count_blobs_anti_join` dry-run arm
sits behind it (the manual preview path passes
`destructive=False` explicitly). The dominance test
`tests/unit/services/test_maintenance_prune_direct_anti_join.py::
test_delete_call_is_structurally_gated_by_destructive_flag`
pins the structural guarantee.

---

## Pre-flight checklist (HARD prerequisites for the FIRST production cycle)

The historical runbook prescribed a 7-section pre-enable checklist
(schema verify, channel_versions layout verify, 7-day dry-run
soak, real-saver integration suite, restore rehearsal, backup
snapshot, flip the ladder). That checklist was the gate before
the destructive arm was reachable. Now that the destructive
arm is the unconditional default, the gate is different:

### [ ] 1. Verify the installed saver's schema in THIS environment

```bash
python -c "import langgraph.checkpoint.postgres.aio as m; print(m.__file__)"
grep -n "CREATE TABLE IF NOT EXISTS checkpoint_blobs" -A 8 \
  "$(python -c 'import langgraph.checkpoint.postgres.base as b, os; print(os.path.dirname(b.__file__))')/base.py"
```

**Expected:** `checkpoint_blobs` PK `(thread_id, checkpoint_ns, channel, version)`;
`SELECT_SQL` joins `jsonb_each_text(checkpoint -> 'channel_versions')`.
**Abort if:** PK or reader join shape differs — the anti-join's
reference relation no longer mirrors the reader.

### [ ] 2. Verify the ACTUAL `channel_versions` shape in PROD

```sql
SELECT jsonb_pretty(checkpoint->'channel_versions') AS cv
FROM checkpoints
WHERE thread_id = '<representative-thread-id>'
ORDER BY checkpoint_id DESC LIMIT 5;
```

**Expected:** `cv` is a flat JSONB object mapping channel → version
string. References must resolve to existing blob rows. **Abort if:**
shape drift — the anti-join must be re-specified first.

### [ ] 3. Real-saver integration suite GREEN

```bash
uv run pytest tests/integration/checkpoint_prune_real_saver.py -v
uv run pytest tests/integration/checkpoint_prune_restore_rehearsal.py -v
uv run pytest tests/integration/checkpoint_prune_limit_batching_real_pg.py -v
uv run pytest tests/unit/services/test_maintenance_prune_direct_anti_join.py \
         tests/unit/services/test_checkpoint_prune_destructive_override.py \
         tests/unit/services/test_checkpoint_prune_limit_batching.py -v
uv run pytest tests/unit/services/test_maintenance_checkpoint_cleanup_service.py -v
uv run pytest tests/unit/test_no_env_flag_remnants.py -v
```

**Expected:** all pass, ZERO skips of the PG-backed integration tests.
A `PostgreSQL not available` skip is NOT a pass — restart PG and
re-run.

### [ ] 4. Restore rehearsal confirmed (automated + manual knowledge)

The automated roundtrip (`tests/integration/checkpoint_prune_restore_rehearsal.py`)
proves backup → destructive prune → restore → byte-equality.
The manual operator convention: `checkpoint_blobs_<YYYYMMDD>_backup`,
restore via `INSERT INTO checkpoint_blobs SELECT * FROM <backup>
ON CONFLICT DO NOTHING`, post-restore count check.

### [ ] 5. Snapshot PROD `checkpoint_blobs` BEFORE the first
post-promote cycle

```sql
CREATE TABLE checkpoint_blobs_postdefault_backup AS SELECT * FROM checkpoint_blobs;
SELECT (SELECT count(*) FROM checkpoint_blobs) AS live,
       (SELECT count(*) FROM checkpoint_blobs_postdefault_backup) AS backup;
```

Hold the backup ≥ 7 days after the first destructive cycle.
This is the recovery-of-record for the unconditional-default run.

### [ ] 6. Verify the LIMIT-batching safety guard and the chunk
termination invariants via the unit tests

```bash
uv run pytest tests/unit/services/test_checkpoint_prune_limit_batching.py -v
```

**Expected:** single-chunk, multi-chunk, empty-candidate, audit-
totals, and per-statement LIMIT-safety-guard cases all pass.

### [ ] 7. Watch the FIRST post-promote cycle closely (no env arming
needed — destructive is on by default)

The first production cycle after the feature/blob-prune-default-on
promote faces the accumulated pair-excess backlog. Concretely:

* The per-pair DELETE for backlogged pairs iterates through
  multiple chunks (each chunk ≤ `CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`
  rows = 1000 rows by default).
* Each chunk is its own SERIALIZABLE transaction with bounded
  retry — observed mid-prune SQLSTATE 40001s are expected and
  transient; the retry handler re-acquires a fresh connection
  on a fresh snapshot and continues.
* The per-sweep INFO line ``op=blob_prune_summary`` reports
  `dry_run=0`, `deleted=N`, `bytes=B`, `scanned_pairs=K`,
  `skipped=S`. The cycle's `summary_json.blobs.destructive`
  on the `maintenance_runs` audit row is `True`.

**Expected first-cycle log signature:**

```
[CheckpointPerf] op=blob_prune_summary threads=N deleted=K bytes=B would_delete=0 would_free=0 dry_run=0 duration_ms=... scanned_pairs=K skipped=S backend=postgres
```

Any `S` (skipped pairs) above 0 deserves a look — the per-pair
reason codes are `ZERO_REFS_FAIL_SAFE`, `MAX_REFS_EXCEEDED`,
or `ERROR:<ExceptionName>`. The bind path for that pair is
resumable on the next cycle; the failure mode is per-pair
(never per-cycle).

---

## HONEST LIMIT disclosure (still applies — destructive-on does
not change the underlying race)

The DEFAULT `AsyncPostgresSaver` path on PG14+ (psycopg autocommit
+ pipeline) commits each `aput`'s blob upsert and checkpoint upsert
as SEPARATE implicit transactions — a µs-scale gap. If the
prune's anti-join DELETE takes its snapshot inside that gap,
it can see the new blob without the checkpoint row referencing
it. The idle gate in the maintenance service is a
PRECONDITION, not a lock — it makes this overlap rare, not
impossible. The §5 backup + the LIMIT-batched SERIALIZABLE
wrap together narrow the conflict classes SSI actually detects
(deadlocks; any second serializable participant). The residual
single-racer window (a lone READ COMMITTED aput racing the
DELETE) is bounded by the §5 backup as the recovery of record.

**Do not deploy without §5.**

---

## ROLLBACK (post-enable breakage)

There is **no on/off switch** for the destructive prune. To
stop the pruning while preserving the daemon:

1. Disable the auto cycle (env-flag, separate from prune):
   `MAINTENANCE_ENDPOINTS_ENABLED=0` does NOT stop the auto
   cycle (INV-1) — use `CHECKPOINT_CLEANUP_INTERVAL=0` or
   stop the maintenance timer via your normal ops path.
2. The manual API can still block all entry via
   `MAINTENANCE_ENDPOINTS_ENABLED=0` (kill-switch on the
   dry-run / execute endpoints).

To roll back the FEATURE (unconditional destructive) on a
specific host without a code rollback:

* `feature/blob-prune-default-on` ships the destructive-on
  contract by default; going back to the old conservative
  ladder requires reverting this feature branch to its
  pre-feature checkpoint.

To restore data after a destructive prune that misbehaved:

```sql
INSERT INTO checkpoint_blobs SELECT * FROM checkpoint_blobs_postdefault_backup
ON CONFLICT DO NOTHING;
SELECT COUNT(*) FROM checkpoint_blobs;
-- Must equal the pre-prune count (survivors conflict-skip;
-- only pruned rows re-insert).
```

Verify liveness by resuming an active instance (`aget` its
thread) and confirm message history + tool outputs
reconstruct. See `tests/integration/checkpoint_prune_real_saver.py`
for the exact reconstruction assertions used in CI.

Drop the backup table only after the restore is verified.

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
even though the read-flip path is O(tip) under a sane plan.
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
  post-default destructive cycle), issue `ANALYZE checkpoint_blobs`
  — the new row distribution changes the index-vs-seqscan
  breakeven and the cached plan may need re-election.

**What this does NOT change.** The honest failure mode remains the
same: the read path is O(tip) when statistics are current; a stale
plancache is environmental noise, not a read-path defect. No
`daemon/**` change is implicated by the depth-growth signature —
the read-flip is correct.

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
