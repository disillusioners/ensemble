# Watchover Operator Runbook

> **Audience:** DevOps / SRE operators running agents-ensemble in production.
> **Scope:** Operating the **Watchover** feature — a per-instance, LLM-backed
> safety gate that intercepts every tool call an agent tries to make and
> prompts a "watcher" model to issue an `Allowed` / `Deny` verdict before
> the call is executed.
> **Last updated:** 2026-08-05.

---

## Overview

Watchover is a **DevOps-first safety mechanism** for instances that are
expected to operate with elevated privilege or on sensitive resources. It
inserts an LLM-evaluated Allow/Deny gate between every agent's reasoning
step and the corresponding tool execution. Operators define a **requirement**
(e.g. *"no destructive operations against the production database"*) and
the watcher enforces it on every tool call the agent attempts.

Watchover is **not** a content filter on user messages, and it is **not** a
permissions system on the agent. It is a behavioral guard on the agent's
hands. Its core value proposition is:

- **Per-instance toggle.** Activate for a single instance without affecting
  siblings or children.
- **Operator-defined policy.** The requirement is supplied by the operator
  at activation time — the watcher has no baked-in policy.
- **Fail-closed on judgment errors.** If the watcher cannot parse the LLM
  response, the tool call is denied (safe default).
- **Fail-open on infra errors.** If the LLM times out / errors, the call is
  allowed and a degraded-mode SSE is emitted (no instance-wide outage from
  a flapping LLM).
- **Hard termination on repeated abuse.** After 3 denied batches in a single
  turn, the instance is terminated via `terminal_reason="watchover_terminated"`.

---

## Architecture at a Glance

```
                       ┌──────────────────────────────────────┐
   agent (LangGraph) ──▶│      watchover_check  node           │
                       │  - Read watchover_context from        │
                       │    instance_metadata JSONB            │
                       │  - Call Watcher LLM (Allow/Deny)      │
                       │  - Deny-whole-batch if any deny       │
                       │  - Increment deny_count               │
                       │  - 3-strike → terminate               │
                       └─────────────┬────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
        verdict = "allow"     verdict = "deny"     count >= 3
              │                      │                      │
              ▼                      ▼                      ▼
        ┌──────────┐         ┌──────────────┐       ┌────────────────────┐
        │  tools   │         │ inject deny  │       │  watchover_termi-  │
        │  node    │         │  ToolMessage │       │  nate_node         │
        │ (execute)│         │ (back to     │       │ → deferred marker  │
        └──────────┘         │  agent)      │       │ → terminate_ins-   │
                             └──────────────┘       │   tance (cascade)   │
                                                   └────────────────────┘
```

**Key invariants:**

- **Deny-whole-batch (LD-1).** If **any** tool call in a batch is denied, the
  **entire batch is denied**. The denied calls receive a denial ToolMessage;
  the allowed calls receive a "deferred — batch contained denied call"
  ToolMessage. **No partial execution.**
- **3-strike termination.** The denial counter advances **once per denied
  batch**, not once per denied call within the batch. When the counter
  reaches `max_denials_per_turn` (default `3`), the node routes to
  `watchover_terminate_node`, which sets a C2-safe deferred marker and
  persists `instance_metadata.watchover_pending_termination=true` to the
  database before the cascade runs.
- **Crash-safe termination.** The cascade runs from the post-graph
  completion path (NEVER inside the graph task — that would self-cancel
  and leave torn state). The DB marker is the crash-safety net: if the
  daemon dies before the cascade runs, the stale-marker sweep on restart
  completes the termination.

---

## Enabling Watchover

### Via API

