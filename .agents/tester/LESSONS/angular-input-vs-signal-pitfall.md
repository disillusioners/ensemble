# Angular @Input() vs input() Signal Pitfall

**Date**: 2026-05-15
**Component**: `MessageInputComponent` (frontend/src/app/components/message-input/)
**Commits**: `751dd43`, `0ed06e5`

## Problem
The Stop button never appeared in the UI despite SSE `status_change` events arriving in 7ms. The `isInstanceRunning` computed signal didn't react to input changes.

## Root Cause
Angular's `@Input()` decorator sets a **plain property**, not a signal. Computed signals can only track other signals. When `instanceStatus` was set via `@Input()`, the computed `isInstanceRunning` never re-evaluated.

```typescript
// BROKEN - plain property, computed can't track
@Input() instanceStatus: InstanceStatus | null = null;

// FIXED - signal function, computed tracks it
readonly instanceStatus = input<InstanceStatus | null>(null);
```

## Fix
Convert all `@Input()` properties that participate in computed signals to Angular `input()` function calls:
- `@Input() disabled` → `readonly disabled = input(false)`
- `@Input() agentColor` → `readonly agentColor = input('coder')`
- `@Input() instanceStatus` → `readonly instanceStatus = input<InstanceStatus | null>(null)`

Also need to update templates to call signals as functions (e.g., `instanceStatus()` instead of `instanceStatus`).

## Detection Pattern
If a computed signal depends on a component input and doesn't re-evaluate when the input changes, check if the input uses `@Input()` instead of `input()`.

## Additional Fix: Direct Navigation
When navigating directly to `/instances/{id}`, the fetched instance was never added to `instanceService.instances()`. Added insertion after API fetch in `handleInstanceIdChange()`.
