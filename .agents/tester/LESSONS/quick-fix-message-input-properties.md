# Quick Fix: Restored Removed Properties in message-input.component.ts

**Date**: 2026-05-15
**Session**: fix-message-input
**Commit**: `781a5c2`

## Issue
The `send-stop-ux-fix` opencode session accidentally removed 5 properties from `MessageInputComponent` while rewriting the e2e test:

1. `MAX_IMAGES = 3` — Used in template for attach button disable logic
2. `MAX_IMAGE_SIZE = 10 * 1024 * 1024` — Used in `processFiles()` for size validation
3. `ACCEPTED_TYPES` — Used in `processFiles()` for file type validation
4. `agentColorMap` — Used by `color` getter for send button styling
5. `color` getter — Used in template for send button background color

## Root Cause
The opencode session's diff included a deletion of these properties that wasn't part of the test rewrite task. The diff went beyond the scope of the e2e test changes.

## Fix
Restored all 5 properties (21 lines) in their original location between `isInstanceRunning` and `get canSend()`.

## Verification
- Angular dev build: ✅ PASS
- Unit tests (28): ✅ ALL PASS
- Template references work correctly

## Lesson
When delegating to opencode, always check the full git diff before accepting results. The session may modify files outside the intended scope.
