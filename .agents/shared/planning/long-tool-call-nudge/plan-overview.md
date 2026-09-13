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
- **The Wedged-Turn-Alive-Child residual that forbids heartbeat gating**: `TaskHeartbeat` beats every 30 s independent of tool execution, so a child wedged mid-tool stays "heartbeat-fresh" forever. The existing watchdog's live-rung gate (`_has_recent_heartbeat`, `waiting_children_watchdog.py:848-890`) is designed to SUPPRESS nudges for exactly this busy-slow case. The long-tool detector therefore keys on **in-flight stamp age ONLY, never heartbeat** (regression-pinned by `TestLongToolNudgeDetectorHeartbeatFreshStillFires`, phase 1 T3).
- **Detection is NET-NEW**: per-tool duration is not recorded anywhere today (zero per-tool log line, zero DB row); the 7200 s task cap is the only existing bound.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Per-Tool Duration Stamping + Scanner + Duration Observability | Create `daemon/services/long_tool_nudge.py` (RAM stamp-registry singleton, wrapped `"tools"` node, scanner loop, threshold resolution, `deliver_long_tool_nudge` stub, per-completion `[LongToolNudge] TOOL_COMPLETED` log line); wire lifespan in `api.py` | None | foundation (defines the seam + registry both later phases consume) | 2-3 sessions |
| 2 | Delivery Seam + Episode Dedup + Config Block + Restart Semantics | Replace the seam stub with the real `enqueue_message` call (source `system:long-tool-nudge`, priority 0, A5 double-notify), per-(parent, child) episode dedup closed on `tool_end`, canonical `_build_long_tool_notice` body with `# FUTURE` seam, nested `LongToolCallNudgeConfig` + lifespan wiring | Phase 1 | tight (consumes the seam signature + registry + EpisodeCtx fields) | 2 sessions |
| 3 | Parent Tuning Tool (`set_instance_tunable`) + Agent Exposure + Docs | Parent-facing write-side tool for `instance_metadata["long_tool_call_threshold_seconds"]` (allowlist-validated, loud raise on floor/ceiling), `"instance"`-category exposure to leader/planner/developer, `docs/long-tool-nudge.md` | Phase 1 + Phase 2 | tight on the metadata-key contract; loose on scanner runtime (write-a-key/read-next-tick) | 1-2 sessions |

### Coupling Assessment

