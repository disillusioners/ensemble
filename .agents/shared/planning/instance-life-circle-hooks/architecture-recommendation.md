# Architecture Recommendation: Instance Lifecycle Hooks

**Date:** 2026-08-08  
**Architect Instance:** architect (controller)  
**Worker Instances:** architect-worker-structural (structural-design), architect-worker-resilience (resilience-design), architect-worker-dataflow (data-flow-design)  
**Status:** Complete  
**Confidence:** High  

---

## Executive Summary

The plan is **architecturally sound** at its core: Observer pattern, event-keyed registry, per-hook error isolation, and insertion-point-after-all-critical-side-effects are all correct choices. However, **six concrete changes are needed before implementation** — four are high-priority (two 🔴, two 🟡), two are medium-priority improvements. None change the fundamental design; they harden it against known failure modes already present in the codebase.

The single most important finding: **the heuristic matcher scores files by slug tokens extracted from the filename, NOT by file content.** The plan's slug derivation strategy must be adjusted accordingly, or hook-written files will be invisible to sibling agents.

---

## Approach Comparison

This was a multi-dimensional analysis (not competitive fan-out). Three workers analyzed different design dimensions independently:

| Dimension | Worker | Skill | Key Verdict |
|-----------|--------|-------|-------------|
| Registry pattern + schema (Q1+Q4) | structural | structural-design | Sound, with schema type fix needed |
| Integration safety + crash recovery (Q2+Q5) | resilience | resilience-design | Convergence point correct; fire-and-forget needs hardening |
| Write path + data flow (Q3) | dataflow | data-flow-design | Third-copy problem; slug-as-matching-signal is critical |

---

## Findings by Focus Area

### Q1: Hook Registry Design — ✅ Sound, with class-wrapper improvement

**Verdict:** The Observer pattern with an event-keyed dict-of-dicts registry is the right structural choice. It matches the existing `CompletionRegistry` precedent (`get_completion_registry()` singleton accessed at module level).

**What works:**
- Insertion-order iteration (Python 3.7+ dict semantics) gives deterministic hook ordering without an explicit priority field
- Per-hook `try/except` matches the established pattern in `_dispatch_post_commit_side_effects` — every existing side effect (SSE, CompletionRegistry, lifecycle event, title gen) is already independently isolated
- Async dispatch composes naturally at the same call site

**Improvement (🟡): Wrap in a class.** Replace the bare module-level `_HOOK_REGISTRY` dict with `class LifecycleHookRegistry` accessed via `get_lifecycle_hook_registry()`. This mirrors `CompletionRegistry` exactly — same complexity, but yields:
- A testable seam (tests can mock `get_lifecycle_hook_registry()` instead of clearing a module global)
- An explicit contract (methods: `register()`, `dispatch()`, `_hooks` attribute)
- Swappable for future DB-backed or remote registries without changing call sites

**Alternatives rejected:**
- **Signal/slot (blinker):** Adds an external dependency for a single hook — overkill
- **EventBus pub-sub:** Overlaps with existing `_publish_instance_lifecycle_event` (manager.py:6636); different use case (system-wide bus vs agent-configured hooks)
- **Protocol-based plugin system:** Over-engineered for one consumer

### Q2: Integration Point Safety — ✅ Correct convergence point, scope is intentional

**Verdict:** `_dispatch_post_commit_side_effects()` is the correct and only convergence point for `on_complete` hooks. The method is reached **only** via `_process_child_completion_and_notify_parent()`, which fires **only** for normal completion-reportable terminal states.

**Critical scope clarification:** ERROR, CANCELLED, and WATCHOVER-TERMINATED completions **never enter this method**. They route through `ErrorReportingService._send_error_report` → `child_reports._emit_terminal_via_bus(status="error")` and the watchover termination path respectively. Gating to `regular_child_completed` is not an artificial narrowing — it is the natural shape of the method. This is the correct default for `on_complete` semantics.

**Outcome coverage map (verified by tracing the method):**

