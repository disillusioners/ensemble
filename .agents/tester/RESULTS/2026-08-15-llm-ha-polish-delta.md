# Test Report: LLM HA Polish Delta — commit ba559598

Date: 2026-08-15
Branch: `feature/llm-ha-polish` @ `ba559598` (parent `8cbe7e71`)
Instance IDs: 66a07dae (failover-v1), aab7375d (classifier-v1), d6ff7d2f (graphretry-v1), f251a181 (failover-v2), 34adc19a (v2-adversarial), d1020e07 (v2-resilience), 6c38eaae (failover-adv), 73050c11 (compaction), 1e1ac170 (skill-evolution), 4a43fe25 (chk-rename), fc16bf60 (chk-extraction)
Scope: focused delta pass per leader's tight list — pure refactor polish (formula extractions `derive_ha_attempt_ceiling`/`is_retryable_status_code`, rename `_make_llm_retry_strategy`→`make_llm_retry_strategy` ~80 occ., 1 unused import, 8 comment rewords, pytest-timeout venv install).

## Summary
- Total packs: 9 | Passed: 9 | Failed: 0 | Timeout: 0
- Battery: 305/305 (leader's 269 + adversarial add-on 36)
- Regression spot-checks: 206 + 47, 0 new failures
- Verify items: 2/2 PASS (rename drift zero; extraction equivalence + both-family runtime smoke)
- Quick fixes applied: 0 (none needed)
- Quarantined: 0

## Verdict: SHIP

## Per-item results (leader's checklist)

| # | Item | Result | Evidence |
|---|------|--------|----------|
| 1 | Full failover battery | ✅ PASS | v1: 64 (failover) + 74 (classifier) + 18 (graph_retry) = 156; v2: 45 + 48 + 20 = 113 → **269/269 at exact expected counts**. Add-on `llm_failover_adversarial_unit_test` 36/36 → **305/305 total**, restoring prior SHIP baseline. |
| 2 | Rename drift check | ✅ PASS | `grep _make_llm_retry_strategy daemon/ tests/` → 0 source occurrences. Non-drift hits only: deliberate both-ways pin in `test_graph_retry_integration.py:425-427` (asserts old name ABSENT from graph source — working as intended), stale git-ignored `.pyc`, git-ignored backup db. New name present: `llm_error_classifier.py` (def :317 + 3 refs), `graph.py` (lazy import :3152 + call :3179 + comment :3122), `services/llm_failover.py` (import :227 + calls :503,:773 + docstrings). Imports resolve from all production modules; `daemon.graph` lazy-imports by design (import-cycle break, identical structure in parent 8cbe7e71 — NOT drift). 5/5 representative tests across all 5 referencing modules PASS (mock.patch targets resolve). |
| 3 | Extraction equivalence | ✅ PASS | Constant-drift test (PRIMARY patched to (5,3)) PASSES: `TestBuildInstanceLLMSFailoverWiring::test_custom_retry_config_ceiling_is_derived_not_hardcoded`. 8 more ceiling/budget tests PASS (W2 clamp, backup-budget-ceiling, extension, pre-HA no-backup, v2 zero-drift budget match, adversarial reraise paths). Patch targets `daemon.llm_error_classifier.{make_llm_retry_strategy, PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX}` resolve post-rename. |
| 4 | Runtime smoke | ✅ PASS | Ad-hoc script (deleted after run): V1 controller path (wired per `graph.py::_wire_retry_and_failover`) — request hosts `primary×3 → backup×1`, response "from-backup", client sticky on backup (`_on_backup=True`), `[LLM-HA]` warning logged. V2 shared facade (`wrap_langchain_failover`) — `is_failover_active=True`, same host sequence. Formula sanity: `derive_ha_attempt_ceiling` 5/5 (budget=max(t,o) no-HA; HA adds max(PRIMARY_*)), `is_retryable_status_code` 17/17 (429/500/502-504/520-524 True; 400/401/403/404/422/200/301 False). Extractions did NOT numb the failover path. |
| 5 | pytest-timeout | ✅ PASS | Plugin registered: `pytest-timeout-2.4.0` at `.venv/.../pytest_timeout.py` (needs `pytest -VV`; plain `--version` prints only version on pytest 9.0.2). No "Unknown config option: timeout" warning — 74 tests collected cleanly. `pyproject.toml:72 timeout = 30` + `:73 timeout_method = "thread"`; dev extras `:44 pytest-timeout>=2.3`; uv.lock present (3 refs). Config engages without warnings. |
| 6 | Regression spot-check | ✅ PASS | compaction_unit_test 206/206 (0.9s, exact baseline); skill_evolution_unit_test 47/47 (1.4s, exact baseline). 0 new failures. |

## ensure.md Validation (blast-radius scoped)
- **Critical**
  - ✅ No regressions in changed packs — all 9 scoped packs PASS
  - ⚪ `concurrency_atomic_unit_test` — EXCLUDED by blast radius: pure rename/formula-extraction touches no job/task/queue/threading code (critical notes convention triggers full e2e only for job/task/queue changes). Files changed: llm_error_classifier.py, graph.py, services/llm_failover.py, 4 test files.
  - ✅ dev.sh `--timeout-graceful-shutdown 10` — present (`dev.sh:102`, comment at :99)
- **Important / Nice-to-have**: out of blast radius (no async conversions, no deletions)
- Release Gate: NOT triggered (polish commit, not big/critical/architecture)

## Scope Decision
> Focused delta pass per leader's tight list (no full suite). Two adjustments: (a) ADDED `llm_failover_adversarial_unit_test` (36 tests) — heaviest mock.patch consumer of the renamed symbol; restores prior 305/305 SHIP baseline; (b) EXCLUDED `concurrency_atomic_unit_test` (ensure.md Core Critical) per blast radius — rename/formula-extraction in 3 production files touches no job/task/queue/threading code; ensure.md's own scoping rule limits validation to the change set. Full suite not warranted.

## Notable observations (non-blocking, report-only)
1. 🟢 No test references `derive_ha_attempt_ceiling` / `is_retryable_status_code` by name — they're covered transitively (drift test patches the constants). A permanent direct unit test would be a nice follow-up (blueprint already tracks ceiling-formula extraction as a follow-up).
2. 🟢 `daemon.graph` lazy-imports `make_llm_retry_strategy` (import-cycle break, pre-existing) — `from daemon.graph import make_llm_retry_strategy` legitimately fails; patches must target `daemon.llm_error_classifier.make_llm_retry_strategy` (all current tests do).
3. 🟢 Stale `.pyc` files in `tests/unit/__pycache__/` still embed the old name — git-ignored, harmless, regenerate on next run.
4. 🟢 `pytest --version` on pytest 9.0.2 does not print the plugins line — use `pytest -VV` to see plugin registration.
5. 🟢 Pack header comments in `llm_failover_v2_adversarial_unit_test.sh` / `llm_failover_v2_resilience_unit_test.sh` still say "target test file does not exist yet" — files exist and pass; cosmetic doc drift.

## Documentation Updated
- [x] RESULTS/2026-08-15-llm-ha-polish-delta.md — this report
- [x] PACKS.md — last-run dates updated for 9 packs
- [x] LESSONS/ — not warranted (no failures, no fixes; observations recorded here)

## Code Changes Summary
None — verification-only session. All 11 workers read-only or ad-hoc-/tmp (smoke script deleted). Repo tree untouched by this testing session.

## Overall Status
- Unit Tests: ✅ PASS (305 battery + 253 regression spot-checks, exact counts)
- ensure.md (scoped): ✅ PASS (Critical 2/2 in-scope; concurrency pack excluded by blast radius)
- **Verdict: ✅ SHIP**
