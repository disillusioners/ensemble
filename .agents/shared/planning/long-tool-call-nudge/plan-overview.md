# Plan Overview: Long Tool Call Detection → Parent Nudge

## Objective

Give the PARENT agent visibility into a child's wedged-slow tool call — a per-child, per-tool-call detection loop that fires an ADVISORY nudge to the parent (no auto-kill/pause) when any single tool call exceeds a threshold (default 900 s, hard max 1800 s, parent-tunable per child) — so the parent can inspect, message, or terminate-and-re-spawn the child before a bd4b36ef-class 5h48m burn recurs.

## Scope Assessment

**MEDIUM** — Touches ~10 files across 4 modules (one net-new service module, `graph.py` single-line wrapper swap, `api.py` lifespan block, `config.py` nested config class, `tools/instance.py` new tool, `_tool_registry.py` one-line name entry, three `meta.json` one-liners, plus a new `docs/long-tool-nudge.md`). No DB migration (RAM stamp registry; threshold override piggybacks the existing `instance_metadata` JSONB column). No FE changes (nudge rides the existing system-source message stream). Estimated 5-7 implementation sessions (Phase 1: 2-3, Phase 2: 2, Phase 3: 1-2).

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Requested by**: Leader / architect chain (maintenancer forensics 2026-09-13)
- **Base**: `plan/long-tool-call-nudge` branched from `latest` @ `0acd3afa`
- **Evidence (bd4b36ef forensics, 2026-09-11)**: a coder child (model=coding) burned **5 h 48 m** on a single task. Five sequential `bash` calls ran 12.5→30+ min each; the loop breaker was evaded by varying arguments; a parent-side nudge sat 19 min **undeliverable mid-tool** (mid-tool children cannot receive messages); the sole backstop was the 7200 s task cap. Signature: **weak-model = long tool time + zero LLM errors**.
- **The Wedged-Turn-Alive-Child residual that forbids heartbeat gating**: `TaskHeartbeat` beats every 30 s independent of tool execution, so a child wedged mid-tool stays "heartbeat-fresh" forever. The existing watchdog's live-rung gate (`_has_recent_heartbeat`, `waiting_children_watchdog.py:848-890`) is designed to SUPPRESS nudges for exactly this busy-slow case. The long-tool detector therefore keys on **in-flight stamp age ONLY, never heartbeat** (regression-pinned by `TestLongToolNudgeScannerHeartbeatFreshStillFires`, phase 1 T3).
- **Detection is NET-NEW**: per-tool duration is not recorded anywhere today (zero per-tool log line, zero DB row); the 7200 s task cap is the only existing bound.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Per-Tool Duration Stamping + Scanner + Duration Observability + Config + Lifespan | Create `daemon/services/long_tool_nudge.py` (RAM stamp-registry singleton, wrapped `"tools"` node, scanner loop, threshold resolution, `deliver_long_tool_nudge` stub, per-completion `[LongToolNudge] TOOL_COMPLETED` log line, **HARD_MAX_THRESHOLD_SECONDS=1800 module constant**, **LongToolCallNudgeConfig** nested class + EnsembleConfig wiring + `STALE_STAMP_TTL_SECONDS=7200` belt support); wire lifespan in `api.py` with explicit shutdown mirror | None | foundation (defines the seam + registry + config + belt scaffolding + canonical threshold chain all later phases consume) | 2-3 sessions |
| 2 | Delivery Seam + Episode Dedup + Per-Child Episodic Close Mechanism | Replace the seam stub with the real `enqueue_message` call (source `system:long-tool-nudge`, priority 0, A5 double-notify), per-(parent, child) episode dedup closed on HEALTHY completion only (AD-9 gate; AD-42 Option (ii) mechanism; AD-37 belt backstop), canonical `_build_long_tool_notice` body with `# FUTURE` seam; phase 2 owns **delivery only** (config + wiring shipped in Phase 1) | Phase 1 | tight (consumes the seam signature + registry + EpisodeCtx fields + config-resolved values) | 1-2 sessions |
| 3 | Parent Tuning Tool (`set_instance_tunable`) + Agent Exposure + Docs | Parent-facing write-side tool for `instance_metadata["long_tool_call_threshold_seconds"]` (allowlist-validated, loud raise on floor/ceiling, gate on `LONG_TOOL_NUDGE_ENABLED`), conditional per-active-`version_tag` `"instance"`-category exposure (skip meta.json edit when the active variant already allows the category), `docs/long-tool-nudge.md` | **Phase 1 only** (no longer depends on Phase 2) | tight on the metadata-key contract; loose on scanner runtime (write-a-key/read-next-tick) | 1-2 sessions |