| Outcome | Reaches hook? | Should it? | Risk |
|---------|--------------|------------|------|
| `regular_child_completed` | ✅ YES | ✅ YES | 🟢 Low |
| `root_completed` | ❌ Early return (L2678) | Out of scope (per plan) | None |
| `tool_invocation_completed` | ❌ Early return (L2710) | Out of scope (per plan) | None |
| `idempotency_skip` | ❌ Early return (L2682) | ❌ No — would double-write on retry | 🟡 if it leaked |
| `deferred_waiting_children` | ❌ Early return (L2616) | ❌ No (child not done) | None |
| `instance_not_found` | ❌ Early return (L2887) | ❌ No | None |

**Documentation requirement:** The plan must explicitly state that error/cancelled/watchover-terminated paths are not eligible for `on_complete` hooks in this phase. If they should be eligible later, separate dispatch calls must be added to `ErrorReportingService` and the watchover termination path.

### Q3: Shared Context Write Path — 🔴 Critical discovery + extract recommendation

**🔴 CRITICAL: The heuristic matcher scores by SLUG, not by content.**  
`_score_context_files` (context_injection.py:302-316) tokenizes the slug extracted from the **filename** (`_extract_slug_from_filename`), NOT the file content. File content is only used for display previews and section extraction. **This means the slug quality directly determines whether a sibling agent ever sees the file.** If the slug doesn't contain keywords a sibling would query, the file is invisible.

**Implication for slug strategy:** Content-derived slug is correct (it captures task topic), but:
- Derive from the report's first heading or first substantive line — NOT boilerplate like "task complete" or "skill applied"
- Expand to full ~80 chars to maximize token coverage
- Consider prefixing with agent_id for `list_context` discoverability (though agent_id alone has poor recall against most queries)

**🔴 Filename collision:** `{slug}_{YYYYMMDD_HHMMSS}.md` truncates to seconds. Two children completing in the same second with similar content → identical slug + identical timestamp → **silent file overwrite (data loss)**. The plan's Phase 2 risk notes this and suggests appending `instance_id[:8]`. **This is necessary, not optional.** But the slug parser regex (`_extract_slug_from_filename`) strips `_\d{8}_\d{6}\.md$` — appending `_iid8` after the timestamp would break it. **The regex must be updated** to also strip `_[a-f0-9]{8}` suffix.

**Improvement (🟡): Extract `write_context_file()` to `context_tools.py`.** The codebase already has two divergent copies of the write pattern:
- `_save_explorer_result` (knowledge_tools.py:524) — hardcodes path, uses dedup, slug from query
- `_save_experience_result` (knowledge_tools.py:598) — hardcodes path, uses dedup, slug from text

The proposed hook would be a **third copy** — but the first to use the canonical `resolve_context_dir()`. Extracting a ~15-line `write_context_file()` utility into `context_tools.py` (the canonical home for context-dir operations) eliminates the third-copy problem and unifies all three writers. Dedup stays caller-specific.

```python
# context_tools.py — proposed addition
def write_context_file(
    context_key: str,
    content: str,
    slug: str,
    suffix: str = ".md",
) -> Path | None:
    dir_path = resolve_context_dir(context_key)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = dir_path / f"{slug}_{timestamp}{suffix}"
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path
```

### Q4: Meta.json Schema Evolution — 🔴 Naming fix + 🟡 type improvement

**🔴 NAMING: `life_circle` is a typo of `lifecycle_hooks`.** This will propagate to:
- The Pydantic field name (`registry.py`)
- JSON keys in `agents/wanderer/meta.json`
- Registry lookup keys
- The plan directory name (`instance-life-circle-hooks`)
- Likely UI labels

**Fix at the schema level before any code is written.** Rename:
- Plan directory: `instance-life-circle-hooks` → `instance-lifecycle-hooks`
- Pydantic field: `life_circle` → `lifecycle_hooks`
- meta.json key: `"life_circle"` → `"lifecycle_hooks"`

**🟡 TYPE: `dict[str, str]` should be `dict[str, list[str]]`.** The proposed `dict[str, str]` (event → single hook name) double-bakes a single-hook constraint into both the config schema and the registry shape. Changing to `dict[str, list[str]]` (event → list of hook names):
- Supports multiple hooks per event from day one (zero-cost: Wanderer uses a 1-element list)
- Makes config and registry shapes symmetric
- Absorbs 6/6 future extensibility requirements without architectural rewrite (new events, multiple hooks, priority, conditional hooks, parameterization, per-project overrides)

