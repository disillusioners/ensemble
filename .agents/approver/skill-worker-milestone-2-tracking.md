# Skill-Worker Milestone 2 — Tracking

## Iteration 001 — REJECTED
**Date**: 2026-07-15
**Verdict**: REJECTED — 4 blocking issues

### Blocking Issues

1. **Phase 1.5 ↔ Phase 3.1 schema contradiction** — The plan claims Phases 1, 2, 4 can run in PARALLEL, but Phase 1 Task 1.5 (`finalize_superseded_skills`) writes `superseded=True` to `SkillUsageRecord` — a column that Phase 3 Task 3.1 adds. The plan itself notes: "implementation order MUST be: Phase 3 schema migration → Phase 1 finalize logic." This is a direct contradiction with the parallelization claim. Under genuine parallel execution, Phase 1.5 produces failed INSERTs.

2. **C3 ordering invariant breaks at restore** — The plan claims explicit REPLACE (Phase 1) and auto_load DEDUP-MERGE (Phase 2) are "naturally ordered by lifecycle stage." This is wrong: on a reused instance where a crash happens between an explicit `<meta>` REPLACE and natural completion, restore re-runs `_apply_post_cache_appends` (which calls `append_auto_load_skills`). If auto_load skills have the same identity as a skill in the previous explicit REPLACE set, the DEDUP-MERGE reintroduces them alongside the explicit set, corrupting the REPLACE semantics. Required: persist `explicitly_replaced_ids` across checkpoints and have auto_load merge skip those IDs.

3. **Phase 4 Task 4.3 is not feasible as written** — Task 4.3 says: "update `get_skill_stats()` to use `get_stats_filtered()`... which returns the additional fields." But the actual `get_skill_stats()` (skill_metrics_service.py:820-910) reads from DENORMALIZED counter columns on the `skills` table (`total_selections`, `total_completions`, `total_fallbacks`, `total_applied`). There are NO `avg_iterations` or `avg_duration` columns. Either:
   - Add new counter columns to `skills` (hidden Phase 3+ dependency) OR
   - Compute at query time from `SkillUsageRecord` aggregation
   The plan does not specify which path. Phase 4's "independent of Phase 3" claim is unsubstantiated.

4. **Fallback heuristic change breaks `high_fallback_rate` trigger** — Changing `fallback = not task_succeeded` means EVERY failed task is a fallback. With `high_fallback_rate` trigger threshold at 0.5 (skill_trigger_seed.py), every skill would eventually trigger this. The metric becomes non-discriminating. The plan's own goal is "clean 1:1 metrics attribution" — a broken fallback metric breaks the rubric the plan exists to build. Required: use C2 superseded records + ab-test-loser records as the fallback numerator.

### Non-Blocking Notes
- C1 (meta tag regex), C2 (finalize-on-replace concept), C3 (ordering concept) are well-designed
- D1-D16 decisions are sound
- Phase 5 (tester prompt changes) and Phase 6 (tests) are well-scoped
- Composite score weights (35/20/20/15/10) are reasonable
- Tie-breaking change (challenger wins) is a good evolution-pressure choice

---

## Iteration 002 — REJECTED
**Date**: 2026-07-15
**Verdict**: REJECTED — 2 new blocking issues (all 4 previous issues addressed)

### Previous Issues — Status
1. ✅ Schema migration moved to Phase 1 Task 1.0 — RESOLVED correctly
2. ✅ `explicitly_replaced_ids` persisted + auto_load skip logic — RESOLVED correctly
3. ✅ SQL aggregation via `get_stats_filtered()` instead of counter columns — RESOLVED (concept correct, but see new Issue A)
4. ✅ Option C worker feedback-driven fallback — RESOLVED (concept correct, but see new Issue B)

### New Blocking Issues (Blind spots in previous review)

**A. `analyze_skill()` reads from wrong stats source — Tier 2 prompt never receives new metrics**

Task 4.3 changes `SkillMetricsService.get_skill_stats()` (metrics service, line 820) to delegate to `get_stats_filtered()`. Task 4.2 enhances `_build_analysis_prompt()` to display `applied_rate`, `avg_iterations`, `avg_duration`. However, the Tier 2 analysis path (`analyze_skill()` at `skill_evolution_service.py:158`) fetches stats from a DIFFERENT method: `self._usage_repo.get_stats(skill_id)` (line 187). This is the `SkillUsageRepository.get_stats()` method (repository.py:995), which is the OLD Python-side aggregation that returns only `{total, selected, applied, completions, fallbacks, completion_rate, fallback_rate}` — it does NOT include `avg_iterations`, `avg_duration`, or `applied_rate`.

The call chain:
```
skill_analyze tool (skill_evolution_tools.py:171)
  → analyze_skill(skill_id, stats=None)  (skill_evolution_service.py:158)
    → self._usage_repo.get_stats(skill_id)  (line 187, OLD method)
      → returns dict WITHOUT avg_iterations/avg_duration/applied_rate
    → _build_analysis_prompt(skill, stats, ...)  (line 204)
      → stats.get("applied_rate", 0.0) → always 0.0
      → stats.get("avg_iterations", 0.0) → always 0.0
```

