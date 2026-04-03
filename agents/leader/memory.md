# Memory

## Workflow Patterns

- Planning workflow produces markdown-only deliverables (plans, roadmaps, analysis)
- Implementation workflow produces code changes — always follow with review + test for SMALL scope and above
- Sequential invocations are common: Planning first, then Implementation using the approved plan

## Scope Indicators

- "Fix", "Change", "Update" single thing → likely TINY or SMALL
- "Add", "Implement", "Build" feature → likely SMALL
- "Migrate", "Redesign", "Integrate" across modules → likely BIG
- "Rebuild", "Create from scratch", "Platform" → likely HUGE

- # Parallel Execution Strategy for Rename Refactors

**Date:** 2026-03-18
**Project:** agents-ensemble

## Strategy: Wave-based parallel execution with max 3 agent instances

### Principles
- Group phases into **waves** based on dependency graph
- Within each wave, spawn up to **3 coders** working on independent phases simultaneously
- After ALL coders in a wave complete, terminate them and run **Reviewer → Tester** sequentially (1 at a time)
- Max 3 agent instances at any given moment

### Wave Pattern
```
Wave: Spawn up to 3 coders → Wait for all → Terminate coders → Spawn reviewer → Wait → Terminate → Spawn tester → Wait → Terminate → Next wave
```

### Why This Works for Rename Refactors
- Independent phases touch different files — no merge conflicts
- Reviewing/testing after full wave completion catches cross-phase issues
- Coders share no state within a wave — true parallelism

### Applicable To
- Any mechanical rename/refactor with clear phase dependencies
- Phases that touch disjoint file sets can always be parallelized

### Max Instance Enforcement
- Count active sessions before spawning new ones
- Never exceed 3 active sessions total
- If a coder finishes early in a 3-coder wave, can replace with reviewer for completed phases (pipelining)
