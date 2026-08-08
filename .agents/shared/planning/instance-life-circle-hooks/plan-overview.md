# Plan Overview: Instance Lifecycle Hooks

Date: 2026-08-08
Author: planner[v2] via plan-creation worker
Status: Draft

## Objective

When a child instance completes, allow its agent's `lifecycle_hooks` config to register a hook function (e.g. `add_to_shared_context_md_files`) that automatically saves the child's report as a markdown file in the shared context directory — making sibling agent findings immediately discoverable via the existing heuristic-matching injection system.

## Scope

### In Scope

- **Lifecycle hook registry + dispatcher** — a new module (`daemon/services/lifecycle_hooks.py`) with an extensible event-keyed registry (dict-of-dicts: `{event_name: {hook_name: hook_fn}}`) and an async `dispatch_lifecycle_hooks(event_name, hook_names, context)` entry point. Only `on_complete` event implemented. The dispatcher filters by `hook_names` (only those named hooks run, never all hooks for the event).
- **First hook function: `add_to_shared_context_md_files()`** — writes the child's final report (`last_content`) as a timestamped `.md` file to `resolve_context_dir(context_key)`, reusing the exact pattern from `_save_explorer_result()` in `knowledge_tools.py:524-561`.
- **Integration at the single convergence point** — one call site in `_dispatch_post_commit_side_effects()` (`child_reports.py:2713` branch, the `regular_child_completed` outcome), fired AFTER all existing side effects but BEFORE the method returns at line 2883. Wrapped in `asyncio.wait_for(timeout=5.0)` with try/except — see Risk #1.
- **`lifecycle_hooks` config field on `AgentMetadata`** — typed Pydantic field (`dict[str, list[str]]`, default empty dict) in `daemon/registry.py:354`. Empty dict = no hooks = zero behavior change. The value is a list so multiple hook functions can be configured per event.
- **Wanderer meta.json config** — add `"lifecycle_hooks": {"on_complete": ["add_to_shared_context_md_files"]}` to `agents/wanderer/meta.json`.
- **Shared helper: `write_context_file()` extracted to `daemon/services/context_tools.py`** — single source of truth for context-dir file writes. Reused by the hook function. The existing `_save_explorer_result` / `_save_experience_result` can be refactored to use it later (out of scope for this feature, but the hook must NOT create a third divergent copy).
- **Unit tests** — registry/dispatcher (including name filtering), hook function, slug-derivation heading extraction, filename collision + format-compat, meta.json parsing, integration with the completion path, timeout/cancellation behavior, heuristic injection end-to-end.

### Out of Scope

- **Other lifecycle events** (`on_spawn`, `on_pause`, `on_resume`, `on_error`, `on_terminate`) — designed for but NOT implemented. The registry's event-keyed structure makes adding them a future one-liner registration.
- **Other hook functions** (beyond `add_to_shared_context_md_files`) — same extensible design, future work.
- **Refactoring existing lifecycle code** — the existing SSE, CompletionRegistry, lifecycle event, and bus hook code stays as-is. The hook system is purely additive.
- **Root/tool-invocation completion hooks** — only `regular_child_completed` outcome triggers `on_complete` hooks in this phase. Root and tool-invocation branches explicitly `return` before the hook call. (Extensible later.)
- **Error / cancelled / watchover-terminated paths** — these outcomes are routed through `ErrorReportingService` (or the watchover termination path) and never enter `_dispatch_post_commit_side_effects` at all. They are excluded by construction (see Outcome Eligibility below). Future hooks for these paths would require **separate dispatch calls** in `ErrorReportingService` and the watchover termination path.
- **Hook ordering / priority / failure cascading** — hooks fire in insertion order, failures are logged and swallowed (fire-and-forget with `asyncio.wait_for(timeout=5.0)`). No priority field.
- **Hook persistence / hot-reload** — hooks are registered at module import time (module-level registry). No DB table. No runtime reconfiguration.
- **Deduplication of hook-written context files** — the first hook function does NOT deduplicate (unlike `_save_explorer_result`). Each child completion writes a new file; the heuristic matcher caps at 50 files by mtime. Dedup is a future hook-function-level concern.

### Outcome Eligibility (W6)

| Outcome | `on_complete` hook fires? | Reason |
|---------|---------------------------|--------|
| `regular_child_completed` | ✅ **ELIGIBLE** | Reaches the hook dispatch site (line 2882, before return at 2883) |
| `root_completed` | ❌ excluded | Early `return` before the hook site |
| `tool_invocation_completed` | ❌ excluded | Early `return` before the hook site |
| error / cancelled | ❌ excluded | Routed through `ErrorReportingService`, never enters `_dispatch_post_commit_side_effects` |
| watchover-terminated | ❌ excluded | Separate path (watchover termination handler) |

