# Phase 2: Bash PID Registry + CancelledError Leak Fix

> **Revision 2 (2026-07-18)** — incorporates review items C1 (CancelledError both await points + uncancel/shield), C2 (path), C3 (instance_id wrapper), M2 (sequential), M3 (repo access), M5 (field naming). **Must start only after Phase 1 merges** (D12).

## Objective

Create a `BashProcessRegistry` to track bash-spawned subprocess `(pid, pgid)` by instance_id, thread `instance_id` into `bash()` via a new `_make_instance_id_aware` wrapper (mirroring `_make_workdir_aware`), wire the registry into the same two-tier hook points Phase 1 established, and fix the CancelledError leak in `bash()` that spans BOTH await points (spawn + wait_for).

## Coupling

- **Depends on:** Phase 1 **merged** (D12 — strict sequential; same lines/functions)
- **Coupling type:** **tight** — Phase 2 edits the SAME lines of the SAME FUNCTIONS Phase 1 added. Specifically:
  - `_dispatch_instance_post_commit_side_effects` — Phase 2 ADDS bash `cleanup_instance` calls adjacent to Phase 1's proc calls, inside the SAME Tier 1 and Tier 2 blocks.
  - `daemon/manager.py shutdown()` — Phase 2 ADDS bash `cleanup_all()` adjacent to Phase 1's proc `cleanup_all()`.
- **Shared files with other phases:** `job_feedback_observer.py`, `daemon/manager.py`, `bash.py` (untouched by Phase 1/3), `instance.py` (wrapper composition)
- **Why strict sequential:** Parallel work would cause merge conflicts on every PR (M2). Phase 2 starts only after Phase 1 merges.

## Context

- **Verified gap:** bash tool's `proc.pid` is a coroutine-local variable, registered nowhere (bash.py:143/152). No registry, no dict, no singleton.
- **Verified gap:** `bash()` signature is `async def bash(command, timeout=1800, workdir=None, input=None)` — NO `instance_id`. It's a module-level `@tool` (bash.py:73-74). `current_instance_id` IS already in scope at `create_instance_tools` (instance.py:547) and `get_current_workdir` closure (instance.py:562) — mirror this for instance_id.
- **Verified bug (C1):** Two await points leak on CancelledError:
  - `await asyncio.create_subprocess_exec/shell(...)` at bash.py:143/152 — cancellation HERE leaks the just-spawned proc.
  - `await asyncio.wait_for(proc.wait(), ...)` at bash.py:172 — covered by the original (incomplete) fix.
  - The outer `except Exception` (bash.py:209) does NOT catch CancelledError (inherits BaseException in Python 3.9+). The function-level `finally` only handles temp files, never `proc`.
  - `_kill_process` (bash.py:30-58) does `await asyncio.wait_for(proc.wait(), timeout=5.0)` at line 47 — calling it from a CancelledError handler re-raises CancelledError at that await (sticky cancellation in Python 3.11+), skipping any subsequent `unregister`.
