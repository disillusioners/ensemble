# Vocabulary bump ⇒ numeric-pin census (prompt-reload gate, 2026-11-07)

## Trap
Any lane that bumps `VOCABULARY_VERSION` in `daemon/checkpoint_metrics.py` must ALSO update the two NUMERIC assertion pins in `tests/unit/metrics/`:
- `tests/unit/metrics/test_metrics_vocabulary_amendment.py:32` (`assert cm.VOCABULARY_VERSION == N`)
- `tests/unit/metrics/test_metrics_family_adjudication.py:440` (+ both docstrings that narrate the bump history)

These sites are NOT part of the privileged-pin census file set and are easy to miss — the artifact pack (`test_tool_category_artifact.py`) catches the ARTIFACT drift but not the raw-constant pins.

## Occurrences
- prompt-reload lane (commit `9fbb3ad46`, 6→7): MISSED both → 2 deterministic net-new reds at the functional gate (GO-WITH-DISCLOSURES pre-stage condition; `git diff base..HEAD -- tests/unit/metrics/` was empty, proving the census gap).
- memoir lane (`b73805dc4`, 5→6): caught them in-lane (the precedent for the fold shape).

## Rule
Checklist item for any vocabulary/artifact version bump: `grep -rn "VOCABULARY_VERSION ==" tests/` — every numeric site rides the bump in the SAME commit family as the constant change. Gate-side: treat unexplained `assert N == N±1` reds in `tests/unit/metrics/` as the FIRST hypothesis when a lane bumped the vocabulary.

## Second lesson (same gate): stale KV ≠ ground truth
The `prompt-reload.scratch-seam` KV row recorded the harness at `8f2a99b50`; the actual pre-state was `711199c8` (one commit further — itself a stale-docstring fix for THIS commission). Always re-derive worktree HEAD with `rev-parse` before mutating; never act on a recorded pin alone.
