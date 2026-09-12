# Research: Instance Lifecycle / Revive Machinery (for generate_chart charter-reuse)

**Date**: 2026-09-10 · **Repo**: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
**Branch**: `feature/generate-chart-charter-reuse` (read via `.git/HEAD`; no bash available — worktree dirty-state NOT verified, no branches switched)
**Scope**: facts only, file:line citations. Read-only research; nothing committed.

---

## 1. Revive-on-send (service layer)

**Where**: `daemon/services/instance_messaging.py`, inside `_prepare_enqueued_message` (the sync DB prelude of `enqueue_message`).

- `enqueue_message` signature — `instance_messaging.py:2070-2083`:
  `(instance_id, message, source="api", priority=1, images=None, metadata=None, *, is_deferred=False, is_background=False, work_id=None, work_id_required=False)`.
  - There is **no `context` / `load_skill` kwarg at the service layer** — those exist only on the agent `send_message` TOOL (§1b). `source` is the origin marker (free string). `metadata` is a free dict.
- Terminal revival block — `instance_messaging.py:1931-1966`:
  - `:1948-1953` `is_terminal_revival = previous_status in (COMPLETED, TERMINATED, ERROR, FAILED)`.
  - `:1954-1959` IDLE / WAITING_CHILDREN / terminal-revival ⇒ `instance.status = InstanceStatus.RUNNING.value`.
  - `:1934-1938` comment: "the checkpoint, message history, and LangGraph thread all persist in the DB and reload on the next graph.astream" — checkpoint reuse is implicit (thread_id = instance_id, cf. `_has_checkpoint` `instance_messaging.py:1495-1507` / `manager.py:8712-8731`).
  - `:1939-1943` **PAUSED deliberately excluded** — enqueue never flips PAUSED; queue rows sit PENDING until resume (`:2115-2122`).
  - `:1961-1966` log "Reactivating terminal instance …".
  - `:1999-2011` fresh-episode attestation reset rides the same transaction (only `priority==1 AND msg_type==HUMAN`; internal agent-to-agent messages do NOT reset).
  - Revive also bumps `version` + `last_activity_at` (documented at `manager.py:7041-7042`; explicit write in `_revive_terminal_instance` at `manager.py:7056-7059`).
- **Dependency watcher re-registration**: on the agent-tool path, `_register_child_completion_watcher` (`daemon/tools/instance.py:605`) is called on EVERY send_message dispatch incl. terminal-revive — `instance.py:3036-3038` ("Shared with convene_council … keeps the parent in waiting_children until the child reports back"; no-op when target is not a child of the sender). `daemon/services/dependency_bus.py:1740-1747` scrubs per-instance parent-error state across terminate/revive cycles so a revived instance doesn't inherit stale parent-failure wiring.

### 1b. `send_message` TOOL kwargs (agent surface)
`daemon/tools/instance.py:2521-2546`: `send_message(instance_id, message, load_skill: str|None = None, context: dict|None = None, ...)`.
- `load_skill` (:2524-2537, mechanics :2714-2731) appends `<meta>{"load_skill": …}</meta>` before dispatch.
- `context` (:2538-2546) structured pre-message context (suggested keys files/notes/plan_ref/conventions).
- Queue-busy guard first (:2930-2936), then dispatch via `manager.enqueue_message` with `source=f"internal_agent:{current_instance_id}"` (:2992-2997).

---

## 2. ReviveGuard — agent-tool path ONLY (critical question answered)

**The guard is enforced ONLY inside the `send_message` TOOL wrapper (and the `job_continue` FAILED branch). A programmatic revive via `manager.enqueue_message` / `InstanceMessagingService.enqueue_message` does NOT pass through it.**

- Refusal check: `daemon/tools/instance.py:2978-2984` — `if routed_via == "enqueue-revive": if manager.get_agent_tool_revive_count(instance_id) >= 1: return "Refused: …"`. It sits AFTER the queue-busy guard and BEFORE `enqueue_message`; the counter increment sits AFTER successful enqueue (`:2999-3012`).
- Counter storage: `daemon/manager.py:773` `self._agent_tool_revive_counts: dict[str, int] = {}` (IN-MEMORY; daemon restart resets — `manager.py:2792-2793`).
- Reader: `manager.get_agent_tool_revive_count` → `manager.py:2814`.
- Recorder: `note_agent_tool_revive` → `manager.py:2816-2895`; SCOPE gate `:2876-2886`: only `prior_status ∈ {ERROR, FAILED}` consumes; **COMPLETED / TERMINATED revives are granted free, counter unchanged**.
- Explicit path-exclusivity (3 independent statements):
  - `manager.py:2794-2799` — "AGENT-TOOL PATH ONLY — the count is bumped exclusively by note_agent_tool_revive, invoked from … send_message terminal-revive in daemon/tools/instance.py AND the FAILED branch of job_continue … User-API revives do not touch it."
  - `manager.py:2832-2834` — "The shared service-layer revive path (daemon/services/instance_messaging.py) must NEVER call this; user-API revives stay uncounted and unblocked."
  - `instance.py:2951-2954` — refusal "returns BEFORE enqueue_message … The user-API revive path … is a different authority — it neither increments the counter nor is blocked by it."
