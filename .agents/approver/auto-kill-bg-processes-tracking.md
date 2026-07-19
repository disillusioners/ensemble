# Tracking: Auto-Kill Background Processes on Root Instance Completion

| Field | Value |
|-------|-------|
| Plan Dir | `.agents/shared/planning/auto-kill-bg-processes/` |
| Created | 2026-07-18 |
| Scope | MEDIUM (~7 files, 3 phases, in-memory only) |

## Iteration 001 — APPROVED

**Date:** 2026-07-18 19:25 UTC
**Verdict:** APPROVED

### Verification Performed

**Codebase fact-check (all claims verified accurate):**
- `BackgroundProcessManager.cleanup_instance` at proc_tools.py:997 — idempotent, atomic bucket pop ✓
- `get_background_process_manager()` singleton at proc_tools.py:1261 ✓
- `_processes` keyed by instance_id at proc_tools.py:205 ✓
- `cleanup_all()` does NOT exist (confirmed gap) ✓
- `_dispatch_instance_post_commit_side_effects` at job_feedback_observer.py:2391 — receives `instance_id` + `parent_id` ✓
- `JobFeedbackObserver` has `self._instance_manager` (line 300), NO `self.repository` ✓
- `_instance_repository` attribute on InstanceManager (manager.py:757) ✓
- `get_tree_ids` is SYNC (def not async def) at repositories/instance/repository.py:246, includes root in result ✓
- `daemon/manager.py` path correct (NOT services/), `shutdown()` at line 5536 ✓
- `terminate_instance` proc cleanup at instance_lifecycle.py:1461 — exists, does NOT call dispatcher ✓
- bash `@tool` at bash.py:73-74, two await points (143/152 spawn, 172 wait_for) ✓
- `_kill_process` uses `os.killpg(os.getpgid(...))` on Unix (lines 42-48) ✓
- `start_new_session=True` at bash.py:112 ✓
- `_make_workdir_aware` wrapper at instance.py:406, composition at instance.py:953 ✓
- `create_instance_tools(manager, current_instance_id, ...)` at instance.py:547 ✓

**Convergence claim verified:** COMPLETED/ERROR/FAILED all reach `_dispatch_instance_post_commit_side_effects`:
- COMPLETED/ERROR via `_finalize_job` (line 1708) and `_finalize_instance` (line 2382)
- ERROR path: `_send_error_report` → `_send_error_report_db_sync` (sets ERROR) → `_emit_terminal_via_bus` → re-triggers `_finalize_job` → dispatcher ✓
- TERMINATED handled separately by `terminate_instance` (own cleanup, does NOT call dispatcher) ✓

**Council assessment (5 areas):**
- AREA 1 (CancelledError fix): Council rated RISKY, but point #1 (killpg) was based on incorrect info — `_kill_process` already uses killpg. Remaining points are minor defensive refinements.
- AREA 2 (tree sweep race): SOUND — benign, descendant Tier 1 backstops
- AREA 3 (idempotency): SOUND — atomic bucket pop under lock
- AREA 4 (sequential phasing): SOUND — justified by same-line/function edit overlap
- AREA 5 (shutdown ordering): SOUND — Phase 2 fix is prerequisite, already sequenced correctly

### Non-Blocking Observations (Notes for implementer)

1. **`except BaseException` in shielded cleanup:** The plan uses `except Exception: pass` around the `asyncio.shield(_kill_process(...))` + `shield(unregister(...))` block in the CancelledError handler. Consider using `except BaseException` for maximum defensive coverage against a second concurrent CancelledError arriving during shielded cleanup. Non-blocking — `asyncio.shield()` already insulates the inner coroutine from the outer cancellation.

2. **Parallel-call idempotency test:** Add a unit test in Phase 1/2 that calls `cleanup_instance(id)` twice concurrently via `asyncio.gather` and asserts no warnings/errors and identical end-state. Strengthens the idempotency guarantee beyond sequential double-fire testing.

3. **Optional helper extraction:** Extracting `cleanup_descendants_of(root_id)` in Phase 1 would make the Tier 2 sweep unit-testable in isolation and reduce the Phase 2 diff. Orthogonal improvement — not required.

4. **Python version floor:** The plan correctly guards `task.uncancel()` with `hasattr(task, "uncancel")` for pre-3.11 compatibility. Confirm the project's minimum Python version (appears to support 3.13/3.14 based on `.pyc` files) so the `uncancel()` path is the primary mechanism.

5. **Known limitations (to document at merge):**
   - Truly-detached orphans (child called `setsid`) are outside the process group — unreachable by killpg. Plan already acknowledges this.
   - Daemon crash between root finalization and descendant finalization = transient leak until process exits naturally or daemon restarts. `cleanup_all()` on next startup does NOT help (registries are in-memory). This is an inherent in-memory design limitation.
