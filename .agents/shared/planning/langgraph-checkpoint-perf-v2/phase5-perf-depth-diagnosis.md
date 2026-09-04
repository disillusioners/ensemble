# Phase 5 T5.5 — Depth-Growth Diagnosis (STOP-Gate Root-Cause Evidence)

> Date: 2026-09-04 (UTC) | Branch: `feature/langgraph-checkpoint-perf-v2`
> Tip at diagnosis: `98d0df49` (verified `git rev-parse`); committed honest-red
> perf tests left untouched.
> DSN discipline: every DSN-resolving invocation carried BOTH
> `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND
> `POSTGRES_DB=ensemble_cpv2_test`. All experiments ran on per-run disposable
> DBs (`ensemble_blob_prune_<uuid>` via
> `tests/helpers/checkpoint_prune_pg.py::create_disposable_db`). PG 14.22
> (Homebrew, aarch64) — same server as the phase5 baseline. `ensemble_prod` /
> `ensemble_dev` never referenced. All diagnostic scripts were throwaways in
> `/tmp` (deleted after the diagnosis; never committed).
> Populate method: the committed harness's own `_populate_thread`
> (9999-style empty `graph.ainvoke` rounds + one final reduced-aput at
> `page_size`), imported unmodified so the populate path is byte-identical to
> the committed test.

## Executive Root-Cause Statement

The variance-cell read growth (1.288 → 5.632 → 13.542 ms at depths
150/400/10000, tip constant at 100 msgs) is **a planner-statistics artifact
delivered through the prepared-statement GENERIC-plan regime of the long-lived
saver connection** — not an O(history) component in the read-path code.

Mechanism, in order:

1. `AsyncPostgresSaver` runs on a long-lived psycopg connection with
   `prepare_threshold=0` (production topology, mirrored by
   `real_pg_checkpointer`). During populate, langgraph's `ainvoke` starts
   every turn with `aget_tuple` → the latest-checkpoint query
   (`SELECT_SQL … ORDER BY checkpoint_id DESC LIMIT 1`) executes **once per
   empty round** — thousands of times per cell — long past PG's
   5-execution custom→generic plan boundary.
2. In the generic regime with absent/stale statistics, the cached generic
   plan's `channel_values` subplan is a **Seq Scan over the ENTIRE
   `checkpoint_blobs` table per read** — cost ∝ total blob rows ∝ history
   depth. Measured on the saver connection at depth 10000 pre-ANALYZE:
   `Seq Scan on checkpoint_blobs … rows=14330 est (actual rows=20001)`,
   3.371 ms in the subplan, **8.557 ms total execution** — inside a read
   whose fresh-plan equivalent costs 0.064 ms.
3. Depth 400 and 150 run the same generic shape against small tables
   (801 / 301 blob rows → 0.23 / 0.02 ms) — hence mild growth at low depths
   and blow-up only at 10000.
4. `ANALYZE` after populate invalidates the cached plan; the fresh-stats
   plancache then settles on **custom plans with `checkpoint_blobs_pkey`
   probes (0.04–0.15 ms, stable across 120 subsequent reads)**. The
   improvement is NOT transient (M3b, below).

A second, independent component — **estimator/process noise on a
constant-cost operation** — explains the residual non-depth-ordered spread
after the stats fix, and is why the AC-3.2 gate still fails with
`ANALYZE` in place (0.4231 < 0.7150 but ≫ 0.10). Details in
§Acceptance-Data-Point.

All five experiments below were run 2026-09-04 on this branch.

---

## H1 — Planner-Stats Artifact: **CONFIRMED (refined form)**

The naive form ("fresh DB, no ANALYZE, custom plan seq-scans") is **DENIED**;
the refined form (generic-plan regime + stale/absent stats) is **CONFIRMED**,
and it is the primary growth carrier.

Key discriminating evidence:

* The saver connection's own `pg_prepared_statements` counters at
  measurement time (M3, depth 10000): `generic_plans=9603, custom_plans=422`
  → the reads ran ~96% under cached GENERIC plans.
* The generic plan actually in force (M3 `EXPLAIN (ANALYZE, BUFFERS)
  EXECUTE` **on the saver connection**, depth 10000, pre-ANALYZE):

```
Limit  (cost=0.41..755.76 rows=1) (actual … rows=1 loops=1)
  ->  Index Scan Backward using checkpoints_pkey on checkpoints
        (actual time=8.520..8.… rows=1 loops=1)          ← subplan attached here
        SubPlan 1
          ->  Aggregate (… rows=1 loops=1)
                ->  Nested Loop (… rows=1 loops=1)
                      ->  Function Scan on jsonb_each_text (… rows=3 loops=1)
                      ->  Seq Scan on checkpoint_blobs bl
                            (cost=0.00..428.95 rows=14330)
                            (actual time=0.004..3.371 rows=20001 loops=1)   ← O(table)
        SubPlan 2
          ->  Aggregate (… rows=1 loops=1)
                ->  Index Scan using checkpoint_writes_pkey (… rows=0 loops=1)
