# Phase 5: Default-Unchanged Regression + Activation

Date: 2026-09-14
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md`
Decisions anchor: `decisions.md` D6, D7, D8 + plan-overview "Activation / Restart Notes" section
Companion: depends on Phase 2 (signature + integration test); independent of Phases 1, 3, 4.

## Objective

Pin that omitting `model_tier` continues the existing weighted-pool default behavior end-to-end (no spawning behavioral change for today's callers). Document and ship the activation / restart checklist for the operator-facing env var `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`.

## Shared Context

- **D6 (decisions.md L321-371):** the regression test MUST prove two things atomically: (a) when `model_tier=None` AND `model=None`, `_resolve_intelligence_tier` is NOT called AND the weighted-pool selection persists into `instance_metadata["model_override"]`; (b) pool non-determinism is handled by membership assertion, not exact-value assertion.
- **D7 (decisions.md L376-382):** confirm via code review that NO new kwarg leaked into `InstanceManager.spawn_instance` — the Facade-Forwarding guard.
- **D8 (decisions.md L384-388):** `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` is process-lifetime config; restart required. This phase's rollout checklist IS the canonical home for activation instructions.
- **Plan-overview §Activation / Restart Notes (L175-189):** the rollout procedure is 4 steps + 1 smoke test. Phase 5's plan AMPLIFIES that into a production-ready runbook section.
- **Open Question 1, 2 (decisions.md L434-435):** the spec locks `Literal["high"]` only and the loud `ValueError` includes the operator-overridable default. Phase 2 implements this; Phase 5 verifies the error message wording and default-replacement are reachable at operator runtime.
- **Repo & Dev Environment Conventions blueprint items (c), (d), (f):** the rollout checklist respects pause-first restart, dev.env conventions, and (if applicable) the disposable-PG pattern for pre-deploy smoke.

## Touched Files (with verified anchors at `a904374e`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `tests/unit/services/test_spawn_intelligence_tier.py` | (Existing from Phase 1 — extend it.) | Add unit-level default-unchanged regression assertions on the resolver + tool layer. |
| `tests/integration/test_spawn_default_unchanged.py` | NEW file. Place under `tests/integration/` next to `test_spawn_intelligence_tier.py` from Phase 2 (or merge into the Phase 2 file — designer's choice, see Tasks below). | The primary regression gate for this phase. |
| `.agents/shared/planning/spawn-intelligence-override/plan-overview.md` | The "Activation / Restart Notes" section (L175-189 currently). | Extend the section into a full runbook with restart-boot-line verification + post-restart grep + smoke-test recipe. |

**Do NOT touch:** `daemon/manager.py` (D7 — facade must remain unchanged); `daemon/services/instance_lifecycle.py` (already locked from Phases 1-3); any kill-switch or runtime config.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read `decisions.md` D6 + D8 + the plan-overview "Activation / Restart Notes" section. Confirm 4 understanding points: (a) the regression test pins the no-`model_tier` path; (b) `_resolve_intelligence_tier` MUST NOT be called when `model_tier=None`; (c) `instance_metadata["model_override"]` MUST equal the pool selection; (d) the rollout is process-lifetime restart. | none | Implementer can recite the 4 steps of the rollout out of order. |
| 2 | Locate the Phase 1 test file `tests/unit/services/test_spawn_intelligence_tier.py`. Decide whether to extend it or create a sibling file for Phase 5 — recommendation: EXTEND (keeps spawn-intelligence test consolidation). | Task 1 | Path confirmed. |
| 3 | Open `tests/unit/services/test_spawn_intelligence_tier.py` (Phase 1's file). Add unit-level regression assertions for the default-unchanged path: | Tasks 1-2 | Pins added. |
| 3a | Pin R: `test_resolve_intelligence_tier_none_returns_no_override` (DUPLICATE of Pin C — re-pinned in this phase for explicit D6 reference; if Pin C exists from Phase 1, this is a cross-reference comment). | 3 | Pinned. |
| 3b | Pin S: `test_resolve_intelligence_tier_high_then_none_pure` — once the resolver returns `(None, None)` for `None`, no error string ever leaks: assert `_resolve_intelligence_tier(None, allowed_models=("agentic",))[1] is None`. | 3 | Pinned. |
| 4 | Create new integration test file `tests/integration/test_spawn_default_unchanged.py`. Use the existing spawn_instance integration fixture (Phase 2's Pin J reuses the same). | Tasks 1-3 | File exists; pytest discovers. |
| 4a | Pin T: real-dispatch default-unchanged primary pin — call `await manager.spawn_instance(agent_id="coder", parent_id=<seed>, instance_id=None, project_id=<seed>, instance_name=None, model=None, version_tag=None)` with NO `model_tier`. Assert returned `_returned_model` is a member of `{"agentic", "coding", "coding2"}` (weighted pool). Assert `instance_metadata["model_override"] == returned_model`. | 4 | Pin passes. |
| 4b | Pin U: `_resolve_intelligence_tier` NOT called — monkeypatch `daemon.services.instance_lifecycle._resolve_intelligence_tier` with a counting fixture (or use `unittest.mock.patch` + a `call_count` attribute); assert `call_count == 0` after the spawn completes. | 4 | Pin passes; resolver wasn't called. |
| 4c | Pin V: persisted `model_override` reads back from DB — fetch the row directly via `instance_repository.get_by_id(...)` (mirror existing fixture's DB-access helper) and assert `model_override == returned_model`. | 4 | Pin passes; confirms persistence. |
| 4d | Pin W: weighted-pool member sanity — repeat Pin T 5 times (e.g., `for _ in range(5): ...`); collect all returned models; assert each is in `{"agentic", "coding", "coding2"}` AND at least 2 distinct values are seen over 5 runs (proves the pool is actually being exercised, not collapsed to a single model). | 4 | Pin passes; demonstrates pool selects non-deterministically. |
| 5 | Locate the Facade-Forwarding verification grep: confirm `daemon/manager.py` is unchanged from `a904374e`. | Tasks 1-4 | `git log --oneline daemon/manager.py` shows no new commit on the feature branch. |
| 5a | …add a quick smoke test to the file: `assert "model_tier" not in inspect.signature(manager.spawn_instance).parameters` — proves no leaked facade kwarg. | 5 | Pin passes. |
| 6 | Open `.agents/shared/planning/spawn-intelligence-override/plan-overview.md` "Activation / Restart Notes" section (~L175-189). | Tasks 1-5 | Located. |
| 6a | …REPLACE the 4-step section with a 6-step runbook: | 6 | New runbook present. |
| 6a.i | …step 1: confirm daemon is currently running (operator preflight). | 6 | Runbook present. |
| 6a.ii | …step 2: pause-first restart per `.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md` Rollout OR per the latest pause-first runbook (see Repo & Dev Environment Conventions blueprint "Pause/Resume Report Delivery" — `pause_instance_cascade` FIRST, then bounded quiescence confirmation, state mutation, resume). | 6 | Runbook present; pointer to canonical pause-first runbook cited. |
| 6a.iii | …step 3: optionally set `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` in `.env` (default `"agentic"` if unset). Document that the env var is process-lifetime — no SIGHUP reload. | 6 | Runbook present. |
| 6a.iv | …step 4: start the daemon. Verify boot log shows the expected config-load line (no NEW boot line required; existing `Creating PostgreSQL engine` / `Loading config` lines suffice). | 6 | Runbook present. |
| 6a.v | …step 5: post-restart verification — grep `data/logs/ensemble.log` (post-baseline) for any `ValueError` or traceback mentioning `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`; absence = healthy. | 6 | Runbook present. |
| 6a.vi | …step 6: smoke test — via REST API, POST `/api/instances` (or equivalent) spawning a child with `model_tier="high"`; verify `instance_metadata.model_override` matches the env-configured value. | 6 | Runbook present. |
| 6b | …ADD a "Rollback" subsection: if the high-tier resolution misfires (e.g., env var names a model not in `allowed_models`), set the env var to `""` (empty string), restart, all `model_tier="high"` calls land in the loud-raise path with a clear error message. The D8 hardcoded default of `"agentic"` is operator-overridable but cannot be hot-swapped. | 6 | Rollback documented. |
| 6c | …ADD a "Kill-switch" subsection: NO new kill-switch exists. If the operator wants to disable `model_tier` entirely, set `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL` to a model name NOT in `allowed_models` — every `model_tier="high"` call raises loud-raise with the configured value listed, parents learn. (D11 deferred to v2.) | 6 | Kill-switch advice documented. |
| 6d | …commit the runbook change in this phase's separate commit (NOT in Phase 1-4 commits; this is documentation-only and reviewer-friendly). | 6 | Runbook in its own commit. |
| 7 | Final non-regression sweep — run the FULL unit + integration suite: `uv run python -m pytest tests/unit tests/integration -q --ignore=tests/postgres` and confirm 0 NEW failures. | All tasks | All green. |
| 8 | Final drift check — `grep -rn "model_tier" daemon/ agents/ .agents/shared/planning/spawn-intelligence-override/` returns: (i) ≥3 matches in `daemon/tools/instance.py` (Pydantic field, runtime kwarg, docstring), (ii) 1 match in `daemon/services/long_tool_nudge.py` (rec 4), (iii) ≥1 match in `daemon/services/instance_lifecycle.py` (resolver + append_allowed_models tail), (iv) 0 matches in `daemon/manager.py` (facade unviolated), (v) ≥1 match in `.agents/shared/planning/spawn-intelligence-override/` (planning docs reference the name). | All tasks | Drift check passes. |

## Test Plan

**Files:**
- `tests/unit/services/test_spawn_intelligence_tier.py` (extended — Pins R, S)
- `tests/integration/test_spawn_default_unchanged.py` (NEW — Pins T, U, V, W; Pin from 5a)

**Run commands (exclusive):**
```bash
# Phase 5 unit slice
uv run python -m pytest tests/unit/services/test_spawn_intelligence_tier.py -v

