# Memory

## Workflow Patterns

- Planning workflow produces markdown-only deliverables (plans, roadmaps, analysis)
- Implementation workflow produces code changes — always follow with review + test for SMALL scope and above
- **Debug workflow** — for bug reports/errors. Collect full evidence → delegate investigation to coder/tester (NO fix yet) → confirm root cause → fix → verify the ORIGINAL symptom is gone. Never assume the cause from logs or a single explore(). Pass full logs to investigators, not just instructions.
- Sequential invocations are common: Planning first, then Implementation using the approved plan

## Scope Indicators

- "Fix", "Change", "Update" single thing → likely TINY or SMALL
- "Add", "Implement", "Build" feature → likely SMALL
- "Migrate", "Redesign", "Integrate" across modules → likely BIG
- "Rebuild", "Create from scratch", "Platform" → likely HUGE

- Schedule Feature Review & Improvement - BIG scope task
- Project: agents-ensemble (83da04de-a410-4fb5-9e92-251a99d28a52)
- Branch: feature/schedule-review-improve
- 15 issues identified across 4 priorities
- Phase plan: Fix bugs → Harden reliability → Test coverage → Clean up
- Status: Git setup in progress, then Planning workflow