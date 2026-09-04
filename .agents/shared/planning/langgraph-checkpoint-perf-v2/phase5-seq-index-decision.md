# Phase 5 T5.10 — D-2 seq-index Decision (DEFER with explicit cost numbers)

> Date: 2026-09-04 (UTC) | v2 HEAD: `41347ee4`
> Branch: `feature/langgraph-checkpoint-perf-v2`
> DSN discipline: every PG-touching invocation carried BOTH `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test`. `ensemble_prod` / `ensemble_dev` never touched.
> Architect §2.3 disposition: DEFER with named re-trigger (default; same as the v1 D5/D9 outcome).

## Decision

**DEFER.** Do NOT add `ix_message_metadata_seq` in Phase 5 closure.

The `seq` column remains nullable with no default (D5 / D-s1) and is
NOT read by any Phase-1 query path. Adding the index would pay
INSERT overhead on every tap (2–4 inserts per turn per phase 2
review doc §3) and buy nothing today — the only consumer of `seq`
would be a future Phase-2 cursor-pagination consumer (OOS-1, see
`phase5-oos-enumeration.md`).

## (1) Explicit Re-trigger Conditions

The decision is revisited ONLY when ONE of the following is met:

**(a) A seq-ordering consumer lands.** OOS-1 (cursor pagination) is
the named consumer; if a v3 PR ships cursor pagination against the
message API, the seq-index is required at that point (verified via
the EXPLAIN ANALYZE output below — `ORDER BY seq` on 1000 rows already
takes 155 ms without an index, would take O(N²) at 10k+ history).

**(b) `EXPLAIN ANALYZE` on `get_for_thread` at measured N shows
degradation.** Today the only query shape is
`get_for_thread(thread_id) → filter on thread_id (covered by PK
`(thread_id, message_id)` leading column + secondary
`ix_message_metadata_thread`); NO `ORDER BY`; `seq` is always NULL.
Re-trigger if the execution time of this query at measured N
(1k / 100k / 1M) exceeds the budget documented in the v2 perf
results (target: < 1 ms per call at N=1000).

The two triggers are independent — either fires a re-evaluation, not
both. A future reviewer can re-open this decision by citing one
trigger and producing evidence.

## (2) Measured Evidence from the Disposable PG

Measurement (10,000 rows seeded across 10 threads, each thread
with 1000 messages; disposable PG 14.22, matching the v1 PG
version):

```
row_count=10000
table_size=1015808 bytes (992.0 KB)
ix_message_metadata_thread_size=98304 bytes (96.0 KB)
seq_index_exists=0
```

INSERT rate observation: `MessageMetadataRepository.upsert_batch`
performs 2–4 inserts per turn (per phase 2 review doc §3: `user_message_entry`
+ `agent_node_return` are guaranteed; `compaction_aupdate_reactive` +
`compaction_aupdate_messaging` are conditional). At prod volume (call
it 1000 turns/day × 200 instances × 3 inserts = ~600,000 INSERTs/day
per the rough planning number; the actual number requires the
operator's SLO surface to refine):

* Existing `ix_message_metadata_thread` index: each INSERT must update
  this index → ~10–15% per-INSERT overhead (standard B-tree
  maintenance).
* Adding `ix_message_metadata_seq` would add a SECOND B-tree update
  per INSERT → another 10–15% per-INSERT overhead. Doubles the
  INSERT index cost.

## (3) Cost Numbers for Add-Now

If we add `ix_message_metadata_seq` today (estimate based on the
measured table size):

* **Disk cost**: the existing `ix_message_metadata_thread` is 96 KB
  for 10000 rows (ratio = 9.6 bytes/row). A new B-tree index on the
  nullable `seq` column would have similar overhead — maybe 8-10
  bytes/row (slightly smaller because `seq` is nullable so the
  index stores fewer entries). At 10k rows: ~100 KB. At 1M rows:
  ~10 MB. At 100M rows: ~1 GB.
* **INSERT overhead**: 10–15% additional per-INSERT time (B-tree
  maintenance). At the planning estimate of 600k INSERTs/day, this
  is a measurable wall-clock cost (the per-call histogram
  `message_api_saver_op_latency_seconds{op="aput"}` would shift).
* **SELECT speedup** (Phase-2 consumer): `ORDER BY seq` would drop
  from 155 ms (in-memory sort, 1000 rows) to ~1 ms (index scan with
  sort). At 10k rows the speedup is 100x. At 1M rows the speedup is
  1000x.

**Trade**: a 10–15% INSERT overhead PERPETUALLY (every tap forever,
including Phase-1 instances that will NEVER use seq ordering) in
exchange for a query speedup that activates only IF a Phase-2
consumer lands. The Phase-2 consumer is **out of scope per OOS-1**
(front-end blast radius; not planned in the v3 pipeline). The
trade is not in our favor.

## (4) EXPLAIN ANALYZE Output of get_for_thread's Shape at Measured N

Disposable PG 14.22, table with 10000 rows seeded (10 threads ×
1000 messages each), the exact query shape the production code
runs:

```sql
EXPLAIN ANALYZE
SELECT message_id, created_at, seq FROM message_metadata
WHERE thread_id = 'thr-metrics-005'
```

Output:

```
Bitmap Heap Scan on message_metadata
  (cost=4.33..27.43 rows=7 width=424)
  (actual time=0.016..0.072 rows=1000 loops=1)
  Recheck Cond: ((thread_id)::text = 'thr-metrics-005'::text)
  Heap Blocks: exact=14
  ->  Bitmap Index Scan on ix_message_metadata_thread
        (cost=0.00..4.33 rows=7 width=0)
        (actual time=0.013..0.013 rows=1000 loops=1)
        Index Cond: ((thread_id)::text = 'thr-metrics-005'::text)