# Phase 5 integration slice
uv run python -m pytest tests/integration/test_spawn_default_unchanged.py -v

# Combined phases 1-5
uv run python -m pytest tests/unit/test_long_tool_nudge.py tests/unit/services/test_spawn_intelligence_tier.py tests/integration/test_spawn_intelligence_tier.py tests/integration/test_spawn_default_unchanged.py -v

# Full sweep (Phase 5 acceptance)
uv run python -m pytest tests/unit tests/integration -q --ignore=tests/postgres
```

**What each pin catches:**

- Pin R/S (D6 unit): the resolver's `None` path is pure — no override, no error, no side effect.
- Pin T (D6 integration primary): end-to-end the no-`model_tier` path persists a `model_override` from the weighted pool.
- Pin U (D6 negative pin): proves the resolver is NOT called when the param is absent — a leaky resolver would generate spurious log lines or error strings.
- Pin V (D6 persistence): proves the DB writeback path is correct — the pool selection landed in `instance_metadata`.
- Pin W (D6 pool sanity): the test is meaningful only if the pool is actually exercised (a hardcoded return value would also pass Pins T/U/V but fail W).
- 5a (D7 facade): proves `InstanceManager.spawn_instance` signature is unchanged.

**Test isolation requirements:**
- Use the same spawn_instance integration fixture from Phase 2.
- `monkeypatch.setattr` (pytest fixture) for `_resolve_intelligence_tier` so teardown is automatic.
- If the weighted pool ever produces an unexpected model (e.g., a new model added to `_ALLOWED_MODELS_DEFAULT` without updating the pin), update the membership list AT THE TEST, not in the production code.

## Non-Regression Checks

- **Manager facade:** `git diff daemon/manager.py` is empty (D7 verified).
- **Resolver function:** Phase 1's function is unchanged; Pins R/S are not new behavior — they're re-pins of the same `(None, None)` shape.
- **Spawn tool surface:** Phase 2's signature is unchanged in this phase.
- **`_build_long_tool_notice`:** Phase 4's rec-4 is unchanged in this phase.
- **`append_allowed_models`:** Phase 3's tail is unchanged.
- **All settled zones (Feature #2 + Feature #1):** GREEN.

## Coupling

- **Tight with Phase 2:** the integration test depends on Phase 2's `manager.spawn_instance(..., model_tier=...)` signature accepting the kwarg and threading it. Phase 2 MUST land first.
- **Independent of Phase 1:** Phase 5 tests the NO-`model_tier` path — the resolver is verified NOT called. The resolver's existence is irrelevant to Phase 5's primary pin (Pin T-U-V are about what the system DOES NOT do when the param is absent).
- **Independent of Phase 3:** docstring/append_allowed_models are separate from regression behavior.
- **Independent of Phase 4:** the nudge text is unrelated to the default-unchanged behavioral assertion.

## Risks (phase-specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R5.1 | Integration test is flaky because the weighted pool returns a model not in `{"agentic", "coding", "coding2"}` (e.g., a future pool expansion adds `"gpt-5"`) | Medium | Low | Pin T's membership set is updated at the test; the production code is untouched. Document the membership in a code comment so future maintainers update it. |
| R5.2 | `_resolve_intelligence_tier` monkeypatch leaks between tests | Medium | Low | Use pytest's `monkeypatch` fixture (auto-teardown), not raw `unittest.mock.patch`. |
| R5.3 | Pin W's "at least 2 distinct models over 5 runs" is hard to satisfy if weights are heavily skewed toward one model | Low | Low | If Pin W fails on a healthy pool, document the weights in the test as a comment and reduce the assertion to "at least 1 distinct model" (single-model pool would still pass T/U/V, just not exercise W's intent). |
| R5.4 | The rollout runbook is unclear about the restart mechanism — operator does dev.sh vs systemd vs bare uvicorn | Medium | Medium | Reference the existing "Pause-First Then Quiesce Convention" from the Pause/Resume Report Delivery blueprint (`daemon/services/instance_lifecycle.py`); the runbook doesn't need to re-document the restart — it points to the canonical pause-first runbook. |
| R5.5 | New env var is silently read at module-import time and the operator never realizes their `.env` update didn't take | High | Low | Step 4 (boot-log verification) + step 6 (smoke test) catch this. Document explicitly: "If the smoke test does not return the env-configured value, the daemon was not restarted properly — re-run steps 2-5." |
| R5.6 | The integration test depends on a `coder` agent existing in the test DB; if the agent registry changes, the fixture breaks | Medium | Low | Use the same fixture Phase 2 uses — likely loads a fixed agent. If that fails, gracefully `pytest.skip` with a clear message. |

## Acceptance Gate

This phase is DONE when:

1. `git diff daemon/manager.py` is STILL empty (D7 final verification).
2. `git diff daemon/services/instance_lifecycle.py` is STILL unchanged from Phase 1's HEAD (resolver, append_allowed_models, _resolve_model_override all stable).
3. `uv run python -m pytest tests/integration/test_spawn_default_unchanged.py -v` is 5/5 green (Pins T, U, V, W + 5a).
4. `uv run python -m pytest tests/unit/services/test_spawn_intelligence_tier.py -v` is ≥8/8 green (Phase 1 Pins A-F + Phase 5 Pins R, S).
5. `uv run python -m pytest tests/unit/test_long_tool_nudge.py -q` is GREEN (Feature #2 settled zones intact).
6. `uv run python -m pytest tests/unit tests/integration -q --ignore=tests/postgres` is 0 NEW-FAILS.
7. `.agents/shared/planning/spawn-intelligence-override/plan-overview.md` "Activation / Restart Notes" section has been extended to a 6-step runbook with Rollback + Kill-switch subsections, committed in this phase's commit.
8. Drift grep is consistent (Task 8 — all 5 buckets match expected counts).

## Exit Criterion

Phase 5 done means: the feature is regression-pinned (no-`model_tier` is provably identical to today's behavior end-to-end), the operator activation runbook is in place, the rollout is operator-runnable without off-band questions. The feature is SHIP-READY.

## Final Note (across all 5 phases)

A combined "implementation worktree" can land all 5 phases in 1 PR (or 5 separate commits — designer's call). A "review worktree" should:

1. Branch from `latest` at SHA `a904374e` (or current `latest` tip if Feature #2 has been re-merged since).
2. Apply Phases 1-5 in numerical order.
3. After each phase, run that phase's test gate — verify green before moving to the next.
4. After all 5 phases, run the full sweep (`uv run python -m pytest tests/unit tests/integration -q --ignore=tests/postgres`).
5. Verify each of the 12 success criteria from `plan-overview.md` L156-169.
6. THEN create the merge PR with the activation runbook in the description.

Sequencing safety: Phases 1-2-5 are on the tool-surface critical path (Phase 5's integration test depends on Phase 2's signature); Phases 3-4 are on the discoverability critical path (Phase 3's docstring + Phase 4's nudge rec-4). The two paths can be developed in parallel by two separate feature worktrees (a "review worktree" merging both into one integration PR).
