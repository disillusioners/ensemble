# Acceptance-Pack Skip-Guards: a PASS Is Not Coverage Proof (2026-09-22)

From the fix/job-completed-result-arm independent gate. The commission's whole point was proving the REAL emission surface (SSE payload + persisted job_completed row) — yet the acceptance pack returned exit 0 / "PASS" while Intent5 (the only real-daemon case) silently SKIPPED via its `skipif(not _daemon_running())` guard.

## Finding
`test/packs/job_completion_acceptance_test.sh` reports pack PASS when Intent5 skips (no daemon on 8079). The pack's own contract documents the skip as informational — but a consumer reading "PASS" gets no signal that the highest-value case never ran. Council MINOR #4 ("Intent5 exit code recorded but never gated") is the same hole from the code side.

## Lessons
1. **Adjudication rule**: for any pack with skip-if guards on its flagship case, a PASS without proof the flagship EXECUTED is a partial verdict — always verify the case count includes the flagship, or boot its precondition (here: Gate 2's dev daemon made Intent5 run for real).
2. **Pack design rule**: flagship cases should either fail the pack on skip or surface an explicit distinct verdict ("PASS-WITH-SKIPS: intent5=skipped(no-daemon)") — silent exit-0 skips will eventually mask a real regression exactly the way the v0.13.9 acceptance pack masked the emission-surface gap (LESSONS/2026-09-21).
3. **Env foot-gun confirmed in the new e2e test itself**: `tests/e2e/test_result_summary_emission.py` PG params fall back to ambient `POSTGRES_*` (~:77-81) — corroborates council MINOR #5; scrubbing POSTGRES_* is mandatory before ANY e2e/db-touching run on this host (ambient currently carries ensemble_prod creds).
4. `./dev.sh` has NO LLM upstream (:4001 refused) — jobs can never complete; the e2e emission test requires `./dev_with_mock.sh` (sanctioned mock LLM :4124) plus `PYTEST_TIMEOUT=280` per ensure.md Release-Gate prerequisites. Document in pipeline.

Evidence: RESULTS/2026-09-22-job-completed-result-arm-gate.md (Gates 1+2).