Wanderer config becomes: `{"on_complete": ["add_to_shared_context_md_files"]}`

**Versioning interaction:** No risk 🟢. String-based hook name lookup happens after `registry.get_version()` resolves the AgentMetadata. The hook function registry is loaded once at daemon startup. The string ref cleanly resolves to a callable regardless of which meta.json version was used.

### Q5: Checkpoint & Crash Safety — ✅ Acceptable gaps, with hardening

**Crash between DB commit and hook execution:** The child is marked DONE in the DB and the parent is notified via the bus (committed in the same transaction). The hook-written context file is never written. On restart, `StaleTaskRecovery` does not re-run post-commit side effects for already-completed instances. **The file is silently lost.** This is acceptable because the context directory is tempdir-based and ephemeral — files vanish on restart regardless. Missing files on restart is indistinguishable from "no children have completed since restart."

**Cross-restart visibility:** The real cost of tempdir-based storage is that cross-restart sibling-context visibility is impossible by construction. If Wanderer spawns coders, coder A finishes (hook writes file), daemon crashes, on restart coder B finishes — coder A's report file is gone (tempdir wiped). Sibling agents get an incomplete view. **The plan should explicitly document this as "best-effort within a single process lifetime."**

**Partial writes:** `write_text()` is a single syscall on POSIX, but on crash mid-write a partial `.md` remains. The heuristic matcher would produce a low-score match (truncated content) that simply isn't injected — not harmful, but consumes one of the 50-file mtime cap slots. **Recommend atomic write pattern:** write to `{file_path}.tmp` → `os.replace()` for POSIX-atomic rename. Adds 3 lines.

**Idempotency:** The method is naturally idempotent at the task level (`idempotency_skip` outcome catches duplicate report_id), so the hook fires once per report. The 50-file mtime cap is the backstop for accumulation.

---

## Concrete Recommendations (Prioritized)

### 🔴 Must-Fix Before Implementation

| # | Change | Rationale | Effort |
|---|--------|-----------|--------|
| 1 | **Rename `life_circle` → `lifecycle_hooks`** everywhere (field, meta.json, plan dir) | Typo propagation is irreversible once code ships; meta.json keys become user-facing | 1 commit |
| 2 | **Update slug derivation to be matching-aware** | Slug is the SOLE matching signal; boilerplate slugs = invisible files. Derive from report heading/substantive first line | Phase 2 task |
| 3 | **Append `instance_id[:8]` to filename + update `_extract_slug_from_filename` regex** | Same-second collision = silent data loss. The slug parser regex must strip the new suffix or matching breaks | Phase 2 task |

### 🟡 Should-Fix Before Implementation

| # | Change | Rationale | Effort |
|---|--------|-----------|--------|
| 4 | **Change field type `dict[str, str]` → `dict[str, list[str]]`** | Single-hook constraint double-baked; `list[str]` absorbs all future extensibility at zero cost | 1 line |
| 5 | **Replace bare `asyncio.create_task` with `asyncio.wait_for(dispatch_lifecycle_hooks(...), timeout=5.0)` + try/except** | Plan's own Phase 4 risk row recommends this but Phase 4 task #2 still uses bare `create_task`. Fully deterministic, no GC risk, no orphan task | Phase 4 task |
| 6 | **Use `except Exception` (not `except BaseException`) in the hook dispatcher** | Codebase has a known bug class where `except BaseException` swallows `CancelledError`, breaking shutdown. CPython 3.13+ promotes `CancelledError` to `BaseException` | Phase 1 task |

### 🟢 Improvements (Recommended, Low-Cost)