```http
POST /api/instances/{instance_id}/watchover
Content-Type: application/json

{
  "enabled": true,
  "requirement": "Do not execute any tool that writes to the production database. Read-only is fine.",
  "context": null
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | **yes** | `true` to activate, `false` to deactivate. |
| `requirement` | no | Operator-supplied requirement string. Passed verbatim to the watcher as the Allow/Deny policy. When omitted, the watcher uses its own persona prompt without an operator-supplied requirement line. |
| `context` | no | Pre-built context string. Production callers should pass `null` so the service builds the context via `ContextCompactor` (summary or raw-tail fallback). Maximum 50 000 characters. |

**Response:**

```json
{
  "instance_id": "abc123…",
  "watchover_enabled": true,
  "context_length": 1842,
  "quiescent": true
}
```

| Field | Meaning |
|-------|---------|
| `context_length` | Length of the constructed context string. Useful for sanity-checking that compaction produced a real summary. |
| `quiescent` | Whether the quiescence barrier (`wait_for_instance_quiescent`) completed within its timeout. `false` indicates a straggler tool call may have raced with activation (see [Known Limitations](#known-limitations)). |

### What happens on activation

The activation lifecycle is implemented in
`daemon/services/watchover_service.py:WatchoverService.activate_watchover`.
The sequence is:

1. **Quiescence barrier** (`wait_for_instance_quiescent`, default 30 s) —
   awaits any in-flight graph task for this instance so the context
   snapshot is taken against a consistent LangGraph state. Best-effort:
   a timeout returns `quiescent: false` and logs a warning without
   blocking the activation.
2. **Pause cascade** (`pause_instance_cascade`) — soft-pauses the instance
   and its children. The cascade is `await`ed but the activation does NOT
   pass `suspension_reason=SuspensionReason.WATCHOVER_SETUP` (the enum
   value is reserved for a future Phase 5 follow-up; today the column
   records `None` for watchover-induced pauses).
3. **Context snapshot** (`_build_watchover_context`) — calls
   `ContextCompactor.compact_state` on the current conversation. If the
   compactor returns `None` (history below the minimum-messages
   threshold, every message injection-flagged, recently compacted, etc.),
   the raw-tail fallback (_last 10 messages_) is used so the watcher
   always has SOMETHING to evaluate against.
4. **Requirement splice** — the user-supplied requirement is prepended
   to the summary so the watcher's Allow/Deny prompt includes both the
   agent's recent activity and the operator's intent.
5. **Atomic flag write** (`enable_watchover`) — writes **all 4 metadata
   keys** in a single `set_metadata_many` call:
   - `watchover_enabled: true`
   - `watchover_context: <snapshot>`
   - `watchover_denial_count: 0`
   - `watchover_requirement: <requirement>`

   A crash mid-write cannot produce torn state (e.g. `enabled=true` but
   `context=null`); the entire flag set is committed atomically.
6. **Resume cascade** (`resume_instance_cascade`) — un-pauses the
   instance and its children.
7. **SSE emit** (`watchover_active`) — frontends subscribed to status
   events see the transition. Best-effort; an SSE failure does not
   trigger rollback because the underlying state is already correct.

**Rollback (W-8):** if step 3, 4, or 5 raises, the partial state is
cleared (`watchover_enabled=false`, `watchover_context=null`,
`watchover_requirement=null`, `watchover_transition="rollback"`),
followed by a best-effort `resume_instance_cascade` so the instance is
never left PAUSED with the flag cleared, and a `watchover_failed` SSE
emit so the frontend is not stuck in a stale "activating" state. The
**original** error is then re-raised.

---

## Disabling Watchover

### Via API

```http
POST /api/instances/{instance_id}/watchover
Content-Type: application/json

