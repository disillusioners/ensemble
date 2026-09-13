# Phase 1: Per-Tool Duration Stamping + Long-Call Detector + Duration Observability

## Objective

Establish the in-process detection primitive the rest of the feature
hangs off: (a) **per-tool duration stamping** at the single shared
`"tools"` node seam (one wrapper, covers both watched and manager-less
variants), (b) a **long-call detector service** that scans in-flight
stamps on a periodic loop and fires the cross-phase hand-off when a
child's single tool call exceeds its effective threshold, and (c)
**durable duration observability** so forensics after the next
bd4b36ef-class incident show per-tool elapsed time on every completion
without reconstructing from log timestamps. Phase 1 ships the
*detection surface only* — the nudge *delivery*, dedup state, and
`InstanceMessagingService.enqueue_message` call live in phase 2 behind
a single seam function.

## Coupling

- **Depends on:** None (first phase).
- **Coupling type:** foundation. This phase defines the registry shape,
  stamp lifecycle, threshold-resolution contract, AND the config +
  hard-max + floor + TTL belt that define the canonical threshold
  precedence chain — the hand-off seam that phases 2 (delivery/dedup)
  and 3 (write-side tool) build on.
- **Shared files with other phases:**
  - `daemon/graph.py` — `build_instance_graph` at `:7879` is the
    `add_node("tools", ToolNode(tools, handle_tool_errors=True))` seam
    this phase mutates (replace with the wrapper). Phase 2 does NOT
    touch `graph.py`; it only consumes the hand-off seam.
  - `daemon/api.py` — lifespan wiring at `:715-759` (mirrors
    `WaitingChildrenWatchdog` lifespan pattern) AND shutdown mirror
    after `:1256` (AM-11). Phase 2 does NOT touch the lifespan wiring;
    phase 3 may adjust kill-switch gating behaviour.
  - `daemon/services/long_tool_nudge.py` — **net-new module created
    in this phase**. Holds the registry, the scanner class, the
    hand-off seam function, the per-completion log line, AND the
    canonical constants (`HARD_MAX_THRESHOLD_SECONDS`, `MIN_THRESHOLD_SECONDS`,
    `STALE_STAMP_TTL_SECONDS`, `LONG_TOOL_NUDGE_SOURCE`). Phase 2
    imports `deliver_long_tool_nudge` from this module to wire
    `enqueue_message`; phase 3 imports `HARD_MAX_THRESHOLD_SECONDS`,
    `MIN_THRESHOLD_SECONDS` (AD-38) and `LONG_TOOL_NUDGE_SOURCE`.
  - `daemon/config.py` — **Phase 1 owns `LongToolCallNudgeConfig`** (B1
    re-slice from Phase 2). Phase 2 does NOT touch `config.py`; phase 3
    does NOT touch `config.py`.
- **Shared APIs/interfaces (defined here, consumed downstream):**
  - `LongToolNudgeRegistry` (RAM singleton; `record_start` /
    `clear` / `snapshot` / `clear_for_instance`).
  - `LongToolNudgeScanner.__init__` /
    `LongToolNudgeScanner.run_once` / `run_long_tool_nudge_loop`.
    (Component name reconciled at synthesis — see `decisions.md`
    AD-29: phase 2's `LongToolNudgeScanner` is canonical; the
    phase-1 working name `LongToolNudgeDetector` is renamed.)
  - `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> None`
    — the **hand-off seam**. Phase 1 implements it as a no-op stub
    guarded by `LONG_TOOL_NUDGE_HANDOFF_STUB_ENABLED=True` so phase 1
    lands the registry+scanner observable and the *fire* event
    without yet delivering a parent message. Phase 2 replaces the
    stub body. (See Open Decisions D5.)
  - Threshold key `metadata.long_tool_call_threshold_seconds` (read
    via `SQLModelInstanceRepository.get_metadata_value`, cf.
    `daemon/repositories/instance/repository.py:1931`). The
    read-side floor (`< 60` falls back to default) lives here
    (AD-38) — see Task 4 / T11.
- **Why this coupling:** The wrapper MUST be installed before the
  scanner has any data; the registry MUST exist before the wrapper
  can stamp; the hand-off seam MUST exist before phase 2 can plug in
  delivery. Order is rigid.

## Context

- The `"tools"` node is a bare `ToolNode` at
  `daemon/graph.py:7879`. Both wiring variants — `agent →
  watchover_check → tools` (`:8089-8097`, `:8106-8114`) and the
  manager-less `agent → tools` fallback (`:8131`) — converge on the
  same node. The wrapper lives at `add_node("tools", ...)` so a
  single site covers both. The post-tools router
  (`create_post_tools_router`, `:8135-8142`) is downstream of the
  wrapper and unchanged.
- **Concurrency reality (architectural hard constraint):** LangGraph
  `ToolNode._arun_batch` (`.venv/.../langgraph/prebuilt/tool_node.py:842-846`)
  executes every `tool_call` of a single AI message concurrently via
  `asyncio.gather(*[_arun_one(c, ...) for c in tool_calls])`. A
  node-level wrapper sees only **batch boundaries**: entry ≈ shared
  by all calls in the batch, exit when the gather resolves. Per-call
  `START`/`END` is unavailable without reimplementing dispatch.
  **v1 consequence:** stamping is per-`tool_call_id` at batch entry
  (all ids stamped with the SAME `started_at`) and per-`tool_call_id`
  clear at batch return (each `id` clears independently, but its
  observed duration equals the batch wall-clock, which OVERESTIMATES
  fast calls sharing a batch with slow ones). This is the
  conservative direction — false positives are recoverable (parent
  decides; phase 2 may include a "this looks slow" tag), false
  negatives (under-detection of bd4b36ef) are not. **Pin the
  overestimation semantics in a docstring** and a regression test;
  See Open Decisions D1.
- **Exception paths:** `handle_tool_errors=True` means tool errors
  return `ToolMessage` objects rather than raising
  (`:7879`). Stamps clear normally on the error path. The remaining
  risk is **node-level cancellation/exception** — pause cancels the
  graph task via `graph_task.cancel()` and the task-cap timeout
  (7200s) raises `asyncio.TimeoutError` or `CancelledError` inside
  the wrapper. The wrapper MUST clear stamps in a `finally` block so
  no entry leaks as eternal in-flight.
- **Watchdog must NOT gate on heartbeat:** `TaskHeartbeat`
  (`daemon/services/worker_pool.py:60-163`) is per-task and beats
  every 30s independent of tool execution — a wedged-mid-tool turn
  stays "alive" forever (the live-rung gate
  `_has_recent_heartbeat` at `waiting_children_watchdog.py:848-890`
  fail-closed returns `True` on probe error and is designed to
  SUPPRESS nudges for exactly this busy-slow case). The long-tool
  detector **keys on in-flight stamp age ONLY**, never on heartbeat.
  A regression-pin test proving heartbeat-fresh + long stamp →
  detector still fires is REQUIRED (TestLongToolNudge.heartbeat_fresh_does_not_gate).