The plan does not mention updating the `analyze_skill()` stats fetch from `_usage_repo.get_stats` to `get_stats_filtered()`. The enhanced prompt (Task 4.2) will always display 0.0 for the new metrics.

- Expected: Tier 2 prompt should show real `applied_rate`, `avg_iterations`, `avg_duration` values
- Found: `analyze_skill()` calls `_usage_repo.get_stats` (old method) instead of `_usage_repo.get_stats_filtered` (new method). New metrics are always 0.0.

**B. Option C breaks `total_fallbacks` counter — `high_fallback_rate` trigger never fires**

Under Option C (Task 4.4):
- `record_task_completion()` → `_record_one()` sets `fallback = False` (always — no longer uses the old heuristic)
- `record_feedback()` sets `fallback` on the usage **record** via `update_feedback(record_id, ..., fallback=True)` when `applied=False`
- BUT the plan does NOT add `increment_counter(skill_id, "total_fallbacks", 1)` to `record_feedback()`

The trigger engine evaluates `high_fallback_rate` by reading `skill.total_fallbacks` COUNTER directly (trigger_engine.py:404: `getattr(skill, "total_fallbacks", 0)`), NOT from `get_skill_stats()` and NOT from usage record aggregation. The trigger evaluation flow:
```
_evaluate_condition(trigger, skill)  (line 141)
  → _eval_high_fallback_rate(skill, condition)  (line 294)
    → getattr(skill, "total_fallbacks", 0)  (line 404) ← reads COUNTER, always 0 under Option C
    → rate = fallbacks / selections → 0.0 / N = 0.0
    → return 0.0 > 0.5 → always False
```

Task 4.3's change to `get_skill_stats()` is irrelevant here — `get_skill_stats()` is called AFTER a trigger fires (line 160, for stats/reason building), not during evaluation. The trigger engine never uses it for condition checking.

The plan explicitly claims (Phase 4 Task 4.4): "No threshold change needed: the high_fallback_rate threshold of 0.5 remains appropriate." But the trigger will never fire because `total_fallbacks` is never incremented.

- Expected: `high_fallback_rate` trigger should fire when workers consistently report skills as unhelpful (applied=False)
- Found: `total_fallbacks` counter is never incremented under Option C. The trigger evaluates against the counter directly, so `high_fallback_rate` is permanently dead.

### Non-Blocking Notes
- The 4 previous issues are genuinely resolved — the schema, checkpoint safety, aggregation approach, and fallback concept are all correct
- The two new issues are implementation-path disconnects: the concept is sound but the plan doesn't follow the code paths to their actual execution sites
- Issue A fix: `analyze_skill()` line 187 should call `self._usage_repo.get_stats_filtered(skill_id, ab_test_group=None)` instead of `self._usage_repo.get_stats(skill_id)`
- Issue B fix: `record_feedback()` should add `increment_counter(skill_id, "total_fallbacks", 1)` when `applied=False` AND `increment_counter(skill_id, "total_fallbacks", -1)` if later updated to `applied=True` (or restructure to avoid double-counting)
- C1/C2/C3 fixes, D17-D20 decisions, Phase 5/6, composite scoring — all sound

---

## Iteration 003 — APPROVED
**Date**: 2026-07-15
**Verdict**: APPROVED
**Council Verification**: Multi-model council confirmed both fixes against actual source code

### Previous Issues — Status
1. ✅ Issue 5 (D21) — `analyze_skill()` line 187 now switches from `get_stats()` to `get_stats_filtered()` — CONFIRMED at code-path level. Single connection point to `_build_analysis_prompt()`. No bypass path.
2. ✅ Issue 6 (D22) — `record_feedback()` now increments/decrements `total_fallbacks` counter with `_prev_fallback` guard — CONFIRMED. Trigger engine reads counter at `trigger_engine.py:404`, plan correctly moves counter bump from `record_task_completion()` to `record_feedback()`.

### Cross-Cutting Verification
- ✅ `_build_reason()` reads `stats['fallback_rate']` — served by `get_skill_stats()` which delegates to `get_stats_filtered()`. Key preserved. No break.
- ✅ Other callers of `get_stats()` — 2 found (one becomes dead code, one test-only). Both addressed.
- ✅ `applied=None` path correctly skips fallback update (no `fallback` kwarg → None → field unchanged).

### Non-Blocking Notes (Implementation Pitfalls for the implementer)
1. `asyncio.to_thread` wrapper must be preserved — `get_stats_filtered()` is sync (uses `Session`). Plan snippets are illustrative, not literal.
2. `update_feedback()` `applied` type changes from `bool` to `Optional[bool]` — current coercion `bool(applied_bool) if applied_bool is not None else False` in `record_feedback()` must be removed to pass None through.
3. `_completion_rate_for()` (skill_metrics_service.py:1001-1021) becomes orphaned dead code after Phase 3 Task 3.6 rewrites `get_ab_comparison_stats()`. Harmless but should be cleaned up.
4. Double-fetch in planned `record_feedback()` — plan fetches `get_latest_for_skill_instance()` for `_prev_fallback`, but `record_feedback()` already fetched the same record at line 767. Reuse the existing `record` variable.
