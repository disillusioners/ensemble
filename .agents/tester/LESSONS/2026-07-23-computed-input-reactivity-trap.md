# Lesson: Angular `computed()` + Non-Signal `@Input()` Reactivity Trap

**Date:** 2026-07-23
**Component:** `frontend/src/app/components/agent-switcher/agent-switcher.component.ts`
**Branch:** `feature/searchable-agent-selector`
**Severity:** 🔴 Critical — feature completely non-functional in browser
**Found by:** E2E test (Playwright), missed by unit tests

## The Bug

An Angular component used decorator-based `@Input()` properties inside `computed()` functions:

```typescript
@Input() agents: Agent[] = [];                    // plain property, NOT a signal

readonly selectableAgents = computed(() =>
  this.agents.filter(agent => !agent.system)      // reads plain property
);
```

**Result in browser:** `selectableAgents` always returns `[]` — the `computed()` caches its first evaluation (when `agents` is the default `[]`) and never re-evaluates because no **signal** dependency changed.

## Why Unit Tests Missed It

TestBed's `fixture.componentRef.setInput('agents', AGENTS)` followed by `fixture.detectChanges()` triggers a change detection cycle that forces the `computed()` to re-evaluate. This masks the reactivity gap — tests pass but the real browser doesn't force re-evaluation.

**This is a fundamental limitation of TestBed for testing signal reactivity:** TestBed always runs change detection synchronously after input setup, so it cannot detect cases where a computed reads a non-signal input that would be stale in production.

## Why tsc Didn't Catch It

TypeScript compilation (`tsc --noEmit`) only checks types. Reading a plain property inside `computed()` is valid TypeScript — no error.

## The Fix

Convert `@Input()` decorators to Angular signal `input()`:

```typescript
// BEFORE (broken)
import { Input, Output, EventEmitter } from '@angular/core';
@Input() agents: Agent[] = [];
@Input() selectedAgent: Agent | null = null;
@Output() agentChange = new EventEmitter<Agent>();

// AFTER (fixed)
import { input, output } from '@angular/core';
readonly agents = input<Agent[]>([]);
readonly selectedAgent = input<Agent | null>(null);
readonly agentChange = output<Agent>();
```

All references must be updated: `this.agents` → `this.agents()`, `this.selectedAgent` → `this.selectedAgent()`.

## Pattern to Watch For

**Any component using `computed()` or `effect()` that reads a `@Input()` property (instead of `input()` signal) is a reactivity bug waiting to happen.**

Audit checklist:
1. Search for `@Input()` in components that also use `computed()` or `effect()`
2. Check if the input value is read inside the computed/effect body
3. If yes → convert to `input()` signal input

## Testing Strategy

- **Unit tests alone are insufficient** for signal reactivity — TestBed's forced change detection masks the bug
- **E2E tests (Playwright/browser) are essential** for catching reactivity gaps — they run the real Angular runtime without TestBed's crutch
- This validates the "two pillars" approach: unit tests for logic correctness, E2E/mock tests for real behavior

## Related

- This bug exists in the original component before the search feature was added, but the search feature (`filteredAgents` computed) made it more visible
- The parent `instance-list` component already uses signal inputs correctly — only `agent-switcher` had the gap
