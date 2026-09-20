# Lesson: `-m integration` deselects UNMARKED integration-dir tests; sibling gate commits break strict HEAD pins (2026-09-20 LCA attest-first gate)

## Context
Merge gate for `feature/lca-attest-first-contract` (RESULTS/2026-09-20-lca-attest-first-contract-merge-gate.md). Two dispatch-authoring defects surfaced by workers, both adjudicated correct by the workers.

## 1. Marker ≠ directory
- pyproject `addopts = "-m 'not integration and not postgres'"` deselects by MARKER, not path. `tests/integration/test_attestation_attest_first_e2e.py` (the evidence tests) carries NO integration marker — a dispatch literal `--override-ini="addopts=" -m integration` selected only **13/229** of the integration-dir cohort and would have SILENTLY SKIPPED the evidence tests.
- Correct pattern for unmarked integration-dir files: `--override-ini="addopts="` WITHOUT a marker filter (run the explicit file list), or rely on the pre-existing `lcau_matrix_intf_*` sub-packs which encode per-tier pytest-timeout overrides (f 30s / m 120s / s1-s3 240s / ints 150s).
- Evidence tests need `--override-ini="timeout=120"` (default 30s kills them; ~21s each).

## 2. Strict HEAD-equality pins break under parallel gate workers
- Three wave-3 workers held "STOP if HEAD != 0b8f4de2" while a wave-3 sibling committed test-only `01d0e3a9` mid-flight → false-stop risk. Fixed by injected pin-update.
- Correct pin for multi-worker gates: accept HEAD iff `git merge-base --is-ancestor <delta-tip> HEAD` AND `git diff --name-only <delta-tip>..HEAD` ⊂ tests/ (or ⊂ .agents/tester/ for docs workers). Stop only on ancestry failure or foreign paths.
- The repo's own convention (h) anticipated this for RESULTS commits; it applies to ALL gate-owned commits.

## 3. Boot-smoke env wrinkles (from the mock LLM boots)
- Parent-shell `OPENAI_REQUEST_GZIP=true` gzips request bodies to a stdlib mock server → mock decode crash; override explicitly in the boot script env.
- Parent-shell `OPENAI_BASE_URL_BACKUP` triggers redundant failover probes; unset it.
- Leader-vs-child mock routing: fingerprint by (model, tools-count) — leader 62 tools vs worker ~15 — more stable than system-prompt content (langgraph injects per-turn system messages).

## Follow-ups
- Registry pass for PACKS.md (concurrency_atomic_unit_test + lcau_matrix_* lack entries).
