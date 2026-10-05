# R0 — Crash Attribution

**Date:** 2026-10-05 UTC
**Verdict:** **PRE-EXISTING-AT-BASE** (NOT caused by the F-1/F-2 branch)

## Crash traceback (extracted from l1-reboot-2.log, first observed at 00:38:29)

```
RuntimeError: DependencyBus is not initialized for instance=f165c8f1...; bus must be initialized (Phase 5).
```

Full stack (caller chain):
```
File "daemon/services/message_processing_pipeline.py", line 820, in _check_child_completion
    await checker(instance_id, message_id)
File "daemon/manager.py", line 8286, in _process_child_completion_and_notify_parent
    return await self._child_reports_service._process_child_completion_and_notify_parent(
File "daemon/services/child_reports.py", line 2576, in _process_child_completion_and_notify_parent
    result = await asyncio.to_thread(...)
File "daemon/services/child_reports.py", line 2841, in _process_child_completion_db_sync
    raise RuntimeError(
        f"DependencyBus is not initialized for instance="
        f"{instance_id[:8]}...; bus must be initialized "
        f"(Phase 5)."
    )
```

## Raise site (verbatim)

`daemon/services/child_reports.py:2841` in `_process_child_completion_db_sync`.
The site is an intentional **A8 HARD ERROR** (not graceful degradation). The
inline comment at the raise site reads:
```
# ─── A8: HARD ERROR (not graceful degradation) ─────────
# Bus is None is an INVALID state. Mirrors the
# A8 hard error at site 2.
raise RuntimeError(...)
```

## Git attribution (18827dbd..HEAD)

### Files in the traceback path
- `daemon/services/child_reports.py` — **NOT TOUCHED** by F-1/F-2 branch
  - `git log 18827dbd..HEAD -- daemon/services/child_reports.py` → empty
  - `git diff --stat 18827dbd..HEAD -- daemon/services/child_reports.py` → empty
- `daemon/services/message_processing_pipeline.py` — **NOT TOUCHED** by F-1/F-2 branch
  - `git log 18827dbd..HEAD -- daemon/services/message_processing_pipeline.py` → empty
- `daemon/manager.py` — touched (66 lines changed), but the call site at
  line 8286 is in the F-1-irrelevant manager dispatch path (no diff
  context shows the line being modified by the F-1/F-2 branch)

### F-1 modified files (for adjacency check)
- `daemon/services/dependency_bus.py` — +58/-20 (F-1 gates at ~:702 emit_terminal
  and ~:907 emit_terminal_for_child_instance). The diff adds the
  `_has_truthy_error` helper and gates the `_parent_errored` flip on it.
- `daemon/repositories/task/repository.py` — +200 lines (F-1 wipe-side
  preserve extension: 2-arm disjunction per W-3)
- `daemon/repositories/message_queue/repository.py` — +165 lines (F-1
  wipe-side mirror)
- `daemon/repositories/report_injection/repository.py` — +43 lines (F-2
  RDRS lane-2 anchor-less admission)
- `daemon/services/report_delivery_recovery.py` — +386 lines (F-2 RDRS
  lane-2 service extension)
- `daemon/services/report_delivery_ledger.py` — +148 lines (F-2 new file)
- `daemon/services/instance_lifecycle.py` — +126 lines (F-1/F-2 boot
  sequence comments + ownership block markers)

### F-1 gate sites vs crash site
The F-1 gates are at `dependency_bus.py:702` (emit_terminal) and
`dependency_bus.py:907` (emit_terminal_for_child_instance). The crash
site is `child_reports.py:2841` in `_process_child_completion_db_sync`.
**The crash site is NOT adjacent to the F-1 gates** — it is in a
different file, in a different function, in a different module.
The F-1 gates control the `_parent_errored` flip based on truthy-error;
the crash is a bus-not-initialized hard error in a different code path.

## Verdict

**PRE-EXISTING-AT-BASE.** The crash is a pre-existing race condition in
the `MessageProcessingPipeline._check_child_completion` → `ChildReportsService._process_child_completion_and_notify_parent`
→ `ChildReportsService._process_child_completion_db_sync` path. The F-1/F-2
branch did not touch any of these files. The branch modified
`dependency_bus.py` (the F-1 bus gates) but the crash site is in
`child_reports.py` — a different file, not adjacent to the F-1 gates.

The crash is by-design ("A8 hard error") — the comment at the raise site
explicitly states "Bus is None is an INVALID state" and "A8: HARD ERROR
(not graceful degradation)". The bug is that the pipeline was called
for an instance whose bus hadn't been initialized (likely a leftover
child-completion from a previous test that fired during boot).

**Recommendation:** This is a pre-existing issue, not a regression. It
should be tracked as a separate issue (e.g., "bus initialization race
in message_processing_pipeline._check_child_completion") and fixed in
a separate commission. The F-1/F-2 branch is NOT responsible.