### Coupling Assessment

| From → To | Coupling | Justification |
|-----------|----------|---------------|
| Phase 1 → Phase 2 | **tight** | Phase 2 replaces the stub body of `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> bool` — the seam signature, the `EpisodeCtx` field set (child_id / parent_id / tool_name / tool_call_id / elapsed_seconds / threshold_seconds / episode_started_at), and the `[LongToolNudge]` log markers are pinned by phase 1 tests (T6) and must not change. Phase 2 also reads the shared `_LONG_TOOL_REGISTRY` singleton (no second stamp store — AD-28) and the Phase 1-resolved config values (`config.long_tool_nudge.*`; env-frozen at boot) — config ownership moved to Phase 1 (AM-8 / B1). |
| Phase 1 → Phase 3 | **tight on the metadata key + config** | Phase 3 writes exactly `metadata.long_tool_call_threshold_seconds` (snake_case, units in the name — a misspelling silently disables the override); phase 1's `_resolve_threshold` reads it via `get_metadata_value` (`repository.py:1931`, AM-12 verification). Phase 3 imports the hard-max constant AND the floor constant FROM `daemon.services.long_tool_nudge` (AD-30 / AD-38 — floor is in the same canonical home). |
| Phase 2 → Phase 3 | **none** | Phase 2 and Phase 3 touch completely disjoint file surfaces now: phase 2 owns the seam body in `daemon/services/long_tool_nudge.py` + delivery tests; phase 3 owns `daemon/tools/instance.py` + `_tool_registry.py` + active-variant `meta.json` (conditional) + `docs/long-tool-nudge.md`. Constants are shared but those live in Phase 1's canonical home. **Fully parallel after Phase 1 lands** (AM-8(d)). |

### Parallelization Opportunity

**After Phase 1 lands, Phases 2 and 3 are FULLY PARALLEL.** Phase 1 owns the seam surface, the registry, the config class, the lifespan wiring, the hard-max constant, the floor constant, the canonical threshold precedence chain, the TTL belt (`STALE_STAMP_TTL_SECONDS`), and the queue-jumping assertions — everything both later phases need.

- Phase 2 (delivery + dedup + close mechanism) and Phase 3 (write-side tool + conditional exposure + docs) touch **disjoint files**: phase 2 owns the seam body in `daemon/services/long_tool_nudge.py` (+ delivery tests); phase 3 owns `daemon/tools/instance.py` + `_tool_registry.py` + active-variant `meta.json` (conditional edit per AM-3) + `docs/long-tool-nudge.md`. The constants are shared but live in Phase 1's canonical home (the services module) — both phases import from one place, not from each other.
- Phase 1 must land first: the wrapper stamps, the scanner loop ticks, the config class parses env, the belt is wired, and the seam exists before either can be exercised end-to-end.

**Implementation runs on a NEW worktree branched from `latest`** (not this planning branch), per repo convention (worktree dispatch-path trap; plan branches are not implementation branches).

## Architecture Summary