{
  "enabled": false
}
```

The same endpoint is used for both activation and deactivation; the
`enabled` field toggles the mode. The `requirement` and `context` fields
are ignored when `enabled: false`.

### What happens on deactivation

The deactivation lifecycle is implemented in
`daemon/services/watchover_service.py:WatchoverService.deactivate_watchover`.
The sequence is:

1. **Pause cascade** (`pause_instance_cascade`).
2. **Clear flag** (`disable_watchover`) — writes
   `watchover_enabled: false`. The existing `watchover_context` and
   `watchover_requirement` are **kept on disk for audit** so an
   operator can see what the watcher was guarding before the toggle-off.
3. **Resume cascade** (`resume_instance_cascade`).
4. **SSE emit** (`watchover_inactive`).

**Rollback (H2):** if step 2 or 3 raises, the rollback block attempts
a best-effort `resume_instance_cascade` so the instance is never left
PAUSED after a deactivation attempt. The original error is then re-raised.

---

## Kill Switch

The `WATCHOVER_ENABLED` environment variable is a **global kill switch**
that disables watchover **for every instance in the deployment**, regardless
of per-instance flags. When `WATCHOVER_ENABLED` is set to anything other
than `true`, `1`, or `yes` (case-insensitive), the watchover graph node
short-circuits to the `tools` path **before reaching the database**.

This is the **zero-cost path** for non-watched deployments: even with
`watchover_enabled=true` flags scattered across `instance_metadata`, the
graph never reads them when the kill switch is off.

```bash
# Globally disable watchover (kill switch)
export WATCHOVER_ENABLED=false

