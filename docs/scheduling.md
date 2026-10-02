# Scheduling Reference

User/operator-facing reference for scheduled tasks: wall-clock triggers
(one-shot, daily, weekly, or raw cron) that fire a message to an agent at a
specific local time. The surface is exposed two ways — **agent tools** (the
`scheduling` tool category, held by `ari`, `leader`, and `jober`) and **REST
endpoints** on `/api/schedules`. Both call the same shared scheduling service,
so semantics cannot drift between them.

Scheduling is layered on top of the scheduler source adapter
(`source_type="scheduler"` rows in `source_configs`); see
[Pluggable Sources Architecture](pluggable-sources-architecture.md) for the
adapter substrate and [API Reference](api-reference.md) for the full REST
surface.

---

## Tool Surface (agent-facing)

Four tools in the `scheduling` category. Agents holding the category: `ari`
(direct scheduling), `jober` (scheduling inside job orchestration), `leader`
(delegation-only posture).

| Tool | Purpose | Terminal? | Hidden-by-default? |
|------|---------|-----------|--------------------|
| `task_schedule` | Create a schedule | — | — |
| `task_schedule_list` | List schedules (filterable) | — | cancelled hidden |
| `task_schedule_cancel` | Cancel by `source_id` OR `label` | **YES** | YES |
| `task_schedule_update` | Reschedule / re-message / pause / resume | — | — |

### task_schedule

```
task_schedule(
    label="morning-briefing",        # unique, 1-128 chars
    message="Give me a briefing",
    when="2026-10-15T06:00:00",      # ISO 8601 (once) or "HH:MM" (daily/weekly)
    recurrence="daily",              # once | daily | weekly | cron
    timezone="America/New_York",     # optional — see Timezone Rule
    weekday=5,                       # 0=Sunday..6=Saturday (weekly only)
    cron_expression="30 6 * * 1-5",  # required when recurrence="cron"
    agent_id=None,                   # None = the calling agent
    project_id=None,
    priority=5,                      # 1-10
    instance_mode="new_instance",    # new_instance | reuse_instance
)
```

Returns `{source_id, label, status, next_run_at_local, next_run_at_utc,
tz_warning}` — both timestamps are always present (both null when nothing is
upcoming). Duplicate labels are rejected. The invoked agent must exist; an
unknown agent is rejected at create time (fail-fast, before any row is
written).

### task_schedule_list

```
task_schedule_list(status=None, project_id=None, include_cancelled=False, agent_id=None)
```

Returns `{"schedules": [...], "count": N}`. Cancelled schedules are **hidden
by default** (you cancelled them; you do not want to see them) — pass
`include_cancelled=true` to audit them. `agent_id=None` scopes to the calling
agent. Large lists truncate with a `_pagination` hint.

### task_schedule_cancel

```
task_schedule_cancel(source_id=..., )     # or label="morning-briefing"
```

Exactly one of `source_id` / `label` must be provided (`label` resolves by
exact name match; first match wins on collision — prefer `source_id`).
Returns `{source_id, status: "cancelled", cancelled_at, last_execution_id}`.

**Cancel is terminal.** The schedule never fires again — not after a daemon
restart, not after a re-list. Execution history is preserved. To step away
temporarily, use `task_schedule_update(paused=true)` instead — pause is
resumable.

### task_schedule_update

```
task_schedule_update(source_id=..., when="07:30", message=..., timezone=..., paused=None, priority=None, label=None)
```

Pause (`paused=true`) is **resumable**: the row stays and the trigger stops;
it survives daemon restarts. Resume (`paused=false`) rebuilds and restarts
the trigger. Rescheduling a `once` task re-anchors its run time;
rescheduling a daily/weekly task rewrites its wall-clock cron in the
schedule's timezone. Updating a **cancelled** schedule is rejected — create a
new schedule instead.

---

## REST Surface

