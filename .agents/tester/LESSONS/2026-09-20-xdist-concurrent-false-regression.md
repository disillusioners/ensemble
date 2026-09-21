# Lesson: xdist + concurrent sibling pytest ⇒ false "regressions" vs base

Date: 2026-09-20
Context: fix/empty-job-completed-event verification (614ab41f vs base 997c670d)

## What happened
A full `tests/job_queue` run with `-n auto` (xdist), executed WHILE a sibling worker ran another pytest pack in the same checkout, flagged 7 tests as regressions (pass at base in single-process subset run, fail at fix under xdist+concurrency).

## Resolution pattern (worked, reuse it)
1. Isolation re-run of just the flagged containers: single process, NO xdist, NO sibling pytest, 3× retry budget, no code changes. All 7 passed 3/3 → execution-environment artifact, not regression.
2. Provenance guard for base comparison in a worktree: the main `.venv` carries an editable install (`_editable_impl_ensemble.pth → /home/nea/ensemble-src`), so a worktree pytest would silently import the FIX's daemon while running BASE tests. Always verify `import daemon; daemon.__file__` resolves inside the worktree (fresh `uv sync` venv in the worktree is the reliable fix).
3. Traceback attribution for remaining consistent failures: compare frames against the diff's changed-file set; a failure living entirely in `daemon/tools/inner_soul.py:824` cannot come from a diff that doesn't touch it.

## Takeaways
- Never adjudicate regressions from a confounded comparison (parallel mode + subset-vs-full + concurrent siblings all differ).
- Concurrent pack workers in one checkout: fine for disjoint scopes, but any FAIL verdict must be re-confirmed in isolation before reporting.
- Base comparison "PASS" from a single-process subset run is not evidence against an xdist-mode failure — compare like-for-like or use isolation to break the tie.
