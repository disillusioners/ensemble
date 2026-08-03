# Bug: Tester Stuck at `waiting_children` Despite Reporting Done (Orphaned `idle` Worker Blocks Completion)

**Date:** 2026-08-03
**Severity:** High
**Status:** Confirmed — root cause identified (investigation only — no code changes)
**Affected Component:** `daemon/services/instance_lifecycle.py` (`get_instance` cold-load path), `daemon/tools/instance.py` (`_resolve_instance_id` / `send_message`), `daemon/services/child_reports.py` (active-children guard)
**Environment:** Production (`ensemble_prod` PostgreSQL), backend on port 8088 / `logs/prod_run.log`

---

## TL;DR

Tester `4585955f` ran to completion and emitted "Testing Complete: all tests green, zero
failures" at `03:12:57`, but never reached `COMPLETED`. The active-children guard in
`child_reports.py:1866-1874` (`status NOT IN TERMINAL_STATUSES`) counted a **ghost worker**
`33477fe4` stuck at `idle` — an instance that was spawned but **never received a message**
(zero `message_queue` rows, zero `task` rows, version 1, zero checkpoints). Because `idle` is
not in `TERMINAL_STATUSES`, the ghost is treated as an active child forever, so the tester's
completion report to its leader is permanently deferred. This also wedges the leader
`52bb9d3e` at `waiting_children`.

The ghost exists because `send_message` to `33477fe4` failed with
`"instance not found"` ~22s after a successful `spawn_instance` — the cold-load path in
`lifecycle.get_instance` returned `None` for a committed DB row. The tester self-recovered by
spawning a replacement (`6bae5d71`) and completing the work there, but the orphaned
`33477fe4` row was never cleaned up, leaving a permanent blocker.

---

## Summary

Leader instance `52bb9d3e-608b-44e2-823b-62e41e9d0272` (agent `leader`) and its direct child
tester `4585955f-42a0-49b7-aa9d-0223c6bd02a1` (agent `tester`) are both stuck at
`waiting_children`:

```
52bb9d3e | leader | waiting_children | version 9
4585955f | tester | waiting_children | version 10
```

The tester's 9 spawned children:

| child | agent | status | notes |
|-------|-------|--------|-------|
| `33477fe4` | worker | **idle** | **ghost** — spawned 02:37:10, version 1, 0 messages, 0 tasks, 0 checkpoints |
| 8 others | worker/explorer | completed | normal |

Only `33477fe4` is non-terminal, so the tester's completion guard
(`active_children > 0` at `child_reports.py:1905`) fires on every turn and defers its
completion report indefinitely.

### The smoking-gun tool responses (decoded from `checkpoints`/`checkpoint_blobs`)

The bug was found by decoding the tester's LangGraph message history (msgpack blobs in
`checkpoint_blobs`), not from `prod_run.log` alone — `send_message` returns error strings to
the LLM rather than logging them, so the failure was invisible in the log.

| msg# | tool | target | result |
|------|------|--------|--------|
| #18 | `spawn_instance` ×3 | `fe65efe7`, `33477fe4`, `fcfab969` | all 3 → **"Successfully spawned"** |
| #22 | `send_message` ×3 | same 3 workers | #23 `fe65efe7` → "queued and sent" ✓<br>#24 `33477fe4` → **`ERROR: instance '33477fe4...' not found and no similar instance found`**<br>#25 `fcfab969` → "queued and sent" ✓ |
| #26-29 | tester self-recovers | spawns `6bae5d71`, sends task | `6bae5d71` → completed the unit-test work ✓ |

The tester **did** correctly attempt to send to `33477fe4`, and gracefully recovered from the
failure. The orphaned `33477fe4` (now permanently `idle`) is what wedges completion.

---

## Root Cause

### Why `send_message` said "not found" for an instance that exists in DB

