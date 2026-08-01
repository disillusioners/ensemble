# Tracking: LLM Model Load Balancing Feature

## Iteration 001 — REJECTED
**Date:** 2026-08-01 21:37
**Worker:** approve-worker-plan (1b083962-8e33-4c13-bf17-76436ff768a9)
**Skill:** plan-approval

### Blocking Issues

1. **Phase 4 internal contradiction — source-attribution log label** (`phase4-plan.md` line 76 vs line 130)
   - The source-attribution ternary (line 76) has only 3 cases: `"override"` / `"llm_models"` / `"llm_model"`. But the edge-case table (line 130, row "No metadata, no override") states source should be `"default"` for that case.
   - Impact: the "No metadata, no override" path (metadata=None OR llm_models empty AND llm_model empty) logs source as `"llm_model"` instead of `"default"`. Functional resolution is correct; only the observability/log label is wrong. Still a direct within-plan contradiction.
   - Fix required: add a 4th case to the source-attribution ternary returning `"default"` when both `llm_models` and `llm_model` are absent, matching the edge-case table.

### Notes (non-blocking — carry forward if addressed)
- **P1↔P2 `min_length=1` mismatch** — Phase 2 risk table recommends `Field(min_length=1)` on `LLMModelWeight.model`, but Phase 1 code sketch omits it. Functional path OK (Phase 2 filter loop handles whitespace defensively). Either add to Phase 1 or remove from Phase 2 risk table.
- **P4 behavior change: `model_override` freeze scope** — Phase 4 persists `resolved_model` to `instance_metadata["model_override"]` even when sourced from `metadata.llm_model` or config default. Previously `llm_model` was NOT an override. Overview says "Persistence of the resolved model" is in-scope, but original objective claims "fully backward compatible". Flag in PR.
- **P3 fallback masks `_build_llm_config` failure** — `resolved_model_holder[0] if resolved_model_holder else validated_model_override` silently masks a `_build_llm_config` raise. Consider raising or logging the empty-holder case.
- **P5 test sketches use wrong `spawn_instance` param names** — Task 4 uses `metadata=agent`, Task 5 uses `meta={...}`. Real API takes `agent_id` + `model`. Refine during implementation.
- **P5 statistical tolerance generous** — ±3% on 10k samples (~6–10σ). Consider ±2% / 50k samples.
- **P3+P4 `getattr(metadata, "agent_id")` may always be `<unknown>`** — `AgentMetadata` may not declare `agent_id`. Defensive `getattr` masks broken observability. Document actual source.
- **P1↔P2 `LLMModelWeight` location inconsistent** — Phase 1 risk table says define in `llm_load_balancer.py` + import; Phase 1 sketch says inline in `registry.py` (or other file); Phase 2 imports from `daemon.registry`. Pick one location definitively.
- **P2 `TYPE_CHECKING` import overly defensive** — non-circular import; direct import is simpler.
- **P4 Task 1b unreachable** — restore path already works (`instance_lifecycle.py:2384` reads `model_override`). Task 1b pseudocode is unused. Note in plan to avoid wasted effort.
- **P5 fixtures undefined** — `initial_daemon.shutdown()`, `daemon_factory()` — verify they exist in `conftest.py` or define them.

### Unverified Items (worker flagged)
- `metadata.agent_id` existence on `AgentMetadata` — defensive getattr covers it.
- HTTP API spawn response `model` field — Phase 4 Task 5 / Phase 5 Task 8 treat as TBD.
- Other callers of `_build_llm_config` beyond spawn/restore — only 2 confirmed.
- Phase 5 statistical tolerance at sample sizes other than 10k.

### Verdict: REJECTED (internal contradiction — must fix before approval)

---

## Iteration 002 — REJECTED
**Date:** 2026-08-01 21:48
**Worker:** approve-worker-plan (53db9a03-5ca9-4569-bf78-e41958c1620a)
**Skill:** plan-approval

### Iteration 001 Blocking Issue Status
- **Phase 4 source-attribution ternary missing 4th "default" case** — PARTIALLY ADDRESSED. The default case appears to have been added, but the worker (fresh context) found a DEEPER variant: when `llm_models` is non-empty but all entries are filtered out, the source is still mislabeled as `"llm_models"` instead of the actual fallback source (`llm_model` or `default`). Original fix incomplete. → New Blocking Issue #4 below.

