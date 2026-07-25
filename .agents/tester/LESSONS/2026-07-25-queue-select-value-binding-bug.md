# Queue Selector Dropdown — Multiple Bugs Found and Fixed

**Date:** 2026-07-25
**Feature:** queue-select-message (branch `feature/queue-select-message`)
**Bugs Found By:** Web automation E2E worker (browser-based, real Angular runtime)
**Fixed By:** Two quick-fix workers (commits `fd876cfb` + `2567af9e`)
**Severity:** 🟡 Medium (UX: wrong default + lost selection on reload; backend unaffected)

## Summary

Three layered bugs (one template + one TS + one caused by the first fix attempt) were
discovered only by browser E2E testing. Unit specs (using a stub `TestMessageInputComponent`)
and `ng build` (template type-checking) missed both.

## Bug #1: `<select [value]>` binding doesn't reflect selectedQueueId due to async options

**File:** `frontend/src/app/components/message-input/message-input.html` (lines 126-128)

Angular's `[value]` property binding on `<select>` evaluates BEFORE the `@for` loop
renders the `<option>` elements (queues are fetched asynchronously). The browser falls
back to the first `<option>`.

**Fix (commit `fd876cfb`):** Added `[selected]` binding per `<option>`:
```html
<select (change)="onQueueChange($any($event.target).value)">
  @for (q of queues(); track q.queue_id) {
    <option [value]="q.queue_id" [selected]="q.queue_id === selectedQueueId()">{{ q.queue_name }}</option>
  }
</select>
```

## Bug #2: Default signal initialization uses NAME where UUID is expected

**File:** `frontend/src/app/components/message-input/message-input.component.ts` (lines 116, 124-127)

The `selectedQueueId` signal was initialized to the literal string `'system_parallel_queue'`
(a queue NAME), but the lookup compared it against `queue_id` (UUID). The comparison never
matched, so the default fell back to the first queue (`system_background_queue`).

**Before:**
```ts
// Line 116
this.selectedQueueId.set(projectId ? localStorage.getItem(`ensemble-queue-select-${projectId}`) || 'system_parallel_queue' : null);

// Lines 124-127
const selected = response.queues.some(q => q.queue_id === stored)
  ? stored
  : response.queues.find(q => q.queue_id === 'system_parallel_queue')?.queue_id ?? response.queues[0]?.queue_id ?? null;
```

**Fix (commit `2567af9e`):** Match by `queue_name` and remove the broken fallback:
```ts
// Line 116
this.selectedQueueId.set(projectId ? localStorage.getItem(`ensemble-queue-select-${projectId}`) : null);

// Lines 124-127
const selected = (stored && response.queues.some(q => q.queue_id === stored))
  ? stored
  : response.queues.find(q => q.queue_name === 'system_parallel_queue')?.queue_id ?? response.queues[0]?.queue_id ?? null;
```

## Why Unit Tests Missed Both Bugs

1. The Jest spec (`message-input.component.spec.ts`) uses a **simplified
   `TestMessageInputComponent`** stub — it tests component logic, not the real
   template DOM. The `[selected]` binding is a template-level concern that only
   manifests at runtime in a real browser.
2. `ng build` (template type-checking) passes because the bindings are
   type-valid — they just don't work at runtime.
3. The `queue_id` vs `queue_name` mismatch is a logic bug that the stubbed
   test component doesn't exercise (the stub replaces the effect).

**Lesson:** For Angular `<select>` + dynamic `<option>` patterns AND for signal
effect logic that depends on async-fetched data, **browser E2E testing is the only
reliable verification**. Unit specs with stub components cannot catch this class
of bug.

## Patterns to Remember

> When using Angular signals + `@for` to render `<option>` elements inside a
> `<select>`, use `[selected]` per-`<option>` instead of `[value]` on
> `<select>`. The `[value]` binding evaluates before async-rendered options
> exist, causing the browser to default to the first option.

> When matching user-facing identifiers (queue_name, slug) to internal IDs
> (queue_id, UUID), make sure the lookup matches the SAME field type. Don't
> store a NAME and compare against an ID — match by NAME to find the ID.

## Verification Workflow That Found These Bugs

The bugs were found by a 2-stage E2E process:
1. **Initial E2E** revealed the template binding bug (showed `system_background_queue` instead of selected).
2. **Re-verification after template fix** revealed the SECOND bug (TS lookup was also wrong).
3. **Re-verification after both fixes** confirmed all 3 tests PASS.

## Artifacts
- Bug evidence: `test-results/queue-selector-e2e/t3-after-reload.png` (template bug)
- 2nd bug evidence: `test-results/queue-selector-final-e2e/test1-default-system-parallel.png` (before fix #2)
- Final success: `test-results/queue-selector-final-e2e/test1-default-system-parallel.png` (after both fixes)
- Fix commits: `fd876cfb` (template) + `2567af9e` (TS lookup)

