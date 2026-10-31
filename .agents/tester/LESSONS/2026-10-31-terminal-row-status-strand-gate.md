# LESSONS — terminal-row status strand gate (2026-10-31)

## 1. Prep-recipe defects: neutralize by per-leg re-derivation, never trust the handoff doc
The prep worker's RECIPE.md had an internally inconsistent block (§46–50 claimed two files HEAD-only that are present at base; two other paths needed `tests/unit/` vs `tests/unit/services/` corrections). Every gate worker was briefed to `test -f` / `git ls-files` per leg before running. Cost: seconds. Value: two bundles would otherwise have mis-reported "no base comparator". **Rule: handoff recipes are hints; per-leg existence checks are the gate.**

## 2. Driving SQLite-bound pytest pins onto real PG: external driver importing the module's own helpers
`tests/repositories/test_turn_reconciler.py` pins are bound to a file-local in-memory SQLite `engine` fixture; worktrees were read-only so no fixture edit was possible. Working substitute: a driver that imports the TEST MODULE (not the production code), reuses its seed helpers + read-assertions verbatim, and swaps only the engine to a real-PG one (fresh throwaway DB per leg). HEAD 3/3 + BASE 3/3 with an honest explanation of why BASE also passes (the guarded seam predates the lane). Gotchas hit: one DB per scenario (shared DB → `uq_job_locks_slot` collision), and the `tasks`→`task` table-name trap (same one as the v0.27.13 F5 fix class). **Durable fix is native: bind these pins to the PG fixture so the postgres shard covers them.**

## 3. Mutation-proofing negative-contract pins requires adversarial plants, not feature plants
6 of 16 pins pin "must NOT change" contracts (PAUSED-exempt, non-terminal no-write, all-terminal skip, count-failure fails-to-skip, attestation preserved). They legitimately survive T1/T2/T3 removal — flipping them needs plants that make the wrong thing happen (e.g. count-failure → re-arm). Report the un-flipped set explicitly with the plant that WOULD flip each; do not silently count them as mutation-proven. `chokepoint_preserves_attestation_counters` is vacuously satisfied under M-T1 (no revive ⇒ nothing to reset) — compensate with a mechanical citation that the counter increment site is unreachable from the path under test.

## 4. Base-code leg beats base-run when the pin file is new
When the pin suite does not exist at base, the strongest cheap substitute is: private worktree at HEAD → `git checkout <base> -- <production files touched by the diff>` → run the pins → RED with the defect's own signatures → restore with diff-empty proof. This proves the pins bind to the FIX (not to fixture noise) without overlaying files into a shared base worktree. (Same lineage as the b95aca982 logic-revert pattern.)

## 5. Symptom harness: poll status INSIDE the fake graph's astream entry
To prove "turn-start reactivation" (not just end-state), the harness polls the row from inside the fake LangGraph node entry — strictly after the reactivation phase, before turn-end. At BASE this yields the hole's signature mid-turn (`START=completed`), which a post-hoc read cannot distinguish from "no turn ran". Also: leg detection must be STRUCTURAL (inspect the running code for the chokepoint call / new symbols), never path-based, or the base leg errors instead of proving.

## 6. Ambient POSTGRES_HOST survived into worker shells again
Gate D found `POSTGRES_HOST=10.44.0.2` (remote prod host) ambient in the worker environment. The per-variable scrub prefix (never `env -u POSTGRES_*`, which unsets nothing) neutralized it on every run. This is the env-poison family's standing hazard — keep the verbatim prefix in every dispatch, and have PG legs print `current_database()` before anything else.
