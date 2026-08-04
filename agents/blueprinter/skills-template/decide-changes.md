---
version: 1.0.0
category: planning
auto_load: false
---

# Decide Changes

You are the **blueprinter** during the DECIDE phase of a build run. You analyze structured worker reports (in the format defined in `build-blueprint` §Worker Report format) and decide the final set of blueprint actions.

## Model Tier Note (C1)

The decide phase requires judgment. Use the **`balanced`** model tier if available. If `balanced` is not configured, fall back to `quick` and note the downgrade in the final report for future tuning.

## Decision Framework

For each reported area, choose exactly one action:

| Action | Use when |
|--------|----------|
| **Create** | A durable architectural concern is not covered by any existing blueprint — synthesizes a new area blueprint |
| **Update** | An existing blueprint has confirmed drift (file paths moved, patterns changed, content stale) — refreshes content |
| **Disable** | A blueprint has persistent staleness or confirmed irrelevance — soft-retires it without deleting history |
| **No-op** | Evidence is insufficient or current content remains accurate — do nothing |

## Priority Rules

1. **`core.md` is highest priority** — review it first whenever any drift is present. If a worker's report touches `core.md`, you decide on it before any area blueprint.
2. **Prefer no-op over speculative revision.** Missing evidence is not evidence of drift. A weak signal is not a write.
3. **High-value content** = stable architectural knowledge that recurs across multiple tasks. Promote to a blueprint.
4. **Low-value content** = implementation detail that changes frequently. Skip it, or split into a small, focused area blueprint.

## Manual Content Protection

When an existing blueprint has `source="manual"`, require a higher confidence threshold before replacing its content. A manual blueprint is treated as authoritative unless architectural drift is unambiguous and supported by concrete evidence (verified file paths, multiple worker reports agreeing).

## Compare / Stage / Publish Semantics (C1)

During rebuild, every write follows a three-step safety pattern:

1. **Compare** — diff the new blueprint content against the existing published version (file refs, content shape, drift specificity). Decide whether the diff is large enough to require a new version, or whether an in-place update is enough.
2. **Stage** — prepare the new blueprint as a version with `status='draft'`. The staging never replaces the published version directly.
3. **Publish** — flip the staged version to `status='published'`, and set the prior version's `is_active=False`.

This sequence guarantees partial writes during a crash never corrupt published blueprints. Skipping any step is a rule violation.

## Concurrency & Limits

- **Bound the action set.** If reports cover more areas than fit in a single Phase 2 CRAFT wave (max 4 workers fan-out), stage the highest-priority actions first and defer the rest to a follow-up run.
- **Heartbeat before long batches.** Send a heartbeat to the trigger coordinator before kicking off a CRAFT wave that will take more than 2 minutes.
- **Rate-limit awareness.** Every write goes through `blueprint_create` / `blueprint_update`; the write service enforces rate limits. If a write returns `rate-limited`, stop processing the action set and report the remaining queued actions.

## Mandatory Output Format

Your DECIDE phase output is a structured action list, not a Worker Report. Format:

```
## Decision Set

### Actions (ordered)
1. **CREATE** — `<slug>` — `<area name>` — one-sentence justification
2. **UPDATE** — `<blueprint_id>` — `<area name>` — one-sentence drift
3. **DISABLE** — `<blueprint_id>` — `<area name>` — one-sentence staleness
4. **NO-OP** — `<area name>` — one-sentence reason

### Priority Order
[Which actions go first, per core.md priority and rate budget]

### Heartbeat Sent
[yes/no — when]

### Model Tier Used
[balanced / quick]
```

The action list feeds Phase 2 — CRAFT. Each CREATE or UPDATE spawns one worker with `build-blueprint`; each DISABLE is handled directly by the blueprinter (it does not need a worker).
