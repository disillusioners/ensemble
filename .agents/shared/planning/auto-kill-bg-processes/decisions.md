# Decisions: Auto-Kill Background Processes on Root Instance Completion

> **Revision 2 (2026-07-18)** — incorporates plan review feedback: C1 (CancelledError both await points), C2 (file path), C3 (instance_id wrapper), C4 (tree_ids NameError), C5 (per-child self-cleanup), M2 (sequential), M3 (repo access + to_thread), M5 (field naming).

## Context

When a root instance (parent_id=None) reaches a terminal state, background processes it (and its descendants) spawned keep running. Processes come from two sources: the **proc tool** (managed, has registry) and the **bash tool** (unmanaged, PID is local-only). We need to kill both on root completion and daemon shutdown.

## Investigation-Verified Facts

- `BackgroundProcessManager.cleanup_instance(instance_id)` exists, is **idempotent** (atomic bucket pop under lock), and kills process groups. Already called from `terminate_instance` (instance_lifecycle.py:1461) only.
- Processes are keyed by **individual instance_id**, not root → tree sweep requires `get_tree_ids(root_id)` then `cleanup_instance(iid)` per member.
- `get_tree_ids(root_id)` (InstanceRepository, **SYNC method**, repository.py:246) includes the root in its returned list. Must be wrapped in `asyncio.to_thread()` when called from async context.
- `_dispatch_instance_post_commit_side_effects` (job_feedback_observer.py:2391) receives `instance_id` + `parent_id` and wraps each side-effect in try/except — best hook for COMPLETED/ERROR/FAILED.
- `JobFeedbackObserver` has **NO** `self.repository` attribute. It stores `self._instance_manager`. Repository access pattern (per instance_lifecycle.py:1836): `self._instance_manager._instance_repository.get_tree_ids(...)`.
- No `cleanup_all()` exists on BackgroundProcessManager — needed for daemon shutdown.
- bash tool's `proc.pid` is a coroutine-local variable, registered nowhere → requires a new registry to track by instance_id.
- CancelledError leaks bash subprocesses: (a) the outer `except Exception` at bash.py does NOT catch CancelledError (inherits from BaseException in Python 3.9+, not Exception); (b) there are TWO await points (spawn at 143/152, wait_for at 172) and neither is covered for cancellation.
- `_kill_process` (bash.py:30-58) awaits internally (`await asyncio.wait_for(proc.wait(), timeout=5.0)`) — calling it from a CancelledError handler re-raises CancelledError at that await (sticky cancellation in Python 3.11+), skipping any subsequent cleanup.
- `_cleanup_instance_state` (manager.py:2090) is sync and does NOT touch proc — restructuring it to be async is out of scope.
- `manager.py` is at **`daemon/manager.py`** (NOT `daemon/services/manager.py`). `shutdown()` at line 5536.

---

## D1: Registry Architecture — Separate `BashProcessRegistry`, not extend `BackgroundProcessManager`

**Decision:** Create a new `BashProcessRegistry` singleton in `daemon/tools/bash.py` (or a sibling module), rather than extending `BackgroundProcessManager` to track bash PIDs.

**Rationale:**
| Option | Pros | Cons |
|--------|------|------|
| A. Extend `BackgroundProcessManager` | One registry to drain | Couples two unrelated process lifecycles; proc has rich ProcessInfo (reader/exit/timeout tasks, spill files) while bash only needs PID+PGID; the bookkeeping differs sharply |
| **B. New `BashProcessRegistry`** ✅ | Clean separation; minimal data (`_entries: dict[str, list[BashProcessEntry]]`); bash-specific lifecycle; can be drained independently | Two registries to coordinate at the auto-kill call site |

**Chosen: B.** The proc and bash tools have fundamentally different process models:
- proc = long-lived async background processes with log readers, exit watchers, timeout tasks, spill files
- bash = synchronous blocking subprocesses with no readers, no exit watchers (wait_for handles it), no spill files