- **Threshold read pattern:** `get_metadata_value(instance_id, key)`
  (`daemon/repositories/instance/repository.py:1931`) is a sync,
  single-key read that does NOT hydrate the row. The watchdog uses
  the same sync-repo surface from its async scan loop
  (`waiting_children_watchdog.py:939`, `list_waiting_children_parents`
  sync call inside `try`/`except`). Mirror that pattern.
- **Lifespan wiring:** the watchdog is lifespan-wired, NOT a service
  registry — `daemon/api.py:715-759` constructs, logs ERROR + sets
  `app.state.waiting_children_watchdog_task = None` on ctor
  failure, and on success + enabled calls
  `asyncio.create_task(run_..._loop(...), name=...)` storing the task
  on `app.state`. The new detector follows the same template.
- **Configuration:** No `WatchdogConfig`-style class exists. The
  watchdog uses flat `ServicesConfig` fields
  (`config.py:1271-1312`, `SERVICES_*` env prefix, `ge=` validators,
  fail-fast-at-boot). The phase 3 spec covers config details; phase
  1 CONSUMES the resolved values via `config.long_tool_nudge.enabled`
  / `.interval_seconds` / `.default_threshold_seconds` working names.
  (Concrete env names land in phase 3 — see Open Decisions D6.)
- **Persistent metadata:** `instance_metadata` is a JSONB column at
  `daemon/repositories/instance/models.py:64-67`; reads via
  `get_metadata_value` `:1931`, atomic upsert via
  `set_metadata` `:2099` / `set_metadata_many` `:2175`. The
  parent-tunable threshold is stored at
  `metadata.long_tool_call_threshold_seconds` (int seconds; absent
  ⇒ detector uses the default).
