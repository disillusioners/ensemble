# Lesson: Unregistered Feature Test Suites (PACKS.md integrity gap)

Date: 2026-08-15
Feature: LLM provider HA failover (`feature/llm-provider-fallback`)

## What happened
The feature added 3 new test suites (`test_llm_failover.py` 64 tests, `test_llm_error_classifier.py` 74, `test_graph_retry_integration.py` 18) but NO pack scripts and NO PACKS.md entries. They were dev-run only. A grep for `llm|failover|classifier|retry` in PACKS.md initially returned nothing (while `llm_config_override_unit_test.sh` existed on disk — also unregistered until a later manual add at line 665).

## Risk
Unregistered suites are invisible to pack-based regression gating: a "scoped packs run" (my standard discipline) would never execute them, silently dropping 156 feature tests from future regression sweeps.

## Fix applied (this session)
- Created 4 pack scripts following the canonical template (commit `67b4bb19` + `45f9a4a6`): `llm_failover_unit_test.sh`, `llm_error_classifier_unit_test.sh`, `graph_retry_unit_test.sh`, `llm_failover_adversarial_unit_test.sh`
- Registered all 4 in PACKS.md Unit Test Packs table with full scope descriptions
- Authored 36 NEW adversarial tests (`tests/unit/test_llm_failover_adversarial.py`) covering the 7 focus areas the dev's suite did not fully pin (zero-behavior-change battery, W2 boundaries, config validation matrix, MockTransport e2e + [LLM-HA] log, sticky/counter-reset cycle-2, production IndexError edits)

## Rule reaffirmed
Before any feature test run: validate PACKS.md ↔ scripts ↔ test files (both directions). A feature branch adding test files without pack registration is a finding to fix in the same session — not a blocker to testing (run the suites via freshly created ad-hoc packs), but a mandatory registration follow-through.

## Secondary observations (non-blocking)
- `pytest-timeout` plugin not installed in `.venv` → `--override-ini="timeout=120"` flags in pack templates are inert; shell `timeout 110s` is the real inner guard. Dual-layer contract intact but per-test granularity is bash-level only.
- Dev-claimed baselines ("43f/6e") may not match documented baselines (41f) — always reconcile observed failures against the RESULTS/ record, not the task description.