Phase-3 endpoints (thin wrappers over the same service — identical semantics
to the tools):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/schedules` | Create (mirrors `task_schedule`) |
| GET | `/api/schedules/{id}` | Single fetch — **returns cancelled rows** (the "did I actually cancel X?" path) |
| DELETE | `/api/schedules/{id}` | **Cancel** — terminal, history preserved (mirrors `task_schedule_cancel`) |

Existing schedule routes are unchanged: `GET /api/schedules` (list),
`PUT /api/schedules/{id}`, `POST /api/schedules/{id}/start|stop|trigger`,
`GET /api/schedules/{id}/executions`. Note that `DELETE /api/sources/{id}`
is a different operation — it **purges** execution history; the schedule
cancel endpoints never do. `PUT /api/schedules/{id}` is currently a
DB-only write (the adapter-rebuild on update is a known latency window
— see R6 in `.agents/shared/planning/scheduled-tasks/decisions.md`);
operator concurrent updates can see stale read-then-write drift inside
the rebuild gap.

---

## Timezone Rule (BOTH local + UTC, always)

A user-stated time (`when`) is interpreted in the supplied `timezone`. The
resolution chain, applied at every surface (agent tools and REST alike —
both call the same shared scheduling service):

1. **Explicit tool param** — the `task_schedule*` `timezone` argument
   (`daemon/services/scheduling_service.py` `_resolve_schedule_timezone`) —
   always wins.
2. **User timezone setting** — the `user_timezone` preference row
   (GLOBAL singleton; read in `daemon/services/scheduling_service.py`
   `_resolve_schedule_timezone`). Set + valid **beats the env default with
   NO warning**; unset/invalid falls through **silently**. See
   [User Timezone Setting](#user-timezone-setting).
3. **Daemon-wide default**: `ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`
   (`SchedulingConfig.default_timezone`; resolver `daemon/util/tz.py`
   `resolve_timezone`; unset = skip).
4. **Host-local timezone auto-detection** (`/etc/localtime` symlink, then `TZ`).
5. **Terminal fallback**: stdlib UTC (`datetime.timezone.utc` — never
   `ZoneInfo('UTC')`) — **with a loud warning**.

The terminal-fallback warning is **never suppressed**. It is echoed in
`tz_warning` on create/update responses (set
`ENSEMBLE_SCHEDULING_TZ_WARNING_ECHO_TO_TOOL_OUTPUT=0` to keep it out of
tool output; the log still carries it). Rung 2 never emits a warning — a
valid user preference is a clean resolution. Every response surfaces
**both** `next_run_at_local` (in the resolved timezone) and
`next_run_at_utc`; when the user timezone is set, LOCAL defaults to it.

### User Timezone Setting

A GLOBAL operator preference stored as the raw IANA name under metadata key
`user_timezone` (one row keyed on the system default project — same storage
mechanism as the user language preference). It is the ensemble-wide "user's
timezone" because there is no user/auth identity layer. When set, every
surface that resolves a schedule timezone picks it up at rung 2, and the
per-agent system prompt's "Current Time" section gains two lines
(`User timezone:` / `User local time:`) so agents reason in the user's local
wall clock. Unset, behavior is identical to the pre-feature chain.

Manage it over REST (see [API Reference](api-reference.md) § Settings):
`GET /api/settings/timezone` reads it (with a `utc_offset` convenience echo),
`PUT /api/settings/timezone` writes it — `null`/empty clears, invalid IANA
names are rejected.

Detection caching: positive results cache for
`ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS` (default 300s); negative
results cache for `ENSEMBLE_SCHEDULING_NEGATIVE_CACHE_SECONDS` (default 60s)
so fixing `/etc/localtime` is observed within a minute, not five.

## DST Semantics

One canonical rule for the whole feature (one-shot helper and cron agree):

- **Spring-forward gap** (nonexistent local time, e.g. 02:30 on a 02:00→03:00
  spring-forward night): the one-shot path **shifts forward** to the first
  valid local time after the gap and emits a warning
  (`shifted-forward from nonexistent local time ...`). Croniter applies the
  same skip-the-gap rule for recurring schedules.
- **Fall-back ambiguity** (a local time that happens twice): the **first
  occurrence** is used (fold=0), matching croniter's default.

## Catch-Up Cap (one-shot lateness)

A one-shot schedule found past-due (after a daemon restart or a stall) fires
immediately by default. Set `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`
to cap this: a one-shot later than the cap is **skipped** (a `SKIPPED` row
lands in the execution history, no job is dispatched, the schedule stays
armed for the operator to re-aim). Unset = unlimited (legacy behavior).

## Cancel vs Pause vs Delete

| Operation | Effect | Reversible? | History |
|-----------|--------|-------------|---------|
| `task_schedule_update(paused=true)` | Trigger stops | Yes (`paused=false`) | Intact |
| `task_schedule_cancel` / `DELETE /api/schedules/{id}` | **Terminal** | **No** | **Preserved** |
| `DELETE /api/sources/{id}` | Purges the source row | No | **Destroyed** |

Cancelled schedules stop appearing in the default list but remain retrievable
(`include_cancelled=true` / `GET /api/schedules/{id}`).

## In-Flight Jobs Are Not Cancelled

A trigger that already dispatched its job before the cancel landed runs to
completion — jobs are independent once admitted. `task_schedule_cancel`
echoes `last_execution_id` (the most recent execution) so the operator can
cancel that in-flight job directly. A cancel racing a due fire may therefore
produce one final execution; its history row is the correct record of what
happened.

## Configuration Summary

| Env var | Default | Meaning |
|---------|---------|---------|
| `ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE` | unset | Daemon-wide default IANA timezone for user-stated local times (rung 3 — loses to an explicit param and to the user timezone setting) |
| `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS` | unset (unlimited) | Skip one-shots past-due beyond this many seconds |
| `ENSEMBLE_SCHEDULING_TZ_WARNING_ECHO_TO_TOOL_OUTPUT` | `true` | Include the UTC-fallback warning in tool/list/REST output |
| `ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS` | `300` | Host-local tz detection cache |
| `ENSEMBLE_SCHEDULING_NEGATIVE_CACHE_SECONDS` | `60` | Failed-detection cache (fixes observed within a minute) |