| # | Change | Rationale | Effort |
|---|--------|-----------|--------|
| 7 | **Wrap registry in `LifecycleHookRegistry` class** with `get_lifecycle_hook_registry()` accessor | Mirrors `CompletionRegistry`; same complexity, testable seam | Phase 1 |
| 8 | **Extract `write_context_file()` to `context_tools.py`** | Eliminates third-copy of write pattern; unifies all context-dir writers | ~15 lines |
| 9 | **Use atomic write pattern** (`.tmp` → `os.replace()`) in hook function | Eliminates partial-file pollution | 3 lines |
| 10 | **Wrap file I/O in `asyncio.to_thread()`** inside the hook | Sync `mkdir` + `write_text` on slow FS blocks the event loop | 1 line |

### 📋 Documentation Additions to Plan

| # | Addition |
|---|----------|
| D1 | State explicitly that error/cancelled/watchover-terminated children are not eligible for `on_complete` hooks (they bypass `_dispatch_post_commit_side_effects` entirely) |
| D2 | Document that context files are best-effort within a single process lifetime — tempdir is wiped on restart |
| D3 | Document the boundary between `_publish_instance_lifecycle_event` (system-wide bus) and `dispatch_lifecycle_hooks` (agent-configured, context-rich) |
| D4 | Document the `HookRef` migration path: when parameter support is needed, change `list[str]` → `list[str | HookRef]` where `HookRef = str | HookConfig(name, params)` |

---

## Risks

| # | Risk | Severity | Status |
|---|------|----------|--------|
| R1 | `life_circle` typo propagates to user-facing meta.json keys | 🔴 | **Fix before implementation** |
| R2 | Slug derived from boilerplate content = files invisible to heuristic matcher | 🔴 | **Fix in Phase 2** |
| R3 | Same-second filename collision = silent data loss | 🔴 | **Fix in Phase 2** (append iid8) |
| R4 | `dict[str, str]` config type blocks multiple hooks per event | 🟡 | **Fix before implementation** (→ `dict[str, list[str]]`) |
| R5 | Bare `asyncio.create_task` GC risk + plan/implementation mismatch | 🟡 | **Fix in Phase 4** |
| R6 | `except BaseException` swallows CancelledError on shutdown | 🟡 | **Fix in Phase 1** |
| R7 | Third copy of write pattern creates maintenance trap | 🟡 | **Extract `write_context_file()`** |
| R8 | 50-file mtime cap silently evicts child report findings | 🟡 | **Track for future** — acceptable for start-small |
| R9 | Cross-restart cache loss invisible to operators | 🟡 | **Document** — tempdir is ephemeral by design |
| R10 | Sync filesystem I/O blocks event loop | 🟢 | **Wrap in `asyncio.to_thread()`** |
| R11 | Error/cancelled completions bypass hooks | 🟢 | **Document as scope decision** |

---

## Decisions Pending (For Leader)

1. **Rename the plan directory** `instance-life-circle-hooks` → `instance-lifecycle-hooks`? (Recommended — yes. The directory has already been created with the typo name, but no code references it yet.)

2. **Extract `write_context_file()` to `context_tools.py` now, or defer?** (Recommended — do it now. ~15 lines, eliminates the third-copy problem, and the developer implementing this will touch `context_tools.py` anyway via `resolve_context_dir()`.)

3. **Accept tempdir-based ephemeral cache as the permanent design, or plan a future persistent store?** (For start-small: accept it. Document the limitation. If cross-restart sibling visibility becomes a requirement, the context directory would need to move to a persistent location — a larger change outside this feature's scope.)

---

## Open Questions

1. **Should the slug parser regex be updated to handle the `_iid8` suffix, or should the instance_id be embedded within the slug portion?** The cleanest approach is appending `_iid8` after the timestamp and updating `_extract_slug_from_filename` to strip `_([a-f0-9]{8})` suffix. But this touches shared code in `context_injection.py` that the explorer and experience writers also depend on. Verify the regex change doesn't break existing context file matching.

2. **Should `on_complete` hooks eventually fire for root completions and tool-invocation completions?** The plan defers this. If yes, the same dispatch call must be added to those branches (lines 2678 and 2710). No architecture change needed — just additional dispatch calls.

3. **How will the 50-file mtime cap interact with long-running Wanderer investigations?** A deep investigation spawning 10+ children could exhaust the cap and silently evict earlier findings. Track whether the cap needs raising for hook-written files vs. explorer-written files.