```
                 every "tools" node invocation (both wiring variants converge here)
                                      │
                                      ▼
                    ┌─────────────────────────────────────────┐
                    │  _wrapped_tools_node(tools, registry)   │  daemon/graph.py:7879
                    │  (single-line swap at add_node("tools"))│
                    └──────┬──────────────────────┬───────────┘
              record_start │                      │ finally: clear(id)
             (all tc ids,  │                      ▼ + [LongToolNudge] TOOL_COMPLETED
              same batch   │            ┌──────────────────────┐
              started_at)  │            │ _LONG_TOOL_REGISTRY  │  RAM singleton
                           │            │ instance→tc_id→stamp │  daemon/services/
                           │            └──────────┬───────────┘  long_tool_nudge.py
                           │                       │ snapshot()
                           ▼                       ▼
                 ┌──────────────────┐   ┌─────────────────────────────┐
                 │ underlying       │   │ LongToolNudgeScanner        │
                 │ ToolNode.ainvoke │   │ (lifespan loop, 60 s tick)  │
                 │ (unchanged)      │   │ elapsed > threshold (strict)│
                 └──────────────────┘   │ AND tc_id not in _fired     │
                                        └──────────┬──────────────────┘
                                                   │ threshold resolution
                                                   │ metadata override → default
                                                   │ → min(.., HARD_MAX=1800)
                                                   ▼
                                    ┌────────────────────────────────┐
                                    │ deliver_long_tool_nudge        │
                                    │ PAUSED parent → WARN + skip    │
                                    │ (parent,child) in _active_     │
                                    │ episodes → dedup (return False)│
                                    └──────────┬─────────────────────┘
                                               │ enqueue_message(
                                               │   source="system:long-tool-nudge",
                                               │   priority=0)  + notify_work()
                                               ▼
                                    ┌────────────────────────────────┐
                                    │  PARENT  (revived from         │
                                    │  WAITING_CHILDREN; attestation │
                                    │  counters untouched)           │
                                    │  notice: inspect → message →   │
                                    │  terminate+re-spawn            │
                                    │  # FUTURE: re-spawn-with-      │
                                    │  higher-intelligence-model     │
                                    └────────────────────────────────┘

  set_instance_tunable (phase 3) ──writes──▶ instance_metadata
  ["long_tool_call_threshold_seconds"] ──read next tick──▶ threshold resolution
```

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Per-batch overestimation false-fires fast calls sharing a batch with a slow one | Medium — a recoverable false positive (parent decides; advisory only) | Medium | Accepted v1 semantics (AD-3): pinned in module docstring + `TestWrappedToolsNodeStampsAndClears`; per-id stamps keep attribution |
| Phase 1 misses a `tool_end` clear (exception path regression) → stamp leaks as eternal in-flight or episode never closes | High — wrong suppression or nudge spam | Low | `finally`-block clears in the wrapper (pause-cancel and 7200 s task-cap included); `clear_for_instance` sweep on pause; `TestWrappedToolsNodeExceptionClearsStamps` + `TestWrappedToolsNodeCancelClearsStamps` |
| Priority mistake resets leader-attestation counters on terminal-revive | High — corrupts attestation state | Low | `priority=0` mandated (AD-13); the reset branch requires `priority==1 AND msg_type==HUMAN` (`instance_messaging.py:1896-1902`); regression pins U7 + I5 |
| PAUSED parent receives a stale nudge deferred to resume | Low — confusing stale notice | Medium | Pre-check + WARN + skip in `deliver_long_tool_nudge` (claim gate would defer anyway; cleaner contract, mirrors watchdog `:996-1009`) |
| Daemon restart mid-episode → ≤1 duplicate nudge | Low — benign duplicate | Low | Accepted (AD-14): RAM-only dedup state, documented restart semantics; durable delivery via the `enqueue_message` txn |
| `meta.json` `tools.allow` change silently ignored if the tool registry caches resolved lists per-spawn | Resolved | Resolved | RESOLVED (AM-1): the registry resolves per-spawn AND per-restore (no resolved-list cache at `_tool_registry.py:15-18`), but meta.json is **snapshotted once per process** (registry singleton, no runtime re-discover). BOTH a new tool and a `tools.allow` change therefore require a daemon restart; after restart **all** instances receive them — newly-spawned via the spawn path, pre-existing via restore-path rehydration (`instance_lifecycle.py:1611/:1714/:3892/:3995`). **No silent-no-op failure mode.** |
| Planner/developer cannot actually spawn children (no `"instance"` category today) → exposure to them is useless | Resolved | Resolved | RESOLVED (AM-3): exposure is **category-wide by construction** — every agent whose active meta.json allows `"instance"` receives the tool automatically (~15 agents today, incl. tester, governor, architect, coder, leader, planner, developer, reviewer[v2], tidier[v2], wanderer, approver[v2], _mother, blueprinter, project-manager). **Phase 3 Task 6 is now a conditional version-tag resolve**: planner[v2] and developer[v2] already allow `"instance"` (likely zero edits); add `"instance"` only to a base-planner/developer variant that is actually resolved and missing it; document the full holder list in `docs/long-tool-nudge.md`. |
| `get_metadata_value` signature differs from the pinned `:1931` | Resolved | Resolved | RESOLVED (AM-12): `get_metadata_value(instance_id, key) -> Any \| None` is sync, single-key, no row hydrate, `None` on missing row/NULL/absent key, JSON re-parse at `:1980`. It is the correct scanner read (architect-verified). |
| Phase 2/3 constant-name drift (import of a non-existent symbol) | Resolved | Resolved | RESOLVED (AM-8 / AD-30): Single canonical home `HARD_MAX_THRESHOLD_SECONDS` in `daemon/services/long_tool_nudge.py` (lives there from **Phase 1**, not Phase 2); `TestFloorAndCeilingConstants` imports the same object (identity check). The floor constant (`MIN_THRESHOLD_SECONDS = 60`, AD-38) lives at the same canonical home and is enforced on BOTH the tool-side (Phase 3 `set_instance_tunable` ValueError) and the scanner read-side (Phase 1 `_resolve_threshold` falls back to default when hand-edited metadata is `< 60`). |
| Threshold override misspelled in metadata key → silent no-op (falls back to default) | Medium — feature appears broken but doesn't error | Low | Exact-spelling contract + `ALLOWED_TUNABLES` allowlist in the tool (phase 3 Task 2) + tool returns `effective_value` so the parent sees what the scanner will use |
| Wrong parent-ownership: any agent can tune any child's threshold | Low — low blast radius (one metadata key) | Low | Accepted house stance (AD-26): `send_message` has no ownership check either; Cardinal #2 scoping discipline; documented in tool docstring + docs caveats |