**Future hook sites for error/cancelled/watchover would require separate `dispatch_lifecycle_hooks(...)` calls in those paths.** The dispatcher signature and registry are reusable; only the call sites need to be added.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Lifecycle Hook Registry & Dispatcher | Extensible event→hook registry with async dispatch | 4 | independent | pending |
| 2 | First Hook Function: add_to_shared_context_md_files | Write child report to shared context dir | 3 | loose (shares context_tools with Phase 3) | pending |
| 3 | Config Field + Wanderer meta.json | Add `lifecycle_hooks` field to `AgentMetadata`, configure Wanderer | 3 | tight (Phase 4 reads config) | pending |
| 4 | Integration into Completion Path | Wire hook dispatch into _dispatch_post_commit_side_effects | 4 | tight (depends on 1+3) | pending |
| 5 | Tests | Unit + integration test coverage | 4 | loose (depends on 1-4) | pending |

## Coupling Map

|  | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| Phase 1 | — | independent | independent | tight (Phase 4 calls dispatcher) | loose (tested) |
| Phase 2 | independent | — | independent | loose (Phase 4 passes hook name) | loose (tested) |
| Phase 3 | independent | independent | — | tight (Phase 4 reads `lifecycle_hooks` config) | loose (tested) |
| Phase 4 | tight | loose | tight | — | loose (tested) |
| Phase 5 | loose | loose | loose | loose | — |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Hook execution blocks the post-commit side-effect chain (bus terminal hook delayed) | Medium | Medium | Run hook dispatch via `asyncio.wait_for(dispatch_lifecycle_hooks(...), timeout=5.0)` wrapped in `try/except Exception` (with `except asyncio.CancelledError: raise` first to preserve cancellation semantics — see W3). Hook failures are logged and swallowed — they must NEVER prevent the bus terminal hook from firing. Bare `asyncio.create_task()` is NOT used because it offers no timeout and risks GC of the task handle. |
| 2 | Hook fires for root/tool-invocation outcomes unexpectedly | Medium | Low | Explicitly gate: only `outcome == "regular_child_completed"` triggers the hook dispatch. Root/tool-invocation branches `return` before reaching the hook call. |
| 3 | Meta resolution fails (agent_id not found, registry error) | Low | Medium | Defensive `get_version()` → `get_resolved()` fallback (existing pattern). If meta is None or `lifecycle_hooks` is empty/missing, skip hook dispatch entirely. |
| 4 | Context directory write fails (disk full, permissions) | Low | Low | Fire-and-forget with try/except. Log at WARNING level. Never raise to the caller. Mirrors `_save_explorer_result` pattern. |
| 5 | `get_tree_root_id()` call adds latency to the post-commit path | Medium | Low | The call is a sync DB traversal on the event loop. Wrap in `asyncio.to_thread()` to avoid blocking, OR resolve from the already-available `instance_id`/`parent_id` via `_resolve_tree_root_id()` pattern. Prefer reusing `_resolve_tree_root_id()` from `context_messages.py:856` (see S4). |
| 6 | Existing tests break due to meta.json schema change | Medium | Low | `lifecycle_hooks` defaults to `{}` (empty dict of empty lists). All existing agents without the field get zero hooks. `extra="ignore"` on AgentMetadata means even raw-dict access is safe. |
| 7 | Context dir accumulates unbounded files over many completions | Low | Medium | Out of scope for this phase. The heuristic matcher already caps at 50 files by mtime (`context_injection.py:812-929`). Future cleanup hook or TTL can address this. |
| 8 | Hook dispatch uses `except BaseException` and swallows `CancelledError`, breaking pause-cancel | Medium | Low | Mandate `except Exception:` everywhere, with `except asyncio.CancelledError: raise` placed BEFORE the broad `except Exception` when cancellation must propagate (W3). This matches the codebase-wide fix referenced in the C2 DB Torn State Fix critical note. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Wanderer child completing produces a `.md` file in `{tempdir}/ensemble/context/{context_key}/` | Inspect the directory after a Wanderer child completes a task; verify file exists with timestamped name | File exists, content matches child's last assistant message |
| 2 | Sibling agents receive the hook-written file via heuristic context injection | Spawn two Wanderer children on related topics; check if second child's context injection includes the first child's report file | File appears in `list_context` output and in injected context messages |
| 3 | Agents WITHOUT `lifecycle_hooks` config have zero behavior change | Run existing test suite; verify no new files written, no hook dispatch called | 100% of existing tests pass, no context files created for non-configured agents |
| 4 | Hook failure does NOT block report delivery to parent | Force a write error (read-only dir); verify bus terminal hook still fires and parent receives report | Parent receives completion report; hook error logged but swallowed |
| 5 | Registry is extensible: adding a new event type requires only a new registry key + dispatch call | Code review: adding `on_spawn` event requires only `_HOOK_REGISTRY["on_spawn"] = {...}` and a single `dispatch_lifecycle_hooks("on_spawn", ...)` call at the spawn site | One registration line + one dispatch call = new event live |
| 6 | `lifecycle_hooks` field present in AgentMetadata with correct type | Pydantic validation test: `AgentMetadata(lifecycle_hooks={"on_complete": ["add_to_shared_context_md_files"]})` parses without error; empty default works | Field is `dict[str, list[str]]`, default `{}` (each value is a list of hook function names) |
| 7 | Hook dispatch is filtered by `hook_names` (C1) | Integration test: 2 hooks registered for `on_complete`, agent configures only 1 → only that 1 runs (verified via mock/spy) | Only configured hook names execute; other registered hooks for the same event are skipped |
| 8 | Hook dispatch times out gracefully at 5s (W2) | Test: hook function sleeps 10s → outer call times out at 5s, post-commit side effects continue normally | Bus terminal hook still fires after timeout; warning logged |
| 9 | Exception handling uses `except Exception:` (W3) | Code review + test: simulated `CancelledError` propagates correctly; non-cancellation exceptions are caught and logged | No swallowed cancellations |