- **Episode keys (defined here for phase 2 to consume — two levels,
  reconciled at synthesis, `decisions.md` AD-2/AD-31):** the STAMP-LEVEL
  fire-dedup key is `(child_id, tool_call_id)` — `tool_call_id`
  distinguishes two long calls from the same child with different
  `tool_call_ids` (bd4b36ef issued five different bash signatures;
  loop breaker evaded consecutive identical signatures), and it
  prevents the scanner re-firing the SAME in-flight stamp on
  consecutive ticks. The NUDGE-LEVEL episode-dedup key — owned by
  phase 2 — is `(parent_id, child_id)`: one nudge per (parent, child)
  episode, **closed by phase 2's `close_episode` ONLY when the wrapper
  records a HEALTHY `tool_end` clear (`duration_seconds < effective_threshold_seconds`,
  AD-9 + AD-42 Option ii close-gate pin — see Task 3)**; re-armed by
  a NEW `tool_call_id` crossing after that close. A LONG completion
  (`duration_seconds >= threshold`) intentionally LEAVES the episode
  open — that completion IS the wedge the parent was warned about;
  closing on it would re-arm prematurely and break SC2 (the bd4b36ef
  replay would emit 5 nudges instead of 1). The orphan-hygiene sweep
  in `run_once` and the AD-37 TTL belt (`STALE_STAMP_TTL_SECONDS=7200`)
  are the belt+suspenders for a missed close. Both sets live in the
  one scanner class and gate different actions; the episode CONTEXT
  carries `tool_call_id` throughout (`EpisodeCtx`). `tool_name` is
  advisory metadata on the notice, not part of either key. See Open
  Decisions D3.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/services/long_tool_nudge.py` module skeleton with `__all__`, module docstring, module-level logger, and **canonical constants** (B1/AM-8(a)) | Docstring records: (a) the per-batch overestimation semantics (D1), (b) the heartbeat-independent detection invariant, (c) the hand-off seam that phase 2 fills in, (d) the canonical threshold precedence chain (AD-41 cross-ref). Module-level constants: `HARD_MAX_THRESHOLD_SECONDS = 1800` (AD-19, AD-30 — canonical home), `MIN_THRESHOLD_SECONDS = 60` (AD-38, AD-26, AD-38 — canonical home), `STALE_STAMP_TTL_SECONDS = 7200` (AD-9a/AD-37 belt; decoupled from effective graph-task cap per P-1). Imports: `asyncio`, `logging`, `threading`, `time`, `dataclasses`, `typing`, and from `daemon.services.waiting_children_watchdog`-adjacent codebases the watchdog-style logger naming. | `daemon/services/long_tool_nudge.py` (new) |
| 2 | Implement `LongToolNudgeRegistry` — RAM singleton, async-safe | `class LongToolNudgeRegistry` with: `__init__(self)` initialising `_stamps: dict[str, dict[str, _Stamp]]` (instance_id → tool_call_id → `_Stamp`), an `asyncio.Lock` (since the stamp is read by the scanner loop and written by the graph node wrapper — both async), and a `_max_tracked_instances: int` guard (e.g. 1024 — drop oldest instance on overflow with WARN log). **`_Stamp` shape (AD-42 Option (ii) cached-parent_id pin — picked over AD-5's scan-time-read path to keep the close authoritative on `finally`):** dataclass `_Stamp(tool_call_id: str, tool_name: str, started_at: float, parent_id: str)` — `started_at` is `time.monotonic()` (never wall-clock), and `parent_id` is captured at `record_start` (one sync repo read per `tool_start`, cached for the stamp's lifetime) so the wrapper's `finally` can call `close_episode(parent_id, child_id)` without re-reading at `tool_end`. Methods: `async def record_start(self, instance_id: str, tool_call_id: str, tool_name: str, parent_id: str) -> None` (signature pinned — every caller passes the cached parent_id; AM-12 verification pins the repo `get(...)` shape), `async def clear(self, instance_id: str, tool_call_id: str) -> None` (returns the cleared `_Stamp` or `None`), `async def clear_for_instance(self, instance_id: str) -> list[_Stamp]` (returns all cleared stamps; used by pause-cancel sweep), `async def snapshot(self) -> dict[str, dict[str, _Stamp]]` (returns a shallow copy so the scanner can iterate without holding the lock). **Close access path (NON-BLOCKING B-N2 pin, see Task 3):** registry also exposes `def attach_close_handler(self, handler: Callable[[str, str], None]) -> None` (called once at lifespan init with the scanner's bound `close_episode`) and `async def close_episode_for(self, parent_id: str, child_id: str) -> None` (delegates to the attached handler; NoOp if no handler attached). The wrapper reaches close through the registry it already holds — no new kwarg on `_wrapped_tools_node(tools, registry)`, no scanner import, no import cycles (AD-28 module-singleton preserved). | `daemon/services/long_tool_nudge.py` |
| 3 | Implement `_wrapped_tools_node(tools, registry)` factory replacing `ToolNode(tools, handle_tool_errors=True)` at `daemon/graph.py:7879` | Returns an async-callable node function `(state, config) -> dict` (AM-9 simplified: drop the unused `writer` kwarg; stream-writer access arrives via `config`, not the node signature — verified per architecture-recommendation §4.4). Body shape: (1) extract the last `AIMessage` from state (mirrors `ToolNode`); (2) for every `message.tool_calls[i]`, call `await registry.record_start(instance_id, tc["id"], tc["name"], parent_id)` where `instance_id = config["configurable"]["thread_id"]` and `parent_id` is read ONCE at stamp-start via `instance_repo.get(instance_id).parent_id` (one sync read per `tool_start`, cached on the `_Stamp` for its lifetime per AD-42 Option (ii) — Task 2 pin); (3) `try:` invoke the underlying `ToolNode(...).ainvoke(state, config)` (the bare `ToolNode` continues to dispatch via `asyncio.gather`); (4) **`finally:` (BLOCKING AD-9 close-gate pin — the wrapper is the close hook):** for each stamped `(tc["id"], stamp)` pair — `await registry.clear(instance_id, tc["id"])` (returns the cleared stamp), compute `duration_seconds = (time.monotonic() - stamp.started_at)` and `threshold_seconds` (looked up from the stamp's effective threshold, or resolved via the same `_resolve_threshold(instance_id)` the scanner uses for parity), then **emit the per-completion log line (Task 6)** with `duration_ms = int(duration_seconds * 1000)` and `threshold_crossed = (duration_seconds >= threshold_seconds)`. **Close-gate (canonical AD-9 + AD-42 Option ii semantics — locked here):** if `duration_seconds < threshold_seconds` (HEALTHY completion — the wedge the parent was warned about is over; this completion is the recovery event), call `await registry.close_episode_for(stamp.parent_id, instance_id)` (the registry method defined in Task 2 — the wrapper's chosen access path; passes through to the scanner's `close_episode(parent_id, child_id)` attached at lifespan init; preserves the AD-28 module-singleton design with no new imports in the wrapper). **A LONG completion (`duration_seconds >= threshold_seconds`) intentionally LEAVES the episode open** — that completion IS the wedge the parent was nudged about; closing on it would either erase the parent's view of the ongoing wedge (if the stamp-level dedup has the tuple) or, worse, allow a fresh `tool_call_id` on the same child to start a *new* episode immediately, producing one nudge per long call (the bug the approver flagged — U2b would emit 5 nudges instead of 1 for the bd4b36ef replay). The episode closes later via (a) a SUBSEQUENT healthy completion on the same child (the close re-arms the next `tool_call_id`), or (b) the AD-37 stamp-TTL belt (`STALE_STAMP_TTL_SECONDS = 7200`) which force-clears stale stamps and discards orphan `_active_episodes` tuples at end-of-`run_once` (belt + suspenders, joins Task 10). **No new kwarg on `_wrapped_tools_node(tools, registry)`** — the registry reference the wrapper already holds carries the close handler (Task 2). **Critical: do NOT mutate the bare `ToolNode` — instantiate it locally and delegate.** This keeps `handle_tool_errors=True` semantics, `Command` outputs, and the `_combine_tool_outputs` return shape untouched. Pin via `TestWrappedToolsNodeNoBehaviorChange` (mock tool, verify identical output shape); pin the gate via `TestWrappedToolsNodeCloseEpisodeGatedOnHealthyCompletion` (long completion leaves `_active_episodes` populated; healthy completion removes it). | `daemon/graph.py:7879`; `daemon/services/long_tool_nudge.py` (factory `_wrapped_tools_node`); `.venv/.../langgraph/prebuilt/tool_node.py:842-846` (delegate target — read-only) |
| 4 | Implement `LongToolNudgeScanner` class + threshold resolution (BOTH-sides floor per AD-38) | `class LongToolNudgeScanner` (name reconciled per `decisions.md` AD-29) with: `__init__(self, *, instance_repository, registry, manager, enabled=True, interval_seconds=60, default_threshold_seconds=900, hard_max_threshold_seconds=1800, handoff_stub_enabled=True, handoff_fn=None)` (the `handoff_fn` parameter is the test seam — production uses the stub from Task 5; `registry` IS the injected `_LONG_TOOL_REGISTRY` singleton — the scanner owns NO parallel stamp store, see AD-28). Ctor validates `interval_seconds > 0`, `default_threshold_seconds >= 1`, `default_threshold_seconds <= hard_max_threshold_seconds`, `hard_max_threshold_seconds <= 86400` (24h ceiling — fail-fast per watchdog pattern, `:481-512`). `_resolve_threshold(instance_id) -> int`: reads `metadata.long_tool_call_threshold_seconds` via `get_metadata_value(instance_id, key)` (sync, mirrors `waiting_children_watchdog.py:939`); **read-side floor (AD-38)**: if `None` OR not int OR `< MIN_THRESHOLD_SECONDS (60)` OR `> hard_max_threshold_seconds`, falls back to `default_threshold_seconds`; ALWAYS defensively `min(value, hard_max_threshold_seconds)`. **Per-tick memoization (cost note):** the scanner reads the threshold **once per tick per child with a stamp** (one `get_metadata_value` per `(instance_id)` per `run_once`); not per stamp. **CLOSE MECHANISM (B4/AD-42 pick Option ii):** the scanner's `run_once` ALSO sweeps `_active_episodes` for orphan tuples whose child stamp has fully cleared — discard each orphan (belt + suspenders, joins AD-37 / Task 10 TTL sweep). `async def run_once(self) -> dict[str, int]`: returns `{"instances_scanned": N, "stamps_inspected": M, "fired": K, "skipped_disabled": 0|1, "errors": E, "stale_stamps_force_cleared": X, "orphan_episodes_discarded": Y}` (same shape as watchdog `:898-905` plus the belt + orphan counters; pin via `TestTickStatsShape`); iterates `await registry.snapshot()`; for each `(instance_id, tool_call_id, stamp)`, computes `now - stamp.started_at`, fires when `> threshold AND tool_call_id not in self._fired_episodes` (STAMP-LEVEL fire dedup, in-memory set of `(child_id, tool_call_id)` — prevents consecutive-tick re-fires of the same stamp; the NUDGE-LEVEL `(parent_id, child_id)` dedup is phase 2's `_active_episodes`, a different gate on a different action — see AD-2/AD-31; pin with `TestDetectorFiresOncePerToolCallId`). **AM-7 pin:** `_fired_episodes.add(...)` happens ONLY on a *successful* fire (i.e. after the seam call returns True). A PAUSED-parent skip (AD-12 / AM-7) does NOT advance the stamp-level dedup, so the same crossing remains eligible on resume with fresh numbers. Per-instance `try/except Exception` (error isolation per watchdog `:919-922`). `_read_parent_id(child_id) -> str | None` reads `instance.parent_id` via the existing repo (lazy; defer DB hit until threshold-cross to avoid hot-path cost). | `daemon/services/long_tool_nudge.py`; `daemon/repositories/instance/repository.py:1931` |
| 5 | Implement the **hand-off seam** `deliver_long_tool_nudge(parent_id, child_id, episode_ctx)` | `async def deliver_long_tool_nudge(parent_id, child_id, episode_ctx: EpisodeCtx) -> None`. `EpisodeCtx` is a dataclass: `tool_call_id: str`, `tool_name: str`, `started_at_monotonic: float`, `elapsed_seconds: float`, `threshold_seconds: int`, `fired_at_monotonic: float`, `registry_observed_at: float`. **Phase 1 body** is a stub gated by `handoff_stub_enabled` (default `True` in ctor; phase 2 flips to `False` and fills the real `enqueue_message` call): emit a single INFO log line with a stable grep-able marker `[LongToolNudge] STUB_FIRE` carrying the `EpisodeCtx` fields. **This is the contract phase 2 inherits.** The log line is the forensic record of "the detector fired" — do NOT change the field set between phase 1 and phase 2 (phase 2 may ADD fields like `parent_decision_hint` but must not drop `tool_call_id` / `tool_name` / `elapsed_seconds`). | `daemon/services/long_tool_nudge.py` |
| 6 | Implement per-completion duration log line (`threshold_crossed` bool in INFO log; no DB write) | At wrapper completion (Task 3's `finally`), after stamping clear, emit ONE structured log line at INFO with a stable grep-able marker `[LongToolNudge] TOOL_COMPLETED`: fields `instance_id` (truncated to 8 chars like watchdog `:887`), `tool_call_id`, `tool_name`, `duration_ms` (int), `threshold_seconds` (the resolved effective threshold), `threshold_crossed` (bool = `duration_seconds > threshold_seconds`). **No DB write** in phase 1 — recommend phase 3 evaluate persistence after observing production write volume (D7). Log at INFO regardless of threshold crossing (operator wants duration on every tool completion, not just the slow ones — bd4b36ef forensics needed 5 prior durations that never existed). | `daemon/services/long_tool_nudge.py` |
| 7 | Wire lifespan in `daemon/api.py` mirroring `daemon/api.py:715-759`; **explicit shutdown mirror per AM-11** | After the `WaitingChildrenWatchdog` lifespan block (`:759`), add an identical-shape block. **Import the module-level `_LONG_TOOL_REGISTRY` singleton explicitly** (AM-11 — the wrapper in `graph.py` and the scanner in this block MUST share one instance; per-graph/per-ctor allocation silently no-ops the whole feature; pinned by T8). Import `LongToolNudgeScanner`, `run_long_tool_nudge_loop`, AND `LongToolCallNudgeConfig`'s resolved values from the new module. **Lifespan wiring pattern (B-N4 pin — repo reuse must be UNAMBIGUOUS):** in the api.py lifespan block, construct `SQLModelInstanceRepository(engine=manager.engine)` **once**, bind it to a local `instance_repo`, and pass THAT SAME `instance_repo` to BOTH the `WaitingChildrenWatchdog` ctor AND the new `LongToolNudgeScanner` ctor. **Example (do NOT diverge):**
```python
# Construct the repo ONCE — reused by both the watchdog and the long-tool-nudge scanner.
instance_repo = SQLModelInstanceRepository(engine=manager.engine)
# Watchdog block (existing pattern at api.py:715-759):
watchdog = WaitingChildrenWatchdog(instance_repository=instance_repo, ...)
# Long-tool-nudge block (new, mirrors the watchdog block shape):
scanner = LongToolNudgeScanner(instance_repository=instance_repo, ...)
# Lifespan also wires the close handler into the registry (Task 2 method, AD-42 Option ii):
_LONG_TOOL_REGISTRY.attach_close_handler(scanner.close_episode)
```
The previous draft example `SQLModelInstanceRepository(engine=manager.engine)` inline at the scanner ctor was ambiguous — it read as fresh construction. **This is the only correct shape** (two engine-holding repos on the same engine would silently double the connection pool; a row-hydrating `.get(...)` call also uses one connection). `try/except` around construction sets `app.state.long_tool_nudge_task = None` and logs ERROR on ctor failure (same pattern `:735-741`). `else:` branch: if `enabled`, `asyncio.create_task(run_long_tool_nudge_loop(scanner, interval_seconds=...), name="long-tool-nudge")` and store on `app.state.long_tool_nudge_task`; else `app.state.long_tool_nudge_task = None` and log "disabled by config". The loop function `run_long_tool_nudge_loop(scanner, *, interval_seconds)` is structurally identical to `run_waiting_children_watchdog_loop` (`waiting_children_watchdog.py:1668-1732`) — same `while True` + `await scanner.run_once()` + `except CancelledError: raise` + `except Exception: logger.error` + `await asyncio.sleep(interval_seconds)` pattern. Disable check at function entry: if `not scanner.enabled`, log INFO + return (mirroring `:1698-1702`). **Shutdown mirror (AM-11):** after the watchdog cancel/await at `api.py:1256`, before `manager.shutdown()`, add: `task = getattr(app.state, "long_tool_nudge_task", None); if task: task.cancel(); try: await task; except asyncio.CancelledError: pass; except Exception as e: logger.warning("Long-tool-nudge shutdown error: {e}"); finally: app.state.long_tool_nudge_task = None`. The `getattr` guard survives partial-startup failures. | `daemon/api.py:759` (insertion site); `daemon/api.py:1256` (shutdown mirror); `daemon/services/long_tool_nudge.py` (loop function); `daemon/services/waiting_children_watchdog.py:1668-1732` (template) |
| 8 | Update graph.py to use the wrapper | Single-line change at `daemon/graph.py:7879`: `graph.add_node("tools", _wrapped_tools_node(tools, registry=_LONG_TOOL_REGISTRY))`. Import `_wrapped_tools_node` and the module-level `_LONG_TOOL_REGISTRY = LongToolNudgeRegistry()` singleton from `daemon.services.long_tool_nudge`. **Critical:** the registry singleton is module-level so the lifespan-wired detector in `api.py` and the graph-wired wrapper in `graph.py` share the SAME instance. Define both the singleton and the wrapper factory in the new module; do NOT allocate a registry inside `_wrapped_tools_node` (graph builds many graphs per process — singleton is the only correct shape). Verify the post-tools router (`create_post_tools_router` `:8135-8142`) and question-pause branch (`:8133`) are downstream of the wrapper and unchanged. | `daemon/graph.py:7879` (single-line replacement); `daemon/services/long_tool_nudge.py` (`_LONG_TOOL_REGISTRY` singleton + `_wrapped_tools_node` factory) |
| 9 | Add `__init__.py` export hook | Add the new module to any internal `services` re-export list if one exists (grep `daemon/services/__init__.py` — if absent, no change needed). The module must export `LongToolNudgeRegistry`, `LongToolNudgeScanner`, `run_long_tool_nudge_loop`, `deliver_long_tool_nudge`, `_wrapped_tools_node`, `_LONG_TOOL_REGISTRY`, `HARD_MAX_THRESHOLD_SECONDS`, `MIN_THRESHOLD_SECONDS`, `STALE_STAMP_TTL_SECONDS`, `LONG_TOOL_NUDGE_SOURCE` via `__all__`. | `daemon/services/long_tool_nudge.py` (top-level); `daemon/services/__init__.py` (only if a re-export list exists) |
| 7a | Add `LongToolCallNudgeConfig` nested class + `EnsembleConfig` wiring in `daemon/config.py` (B1/AM-8) | Add nested `LongToolCallNudgeConfig(BaseSettings)` **between** `ReportIntegrityConfig` (`:1759`) and `LanguageConfig` in `daemon/config.py`: `model_config = SettingsConfigDict(env_prefix="LONG_TOOL_NUDGE_")`; `enabled: bool = Field(default=True, ...)`; `interval_seconds: int = Field(default=60, ge=1, ...)`; `default_threshold_seconds: int = Field(default=900, ge=1, le=HARD_MAX_THRESHOLD_SECONDS, ...)`. **Import `HARD_MAX_THRESHOLD_SECONDS`** (and `MIN_THRESHOLD_SECONDS`, AD-38) from `daemon.services.long_tool_nudge` for the `le=` validator — import direction config→services is acyclic (AD-30). Add the wiring line on `EnsembleConfig` immediately after the LoopBreaker wiring (`:2109`): `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=LongToolCallNudgeConfig)`. Add to `__all__` if one exists; lazy-import if config uses lazy nested-config loading. **Phase 1 owns the config plumbing, not Phase 2** (B1 re-slice). The `_resolve_threshold` signature MUST handle the floor: invalid metadata falls back to default (see Task 4). | `daemon/config.py:1759-2109`; `daemon/services/long_tool_nudge.py` (canonical constants) |
| 10 | Implement stamp-TTL belt end-of-`run_once` force-clear + orphan hygiene (B2/AD-9a/AD-37) | At the end of every `run_once` tick (after fire-dedup evaluation): sweep `registry.snapshot()`. For each stamp with `age > STALE_STAMP_TTL_SECONDS` (= 7200 = `4 × HARD_MAX_THRESHOLD_SECONDS`): (a) `registry.clear(instance_id, tool_call_id)` (force-clear); (b) `_active_episodes.discard((parent_id, child_id))` (close episode); (c) `_fired_episodes.discard((child_id, tool_call_id))` (re-arm stamp-level dedup); (d) `logger.warning("[LongToolNudge] STALE_STAMP force-cleared ...")`. Hygiene sweeps in the same pass: (e) discard `_fired_episodes` entries whose `(child_id, tool_call_id)` no longer appears in the snapshot — prevents unbounded set growth from any clear-path that bypasses the scanner's bookkeeping; (f) discard orphan `_active_episodes` tuples whose child stamp has fully cleared (B4 — joins AD-42 Option (ii) belt+suspenders). Belt counters land in `run_once`'s return dict (`stale_stamps_force_cleared`, `orphan_episodes_discarded`) — used by `TestTickStatsShape`. Zero-false-positive invariant: legitimate in-flight stamps cannot outlive their task (process task supervision cancels them first). | `daemon/services/long_tool_nudge.py` (scanner `run_once`) |

## Tests

| # | Test file | Class / function | What it pins |
|---|-----------|------------------|--------------|
| T1 | `tests/unit/services/test_long_tool_nudge_registry.py` (new) | `TestLongToolNudgeRegistry` | `record_start` / `clear` / `snapshot` concurrency under `asyncio.gather` of 50 concurrent writers + 1 snapshot reader; `clear` returns the cleared stamp; `clear_for_instance` returns the right list and leaves no orphans; overflow cap (1024 instances) drops oldest with WARN log; empty `snapshot()` returns `{}`; `record_start` with duplicate `(instance_id, tool_call_id)` keeps the FIRST stamp (idempotent — the gather case where two nodes share an id must not race-stomp). |
| T2 | `tests/unit/services/test_long_tool_nudge_threshold.py` (new) | `TestThresholdResolution` | metadata value used when int and `1 <= v <= hard_max`; metadata value >hard_max capped (e.g. `3600` → `1800`); metadata value `<1` falls back to default; metadata missing returns default; metadata non-int (e.g. `"900"` string from SQLite round-trip — see `get_metadata_value` `:1975-1982` JSON re-parse) falls back to default; default `<1` rejected by ctor; `hard_max_threshold_seconds > 86400` rejected by ctor; `default_threshold_seconds > hard_max_threshold_seconds` rejected by ctor (mirroring watchdog `:507-512`). |
| T3 | `tests/unit/services/test_long_tool_nudge_detector.py` (new) | `TestLongToolNudgeScannerBoundary` | Fires at `elapsed > threshold` (strictly greater — matches watchdog's `age > threshold` SQL pattern); does NOT fire at exactly `elapsed == threshold`; does NOT fire below. **Pin the off-by-one here** — `>` not `>=`. `TestLongToolNudgeScannerHeartbeatFreshStillFires` — wire a fresh `TaskHeartbeat` stub (always returns recent) into the scanner context and verify the scanner fires (regression pin against the Wedged-Turn-Aliver-Child trap — D2 follow-up). `TestLongToolNudgeScannerFiresOncePerToolCallId` — two ticks with the same still-in-flight stamp: fires on tick 1, does NOT fire on tick 2 (in-memory `_fired_episodes` STAMP-LEVEL dedup — the NUDGE-LEVEL `(parent_id, child_id)` dedup is phase 2's separate gate, AD-2/AD-31). `TestLongToolNudgeScannerPerInstanceErrorIsolation` — one instance raises during threshold resolution, the rest of the snapshot still scans; `errors` counter increments; `notices_enqueued` counter for healthy instances is unaffected (mirrors watchdog `:919-922`). |
| T4 | `tests/unit/services/test_long_tool_nudge_wrapper.py` (new) | `TestWrappedToolsNodeNoBehaviorChange` | Given a tool returning `ToolMessage(content="ok", tool_call_id="x")`, the wrapper returns the SAME shape (list / dict per `input_type`) — `ToolNode._combine_tool_outputs` semantics preserved; `_arun_batch` delegate confirmed by patching the underlying `ToolNode.ainvoke` and asserting it was called exactly once with `(state, config)`. `TestWrappedToolsNodeStampsAndClears` — entry stamps all `tool_calls[*].id`; return clears all stamped ids; `snapshot()` is empty after. `TestWrappedToolsNodeExceptionClearsStamps` — mock the underlying `ToolNode.ainvoke` to raise `RuntimeError`; verify `finally` cleared every stamped id (snapshot is empty) AND the exception propagated (do NOT swallow). `TestWrappedToolsNodeCancelClearsStamps` — raise `asyncio.CancelledError` from the underlying node; verify snapshot empty post-cancel + cancellation propagated. `TestWrappedToolsNodeHandleToolErrorsTrue` — a tool that returns an error `ToolMessage` (no raise) clears normally and the wrapper returns it (no exception). |
| T5 | `tests/unit/services/test_long_tool_nudge_loop.py` (new) | `TestRunLongToolNudgeLoop` | Disabled detector → loop returns immediately, no `run_once` calls. Enabled → patch `asyncio.sleep` (watchdog test pattern) and assert `run_once` called ≥3 times. `CancelledError` → propagates (shutdown contract). Random `Exception` in `run_once` → swallowed, `ERROR` log emitted, loop continues to next tick (mirrors watchdog `:1717-1727`). |
| T6 | `tests/unit/services/test_long_tool_nudge_hand_off.py` (new) | `TestDeliverLongToolNudgeStub` | Calling `deliver_long_tool_nudge` with `handoff_stub_enabled=True` emits the `[LongToolNudge] STUB_FIRE` log with `EpisodeCtx` fields. With `handoff_stub_enabled=False` + `handoff_fn` injected, the injected fn is called exactly once with the same `EpisodeCtx`. **This is the phase 2 contract test** — phase 2 must NOT change the `EpisodeCtx` shape or the marker line. |
| T7 | `tests/unit/services/test_long_tool_nudge_observability.py` (new) | `TestPerCompletionLogLine` | Caplog capture at INFO; assert one log line per tool completion (no duplicates on clear, no leak on exception); assert the line carries the stable marker `[LongToolNudge] TOOL_COMPLETED` and all 6 documented fields; assert `threshold_crossed` is True for `duration > threshold` and False otherwise. **Stable-marker test is mandatory** — phase 2 / operator scripts depend on grep-ability. |
| T8 | `tests/integration/test_long_tool_nudge_graph_smoke.py` (new) | `TestGraphSmoke` | Build a graph via `build_instance_graph` with the wrapper; assert the `"tools"` node exists, registry is the singleton (`id(registry) == id(_LONG_TOOL_REGISTRY)`), and a happy-path turn (single tool_call) leaves the registry empty post-completion. Pin against any future refactor that allocates a per-graph registry (graph builds many graphs per process). |
| T9 | `tests/unit/services/test_long_tool_nudge_belt.py` (new) | `TestScannerStaleStampForceCloses` | Synthetic stamp age 7300 s (above `STALE_STAMP_TTL_SECONDS=7200`): end-of-`run_once` force-clears it, closes the corresponding `_active_episodes` tuple, re-arms the `_fired_episodes` entry, emits `[LongToolNudge] STALE_STAMP force-cleared ...` WARN log; a fresh `tool_call_id` on the same child fires normally on the next tick. **At-and-below-TTL (7199 s) does NOT trigger the belt** — only over-TTL. `TestScannerOrphanEpisodeClose` — orphan tuple in `_active_episodes` with no live stamp is discarded on next `run_once` (B4 hygiene sweep joins AD-37). `TestScannerFiredEpisodesHygieneDiscard` — orphan stamp-level `_fired_episodes` entry whose `(child_id, tool_call_id)` no longer appears in the registry snapshot is discarded (AM-5 hygiene). `TestTickStatsShape` — `run_once` returns the seven expected keys including `stale_stamps_force_cleared` and `orphan_episodes_discarded`. |
| T10 | `tests/unit/test_long_tool_nudge_config.py` (new; relocated from Phase 2 U14-U16) | `TestLongToolNudgeConfigDefaults` | Construct `LongToolCallNudgeConfig()` with no env; assert `enabled=True`, `interval_seconds=60`, `default_threshold_seconds=900`. `TestLongToolNudgeConfigEnvOverrides` — set `LONG_TOOL_NUDGE_ENABLED=false`, `LONG_TOOL_NUDGE_INTERVAL_SECONDS=30`, `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS=1200`; construct; assert all three fields reflect env. `TestLongToolNudgeConfigFailFastAtBoot` — set `LONG_TOOL_NUDGE_INTERVAL_SECONDS=0` (`ge=1` requires ≥1); assert `ValidationError`. Set `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS=2000` (`le=HARD_MAX=1800`); assert `ValidationError` (mirrors `tests/unit/test_config.py` watchdog-config style). |
| T11 | `tests/unit/services/test_long_tool_nudge_threshold.py` (extended; AD-38 read-side floor) | `TestResolveThresholdFloorBypass` | Mocked metadata of `30` (below `MIN_THRESHOLD_SECONDS=60`) falls back to the configured default; metadata of `60` is honored; metadata of `0`, `None`, `"60"` (string) all fall back. **This is the read-side floor case** (mirrors the tool-side `ValueError` in `set_instance_tunable`; AD-26/AD-38). |
| T12 | `tests/unit/services/test_long_tool_nudge_resolve.py` (new; AM-7 pin) | `TestFiredEpisodesOnlySetOnSuccessfulFire` | Construct scanner with stub seam (records seamed call success). Call `run_once` with a PAUSED parent; assert `_fired_episodes` does NOT contain the stamp (the stamp-level dedup only advances on successful fire — AM-7). A subsequent call with the parent RUNNING fires and records the stamp. Separate: `TestResolveThresholdPerTickMemoization` — two stamps on the same child in one snapshot resolve the threshold via **one** `get_metadata_value` call (counter-instrumented mock). |

All tests use the existing in-memory SQLite + `SQLModel.metadata.create_all(engine)` fixture pattern from `tests/unit/services/test_waiting_children_watchdog.py:79-89`. Mock the `instance_repository` for unit tests that don't need real DB; integration test uses a real `SQLModelInstanceRepository`.

## Constraints

- **No auto-kill/pause.** This phase detects and (in phase 2) nudges. The
  detector MUST NOT mutate instance state — no `pause_instance`, no
  terminate, no metadata writes (other than the existing
  `last_activity_at` flow driven by LangGraph, untouched here).
- **No change to the 7200s task cap or per-tool hard timeout.**
  Bounded `bash` timeout is a separate candidate — capture as an open
  question only.
- **No system-prompt changes. No loop-breaker changes.**
- **No DB schema changes in this phase.** The
  `metadata.long_tool_call_threshold_seconds` key piggybacks on the
  existing `instance_metadata` JSONB column (`models.py:64-67`). Phase
  3 may introduce a separate config-only mechanism if write volume
  warrants.
- **Heartbeat-independent.** Detector keys on in-flight stamp age
  ONLY. Regression pin (T3) is mandatory.
- **Watchdog-compatible shutdown.** Lifespan task responds to
  `CancelledError` cleanly; `app.state.long_tool_nudge_task` set to
  `None` on ctor failure (mirrors `daemon/api.py:735-741`).
- **Single hand-off seam.** Phase 2 may NOT add a parallel delivery
  path; it must replace the stub body of `deliver_long_tool_nudge`
  (`EpisodeCtx` field set pinned by T6).
- **Registry singleton.** `_LONG_TOOL_REGISTRY` is module-level so the
  lifespan-wired detector and the graph-wired wrapper share the same
  instance. Per-graph allocation is FORBIDDEN (T8 pins this).

## Open Decisions (Phase 1)

> Every entry below is a decision made-by-default in this plan or an
> unresolved question. Each carries a **Recommendation** and the
> alternatives considered. The synthesis worker copies these into
> `decisions.md` verbatim — do NOT rewrite.

- **D1 — Per-batch overestimation semantics (decision made by default).**
  LangGraph's `ToolNode._arun_batch` (`.venv/.../langgraph/prebuilt/tool_node.py:842-846`)
  dispatches every `tool_call` of one AI message via
  `asyncio.gather`, so a node-level wrapper sees only batch
  boundaries. All `tool_call_id`s in the batch get the SAME
  `started_at` at entry; each clears independently at exit, so a
  fast call sharing a batch with a slow one observes
  `duration ≈ batch_wall_clock` (overestimation).
  **Recommendation:** Ship v1 as overestimation. False positives are
  recoverable (parent decides per v1 non-goal); false negatives are
  not (the bd4b36ef class is exactly what we are buying). Pin the
  semantics in the module docstring + `TestWrappedToolsNodeStampsAndClears`
  (T4).
  - Alt A — Reimplement dispatch by wrapping each tool individually
    inside the wrapper: replace the `ToolNode` delegation with a
    manual `asyncio.gather` of per-id coroutines. **Rejected:** risk
    surface is large (must re-derive `Command` handling, error
    formatting, stream-writer semantics); likely to break the
    `_combine_tool_outputs` shape that downstream code
    (`create_post_tools_router`) depends on.
  - Alt B — Per-batch semantics (single stamp per entry, no per-id):
    fires when **any** call in a batch is slow. **Rejected:**
    collapses to one stamp per message — no way to attribute which
    `tool_call_id` is the offender; the loop-breaker-class evasion
    (bd4b36ef's five distinct bash signatures — actually five
    DIFFERENT AI messages here) is still caught, but a future case
    where one slow + one fast call share a batch would fire even
    though only one is slow. Overestimation preserves the
    per-id-attribution while remaining conservative.

- **D2 — Parent_id read at scan time, NOT stamp time (decision made by default).**
  Stamps carry `tool_call_id` + `tool_name` + `started_at`; the
  detector reads `instance.parent_id` from the repo at scan time
  when a stamp is about to fire.
  **Recommendation:** Read at scan time. Parent_id is a stable
  identifier on the child row (`models.py:61`) — caching it at
  stamp time saves a DB read but couples stamp lifetime to a
  denormalized field. Scan-time read keeps the registry small
  (one `_Stamp` per call) and the parent-link fresh.
  - Alt A — Stamp carries `parent_id`, read once at
    `record_start`. **Rejected:** adds DB read on the graph-node
    hot path (every tool entry); gains nothing — the field is
    stable for the stamp's lifetime.
  - Alt B — Stamp carries `parent_id`, read at
    `clear_for_instance` time. **Rejected:** duplicates the scan
    logic for the same value; no benefit over Alt A or default.

- **D3 — Episode key = `(child_id, tool_call_id)` (decision made by default).**
  Two long calls from the same child with different `tool_call_id`s
  are independent episodes; two ticks on the same
  `(child_id, tool_call_id)` are the same episode (dedup fires once
  per process for v1; phase 2 owns the durable dedup).
  **Recommendation:** Use `(child_id, tool_call_id)`. `tool_call_id`
  is a stable LangGraph-issued UUID — survives the batch case where
  the same id is recorded by both the entry stamp and any future
  re-entry; `tool_name` is redundant with the id.
  - Alt A — `(child_id, tool_name)` only. **Rejected:** bd4b36ef
    issued five calls to the SAME `bash` tool with different
    arguments — the key would collapse to one episode and the
    detector would fire only once for all five.
  - Alt B — `(child_id, tool_name, args_hash)`. **Rejected:**
    args may be sensitive (bd4b36ef included repo paths); hashing
    forces us to either persist or rebuild the args at scan time;
    `tool_call_id` already disambiguates without the privacy/size
    hazard.

- **D4 — Per-process in-memory `_fired_episodes` set for v1 (decision made by default).**
  The detector maintains `set[tuple[str, str]]` (instance_id,
  tool_call_id) of fired-this-process stamps; clear when the stamp
  is cleared from the registry. Daemon restart resets.
  **Recommendation:** Ship v1 with in-memory dedup. The bd4b36ef
  forensic scenario is a single-process, single-instance incident
  lasting 5h48m — process restart is not on the critical path.
  Documented limitation, mirrors watchdog `_notified`/`:537` and
  `_nudge_counts`/`:561`.
  - Alt A — Durable dedup via a new DB table. **Rejected for
    phase 1:** write volume per completed call (Task 6 emits one
    log line per completion already; a DB write would compound);
    forensics don't need durable dedup, just per-process
    observability. Phase 2/3 may revisit if cross-restart dedup
    is needed.

- **D5 — Hand-off seam as a stub gated by `handoff_stub_enabled=True` (decision made by default).**
  Phase 1 ships `deliver_long_tool_nudge` as an INFO-log stub;
  phase 2 flips `handoff_stub_enabled=False` (via constructor
  arg) and replaces the body with `enqueue_message` (or whatever
  the phase 2 delivery path turns out to be). The `EpisodeCtx`
  field set is pinned by `TestDeliverLongToolNudgeStub` (T6).
  **Recommendation:** Stub-then-replace. The detector is testable
  end-to-end in phase 1 (T3, T6); phase 2 only needs to fill in
  delivery, not retrofit detection.
  - Alt A — Defer the seam function entirely; phase 1 logs the
    fire event and phase 2 adds the call site inline at the
    detector. **Rejected:** the seam is the architectural
    contract — without it, phase 2 has two options (add inline
    call vs. add a parallel delivery path) and the contract
    surface is implicit.
  - Alt B — Implement the full `enqueue_message` call in phase 1.
    **Rejected:** couples phase 1 to the delivery path (which is
    the phase 2 non-goal exploration); if the delivery path
    changes (e.g., `_parent_attestation_attest` handshake), phase
    1 changes too. Stub decouples.

- **D6 — `config.long_tool_nudge.*` and the canonical home (decision made by default; landing phase moved to Phase 1 via B1).**
  Phase 1 owns the canonical constants (`HARD_MAX_THRESHOLD_SECONDS`,
  `MIN_THRESHOLD_SECONDS`, `STALE_STAMP_TTL_SECONDS`) AND the
  `LongToolCallNudgeConfig` nested class + `EnsembleConfig`
  wiring (Task 7a, B1 re-slice). Phase 1 reads
  `config.long_tool_nudge.enabled`, `.interval_seconds`,
  `.default_threshold_seconds` and parses the env at boot
  (`LONG_TOOL_NUDGE_*` env vars derived mechanically from the
  class's `env_prefix`). Out-of-range env values fail fast at boot
  via `ge=`/`le=` validators.
  - Alt A — Phase 1 hard-codes env names.
    **Rejected:** forces phase 1 to make a policy decision phase
    3 owns.
  - Alt B — Phase 1 reads defaults only and skips config wiring.
    **Rejected:** loses the `enabled` toggle, which is a v1
    hard requirement (operator must be able to disable the
    detector).

- **D7 — No DB persistence for duration observability in phase 1 (decision made by default, revisitable).**
  Phase 1 emits a structured INFO log line per tool completion
  (Task 6) with a stable grep-able marker. No DB write. Forensic
  reconstruction uses the log (operators `grep` `[LongToolNudge]
  TOOL_COMPLETED`).
  **Recommendation:** Log-only for v1. Forensics driver was "per-tool
  duration is NOT durably recorded anywhere" — a log line is
  durable recording at the level the driver requires. DB write
  costs on every tool completion (write amplification vs the
  bd4b36ef baseline of zero tool records) is not justified until
  the log volume is measured in production.
  - Alt A — Per-completion DB insert into a new
    `tool_duration_log` table. **Rejected for phase 1:** write
    volume + schema migration cost for an observability signal
    that log already provides. Re-evaluate in phase 3 if log
    volume is unmanageable.
  - Alt B — Aggregated per-instance row (one row per instance,
    refreshed every N completions). **Rejected for phase 1:**
    loses the per-call attribution the bd4b36ef forensics need
    (operator needs to see 5 distinct bash-call durations).

- **D8 — Default `interval_seconds=60` (decision made by default).**
  Watchdog uses 3600s (1h). Long-tool detector scans every 60s.
  **Recommendation:** 60s. The detector's job is to fire FAST once
  a call crosses threshold — a 1h scan means a 900s threshold
  might not fire until 1800s of wall-clock since entry. 60s keeps
  detection latency ≤ threshold + 60s.
  - Alt A — 300s (5min). **Rejected:** unnecessary delay for a
    detector whose sole purpose is fast notice; watchdog's 3600s
    is fine because the watchdog's threshold is 3600s by default.
  - Alt B — 10s. **Rejected:** registry snapshots are cheap
    (RAM-only dict copy) but the scan does a
    `get_metadata_value` per active stamp — 10s × N stamps is
    wasted DB load.

- **D9 — Overflow cap of 1024 tracked instances (decision made by default).**
  `LongToolNudgeRegistry` drops the OLDEST instance on overflow
  with a WARN log.
  **Recommendation:** 1024 is the watchdog order-of-magnitude
  active-instance cap. Drop-oldest is acceptable because the
  detector only cares about CURRENT in-flight stamps; an instance
  with no in-flight calls has no entries to drop.
  - Alt A — Unlimited. **Rejected:** long-lived daemon + stuck
    instances (bd4b36ef-class) could grow the dict without bound.
  - Alt B — 4096. **Accepted as a follow-up tunable** but 1024
    ships first; revisit if production drops too aggressively.

- **D10 — Boundary semantics: strict `>` (decision made by default, pinned by T3).**
  Detector fires when `now - started_at > threshold` (strictly
  greater). At exactly `== threshold`, no fire.
  **Recommendation:** `>`. Matches watchdog's `age > threshold`
  SQL pattern (`waiting_children_watchdog.py` hang SQL — see the
  docstring at `:7`: "child at ``threshold-1`` seconds is NOT
  hung, child at ``threshold+epsilon`` IS hung"). Consistency
  with the existing precedent.
  - Alt A — `>=` (fire at exact threshold). **Rejected:**
    divergent from the house precedent; off-by-one at the
    boundary is annoying for forensics ("why did the detector
    fire at exactly 900s for a 900s threshold?").
  - Alt B — Configurable. **Rejected:** no operational signal
    that `>=` is ever wanted; adds a config knob for no reason.

- **D11 — Open question (NOT a decision): bounded per-call `bash` timeout enforcement — DECIDED — STRICTLY DEFER (was Open, AD-34).**
  v1 is detection + advisory nudges only; a separate candidate is a
  bounded default `bash` timeout (currently `timeout=1800` per-call,
  `bash.py:204`, cap 1800, `asyncio.wait_for` enforcement at `:327`).
  The bd4b36ef forensics showed the LAST bash call ran ~30 min and
  never returned — a 30 min bounded timeout would have cancelled
  the call cleanly, but the **visibility** gap was the real harm:
  the parent had no signal. The long-tool-nudge path now covers
  visibility; a future per-call default-policy change is captured as
  a follow-up feature with the ticket embedded in
  `architecture-recommendation.md` §3.1 (AD-34 DECIDED — STRICTLY
  DEFER, AM-4).

- **D12 — Open question (NOT a decision): per-call (not per-batch) attribution.**
  The overestimation semantics (D1) make fast calls look slow
  when they share a batch with a slow one. A future refinement
  could reimplement dispatch to attribute true per-call duration.
  This is the Alt A of D1; rejected today, captured as a known
  improvement target in `decisions.md`.