## Success Criteria

- [ ] SC1: A child tool call crossing `threshold` fires a nudge within ~one scan interval (≤ `interval_seconds` + LangGraph claim latency; default ≤ ~2 min from breach to parent turn start)
- [ ] SC2: Exactly ONE nudge per (parent, child) wedge EPISODE (D-A). Consecutive tool calls WITHOUT an intervening healthy (<threshold) tool completion on the same child = ONE episode (the bd4b36ef replay of 5 sequential long calls asserts exactly 1 nudge). Re-arm deterministically on (a) a healthy tool completion (close-on-tool-end, the primary re-arm), OR (b) TTL cooldown `STALE_STAMP_TTL_SECONDS=7200` (the belt — fires at end-of-`run_once` for stamps exceeding 4×HARD_MAX). Consecutive ticks on the same in-flight stamp do not re-fire (dedup set `_fired_episodes`, only set on successful fire — AM-7); `_active_episodes` closes on `tool_end`; new `tool_call_id` post-close = fresh episode.
- [ ] SC3: A parent in `WAITING_CHILDREN` transitions to `RUNNING` after the nudge lands (the wake is real — `enqueue_message` + `notify_work`, not just a committed row)
- [ ] SC4: A heartbeat-fresh child wedged mid-tool is STILL nudged (heartbeat-independent detection — regression pin `TestLongToolNudgeScannerHeartbeatFreshStillFires`)
- [ ] SC5: A PAUSED parent is skipped (WARN log; zero `enqueue_message` calls; no exception into the scanner tick)
- [ ] SC6: Every tool completion emits one `[LongToolNudge] TOOL_COMPLETED` INFO log line carrying `instance_id`, `tool_call_id`, `tool_name`, `duration_ms`, `threshold_seconds`, `threshold_crossed` (forensic duration record that did not exist before)
- [ ] SC7: `set_instance_tunable` validates the range [60, 1800] — rejects (loud `ValueError`) values outside it, rejects non-int/bool, rejects unknown keys with `{"error_code": "UNKNOWN_KEY"}`; effective value = `min(stored-or-default, 1800)` echoed in the return dict. The 60 s floor is enforced on **both sides**: the tool loud-raises if `value < 60`, AND the scanner's `_resolve_threshold` (Phase 1) treats a hand-edited metadata value `< 60` as invalid and falls back to the configured default. When `LONG_TOOL_NUDGE_ENABLED=0` the tool returns a clear "feature disabled" message and writes NO metadata; the per-completion `[LongToolNudge] TOOL_COMPLETED` log line and stamp registry still tick by design (stamp/log presence ≠ delivery).
- [ ] SC8: The nudge reaches the parent with `priority=0` and provably does NOT reset `attestation_denied_count` / completion-gate counters (U7 unit + I5 integration pins)
- [ ] SC9: `LONG_TOOL_NUDGE_ENABLED=0` (a) disables the scanner at boot (lifespan logs "disabled by config"; `app.state.long_tool_nudge_task = None`; zero tick overhead for the SCAN loop itself — stamp registry + per-completion `[LongToolNudge] TOOL_COMPLETED` log line continue by design); (b) gates `set_instance_tunable` — when disabled, the tool returns a clear "feature disabled" message and writes NO metadata. Stamp/log presence ≠ delivery.

