# Lesson: File-Tab Edit Preservation Bug (code-viewer binding)

**Date:** 2026-07-24
**Branch:** `feature/workspace-file-tabs` @ `37d2039f`
**Commit:** `67213e70`
**Found by:** Worker `file-tabs-e2e` (E2E scenario 4: "Edit file A, switch to B, switch back to A → unsaved edits preserved")

## Problem

When using the new VS Code-style multi-file tabs, editing a file then switching to another tab and back would **lose unsaved edits**. The editor content reverted to the pristine (last-loaded) version.

## Root Cause

`CodeViewerComponent` template bound the CodeMirror editor to `[content]="f.content"` — the **pristine** file content from the `OpenFileTab.content` field. When switching tabs, the workspace correctly preserved `editedContent` in state, but the template was not reading it. The round-trip through Angular's binding clobbered the user's edits with the original content.

## Fix

1. **`code-viewer.component.ts`** (1 line): Template binding changed from `[content]="f.content"` to `[content]="editedContent()"` — reads the live edited value instead of the pristine content.

2. **`codemirror.directive.ts`** (+7 lines): Added an equality guard that skips dispatching content to CodeMirror when the incoming value equals the current document string. This prevents cursor jumps and redundant re-renders caused by the binding round-trip (Angular updates the input → directive dispatches to CM → CM triggers change → Angular re-checks).

## Additional Compile Fixes (pre-existing WIP)

The working tree had incomplete WIP changes that prevented compilation:
- `workspace.service.ts`: duplicate `OpenFileTab` import (lines 5+13), missing `FileChangeEvent` import
- `workspace.model.ts`: `OpenFileTab.content` was required but never populated by `openFiles` computed
- `workspace.component.ts`: `markSaved()` → `markSaved(savedContent)` call site not updated

These were fixed to make the E2E test runnable; they were already present in the working tree as unfinished work.

## Before/After

| Aspect | Before | After |
|--------|--------|-------|
| Edit preservation on tab switch-back | ❌ Edits lost | ✅ Edits preserved |
| Cursor jumps on re-bind | ❌ Possible | ✅ Guarded (equality check) |
| Compile | ❌ 5 errors (WIP) | ✅ Clean |

## Pattern

**Key takeaway:** When implementing a multi-tab editor pattern in Angular:
1. Bind the editor to the **edited/live content** (signal/computed), NOT the pristine source field
2. Add an equality guard in the CodeMirror directive to prevent cursor jumps from binding round-trips
3. The `OpenFileTab` model should track both `content` (pristine) and the edited state separately
