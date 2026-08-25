# Pre-Deploy Validation — 84fd8018 graph tool-result placeholder synthesis

Date: 2026-08-25 (UTC) | Mode: quick-deploy pre-validation, VALIDATION-ONLY (no fixes authorized)
Repo: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble | Branch: `latest`
Commit: `84fd80189a6407fe491fccd2ff7ae2200da61333` — "fix(graph): synthesize tool-result placeholders before mid-turn HumanMessage injection"
Worker instances: b9b930cd (verify), a75f9061 (pairing + bundle), 299f76ad (dir sweep + compaction), 476ef7cd (inj-graph + nudge), 8d91c195 (graph_retry), a3d84730 (context_graph), 879e704f (attribution). 10 dispatches, 0 direct executions.

## Verdict

**✅ PASS — DEPLOY-CLEAR.** All in-scope packs green; the only failures (4) are proven PRE-EXISTING (base-evidenced) and quarantined; repo state verified clean and compilable.

## Scope Decision

> Full breadth was NOT requested as a release gate; leader asked for focused pre-deploy validation. Change = daemon/graph.py (+217/−1, additive, O(1) early return, 3 guarded HumanMessage append sites in agent_node) + 1 new test file. Blast radius = graph message-shape/injection seam only. Ran: 7 packs (new suite + registered injection/graph adjacency). Skipped: job/task/queue e2e Release Gate (change touches none of claim_pending_task / turn_transitions / reconcile_turn_mirror / job_processor / job_locks — mandatory-e2e critical-note rule NOT triggered), concurrency_atomic (no lock/async/DB surface in diff), dev.sh static (untouched by change set). tests/unit/graph/ contains ONLY the new suite — the leader's "adjacent graph suites" were located via the discovery fallback and run from their registered pack locations.

## Repo State Gate (worker b9b930cd) — 7/7 PASS

| Check | Result |
|---|---|
| HEAD sha | `84fd80189a6407fe491fccd2ff7ae2200da61333` ✅ |
| Branch | `latest` ✅ |
| Working tree | clean (`git status --short` empty) ✅ |
| Commit message | exact match ✅ |
| Stat | graph.py +217/−1; test_injection_tool_pairing.py +553 (new) — "2 files changed, 770 insertions(+), 1 deletion(-)" ✅ |
| py_compile daemon/graph.py | exit 0, zero stderr — no silent-edit corruption ✅ |
| tests/unit/graph/ inventory | only test_injection_tool_pairing.py + __pycache__ |

## Pack Results (7 packs, 410 collected: 406 PASS / 4 FAIL-pre-existing / 0 skip)

| # | Pack | Result | Counts | Runtime | Baseline delta |
|---|---|---|---|---|---|
| 1 | injection_tool_pairing_unit_test (NEW ad-hoc) | ✅ PASS | 16/16 | 0.50s | n/a — commit acceptance suite |
| 2 | injection_graph_unit_test | ✅ PASS | 11/11 | 0.58s | +2 vs 9 (stale row; growth = commit 85097179 2026-07-22, file untouched by 84fd8018) |
| 3 | graph_retry_unit_test | ✅ PASS | 19/19 | 0.90s | exact vs 2026-08-25 reference |
| 4 | context_graph_integration_test | ✅ PASS | 20/20 | 0.90s | exact vs 2de4af3a baseline |
| 5 | compaction_unit_test | ✅ PASS | 207/207 | 1.24s | +1 vs 206 (benign drift post-2fca56ae) |
| 6 | nudge_regression_unit_test | ✅ PASS | 40/40 | 0.74s | +4 vs 36 (stale row; growth = commit ab3d4722 2026-08-02, ancestor-verified) |
| 7 | injection_unit_test bundle (sse/slot/cleanup/api/compaction/loop_breaker_integration) | ❌ FAIL → attributed PRE-EXISTING | 93/97 | 2.54s | 4 failures, one root cause (below) |

Per-file in bundle 7: sse 12/12, slot 19/22 (3F), cleanup 2/3 (1F), api 33/33, compaction 7/7, loop_breaker_integration 20/20.

## Failure Cluster — Attribution (worker 879e704f)

All 4 failures share one signature: `daemon/manager.py:3488` in `_cleanup_instance_state` → `self._deferred_watchover_terminate.discard(instance_id)` → `AttributeError: '_ManagerStub' object has no attribute '_deferred_watchover_terminate'`.

- F1 `tests/test_injection_slot.py:262` TestCleanupInstanceState::test_clears_all_three_dicts
- F2 `tests/test_injection_slot.py:284` TestCleanupInstanceState::test_clears_when_only_injection_present
- F3 `tests/test_injection_slot.py:297` TestCleanupInstanceState::test_clears_when_no_state_present
- F4 `tests/test_injection_cleanup.py:143` test_project_delete_clears_injection

**Verdict: PRE-EXISTING — NOT introduced by 84fd8018.** Evidence chain (each link independently sufficient):
1. `git log -S "_deferred_watchover_terminate.discard" -- daemon/manager.py` → introducer `12378edb` (2026-08-06, feat(watchover)); ancestor of HEAD (exit 0); 40 intervening manager.py commits.
2. `git show --stat HEAD` → 84fd8018 touched ONLY graph.py + new test file, NOT manager.py.
3. Failing test files last touched 2026-07-22 (`2ec1099a`) / 2026-07-13 (`700cad12`) — both PREDATE the introducer; stubs frozen before the attribute existed. Companion `_deferred_question_pause` attr IS present in stubs; only the watchover pair is missing.
4. Decisive base re-run at parent `f5e4b79a` (worktree /tmp/attrib-base-84fd8018): `4 failed, 21 passed in 2.65s` — identical 4 test IDs, same line, same exception, verbatim.
5. Worktree removed; main checkout clean post-run.

**Disposition:** 4 rows added to QUARANTINE.md (deterministic, base-evidenced). Fix when convenient: add `_deferred_watchover_terminate: set[str] = set()` to both `_ManagerStub` fixtures (test-code fix, ~2 lines). Not deploy-blocking.

## ensure.md Validation (scoped)

- **Critical R1 — no regressions in changed packs**: ✅ PASS (all change-set packs green; 4 bundle failures quarantine-attributed per ensure.md's own quarantine-aware rule).
- **Critical R2/R3 — concurrency_atomic**: SCOPED OUT — diff has no lock/async-signature/DB surface (graph.py message assembly only).
- **Critical R4 — dev.sh static**: SCOPED OUT — dev.sh untouched by change set.
- **Release Gate**: NOT TRIGGERED — no job/task/queue system touch (five-path rule from critical notes).
- Deviation note: leader's task-3 shorthand suggested `pytest -x`; `-x` is forbidden by ensure.md header ("No -x") — all suites ran with `--tb=short -q`, full failure review.
- No contradictions against ensure.md methods were triggered by the file itself this session.

## Registry Maintenance Flagged (not fixed this session — validation-only)

- PACKS.md stale baselines for later re-registration: `injection_graph_unit_test` :490 (9→11), `nudge_regression_unit_test` :205 (36→40), `compaction_unit_test` :136 (206→207).
- NEW pack registered via session banner: `injection_tool_pairing_unit_test` (ad-hoc, 16 tests).
- QUARANTINE.md: +4 rows (injection bundle fixture drift).
- LESSONS/2026-08-25-injection-bundle-fixture-drift-coverage-rot.md written.

## Overall Status

- Scoped packs: ✅ PASS (deploy-clear)
- Quarantined (this session): 4 tests, pre-existing, base-evidenced
- **Testing Complete: ✅ READY — 84fd8018 cleared for quick deploy**