# Re-enable (default)
export WATCHOVER_ENABLED=true
```

**Operational notes:**

- The kill switch takes effect on the **next tool call** after the env
  var flips. There is no in-flight cancel; tool calls already in
  `ToolNode` execute normally.
- The kill switch does **not** clear per-instance flags. When you
  re-enable, every instance that was watched before the kill switch
  will be watched again — including the 3-strike counter. If you want
  a clean reset, call `POST /instances/{id}/watchover {"enabled": false}`
  on each instance before flipping the kill switch back.
- Set the kill switch to `false` before any maintenance window that
  spans the LLM provider (the watcher will fail-open on LLM outage;
  the kill switch explicitly turns the watcher off so there is no
  hidden latency on every tool call).

---

## The Watcher's Decision Logic

The watcher is a single LLM call per tool-call batch. It is implemented
in `daemon/graph.py:WatchoverEvaluator`. The watcher's job is to evaluate
each tool call in the batch and return a structured verdict.

**Input to the watcher (per tool call):**

```json
{
  "context": "<watchover_context from instance_metadata>",
  "tool_call": {
    "id": "tc_abc123",
    "name": "delete_database",
    "args": {"database": "production", "confirm": true}
  },
  "recent_messages": [
    {"role": "human", "content": "…"},
    {"role": "ai", "content": "…"}
  ]
}
```

**Output (per tool call):**

The watcher returns one of three structured verdicts:

| Verdict | When | Effect |
|---------|------|--------|
| `Allowed` | The watcher believes the tool call is consistent with the requirement. | The batch proceeds to `tools` node. No deny counter increment. |
| `Deny: <reason>` | The watcher believes the tool call violates the requirement. | The batch is denied. One increment on the deny counter. |
| Unparseable | Watcher response is neither `Allowed` nor `Deny: …`. | **Judgment error** — fail-CLOSED. The batch is denied. One increment on the deny counter. |
| `Timeout` / `5xx` / `NetworkError` | The LLM provider failed. | **Infra error** — fail-OPEN. The batch is allowed. **No** deny counter increment. A `watchover_event{status: "degraded"}` SSE is emitted. |

**Why fail-open on infra errors?** An LLM provider outage that causes
every tool call to be denied would produce a mass-termination of watched
instances — a self-DoS. The fail-open policy treats provider failures
as "watcher unavailable; proceed with default permission" and surfaces
the degraded mode via SSE so the operator can take action.

**Why fail-closed on judgment errors?** A watcher that produces an
unparseable response is a configuration or model bug. The safe default
is to deny, log the error, and let the operator investigate.

**Deny-whole-batch semantics.** If the batch contains 3 tool calls and
the watcher denies 1, the batch is **fully denied**: the denied call
receives a denial ToolMessage with the reason, the allowed calls receive
a "Watchover deferred this tool call: another call in this batch was
denied. Please retry." ToolMessage, and the deny counter is incremented
by **one** (not three). The agent sees a clean tool-result protocol
response for every emitted tool_call.

**3-strike termination.** When the deny counter reaches
`max_denials_per_turn` (default 3), the node sets
`watchover_route="watchover_terminate_node"`. The conditional edge
routes to the termination node, which:

1. Persists `instance_metadata.watchover_pending_termination=true` to
   the database (crash-safety net).
2. Adds the instance ID to the in-memory `_deferred_watchover_terminate`
   set.
3. Returns `{}` — the cascade runs from the post-graph completion path,
   not from inside the graph task.

The post-graph completion path consumes the deferred marker and calls
`manager.terminate_instance(instance_id, terminal_reason="watchover_terminated")`,
which cascades the termination to all children. The terminal reason
threads through to the JobItem's `terminal_reason` column so the work
API surfaces the watchover reason via `canonicalize_status` rather than
the generic `"aborted"`.

---

## SSE Events

The watchover feature emits the following SSE events. Operators
subscribing to the instance's `/messages` SSE stream can observe the
watcher's behavior in real time.

| Event | When | Payload | Meaning |
|-------|------|---------|---------|
| `status_change: watchover_active` | Activation completes successfully. | `{instance_id, status: "watchover_active"}` | Watchover is now intercepting tool calls for this instance. |
| `status_change: watchover_inactive` | Deactivation completes successfully. | `{instance_id, status: "watchover_inactive"}` | Watchover is no longer intercepting tool calls. The `watchover_context` and `watchover_requirement` are preserved on disk for audit. |
| `status_change: watchover_failed` | Activation rolled back after a partial-state failure. | `{instance_id, status: "watchover_failed"}` | Activation failed and the rollback block has cleared the partial flag. The original error is logged but NOT propagated via SSE; the operator should check the daemon logs. |
| `watchover_event{status: "degraded"}` | The watcher LLM timed out, returned 5xx, or hit a network error. | `{instance_id, event_type: "watchover_event", status: "degraded", reason: "watcher_infra_error: <exception class>"}` | The watcher is in degraded mode. The batch was allowed (fail-open). The deny counter was NOT incremented. |
| `watchover_denial` | A tool batch was denied. | `{instance_id, tool_call, reason, denial_count}` _(Phase 5 / T5.6)_ | One tool call (or more) in the batch was denied by the watcher. |
| `watchover_terminate` | The 3-strike cap was hit. | `{instance_id, reason: "watchover_terminated"}` _(Phase 5 / T5.6)_ | The instance is being terminated by the watchover cascade. |

**Practical guidance for FE / dashboards:**

- Show a "watchover active" badge when `watchover_active` is received.
- Show a warning banner "Watcher unavailable — tool calls are not being
  vetted" when `watchover_event{status: "degraded"}` fires.
- Show a "watchover denied N tool calls" counter on `watchover_denial`.
- Show a "Terminated by watchover" modal on `watchover_terminate`.

---

## Crash Recovery

The watchover feature is designed to survive a daemon crash at any point
in the activation / evaluation / termination lifecycle.

**Crash during activation (between step 3 and step 5).**

The atomic `set_metadata_many` write ensures the activation is either
fully committed or fully rolled back. If the daemon dies mid-write, the
DB never sees `watchover_enabled=true` without its companion fields.
On restart, the instance loads with `watchover_enabled=false` (the
default) and continues running unwatched. The operator can re-attempt
activation after verifying the instance is in a healthy state.

**Crash during deactivation.**

`disable_watchover` is also a single `set_metadata_many` call. A
crash mid-write leaves the instance in its previous state (likely
`watchover_enabled=true`); on restart, the instance loads as watched
and the operator should manually re-attempt deactivation.

**Crash during tool-call evaluation.**

No state is persisted during evaluation — the deny counter lives in
**per-instance LangGraph state** (not in `instance_metadata`). A crash
during the evaluator's LLM call discards the in-flight verdict; on
restart, the next tool call re-evaluates from the current counter
value. The persistence boundary is the watcher node's return value, not
the LLM call.

**Crash during 3-strike termination.**

This is the critical case. The `watchover_terminate_node` writes
`instance_metadata.watchover_pending_termination=true` to the database
**before** setting the in-memory deferred marker. If the daemon dies
between the DB write and the post-graph completion path consuming the
in-memory marker, the marker is lost but the DB flag survives.

The **stale-marker sweep** in `stale_task_recovery.py` (Phase 5 / T5.7)
runs on startup and detects instances with `watchover_pending_termination=true`
that are still alive. The sweep re-triggers the termination cascade for
each such instance.

**Operational note:** the sweep has a grace period (default 60 s) to
avoid racing with a normal post-graph completion path that is still
processing. If you see `watchover_pending_termination` flags persist
beyond 60 s, the instance is genuinely stuck and the sweep will
terminate it.

---

## Recovering from Stuck Markers

If an instance has a `watchover_pending_termination=true` flag that is
not being cleared by the normal post-graph completion path (e.g. the
graph task never ends because the agent is in a loop), the stale-marker
sweep will eventually terminate the instance. Until that sweep runs, the
instance is in a transitional state.

**Manual recovery steps:**

1. **Inspect the instance state.** Query the database for the
   `instance_metadata` JSONB:

   ```sql
   SELECT instance_id, instance_metadata->>'watchover_pending_termination'
   FROM instances
   WHERE instance_id = '<id>';
   ```

2. **Check the daemon logs** for the `[watchover_terminate_node]` event
   that wrote the flag. If the message `[watchover_drain] deferred
   watchover termination failed for <id>` appears, the
   `terminate_instance` call itself failed — check the database
   connectivity and the cascade target's health.

3. **Force-terminate the instance.** If the instance is genuinely stuck
   and the post-graph completion path is not running, terminate it
   directly:

   ```http
   DELETE /api/instances/{instance_id}
   ```

   This cascades to children and tears down the graph task. The DB
   flag will be cleared on cleanup.

4. **Clear the flag manually (last resort).** If you need to keep the
   instance alive but the flag is stuck, update the metadata directly:

   ```sql
   UPDATE instances
   SET instance_metadata = instance_metadata - 'watchover_pending_termination'
   WHERE instance_id = '<id>';
   ```

   Then call `POST /instances/{id}/watchover {"enabled": false}` to
   disable watchover cleanly. **Do this only when you have verified
   that the agent is behaving correctly** — the watchover termination
   was triggered for a reason, and clearing the flag without
   addressing the underlying unsafe behavior is a workaround.

---

## Known Limitations

### In-Flight Tool Calls (CRITICAL)

Watchover activation does **NOT** guarantee interception of tool calls
that began executing **before** activation was requested.
`pause_instance_cascade` cancels the graph task that owns the tool call
queue, but it cannot stop a tool that is already running in a worker
thread — the tool process holds the thread and the call completes
naturally.

**For maximum safety, activate watchover before the agent begins
autonomous work, or pause the instance manually first.**

```http
# Recommended sequence for activating on a busy instance:
POST /api/instances/{id}/pause      # hard-pause first
POST /api/instances/{id}/watchover  # then activate watchover
POST /api/instances/{id}/resume     # resume
```

When this sequence is not feasible, the quiescence barrier
(`wait_for_instance_quiescent`) reduces the TOCTOU window but does not
eliminate it. The `quiescent: false` response on activation indicates
the barrier timed out and a tool call may have raced with activation —
check the denial counter and the SSE stream to see which calls slipped
through.

### Crash Recovery Edge Cases

The stale-marker sweep has a **60-second grace period** to avoid racing
with the normal post-graph completion path. If the marker is set and
the instance is still alive but the post-graph completion path takes
longer than 60 s, the sweep will terminate the instance. This is a
known low-frequency edge case (P5-R3 in the Phase 5 plan). If you have
instances that routinely exceed 60 s in post-graph completion, raise
the grace period via the stale-task recovery configuration.

The `WATCHOVER_ENABLED` env var is read on **every tool call**; it is
not cached. Flipping the kill switch mid-tool-call does not affect
the in-flight call but takes effect on the next one. There is no
instance-level snapshot of the kill switch state.

### Compaction Timing

The context snapshot is taken **at activation time**. On long-running
sessions (hundreds of turns), the snapshot grows stale — the agent's
recent activity drifts away from the context the watcher is evaluating
against. Phase 5 (T5.3) introduces a configurable freshness refresh
(default: per-turn) that re-derives a lightweight context snapshot
when the conversation advances. Until Phase 5 ships, operators with
long-running watched instances should:

- **Deactivate and re-activate** periodically to refresh the snapshot.
- **Keep the requirement concise** so the watcher LLM cost per call
  stays predictable.
- **Watch the daemon logs** for `watchover_context read failed` —
  this is a benign warning that triggers when the metadata is missing
  but the batch is denying-as-judgment-error in that case.

---

## Troubleshooting

### Watchover silently inactive after restart

**Symptom:** Watchover was active before a daemon restart; after restart,
`is_watchover_enabled(instance_id)` returns `false`.

**Diagnosis:**

1. Check the global kill switch:
   ```bash
   echo $WATCHOVER_ENABLED
   ```
   If it is `false`, the kill switch is OFF and the watcher is
   zero-cost short-circuiting. Set it to `true` and re-evaluate.

2. Check the per-instance flag in the DB:
   ```sql
   SELECT instance_metadata->>'watchover_enabled'
   FROM instances
   WHERE instance_id = '<id>';
   ```
   If the flag is `true` but the watcher is still not intercepting,
   the instance was loaded before the flag restore (Phase 5 / T5.1)
   was wired through every instance-load path. Call
   `POST /instances/{id}/watchover {"enabled": false}` then
   `{"enabled": true, "requirement": "..."}` to force a re-activation.

3. Check the watcher agent directory:
   ```bash
   ls agents/watcher/
   ```
   The watcher reads `agents/watcher/soul.md` at module load. If the
   directory is missing, a minimal fallback prompt is used; in
   production deployments, treat a missing watcher directory as a
   deployment bug.

### Instance stuck in watchover termination loop

**Symptom:** The instance shows `terminal_reason="watchover_terminated"`
repeatedly, or the watchover defer marker is set and re-set on every
turn.

**Diagnosis:**

1. The agent is repeatedly violating the requirement. Read the
   `watchover_requirement` field:
   ```sql
   SELECT instance_metadata->>'watchover_requirement'
   FROM instances
   WHERE instance_id = '<id>';
   ```
   The agent's tool calls are being judged unsafe against this policy.

2. Either the requirement is too aggressive (the agent cannot satisfy
   it with its current tool set) or the agent is genuinely misbehaving.

**Recovery:**

- Soften the requirement: deactivate watchover, re-activate with a
  narrower policy.
- Restrict the agent's tool set so the conflicted calls are no longer
  available.
- If the instance is hung mid-termination, force-terminate:
  ```http
  DELETE /api/instances/{instance_id}
  ```

### Watcher always denies

**Symptom:** Every tool call is denied; the deny counter ticks toward
3-strike on every turn.

**Diagnosis:**

1. **Empty context.** An empty `watchover_context` produces deny
   because the watcher cannot assert safety. Check that activation
   supplied a non-empty `requirement` (or that the compactor produced
   a real summary). Inspect:
   ```sql
   SELECT length(instance_metadata->>'watchover_context'),
          length(instance_metadata->>'watchover_requirement')
   FROM instances
   WHERE instance_id = '<id>';
   ```
   If both are `0` or `NULL`, the operator-supplied requirement is
   missing AND the compactor returned nothing. Either re-activate
   with a non-empty `requirement` or trigger a normal conversation
   turn so the compactor has something to summarize.

2. **Requirement is implausible.** The requirement may be
   contradictory (e.g. *"do not use any tools"* on an agent whose only
   purpose is to use tools). The watcher is correctly denying; the
   operator should refine the requirement.

3. **Watcher model is misconfigured.** The watcher uses the same LLM
   configuration as the agent. If the configured model is broken
   (overloaded, misconfigured, returning garbage), the watcher
   judgment errors fail-CLOSED. Check the daemon logs for
   `[Watchover] judgment error` — a high frequency of these indicates
   the watcher LLM is the problem, not the agent.

### Watcher hangs (no verdicts, no denial)

**Symptom:** Tool calls are blocked indefinitely; the daemon log shows
`[Watchover] infra error` with `TimeoutError`.

**Diagnosis:** The watcher LLM is timing out. The default timeout is
10 seconds (`WATCHOVER_TIMEOUT_SECONDS_DEFAULT`); an infra error
short-circuits the watcher to fail-open **after** the timeout, so the
batch is allowed. Subsequent calls may also time out.

**Recovery:**

1. Check the LLM provider's status.
2. Increase the timeout by editing `agents/watcher/meta.json`:
   ```json
   {
     "watchover": {
       "timeout_seconds": 30,
       "max_denials_per_turn": 3
     }
   }
   ```
3. If the timeout is due to a transient provider blip, the watcher
   will recover automatically when the provider recovers. The
   degraded SSE events tell the operator the watcher is in fallback
   mode during the recovery window.

---

## Configuration Reference

All watchover-side configuration lives in `agents/watcher/meta.json`
under the `watchover` key. The defaults are:

| Key | Default | Description |
|-----|---------|-------------|
| `timeout_seconds` | `10` | Maximum seconds to wait for the watcher LLM response before fail-open. |
| `max_denials_per_turn` | `3` | Number of denied batches (not denied calls) before 3-strike termination. |
| `mirror_message_count` | `5` | Number of recent messages sent to the watcher LLM as conversation context. |
| `llm_model` | n/a | Optional override. Currently only `"quick"` is honored; falls through to the session LLM. |
| `failure_mode` | informational | The evaluator always runs in bifurcated mode (allow on infra, deny on judgment). |

**Global env vars:**

| Var | Default | Description |
|-----|---------|-------------|
| `WATCHOVER_ENABLED` | `"true"` | Global kill switch. Anything other than `true`/`1`/`yes` (case-insensitive) disables watchover for every instance. |

**Per-instance metadata keys (in `instance_metadata` JSONB):**

| Key | Set by | Description |
|-----|--------|-------------|
| `watchover_enabled` | `enable_watchover` / `disable_watchover` | `true` / `false`. The hot-path flag. |
| `watchover_context` | `activate_watchover` | The compaction summary or raw-tail snapshot. |
| `watchover_requirement` | `activate_watchover` | The operator-supplied requirement. |
| `watchover_denial_count` | `watchover_check` + `disable_watchover` | Per-turn denial counter. Reset to 0 on activation. |
| `watchover_pending_termination` | `watchover_terminate_node` | Crash-safety marker. `true` when the 3-strike path has set the in-memory deferred marker. |
| `watchover_transition` | `activate_watchover` (rollback) | Audit marker. Set to `"rollback"` when an activation attempt failed and the partial state was cleared. |

---

## See Also

- `daemon/services/watchover_service.py` — activation / deactivation
  lifecycle (T3.4–T3.6).
- `daemon/graph.py` — `WatchoverSlot`, `WatchoverEvaluator`,
  `create_watchover_check_node`, `create_watchover_terminate_node`,
  `should_end_watchover`.
- `daemon/manager.py` — `is_watchover_enabled`, `enable_watchover`,
  `disable_watchover`, `enable_watchover_lifecycle`,
  `disable_watchover_lifecycle`, `set_deferred_watchover_terminate`,
  `wait_for_instance_quiescent`.
- `daemon/routers/instances.py` — `POST /instances/{id}/watchover` and
  `WatchoverRequest` schema.
- `.agents/shared/planning/watchover/phase5-plan.md` — edge cases,
  crash recovery, persistence, and the full set of T5.1–T5.8 tasks.
- `daemon/services/stale_task_recovery.py` — stale-marker sweep
  (Phase 5 / T5.7).
