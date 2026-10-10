# Architecture-kill branches must sweep dependent test asserts beyond the owned files

**Date:** 2026-10-10 · **Commission:** designer-critic-orchestration final gate @ 35d7e9c5d
**Pattern:** When a branch deliberately kills an architecture (here: designer's `od.*` tool grants, removed by user directive), every test that ASSERTS the old architecture becomes red at merge time — even when the branch's own verification surface is 100% green.

## What happened

- The plan's phase-4 owned-test-task correctly reworked the two commissioned test files (`test_sketcher_agent.py`, `test_designer_rewire.py`) and fenced all other test edits behind "hard-escalates non-workflow edits elsewhere".
- `tests/unit/plugin_subsystem/test_tier1_wiring.py` (NOT in the branch's 58-file inventory) still asserted designer `tools.allow` binds all 4 `od.*` Port tools (`test_designer_shaped_allow_resolves_all_four` :215, `test_designer_allow_includes_all_four` :337). Both went red the moment phase-1 (`a29ae3391`) landed — provably stale (premise removed by directive), but branch-caused red on the neighbor suite, and no escalation or deferred-debt entry existed for it.
- The plan's own §7 SC list and the verification surface did not include this file — so the branch's gates stayed green while collateral red accrued silently.

## Lesson (for future architecture-kill commissions)

1. **When the plan removes capability X from agent/file A, grep the WHOLE test tree for tests asserting X on A** (`grep -rn "od\." tests/` equivalents) at plan time, not just the owned files — every hit is either an owned-file edit, an explicit escalation, or a deferred-debt entry. None of the three may be silent.
2. **Verification surfaces should include a "dependent-assert sweep" gate** beyond the two commissioned files — the cheap form is one grep + one scoped pytest of the containing directory, exactly what the final-gate neighbor run did here.
3. **At final gates, classify neighbor failures three ways:** pre-existing (base-proven families), environmental (worktree/interpreter shaped, file untouched by branch), branch-caused (stale asserts). Only the third is a merge-adjudication item; the first two must not be silently counted either way. Evidence discriminator: the branch's changed-file inventory (`git diff --name-only base..HEAD`) — a failing test file absent from the inventory + asserting removed architecture = stale assert, not a code defect.
4. **Environmental corollary:** probes/tests hardcoding `.venv/bin/python` break in worktrees without their own venv (seen: `test_sync_runner.py:1663`). Interpreter resolution should go through an env/derived base, not a relative literal.

## Disposition

N1/N2 escalated to leader at the final gate with two options (small test-only follow-up commit pre-merge vs explicit deferred-debt entry). Recorded in RESULTS/2026-10-10-designer-critic-orchestration-final-gate.md § Action Needed.