- **Verified behavior:** On normal completion, backgrounded grandchildren (`sleep 3600 &`, `nohup foo &`) survive in the process group (because of `start_new_session=True` at bash.py:112), so `killpg` reaches them — but only if we DON'T unregister on normal foreground completion (D5).
- **Hook points from Phase 1:** Tier 1 (always) + Tier 2 (root-gated) inside `_dispatch_instance_post_commit_side_effects` (job_feedback_observer.py:2391); `manager.shutdown()` (daemon/manager.py:5536).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `BashProcessRegistry` class + singleton | New class in `daemon/tools/bash.py` (top of file, before `_kill_process`). Stores `_entries: dict[str, list[BashProcessEntry]]` where `BashProcessEntry = (pid: int, pgid: int)`. Methods: `register(instance_id, pid, pgid)`, `unregister(instance_id, pid)`, `cleanup_instance(instance_id)`, `cleanup_all()`. Singleton accessor `get_bash_process_registry()`. | `daemon/tools/bash.py` |
| 2 | Thread `instance_id` into `bash()` via wrapper (C3) | Add `instance_id: str \| None = None` kwarg to `bash()`. Define `_make_instance_id_aware(tool, get_default_instance_id)` mirroring `_make_workdir_aware` (instance.py:406-483). Compose at instance.py:953. Fallback: if `instance_id is None`, log WARNING and skip registration. See Task 3 detail. | `daemon/tools/bash.py`, `daemon/tools/instance.py` |
| 3 | Capture PGID eagerly at spawn (D4) | In `bash()`, immediately after `create_subprocess_exec/shell` returns (bash.py:143/152), capture `(proc.pid, os.getpgid(proc.pid))` BEFORE any await. Wrap getpgid in try/except — fall back to `pgid = proc.pid`. Call `registry.register(instance_id, pid, pgid)`. | `daemon/tools/bash.py:140-159` |
| 4 | Fix CancelledError leak — BOTH await points (C1) | Rework bash() try/except: (a) init `proc = None` sentinel before try; (b) wrap ENTIRE spawn+wait in try; (c) add `except asyncio.CancelledError` BEFORE `except Exception`; (d) in handler, guard `if proc is not None`, call `task.uncancel()` + `asyncio.shield(_kill_process(proc))` + `shield(unregister)`, then `raise`. See Task 4 detail. | `daemon/tools/bash.py:171-209` |
| 5 | Wire bash cleanup into two-tier dispatcher | In `_dispatch_instance_post_commit_side_effects`, add bash `cleanup_instance(instance_id)` in Tier 1 (adjacent to proc) and bash `cleanup_instance(iid)` in Tier 2 loop (adjacent to proc). Reuse `tree_ids` computed by Phase 1 (it's initialized outside try — C4). Best-effort try/except. | `daemon/services/job_feedback_observer.py:2391` |
| 6 | Wire bash cleanup into `manager.shutdown()` | Right after the proc `cleanup_all()` call added in Phase 1, add `get_bash_process_registry().cleanup_all()`. Best-effort try/except. | `daemon/manager.py:5536` |
| 7 | Unit test: registry register/unregister/cleanup | Test `register` adds `BashProcessEntry`, `unregister` removes a specific pid, `cleanup_instance` SIGKILLs all entries for an iid and clears the list, `cleanup_all` sweeps all. | `tests/.../test_bash_registry.py` (new) |
| 8 | Unit test: CancelledError at WAIT await point kills subprocess | Mock `asyncio.wait_for` to raise CancelledError. Assert `_kill_process` called, `unregister` called, CancelledError re-raised. Verify `task.uncancel()` was invoked (Python 3.11+). | `tests/.../test_bash.py` |
| 9 | Unit test: CancelledError at SPAWN await point kills subprocess | Mock `create_subprocess_exec` to spawn THEN raise CancelledError (simulate cancellation landing between spawn return and next await). Assert `_kill_process` called on the spawned proc, registry entry removed. | `tests/.../test_bash.py` |
| 10 | Unit test: `_make_instance_id_aware` injects instance_id | Test the wrapper: raw `bash` called with `instance_id=None` → wrapper injects `current_instance_id` from closure. Verify the `@tool` args_schema does NOT expose `instance_id` to the LLM. | `tests/.../test_instance_tools.py` |
| 11 | Unit test: Tier 1 + Tier 2 bash cleanup | Same pattern as Phase 1's proc tests, but for bash registry. Tier 1 always; Tier 2 root-gated. | `tests/.../test_job_feedback_observer.py` |
| 12 | Regression: pause/resume/cancel flows | Run existing pause/resume/cancel tests. The CancelledError fix changes the bash path under cancellation — confirm no regression. Pay special attention to `pause_instance_cascade` tests. | existing tests |

## Task 1 — `BashProcessRegistry` Implementation Detail (M5: `_entries`)

```python
# daemon/tools/bash.py (top of file, after imports)

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class BashProcessEntry:
    """A tracked bash-spawned subprocess.
    PID and PGID captured eagerly at spawn to avoid getpgid races
    at cleanup time (process may have exited)."""
    pid: int
    pgid: int


class BashProcessRegistry:
    """Tracks bash-spawned subprocess (pid, pgid) by instance_id.

    Minimal counterpart to BackgroundProcessManager: bash processes are
    synchronous/blocking with no readers, no exit watchers, no spill files.
    The registry only needs (pid, pgid) to SIGKILL the process group on
    instance cleanup.
    """

    def __init__(self) -> None:
        # M5: canonical field name is _entries (dict of lists of BashProcessEntry).
        # NOT _handles, NOT a set of Process objects.
        self._entries: Dict[str, List[BashProcessEntry]] = {}
        self._lock = asyncio.Lock()

    async def register(self, instance_id: str, pid: int, pgid: int) -> None:
        async with self._lock:
            self._entries.setdefault(instance_id, []).append(
                BashProcessEntry(pid=pid, pgid=pgid)
            )

    async def unregister(self, instance_id: str, pid: int) -> None:
        """Remove a single pid from an instance's list.
        Called after explicit _kill_process (timeout/cancel) — the group
        is already dead, so we stop tracking it."""
        async with self._lock:
            lst = self._entries.get(instance_id)
            if not lst:
                return
            self._entries[instance_id] = [e for e in lst if e.pid != pid]
            if not self._entries[instance_id]:
                del self._entries[instance_id]

    async def cleanup_instance(self, instance_id: str) -> int:
        """SIGKILL the process group for every tracked bash process
        owned by instance_id. Idempotent (atomic list pop under lock).
        Returns number of entries killed."""
        async with self._lock:
            entries = self._entries.pop(instance_id, [])
        killed = 0
        for entry in entries:
            try:
                self._kill_group(entry.pgid)
                killed += 1
            except Exception as e:
                logger.warning(
                    f"bash cleanup: killpg({entry.pgid}) failed: "
                    f"{type(e).__name__}: {e}"
                )
        return killed

    async def cleanup_all(self) -> int:
        """Daemon-shutdown sweep. Kill all tracked bash processes."""
        async with self._lock:
            instance_ids = list(self._entries.keys())
        total = 0
        for iid in instance_ids:
            try:
                total += await self.cleanup_instance(iid)
            except Exception as e:
                logger.warning(
                    f"bash cleanup_all: failed for {iid[:8]}: "
                    f"{type(e).__name__}: {e}"
                )
        if total:
            logger.info(f"bash cleanup_all: killed {total} process(es)")
        return total

    @staticmethod
    def _kill_group(pgid: int) -> None:
        """SIGKILL the whole process group. Unix-first; Windows fallback."""
        if sys.platform != "win32":
            os.killpg(pgid, signal.SIGKILL)
        else:
            # Windows: no process groups. Best-effort single-proc kill.
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pgid)],
                capture_output=True,
            )


# Module-level singleton + accessor (mirrors BackgroundProcessManager pattern)
_bash_process_registry: "BashProcessRegistry | None" = None


def get_bash_process_registry() -> BashProcessRegistry:
    global _bash_process_registry
    if _bash_process_registry is None:
        _bash_process_registry = BashProcessRegistry()
    return _bash_process_registry
```

## Task 2 & 3 — `instance_id` Plumbing via Wrapper (C3)

**Why a wrapper, not a raw param:** `bash` is a module-level `@tool` (bash.py:73-74) that becomes a `StructuredTool` whose `args_schema` is LLM-visible. Adding `instance_id` to the raw signature and letting `@tool` auto-expose it would let the LLM control instance scoping (bad). The wrapper pattern (already used for `workdir` via `_make_workdir_aware`) injects the value from a closure without advertising it.

**Step 1: Add `instance_id` kwarg to `bash()` (bash.py:74):**
```python
@tool
async def bash(
    command: str | List[str],
    timeout: int | float | None = 1800,
    workdir: str | None = None,
    input: str | None = None,
    instance_id: str | None = None,  # NEW — injected by _make_instance_id_aware; NOT LLM-visible
) -> str:
```
**Verify:** the `@tool` decorator (bash.py:73) auto-generates `args_schema` from the function signature. Check whether it exposes `instance_id`. If it does, suppress via `args_schema` Pydantic model that excludes `instance_id`, or via the decorator's exclude mechanism. The `_full_doc_` at bash.py:244 documents only `command`, `timeout`, `workdir`, `input` — match that.

**Step 2: Define `_make_instance_id_aware` (instance.py, near `_make_workdir_aware` at line 406):**
Mirror `_make_workdir_aware` (instance.py:406-483) exactly, but for `instance_id` instead of `workdir`. The wrapper injects `instance_id` kwarg when it's None, using the closure `get_default_instance_id`. Handle both `StructuredTool` and plain-function cases (same as the workdir wrapper).

```python
def _make_instance_id_aware(
    tool,
    get_default_instance_id: Callable[[], str | None],
):
    """Wrap a tool to auto-inject instance_id from a closure.

    Mirrors _make_workdir_aware (line 406) but for instance_id.
    The instance_id is injected as a kwarg and is NOT exposed in the
    tool's LLM-visible args_schema (callers pass it via the wrapper,
    not the LLM).
    """
    # ... mirror _make_workdir_aware structure ...
    # Key difference: inject instance_id instead of workdir:
    #   if kwargs.get('instance_id') is None:
    #       kwargs['instance_id'] = get_default_instance_id()
```

**Step 3: Compose at instance.py:953:**
```python
# instance.py:547 — create_instance_tools already receives current_instance_id
def create_instance_tools(manager, current_instance_id, agent_id=""):
    # ... existing closures ...
    def get_current_workdir() -> str | None:           # existing (line 562)
        return _get_project_workdir(manager, current_instance_id)
    def get_current_instance_id() -> str | None:       # NEW — trivial closure
        return current_instance_id

    # instance.py:953 — compose BOTH wrappers. Order: workdir first, then instance_id.
    bash_aware = _make_instance_id_aware(
        _make_workdir_aware(bash, get_current_workdir),
        get_current_instance_id,
    )
    # ... rest of tools unchanged ...
```
Order rationale: `workdir` and `instance_id` are independent kwargs. Wrapping order doesn't affect correctness, but workdir-first matches the existing call site structure.

**Step 4: Runtime fallback in `bash()` (bash.py):** If `instance_id is None` when bash() runs (e.g., called outside the wrapper), log WARNING and skip registration. Do NOT register under a sentinel like `"unknown"` — that would create an unkillable bucket shared by all unwrapped calls.

## Task 4 — CancelledError Fix: BOTH Await Points + Uncancel/Shield (C1)

**The two await points that leak:**
| Await point | Line | What leaks on CancelledError |
|-------------|------|------------------------------|
| `await asyncio.create_subprocess_exec/shell(...)` | bash.py:143/152 | The just-spawned subprocess (spawn may complete before cancellation propagates). |
| `await asyncio.wait_for(proc.wait(), ...)` | bash.py:172 | The subprocess + its process group. |

**Why the current code doesn't catch CancelledError:**
- The outer `except Exception` (bash.py:209) does NOT catch `asyncio.CancelledError` — in Python 3.9+, `CancelledError` inherits from `BaseException`, not `Exception`. So it propagates unhandled.
- The inner `except asyncio.TimeoutError` (bash.py:175) only catches timeouts.
- The function-level `finally` (bash.py:211-226) only closes temp files — it has no reference to `proc`.

**Why `_kill_process` can't be called naively from a CancelledError handler:**
- `_kill_process` does `await asyncio.wait_for(proc.wait(), timeout=5.0)` (bash.py:47).
- In Python 3.11+, cancellation is "sticky" — once a task is cancelled, the next `await` re-raises `CancelledError`. So `_kill_process`'s internal await would re-raise, skipping the `unregister` call after it.
- `task.uncancel()` decrements the cancellation count to 0, allowing the awaits in `_kill_process` and `unregister` to proceed. We then `raise` manually to re-propagate the original cancellation.

**Implementation (reworked bash.py structure):**
```python
async def bash(command, timeout=1800, workdir=None, input=None, instance_id=None) -> str:
    # ... validation, temp file setup (unchanged) ...
    proc: asyncio.subprocess.Process | None = None  # ← SENTINEL: guard in CancelledError handler
    try:
        try:
            stdin_arg = stdin_file if stdin_file is not None else asyncio.subprocess.DEVNULL
            if isinstance(command, list):
                proc = await asyncio.create_subprocess_exec(*command, ...)  # AWAIT POINT 1
            else:
                proc = await asyncio.create_subprocess_shell(command, ...)  # AWAIT POINT 1
        finally:
            stdout_file.close(); stderr_file.close()
            if stdin_file is not None: stdin_file.close()

        # REGISTER after spawn (always) — D4: eager PGID capture
        if instance_id is not None and proc is not None:
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                pgid = proc.pid  # start_new_session=True → PGID == PID
            await get_bash_process_registry().register(instance_id, proc.pid, pgid)
        elif instance_id is None:
            logger.warning("bash: instance_id is None; skipping process registration")

        # WAIT — AWAIT POINT 2
        actual_timeout = None if timeout == 0 else timeout
        try:
            await asyncio.wait_for(proc.wait(), timeout=actual_timeout)
            timed_out = False
        except asyncio.TimeoutError:
            await _kill_process(proc)
            if instance_id is not None:
                await get_bash_process_registry().unregister(instance_id, proc.pid)
            timed_out = True

        # ... read output, build content (unchanged) ...
        return content

    except asyncio.CancelledError:  # ← MUST be BEFORE except Exception
        # Cancellation at EITHER await point (spawn or wait_for).
        # Clean up the subprocess if it was spawned, then re-propagate.
        if proc is not None:
            # Clear sticky cancellation so _kill_process's internal awaits
            # don't immediately re-raise CancelledError (Python 3.11+).
            task = asyncio.current_task()
            if task is not None and hasattr(task, "uncancel"):
                task.uncancel()
            try:
                # shield protects _kill_process from concurrent cancellation re-entry
                await asyncio.shield(_kill_process(proc))
                if instance_id is not None:
                    await asyncio.shield(
                        get_bash_process_registry().unregister(instance_id, proc.pid)
                    )
            except Exception:
                pass  # best-effort during cancellation — must still re-raise below
        raise  # ALWAYS re-propagate the cancellation

    except Exception as e:
        return f"ERROR: {str(e)}"
    finally:
        # temp file cleanup (unchanged) — does NOT touch proc
        ...
```

**Key correctness points:**
1. `proc: ... | None = None` sentinel BEFORE the try — the CancelledError handler can safely guard `if proc is not None`. Covers the case where cancellation lands during spawn (proc never assigned) vs after spawn (proc assigned).
2. `except asyncio.CancelledError` is placed BEFORE `except Exception` — CancelledError is a `BaseException`, and Python matches except clauses in order. If `except Exception` came first, CancelledError would bypass it (correct) but then there'd be no handler at all (wrong — it'd propagate without cleanup).
3. `task.uncancel()` (Python 3.11+) clears sticky cancellation so `_kill_process`'s internal `await asyncio.wait_for(proc.wait(), timeout=5.0)` doesn't immediately re-raise. Guard with `hasattr(task, "uncancel")` for older Python.
4. `asyncio.shield()` is belt-and-suspenders — it protects `_kill_process` from any concurrent cancellation re-entry even if `uncancel()` isn't available.
5. The handler ALWAYS ends with `raise` — cancellation must propagate. The cleanup is best-effort; if it fails, we still re-raise.
6. `unregister` after `_kill_process` — the group is dead, so stop tracking it. (Matches D5.)