| From → To | Coupling | Justification |
|-----------|----------|---------------|
| Phase 1 → Phase 2 | **tight** | Phase 2 replaces the stub body of `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> bool` — the seam signature, the `EpisodeCtx` field set (child_id / parent_id / tool_name / tool_call_id / elapsed_seconds / threshold_seconds / episode_started_at), and the `[LongToolNudge]` log markers are pinned by phase 1 tests (T6) and must not change. Phase 2 also reads the shared `_LONG_TOOL_REGISTRY` singleton (no second stamp store — AD-28). |
| Phase 1 → Phase 3 | **tight on the metadata key** | Phase 3 writes exactly `metadata.long_tool_call_threshold_seconds` (snake_case, units in the name — a misspelling silently disables the override); phase 1's `_resolve_threshold` reads it via `get_metadata_value` (`repository.py:1931`). Import-direction: phase 3 imports the hard-max constant FROM `daemon.services.long_tool_nudge` (AD-30). |
| Phase 2 → Phase 3 | **loose** | Shared config validation constants only (`HARD_MAX_THRESHOLD_SECONDS` imported by the tool's range check) + the effective-value formula `min(stored-or-default, 1800)` that phase 3 must echo back in its return dict. No shared runtime state: the tool writes a key; the scanner reads it on the next tick (≤60 s). |

### Parallelization Opportunity

**Phases 2 and 3 are partially parallel** once phase 1's seam lands:

- Phase 2 (delivery + dedup + config) and Phase 3 (write-side tool + exposure + docs) touch **disjoint files** — phase 2 owns `daemon/config.py` + `api.py` lifespan + the module's delivery half; phase 3 owns `daemon/tools/instance.py` + `_tool_registry.py` + `agents/*/meta.json` + `docs/`. The only shared surface is the `HARD_MAX_THRESHOLD_SECONDS` constant (phase 3 imports it; phase 2 defines it — phase 2 lands the constant first, or phase 3 stubs it behind the documented name and flips the import when phase 2 merges).
- Phase 1 must land first: the wrapper stamps, the scanner loop ticks, and the seam exists before either can be exercised end-to-end.

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
| meta.json `tools.allow` change silently ignored if the tool registry caches resolved lists per-spawn | Medium — exposure doesn't take effect | Unknown | **OPEN VERIFICATION (AD-32)**: phase 3 Task 7 must check `_tool_registry.py` cache behaviour; if per-spawn, document that meta.json changes affect only newly-spawned instances |
| Planner/developer cannot actually spawn children (no `"instance"` category today) → exposure to them is useless | Low — narrower surface than planned | Unknown | **OPEN VERIFICATION (AD-35)**: phase 3 D9 fallback = leader-only exposure; verify spawn capability during Task 7 |
| `get_metadata_value` signature differs from the pinned `:1931` | Low — one-line fallback | Low | **OPEN VERIFICATION (AD-36)**: fall back to `repo.get(instance_id)` + dict access; same result |
| Phase 2/3 constant-name drift (import of a non-existent symbol) | Medium — ImportError at boot | Low | Single canonical home `HARD_MAX_THRESHOLD_SECONDS` in `daemon/services/long_tool_nudge.py` (AD-30); `TestFloorAndCeilingConstants` imports the same object (identity check) |
| Threshold override misspelled in metadata key → silent no-op (falls back to default) | Medium — feature appears broken but doesn't error | Low | Exact-spelling contract + `ALLOWED_TUNABLES` allowlist in the tool (phase 3 Task 2) + tool returns `effective_value` so the parent sees what the scanner will use |
| Wrong parent-ownership: any agent can tune any child's threshold | Low — low blast radius (one metadata key) | Low | Accepted house stance (AD-26): `send_message` has no ownership check either; Cardinal #2 scoping discipline; documented in tool docstring + docs caveats |

## Success Criteria

- [ ] SC1: A child tool call crossing `threshold` fires a nudge within ~one scan interval (≤ `interval_seconds` + LangGraph claim latency; default ≤ ~2 min from breach to parent turn start)
- [ ] SC2: Exactly ONE nudge per (parent, child) episode — consecutive ticks on the same in-flight stamp do not re-fire (dedup set `_active_episodes`; closes on `tool_end`; new `tool_call_id` = fresh episode)
- [ ] SC3: A parent in `WAITING_CHILDREN` transitions to `RUNNING` after the nudge lands (the wake is real — `enqueue_message` + `notify_work`, not just a committed row)
- [ ] SC4: A heartbeat-fresh child wedged mid-tool is STILL nudged (heartbeat-independent detection — regression pin `TestLongToolNudgeDetectorHeartbeatFreshStillFires`)
- [ ] SC5: A PAUSED parent is skipped (WARN log; zero `enqueue_message` calls; no exception into the scanner tick)
- [ ] SC6: Every tool completion emits one `[LongToolNudge] TOOL_COMPLETED` INFO log line carrying `instance_id`, `tool_call_id`, `tool_name`, `duration_ms`, `threshold_seconds`, `threshold_crossed` (forensic duration record that did not exist before)
- [ ] SC7: `set_instance_tunable` validates the range [60, 1800] — rejects (loud `ValueError`) values outside it, rejects non-int/bool, rejects unknown keys with `{"error_code": "UNKNOWN_KEY"}`; effective value = `min(stored-or-default, 1800)` echoed in the return dict
- [ ] SC8: The nudge reaches the parent with `priority=0` and provably does NOT reset `attestation_denied_count` / completion-gate counters (U7 unit + I5 integration pins)
- [ ] SC9: `LONG_TOOL_NUDGE_ENABLED=0` disables the scanner at boot (lifespan logs "disabled by config"; `app.state.long_tool_nudge_task = None`; zero tick overhead)

## Activation

- **Daemon restart required** — the scanner is lifespan-wired (`api.py` block after the waiting-children-watchdog block); the wrapped `"tools"` node and the config class also load at boot. No DB migration to run.
- **Kill-switch**: `LONG_TOOL_NUDGE_ENABLED` (env prefix of the nested `LongToolCallNudgeConfig`), **default ON**. `=0` restores exact pre-feature behaviour (no loop task; zero scan overhead). The `#`-comments in the module docstring record the restart semantics.
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
| Stamp shape | `_Stamp(tool_call_id, tool_name, started_at: time.monotonic())` |
| Hand-off seam | `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> bool` (phase 1 stub → phase 2 real body) |
| Episode context | `LongToolNudgeEpisodeCtx` (child_id, parent_id, tool_name, tool_call_id, elapsed_seconds, threshold_seconds, episode_started_at) |
| Stamp-level fire dedup | `_fired_episodes: set[(child_id, tool_call_id)]` (scanner; per-process) |
| Nudge-level episode dedup | `_active_episodes: set[(parent_id, child_id)]` (scanner; close on `tool_end`, re-arm on new `tool_call_id`) |
| Config class | `LongToolCallNudgeConfig(BaseSettings)` nested on `EnsembleConfig.long_tool_nudge` |
| Env vars | `LONG_TOOL_NUDGE_ENABLED` (default ON), `LONG_TOOL_NUDGE_INTERVAL_SECONDS` (default 60, ge=1), `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` (default 900, ge=1, le=1800) |
| Hard-max constant | `HARD_MAX_THRESHOLD_SECONDS = 1800` in `daemon/services/long_tool_nudge.py` (canonical home — AD-30); imported by `daemon/config.py` and `daemon/tools/instance.py` |
| Metadata key | `long_tool_call_threshold_seconds` (int seconds; absent ⇒ default; effective = `min(value, 1800)`) |
| Message source | `system:long-tool-nudge` (constant `LONG_TOOL_NUDGE_SOURCE`) |
| Message priority | `0` (system lane — never resets attestation counters) |
| Tunable tool | `set_instance_tunable(instance_id, key, value)` in `daemon/tools/instance.py`, category `"instance"`, registered in `daemon/tools/_tool_registry.py` alphabetic list |
| Defaults | threshold 900 s · hard max 1800 s · floor 60 s · interval 60 s · registry overflow cap 1024 instances |
| Loop task | `app.state.long_tool_nudge_task`, named asyncio task `"long-tool-nudge"` |
| Notice builder | `_build_long_tool_notice()` — single canonical home, 5 mandatory sections, `# FUTURE` extensibility seam |
| Log markers | `[LongToolNudge] TOOL_COMPLETED` (per completion), `[LongToolNudge] tick stats` (scanner), `[LongToolNudge] STUB_FIRE` (phase-1-only seam stub) |

## Open Questions

- **Bounded per-call `bash` timeout** (phase 1 D11 / AD-32): the last bd4b36ef bash ran ~30 min and never returned; a bounded per-call timeout would have cancelled cleanly. Explicitly OUT of scope v1 — captured for a future feature.
- **True per-call attribution** (phase 1 D12 / AD-33): v1 stamps at batch entry (overestimation for co-batched fast calls). Reimplementing dispatch inside the wrapper is the named future refinement.
- **Three phase-3 verifications** (AD-32/35/36): tool-registry cache behaviour, planner/developer spawn capability, `get_metadata_value` signature — all resolvable by the implementer with a read; none block planning.

## Tracking

- Created: 2026-09-13
- Branch: `plan/long-tool-call-nudge` (phase files committed at `18019bd4` / `ce85c87c` / `b6329a89`; base `0acd3afa`)
- Status: Ready for Review (synthesis + cross-phase reconciliation applied; see `decisions.md` AD-28..AD-31 for the reconciliation record)
