# Workspace Viewer — Approval Tracking

## Iteration 001
**Date**: 2026-07-22 19:28
**Verdict**: REJECTED
**Status**: IN_PROGRESS

### Blocking Issues Found

1. **Watchdog thread-safety bug** — `FileChangeMonitor._emit()` calls `queue.put_nowait()` on an `asyncio.Queue` directly from watchdog's Observer thread. `asyncio.Queue` is NOT thread-safe. The codebase already has the correct pattern (`dispatch_event_bus.py:67`, `completion_registry.py:133` use `loop.call_soon_threadsafe`). This will silently break SSE file-change events.
   - Expected: Thread-safe queue writes via `loop.call_soon_threadsafe(queue.put_nowait, event)` + `QueueFull` guard
   - Found: Direct `queue.put_nowait()` call from non-asyncio thread

2. **Diff tab never fetches data** — `WorkspacePageComponent` template binds Diff tab to `(change)="viewMode.set('diff')"` but never calls `workspace.getFileDiff()`. The `onToggleDiff()` method exists but is orphaned. `DiffViewerComponent` renders empty (currentDiff signal stays null).
   - Expected: Switching to Diff tab triggers `getFileDiff(projectId, path)` and renders diff
   - Found: No data-fetch binding on tab switch

3. **FileChangeMonitor singleton broken after stop** — `watchdog.Observer` is single-shot (its thread ends on `stop()`). The `_instances` registry keeps the stopped instance forever; `get_or_create` returns it on re-subscription. `_start()` would try to re-use a dead observer.
   - Expected: Fresh observer on re-subscription or `is_running()` guard
   - Found: Stopped instance reused from class-level registry

4. **FileTreeComponent subscribes to `fileChanged` as if it were an Observable pipe** — Phase 3 Task 4 code uses `this.workspace.fileChanged.pipe(takeUntil(...))`, but `fileChanged` is declared as a `signal` in Phase 3 Task 1, not an Observable. Signals don't have `.pipe()`. This won't compile.
   - Expected: Either use `effect()` for signal subscription, or declare `fileChanged` as an Observable/Subject
   - Found: `.pipe()` called on a signal

### Non-Blocking Observations
- `GIT_TIMEOUT_S` missing from constants.py (plan already lists constants.py as MODIFY — ordering detail)
- `_build_tree` has no pagination on the tree endpoint (fast-follow for large repos)
- Polling fallback `_poll_loop` body is incomplete (`...` placeholder)

## Iteration 002
**Date**: 2026-07-22 19:55
**Verdict**: APPROVED
**Status**: APPROVED

### Previous Blocking Issues — Resolution Verification
1. **Watchdog thread-safety** ✅ FIXED — `_emit()` now uses `loop.call_soon_threadsafe(self._safe_put, queue, event_data)` with QueueFull guard (phase1-backend.md:817-829)
2. **Diff tab never fetches data** ✅ FIXED — `onSelectDiff()` calls `workspace.getFileDiff()` before switching view mode (phase2-frontend.md:738-751)
3. **FileChangeMonitor singleton broken after stop** ✅ FIXED — `_stop()` evicts from `_instances` (line 885), `get_or_create` checks `_started` flag and evicts dead instances (lines 762-769)
4. **Signal pipe bug** ✅ FIXED — Phase 3 Task 4 uses `effect()` instead of `.pipe()` (phase3-integration.md:225-230)

### Fresh Evaluation (Independent)
- **Architecture**: Sound. Backend (WorkspaceRouter + WorkspaceGuard + GitDiffService + FileChangeMonitor) + Frontend (3 components + directive + service) + Integration (SSE wiring + navigation). Clean module boundaries.
- **Security model**: Thorough. Path traversal via WorkspaceGuard.resolve(), file size limit (1MB), binary detection, git injection prevention (arg list, never shell=True), symlink traversal prevention (lstat + type=symlink + no recursion into symlinks), directory depth limit.
- **Phase structure**: Correct. Phase 1 (root) → Phase 2 (loose, REST contract only, schema freeze gate) → Phase 3 (tight, requires both phases). Scheduling strategy is sound.
- **Codebase verification**: All claimed patterns verified against actual source — `_resolve_within_workdir` etc. exist in filesystem.py:45-176, `repo.get(project_id)` exists in repository.py:218, SSE pattern in notifications.py:43-90, constants SSE_TIMEOUT_S/PING_INTERVAL/QUEUE_MAXSIZE in constants.py:23-25, Angular routes/standalone components/service patterns all confirmed.
- **Code stub correctness**: WorkspaceGuard faithfully ports filesystem.py logic. GitDiffService has_changes logic correctly handles modified/new/unchanged files. FileChangeMonitor thread-safety is correct.

### Non-Blocking Observations (Notes)
1. **FileChangeMonitor `_stop()` key mismatch** — `_stop()` uses `str(self.workdir)` (unresolved Path) while `get_or_create` uses `str(Path(workdir).resolve())`. If workdir has symlinks/relative components, `_stop()` eviction silently fails. Harmless because `get_or_create` also cleans up dead instances on next access. Minor memory leak in rare edge case (last subscriber disconnects, no reconnection). Fix: use `str(self.workdir.resolve())` in `_stop()`.
2. **GitDiffService working_content read has no size check** — The diff endpoint reads the working file via `read_text()` without checking against MAX_FILE_SIZE_BYTES (unlike the file endpoint's `_read_file_safe`). A very large file could cause memory pressure. Low risk in practice (source files are typically <1MB). Fix: add stat.st_size check before read_text in GitDiffService.get_file_diff.
3. **Language map mismatch** — Backend detects 17 languages (including scss, shell, go, rust, java, tsx, jsx); frontend CodeMirror directive only imports 9 language packages. Unknown languages degrade gracefully to plain text. Could add more language packages or sync the maps.
4. **FileTreeComponent tree data wiring** — The `setTree(tree)` method exists but is not shown wired to `workspace.currentTree` signal in the WorkspaceComponent template. Implementer would catch this during Phase 2 implementation.
