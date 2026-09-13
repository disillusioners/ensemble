# Phase 2: Delivery Seam + Episode Dedup + Config Block + Daemon-Restart Semantics

## Objective

Implement the parent-nudge delivery seam (`deliver_long_tool_nudge(parent_id, child_id, episode_ctx)`) that Phase 1 calls on every threshold crossing, with per-(parent, child) episode deduplication, a nested `LongToolCallNudgeConfig` block, and the daemon-restart durability decisions documented. Phase 2 is the ONLY writer of the notice text, the episode dedup state, the config block, and the lifespan wiring of the loop; Phase 1 owns duration stamping + scanner; Phase 3 owns the parent-tunable threshold tool.

## Coupling

- **Depends on**: Phase 1 — `LongToolCallScanner` (delivers) calls `await deliver_long_tool_nudge(parent_id, child_id, episode_ctx)` from a single threshold-crossing seam.
- **Coupling type**: **tight** (shared seam signature, shared metadata key, shared `WORKER_POOL` injection, shared `enqueue_message` provenance)
- **Shared files with other phases**:
  - `daemon/services/long_tool_nudge.py` — **new module**, single canonical home for: stamp registry, scanner class, `_build_long_tool_notice`, `deliver_long_tool_nudge`, episode dedup state, `LONG_TOOL_NUDGE_SOURCE` constant, `_run_long_tool_nudge_loop`. Co-located because Phase 1 + Phase 2 + the Phase 3 threshold tool all touch the same module.
  - `daemon/config.py` — Phase 2 writes `LongToolCallNudgeConfig` (nested `BaseSettings`) and the `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=...)` wiring line on `EnsembleConfig`.
  - `daemon/api.py` — Phase 2 writes the lifespan wiring (ctor + named asyncio task; mirrors `waiting_children_watchdog` block at `:715-759`).
  - `daemon/manager.py` — Phase 3 introduces the parent-tunable threshold tool which uses the SAME metadata key `long_tool_call_threshold_seconds`. Phase 2 only READS this key (via `repo.get_metadata_value(parent_id, "long_tool_call_threshold_seconds")`); Phase 3 only WRITES it (via `repo.set_metadata(...)`). No file ownership overlap.
- **Shared APIs/interfaces** (consumed by Phase 1 + Phase 3):
  - `deliver_long_tool_nudge(parent_id: str, child_id: str, episode_ctx: LongToolNudgeEpisodeCtx) -> bool` — the single seam Phase 1 calls. Returns `True` if a nudge was enqueued this call, `False` otherwise (skipped/suppressed by dedup).
  - `LongToolNudgeEpisodeCtx` — TypedDict or dataclass with fields `child_id`, `parent_id`, `tool_name: str`, `tool_call_id: str`, `elapsed_seconds: float`, `threshold_seconds: int`, `episode_started_at: float`. See "Inter-phase contract" in Context below.
  - `repo.get_metadata_value(parent_id, "long_tool_call_threshold_seconds")` → optional int override (default 900, hard max 1800). Phase 3 calls `set_metadata(parent_id, ...)` on the same key.
  - `LONG_TOOL_NUDGE_SOURCE = "system:long-tool-nudge"` constant for `enqueue_message` provenance.
- **Why this coupling**: The delivery seam IS the v1 contract for the whole feature. Phase 1's scanner MUST hit it on every crossing; Phase 3's threshold tool MUST write the metadata key Phase 2 reads; the lifespan wiring MUST start before any child instance can spawn. Putting the seam + scanner + dedup in one module lets Phase 1 import the seam by name without a circular import and lets the synthesis worker review the dedup logic next to the notice text.

## Context

### Verified code-map pins (re-read only if your task mutates that file)