## Activation

### Pre-implementation worktree hazards (READ BEFORE CODING)

- **Bare `uv sync` is enough** — deps live in `[dependency-groups].dev` (PEP 735); `uv sync` includes them. The old `uv sync --extra dev` advice is OBSOLETE.
- **Tests run EXCLUSIVELY via `uv run python -m pytest`** from the worktree root. Bare `pytest` PATH-resolves to a broken Homebrew install (`_console_main` ImportError) and silently fails.
- **`.env` is gitignored.** Worktree dispatches start without it; export env vars (POSTGRES_*, etc.) or copy `.env` from the main checkout BEFORE running `dev.sh`. Port collision: `dev.sh:88 HARDCODES PORT=8079` — custom-port boots bypass dev.sh.
- **`git rev-parse branch+short HEAD` BEFORE EVERY `git` invocation.** Shared-worktree drift is a hard blocker; never `checkout`/`stash`/`reset` to recover — rebase or re-clone instead. Multi-worker merge-gate: foreign actors can mutate an index AFTER authoring — verify with `git diff --cached` before merge AND at report time.
- **Implementation branches from `latest`**, NOT from `plan/long-tool-call-nudge`. Plan docs sit on this planning branch; the implementation worktree branches from `latest` (the integration branch). **Merge-order hazard**: do NOT merge the implementation branch back into `plan/long-tool-call-nudge` by accident; `plan/long-tool-call-nudge` is a planning branch and its commits land in `latest` via the planning-only path. Reviewers re-pin tree state (`rev-parse + status --porcelain`) at the start AND end of the review.
- **Plan's own per-file edits** (this doc-consolidation pass) live ONLY on `plan/long-tool-call-nudge`; do NOT cherry-pick them into the implementation worktree.

- **Daemon restart required** — the scanner is lifespan-wired (`api.py` block after the waiting-children-watchdog block); the wrapped `"tools"` node and the config class also load at boot. No DB migration to run.
- **Kill-switch**: `LONG_TOOL_NUDGE_ENABLED` (env prefix of the nested `LongToolCallNudgeConfig`), **default ON**. `=0` disables the scanner at boot AND gates `set_instance_tunable` (Phase 3) — the tool returns a clear "feature disabled" message and writes NO metadata when disabled. The per-completion `[LongToolNudge] TOOL_COMPLETED` log line and stamp registry continue to tick by design when disabled; only the nudge *delivery* and tool *write* cease (stamp/log presence ≠ delivery). The `#`-comments in the module docstring record the restart semantics.
- **Operator expectation note (A-N3 alignment, SC9):** Kill-switch `LONG_TOOL_NUDGE_ENABLED=0` does **NOT** mean zero overhead. The scanner loop stops (no tick cost from `run_once` / threshold resolution), but the per-completion `[LongToolNudge] TOOL_COMPLETED` log line and the stamp registry's `record_start`/`clear` continue by design — stamp/log presence is part of the duration-observability surface (SC6, independent of the kill-switch) and is preserved for forensics even when delivery is off. Only **nudge delivery** and **`set_instance_tunable` writes** cease when disabled. Operators reading the boot log or a forensic timeline should expect `[LongToolNudge] TOOL_COMPLETED` lines to appear regardless of the kill-switch state; absence would indicate the wrapper is not wired (separate failure mode, not a kill-switch effect).
- **Validation boot lines** (grep after restart):
  - `Long-tool-nudge scanner started: interval=60s, default_threshold=900s` (enabled path)
  - `Long-tool-nudge scanner disabled by config` (kill-switch path)
  - Absence of either + `app.state.long_tool_nudge_task` unset ⇒ ctor failed — check the preceding ERROR line (`Long-tool-nudge scanner DISABLED — construction failed`)
