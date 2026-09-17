# 2026-09-17 — Large fan-out dispatch saturates the daemon QueuePool; and shared-worktree drift gates need a descendant-commit clause

## Context
LCA Stage-3 gate (`feature/lca-resolver-stage3` @ f8e78a40): 32 worker dispatches, including a 21-way parallel matrix fan-out against the shared worktree `agents-ensemble-wt-lca-stage3`.

## Finding 1 — send_message QueuePool saturation (ensemble self-system)
Firing 21 `send_message` calls in rapid parallel batches saturated the daemon's SQLAlchemy QueuePool (size 5 + overflow 10): 6 sends returned `TimeoutError('QueuePool limit ... connection timed out, timeout 30.00')` / `Failed to register message correlation (dependency_bus)`.

**Critical nuance**: the errors were POST-ENQUEUE correlation failures — every "errored" message actually landed. Verified via `get_instance_info`: `initiative_message` set (delivered) or Pending:1 (queued for the 5-slot worker pool). The retry guard ("already has a message in progress. Pending: 1") makes retries duplicate-safe.

**Lesson**: on large fan-outs, batch sends (≤5-8 per wave); on TimeoutError responses, CHECK instance queue state before re-sending — the guard blocks true duplicates, but blind re-sends waste a cycle. WORKER_POOL_SIZE=5 also throttles how fast spawned children actually start.

## Finding 2 — shared-worktree drift gates must allow descendant test-only commits
Workers in a multi-worker gate commit test-only artifacts to the SAME branch concurrently (packs, pins, zoo). A drift guard phrased "HEAD != <expected> → STOP" halts legitimate later workers (2 stops in this gate: lca3-bred, lca3-budget). Both were correct per instructions and resumed after adjudication.

**Lesson (dispatch phrasing)**: for shared-worktree gates, phrase the guard as: branch matches AND `git merge-base --is-ancestor <anchor> HEAD` AND `git diff --name-only <anchor>..HEAD -- daemon/` EMPTY. That admits sibling test-only commits while still hard-failing any production drift or wrong branch. Workers should never be offered a `git reset --hard` option on a shared worktree.

## Finding 3 — pre-flight drift checks by EVERY pack worker are cheap and caught nothing (this time) — keep them anyway
All 21 pack workers verified branch+HEAD before running; zero drift at pack-execution time (the interleaved commits landed only during long authoring tasks). The check costs one git call; keep it as standard.

## Finding 4 — live-LLM verdict stochasticity is a flake CLASS for rescue-semantics tests
`test_attestation_revive_after_escalation` intermittently reds at ANY commit when the real judge answers `verdict=complete` on the deny band (rescue → allow → escalation flag unwritten). Tests asserting the deny-band escalation flow must hermetically pin the judge verdict (stub at `_invoke_judge_llm`) — the live path is nondeterministic by design (judge-as-rescuer).