| Pin | Location | Verified |
|---|---|---|
| Delivery primitive (A5 pattern, `enqueue_message` + direct `notify_work()` + try/except + `inspect.iscoroutine` shim) | `daemon/services/waiting_children_watchdog.py:1559-1620` | yes (verified 2026-09-13 @ `0acd3afa`) |
| `notify_work()` is **sync** in prod (`WorkerPool.notify_work() -> None`), `AsyncMock` returns coroutine in some test fixtures — the `inspect.iscoroutine(_notify_result)` shim is REQUIRED | `daemon/services/worker_pool.py:1300-1306`; usage at `daemon/services/waiting_children_watchdog.py:1597-1606` | yes |
| `enqueue_message` service signature: `(instance_id, message, source="api", priority=1, images=None, metadata=None, *, is_deferred, is_background, work_id, work_id_required)`; **no JobItem** — MessageQueue + Task in one txn (`:1981-1991`); `worker_pool.notify_work()` called internally at `:1987` (the WHY behind the wedge double-notify: incident 33252 2026-09-11, comment block `:1572-1619`) | `daemon/services/instance_messaging.py:1967-1991` | yes |
| Manager facade `enqueue_message` already accepts + forwards `instance_id, message, source, priority, images, metadata` (positional + default) — Facade-Forwarding Discipline is N/A for THIS feature (no new kwarg) — but plan MUST document the grep and assert no facade change required | `daemon/manager.py:6777-6873` | yes |
| Status routing on enqueue: IDLE / WAITING_CHILDREN → RUNNING; terminal (COMPLETED/TERMINATED/ERROR/FAILED) → revive to RUNNING; PAUSED is **excluded** (cooperative pause gate; `:1836-1840`); claim gate is the defense-in-depth backstop (`:1813-1821` INFO log + SQL gate) | `daemon/services/instance_messaging.py:1810-1854` | yes |
| ⚠️ Priority nuance: terminal-revive resets leader-attestation counters ONLY when `priority == 1 AND msg_type == HUMAN.value` (`:1896-1902`). System nudges MUST use `priority=0` so a nudge never resets an in-progress attestation episode. | `daemon/services/instance_messaging.py:1896-1902` | yes |
| Watchdog episode-dedup state shape: `self._notified: set[tuple[str, str]]` (`:537`); re-derived from SQL each tick (`:949-958`); 3-purge close mechanism (docstring `:128-147`); per-(parent, child) `_nudge_counts: dict[tuple[str,str], int]` (`:561`) — counter survives the lifetime of one episode; reset on daemon restart (documented limitation). | `daemon/services/waiting_children_watchdog.py:537, 561, 949-958`; docstring `:128-147` | yes |
| Watchdog loop skeleton: `async def run_waiting_children_watchdog_loop(watchdog, *, interval_seconds)`, `while True` → `run_once` → `CancelledError raise` / `Exception log` → `asyncio.sleep(interval)` (sleep swallows CancelledError) | `daemon/services/waiting_children_watchdog.py:1668-1732` | yes |
| Lifespan wiring pattern: ctor inside `try: ... except Exception as boot_exc: app.state.<watch>_task = None; logger.error(...)` (preserves app boot on ctor failure); else `asyncio.create_task(run_<watch>_loop(...), name="<watch-name>")` only when `enabled=True`; disabled → `app.state.<watch>_task = None; logger.info("...disabled by config")` | `daemon/api.py:715-759` | yes |
| Sync repo helpers usable from async loop (mirror watchdog's pattern: SQLModelSession blocks the asyncio loop briefly; acceptable for ≤60s cadence scans): `get_metadata_value` (`:1931`), `set_metadata` (`:2099`) — both plain `def` not `async def` | `daemon/repositories/instance/repository.py:1931-1983, 2099-2160` | yes |
| Config — two house styles: (1) flat `ServicesConfig` fields, `SERVICES_*` env prefix, `config.py:1271-1312`; (2) nested `BaseSettings` like `LoopBreakerConfig` (`:1737-1756`) with `env_prefix="LOOP_BREAKER_"` and wired via `loop_breaker: LoopBreakerConfig = Field(default_factory=LoopBreakerConfig)` at `:2109`. **WORKING DECISION (see Open Decisions)**: nested `LongToolCallNudgeConfig`, env prefix `LONG_TOOL_NUDGE_`. | `daemon/config.py:1271-1312, 1737-1756, 2109` | yes |
| WorkerPool is wired on `manager._worker_pool` (set in `setup_worker_pool` at `:6519`); read access via `getattr(self._manager, "_worker_pool", None)` is the house pattern | `daemon/manager.py:692, 1113, 6253-6283, 6519, 7755-6, 10704-10752` | yes |
| Watchdog constants for naming: `WATCHDOG_SOURCE = "system:watchdog"` (`:171`), `WEDGE_SOURCE = "system:watchdog:wedge"` (`:177`) — colon-namespaced provenance pattern. **WORKING NAME**: `LONG_TOOL_NUDGE_SOURCE = "system:long-tool-nudge"` | `daemon/services/waiting_children_watchdog.py:171, 177` | yes |

### Inter-phase contract (this phase CONSUMES from Phase 1; Phase 3 also READS)

```python
# In daemon/services/long_tool_nudge.py — defined by Phase 1, USED by Phase 2

from typing import TypedDict

class LongToolNudgeEpisodeCtx(TypedDict):
    """Context Phase 1 hands Phase 2 when a threshold crossing fires."""
    child_id: str            # the (busy) child whose tool exceeded threshold
    parent_id: str           # the parent who must be told
    tool_name: str           # e.g. "bash", "subtree_messages" — exact tool name from the AIMessage.tool_calls entry
    tool_call_id: str        # exact tool_call_id so the parent can correlate
    elapsed_seconds: float   # monotonic duration of the in-flight tool call at crossing
    threshold_seconds: int   # the effective threshold used (min(metadata_or_default, 1800))
    episode_started_at: float  # monotonic timestamp when the long call started — for "elapsed since start" in the notice
```

**The seam Phase 2 implements** (signature Phase 1 will call):

```python
async def deliver_long_tool_nudge(
    parent_id: str,
    child_id: str,
    episode_ctx: LongToolNudgeEpisodeCtx,
) -> bool:
    """Deliver a one-nudge-per-episode advisory to the parent about a long-running child tool.

    Returns True if a nudge was enqueued this call; False if suppressed by:
      - PAUSED parent (skip + WARN — never raise into the scanner tick)
      - episode dedup (already-notified for this (parent, child) episode)
      - threshold override not configured / config disabled
    """
```

### House patterns referenced

- **ATTESTATION_NUDGE_TEXT single-source discipline** — every nudge text in the daemon lives in exactly ONE canonical home (e.g. `ATTESTATION_NUDGE_TEXT` in `daemon/graph.py:2834`). Phase 2 follows: `_build_long_tool_notice()` in `daemon/services/long_tool_nudge.py` is the only writer of the long-tool-nudge body. Tests assert the constant location; no other module constructs the body.
- **A5 wedge-notify pattern** (`waiting_children_watchdog.py:1572-1619`) — the double-notify (enqueue + direct `worker_pool.notify_work()`) defends against the `incident 33252` notify-loss class. Phase 2 mirrors it verbatim because the failure mode is the same: an enqueue-commit / pool-notify race that strands the Task PENDING. The watchdog comment block (`:1576-1581`) names the exact failure mode ("lost wake between the enqueue commit and the pool's notify, or a defer-gate race that parked the notice behind a busy witness") — the long-tool-nudge path can hit the same.
- **Pause-gate discipline** — watchdog skip pattern at `:996-1009`: read `parent.status` before any heavy work; on PAUSED, increment `parents_skipped_paused` stat, log INFO, return; never raise into the scan loop. Phase 2 mirrors with a single `WARN` log (no stat — out of scope for v1; see Open Decisions).

### Daemon-restart durability decisions (documented in Tasks, justified in Open Decisions)

| State | Storage | Worst case on restart | Accept? |
|---|---|---|---|
| Stamp registry (which child has an in-flight long call) | RAM-only (`_in_flight: dict[child_id, LongToolCallStamp]`) | In-flight calls are interrupted/lost on restart anyway (worker tasks are cancelled); on restart the scanner re-detects fresh stamps. | **Yes** — the scanner rebuilds from observed `tool_start`/`tool_end` events. No durability needed. |
| Episode dedup state (which (parent, child) pairs are mid-episode) | RAM-only (`_active_episodes: set[tuple[parent_id, child_id]]`) | ≤1 duplicate nudge post-restart if a child is still mid-tool when the daemon restarts. Parent sees the same nudge twice across the restart boundary. | **Yes** — duplicate is benign (parent ignores stale nudges or reads them as the same event); alternative (SQL-persisted episode set) adds DB writes on every crossing for marginal benefit. |
| Nudge delivery durability | `enqueue_message` writes `MessageQueue` + `Task` rows in one txn (`instance_messaging.py:1981-1987`) | Notification is durable the instant `enqueue_message` returns. Restart mid-enqueue → txn rolls back, no half row. | **Yes** — built-in. No `report_injections`-style obligation table needed. |

### Out of scope (v1) — explicit non-goals

- No escalation semantics (no "second nudge" / "third nudge" / operator page). Episode = exactly 1 nudge.
- No auto-kill, no auto-pause, no auto-terminate. Parent decides.
- No changes to the 7200s task cap.
- No system-prompt changes (parent learns about the nudge via the normal message stream; no special handling).
- No new kwarg on `enqueue_message` facade (no Facade-Forwarding Discipline work needed).
- No FE changes (FE already renders system-source messages; long-tool-nudge rides the existing path).
- No migration / schema change (stamp registry is RAM, threshold override is in the existing `instance_metadata` JSON column).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/services/long_tool_nudge.py` with constants + helpers | Module docstring (purpose + episode dedup contract + restart semantics, mirroring watchdog's `:1-150` docstring density); `LONG_TOOL_NUDGE_SOURCE = "system:long-tool-nudge"` constant; module-level `HARD_MAX_THRESHOLD_SECONDS = 1800` (intentionally NOT env-tunable, per the working decision — see Open Decisions); `_format_age_human(seconds: float) -> str` helper copied from watchdog's `:180` for one-line formatting (do not import — keep the modules independent so watchdog can be refactored without touching nudge). | `daemon/services/long_tool_nudge.py` (new) |
| 2 | Implement `LongToolNudgeScanner` class | Constructor takes `(instance_repository, manager, *, enabled=True, interval_seconds=60, default_threshold_seconds=900)` + optional `task_repository=None` (mirrors watchdog's ctor at `:481-491` for consistency, even though the long-tool path doesn't use it); fields: `_in_flight: dict[str, LongToolCallStamp]` (RAM stamp registry, child_id → stamp), `_active_episodes: set[tuple[str, str]]` (RAM episode dedup, `(parent_id, child_id)`), `_nudge_counts: dict[tuple[str, str], int]` (per-episode counter, kept for future escalation hooks but never used to fire v1). Methods: `async def run_once(self) -> dict[str, int]` — returns `{"scanned_children": N, "nudges_enqueued": M, "suppressed_episode": K, "skipped_paused_parent": P, "errors": E}`; `def start_stamp(child_id, parent_id, tool_name, tool_call_id, started_at, threshold_seconds)`, `def clear_stamp(child_id, tool_call_id)` (Phase 1 calls these from the duration-tracker hook — see Phase 1 plan). NO DB writes in the scanner itself (Phase 2 is pure delivery). | `daemon/services/long_tool_nudge.py` (new) |
| 3 | Implement `deliver_long_tool_nudge(parent_id, child_id, episode_ctx)` | Private helper `_resolve_threshold(parent_id)` that calls `self._repo.get_metadata_value(parent_id, "long_tool_call_threshold_seconds")` → `int | None`, then `effective = min(override or self._default_threshold, HARD_MAX_THRESHOLD_SECONDS)` (the `min(..., 1800)` is the SECOND line of defense after the `le=1800` Field validator — operator-facing env that bypasses pydantic still gets clamped). Then: (a) parent status check via `self._repo.get(parent_id)`; if PAUSED → `logger.warning(...)` + return False (defense-in-depth: the claim gate would defer the Task to resume, but the notice text would be stale by then — pre-check + skip is the cleaner contract, mirrors watchdog `:996-1009`); (b) episode dedup check: if `(parent_id, child_id) in self._active_episodes` → return False (no log — high volume if scanner fires on consecutive ticks); (c) build notice via `_build_long_tool_notice(parent_id, episode_ctx, effective_threshold)`; (d) `await self._manager.enqueue_message(instance_id=parent_id, message=notice, source=LONG_TOOL_NUDGE_SOURCE, priority=0, metadata={"long_tool_nudge": True, "long_tool_nudge_tool": tool_name, "long_tool_nudge_tool_call_id": tool_call_id, "long_tool_nudge_elapsed_seconds": elapsed_seconds, "long_tool_nudge_threshold_seconds": effective_threshold})`; (e) A5 direct-notify pattern (mirrors `:1592-1619`): `worker_pool = getattr(self._manager, "_worker_pool", None)`; `if worker_pool is not None: try: _r = worker_pool.notify_work(); import inspect; if inspect.iscoroutine(_r): await _r; except Exception as e: logger.warning("[LongToolNudge] direct notify_work raised {e!r} for parent {parent_id[:8]}... — relying on enqueue_message's internal notify")`; (f) `_active_episodes.add((parent_id, child_id))` + `_nudge_counts[(parent_id, child_id)] = 1`; (g) return True. **CRITICAL**: NO `is_deferred` / `is_background` / `work_id` / `work_id_required` kwargs — system nudges are foreground, no JobItem linkage. | `daemon/services/long_tool_nudge.py` (new) |
| 4 | Implement `_build_long_tool_notice()` — SINGLE canonical home | Function `_build_long_tool_notice(parent_id: str, episode_ctx: LongToolNudgeEpisodeCtx, effective_threshold: int) -> str`. **Structure** (locked by task spec — every section below is REQUIRED): (a) Header: `[system:long-tool-nudge] Long-tool advisory for child <short_id> (<child_name_or_id>) — tool '<tool_name>' (call <tool_call_id>) has been in-flight <elapsed_human> (threshold <threshold_human>).` (b) Why-it-matters paragraph: `This is the busy-slow / weak-model signature — long tool time, zero LLM errors so far. The loop breaker (3-arg cycle detector) cannot catch varying-args calls, so you (the parent) are the only one who can decide.` (c) Recommendations (existing parent tools only — explicitly NO pause/resume advice because agents have NO pause tools; pause is operator-only): 1. `subtree_messages` (or `get_instance_messages` / `get_instance_info`) — read the child's recent turns to confirm the tool is wedged vs. just slow; 2. `send_message` to the child — lands at next turn boundary (mid-tool child CANNOT receive — explicit warning text mirroring the bd4b36ef 19-min-undeliverable lesson); 3. `terminate_instance` + re-spawn replacement (drop-in if the child has been stuck for more than 2× threshold). (d) **EXTENSIBILITY SEAM (HARD REQUIREMENT)**: end the notice with a clearly-marked block `# ── FUTURE: re-spawn-with-higher-intelligence-model recommendation ── # FUTURE (Feature #1 companion): when smart-model spawning ships, append a recommendation line here pointing to the new spawn-with-intelligence tool. Template: '4. Consider re-spawning with a higher-intelligence model: {spawn_tool_name}(child_role, model=<better_model>).' Do NOT implement today; the comment + template section is the contract for Phase 5+ work. Marked with the `# FUTURE` prefix so a one-grep audit finds it. (e) Footer: `This is advisory only — no automatic action has been taken. Episode id: {child_id[:8]}:{tool_call_id[:8]}.` Keep total length ≤ 1.5× the watchdog hang-notice (`_build_wedge_notice` at `:388-426`, ~14 lines) so the parent's context budget isn't blown. Tests pin the structure (5 mandatory sections, ≤ 1.5× wedge-notice length, no `pause`/`resume` advice as agent instructions). | `daemon/services/long_tool_nudge.py` (new) |
| 5 | Implement episode-close semantics | Add `close_episode(parent_id: str, child_id: str) -> None` method on `LongToolNudgeScanner` (Phase 1's stamp-clear path calls this). Close = `self._active_episodes.discard((parent_id, child_id))` + `_nudge_counts.pop((parent_id, child_id), None)`. **Definition of "episode close"** (v1, simple — see Open Decisions for the alternative): episode closes when Phase 1's duration-tracker records a `tool_end` event for the long call (i.e., the child either completed the tool or moved on). The next crossing on a NEW tool_call for the same (parent, child) is a FRESH episode and re-nudges. Documented in module docstring; tests pin the behavior. **Mirror watchdog's 3-purge close mechanism for the WAITING_CHILDREN case is NOT applicable here** (the long-tool path is not coupled to WC) — open `LongToolNudgeScanner` does NOT re-derive from SQL each tick; it relies on Phase 1's stamp-clear callback. **Justification**: the watchdog re-derives because a child can reach terminal status silently (e.g., crash); the long-tool path has an explicit `tool_end` event Phase 1 already records, so the close signal is reliable. Acceptable trade-off documented in Open Decisions. | `daemon/services/long_tool_nudge.py` (new) |
| 6 | Implement `_run_long_tool_nudge_loop` (loop skeleton) | Mirrors `run_waiting_children_watchdog_loop` (`waiting_children_watchdog.py:1668-1732`) verbatim: `while True: try: stats = await scanner.run_once(); if stats["nudges_enqueued"] > 0 or stats["errors"] > 0: logger.info(f"[LongToolNudge] tick stats: {stats}"); except asyncio.CancelledError: raise; except Exception as exc: logger.error(f"[LongToolNudge] cycle failed: {exc}", exc_info=True); try: await asyncio.sleep(interval_seconds); except asyncio.CancelledError: return`. If `not scanner.enabled`, log INFO + return immediately. Add `run_long_tool_nudge_loop` to `__all__`. | `daemon/services/long_tool_nudge.py` (new) |
| 7 | Wire `LongToolCallNudgeConfig` into `daemon/config.py` | Add nested config class **between** `ReportIntegrityConfig` (`:1759`) and `LanguageConfig` (so it sits with the other infra-loop configs): `class LongToolCallNudgeConfig(BaseSettings): model_config = SettingsConfigDict(env_prefix="LONG_TOOL_NUDGE_"); enabled: bool = Field(default=True, description="Enable the long-tool-call parent-nudge advisory service. When False, the daemon does not start the loop task in the lifespan (zero overhead). Default True. Override via LONG_TOOL_NUDGE_ENABLED env var (true / false)."); interval_seconds: int = Field(default=60, ge=1, description="How often the scanner checks in-flight tool stamps (seconds). Default 60 = once per minute. Lower = more responsive but more DB scans. Must be >= 1; out-of-range values FAIL FAST AT BOOT. Override via LONG_TOOL_NUDGE_INTERVAL_SECONDS."); default_threshold_seconds: int = Field(default=900, ge=1, le=HARD_MAX_THRESHOLD_SECONDS, description="Default per-child long-tool-call threshold (seconds). When a child's single tool call exceeds this (strictly greater), a one-nudge advisory is delivered to the parent. Default 900 = 15 min. Hard max 1800 (30 min) — values above FAIL FAST AT BOOT via le validator. Per-instance override via parent setting the instance_metadata.long_tool_call_threshold_seconds key (set by Phase 3's parent-tunable tool). Override via LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS env var.")`. Add wiring line on `EnsembleConfig` (immediately after `:2109`, before `report_repair`): `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=LongToolCallNudgeConfig)`. Add `LongToolCallNudgeConfig` to the module's `__all__` if one exists; add to the lazy imports section if `daemon/config.py` uses lazy loading for nested configs (check `:2095-2117`). | `daemon/config.py:1737-1756, 2095-2117` |
| 8 | Wire the loop in the lifespan (`daemon/api.py`) | Add a new block **after** the waiting-children-watchdog block at `:715-759`, in the same `try/except` shape. Import at the top of the block: `from daemon.services.long_tool_nudge import LongToolNudgeScanner, run_long_tool_nudge_loop`. Try block: `long_tool_nudge_scanner = LongToolNudgeScanner(instance_repository=scanner_repo, manager=manager, enabled=config.long_tool_nudge.enabled, interval_seconds=config.long_tool_nudge.interval_seconds, default_threshold_seconds=config.long_tool_nudge.default_threshold_seconds)`. Reuse the existing `watchdog_instance_repo = SQLModelInstanceRepository(engine=manager.engine)` from `:720` (don't create a second repo — the metadata getter is a tiny SELECT, no contention concern; alternatively pass `watchdog_instance_repo` directly). Except: `app.state.long_tool_nudge_task = None; logger.error("Long-tool-nudge scanner DISABLED — construction failed: {e}", exc_info=True)`. Else: if `long_tool_nudge_scanner.enabled`: `long_tool_nudge_task = asyncio.create_task(run_long_tool_nudge_loop(long_tool_nudge_scanner, interval_seconds=long_tool_nudge_scanner.interval_seconds), name="long-tool-nudge"); app.state.long_tool_nudge_task = long_tool_nudge_task; logger.info("Long-tool-nudge scanner started: interval={N}s, default_threshold={T}s".format(N=long_tool_nudge_scanner.interval_seconds, T=long_tool_nudge_scanner.default_threshold_seconds))`. Else: `app.state.long_tool_nudge_task = None; logger.info("Long-tool-nudge scanner disabled by config")`. **CRITICAL**: place the new block AFTER `setup_worker_pool()` is called (it's called higher in the lifespan, before `:715`); the scanner accesses `manager._worker_pool` via getattr, so an unset pool is logged-INFO'd-and-skipped, not crashed (mirrors watchdog `:1614-1619`). | `daemon/api.py:715-759` (insert new block after) |
| 9 | Document daemon-restart semantics in module docstring | Add a "Restart semantics" subsection to the `long_tool_nudge.py` module docstring that explicitly states: (a) stamp registry is RAM-only; in-flight calls are lost on restart by design (worker task cancellation); scanner rebuilds from Phase 1's next `tool_start` event. (b) `_active_episodes` is RAM-only; worst case = ≤ 1 duplicate nudge post-restart if a child is still mid-tool when the daemon restarts. Acceptable trade-off; parent dedup by reading the latest nudge as the current state. (c) Nudge delivery durability = the `enqueue_message` txn (MessageQueue + Task rows, commit atomic with status flip); no `report_injections`-style obligation table needed because the MessageQueue row IS the durable record. Cross-reference `enqueue_message` at `daemon/services/instance_messaging.py:1981-1991`. | `daemon/services/long_tool_nudge.py` (module docstring) |
| 10 | House-style cleanup: `__all__` + module-level `logger` + type hints | At the bottom of `long_tool_nudge.py`: `__all__ = ["LONG_TOOL_NUDGE_SOURCE", "HARD_MAX_THRESHOLD_SECONDS", "LongToolNudgeScanner", "deliver_long_tool_nudge", "run_long_tool_nudge_loop", "LongToolNudgeEpisodeCtx"]`. Module-level `logger = logging.getLogger(__name__)`. All public functions have type hints + a one-line docstring (mirrors watchdog's style at `:180-189` for `_format_age_human`). | `daemon/services/long_tool_nudge.py` |

## Tests

### Unit tests (mirror existing watchdog test patterns; see `tests/unit/` for `test_waiting_children_watchdog.py` location)

| # | Test name | What it pins | Key files |
|---|-----------|--------------|-----------|
| U1 | `test_long_tool_nudge_deliver_happy_path` | Mock manager; call `deliver_long_tool_nudge(parent, child, ctx)`; assert `manager.enqueue_message` called ONCE with `source=LONG_TOOL_NUDGE_SOURCE`, `priority=0`, `metadata={"long_tool_nudge": True, ...}` (assert each key); assert `_active_episodes` contains `(parent, child)`; assert `_nudge_counts[(parent, child)] == 1`; assert return value `True`. | `tests/unit/services/test_long_tool_nudge.py` (new) |
| U2 | `test_long_tool_nudge_dedup_consecutive_long_calls` | Call `deliver_long_tool_nudge` twice for the same `(parent, child, tool_call_id)` (no `close_episode` between). Assert `enqueue_message` called ONCE total; second call returns `False`. Mirror bd4b36ef scenario (5 consecutive long bash calls → 1 nudge). | same |
| U3 | `test_long_tool_nudge_rearm_after_close` | Call `deliver_long_tool_nudge`; call `close_episode(parent, child)`; call `deliver_long_tool_nudge` again with a NEW `tool_call_id`. Assert `enqueue_message` called TWICE total; second nudge includes the new `tool_call_id` in `metadata`. | same |
| U4 | `test_long_tool_nudge_per_parent_child_independence` | Two different parents both have a long-running child. Call `deliver_long_tool_nudge` for both. Assert `enqueue_message` called TWICE (one per parent). Then a SECOND long call for parent-A's child fires while parent-B's episode is still active. Assert only parent-A's second call is suppressed (parent-B's set is unaffected). | same |
| U5 | `test_long_tool_nudge_paused_parent_skipped` | Mock repo to return parent with `status=PAUSED`. Call `deliver_long_tool_nudge`. Assert `enqueue_message` called ZERO times; assert `logger.warning` called with PAUSED context; assert return value `False`. Critically: no exception raised into the scanner tick. | same |
| U6 | `test_long_tool_nudge_threshold_override` | Mock repo to return `parent.metadata.long_tool_call_threshold_seconds = 1500` for parent. Call `deliver_long_tool_nudge` with `episode_ctx["threshold_seconds"] = 900`. Assert effective threshold in the delivered `metadata["long_tool_nudge_threshold_seconds"]` is `1500`. Then test override of 2000 → clamped to 1800 (`min(override, HARD_MAX)`). | same |
| U7 | `test_long_tool_nudge_priority_zero_no_attestation_reset` | Wire a real (or acceptance-grade fake) `InstanceMessagingService.enqueue_message` such that a paused-instance revival path runs; assert the `priority=0` notice does NOT trigger the `attestation_denied_count` reset branch (mirrors `instance_messaging.py:1896-1902`). This is the regression-pin for the "system nudge resets leader attestation counter" failure mode. | same |
| U8 | `test_long_tool_nudge_notify_work_called_after_enqueue` | Mock `manager._worker_pool` with a sync `Mock(notify_work=Mock())`; call `deliver_long_tool_nudge`. Assert `enqueue_message` called first, `notify_work` called second (A5 pattern). Then swap mock for `AsyncMock(notify_work=AsyncMock())`; assert the coroutine is awaited (no `RuntimeWarning: coroutine never awaited`). | same |
| U9 | `test_long_tool_nudge_notify_work_pool_missing_skipped` | Mock manager with `_worker_pool = None`. Call `deliver_long_tool_nudge`. Assert `enqueue_message` called once, no exception raised, `logger.debug` fired with "worker_pool not wired" message (mirrors watchdog `:1614-1619`). | same |
| U10 | `test_long_tool_nudge_notify_work_exception_swallowed` | Mock `manager._worker_pool.notify_work` to raise `RuntimeError("boom")`. Call `deliver_long_tool_nudge`. Assert `enqueue_message` called once, exception caught, `logger.warning` fired with the error, return value still `True`. The nudge is durable even if the direct notify fails (the enqueue commit is the source of truth). | same |
| U11 | `test_long_tool_nudge_notice_structure` | Call `_build_long_tool_notice(...)` with a fixture `episode_ctx`. Assert the returned string contains all 5 mandatory sections (header / why-it-matters / 3 recommendations / FUTURE block / footer); assert NO occurrence of `pause_instance` or `resume_instance` as agent-instruction verbs; assert total length ≤ 1.5× `len(_build_wedge_notice(parent_id))`; assert `# FUTURE` prefix exists at the extensibility seam; assert `<elapsed_human>` and `<threshold_human>` come from `_format_age_human`. | same |
| U12 | `test_long_tool_nudge_loop_returns_immediately_when_disabled` | Construct `LongToolNudgeScanner(enabled=False, ...)`. Call `run_long_tool_nudge_loop(scanner, interval_seconds=60)`. Assert returns within 100ms without running any tick (mirrors watchdog `:1698-1702`). | same |
| U13 | `test_long_tool_nudge_loop_cancellation_propagates` | Construct enabled scanner. Run `run_long_tool_nudge_loop` in a task; cancel the task after 100ms; assert `CancelledError` raised (mirrors watchdog `:1717-1719`). | same |
| U14 | `test_long_tool_nudge_config_defaults` | Construct `LongToolCallNudgeConfig()` with no env. Assert `enabled=True`, `interval_seconds=60`, `default_threshold_seconds=900`. | `tests/unit/test_long_tool_nudge_config.py` (new) |
| U15 | `test_long_tool_nudge_config_env_overrides` | Set `LONG_TOOL_NUDGE_ENABLED=false`, `LONG_TOOL_NUDGE_INTERVAL_SECONDS=30`, `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS=1200`. Construct config. Assert all three fields reflect env. | same |
| U16 | `test_long_tool_nudge_config_fail_fast_at_boot` | Set `LONG_TOOL_NUDGE_INTERVAL_SECONDS=0` (must be `>= 1`). Assert `ValidationError` raised at instantiation. Set `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS=2000` (must be `<= 1800`). Assert `ValidationError` raised. (Mirrors watchdog config test style at `tests/unit/test_config.py`.) | same |
| U17 | `test_long_tool_nudge_episode_close_drops_count` | Add `(parent, child)` to `_active_episodes` and `_nudge_counts` directly. Call `close_episode`. Assert both structures drop the tuple. Call `close_episode` again — assert idempotent (no exception, no-op). | `tests/unit/services/test_long_tool_nudge.py` |

### Integration tests (extend existing harness; one file, focused)

| # | Test name | What it pins | Key files |
|---|-----------|--------------|-----------|
| I1 | `test_long_tool_nudge_e2e_child_long_tool_nudges_parent` | Full daemon harness (extend `tests/integration/test_attestation_in_graph_nudge_flow.py` pattern — that test exercises the in-graph nudge path; the long-tool path uses the same `enqueue_message` seam). Setup: spawn a parent in `WAITING_CHILDREN` + spawn a child who is mid a 2s `bash sleep 30` tool call. Mock the scanner's `_in_flight` to contain the child with `elapsed_seconds=905`, `threshold_seconds=900`. Wait one scanner tick (`asyncio.sleep(interval_seconds + 1)`). Assert: (a) parent receives one message with `source="system:long-tool-nudge"`; (b) parent transitions `WAITING_CHILDREN → RUNNING`; (c) parent's nudge text contains all 5 mandatory sections (U11's assertions hold); (d) no SECOND nudge fires for the next 3 ticks (dedup works). | `tests/integration/test_long_tool_nudge_e2e.py` (new) |
| I2 | `test_long_tool_nudge_e2e_parent_in_waiting_children_revives_to_running` | Same harness as I1, but parent starts in `WAITING_CHILDREN` (not RUNNING) — exercises the WC→RUNNING flip path. Assert parent's status flips after the nudge lands (proves the wake is real, not just the enqueue committed). | same |
| I3 | `test_long_tool_nudge_e2e_daemon_restart_dedup_reset` | Run I1's setup. Restart the daemon (test fixture simulates restart by tearing down + re-creating the scanner instance, but keeping the DB). Assert: (a) child is still mid-tool; (b) next tick after "restart" re-nudges (the `_active_episodes` set was reset); (c) duplicate is acceptable behavior per Restart Semantics docstring. Pins the v1 trade-off so a future reviewer can verify the documented limit. | same |
| I4 | `test_long_tool_nudge_e2e_paused_parent_no_nudge` | Setup: parent in PAUSED state; child mid long-tool. Wait one scanner tick. Assert no nudge enqueued; assert `logger.warning` line in captured logs contains "PAUSED" or the parent's short id. | same |
| I5 | `test_long_tool_nudge_e2e_priority_zero_does_not_reset_attestation` | Setup: parent in WAITING_CHILDREN with `attestation_denied_count=2, completion_gate_escalated=True` (forge via direct DB write). Trigger a long-tool nudge. Assert: parent is revived (WAITING_CHILDREN → RUNNING); both attestation counters are UNCHANGED (priority=0 path skips the `priority == 1 AND msg_type == HUMAN` branch at `:1896-1902`). | same |

### Test discovery + harness notes

- `tests/integration/` already has 100+ integration tests; the e2e harness (conftest, daemon start/stop, DB fixture) lives at `tests/integration/conftest.py` (verified file exists at `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/tests/integration/conftest.py`). New file `tests/integration/test_long_tool_nudge_e2e.py` imports from conftest — no harness duplication.
- `tests/unit/services/` is the house location for service-class unit tests; watchdog's unit tests live in `tests/unit/test_waiting_children_watchdog.py` (root-level, not under `services/`) — match that pattern for findability: `tests/unit/test_long_tool_nudge.py`. Config test mirrors `tests/unit/test_config.py` location pattern.

## Open Decisions (Phase 2)

Each item below: **Recommendation** (what I did by default + why) + **Alternatives** + **Why I chose it**. Synthesis worker should record the decision in `decisions.md`; reviewers should dispute here first.

### D2-1: Nested `LongToolCallNudgeConfig(BaseSettings)` vs flat `ServicesConfig` fields

- **Recommendation**: Nested `LongToolCallNudgeConfig(BaseSettings)` with `env_prefix="LONG_TOOL_NUDGE_"`, wired once via `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=...)` on `EnsembleConfig` (Task #7).
- **Alternatives**: (a) Flat `ServicesConfig` fields with `SERVICES_*` env prefix — matches `waiting_children_watchdog_*` precedent at `config.py:1271-1312`. (b) No config block — hard-code 60s / 900s / 1800s. (c) Per-instance only — no global defaults.
- **Why I chose it**: Two reasons outweigh the precedent argument. (1) The feature ships 3 independent knobs (`enabled`, `interval_seconds`, `default_threshold_seconds`) — flat `ServicesConfig` would clutter the already-3000-line `ServicesConfig`; nested groups them under a single import path so the wire-once field is atomic. (2) LoopBreaker already pioneered the nested `BaseSettings` pattern (`config.py:1737-1756`, wired at `:2109`) for the same reason (multiple infra-loop knobs); consistency with the closer precedent beats consistency with the older watchdog precedent. (3) Per the blueprint (`Config (2) nested`), nested is the documented modern style.

### D2-2: Hard max `1800` as module CONSTANT, not env var

- **Recommendation**: `HARD_MAX_THRESHOLD_SECONDS = 1800` at module level in `daemon/services/long_tool_nudge.py` (Task #1), AND `Field(..., le=HARD_MAX_THRESHOLD_SECONDS)` validator on `default_threshold_seconds` (Task #7), AND `min(metadata_or_default, HARD_MAX_THRESHOLD_SECONDS)` clamping in `_resolve_threshold` (Task #3). Three layers of defense, none env-tunable.
- **Alternatives**: (a) `LONG_TOOL_NUDGE_HARD_MAX_SECONDS` env var. (b) No hard max — trust the `le=1800` validator alone.
- **Why I chose it**: Per the working decision in the dispatch, the hard max is a SAFETY invariant (parent shouldn't be expected to act on a 4-hour tool — by then the work is stale, and a single nudge won't be acted on anyway; the parent should re-spawn). Safety invariants do not be operator-tunable. The module CONSTANT lets the runtime `min(...)` clamp overrides that bypass pydantic (e.g., a future DB-stored value someone hand-edits). The `le=` validator catches config mistakes at boot. Three layers is appropriate for a safety invariant.

### D2-3: Episode dedup = "one nudge per (parent, child, tool_call_id) episode" — close on `tool_end`, NOT a time-based cooldown

- **Recommendation**: Episode closes when Phase 1's duration-tracker records a `tool_end` event for the tool call (Task #5: `close_episode(parent_id, child_id)` called by Phase 1's `tool_end` hook). v1 = 1 nudge per episode, full stop.
- **Alternatives**: (a) Time-based cooldown: `suppress further nudges for max(2× threshold, 30 min) per (parent, child)` regardless of tool state. (b) Episode-based but re-armable per-tool: close on `tool_end` AND on new `tool_start` for a DIFFERENT `tool_call_id`. (c) Escalation ladder (already out of scope per the dispatch's NON-GOALS, but tracked for future work).
- **Why I chose it**: (a) is simpler but WRONG for bd4b36ef forensics — the 5 consecutive bash calls are SEPARATE tool calls (different `tool_call_id`); a time-based cooldown would suppress all 5 (good for v1 spam control), but it would also suppress a LEGITIMATE new long call that starts 30 min after the first ends (bad — the parent needs to know). (b) is what I implemented — closes on `tool_end`, but the next crossing on a NEW tool_call_id re-nudges (tested in U3). This matches the bd4b36ef lesson (5 long bash → 1 nudge for the first tool_call_id; if the 6th call is also long, parent gets a second nudge because the work changed) AND avoids the false-negative of (a). The trade-off (parent may see multiple nudges if the child has multiple genuinely-long tool calls in sequence) is acceptable because the parent dedups by reading the latest nudge.
- **Limitation**: Relies on Phase 1 emitting a reliable `tool_end` event. If Phase 1 misses an event, the episode stays open and a future crossing for the same (parent, child) is wrongly suppressed. Mitigation: Phase 1's stamp-clear is part of its seam contract (verified in its plan); this is the same risk class as the watchdog's 3-purge close — accepted v1 limit.

### D2-4: `_active_episodes` is RAM-only (no SQL persistence)

- **Recommendation**: RAM-only `set[tuple[str, str]]` (Task #2). Daemon restart resets the set; worst case ≤ 1 duplicate nudge post-restart.
- **Alternatives**: (a) SQL-persisted episode set (new table or `instance_metadata` JSON column). (b) Per-instance `last_nudge_at` timestamp in `instance_metadata` (compare against scan timestamp).
- **Why I chose it**: (a) adds a DB write on EVERY nudge and an extra query on EVERY crossing — for marginal benefit (1 duplicate every restart). (b) is cheaper but introduces a new metadata key, a TTL question (when does the cooldown expire?), and an extra read on every tick. RAM-only is consistent with the watchdog's `self._notified` (`:537`) — same trade-off, same accepted v1 limit, same restart-reset semantics. Restart is rare; the worst case is benign (duplicate nudge).

### D2-5: Stamp registry is RAM-only (`_in_flight: dict[child_id, stamp]`)

- **Recommendation**: RAM-only (Task #2). Scanner rebuilds from Phase 1's next `tool_start` event after restart.
- **Alternatives**: SQL-persisted stamp table.
- **Why I chose it**: In-flight tool calls are interrupted by daemon restart anyway (worker tasks are cancelled; their tool processes, if subprocess-spawned, may survive but the supervising task is gone). A persisted stamp would outlive the tool it tracks — a stale-stamp-detector would be needed, with its own edge cases. RAM is honest: "we don't know what's in-flight after restart." Phase 1's next `tool_start` repopulates the dict.

### D2-6: `priority=0` (system lane) — does NOT reset leader-attestation counters

- **Recommendation**: `priority=0` for the long-tool-nudge (Task #3 + U7 + I5 regression pin).
- **Alternatives**: (a) `priority=1` (user lane). (b) `priority=0` but with a special metadata flag that opt-in resets attestation. (c) Skip the nudge entirely if `attestation_denied_count > 0` (defer to the next user-driven fresh episode).
- **Why I chose it**: The dispatch is explicit (system nudges MUST use `priority=0` to avoid resetting attestation episode state — `instance_messaging.py:1896-1902`). (a) is WRONG per the codebase's documented contract. (b) and (c) add complexity for no v1 benefit. The pin is in U7 (unit) and I5 (integration) so a future reviewer can't accidentally regress this.

### D2-7: No `Facade-Forwarding Discipline` work needed (no new kwarg on `enqueue_message`)

- **Recommendation**: No `manager.enqueue_message` facade change (Task #3 uses only existing kwargs: `instance_id, message, source, priority, images, metadata`).
- **Alternatives**: (a) Add a `long_tool_nudge: bool = False` kwarg on the facade for explicit observability. (b) Add a `nudge_metadata: dict | None` kwarg for the metadata-keyed contract.
- **Why I chose it**: I performed the grep (verified `manager.py:6780-6784` accepts `instance_id, message, source, priority, images, metadata`); all are already forwarded. The metadata dict already carries the observability flag (`metadata["long_tool_nudge"] = True`). Adding a new facade kwarg would require: a facade-forwarding unit test, an integration test asserting dispatch, and a reviewer pass — pure overhead for an internal call path. The observability lives in `metadata`, which is the existing contract.

### D2-8: Default `interval_seconds = 60` (not 30, not 120)

- **Recommendation**: `interval_seconds=60` (Task #7).
- **Alternatives**: (a) `30` (more responsive; 2× more DB scans). (b) `120` (less DB load; 2× slower worst-case nudge latency). (c) `300` (matches watchdog's 1h cadence scaled down).
- **Why I chose it**: A 900s threshold + 60s scan means a crossing fires within 60s of crossing — total worst-case parent-notice latency ≈ 60s + LangGraph claim + turn. That's ~2 min from threshold breach to parent turn start. Tighter (30s) doubles DB scans for 1 min of latency saved; looser (120s) saves half the scans but doubles the latency to 2 min. 60s is the balanced default; operator can tune via env.

### D2-9: Default `threshold_seconds = 900` (not 600, not 1800)

- **Recommendation**: `default_threshold_seconds=900` (Task #7).
- **Alternatives**: (a) `600` (10 min — matches "feels slow to a human" intuition). (b) `1800` (matches the hard max). (c) Make the parent-tunable tool REQUIRED (no default).
- **Why I chose it**: Per the dispatch, default 900s. The bd4b36ef case (5 calls at 12.5→30 min) shows 900s catches the first call at ~15 min (after it crosses); operator can tighten via Phase 3's per-instance override. (c) is rejected — a parent-tunable tool that requires config is a chicken-and-egg UX disaster for the very first long call.

### D2-10: Scanner constructor accepts `task_repository=None` (mirrors watchdog) even though unused

- **Recommendation**: Accept `task_repository=None` (Task #2), unused in v1.
- **Alternatives**: Drop the parameter entirely.
- **Why I chose it**: (a) Future-proofs the ctor — if v2 adds a per-task stamp query, no signature change. (b) Mirrors the watchdog's ctor at `:481-491`, so a sibling test fixture that swaps repos works unchanged. (c) Cheap — one `None` field, no overhead.

### D2-11: Single canonical home for the notice text — `_build_long_tool_notice()` in `daemon/services/long_tool_nudge.py`

- **Recommendation**: Single function in `long_tool_nudge.py` (Task #4). No other module constructs the body.
- **Alternatives**: (a) Put it in `daemon/graph.py` near `ATTESTATION_NUDGE_TEXT` (`:2834`). (b) Put it in `daemon/services/_system_messages.py` (a new shared module for system-message bodies). (c) Config-template (`config.long_tool_nudge.notice_template: str`).
- **Why I chose it**: (a) violates the long-tool-specific design — graph.py is for in-turn nudges, not scanner-delivered messages. (b) is overkill for one body (wait for a second body to land before extracting). (c) makes the body operator-tunable, which is wrong — the body has a strict 5-section structure with test pins; if an operator changes it, the tests break, and the operator can't easily undo. Single canonical home + test-pinned structure = clean contract.

### D2-12: Pause-skip is a `WARN` log, not a stat counter (out of scope for v1)

- **Recommendation**: `logger.warning(...)` only (Task #3). No `parents_skipped_paused` stat; no observability counter.
- **Alternatives**: Add a stat dict mirror to watchdog's `run_once` return shape (`{"scanned_children", "nudges_enqueued", "suppressed_episode", "skipped_paused_parent", "errors"}` — already includes skipped_paused_parent; the question is whether to surface it).
- **Why I chose it**: The stat IS in the return shape (D2-12 is consistent with D2-2's Task #2 dict), but it has no dashboard / metric-wiring hookup in v1. The `[LongToolNudge] tick stats` log line at INFO-level already surfaces the count when it's > 0 (Task #6 mirrors watchdog's `:1713-1716`). Adding a real metric would require a separate hook into the daemon's metrics service — out of scope for v1. WARN log + tick-stats log = enough to debug, no infra cost.

### D2-13: Extensibility seam is a `# FUTURE` comment block, not a real recommendation

- **Recommendation**: Comment + template block in the notice body (Task #4 section (d)). The text mentions "Feature #1 companion" as the expected future work.
- **Alternatives**: (a) Add the recommendation today — promote the comment to a real line. (b) Use a more structured registry (a `FUTURE_NUDGE_INSERTIONS: list[Callable]` dict).
- **Why I chose it**: Per the dispatch, the recommendation must NOT be implemented today. The owner explicitly named this a "hard requirement" — the seam must exist so future work can drop in WITHOUT restructuring the body. A comment + template section is the minimum viable contract: the future author sees exactly where to insert, with the exact text format. (b) is over-engineered for a one-line addition. (a) violates the dispatch.

### D2-14: No `report_injections`-style obligation table for the nudge

- **Recommendation**: Nudge durability = `enqueue_message` txn (MessageQueue + Task rows, atomic) — no obligation table needed (Task #9 documents in module docstring).
- **Alternatives**: (a) Add a `report_injections`-style table tracking which parents received which nudge episodes, for the cleanup sweep. (b) Add the nudge to an existing audit log.
- **Why I chose it**: (a) and (b) both add DB writes on every nudge for marginal benefit. The MessageQueue row IS the durable record — when the parent claims the Task and the LLM processes the nudge, the row's lifecycle is identical to every other system-source message (no special cleanup needed). The watchdog (which DOES have a similar wedge-notice path) doesn't have one either. Consistency wins.

### D2-15: Lifespan wiring uses `app.state.long_tool_nudge_task` (mirrors watchdog)

- **Recommendation**: `app.state.long_tool_nudge_task = asyncio.create_task(...)` (Task #8).
- **Alternatives**: Wire the task into a shared `infra_loops` registry; use a daemon-level task group.
- **Why I chose it**: House pattern is per-loop `app.state.<loop_name>_task`. The watchdog sets `app.state.waiting_children_watchdog_task` (`:751`); this matches. Refactoring to a shared registry is a multi-loop refactor, out of scope for v1.

### D2-16: Where to import the scanner in `daemon/api.py`

- **Recommendation**: Lazy import at the top of the wiring block (`from daemon.services.long_tool_nudge import LongToolNudgeScanner, run_long_tool_nudge_loop`) — mirrors watchdog at `:715-718`.
- **Alternatives**: Top-of-file eager import.
- **Why I chose it**: Mirrors watchdog exactly; keeps the lifespan import locality tight (each infra-loop block owns its imports). Refactoring to top-of-file is a daemon-wide cleanup, out of scope.