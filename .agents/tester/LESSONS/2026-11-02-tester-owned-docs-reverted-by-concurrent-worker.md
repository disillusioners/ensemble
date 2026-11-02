# Tester-owned docs were reverted mid-gate by a concurrent worker's `git checkout --`

**Date:** 2026-11-02 · **Commission:** central-metrics-v1 tester functional gate · **Severity:** important (recoverable, but it silently destroyed gate prerequisites)

## What happened

While the tester functional gate was running, the tester (leader lane) wrote and saved two
tester-owned artefacts directly — as the role mandates:

- `.agents/tester/PACKS.md` — the central-metrics-v1 gate-pack registration (the contract's
  `gate-pack-ids` block said the file carried **0 of nine**; the registration closed that).
- `.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md` — the
  R-v HOUSE CANON section (LC_ALL=C on both `sort` and `comm`, files-first→log, FORM tags,
  FAIL-LOUD empty-verdict).

Neither was committed. A dispatched worker running the FE dual-side pack then observed them as
tracked-file dirt and restored them with `git checkout -- <file>` (the pack's own hygiene step,
installed because a pytest run appends to the git-tracked
`.agents/shared/planning/designer-agent/install-audit.jsonl`). `git checkout --` reverts a tracked
file to HEAD — so **both artefacts silently reverted to their pre-gate state**. The registration
block vanished; the canon vanished. Only the UNTRACKED `sweep_digest_verdict.sh` survived, because
`checkout` cannot touch untracked files.

It was caught only because the worker's report happened to enumerate what it restored. A terser
report would have left the gate running against an unregistered pack set with no canon — and the
final report would have claimed the registration existed.

## Root cause

Two independent rules collided:

1. **Role rule:** the tester writes `.agents/tester/**` directly (it owns that tree) — so it
   naturally produces uncommitted edits while it plans and dispatches.
2. **Repo hygiene rule:** workers restore tracked-file dirt with `git checkout --` (correct, and
   required by the install-audit side-effect).

An uncommitted tester artefact is therefore **indistinguishable from stray dirt** to every
concurrent worker. Ownership does not confer protection; only the git index does.

## The rule (adopt)

> **Any `.agents/tester/**` artefact that a running gate depends on MUST be committed BEFORE the
> first gate worker is dispatched.** Uncommitted tester docs are not "work in progress" on a shared
> worktree — they are dirt that the next worker will delete.

Practical form:

- Write → verify → **commit** (targeted `git add --`, never `git add -A`) → *then* dispatch.
- If a worker must run while a tester doc is still being edited, tell it explicitly which paths are
  tester-owned and MUST NOT be reverted — but treat that as a weak second line of defence, not the
  primary control. The commit is the control.
- When a worker reports a `git status --porcelain` containing `.agents/tester/**` that YOU authored,
  treat that line as an alarm, not a detail.

## Recovery (for the record)

The PACKS.md block was recoverable because its source text had been staged to `/tmp` before the
write; the recipe section was re-typed from the session transcript. Both were then re-applied and
committed in the same sitting. Total loss: one re-application cycle, no gate result invalidated
(the FE legs' per-leg results were captured before the revert and are unaffected by a docs-only
file).

## Generalisation

This is the same class as the repo's older lesson about **shared-worktree collisions**: a shared
checkout is a shared mutable resource, and "I own this file" is a convention, not a lock. Any agent
that writes into a shared worktree must make its writes durable (commit) before another agent's
hygiene pass runs.
