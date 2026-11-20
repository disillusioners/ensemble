# jobs-badge-cost measurement harness (M3)

NOT SHIPPED. Measurement scaffolding for the jobs-badge-loop-hog lane.
Zero production code; exercises the daemon service/repository layer
read-only over a file-backed SQLite fixture.

## What it measures

BASE baseline of the badge composite cost:

| Leg | Service call | Census |
|---|---|---|
| jobs_active_badge | WorkResolverService.list_work(kind="job") | SELECTs per round (no-LIMIT fetch) |
| jobs_terminal_page | WorkResolverService.list_work(kind="job", status=terminal set) | SELECTs per round |
| missions | MissionResolver.resolve_page(limit=20, liveness=...) | 3 SELECTs per page |
| instances | SQLModelInstanceRepository.list(limit=10, order=activity) | flat pagination |
| defer_blocked_watch_read | JobWatcherRepository.get_all_active_watches() | unbounded watch read |
| watch_reconcile | JobQueueService.reconcile_terminal_watches() + reconcile_held_watches_for_instance(None) | timing + statement census |

## Seed population

>=400 terminal JobItems (completed/failed/cancelled/dead_letter mix,
dead_letter>=5), >=8 queued + >=4 active, ~30 linked Instances, a few
ORPHAN instance_ids, some Task timing rows, >=50 JobWatcher rows
(mixed active). Fixture: NullPool + WAL + busy_timeout.

## Entrypoint

```
export PATH="$PATH:/home/nea/.local/bin"
cd <repo>
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_SELF_ENV \\
  .venv/bin/python .agents/tester/harness/jobs-badge-cost/main.py --mode base --out .agents/tester/harness/jobs-badge-cost/raw
```

Outputs: raw/BASELINE.badge_cost.<mode>.json

Mode switch (--mode base|head) changes only assertion expectations, not
measurement. Loop-lag raw (ensemble_loop_lag_seconds) is NOT capturable
in this in-process harness (no daemon LoopLagProbe) and is recorded as
null. Pool-queueing is also not visible in-process; stated honestly in
causal_split_hooks.
