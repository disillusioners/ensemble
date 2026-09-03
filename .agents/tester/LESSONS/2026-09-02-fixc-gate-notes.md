# Fix C Gate Notes (2026-09-02)

Gate: `feature/job-task-fix-c` @ `ab518e0b` vs base `e20d6e48` — PASS. Full report: RESULTS/2026-09-02-fix-c-full-regression-gate.md. Reusable lessons:

1. **`pytest | tee` exit-code capture: mandate `${PIPESTATUS[0]}` in every dispatch template.** Four independent workers this gate caught that `echo "EXIT=$?"` after a `| tee` pipeline reports tee's exit (always 0), not pytest's — one worker derived RESULT from the log summary line instead, another used `set -o pipefail`, a third re-collected Phase-2 results after catching its own `|| EXIT=$?` variant. The Fix-C dispatch template now carries `echo "PYTEST_EXIT=${PIPESTATUS[0]}"`; keep it. (Same class as the base worker's runner_v3 fix — subshell + `wait $pid` also works.)

2. **Base-batch false-positives exist in BOTH directions — always solo-verify pass-at-base nodes.** `test_agent_bootstrap_and_hello` PASSED the base batch run but fails solo 3/3 at base (context-dependent ordering). Fix B had documented the inverse (fails solo, passes batch). Rule for future gates: a pass-at-base batch outcome is NOT evidence of pre-existence or causedness — only the solo 3× budget at both commits decides. This gate ran solo budgets on all 26 phase-2 nodes; keep that.

3. **Counter-equivalence cross-check settles "mock-counted vs engine-bound" disputes without rewriting the test.** The N+1 test's Session.exec class-patch was audit-labeled MOCK-COUNTED; a runtime script counting via BOTH the patch mechanism AND a `before_cursor_execute` engine listener showed exact equality at two page sizes → ENGINE-EQUIVALENT verdict, no test rewrite needed. Generalize: when a mock-audit questions a counting mechanism, run the dual-counter runtime diff before demanding refactor.

4. **Collect-only sentinel is not trustworthy under repo-default addopts.** `pytest tests/ --collect-only -q` returned 3,222 vs the 8-partition run-sum 16,137 (partial collection; 2.85s is too fast for 16k). Coverage anchoring should rest on partition parity + file-count enumerations, not a whole-tree collect-only. Drop the sentinel or run it with `--override-ini="addopts="` after diagnosing.

5. **Flake-family membership wobbles run-to-run — stamp membership, not just count.** QUARANTINE row 37's family presented as 19 this gate vs 18 at Fix-B (cross_phase_b ×2 + debug_llm ×1 surfaced, one vscode node fewer). All members remained solo-clean at both commits — the FAMILY is stable, the membership is not. Record which nodes manifested each gate so drift stays visible without re-quarantining.