## Task 5 & 6 — Hook Wiring (parallel to Phase 1, same blocks)

```python
# job_feedback_observer.py, in _dispatch_instance_post_commit_side_effects
# Phase 1 added Tier 1 + Tier 2 proc blocks. Phase 2 ADDS bash adjacent:

from daemon.tools.bash import get_bash_process_registry

# --- TIER 1: ALWAYS clean THIS instance's own processes ---
try:
    proc_mgr = get_background_process_manager()
    await proc_mgr.cleanup_instance(instance_id)
except Exception as e:
    logger.warning(f"Tier-1 proc cleanup failed for {instance_id[:8]}: {type(e).__name__}: {e}")
# NEW (Phase 2): bash Tier 1
try:
    bash_reg = get_bash_process_registry()
    await bash_reg.cleanup_instance(instance_id)
except Exception as e:
    logger.warning(f"Tier-1 bash cleanup failed for {instance_id[:8]}: {type(e).__name__}: {e}")

# --- TIER 2: Root-gated tree sweep for DESCENDANTS ---
tree_ids: list[str] = []  # initialized by Phase 1, OUTSIDE try
if parent_id is None:
    try:
        instance_repository = getattr(self._instance_manager, "_instance_repository", None)
        if instance_repository is not None:
            tree_ids = await asyncio.to_thread(instance_repository.get_tree_ids, instance_id)
    except Exception as e:
        logger.warning(f"Tier-2 get_tree_ids failed for root {instance_id[:8]}: {type(e).__name__}: {e}")
    for iid in tree_ids:
        if iid == instance_id:
            continue  # already cleaned in Tier 1
        try:
            await proc_mgr.cleanup_instance(iid)
        except Exception as e:
            logger.warning(f"Tier-2 proc cleanup failed for {iid[:8]}: {type(e).__name__}: {e}")
        # NEW (Phase 2): bash Tier 2
        try:
            await bash_reg.cleanup_instance(iid)
        except Exception as e:
            logger.warning(f"Tier-2 bash cleanup failed for {iid[:8]}: {type(e).__name__}: {e}")
```

