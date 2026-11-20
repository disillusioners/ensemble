# MEASUREMENT-BASE — jobs-badge-loop-hog (M3, slot 4)

Base tree: 213ea6c1e (origin/latest). Mode: base. Raw: `.agents/tester/harness/jobs-badge-cost/raw/BASELINE.badge_cost.base.json`.

Seed population: 30 linked instances, 420 terminal JobItems (dead_letter 8), 10 queued, 5 active, 5 orphan instance_ids, 20 Task timing rows, 60 JobWatcher rows (mixed). Replayed 8 rounds per leg.

## Per-leg badge composite (p50 / max latency, statements per round, rows)

| Leg | latency p50 (ms) | latency max (ms) | statements/round | rows/round |
|---|---:|---:|---:|---:|
| jobs_active_badge (queued,active, no limit) | 44.71 | 98.20 | 5 | 435 |
| jobs_terminal_page (completed,...,dead_letter, limit=10) | 36.29 | 38.94 | 5 | 420 |
| missions (liveness=processing,pending,paused, limit=20) | 9.15 | 13.58 | 3 | 20 |
| instances (limit=10, include_descendants=false, order=activity) | 5.58 | 7.43 | 2 | 10 |
| defer-blocked watch read (get_all_active_watches) | 3.91 | 4.67 | 1 | 60 |

Load-bearing pre-fix number: the no-LIMIT fetch on `jobs_active_badge` returns 435 rows per round with 5 SELECTs (2 JobItem + 2 Instance + 1 Task) — the unbounded row set is the cost driver, not the round-trip count.

## Watch-reconcile leg

| Method | time (ms) | statements |
|---|---:|---:|
| reconcile_terminal_watches() | 716.15 | 121 (60 job_queue_items + 60 task) |
| reconcile_held_watches_for_instance(None) | 1926.28 | 121 (60 job_queue_items + 60 task) |

The watch-read component: get_all_active_watches() issues an unbounded SELECT with no active-state WHERE (full-table read); callers filter in Python. M1b adds the active-state WHERE and bridges via asyncio.to_thread.

## Loop-lag raw

Not capturable in this in-process harness (no daemon LoopLagProbe running). Recorded as null in the raw JSON. Analysis reads lag-1.0s (p50≈1.0s floor). The AFTER run at integrate completes the comparison.

## Causal-split hooks

- **Enrichment-count (M1a)**: `jobs_active_badge` statements/round = 5 SELECTs but 435 rows fetched (no-LIMIT). The no-LIMIT fetch + per-row _parse_iso_datetime + batch lookups over ALL matching rows is the enrichment-count driver. M1a pushes SQL LIMIT (margin = limit + len(ensure_job_ids) + tie pad) with guaranteed fallback re-query for ensured ids; no-limit path untouched (Q1-ii).
- **Router-per-row (M1b)**: per-QUEUED-job `await service._get_queue_position` N+1 in the row loop + per-dead_letter-row `dlq_service.get_dlq_by_job_id` SYNC on-loop. Batched in M1b (one pending-list fetch → all positions; one IN-query DLQ enrichment).
- **Watch-read (M1b)**: `defer_blocked_watch_read` = 1 SELECT over 60 rows (unbounded full-table read); reconcile leg = 121 SELECTs over 60 watch rows. M1b adds active-state WHERE + to_thread bridge.
- **Pool-queueing**: NOT visible in this in-process harness (no connection-pool contention / queueing under single-threaded SQLite). Stated honestly. Requires a multi-process or threaded load with a real pool; out of scope for this harness.

## How to verify

```
export PATH="$PATH:/home/nea/.local/bin"
cd /home/nea/ensemble-src-wt-jbh-m3
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_SELF_ENV \
  .venv/bin/python .agents/tester/harness/jobs-badge-cost/main.py --mode base --out /tmp/scratchpad/badge-cost-verify
```

Interval pins (FE): `cd frontend && npx jest src/app/poll-visibility.bindings.pins.spec.ts --maxWorkers=8` → 6 interval pins GREEN, 12 gate pins RED at base (expected).
