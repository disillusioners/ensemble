# LESSONS — Defer-Gate PG Hotfix Gate (2026-09-04)

Branch `hotfix/defer-gate-pg-ambiguous-param` @ `693a4ffc` (+gate commit `348a6edc`), base `2f80d45b`.
Full report: `RESULTS/2026-09-04-defer-gate-pg-hotfix-gate.md`.

## 1. Dispatched worker can land IDLE-never-executed; a single kick recovers it
**Observed:** two workers (deadlock pack, reg-base leg) showed status IDLE with 0 queued messages after spawn+send — turn never ran, no report, no error. Not a crash: the message was delivered but the loop never picked it up.
**Fix pattern:** re-send the FULL task contract to the same instance once (send_message to IDLE → ENQUEUE → wakes the loop). Both recovered and executed correctly on the kick. This counts as the single fan-in escape-valve attempt; a second failure would have meant replacement spawn.
**Rule of thumb:** before assuming a worker is "slow", check `subtree_status` — IDLE + 0 queued + no report = never started, kick; RUNNING/age growing = actually slow, wait.

## 2. Mutation-kills in a SHARED worktree race sibling pack runs
**Observed:** the post_settle worker temporarily mutated `daemon/repositories/job_queue/_idle_predicate_sql.py` (re-injecting the collapsed shape) to kill the static guard while the HEAD regression partition was concurrently running in the same worktree. No contamination materialized (the static-guard test passed inside the concurrent partition run — the mutation window was ~seconds), but the race was real: a partition run overlapping the window would have recorded a phantom failure.
**Rule of thumb:** schedule mutation-kills/worktree-mutations either (a) in a private throwaway worktree, or (b) strictly after sibling packs in that worktree have reported. If a concurrent partition shows a failure in the mutated file's test surface, re-run that partition after proving the tree clean before trusting the inventory.

## 3. Ambiguous acceptance counts → resolve by SUPERSET, not by guessing
**Observed:** the gate handoff quoted "seam trio (68+16+12+13)" but inventory found three files collecting exactly 12 and several collecting 13 (and the ledgered 13-count file had grown to 45). Guessing one file risks silently dropping acceptance coverage.
**Rule of thumb:** when handoff counts are ambiguous at inventory time, run ALL matching candidates in one pack (they're tiny) and report per-file counts. Over-inclusion costs seconds; under-inclusion loses evidence. Reconcile the mapping in the report; flag ledger drift (here `test_dead_letter_service.py` 13→45) for rebaseline.

## 4. PG legs: distinguish "the pin executed" from "a sibling skipped for env"
**Observed:** the PG incident pin passed on a self-provisioned disposable schema, while its legacy sibling parity test skipped because the `docker-compose.test.yml` public-schema stack wasn't running. A blunt "any skip = FAIL" rule mislabels this; the sibling's skip is loud (`-rs` prints URL + remedy) and was accepted by the prior gate for the same reason.
**Rule of thumb:** gate on the SPECIFIC pin's execution (test id PASSED, not SKIPPED), require all skips to be loud (`-rs`), and adjudicate env-conditional siblings against prior-gate precedent — document, don't hide. Provision schema freshness yourself (DROP IF EXISTS + absence proof before AND after) so the pin provably depends on no leftover state.

## 5. Pack banner strings are baked at authoring time — rev-parse is the authority
**Observed:** `defer_gate_runtime_matrix_test.sh` printed `Branch: fix/defer-gate-post-settle-window @ b46c9f8b` — a header string from the commit that authored the pack, not the running worktree state. Treat banner text as decoration; the worker-side `git rev-parse` before each invocation is the provenance record.