```python
# daemon/manager.py, in shutdown(), right AFTER Phase 1's proc cleanup_all():
try:
    from daemon.tools.bash import get_bash_process_registry
    bash_killed = await get_bash_process_registry().cleanup_all()
    if bash_killed:
        logger.info(f"shutdown: killed bash processes: {bash_killed}")
except Exception as e:
    logger.warning(f"shutdown: bash cleanup_all failed: {type(e).__name__}: {e}")
```

**Note:** Phase 2 reuses `tree_ids` (Phase 1 initialized it outside try — C4). Phase 2 checks `tree_ids` iterates the same list. No re-fetch.

## Key Files

- `daemon/tools/bash.py` — new `BashProcessRegistry` class + `instance_id` kwarg + `bash()` rework (register, CancelledError fix, unregister)
- `daemon/tools/instance.py` — new `_make_instance_id_aware` wrapper + composition at line 953
- `daemon/services/job_feedback_observer.py` — add bash calls adjacent to Phase 1's proc calls in Tier 1 + Tier 2
- `daemon/manager.py` — add bash `cleanup_all()` in `shutdown()` adjacent to Phase 1's proc call
- `tests/.../test_bash_registry.py` (new) — registry unit tests
- `tests/.../test_bash.py` — CancelledError leak fix tests (both await points)
- `tests/.../test_instance_tools.py` — `_make_instance_id_aware` wrapper test + args_schema non-exposure
- `tests/.../test_job_feedback_observer.py` — Tier 1 + Tier 2 bash cleanup