Execution Time: 8.557 ms
```

* Post-`ANALYZE`, same connection, same statement: `custom_plans` +20 for
  the next 20 reads (plan invalidated → custom regime), blob subplan flips
  to `Index Scan using checkpoint_blobs_pkey (… rows=0/1 loops=3)`,
  **Execution Time: 0.064 ms** (M3).
* M3b (steady state): after `ANALYZE`, 6 batches × 20 reads (120 reads,
  ~40+ executions — far past the 5-execution re-election boundary) NEVER
  revert to the generic seq scan: `generic_plans` frozen at 9851,
  `custom_plans` 174→279, blob node = pkey probe, exec 0.04–0.15 ms
  throughout. With fresh stats the plancache re-election keeps custom plans
  (generic est-cost no longer wins).
* Custom plans on a FRESH connection were never the problem: raw
  `SELECT_SQL` execute+fetch on a new connection = 0.32 / 0.59 / 0.74 ms at
  depths 150/400/10000 (E1) — flat-ish, sub-ms even where `pg_stats` was
  completely empty (depth 150 pre-ANALYZE had `pg_stats` rows: NONE,
  `last_autoanalyze: null` on all three tables).
* Why stats were stale-but-present at depth 10000: autovacuum's
  `autoanalyze` fired during the ~102 s populate (last_autoanalyze
  13:09:31 ≈ populate end; `n_live_tup` 19988 vs 20001 actual). The generic
  plan was (re)elected from that mid-populate snapshot and then cached; it
  seq-scanned regardless because the generic estimate (14330 rows) still
  priced the seq scan below the index for the parameter-agnostic shape.
  Mid-populate autoanalyze does NOT fix the regime; only a post-populate
  `ANALYZE` (plan invalidation) does.

### EXPLAIN ANALYZE before/after (summary, depth 10000)

| Phase | Regime (saver conn) | Blob subplan | DB exec | Full read (mean of 20) |
|---|---|---|---|---|
| Pre-ANALYZE | generic (g=9603/c=422) | **Seq Scan, 20001 rows actual** | **8.557 ms** | 12.5–17.1 ms |
| Post-ANALYZE | custom (invalidation) | pkey Index Scan probe | **0.064 ms** | 1.87–5.09 ms |

Depth-scaling of the generic blob subplan (actual seq-scanned rows / time):
301 rows / 0.02 ms (150) → 801 / 0.23–0.37 ms (400) → 20001 / 3.37 ms
(10000) — linear in history depth, exactly the observed growth shape.

---

## H2 — Component Localization: **CONFIRMED — growth carries in `saver.aget`'s DB execution**

Per-component × per-depth (seconds-scale stable items; manager-less harness —
`message_metadata` lookup and synthetic-system/context injection are
structurally absent, i.e. contribute 0 by `manager=None`):

| Component | depth 150 | depth 400 | depth 10000 | Depth-scaling? |
|---|---:|---:|---:|---|
| Raw SQL execute+fetch (fresh conn, custom plan) | 0.963 ms | 1.325 ms | 0.742 ms | No |
| Saver-conn DB execution (generic regime, M3) | 0.859 ms | 0.591 ms | **8.557 ms** | **YES (∝ blob rows)** |
| `saver.aget` total (E1, single-shot, noisy) | 1.428 ms | 2.536 ms | 12.082 ms | follows DB exec |
| `_load_blobs` (serde+msgpack of tip blob) | 0.363 ms | 1.356 ms | 2.491 ms | No (process-noise) |
| `_load_writes` (to_thread) | 0.0005 ms | 0.0009 ms | 0.0016 ms | No |
| Channel extraction (`channel_values["messages"]`) | 0.0005 ms | 0.0009 ms | 0.0017 ms | No |
| Serialize loop (100 msgs, constant content) | 0.499 ms | 1.794 ms | 3.261 ms | No (process-noise) |
| `message_metadata` repo lookup | 0 (manager=None) | 0 | 0 | — |
| Synthetic-system + context injection | 0 (manager=None) | 0 | 0 | — |

Caveat recorded for honesty: E1's component probes are single-shot;
`serialize_ms` "dropped" 3.26→1.56 ms and depth-400 post-ANALYZE read got
SLOWER (5.03→6.10 ms in E1, 5.99 vs 5.53 in M3) — signs that the Python-side
components are dominated by a ±2–6 ms process noise floor, not by depth.
M3b even caught a 6.8 ms batch mean while DB exec was 0.153 ms. The only
component with a *systematic, mechanism-backed* depth signature is the
saver-connection DB execution under the generic regime.

---

## H3 — History-Presence vs Table-Size: **CONFIRMED**

Depth-10000 thread (E1): delete ALL historical checkpoints + blobs not
referenced by the tip (retention/Operation-D mimic; NO vacuum, NO ANALYZE
in between), then re-measure:

| | rows (checkpoints / blobs / writes) | read (mean of 10) |
|---|---|---:|
| Before delete | 30000 / 20001 / 30000 | 17.078 ms |
| After delete (tip + 2 blob rows remain) | 1 / 2 / 0 | **3.045 ms** |
| After delete + ANALYZE | 1 / 2 / 0 | 2.939 ms |

Deleted: 29999 checkpoints / 19999 blobs / 30000 writes. The read got ~5.6×
faster purely from table shrinkage with the plan regime untouched — the
generic blob subplan's cost is a function of TABLE SIZE (history), not of
tip content. Complements H1 exactly: what `ANALYZE` fixes by replanning,
shrinking the history table fixes by making the seq scan cheap.

---

## H4 — Populate-Method Tip Contamination: **CONFIRMED CLEAN (one cosmetic caveat)**

Tip fingerprint per depth (blob = the 100-message messages-channel blob):

| depth | tip blob bytes | md5 | n_messages at tip | channel_versions |
|---:|---:|---|---:|---|
| 150 | 16293 | c3475f25… | 100 | messages, __start__, branch:to:echo |
| 400 | 16293 | 4bbdd31d… | 100 | messages, __start__, branch:to:echo |
| 10000 | 16493 | 5f8837ff… | 100 | messages, __start__, branch:to:echo |

* The +200 B delta at depth 10000 is exactly the 2-chars-longer thread id
  embedded in all 100 message ids (`m-thr-…-10000-…` vs `m-thr-…-150-…`;
  2 chars × 100 ids ≈ 200 B). The md5 differences across depths are the same
  naming artifact. Not content contamination.
* Within every depth the tip's two blob rows (the final invoke's two
  superstep writes, versions …29999.… and …30000.…) are byte-identical
  (same md5).
* Only the `messages` channel has blob rows; `__start__` /
  `branch:to:echo` are non-blob channels (inlined/absent), which is why the
  blob-probe loops=3 yield 1 hit + 2 misses.

Verdict: the tip is substantively IDENTICAL across depths; the populate
method does not contaminate the measurement (the harness's own
`thr-{page}-{depth}` naming produces the same +200 B cosmetic delta).

---

## Acceptance-Data-Point — committed variance test re-run with an ANALYZE-equivalent

Per the brief: the committed perf test was re-run ONCE with an
ANALYZE-before-measure wrapper, WITHOUT editing the committed test. Wrapper:
a throwaway pytest plugin (`/tmp/perf_analyze_plugin.py`, loaded via
`-p perf_analyze_plugin` from `PYTHONPATH=/tmp`, active only under
`PERF_ANALYZE_PLUGIN=1`) that monkeypatches
`tests.performance.test_message_api_cost._measure_cell` at collection time
to run `ANALYZE checkpoints / checkpoint_blobs / checkpoint_writes` on the
cell's saver connection before delegating to the original measurement code.
No repo file was touched.

| Run | per_msg_latencies [150, 400, 10000] (ms/msg) | reads (ms) | rel_var | verdict |
|---|---|---|---:|---|
| Baseline (no plugin; E5, this machine) | [0.0133, 0.0542, 0.1237] | 1.329 / 5.423 / 12.369 | **0.7150** | FAIL (reproduces the committed 0.7437 regime) |
| ANALYZE-before-measure (E6) | [0.0263, 0.0502, 0.0187] | 2.632 / 5.016 / 1.865 | **0.4231** | still FAIL (< 0.10 not reached) |

Reading:

* The stats fix collapses exactly the cell the mechanism predicts (10000:
  12.37 → 1.87 ms; per_msg 0.1237 → 0.0187).
* rel_var does NOT reach < 0.10 because the post-fix residuals
  {2.63, 5.02, 1.87} ms are NOT depth-ordered — the 400 cell (5.0 ms) is
  slower than the 10000 cell (1.9 ms) with DB exec measured at
  0.59–0.91 ms. The remaining spread is the process/estimator noise floor
  (±2–6 ms), which at per_msg magnitudes of 0.01–0.05 ms/msg dominates a
  3-sample CoV. Supporting observations: the committed harness's own
  single-shot F2(b) aget-component printed 17.4 ms (E5) vs 9.5 ms (E6) for
  the SAME (100,400) cell; M3b caught a 6.8 ms batch with 0.15 ms DB exec;
  E5's baseline per_msg values match the committed failing run
  (0.7150 vs 0.7437) confirming the same regime.
* With the plugin, test outcomes were 3 failed / 9 passed (variance +
  both AC-3.3 anchors; the (100,400) aget component 9.47 ms blew the 2×
  gate in this run). Without, 2 failed / 10 passed — matching the
  committed honest-red state.

---

## ROOT CAUSE — final statement, confidence, and the two dispositions

**Root cause (two stacked components):**

1. **Depth-growth component [PRIMARY — stats artifact, CONFIRMED].** The
   variance cells' read cost grows with history depth because the saver's
   long-lived prepared statement is in the generic-plan regime by
   measurement time, and with stale/absent statistics the cached generic
   plan seq-scans the whole `checkpoint_blobs` table per read
   (8.557 ms @ depth 10000 vs 0.064 ms with a fresh-stats plan). The
   aget-only read path itself is O(tip): custom plans are flat
   (0.06–0.9 ms) at every depth, H3 shows table-shrinkage alone removes
   the growth, and H4 shows tip content is constant. Confidence: **HIGH**
   — the regime is proven by `pg_prepared_statements` counters on the
   saver connection itself, the plan is proven by
   `EXPLAIN (ANALYZE) EXECUTE` on that connection, the fix-direction is
   proven three ways (ANALYZE: 17.1→5.1 / 15.6→3.2 / 12.4→1.9 ms;
   plan-invalidation counter movement; custom-plan stability over 120
   reads in M3b), and the counterfactual is proven (fresh-conn custom
   SQL is flat; H3 shrink; H4 tip).
2. **Variance-floor component [SECONDARY — environment noise, CONFIRMED].**
   The remaining AC-3.2 failure with stats fixed (0.4231) is carried by a
   ±2–6 ms per-cell process noise floor (non-depth-ordered residuals,
   single-shot component instability, intra-run 6.8 ms batch at 0.15 ms
   DB exec). Confidence: HIGH for its existence and magnitude; it is
   measured directly in three independent experiments.

**The two dispositions** (as framed by the brief):

* **Stats-artifact → harness/AC measurement fix path:** supported by the
  evidence for component 1. The growth is environmental (plan regime ×
  statistics state), not a read-path defect; no `daemon/**` change is
  implicated by any measurement (the read path is O(tip) under a sane
  plan, and the harness manager-less path carries zero metadata/injection
  cost). Under this disposition, the harness needs (a) a statistics-
  fresh measurement environment (e.g. analyze-after-populate — the E6
  wrapper is a working, committed-test-untouched demonstration) and
  (b) an AC-3.2 shape that does not read environment noise as depth
  sensitivity (the 0.4231 residual is noise, not signal).
* **Real O(history) read-path defect → read-path fix path:** NOT
  supported. No experiment produced an O(history) component that survives
  plan/statistics controls: custom plans are flat across depths, H3
  collapses the read by shrinking history with tip constant, H4 shows a
  constant tip, and the Python-side components show no monotone depth
  trend beyond the noise floor.

Per the brief this doc records facts and evidence only; it makes no
threshold recommendation and proposes no fix.

## Reproduction Notes

* E1 script: disposable-DB populate (harness `_populate_thread`) →
  `EXPLAIN (ANALYZE, BUFFERS)` of the exact `SELECT_SQL` latest-checkpoint
  query on a fresh connection + `pg_stat_user_tables`/`pg_stats` snapshot →
  component-timed read passes → `ANALYZE` → re-run → H3 delete/remeasure →
  H4 fingerprint (tip `channel_versions` + blob `octet_length`/`md5` +
  per-table row counts). Raw results: `/tmp/depth_diag_results.json`
  (deleted with the script after diagnosis).
* M2: A1/A2 double-batch before ANALYZE (decay-vs-analyze attribution) +
  mirror-connection introspection (superseded by M3).
* M3: `pg_prepared_statements` + `EXPLAIN (ANALYZE, BUFFERS) EXECUTE`
  ON the saver connection, pre/post ANALYZE, depths {150, 400, 10000}.
* M3b: post-ANALYZE steady state — 120 reads / 6 batches with per-batch
  regime introspection (no generic reversion; noise-floor quantified).
* E5/E6: the committed perf file, without and with the throwaway
  ANALYZE plugin.
* All disposable DBs dropped (`drop_database` per run); orphan sweep of
  earlier `ensemble_blob_prune_*` DBs performed with an active-connection
  check before dropping. `git status` at commit time: clean except the
  diagnosis doc.