Mixing them would force `ProcessInfo` to grow nullable fields or a discriminated union, and `cleanup_instance` would have to branch on process type. A separate registry keeps each clean. The auto-kill call site drains both — that's a few extra lines per hook.

**BashProcessRegistry shape (canonical naming per D4/M5):**
```python
class BashProcessRegistry:
    """Tracks bash-spawned subprocess (pid, pgid) by instance_id."""
    _entries: dict[str, list[BashProcessEntry]]  # ← NOT _handles; NOT set of Process objects
    _lock: asyncio.Lock

    async def register(instance_id, pid, pgid) -> None
    async def unregister(instance_id, pid) -> None   # called after explicit _kill_process
    async def cleanup_instance(instance_id) -> int   # SIGKILL group for each surviving entry
    async def cleanup_all() -> int                   # daemon shutdown sweep
    get_bash_process_registry() -> BashProcessRegistry  # module-level singleton accessor
```

**M5 Resolution:** Field name is `_entries` (dict of lists), NOT `_handles` (dict of sets). Rationale: D4 requires eager `(pid, pgid)` capture, which needs the `BashProcessEntry` tuple, which requires a list (sets can't hold mutable tuples without frozen dataclass). Updated consistently across all docs.

---

## D2: Hook Site — `_dispatch_instance_post_commit_side_effects` (single converged dispatcher)

**Decision:** Add proc/bash auto-kill as new steps inside `_dispatch_instance_post_commit_side_effects` (job_feedback_observer.py:2391). See D3 for the gating logic (always-fire self-cleanup + root-gated tree sweep).

**Rationale:**
- This is the **single converged post-commit dispatcher** for the COMPLETED/ERROR/FAILED paths.
- It already receives `instance_id`, `parent_id`, `terminal_status`, `agent_id`.
- Each side-effect is in its own try/except — best-effort semantics already established.
- It fires **after the DB commit**, so killing processes never blocks or corrupts the terminal transition.

**Alternative considered: Wire each of the 3 missing terminal paths separately.**
Rejected — the three paths (child completion, error report, finalize) have different call shapes and duplicating the cleanup logic 3× risks drift. `_dispatch_instance_post_commit_side_effects` is the one place all 3 converge for post-commit side effects. (Note: `terminate_instance` already does its own proc cleanup at 1461, so it remains untouched.)

**Repository access (M3 fix):** `JobFeedbackObserver` has NO `self.repository`. Use:
```python
instance_repository = getattr(self._instance_manager, "_instance_repository", None)
if instance_repository is not None:
    tree_ids = await asyncio.to_thread(instance_repository.get_tree_ids, instance_id)
```
- `get_tree_ids` is a SYNC method (repository.py) — wrap in `asyncio.to_thread()` to avoid blocking the event loop.
- Use `getattr` with default `None` for defensive access; log WARNING and skip if repository unavailable.

---

## D3: Two-Tier Cleanup — Always-Fire Self-Cleanup + Root-Gated Tree Sweep (REVISED per C5)

**Decision:** Inside `_dispatch_instance_post_commit_side_effects`, cleanup fires in TWO tiers:

1. **Tier 1 — Always (any terminal instance, regardless of parent_id):** Call `cleanup_instance(instance_id)` for THIS instance on both registries. This closes the per-child leak window (a child that COMPLETED leaks its own procs until the root finalizes).

2. **Tier 2 — Root-gated (only when parent_id is None):** Call `get_tree_ids(instance_id)` then `cleanup_instance(iid)` for every OTHER tree member (skip the root itself — already cleaned in Tier 1). This sweeps descendants' processes.

**Why this change (C5):** The original design (root-gated-only) left a child's background processes running for hours if the root kept running. Tier 1 ensures every terminal instance cleans up its own processes immediately.

**Implementation pattern:**
```python
# Tier 1: ALWAYS clean this instance's own processes (regardless of parent_id)
try:
    proc_mgr = get_background_process_manager()
    await proc_mgr.cleanup_instance(instance_id)
except Exception as e:
    logger.warning(f"Tier-1 proc cleanup failed for {instance_id[:8]}: {type(e).__name__}: {e}")
try:
    bash_reg = get_bash_process_registry()
    await bash_reg.cleanup_instance(instance_id)
except Exception as e:
    logger.warning(f"Tier-1 bash cleanup failed for {instance_id[:8]}: {type(e).__name__}: {e}")

# Tier 2: Root-gated tree sweep for DESCENDANTS (skip root — already cleaned in Tier 1)
tree_ids: list[str] = []  # ← initialized OUTSIDE try (C4 fix — prevents NameError)
if parent_id is None:
    try:
        instance_repository = getattr(self._instance_manager, "_instance_repository", None)
        if instance_repository is not None:
            tree_ids = await asyncio.to_thread(instance_repository.get_tree_ids, instance_id)
    except Exception as e:
        logger.warning(f"Tier-2 get_tree_ids failed for root {instance_id[:8]}: {type(e).__name__}: {e}")
    # Phase 2 adds bash sweep here too
    for iid in tree_ids:
        if iid == instance_id:
            continue  # already cleaned in Tier 1
        try:
            await proc_mgr.cleanup_instance(iid)
        except Exception as e:
            logger.warning(f"Tier-2 proc cleanup failed for {iid[:8]}: {type(e).__name__}: {e}")
```

**Edge cases:**
- **Child COMPLETED with parent still running:** Tier 1 cleans the child's own procs immediately. Tier 2 skipped (parent_id != None). Root's procs untouched. ✓
- **Root COMPLETED:** Tier 1 cleans root's own procs. Tier 2 sweeps descendants (get_tree_ids includes root, but we `continue` on root since Tier 1 handled it). ✓
- **`get_tree_ids` raises:** `tree_ids` stays `[]` (initialized outside try). Tier 2 loop is a no-op. Tier 1 already ran. Other side-effects still fire. ✓ (C4 fixed)
- **Double-fire (terminate cascade + finalize):** Both tiers are idempotent (atomic bucket pop). No-op second time. ✓

---

## D4: Bash PGID Capture — Store `(pid, pgid)` eagerly at register time

**Decision:** `BashProcessRegistry` stores `BashProcessEntry(pid, pgid)` per instance, captured eagerly at spawn time. NOT live `asyncio.subprocess.Process` references.

**Rationale:**
- `os.getpgid(pid)` raises `ProcessLookupError` if the process has already exited by cleanup time.
- bash uses `start_new_session=True` (bash.py:112) on Unix → PGID == PID at spawn, but capturing explicitly is robust against future changes.
- Storing integers avoids holding Process references (which may keep resources alive) and avoids `getpgid` races at cleanup.
- SIGKILL via `os.killpg(pgid, SIGKILL)` works even if the process has exited (killpg on an empty/missing group is a benign no-op or ProcessLookupError — both swallowed by best-effort try/except).

**Registry entry shape:**
```python
@dataclass
class BashProcessEntry:
    pid: int
    pgid: int

_entries: dict[str, list[BashProcessEntry]]  # canonical naming (M5)
```

**Windows:** `start_new_session` is Unix-only. On Windows, no process group exists — fall back to `taskkill /F /T /PID`. Keep the `sys.platform != "win32"` guard consistent with proc_tools.py:636 and bash.py:112.

---

## D5: Bash Drain — Register always, unregister only after explicit `_kill_process`

**Decision:** In `bash()`, call `registry.register(instance_id, pid, pgid)` immediately after spawn (always). Call `registry.unregister(instance_id, pid)` ONLY after explicit `_kill_process` (timeout path and the new CancelledError path). Do NOT unregister on normal foreground completion.

**Rationale:**
- A bash invocation that completes normally has no surviving foreground subprocess, BUT backgrounded grandchildren (`sleep 3600 &`, `nohup foo &`) survive in the process group even though the direct child exited.
- The registry's job is to kill the process *group*, which includes backgrounded grandchildren.
- The foreground child exiting does not imply the group is empty — so we keep the entry until the owning instance finalizes (Tier 1/Tier 2 cleanup).
- On timeout/cancellation, `_kill_process` already SIGKILLs the group — so the entry is dead and can be unregistered.

---

## D6: Daemon Shutdown — Add `cleanup_all()` to both registries, call from `manager.shutdown()`

**Decision:**
- Add `BackgroundProcessManager.cleanup_all()` → iterate all instance buckets, calling `cleanup_instance(iid)` for each.
- Add `BashProcessRegistry.cleanup_all()` → iterate all instance buckets, calling `cleanup_instance(iid)` for each.
- Wire both into `manager.shutdown()` (daemon/manager.py:5536) as a new best-effort step early in the sequence (before worker pool shutdown).

**Rationale:**
- Shutdown must sweep ALL processes regardless of tree ownership — the daemon is going away.
- Both `cleanup_all()` methods are idempotent (each `cleanup_instance` pops a bucket).
- Best-effort: wrap in try/except, never block shutdown.
- Order: kill processes early in shutdown, before worker pool/event bus teardown, so in-flight tasks don't re-spawn or hold process references.

---

## D7: Phase 1 Keeps Scope Tight — proc only, reuses existing primitive

**Decision:** Phase 1 ships proc auto-kill with Tier 1 + Tier 2 (proc only), `cleanup_all()`, and `manager.shutdown()` wiring. No bash registry, no bash changes, no CancelledError fix.

**No new registry, no bash changes, no CancelledError fix.** This validates the hook points and the two-tier tree-sweep pattern before Phase 2 adds the bash registry and fixes the CancelledError leak.

**Rationale:** Lower risk, faster feedback, clear blast radius. If the hook site has issues (e.g., tree-sweep races, repository access), we discover them before entangling bash.

---

## D8: Best-Effort Semantics — Mirror `instance_lifecycle.py:1457-1467`

**Decision:** Every new cleanup call site wraps in try/except that logs a WARNING and continues. Mirror the exact pattern from `terminate_instance`:

```python
try:
    await get_background_process_manager().cleanup_instance(instance_id)
except Exception as e:
    logger.warning(
        f"proc cleanup failed for {instance_id[:8]}: "
        f"{type(e).__name__}: {e}"
    )
```

**Rationale:** Process cleanup must never block a terminal transition (user-visible) or daemon shutdown. All cleanup is best-effort by design.

---

## D9: Instance Availability — Pass `instance_id` Through, Don't Re-fetch

**Decision:** `_dispatch_instance_post_commit_side_effects` already receives `instance_id` as a parameter. Use it directly for cleanup. Do NOT re-fetch the instance from DB (it's already terminal; re-fetch adds a race and a round trip).

---

## D10: bash() instance_id Plumbing — `_make_instance_id_aware` wrapper mirroring `_make_workdir_aware` (NEW per C3)

**Decision:** Thread `instance_id` into the bash tool via a new `_make_instance_id_aware(tool, get_default_instance_id)` wrapper that mirrors the existing `_make_workdir_aware` (instance.py:406-483). Compose both wrappers at instance.py:953.

**Why NOT a raw param on `bash()`:** `bash` is a module-level `@tool` (bash.py:73-74) that becomes a `StructuredTool` whose `args_schema` is LLM-visible. Adding `instance_id` to the raw signature would surface it to the LLM as a fillable argument (bad — the model shouldn't control instance scoping).

**Implementation (3 steps):**

1. **Add `instance_id: str | None = None` kwarg to `bash()`** (bash.py:74). Callers without the wrapper still work (defaults to None). The `@tool` decorator exposes `command`, `timeout`, `workdir`, `input` to the LLM — the extra `instance_id` kwarg is accepted by the function but NOT advertised (LangChain's `@tool` only exposes the documented args from the docstring/typing unless explicitly configured). **Verify** the `@tool` decorator doesn't auto-expose `instance_id` in args_schema; if it does, suppress it via `args_schema` override or exclude pattern.

2. **Define `_make_instance_id_aware(tool, get_default_instance_id)`** in `daemon/tools/instance.py`, mirroring `_make_workdir_aware` (instance.py:406-483). The wrapper injects `instance_id` kwarg when it's None, using the closure `get_default_instance_id`.

3. **Compose at instance.py:953:**
```python
# instance.py:547 — current_instance_id already in scope
get_current_instance_id = lambda: current_instance_id  # trivial closure

# instance.py:953 — compose both wrappers
bash_aware = _make_instance_id_aware(
    _make_workdir_aware(bash, get_current_workdir),
    get_current_instance_id,
)
```
Order matters: workdir-aware wraps first (it fills `workdir`), then instance_id-aware wraps the result (it fills `instance_id`). Both operate on kwargs independently.

4. **Fallback at runtime (bash.py):** If `instance_id is None` when bash() runs, log WARNING and skip registry registration (do NOT register under a sentinel like "unknown" — that would create an unkillable bucket).

**Why this is better than a context var:** The closure pattern already exists (`get_current_workdir`, `create_instance_tools(manager, current_instance_id, ...)` at instance.py:547). Mirroring it is consistent and testable. `current_instance_id` is already captured in scope at the factory — no new plumbing needed upstream.

---

## D11: CancelledError Fix — Cover BOTH await points + uncancel/shield (NEW per C1)

**Decision:** Rework the bash() try/except structure to:
1. Wrap the ENTIRE spawn+wait section in a `try/except asyncio.CancelledError/finally` that destroys any spawned `proc`.
2. In the CancelledError handler, call `task.uncancel()` (Python 3.11+) before `_kill_process` to prevent sticky cancellation from re-raising at `_kill_process`'s internal await. Fall back to `asyncio.shield()` on older Python.
3. Initialize `proc = None` before the try block so the handler can guard with `if proc is not None`.

**The two await points and why both leak:**
| Await point | Line | What leaks on CancelledError |
|-------------|------|------------------------------|
| `await asyncio.create_subprocess_exec/shell(...)` | bash.py:143/152 | The just-spawned subprocess (spawn may complete before cancellation propagates, leaving `proc` assigned but untracked). |
| `await asyncio.wait_for(proc.wait(), ...)` | bash.py:172 | The subprocess + its process group. |

**Why the current code doesn't catch CancelledError:**
- The outer `except Exception` (bash.py ~209) does NOT catch `asyncio.CancelledError` — in Python 3.9+, `CancelledError` inherits from `BaseException`, not `Exception`. So it propagates unhandled.
- The inner `except asyncio.TimeoutError` (bash.py:175) only catches timeouts.
- The function-level `finally` (bash.py:211-226) only closes temp files — it has no reference to `proc`.

**Why `_kill_process` can't be called naively from a CancelledError handler:**
- `_kill_process` does `await asyncio.wait_for(proc.wait(), timeout=5.0)` (bash.py:47).
- In Python 3.11+, cancellation is "sticky" — once a task is cancelled, the next `await` re-raises `CancelledError`. So `_kill_process`'s internal await would re-raise, skipping the `unregister` call after it.
- `task.uncancel()` decrements the cancellation count to 0, allowing the awaits in `_kill_process` and `unregister` to proceed. We then `raise` manually to re-propagate the original cancellation.

**Implementation pattern (bash.py):**
```python
proc: asyncio.subprocess.Process | None = None  # ← sentinel; guard in handler
try:
    # ... temp file setup (unchanged) ...
    try:
        # SPAWN (await point 1)
        proc = await asyncio.create_subprocess_exec(...)  # or _shell
    finally:
        stdout_file.close(); stderr_file.close()
        if stdin_file is not None: stdin_file.close()

    # REGISTER after spawn (always)
    if instance_id is not None:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = proc.pid  # start_new_session=True → PGID == PID
        await get_bash_process_registry().register(instance_id, proc.pid, pgid)

    # WAIT (await point 2)
    actual_timeout = None if timeout == 0 else timeout
    try:
        await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
        timed_out = False
    except asyncio.TimeoutError:
        await _kill_process(proc)
        await get_bash_process_registry().unregister(instance_id, proc.pid)
        timed_out = True

    # ... read output, build content (unchanged) ...
    return content

except asyncio.CancelledError:
    # Cancellation at EITHER await point (spawn or wait_for).
    # Clean up the subprocess if it was spawned, then re-propagate.
    if proc is not None:
        # Clear sticky cancellation so _kill_process's internal awaits
        # don't immediately re-raise CancelledError (Python 3.11+).
        task = asyncio.current_task()
        if task is not None and hasattr(task, "uncancel"):
            task.uncancel()
        try:
            await asyncio.shield(_kill_process(proc))
            if instance_id is not None:
                await asyncio.shield(
                    get_bash_process_registry().unregister(instance_id, proc.pid)
                )
        except Exception:
            pass  # best-effort during cancellation
    raise  # ALWAYS re-propagate the cancellation

except Exception as e:
    return f"ERROR: {str(e)}"
finally:
    # temp file cleanup (unchanged)
    ...
```

**Notes:**
- `task.uncancel()` requires Python 3.11+. Guard with `hasattr(task, "uncancel")`. On older Python, rely on `asyncio.shield()` alone (shield prevents the inner coroutine from seeing the cancellation, though the outer task remains cancelled).
- `asyncio.shield()` is belt-and-suspenders alongside `uncancel()` — it protects `_kill_process`'s awaits from any concurrent cancellation re-entry.
- The handler is placed BEFORE `except Exception` so CancelledError (BaseException) is caught specifically, not swallowed by a broad handler.
- If spawn itself was cancelled (proc is None), the handler skips `_kill_process` and just re-raises. No subprocess to clean (spawn didn't complete).

---

## D12: Phasing — Strictly Sequential (REVISED per M2)

**Decision:** Phase 1 → Phase 2 → Phase 3 are **strictly sequential**. No parallel work, no pipeline overlap.

**Why (M2):** Phase 1 and Phase 2 edit the SAME LINES of the SAME FUNCTIONS:
- `manager.shutdown()` step list (daemon/manager.py:5536) — Phase 1 adds proc `cleanup_all()`; Phase 2 adds bash `cleanup_all()` immediately after.
- `_dispatch_instance_post_commit_side_effects` (job_feedback_observer.py:2391) — Phase 1 adds the two-tier proc sweep; Phase 2 adds the bash sweep within the same blocks.

Parallel work would cause merge conflicts on every PR. Sequential development avoids this entirely. Phase 2 starts only after Phase 1 merges.

**Scheduling:** Phase 1 (merge) → Phase 2 (merge) → Phase 3 (merge). No exceptions.

---

## Summary Table

| ID | Decision | Phase | Review Item |
|----|----------|-------|-------------|
| D1 | New `BashProcessRegistry` singleton, `_entries: dict[str, list[BashProcessEntry]]` | 2 | M5 |
| D2 | Hook: `_dispatch_instance_post_commit_side_effects`; repo via `self._instance_manager._instance_repository` + `to_thread` | 1 | M3 |
| D3 | Two-tier: always-fire self-cleanup (Tier 1) + root-gated descendant sweep (Tier 2) | 1 | C5 |
| D4 | Bash registry stores `BashProcessEntry(pid, pgid)`, capture at spawn | 2 | — |
| D5 | bash register always; unregister only after explicit `_kill_process` | 2 | — |
| D6 | Add `cleanup_all()` to both registries; wire into `manager.shutdown()` | 1+2 | — |
| D7 | Phase 1 = proc only, validates hooks before bash | 1 | — |
| D8 | Best-effort try/except everywhere | 1+2 | — |
| D9 | Use instance_id from dispatcher params, no DB re-fetch | 1+2 | — |
| D10 | `_make_instance_id_aware` wrapper mirrors `_make_workdir_aware`; instance_id kwarg hidden from LLM | 2 | C3 |
| D11 | CancelledError fix: cover both await points, `proc=None` sentinel, `uncancel()`+`shield` in handler | 2 | C1 |
| D12 | Phases strictly sequential (same lines/functions) | — | M2 |