- **Consequence for chart_tools**: a direct `manager.enqueue_message(child_id, …)` revive from tool code hits ZERO revive budget. A second programmatic revive path with no guard also already exists: `InstanceManager._revive_terminal_instance` (`manager.py:~6990-7062`, terminal check :7024-7031, RUNNING write :7056-7059) — used by recovery flows, guarded only by `_write_guard` + `_session_scope`.

---

## 3. Child discovery (finding a caller's existing charter instance)

Best existing queries in `daemon/repositories/instance/repository.py`:

| Helper | Lines | Source table | Notes |
|---|---|---|---|
| `get_by_agent_id(agent_id)` | :393-405 | `instances.agent_id == agent_id` | ALL instances of that agent (every caller's charters mixed together) |
| `get_children(instance_id)` | :424-429 | `instances.parent_id == instance_id` | **Permanent record — includes completed/terminated children** (:236-243 docstring points here for that reason) |
| `count_children(parent_id)` | :439-452 | `instance_hierarchy` (transient working set) | Used by the spawn cap; misses completed children |
| `list_child_ids` | :236-245 | `instance_hierarchy` | Working-set only |
| `list_child_ids_permanent` | :247-261 | `instances.parent_id` | "includes completed / terminated children" (:253-254) |
| `get_tree_ids_permanent(root_id)` | :527+ | parent_id BFS | Subtree enumeration, capped depth |
| `list_by_parent(parent_id)` | :1056-1066 | `instance_hierarchy` | Hierarchy-based (transient) |

- **No single "find child by (parent_id, agent_id)" helper exists** — must combine `get_children(parent_id)` then filter `instance.agent_id == "charter"` in code (both columns available; `parent_id` is indexed, `models.py:61`).
- `subtree_status` tool: backed by `InstanceManager` at `manager.py:10344` / `:10384`; tool constants `daemon/tools/instance.py:1132-1157` (default max 50 instances, hard cap 200, output ceiling 16000 chars). `subtree_messages` helpers/constants: `instance.py:1006-1103`; backend `manager.py:10318`.
- Manager-side descendant scan with terminal-status filter: `manager.py:8683-8710` (`get_tree_ids_permanent` + BFS cap `LIVE_DESCENDANTS_BFS_CAP`).
- Note: chart children are spawned with `instance_name=f"chart-{description[:30]}"` (`daemon/tools/chart_tools.py:107`) — name is truncated description, NOT a stable per-caller key.

---

## 4. Limits & cleanup

**Per-parent direct-children cap**
- Config: `limits.max_children_per_instance`, **default 50** — `daemon/config.py:469`.
- Enforcement: `daemon/services/instance_lifecycle.py:1563-1570` — checked in `spawn_instance` only when `parent_id` provided (root instances skip); `child_count >= max` ⇒ **ValueError** (documented `:1395`), i.e. the spawn is REFUSED. Count source is `count_children` → `instance_hierarchy` rows (transient; rows are deleted when a child completes per `repository.py:251-253`), so completed children free cap headroom.
- All spawns funnel through `InstanceLifecycleService.spawn_instance` (`instance_lifecycle.py:1222`) — this is below the tool layer, so charter-reuse spawns would inherit the cap automatically.

**Cleanup of COMPLETED instances**
- `_cleanup_cached_instances` (`manager.py:4378-4441`): 10-min cadence (:4386); releases **in-memory graphs only** for terminal/PAUSED instances past `INSTANCE_CACHE_TTL_HOURS` (:4391-4400, :4433-4435). "Only affects in-memory cache — **database records remain intact**" (:4382).
- **No DB reaper for instances or checkpoints was found** — no `delete(Instance)` / archive lifecycle for completed instance rows in `daemon/` (checked repositories + lifecycle + manager). Completed instances and their LangGraph checkpoints persist indefinitely (absence-of-evidence finding; revive depends on exactly this persistence, `instance_messaging.py:1934-1938`).
- Related sweeps that do NOT delete instances: injection-queue TTL sweep (`manager.py:4452-4460`), opencode session eviction (:4443-4450).

**Status lifecycle after a turn**
- Revive sets RUNNING (`instance_messaging.py:1958`); "terminal only records WHY the last run stopped" (:1932-1934) ⇒ after each turn the instance returns to a terminal status; on success that is COMPLETED. Corroborating writes: `error_reporting.py:319` (`parent.status = InstanceStatus.COMPLETED.value` when children finish), lifecycle-event vocabulary "completed/terminated/error" (`manager.py:10184-10192`), resume-failure ERROR write (`manager.py:10134-10140`), terminal set in `_cleanup_cached_instances` (:4391-4396) and `stale_task_recovery.py:34-37`.
- **UNCONFIRMED (explicit)**: the exact line of the normal end-of-turn graph→COMPLETED status write (the success-path terminal write inside the turn wrapper) was not pinned despite multiple searches.

---

## 5. Caller identity plumbing & state options

**Closure injection**
- `create_chart_tools(manager, current_instance_id)` — `daemon/tools/chart_tools.py:33`; the tool reads manager + `current_instance_id` from the closure; `generate_chart` defined at `:59`, calls `invoke_agent_and_wait(..., parent_id=current_instance_id, instance_name=f"chart-{description[:30]}", timeout=600.0, return_instance_id=True)` at `:101-110`.
- Call site: `create_instance_tools` → `daemon/tools/instance.py:4316` `chart_tool_list = create_chart_tools(manager, current_instance_id)` (import `:197`). Same closure pattern as knowledge_tools.
- `invoke_agent_and_wait` — `daemon/utils.py:588-704`: pre-generates instance_id (:650), `manager.spawn_instance_with_mcp(agent_id, instance_id, parent_id, project_id, instance_name, invoked_as_tool=True [, model])` (:660-670), `registry.register` (:674), `manager.enqueue_message(source=f"internal_invoke_and_wait:{parent_id or 'system'}")` (:677-682), `registry.wait_for(timeout)` (:689), timeout ⇒ `_try_terminate_orphan` (:691-697). **It ALWAYS spawns a new instance today — no reuse/re-find step** (the gap the feature targets). Semaphore caps concurrency at WORKER_POOL_SIZE-1 (:605-606, :643-647). `model` kwarg exists (:598, :625-632) for caller-based model routing.

**Per-caller state options (observed precedents)**
- In-memory on manager: `_agent_tool_revive_counts` dict keyed by child instance id (`manager.py:773`), sync/await-free relying on asyncio single-thread atomicity (`manager.py:2850-2853`); lost on restart (:2792-2793). This is the natural home for a `{caller_instance_id → charter_instance_id}` map.
- Durable — `instances` table: `parent_id` indexed nullable column (`daemon/repositories/instance/models.py:61`); add-column migrations are the established pattern; discovery-by-query (§3) avoids any new column.
- Durable — SharedMetaKV: `daemon/repositories/shared_meta_kv/models.py:55` (`context_key`), :72 (`UniqueConstraint("context_key","meta_key")`); repository `repository.py:42-47` (all ops scoped by `context_key`), `get_all(context_key)` :104; manager property `shared_meta_kv_repo` (`manager.py:2115-2126`, wired :571). Partition-agnostic: any string may serve as `context_key` (:39) — e.g. a caller instance id. Ambient-KV rendering to agents exists (restart-gated), but direct repo access from tool code is available regardless.

---

## 6. parent_id permanence vs instance_hierarchy transience on revive

- `instances.parent_id` is **PERMANENT** — survives completion, error, terminate→revive:
  - `repository.py:198-199` — "permanent enumeration uses instances.parent_id, hierarchy fallback uses instance_hierarchy".
  - `repository.py:251-257` — hierarchy rows "are deleted when a child completes"; the parent_id walk "includes completed / terminated children" so the child never orphans from its tree.
  - `repository.py:530` — "Walks instances.parent_id (permanent — survives completion, error, …)".
  - `mission_resolver.py:379-381` — "`== instance.parent_id``. Permanent across revive".
  - Models: `parent_id` nullable indexed column (`models.py:61`); `InstanceHierarchy.parent_id` part of composite PK (`models.py:42`).
- **A revived child keeps its parent linkage cleanly**: `parent_id` on the row is never touched by revive (revive writes only status/version/last_activity_at — `manager.py:7056-7059`; RUNNING set `instance_messaging.py:1958`). Query-time lineage via `get_children` / `list_child_ids_permanent` keeps working across revive; `instance_hierarchy` working-set rows do NOT come back on revive (they were deleted at completion) — anything relying on hierarchy (`count_children`, `list_by_parent`, cap counting) will not see a revived-again child until a new hierarchy row is written.
- Blueprint corroboration: "instances.parent_id is permanent for the child row (surviving terminate-to-revive); instance_hierarchy rows are transient, so query-time lineage survives revive while cascade cleanup drains correctly."

---

## Explicitly unconfirmed / gaps

1. Exact file:line of the normal success end-of-turn `instance → COMPLETED` write (graph turn wrapper) — lifecycle is corroborated indirectly (§4) but the write site was not pinned.
2. No DB reaper for completed instances/checkpoints: stated as absence-of-evidence within `daemon/` (manager, lifecycle, repositories, services) — a deletion path outside those trees was not searched.
3. Worktree dirty-state and exact HEAD sha not verified (no bash; `.git/HEAD` read only).
4. `get_agent_tool_revive_count` wiring for `job_continue` FAILED branch (`daemon/tools/job_queue.py`) was taken from the manager docstrings (`manager.py:2797-2799`, :2828-2831), not re-verified in job_queue.py itself.
