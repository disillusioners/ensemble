# Long Tool Call Nudge (long-tool-call-nudge)

Advisory parent notification when a child's single tool call runs past a
configurable threshold. This feature exists because a child agent once
burned 5h48m on one task (five consecutive 12.5–30+ min bash calls) with
no parent-side signal — the per-tool duration record did not exist and
the 7200s task cap was the only bound. The nudge closes the *visibility*
gap; it is **advisory only** — nothing is killed, paused, or terminated.

## What it is

* Every `tools` node invocation stamps each `tool_call_id` into a RAM
  registry (`daemon/services/long_tool_nudge.py`, module singleton
  `_LONG_TOOL_REGISTRY`) with a `time.monotonic()` start.
* A lifespan-wired scanner (`app.state.long_tool_nudge_task`, name
  `"long-tool-nudge"`) ticks every `interval_seconds` and fires one
  advisory message to the child's **parent** when an in-flight stamp
  exceeds the effective threshold (`elapsed > threshold`, strict).
* Detection keys on **in-flight stamp age only — never on
  TaskHeartbeat** (heartbeats beat every 30s even when a child is
  wedged mid-tool, so heartbeat freshness means nothing here).
* One nudge per wedge **episode** per `(parent, child)`: the episode
  closes when a tool call completes HEALTHY (under threshold) or via
  the 7200s stale-stamp belt. Consecutive long calls with no healthy
  completion in between = ONE nudge (the incident replay case).
* Every tool completion emits a grep-able duration record:
  `[LongToolNudge] TOOL_COMPLETED instance=… tool_call_id=… tool=…
  duration_ms=… threshold_seconds=… threshold_crossed=…` — regardless
  of the kill-switch (duration observability is independent of
  delivery).

## Configuration (env; restart required)

| Env var | Default | Meaning |
|---|---|---|
| `LONG_TOOL_NUDGE_ENABLED` | `true` | Kill-switch. `=0` disables the scanner loop, nudge delivery, and `set_instance_tunable` writes. The per-completion duration log line and stamp registry continue (stamp/log presence ≠ delivery). |
| `LONG_TOOL_NUDGE_INTERVAL_SECONDS` | `60` | Scanner tick cadence (`ge=1`). |
| `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` | `900` | Fallback threshold (`ge=1`, hard max 1800) when a child has no metadata override. |

The hard maximum `1800` is a system invariant — NOT env-tunable. It is
enforced in the scanner's runtime clamp, the boot validator, and the
tuning tool.

## Per-child override

Metadata key `long_tool_call_threshold_seconds` (integer seconds,
`instance_metadata` JSONB — the same substrate as watchover tunables).
Effective threshold resolution, in order:

1. kill-switch (above);
2. the child's metadata key — ignored if absent, non-int, or `< 60`
   (read-side floor, so a hand-edited micro-threshold cannot defeat the
   feature);
3. env default (900);
4. `min(·, 1800)` clamp;
5. strict `>` fire comparison.

## Tool usage

`set_instance_tunable(instance_id, key, value)` — category `instance`:

```
set_instance_tunable(instance_id="<child>", key="long_tool_call_threshold_seconds", value=1200)
→ {"instance_id": "...", "key": "long_tool_call_threshold_seconds",
   "prior_value": null, "effective_value": 1200, "applied_at": "..."}
```

* Range `[60, 1800]` — out-of-range or non-int values raise `ValueError`
  (loud, no silent clamping).
* Unknown key → `{"error_code": "UNKNOWN_KEY"}`; missing instance →
  `{"error_code": "NOT_FOUND"}`; disabled →
  `{"error_code": "FEATURE_DISABLED"}` (no metadata written).
* Effect: the scanner picks the new threshold up on its next tick
  (default 60s) — **no daemon restart** needed for per-child overrides
  (env vars DO require a restart).

## Effect on the child

None directly — the child is **not** paused / killed / terminated. The
parent receives a `[system:long-tool-nudge]` advisory (priority 0, which
never resets leader-attestation counters) recommending: inspect via
`subtree_messages`/`get_instance_info`; `send_message` the child (lands
at the next turn boundary — a mid-tool child cannot receive);
`terminate_instance` + re-spawn past 2× threshold.

## Restart semantics

All RAM state (stamps, fired-episodes, active-episodes) dies with the
process. Worst case after a restart: ≤ 1 duplicate nudge for a child
still mid-tool across the restart. The enqueued nudge itself is durable
the instant `enqueue_message` returns (MessageQueue + Task in one txn).

## Exposure

Category-wide by construction: every agent whose active `tools.allow`
includes `"instance"` gets the tool automatically. Effective holders at
ship time: leader, planner, developer, tester, governor, architect,
coder, reviewer[v2], tidier[v2], wanderer, approver[v2], _mother,
blueprinter, project-manager (planner/developer gained it with this
feature; their [v2] variants already had it). `worker` is denied by the
team-membership gate regardless.

## Caveats

* No parent-ownership check (house stance, mirrors `send_message`): any
  `"instance"`-category holder can tune any instance's threshold. Blast
  radius is one advisory-threshold metadata key.
* Batched tool calls (multiple `tool_call_id`s in one AI message) share
  one batch-entry timestamp — a fast call co-batched with a slow one
  reads slow. Conservative direction; advisory-only recoverable.
* Related follow-up (deferred, separate feature): bounded bash default
  timeout (`ENSEMBLE_BASH_DEFAULT_TIMEOUT_SECONDS`) — visibility is
  covered here; a hard per-call timeout is an action, not an advisory.

## Source

Plan package: `.agents/shared/planning/long-tool-call-nudge/`
(plan-overview.md, decisions.md, phase1/2/3-plan.md,
architecture-recommendation.md).