- **Post-activation smoke**: trigger (or simulate) a >900 s in-flight child tool call; observe one `[LongToolNudge] TOOL_COMPLETED` line on completion, one `[LongToolNudge] STUB_FIRE`-replaced delivery tick in the scanner stats (`[LongToolNudge] tick stats: {... 'nudges_enqueued': 1 ...}`), and the parent's receipt of a `[system:long-tool-nudge]` advisory.
- **Rollback**: set `LONG_TOOL_NUDGE_ENABLED=0` + restart (no schema to revert; `graph.py` wrapper swap is inert without the registry consumers — the wrapper still stamps/clears, cost ≈ two dict ops per tool call).

## Working-Names Table (canonical, post-reconciliation)

| Artifact | Canonical value |
|----------|-----------------|
| Module | `daemon/services/long_tool_nudge.py` (net-new, single home for registry + scanner + seam + notice builder + loop) |
| Scanner class | `LongToolNudgeScanner` (phase 1's working name `LongToolNudgeDetector` renamed — AD-29) |
| Stamp registry | `LongToolNudgeRegistry`, module-level singleton `_LONG_TOOL_REGISTRY` (the ONLY stamp store — AD-28) |
| Stamp shape | `_Stamp(tool_call_id, tool_name, started_at: time.monotonic(), parent_id: str)` (AD-42 Option (ii) cached-parent_id pin; `parent_id` captured at `record_start` and lives on the stamp for its lifetime — see phase1 Task 2 + Task 3) |
| Hand-off seam | `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> bool` (phase 1 stub → phase 2 real body) |
| Episode context | `LongToolNudgeEpisodeCtx` (child_id, parent_id, tool_name, tool_call_id, elapsed_seconds, threshold_seconds, episode_started_at) |
| Stamp-level fire dedup | `_fired_episodes: set[(child_id, tool_call_id)]` (scanner; per-process) |
| Nudge-level episode dedup | `_active_episodes: set[(parent_id, child_id)]` (scanner; close on HEALTHY `tool_end` ONLY — `duration_seconds < effective_threshold_seconds`, AD-9 + AD-42 Option ii close-gate; long completions intentionally LEAVE the episode open; AD-37 TTL belt is the secondary close; re-arm on new `tool_call_id` after a successful close) |
| Config class | `LongToolCallNudgeConfig(BaseSettings)` nested on `EnsembleConfig.long_tool_nudge` |
| Env vars | `LONG_TOOL_NUDGE_ENABLED` (default ON), `LONG_TOOL_NUDGE_INTERVAL_SECONDS` (default 60, ge=1), `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` (default 900, ge=1, le=1800) |
| Hard-max constant | `HARD_MAX_THRESHOLD_SECONDS = 1800` in `daemon/services/long_tool_nudge.py` (canonical home — AD-30; defined in **Phase 1** per AM-8); imported by `daemon/services/long_tool_nudge.py` (self), `daemon/config.py`, and `daemon/tools/instance.py` |
| Floor constant | `MIN_THRESHOLD_SECONDS = 60` in `daemon/services/long_tool_nudge.py` (AD-38; same canonical home, defined in **Phase 1**); the 60 s floor is enforced on BOTH sides — the tool's loud ValueError (Phase 3) AND the scanner's `_resolve_threshold` (Phase 1: any hand-edited metadata value `< 60` falls back to default) |
| Stale-stamp TTL | `STALE_STAMP_TTL_SECONDS = 7200` in `daemon/services/long_tool_nudge.py` (AD-9a; belt constant decoupled from the (unresolved) effective graph-task cap per P-1); end-of-`run_once` force-clear + close + re-arm + WARN |
| Metadata key | `long_tool_call_threshold_seconds` (int seconds; absent ⇒ default; effective = `min(value, 1800)` after the floor + integer validation) |
| **Canonical threshold precedence chain** | (1) kill-switch `LONG_TOOL_NUDGE_ENABLED` — when OFF, no scanner fires, no tool write; (2) per-child metadata key `long_tool_call_threshold_seconds` (read via `get_metadata_value`); if absent / None / non-int / `< 60` (read-side floor bypass) ⇒ fall through; (3) env default `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` (validated `ge=1 le=1800` at boot); (4) `min(·, HARD_MAX_THRESHOLD_SECONDS=1800)` clamp (defense layer 1 — environment ceiling); (5) strict `>` comparison in the scanner (`elapsed > effective_threshold`; matches watchdog precedent, AD-4) — fire boundary. **Stated once here; every other mention refers back to this chain.** |
| Per-tick metadata-read memoization | The scanner resolves thresholds **once per tick per child with a stamp** (a single `get_metadata_value` per `(instance_id)` per `run_once`); not per stamp — a child with N concurrent stamps shares one resolution. Cheap insurance against the 60 s tick rate against the N-stamps-per-child stress case. |
| Message source | `system:long-tool-nudge` (constant `LONG_TOOL_NUDGE_SOURCE`) |
| Message priority | `0` (system lane — never resets attestation counters) |
| Tunable tool | `set_instance_tunable(instance_id, key, value)` in `daemon/tools/instance.py`, category `"instance"`, registered in `daemon/tools/_tool_registry.py` alphabetic list |
| Defaults | threshold 900 s · hard max 1800 s · floor 60 s · interval 60 s · registry overflow cap 1024 instances |
| Loop task | `app.state.long_tool_nudge_task`, named asyncio task `"long-tool-nudge"` |
| Notice builder | `_build_long_tool_notice()` — single canonical home, 5 mandatory sections, `# FUTURE` extensibility seam |
| Log markers | `[LongToolNudge] TOOL_COMPLETED` (per completion), `[LongToolNudge] tick stats` (scanner), `[LongToolNudge] STUB_FIRE` (phase-1-only seam stub) |

## Open Questions

- **Bounded per-call `bash` timeout** (phase 1 D11 / AD-34, DECIDED — STRICTLY DEFER): the daemon's `bash` tool **already has** a per-call `timeout` kwarg (default **1800 s**, cap 1800, enforced `asyncio.wait_for` at `bash.py:327`), so the bd4b36ef incident was a **visibility** gap, not an unboundedness gap — the last bash ran ~30 min and never returned, but the long-tool-nudge path now covers the visibility side. A default-policy change (e.g. `ENSEMBLE_BASH_DEFAULT_TIMEOUT_SECONDS=300`) is a separate feature; full ticket embedded in `architecture-recommendation.md` §3.1 (amended per AM-4). v1 notice text must NOT reference the future env var (AD-8 structure locked).
- **True per-call attribution** (phase 1 D12 / AD-33): v1 stamps at batch entry (overestimation for co-batched fast calls). Reimplementing dispatch inside the wrapper is the named future refinement.
- **(Resolved)** ~~AD-32 tool-registry cache timing~~ — see Risks table (AM-1: per-spawn-and-per-restore resolution, meta.json snapshotted per-process, daemon restart reaches all instances via restore-rebuild).
- **(Resolved)** ~~AD-35 planner/developer spawn capability~~ — see Risks table (AM-3: category-wide exposure by construction, ~15 holders).
- **(Resolved)** ~~AD-36 `get_metadata_value` signature~~ — see Risks table (AM-12: signature verified at `:1931-1982`).

## Tracking

- Created: 2026-09-13
- Branch: `plan/long-tool-call-nudge` (phase files committed at `18019bd4` / `ce85c87c` / `b6329a89`; base `0acd3afa`)
- Status: Ready for Review (synthesis + cross-phase reconciliation applied; see `decisions.md` AD-28..AD-31 for the reconciliation record)
