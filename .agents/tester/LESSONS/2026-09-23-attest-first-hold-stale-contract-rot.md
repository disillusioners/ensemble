# Attest-first HOLD stale-contract rot — latent red class (2026-09-23)

## Context
LCA check-note removal merge gate (feature/lca-remove-check-note @ ff9eb849, base 6bf7bed7). The full attestation matrix surfaced 3 unexpected reds; all three were base-proven PRE-EXISTING via same-session A/B adjudication — zero branch-caused.

## The class
Tests written against the OLD "attest → immediate plain-allow (R4 short-circuit)" contract break under the attest-first HOLD contract (b0a4f21d + ship-together f00775bb/0b8f4de2/213b333e): after a successful attest, a short/bundled final AI gets `decision=hold` + a counter-independent Final Report Reminder injection (graph.py:5831 at tip, :5984 at base), which requests ONE MORE LLM turn than pre-attest-first fixtures scripted.

Two failure signatures, same root:
1. **Assertion red**: test asserts `messages` NOT in the attested-turn result; actual carries `attestation_final_report_reminder:<iid>` (test_lcan_legacy_checkpoint s1).
2. **ScriptedChatModel exhausted**: fixture scripts 4 turns; HOLD reminder demands a 5th (test_lcan_childlie_e2e s1, test_lcau_incident_e2e scenario_b).

## Why latent
`test_lcan_childlie_e2e.py` is `-m integration`-marked and DESELECTED by default addopts (`-m 'not integration and not postgres'`). Dev lanes that run default addopts never execute it → the rot shipped invisibly. Any gate that wants true full-surface attestation coverage MUST include an addopts-override pack for integration-marked files.

## Adjudication recipe (what worked)
1. Detached throwaway worktree at base (`git worktree add --detach /tmp/<name> <base>`; NEVER the shared gate worktree).
2. Prove the test file untouched: `git diff <base> <tip> -- <file>` EMPTY (or, for childlie, the touched hunk provably excludes the failing test).
3. Same-session A/B, same invocation shape (env scrubs, addopts override, timeout wrappers): node-level at base AND tip.
4. Determinism check: solo re-run ×2 at base (distinguishes rot from flake).
5. Full-pack-shape parity when cheap (pack-6 tallies byte-identical 2F/28P/2S both sides).

## Durable fix (test-side commission, not production)
Re-anchor the 3 tests to the attest-first contract: expect HOLD + reminder after attest when the final AI is not a standalone ≥150w report; extend ScriptedChatModel fixtures by one turn (the post-reminder report turn).

## Side lessons
- **Blueprint convention (f) drift**: `ENSEMBLE_TEST_PG_URL` is NOT the daemon's postgres-vs-sqlite auto-detect input — the daemon reads `POSTGRES_HOST+POSTGRES_DB` (ensemble_config.py:82/124-135). Disposable-PG daemon boot = explicit `POSTGRES_*` + `env -u` scrub of the inherited prod/dev values. Reconcile the blueprint.
- **Pack estimate calibration**: mock-unit slices run 40× faster than splitter estimates (467 tests in 4.1s) while integration packs run 2.5–6× SLOWER (cold-import + fixture bring-up); set inner deadlines from prior ACTUALS, not probe heuristics, and keep ≥30s margin under the 300s outer cap (matrix-5 quick-fix afdee41f).