## Constraints

- **Start only after Phase 1 merges** (D12, M2). Same lines/functions.
- **Don't extend `BackgroundProcessManager`.** Separate registry (D1). Field is `_entries: dict[str, list[BashProcessEntry]]` (M5).
- **PGID captured eagerly at spawn.** Never call `os.getpgid(pid)` at cleanup time — the process may be dead.
- **Don't unregister on normal foreground completion.** Backgrounded grandchildren survive in the process group; the registry must keep the PGID until instance cleanup.
- **CancelledError fix must cover BOTH await points.** `proc=None` sentinel; handler BEFORE `except Exception`; `uncancel()` + `shield()`; always `raise` at end.
- **`instance_id` hidden from LLM.** Verify `@tool` args_schema doesn't expose the new kwarg. Suppress if needed.
- **Best-effort everywhere.** All registry calls wrapped in try/except; failures log WARNING.
- **Windows fallback.** Keep `sys.platform != "win32"` guards. Use `taskkill /F /T /PID` on Windows.
- **Reuse `tree_ids` from Phase 1.** Don't re-fetch in Phase 2's bash block.

## Deliverables

- [ ] `BashProcessRegistry` class with register/unregister/cleanup_instance/cleanup_all (`_entries` field)
- [ ] `_make_instance_id_aware` wrapper defined and composed at instance.py:953
- [ ] bash spawns register their (pid, pgid) eagerly; `instance_id=None` fallback logs WARNING
- [ ] `@tool` args_schema does NOT expose `instance_id` to the LLM
- [ ] CancelledError at WAIT await point kills subprocess (no leak)
- [ ] CancelledError at SPAWN await point kills subprocess (no leak)
- [ ] `uncancel()` + `shield()` prevent sticky-cancellation re-raise in handler
- [ ] Tier 1 + Tier 2 bash cleanup wired into dispatcher
- [ ] Daemon shutdown kills all bash processes
- [ ] All existing tests pass (especially pause/resume/cancel)
- [ ] Best-effort: cleanup failures log WARNING, never block