## Research Insights

### Integration Point (verified)

- **`_dispatch_post_commit_side_effects()`** (`child_reports.py:2574-2892`) is the single convergence point for all three completion outcomes.
- The `regular_child_completed` branch (line 2713) runs: bus terminal hook (2725) → corrective multi-turn emit (2774) → worker pool notify (2789) → report-injection pending set (2807) → CompletionRegistry (2812) → SSE child completed (2816) → lifecycle broadcast (2827) → parent completion cascade (2842) → parent waiting_children SSE (2869) → title generation (2882) → **return (2883)**.
- **Insertion point: line 2882, BEFORE the `return` at 2883**, AFTER all existing side effects. This ensures hooks run after report delivery is fully wired but do not delay any critical path.
- `_ChildCompletionDbResult` (NamedTuple, line 67) provides: `instance_id`, `agent_id`, `parent_id`, `child_agent_id`, `report_message_id`, `outcome`.
- `last_content` (the child's final assistant message) is passed as a separate parameter to `_dispatch_post_commit_side_effects()`.

### Meta Resolution (verified)

- `AgentMetadata` at `daemon/registry.py:235-375`, uses `extra="ignore"` (line 356).
- Resolution pattern: `registry.get_version(agent_id, version_tag)` → fallback `registry.get_resolved(agent_id)` (`daemon/registry.py:632-833`).
- `lifecycle_hooks` field is adjacent to existing `context_injection` field (line 303).

### Shared Context (verified)

- `resolve_context_dir(context_key)` at `context_tools.py:23-39` — returns `{tempdir}/ensemble/context/{context_key}/`.
- Write pattern from `_save_explorer_result()` (`knowledge_tools.py:524-561`): `dir_path.mkdir(parents=True, exist_ok=True)` → `file_path.write_text(content, encoding="utf-8")`. This pattern is now extracted to `write_context_file()` in `context_tools.py` (see W5).
- Filename: `{slug}_{YYYYMMDD_HHMMSS}_{instance_id[:8]}.md` (C4 — new format with `instance_id[:8]` suffix to prevent same-second collisions and enable the new timestamp regex).
- **Slug derivation (C3):** taken from the report's first `#`-prefixed heading line, OR the first substantive non-boilerplate line of `last_content` (skipping lines starting with `✅`, `Task Complete`, `Skill(s)`, `---`, ```` ``` ````, blank lines, and similar boilerplate). Expanded to ~80 chars via `re.sub(r'[^a-z0-9]+', '-', text).strip('-')[:80]`. Only falls back to `f"child-report-{ctx.instance_id[:8]}"` when no substantive line is found. The slug is matched against filenames (not content) by `_score_context_files` (`context_injection.py:302-316`), so extracting it from the heading is what makes the file discoverable.
- `context_key` = tree-root instance ID, resolved via `_resolve_tree_root_id()` from `context_messages.py:856-897` (preferred — S4) or directly via `instance_repository.get_tree_root_id()`. **W7 fallback policy:** if the helper returns `None`, fall back to `instance_id`; if no `instance_repository` is available, skip the hook entirely (DEBUG-logged).
- Heuristic matching at `context_injection.py:812-929` — token-overlap scoring, threshold 0.10, caps at 50 files by mtime.

## Implementation Approach

### Architecture: Module-Level Registry + Lightweight Dispatcher

```
daemon/services/lifecycle_hooks.py
├── _HOOK_REGISTRY: dict[str, dict[str, Callable]]  # {event_name: {hook_name: async_fn}}
├── register_lifecycle_hook(event, hook_name, fn)    # Registration helper
├── async dispatch_lifecycle_hooks(event, hook_names, context)  # Filtered dispatch entry point
├── async _add_to_shared_context_md_files(context)   # First hook implementation
└── LifecycleHookContext (NamedTuple)                # Data passed to hooks
    ├── instance_id, agent_id, parent_id
    ├── last_content, outcome
    ├── context_key, manager (for repo access)
```

**Design choices:**
1. **Module-level registry** (not a class) — simplest possible, registered at import time. Avoids wiring a new service into InstanceManager. The registry is a plain dict.
2. **Hook functions are async** — `dispatch_lifecycle_hooks()` uses `await`. The integration site wraps the call in `asyncio.wait_for(..., timeout=5.0)` with `try/except Exception` (plus `except asyncio.CancelledError: raise` for cancellation preservation) — NOT bare `asyncio.create_task()`, which has no timeout and risks task-handle GC.
3. **`LifecycleHookContext` NamedTuple** — typed payload passed to every hook. Contains everything hooks need (instance_id, agent_id, parent_id, last_content, outcome, context_key, manager reference).
4. **No DB, no persistence** — hooks are code-level registrations. Config (`lifecycle_hooks` in meta.json, type `dict[str, list[str]]`) selects which named hooks to fire per agent per event.

### Data Flow

```
Child instance completes
→ _process_child_completion_and_notify_parent()
→ asyncio.to_thread(_process_child_completion_db_sync)  [DB commit]
→ _dispatch_post_commit_side_effects(result, last_content, msg_id)
  → [existing side effects: bus, SSE, CompletionRegistry, lifecycle...]
  → IF outcome == "regular_child_completed":       ← ONLY eligible outcome (W6)
      → resolve agent_meta via registry.get_version/get_resolved
      → read lifecycle_hooks = agent_meta.lifecycle_hooks (dict[str, list[str]])
      → hook_names = lifecycle_hooks.get("on_complete", [])   # list
      → IF hook_names:
          → resolve context_key (W7 policy: see below)
          → build LifecycleHookContext(
              instance_id, agent_id, parent_id,
              last_content, outcome, context_key, manager)
          → try:
              → await asyncio.wait_for(
                  dispatch_lifecycle_hooks("on_complete", hook_names, ctx),
                  timeout=5.0)
          → except asyncio.CancelledError:
              → raise                    # W3 — preserve cancellation
          → except Exception as e:
              → log(severity=WARNING, msg=f"lifecycle hook failed: {e}")
  → return
```

**W7 — context_key None/fallback policy (single source of truth):**
1. Try `await asyncio.to_thread(_resolve_tree_root_id, instance_id)` (S4 — preferred, reuse `context_messages.py:856-897` helper).
2. If that returns `None`, fall back to `instance_id` itself.
3. If no `instance_repository` is available at all (manager missing the attribute), set `context_key = None` and **skip the hook dispatch entirely** (DEBUG-logged: "context_key unavailable; hook dispatch skipped").

## Open Questions

1. **Should root/tool-invocation outcomes also trigger `on_complete` hooks?** — Current plan: NO (only `regular_child_completed`). If needed later, add the same dispatch call to those branches. This is a deliberate scope decision, not a technical limitation.

2. **Should the hook-written file include a dedup check?** — `_save_explorer_result()` deduplicates by concise-section token overlap. For child reports, each report is unique (different tasks), so dedup is less critical. Deferred to a future enhancement of the hook function itself.

3. **File naming: should the slug come from the report heading or the agent_id?** — **Resolved (C3):** the slug is derived from the first `#` heading line, OR the first substantive non-boilerplate line of `last_content`, expanded to ~80 chars. This makes topic keywords from the report matchable by `_score_context_files` (which scores by filename slug tokens), so a child whose heading says "Distributed Consensus Algorithms" gets a slug containing `distributed-consensus-algorithms` and sibling agents investigating the same topic will pick the file up via heuristic injection. Only falls back to `child-report-{instance_id[:8]}` when no substantive line is found.

4. **Should hooks be configurable to support synchronous dispatch (await inline) for tests?** — Out of scope. The 5s `asyncio.wait_for` bound is short enough that tests can use a fast `asyncio.sleep(0.001)` hook. If a future test needs true synchronous semantics, expose a `_dispatch_lifecycle_hooks_sync` test-only path.
