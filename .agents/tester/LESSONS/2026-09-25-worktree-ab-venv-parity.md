# Lesson: Worktree A/B legs + tests that shell out to a relative `.venv/bin/pytest`

**Date:** 2026-09-25
**Gate:** mission-terminal watch/report-publish (fix/mission-terminal-watch-report-publish @ fe9692ad, base db71500a)
**Discovered by:** base-leg worker `79294c14`; disposed by parity worker `27c506eb`

## Symptom

Full-dir A/B on `tests/job_queue/`: 3 base-only reds in
`tests/job_queue/test_f1_killswitch_tz_matrix.py::TestRecoveryServiceBothStateParity`
(`test_recovery_33p_passes_under_switch_on` / `…_switch_off` / `test_recovery_pass_count_identical_both_states`)
with `FileNotFoundError: '.venv/bin/pytest'` — while the same 3 tests PASS on the feature leg.

Initial read could be mistaken for "feature fixed 3 pre-existing failures". It is not.

## Root cause

Those tests shell out to a **relative** `.venv/bin/pytest` subprocess. The base leg ran in a detached
git worktree (`/tmp/abgate-base-db71500a`), which has no local `.venv` (worktrees do not carry
untracked/ignored files). The feature leg ran in the main checkout, which has `.venv`. The asymmetry
is a property of the **gate protocol**, not of the code under test.

Same defect class as the quarantined b1_wc hardcoded-cwd test (QUARANTINE.md): tests baking
environment paths instead of deriving them (`Path(__file__)`-relative repo root, or resolvable venv).

## Fix (gate-protocol level, no code change)

Parity symlink inside the worktree, run, then remove:

```bash
cd /tmp/abgate-base-db71500a
ln -s /home/nea/ensemble-src/.venv .venv
PYTHONPATH=/tmp/abgate-base-db71500a timeout 300 /home/nea/ensemble-src/.venv/bin/pytest \
  tests/job_queue/test_f1_killswitch_tz_matrix.py -q --tb=short -p no:cacheprovider
rm /tmp/abgate-base-db71500a/.venv   # restore pristine worktree
```

Result: **29 passed** (the 3 previously-FileNotFound tests pass at base) → base-only reds disposed
as environment artifacts; A/B table stays honest (0 feature-only, 0 base-only after disposal).

## Rule for future A/B gates

1. Any base-only red whose error mentions a missing path/file that exists in the main checkout
   (`.venv`, `dist/`, data dirs) is a **worktree artifact suspect** — re-run at base with a parity
   symlink/disposition before adjudicating it "fixed by the branch".
2. Keep the parity fix OUT of the repo (symlink only, remove after) — the worktree must stay
   byte-clean for any later per-failure re-runs.
3. Long-term test-side fix (separate commission): make the killswitch tz-matrix tests derive the
   pytest binary from the environment (`sys.executable` or `Path(__file__)`-anchored venv) instead
   of a literal relative path.

## Related

- QUARANTINE.md consolidated row (2026-09-25) lists the 16 both-red families from the same gate.
- RESULTS/2026-09-25-mission-terminal-watch-report-publish-ab-gate.md §1 rows 17–19.
