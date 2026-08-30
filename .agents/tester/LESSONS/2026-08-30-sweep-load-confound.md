# Lesson: Full-suite sweep load-confound — concurrent packs can fabricate "regressions"

**Date:** 2026-08-30 (governor-recursion-guard gate)
**Trigger:** Sweep partition SW10 (opencode + message_queue_redesign + repositories + integration + e2e, `-n 4`) executed while two sibling workers ran packs concurrently (concurrency_atomic pack + PG slice).

## Symptom
At HEAD under load, SW10 showed:
- 10× setup `TypeError: object.__new__() takes exactly one argument` across `tests/integration/test_vscode_routing_e2e.py` + `test_vscode_security_integration.py` (embedded FastAPI/uvicorn startup inside fixtures)
- 6× flaky F/ERROR flips in the same vscode cluster
- `test_update_activity_concurrent_does_not_clobber_terminal_status` PASS→FAIL flip under `-n 4`

At BASE 6ba8da82 (isolated) these were ALL absent — initially mis-read as "branch regression".

## Root cause
Machine-load sensitivity, not code. The vscode fixtures spin up embedded daemons; under concurrent sibling pytest load, startup races produce the `object.__new__()` TypeError class. The base-vs-head comparison was **confounded by unequal load**: HEAD ran loaded, base ran isolated.

## Fix protocol (worked here — reuse it)
When a base-worktree comparison implicates the branch, run the **discriminating experiment**: re-run the SAME cluster at HEAD **isolated** (no sibling workers, sequential invocations), serial AND parallel.
- Reproduces at HEAD isolated → regression confirmed (base was clean under identical conditions).
- Vanishes at HEAD isolated → load-induced infra-flake; void the attribution; document.

## Prevention
- Gates with ≤3-concurrency budget: prefer scheduling embedded-daemon partitions (tests/integration vscode, e2e) so they run WITHOUT sibling pack load, or treat their setup-error clusters as load-suspect by default.
- Never conclude REGRESSION from a single loaded-vs-isolated asymmetry.

## Related
- SW6 race: a sweep glob (`test_[a-h]*` matches `test_governor...`) picked up another worker's IN-FLIGHT uncommitted test file → 13 phantom failures. Rule: when authoring workers may commit mid-gate, either sequence sweeps after authoring completes or exclude known in-flight paths.
- Base worktree triage pattern (feature `.venv` interpreter + worktree CWD) is cheap and reliable — kept worktrees at distinct paths per worker.