`spawn_instance` for `33477fe4` succeeded (msg#20 returned the instance_id) and committed the
row — confirmed present in `instances` (`status=idle`, `version=1`,
`created_at=2026-08-02T19:37:10`). Yet 22 seconds later, `send_message`'s
`_resolve_instance_id` (`instance.py:577`) → `manager.get_instance` → `lifecycle.get_instance`
raised `KeyError`.

The decisive log line:

```
02:37:32 - daemon.services.mcp_service - INFO - Lazy-loaded 7 MCP tool schemas from 4 server(s) for instance 33477fe4
```

This is the **cold-load path** in `lifecycle.get_instance` (`instance_lifecycle.py:2229-2234`):
it runs only when the instance is **NOT** in the in-memory cache (`self._manager.instances`).
`spawn_instance` populates that cache synchronously at `instance_lifecycle.py:1096`
(`self._manager.instances[instance_id] = (graph, resolved_agent_dir)`), so by the time the
spawn tool returned success, `33477fe4` was cached. Yet 22 seconds later the cache lookup
missed and the cold-load path ran — and critically, **only `33477fe4` hit cold-load; its
siblings `fe65efe7`/`fcfab969` stayed cached and resolved from cache** (no "Lazy-loaded" log
line for them).

So between spawn (02:37:10) and send (02:37:32):
1. `33477fe4` was **evicted from the in-memory cache** while siblings were not, then
2. The cold-load `instance_repository.get(instance_id)` returned `None` → `KeyError` →
   "not found" (despite the row being committed in the DB).

#### Unexplained: the eviction + the `None` read

The eviction is not explained by any cache-removal site found in code:

| cache-removal site | applies to `33477fe4`? |
|--------------------|------------------------|
| `_release_cached_instance` (TTL sweep at `manager.py:2966`) | **No** — only terminal/paused statuses; `33477fe4` is `idle` |
| `terminate_instance` (`instance_lifecycle.py:1335`) | **No** — no terminate of `33477fe4` in the log |
| `on_instance_deleted`/hard-delete callback | **No** — row still exists |

A cold-load `instance_repository.get` returning `None` for a committed, present row is also
anomalous and needs a reproduction with PG. Most plausible explanations (undetermined):

1. **Transaction-snapshot visibility race.** `_spawn_instance_db_sync` commits in its own
   `WriteGuardSession`; the cold-load `instance_repository.get` runs on a different
   connection. Under PostgreSQL `READ COMMITTED` a committed row should be visible, but a
   connection still inside an older (stale) transaction snapshot would not see it. This needs
   reproduction/inspection of session/transaction isolation around `spawn_instance` and the
   cold-load read.
2. **A transient exception swallowed before `meta = instance_repository.get(...)`**, returning
   a falsy value. (Not found in code; needs reproduction.)

> **Note:** the precise eviction-cum-`None`-read mechanism is the one open item. It is
> **not required** to fix the downstream consequence (the ghost-child blocker) — the
> defense-in-depth fix below unblocks the stuck state regardless of which mechanism produced
> the orphan. The deeper "prevent the ghost" fix does depend on it.

### Why completion is permanently blocked (the certain part)

The active-children guard at `child_reports.py:1866-1874`:

```python
active_children = session.exec(
    select(func.count())
    .select_from(Instance)
    .where(Instance.parent_id == instance_id)
    .where(Instance.instance_id != instance_id)
    .where(Instance.status.not_in(TERMINAL_STATUSES))
).scalar_one()
```

`TERMINAL_STATUSES` (`job_queue_service.py:95`) is `{terminated, completed, error, failed}`.
`idle` is **not** terminal, so `33477fe4` at `idle` is counted as an active child forever.
At `child_reports.py:1905`:

```python
if active_children > 0 or pending_tasks > 0:
    # ... defer completion_report to parent
    return _ChildCompletionDbResult(outcome="child_still_running_defer", ...)
```

This fires on every tester turn, so the tester never sends its completion report. The log at
`03:12:57`:

```
Non-root instance 4585955f... has 1 active children / 0 pending task(s),
deferring completion_report to parent (parent=52bb9d3e..., active-children/pending-tasks guard)
```

The same blind spot also defeats the newly-added **Change B** defense-in-depth gate
(`child_reports.py:1463`, the root live-children cross-check) — it uses the same
`status NOT IN (terminal...)` predicate, so a ghost `idle` child also bypasses it. Note the
tester is a **non-root** instance, so it goes through the active-children guard at `:1866`,
not the root gate at `:1463`; both share the blind spot.

---

## Code Positions

| File | Line(s) | Role |
|------|---------|------|
| `daemon/services/instance_lifecycle.py` | 1096 | spawn caches instance in `self._manager.instances` synchronously |
| `daemon/services/instance_lifecycle.py` | 2225-2236 | `get_instance` cold-load path: cache miss → `ensure_mcp_preloaded` → `instance_repository.get` returned `None` → `KeyError` |
| `daemon/tools/instance.py` | 577-625 | `_resolve_instance_id`: catches `KeyError` from `get_instance`, returns "not found" error string to the LLM |
| `daemon/tools/instance.py` | 1649-1656 | `send_message` guard (unrelated here — `33477fe4` had no messages) |
| `daemon/services/child_reports.py` | 1866-1874 | **active-children guard** — counts `idle` child as active (the blocker) |
| `daemon/services/child_reports.py` | 1905 | `active_children > 0` → defers completion report forever |
| `daemon/services/child_reports.py` | 1463 | **Change B** root live-children gate — shares the same `idle` blind spot |
| `daemon/services/job_queue_service.py` | 95-100 | `TERMINAL_STATUSES` — lacks `idle` |

---

## Impact

- Tester completed its work (via the recovered replacement `6bae5d71`) but can never report
  completion to the leader — stuck at `waiting_children` forever.
- Leader `52bb9d3e` is transitively wedged at `waiting_children` (never receives the tester's
  completion report).
- Any job/observer expecting the tester or leader tree to finalize is stuck. Manual DB
  intervention (terminate/complete `33477fe4`) is required to unblock.
- The bug class is silent in `prod_run.log`: `send_message` returned the "not found" error to
  the LLM as a `ToolMessage`, not as a log line. The only log signal was the downstream
  "has 1 active children / 0 pending task(s), deferring" line repeated on every tester turn.

---

## Suggestions (Investigation Only — Not Implemented)

Priority-ordered by how directly each addresses the confirmed consequence.

### A. (PRIMARY FIX) Exclude `idle` ghost children from the completion guards

`daemon/services/child_reports.py:1866-1874` (active-children guard) and
`daemon/services/child_reports.py:1463` (Change B root live-children gate). Both use
`status NOT IN TERMINAL_STATUSES`. An `idle` child that has **never received work**
(`version=1`, zero `message_queue`/`task` rows) is a dead-end state — no message will ever
arrive for it (its dispatch already failed) — so it must not block its parent.

Two options, both safe:

1. **Minimal:** add `idle` to the exclusion set for both guards. `idle` means "spawned but
   nothing was ever dispatched"; it cannot produce a completion report on its own. Cheapest
   change; directly unblocks this and the residual state.
2. **Tighter:** exclude children with `status = 'idle' AND version = 1 AND NOT EXISTS
   (message_queue row) AND NOT EXISTS (task row)` — a true ghost. More precise but more
   complex; the minimal version is sufficient to prevent the bug class.

Either would have unblocked the tester and leader immediately, regardless of which mechanism
produced the orphan.

### B. (DEEPER FIX — needs reproduction) Prevent the ghost at the spawn/send boundary

The orphan exists because `send_message` failed on a just-spawned, committed instance. Two
angles; neither can be confirmed from logs alone — needs a PG reproduction:

1. **Cold-load `instance_repository.get` returning `None` for a committed row.** Reproduce
   `spawn_instance` then `get_instance` on a fresh instance under PG with concurrent load, and
   inspect transaction snapshots / connection reuse. If the read runs in a stale snapshot, the
   fix is to ensure the cold-load read uses `autocommit` / a fresh transaction (or a direct
   `SELECT ... WHERE instance_id = ?` that bypasses any cached session).
2. **Unexplained cache eviction of a freshly-spawned `idle` instance.** Trace exactly which
   code path removed `33477fe4` from `self._manager.instances` between 02:37:10 and 02:37:32
   (siblings survived). Add a structured log at every `del self.instances[...]` /
   `_release_cached_instance(...)` site so a future eviction is attributable. The current
   cache-removal sites (TTL terminal/paused, terminate, hard-delete) do **not** match an
   `idle` instance 22s old — so there is either an unlogged path or a subtle condition.

### C. Log `send_message` / `_resolve_instance_id` errors authoritatively

`send_message` returns error strings to the LLM (`ToolMessage`) without logging them, so this
incident was invisible in `prod_run.log`. Add a WARNING/ERROR log on the `KeyError` /
"not found" branch of `_resolve_instance_id` (and the `send_message` enqueue-failure path)
with `extra={instance_id, caller_instance_id}`. This would have surfaced the dropped dispatch
at 02:37:32 in real time rather than requiring checkpoint-blob forensics.

### D. (DEFENSE-IN-DEPTH) Auto-terminate orphaned `idle` children

A periodic sweep (mirroring `_sweep_orphan_watchers`) could terminate `instances` rows whose
`status='idle' AND version=1 AND age > N minutes AND no message_queue/task rows AND parent is
not idle`. This cleans up ghosts produced by any spawn/send failure mode, not just the one
observed here. Lower priority than A (which makes ghosts harmless), but valuable for
hygiene/observability.

---

## Immediate Unblock (operator action — applied 2026-08-03)

The stuck state was manually unblocked in production `ensemble_prod` by marking the ghost
worker terminal. The queries below were run against PostgreSQL (`ensemble_prod`) at
~09:33 local (02:33 UTC).

```sql
-- 1. Confirm the ghost (read-only)
SELECT instance_id, agent_id, status, version
FROM instances
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';
-- Confirmed: idle, worker, version 1

-- 2. Confirm it never received work (read-only)
SELECT count(*) FROM message_queue
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';  -- returned 0
SELECT count(*) FROM task
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';  -- returned 0

-- 3. Mark terminal so the active-children guard stops counting it
UPDATE instances
SET status = 'terminated', updated_at = NOW()
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';
```

After this, the next tester turn's `_process_child_completion_db_sync` should see
`active_children == 0` and emit the deferred completion report, unblocking the leader
`52bb9d3e`. **Verification pending** — confirm post-unblock that:

```sql
-- Tester + leader should advance out of waiting_children
SELECT instance_id, agent_id, status, version, updated_at
FROM instances
WHERE instance_id IN (
  '4585955f-42a0-49b7-aa9d-0223c6bd02a1',
  '52bb9d3e-608b-44e2-823b-62e41e9d0272'
);
```

If the tester/leader do not advance on their own (no pending turn to re-run the completion
guard), a no-op message may need to be enqueued to trigger a fresh
`_process_child_completion_and_notify_parent` turn.

---

## Reproduction / Verification Queries (read-only)

```sql
-- The ghost worker blocking the tester (counted as 1 active child)
SELECT instance_id, agent_id, status, version, parent_id, created_at, updated_at
FROM instances
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';

-- Confirms: never dispatched (0 messages, 0 tasks, 0 checkpoints)
SELECT count(*) AS msgs FROM message_queue
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';
SELECT count(*) AS tasks FROM task
WHERE instance_id = '33477fe4-5a75-4766-a594-b173da6451fe';
SELECT count(*) AS ckpts FROM checkpoints
WHERE thread_id = '33477fe4-5a75-4766-a594-b173da6451fe';

-- The tester's 9 children: only 33477fe4 is non-terminal
SELECT instance_id, agent_id, status
FROM instances
WHERE parent_id = '4585955f-42a0-49b7-aa9d-0223c6bd02a1'
ORDER BY status;

-- The stuck parent chain
SELECT instance_id, agent_id, status, version
FROM instances
WHERE instance_id IN (
  '4585955f-42a0-49b7-aa9d-0223c6bd02a1',  -- tester
  '52bb9d3e-608b-44e2-823b-62e41e9d0272'   -- leader
);

-- Messages the tester sent to its three spawned workers at 02:37:32
-- (confirm 33477fe4 never received one — only fe65efe7 / fcfab969 did)
SELECT message_id, instance_id, status, substr(content,1,60) AS preview
FROM message_queue
WHERE source LIKE 'internal_agent:4585955f%'
  AND instance_id IN (
    '33477fe4-5a75-4766-a594-b173da6451fe',
    'fcfab969-dc1a-45ad-a733-65ea05934b43',
    'fe65efe7-a936-4f8e-b1eb-8cf727cadd8b'
  )
ORDER BY enqueued_at;
```

### Checkpoint-blob decode (how the tool-call sequence was recovered)

The `send_message` "not found" error is **not** in `prod_run.log` — `send_message` returns
error strings to the LLM, not log lines. It was recovered by decoding the tester's LangGraph
message history from `checkpoint_blobs` (msgpack):

```python
import msgpack, psycopg
conn = psycopg.connect(host="localhost", port=5432, user="ensemble", dbname="ensemble_prod")
cur = conn.cursor()
cur.execute("""
    SELECT blob FROM checkpoint_blobs
    WHERE thread_id = '4585955f-42a0-49b7-aa9d-0223c6bd02a1'
      AND channel = 'messages'
    ORDER BY version DESC LIMIT 1
""")
data = msgpack.unpackb(cur.fetchone()[0], raw=False, strict_map_key=False)
# Each item is ExtType(code=5); inner msgpack is [class, type_name, fields_dict, method]
msgs = [msgpack.unpackb(m.data, raw=False, strict_map_key=False) for m in data]
# msgs[18] = AIMessage with 3 spawn_instance; msgs[22] = AIMessage with 3 send_message
# msgs[24] = ToolMessage: "ERROR: instance '33477fe4...' not found ..."
```
