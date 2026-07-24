# Lesson: CodeMirror Undo/Redo Not Propagating to Dirty State (W2 Fix)

**Date:** 2026-07-24
**Branch:** `feature/workspace-file-tabs`
**Found during:** Final E2E re-test (Scenario 14 / W2: "Edit, save, type during save, undo → still dirty")
**Commit:** `2de21436` — `fix: propagate undo/redo events through CodeMirror directive for dirty state tracking`

## Problem

When a user edited a file in the CodeMirror editor and then pressed Undo (Cmd+Z / Ctrl+Z), the dirty indicator (`.dirty-dot`) did NOT update correctly. The `editedContent` signal was never updated by undo/redo transactions, leaving the dirty state stuck — even after undoing back to the saved state, the tab still showed as dirty.

## Root Cause

The `CodemirrorDirective`'s `updateListener` callback filtered transactions using only `t.isUserEvent('input')`. This caught normal keyboard input but **silently dropped undo/redo transactions**, which have `userEvent` values of `'undo'` and `'redo'` respectively.

The listener code was:
```typescript
// BEFORE (buggy)
updateListener: (update) => {
  if (update.transactions.some(t => t.isUserEvent('input'))) {
    this.contentChange.emit(/* ... */);
  }
}
```

CodeMirror 6 treats undo/redo as separate user event types — `'input'` only covers direct text insertion/deletion. Undo/redo are batched transactions with their own event names.

## Fix

Added `'undo'` and `'redo'` to the transaction filter (1 line expanded to 3):

```typescript
// AFTER (fixed)
updateListener: (update) => {
  if (update.transactions.some(t =>
    t.isUserEvent('input') || t.isUserEvent('undo') || t.isUserEvent('redo')
  )) {
    this.contentChange.emit(/* ... */);
  }
}
```

**Commit:** `2de21436`

## Before/After

| Aspect | Before | After |
|--------|--------|-------|
| Normal typing → dirty | ✅ Works | ✅ Works |
| Undo → dirty state updates | ❌ Stuck dirty | ✅ Correctly updates |
| Redo → dirty state updates | ❌ Stuck clean | ✅ Correctly updates |
| Undo to saved state → clean | ❌ Still showed dirty | ✅ Shows clean |

## Pattern

**Key takeaway:** When using CodeMirror 6's `updateListener` to track content changes:
1. `t.isUserEvent('input')` only catches direct text input — NOT undo/redo
2. Always also check `'undo'` and `'redo'` user events for complete dirty-state tracking
3. CodeMirror 6's transaction/userEvent taxonomy is granular: input, input.drop, delete, undo, redo, etc.
4. **Follow-up recommended:** Add unit tests for undo/redo propagation in `codemirror.directive.spec.ts` — current tests only cover `input` events.

## Testing History Across Rounds

This is the 2nd bug found by E2E testing on this feature branch:
1. **Round 1** (`37d2039f`): Edit preservation on tab switch-back (code-viewer bound to pristine content) — commit `67213e70`
2. **Round 3** (`3a3943df`): Undo/redo not propagating to dirty state — commit `2de21436`

Both bugs were in the content-binding/dirty-state tracking layer of the editor. This is a high-risk area for the multi-tab feature.
