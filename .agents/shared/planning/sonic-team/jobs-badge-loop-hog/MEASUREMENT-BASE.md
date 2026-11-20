# MEASUREMENT-BASE — jobs-badge-loop-hog (M3, slot 4)

Base tree: 213ea6c1e (origin/latest). Mode: base. Raw: `.agents/tester/harness/jobs-badge-cost/raw/BASELINE.badge_cost.base.json`.

Seed population: 30 linked instances, 420 terminal JobItems (dead_letter 8), 10 queued, 5 active, 5 orphan instance_ids, 20 Task timing rows, 60 JobWatcher rows (mixed). Replayed 8 rounds per leg. Reconcile repeated 5 times (median reported).

## Per-leg badge composite (p50 / max latency, statements per round, rows)

| Leg | shape | latency p50 (ms) | latency max (ms) | statements/round | rows/round |
|---|---|---:|---:|---:|---:|
| jobs_active_badge | no-limit (queued,active) — M1a UNTOUCHED | 29.07 | 85.23 | 5 | 435 |
| jobs_terminal_page | limit=10 + ensure_job_ids (route shape) — M1a bounded | 25.39 | 29.48 | 5 | 10 |
| missions | limit=20 (liveness=processing,pending,paused) | 10.05 | 11.68 | 3 | 20 |
| instances | limit=10, include_descendants=false, order=activity | 5.20 | 7.90 | 2 | 10 |
| defer-blocked watch read | get_all_active_watches (unbounded) | 3.69 | 5.10 | 1 | 60 |

Two badge shapes are measured deliberately:
- **No-limit badge shape** (`jobs_active_badge`): exercises the no-LIMIT path, which M1a deliberately does NOT touch (D-68 / Q1-ii "no server-side default cap"). Its router-side N+1s (per-QUEUED-job `_get_queue_position`, per-dead_letter-row DLQ lookup) are what M1b changes there. Statement count stays 5; row count 435.
- **Limit-explicit terminal page** (`jobs_terminal_page`): mirrors the route's actual call (limit=10 + ensure_job_ids, jobs_crud.py:917-922). This is the shape M1a's SQL LIMIT pushdown bounds: 10 rows returned instead of the full set, same 5 SELECTs.

## Watch-reconcile leg (median of 5 repeats)

| Method | median (ms) | min (ms) | max (ms) | statements |
|---|---:|---:|---:|---:|
| reconcile_terminal_watches() | 872.68 | 476.41 | 941.49 | 121 (60 job_queue_items + 60 task) |
| reconcile_held_watches_for_instance(None) | 1756.93 | 1589.63 | 1907.28 | 121 (60 job_queue_items + 60 task) |

The watch-read component: get_all_active_watches() issues an unbounded SELECT with no active-state WHERE (full-table read); callers filter in Python. CORRECTED (cleanup wave, per review): M1b bridges the read off the event loop via asyncio.to_thread ONLY — the read remains a FULL-TABLE read (job_watchers has NO persisted active-state column), so the census-suggested "bridge+bound" is HALF delivered: bridged, not bounded. See HANDOFF.md Known risks. Single-shot numbers are noisy; repeats expose the range.

## Loop-lag raw

Not capturable in this in-process harness (no daemon LoopLagProbe running). Recorded as null in the raw JSON. Analysis reads lag-1.0s (p50≈1.0s floor). The AFTER run at integrate completes the comparison.

## Causal-split hooks

- **Enrichment-count (M1a)**: `jobs_terminal_page` (limit-explicit) bounds the row set to 10 via SQL LIMIT (margin + ensure_job_ids fallback). `jobs_active_badge` (no-limit) stays 435 rows — intentionally untouched (D-68/Q1-ii).
- **Router-per-row (M1b)**: per-QUEUED-job `await service._get_queue_position` N+1 in the row loop + per-dead_letter-row `dlq_service.get_dlq_by_job_id` SYNC on-loop. Batched in M1b.
- **Watch-read (M1b)**: `defer_blocked_watch_read` = 1 SELECT over 60 rows (unbounded full-table read); reconcile leg median 873ms / 1757ms over 60 watch rows. M1b adds the to_thread bridge ONLY — the read stays a full-table read (no persisted active-state column; bridged, not bounded — HANDOFF supersedes this doc where they disagree).
- **Pool-queueing**: NOT visible in this in-process harness (no connection-pool contention / queueing under single-threaded SQLite). Stated honestly. Requires a multi-process or threaded load with a real pool; out of scope for this harness.

## How to verify

```
export PATH="$PATH:/home/nea/.local/bin"
cd /home/nea/ensemble-src-wt-jbh-m3
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_SELF_ENV \
  .venv/bin/python .agents/tester/harness/jobs-badge-cost/main.py --mode base --out /tmp/scratchpad/badge-cost-verify
```

Interval pins (FE): `cd frontend && npx jest src/app/poll-visibility.bindings.pins.spec.ts --maxWorkers=8` → 6 interval pins GREEN, 12 gate pins RED at base (expected).