## Edge Cases Handled in This Phase

| Edge Case | Handling |
|-----------|----------|
| bash foreground exits normally, grandchildren survive | Registry keeps PGID; killed on instance cleanup (Tier 1 or Tier 2). |
| bash times out | `_kill_process` already called; `unregister` removes the entry. No double-kill at cleanup. |
| bash cancelled at WAIT await point | NEW CancelledError handler: `uncancel()` + `shield(_kill_process)` + `shield(unregister)` + `raise`. No leak. |
| bash cancelled at SPAWN await point | Handler guards `if proc is not None`. If spawn completed before cancellation, proc is killed. If not, skip and re-raise. |
| Process exits between spawn and `os.getpgid` | Fallback to `pgid = proc.pid` (start_new_session guarantees PGID==PID). |
| `killpg` on already-dead group | `ProcessLookupError`/`OSError` caught per-entry, WARNING logged, loop continues. |
| `instance_id` is None at runtime (unwrapped call) | WARNING logged, registration skipped. No sentinel bucket. |
| `@tool` exposes instance_id to LLM | Suppress via args_schema override (verify first). |
| Truly detached orphan (child called `setsid`) | Known limitation — outside the process group, unreachable by `killpg`. Document. |
| No bash processes for an instance | `cleanup_instance` returns 0. No-op. |
| Double cleanup (terminate cascade + finalize) | Idempotent list pop. No-op second time. |
| Python < 3.11 (no `task.uncancel`) | `hasattr(task, "uncancel")` guard; rely on `asyncio.shield()` alone. |
