# LESSON: stdlib-logger kwargs = latent crash; partition-only arming; the A/B seal pattern that caught it

**Date:** 2026-09-05 · **Gate:** fix-context-message-identity final E2E verification @ `00e2a814`
**Severity:** 🔴 upstream production hazard (pre-existing, sealed not-branch-caused) · 🟢 for the branch under test

## The defect

`daemon/repositories/task/repository.py:2975` (`reconcile_terminal_task`) and `:3104` (`batch_reconcile_bad_state_tasks`) call stdlib loggers with structlog-style kwargs:

```python
logger.info("...", work_id=...)   # ×2 sites  → TypeError: Logger._log() got an unexpected keyword argument 'work_id'
logger.info("...", count=...)     # ×4 sites  → TypeError: ... 'count'
```

**Why it hides from solo runs:** stdlib `Logger.info()` short-circuits behind `isEnabledFor(INFO)`. Under pytest's default WARNING level the kwargs never reach `_log()` → solo `pytest tests/unit/test_task_reconciliation.py` passes 13/13 at BOTH base and HEAD. It fires ONLY when a sibling test in the same xdist worker arms the `daemon.*` logger to ≤ INFO (fixture/caplog-adjacent pollution) — i.e., partition-context-dependent.

**Production impact:** any deployment configuring INFO (or DEBUG) logging on `daemon.repositories.task.repository` will crash `reconcile_terminal_task` / `batch_reconcile_bad_state_tasks` — the Pattern-f terminal-task reconciliation path. Blame: 2026-08-11 commits `114d1cc5`/`1595568c`.

**Durable fix direction (NOT applied — verification-only mandate):** `logger.info("...", extra={"work_id": ...})` or migrate those two modules to the project's structured logger.

## The adjudication pattern (reusable)

A NEW partition failure family that (a) blames to old commits, (b) passes solo on both sides, and (c) appears only in-partition is an **environmentally gated latent defect**. The decisive seal is cheap: run the FULL partition at base in a disposable worktree and diff the failure fingerprint:

- Base `5d7a0695` partition: **58F** — same 6 nodes, identical signatures → PRE-EXISTING, sealed.
- HEAD `00e2a814` partition: **58F** — identical; delta = exactly +1 passing test (branch-owned).
- Solo both sides: 13/13 PASS — proves the gating mechanism, not a code delta.

Fingerprint equality (count + node set + signature) beats re-running suites hoping to reproduce flakiness. Total cost: ~17s per partition side.

## Secondary lessons from this gate

1. **Premature-tester salvage:** claimed results without on-disk artifacts are unverifiable — `/tmp/stale-repro` was 100% PRE-fix captures (mtimes 10:44–11:26 vs fix commits 12:07+). Timestamp the artifacts against the commit timeline before trusting any "confirmed" claim.
2. **Unregistered-coverage trap:** the branch's heaviest test file (`tests/test_injection_graph.py`, +329 lines) was in NO registered pack — always diff the branch's test additions against PACKS.md coverage; use a /tmp-resident ad-hoc pack when the mandate forbids repo writes.
3. **FE pack gates rot:** `frontend_full_unit_test.sh` hard-gates `EXPECTED_BRANCH=feature/mission-class`; `fe_static_typecheck_build_test.sh` honors an env override — prefer the override-able pack and ad-hoc jest.