### Blocking Issues

1. **LLMModelWeight import location inconsistent across phases** (`phase1-plan.md` §Tasks 1–2; `phase2-plan.md` §Code Sketch; `phase5-plan.md` §Task 3)
   - Expected: one canonical definition + consistent import path
   - Found: Phase 1 defines in `llm_load_balancer.py`; Phase 2 imports from `daemon.registry`; Phase 5 imports from `daemon.registry`. Mutually inconsistent — risks circular import / unresolved reference.
   - *(Escalated from iter-001 Note #8 — location was claimed "unified" but still inconsistent across 3 phases.)*

2. **Single-resolution guarantee not enforced** (`phase3-plan.md` §Code Sketch, lines 75–99)
   - Expected: model chosen ONCE at instance creation, reused for instance lifetime
   - Found: `_build_llm_config()` re-randomizes on every call without override. Multiple creation-path calls can select different models. Proposed "call twice → same result" test cannot pass.

3. **Backward compatibility contradiction — model_override freeze scope** (`phase4-plan.md` §Code Sketch, lines 67–93; `plan-overview.md` §Scope)
   - Expected: "fully backward compatible" — existing agents retain current behavior
   - Found: Phase 4 persists resolved model to `model_override` even for `metadata.llm_model` and global-default sources. Freezes values that were previously dynamic. Contradicts stated compatibility requirement.
   - *(Escalated from iter-001 Note #3.)*

4. **Source attribution mislabels filtered/fallback cases** (`phase4-plan.md` §Code Sketch, lines 72–92; `phase3-plan.md` §Code Sketch, lines 91–99)
   - Expected: source correctly distinguishes spawn-override / llm_models / llm_model / default, including when all `llm_models` entries are filtered out
   - Found: when `llm_models` is non-empty but all entries invalid/filtered, source is labeled `"llm_models"` — but actual resolution fell through to `llm_model` or default. `_build_llm_config()` returns model only, not source; Phase 4 infers source from field truthiness which is incorrect for filtered cases.
   - *(Deeper variant of iter-001 blocking issue — default case added but filtered/fallback attribution path unhandled.)*

5. **Restore test doesn't exercise actual restore path** (`phase5-plan.md` §Task 5, lines 374–400)
   - Expected: test proves persisted selection is re-applied via the production restore/reload path
   - Found: test calls `get_instance_model()` on a restarted daemon — never calls `restore_instance()` or verifies DB-to-graph reconstruction. Can pass by reading DB value without rebuilding instance config.

6. **Incomplete spawn-path test coverage** (`plan-overview.md` §Research Insights; `phase5-plan.md` §Tasks 3, 6, 8)
   - Expected: all 5 convergent spawn paths + source-attribution precedence tested
   - Found: no tests for `spawn_instance_with_mcp`, `invoke_agent_and_wait`, explorer caller overrides, or default/fallback paths. HTTP smoke test uses placeholder endpoint/fixture. Concurrent test is sequential, doesn't prove all candidates are reachable.

7. **Invalid configuration policy inconsistency** (`phase1-plan.md` §Tasks 1–5; `phase2-plan.md` §Tasks 3–6)
   - Expected: single consistent policy for invalid config
   - Found: Phase 1 removes entire `llm_models` field on one invalid entry; Phase 2 filters entries individually. Different behaviors, untested. Weight type contract incomplete (booleans, numeric strings, floats, null unspecified).

### Notes (non-blocking)
- `random.uniform()` vs `randrange()` — boundary behavior (phase2 lines 124–129)
- Silent weight clamping — consider logging (plan-overview lines 79, 87)
- 70/30 distribution not directly tested (phase5 lines 69–85)
- Global `random.seed()` — consider dedicated `random.Random` instance for isolation
- Case-insensitive matching — persisted/returned spelling not defined
- API response contract left as "likely"/pseudocode (phase4 lines 17, 103–109)

### Unverified Items (worker flagged)
- Actual signatures/call graph of `_build_llm_config()`, `spawn_instance()`, `restore_instance()`, HTTP spawn endpoint — not independently inspected (scoped to 6 plan files)
- Whether existing system already freezes `llm_model`/global-default independently of `model_override` — plan asserts both "invisible" AND "behavior change"; unresolved ambiguity

### Verdict: REJECTED (7 blocking issues — correctness, consistency, and completeness gaps)
