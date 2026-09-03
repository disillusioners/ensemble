# Mission-M1 FINAL gate — lessons (2026-09-02)

Gate: `feature/mission-class` @ `0e74ca1e` (+2 gate infra commits → `b488fabc`), base `e676ddea`. Full report: RESULTS/2026-09-02-mission-m1-final-gate.md.

## 1. Branch tip committed tests its own manifest can't run (pytest-mock gap)
Branch commit `0e74ca1e` ("M1 gate round C") added `mocker`-based degradation tests, but `[dependency-groups].dev` had no `pytest-mock` — fresh `uv sync`/CI errors them with `fixture 'mocker' not found` (46P/2E). The dev's environment masked it (plugin installed ad-hoc). **Lesson: any gate whose acceptance count > collected-pass should check for missing pytest plugins FIRST — the failure signature (`fixture 'X' not found` at setup, zero test body execution) is unmistakable.** Fix: `uv add pytest-mock --group dev` (commit `12ed8f86`). Grep confirmed zero other `mocker` usage under tests/ — impact isolated.

## 2. Committed packs carry per-gate branch pins — EXPECTED_BRANCH staleness class
Both FE packs hardcoded `EXPECTED_BRANCH="feature/job-queue-fe-liveness"` from the prior gate → Stage-0 DRIFT short-circuit on `feature/mission-class` (packs never ran; jest/tsc/build exits NOT OBSERVED — report as such, never as zero/Pass). Fix: `${EXPECTED_BRANCH:-<branch>}` env-overridable form (commit `b488fabc`), matching the constitution pack's pattern. **Lesson: any gate dispatching branch-pinned packs must pre-flight the pin; the env-overridable form should be the standard for all new packs.**

## 3. Unit-green ≠ contract-kept: the S4 binding-level divergence
All 48 resolver unit tests green, yet the runtime ON-path matrix caught a real contract miss: doc §8.3:1096 (W4 hazard) requires `mission_terminal_reason='dead_letter'` when admission is DEAD; the read surface returns `'failed'` because `work_resolver.py:1702` calls `resolver.project(instance)` (defaults `dead_linked=False`) instead of the `resolve()/resolve_many()` path that pre-fetches `dead_linked`. Resolver logic itself is correct — the BUG is at the API-binding seam. **Lesson: for projection/derived-field features, a runtime matrix driven through REAL routes is mandatory — resolver-level tests structurally cannot see binding bugs. Recommend the committed integration pin (list+detail+SSE for the dead-letter scenario) before any flag flip.**

## 4. N+1 adjudication: pinned bound vs route-level totals
The task rail ("exactly 1 JobItem SELECT via _batch_jobitem_lookup") UPHELD (engine-counted, N=8/16 both exactly 1; Instance SELECTs flat at 2). Route-level TOTALS are `3 + N_queued` (count+list pagination + per-QUEUED `_get_queue_position`) — looked like a violation until the base re-run proved the signature statement-for-statement identical at `e676ddea` (fe-liveness-era). **Lesson: always decompose route-level query counts into baseline-pagination / pre-existing-features / feature-under-test before crying N+1; the base worktree settles it in ~2s.** Also: one Fix-C-era test counts via `patch.object(Session,'exec')` (MOCK-COUNTED — wouldn't catch a raw-SQL bypass); the M1-added bound test IS engine-bound. Migrate the old one.

## 5. Wave-0 anomalies to distrust
- Wave-0 claimed `test_fix_c_read_model_split.py` was "NEW at M1" (git diff listing artifact) — acc-set-4 worker proved EXISTS_AT_BASE (+91/−0). **Verify provenance with `git cat-file -e <base>:<path>`, not diff listings.**
- P4 "+1 file vs Fix-C" was pre-M1 (landed between `ab518e0b` and `e676ddea`) — baseline drift between gates is normal; attribute against the CURRENT gate's base, not stale counts.
- `test_debug_llm_invocation_count`: Fix-C called it flake; at `e676ddea` it is deterministic-fail. Classes drift between bases — re-verify, don't inherit.

## 6. Gate-owned commits move HEAD mid-flight — pre-announce brackets
Two quick-fix commits advanced HEAD during the gate (`0e74ca1e → 12ed8f86 → b488fabc`). In-flight workers bracketed against the old SHA. Managed by: (a) dispatcher bracket-update injections listing valid SHAs, (b) advisory "test-infra-only commit may land; verify `git show --stat HEAD` touches only test/packs|pyproject+uv.lock → treat valid". Zero false drift-stops. **Lesson: publish the valid-SHA set + the adjudication rule the moment a gate commits.**

## 7. Runtime scripts: make them base-runnable
`purity_verify.py` had no argv interface and imported M1-only modules → needed a stubbed copy to run at base. **Lesson: verification scripts meant for HEAD↔base comparison should (a) take side/db-path argv, (b) guard M1-only imports, (c) not self-delete their DBs if evidence retention matters.**