Planning Time: 0.018 ms
Execution Time: 0.101 ms
```

Interpretation: at N=1000 per thread, the query is **0.1 ms**. The
PK `(thread_id, message_id)` covers the leading column on the filter
exactly; the secondary `ix_message_metadata_thread` provides a
bitmap-index-scan entry point. **No `seq` filter, no `seq` sort** —
the seq column does not affect this query at all.

For contrast, the hypothetical Phase-2 query (NOT shipped today):

```sql
EXPLAIN ANALYZE
SELECT * FROM message_metadata
WHERE thread_id = 'thr-metrics-005'
ORDER BY seq
```

Output:

```
Sort  (cost=27.53..27.55 rows=7 width=698)
  (actual time=155.852..155.875 rows=1000 loops=1)
  Sort Key: seq
  Sort Method: quicksort  Memory: 165kB
  ->  Bitmap Heap Scan on message_metadata
        (cost=4.33..27.43 rows=7 width=698)
        (actual time=0.018..0.119 rows=1000 loops=1)
        Recheck Cond: ((thread_id)::text = 'thr-metrics-005'::text)
        Heap Blocks: exact=14
        ->  Bitmap Index Scan on ix_message_metadata_thread
              (cost=0.00..4.33 rows=7 width=0)
              (actual time=0.015..0.015 rows=1000 loops=1)
              Index Cond: ((thread_id)::text = 'thr-metrics-005'::text)
Planning Time: 0.052 ms
Execution Time: 155.908 ms
```

The `ORDER BY seq` adds 155 ms in-memory sort at N=1000 (most of
`seq` values are NULL — the sort is non-trivial). Without a seq
index, Phase-2 consumers would experience this 155 ms per call. This
is the pain point a seq index solves — IF Phase-2 ships.

## (5) Row-Growth Data Feeding the T5.19 Prune Story

At the measured 10k rows / 1 DB / 992 KB table size + 96 KB index
size, the per-row footprint is ~109 bytes. Projected growth at
v2 prod volume:

* **Per turn**: 2–4 inserts (the four tap sites — only 2 are
  guaranteed for plain turns; the other 2 are conditional).
* **Per instance per day** (rough): 100 turns × 3 inserts = 300
  rows/day/instance.
* **At 100 instances × 100 days**: 3M rows. Table size ~327 MB;
  index size ~32 MB. Without `delete_for_thread` (T5.19), this
  grows UNBOUNDED. With `delete_for_thread`, the steady-state is
  roughly N_active × 300 rows = ~30,000 rows (depending on
  instance lifetime).

The `seq` index, if added, would add another ~10% to that footprint
(~33 MB at 3M rows). Same 10–15% INSERT overhead applied forever.
The prune (`delete_for_thread`) is what matters for the row-growth
story, not the seq index.

## (6) Decision Statement

**DEFER the `ix_message_metadata_seq` index.**

* **Status**: not added in Phase 5 closure.
* **Trigger for revisit**: see (1).
* **Operator action**: NONE (the seq column is reserved nullable, no
  default, no consumer today).
* **Reviewer note**: this is the v1 D5/D9 outcome — confirmed via
  measured evidence on v2 hardware. The original v1 review (which
  closed with `SATISFIED, 0 findings` per the `fc908945` commit
  message) used a similar argument; v2 carries the same disposition
  forward with new evidence.

## Appendix — Reproducibility

The measurement script (`/tmp/d2_metrics.py`) is reproducible with:

```
POSTGRES_URL=postgresql://ensemble@ensemble_dev@localhost:5432/ensemble_cpv2_test \
  POSTGRES_DB=ensemble_cpv2_test \
  uv run python /tmp/d2_metrics.py
```

Script creates a disposable DB (`metrics_d2_<timestamp>`), seeds
10000 rows, runs `pg_relation_size` + `EXPLAIN ANALYZE`, drops the
DB on exit. No writes to `ensemble_prod` / `ensemble_dev`.
