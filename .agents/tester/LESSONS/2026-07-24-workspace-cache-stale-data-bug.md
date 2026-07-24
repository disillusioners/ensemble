# Pre-Existing Bug: WorkspaceService Cache Restore Stale Data — RESOLVED

**Date:** 2026-07-24  
**Discovered by:** E2E worker `e86c8179` (dep-drop-e2e-verify)  
**Fixed by:** commit `8d7e5c29` on `feature/tab-workspace-sync`  
**Status:** ✅ RESOLVED

## Symptom

When switching to a previously-visited project tab, the workspace file tree showed the **wrong project's** files (stale data from the previously active project). No API call was made (cache hit returned wrong data).

## Root Cause

The `saveCurrentState`/`restoreState` LRU cache in `workspace.service.ts` had a timing race: `saveCurrentState` snapshotted `currentTree` before the new project's tree loaded, so cache entries for the destination project contained the source project's tree data.

## Fix

Commit `8d7e5c29` — Added `_treeProjectId` field to track which project's data `currentTree` holds. `saveCurrentState` now guards against overwriting cache entries when `_treeProjectId !== projectId`. Regression test at `workspace.service.spec.ts:556` verifies the late-HTTP-response race.

## Verification

- Unit: 3 critical regression tests PASS (chat effect dep-drop + 2 cache tests)
- E2E: 9/9 steps PASS, 8/8 rapid switches correct — file tree shows correct project on every switch
- Previous symptom "workspace stops changing after 3-4 tab switches" FULLY RESOLVED

## Confirming Runs

- Full Jest suite: 1,655/1,655 PASS
- Browser E2E: 9/9 steps, 8/8 rapid switches — all file tree content correct
