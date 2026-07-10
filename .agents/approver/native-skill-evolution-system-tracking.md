# Native Skill Evolution System — Tracking

## Iteration 001 (2026-07-10)
**Verdict: REJECTED**

### Blocking Issues

#### 1. `feedback_applied` column missing from schema
- **Where:** Referenced in Phase 3 (line 230), Phase 4 (lines 53, 68-69, 72-80), Phase 5 (lines 96, 283-284, 297-301, 392-394, 455)
- **Expected:** `feedback_applied` should be a column on `skill_usage_records` table (defined in Phase 1 models, Phase 1 SQL migration, and Phase 1 PostgreSQL parity)
- **Found:** `feedback_applied` is referenced as a column on SkillUsageRecord in Phase 1's model docstring (line 43: `feedback_applied (nullable bool)`) but is NOT included in the SQLite migration SQL (lines 178-200) or the PostgreSQL CREATE TABLE statement (lines 232-253). The column exists in the model description but is missing from both DDL statements.
- **Impact:** Capture flow (Phase 5) depends entirely on querying `feedback_applied` records. The `check_and_capture()` method queries `self._usage_repo.get_applied_for_instance(instance_id)` and `has_applied_for_instance(instance_id)` — neither repository method is defined. The entire capture flow is unbuildable as specified.

#### 2. `skill_injection: bool` is dead on agent load
- **Where:** Phase 1 Task 7 (lines 307-319), Phase 3 (lines 122, 243)
- **Expected:** Adding `skill_injection: bool = False` to `AgentMetadata` model should make it readable from agent `meta.json` files
- **Found:** `AgentMetadata` is constructed at `daemon/registry.py:195-210` with explicit kwargs. The constructor does NOT read `meta.get("skill_injection", False)`. Additionally, `model_config` has `extra="ignore"` (line 100), meaning Pydantic silently drops unknown fields from the JSON. Even if the field is added to the model class, it will always be `False` unless the constructor is updated to read it from the meta dict.
- **Impact:** No agent will ever have `skill_injection=true` regardless of meta.json content. The entire injection system (Phase 3) and capture flow (Phase 5) are gated on this flag and will never activate.

#### 3. Skill-keeper jobs route to FIFO queue, not parallel queue
- **Where:** Phase 5 Task 3 (lines 330-374), plan-overview.md risk table (line 120)
- **Expected:** Skill evolution jobs should use `system_parallel_queue` (concurrency=5) as stated in the plan
- **Found:** `JobQueueService.enqueue()` at `job_queue_service.py:590-598` (idempotency path) and `699-709` (non-idempotency path) resolves `queue_id=None` to `system_fifo_queue` (concurrency=1). The plan's `_enqueue_skill_keeper_job()` method (lines 366-374) calls `self._job_service.enqueue(agent_id="skill-keeper", message=..., source="skill_evolution", project_id=project_id, job_type=job_type, metadata=metadata)` with NO `queue_id` parameter. Jobs will land in FIFO queue.
- **Impact:** Skill evolution jobs will be serialized (concurrency=1), not parallelized (concurrency=5). While not a crash, it violates the stated architecture and could cause significant latency when multiple skills need analysis. The plan must either: (a) pass `queue_id` resolved via `queue_repo.get_by_name(project_id, "system_parallel_queue")`, or (b) use `enqueue_message` instead of `enqueue()`.

### Notes (non-blocking)
- A/B testing "extend by another N" has no upper bound — could run indefinitely for statistically tied variants. Recommend adding `max_extensions` or `force_resolve_after` cap.
- Injection hook prepends HumanMessage without `id=` parameter — existing messages use `id=message_id`. May cause checkpoint deduplication issues on retry.
- Injection hook is not gated by `if not is_retry` — could re-inject duplicate skill messages on checkpoint retry.

## Iteration 002 (2026-07-10)
**Verdict: REJECTED**

### Previously Fixed (verified — all 3 blocking + 3 notes correctly addressed)
1. ✅ `feedback_applied` + `feedback_note` columns added to SQLite DDL (line 248-249) and PostgreSQL DDL (line 354-355). `get_applied_for_instance()` and `has_applied_for_instance()` methods added to `SkillUsageRepository` (lines 126-140). Index `idx_skill_usage_applied` added.
2. ✅ `skill_injection: bool = False` added to `AgentMetadata` model (line 447-450) AND wired into constructor at `registry.py:195-210` with explicit `skill_injection=meta.get("skill_injection", False)` (line 495). Test requirement added (line 499).
3. ✅ `_resolve_parallel_queue_id()` method added (lines 372-383) using `queue_repo.get_by_name(project_id, "system_parallel_queue")`. All enqueue methods pass `queue_id=queue_id` explicitly (line 428).
4. ✅ `max_extensions: int = Field(default=3)` added to `SkillEvolutionConfig` (line 423). Force-resolve logic implemented in `check_ab_test_resolution()` (lines 257-262).
5. ✅ Injected HumanMessage has `id=str(_uuid.uuid4())` (lines 139-143).
6. ✅ `is_retry` gating verified — hook condition `if not is_retry and not is_completion_report` (line 110). Constraint documented (line 256).

### New Blocking Issues

#### 4. `extension_count` has no persistence — A/B `max_extensions` cap is dead code
- **Where:** Phase 4 `get_ab_comparison_stats()` return dict (line 111), Phase 5 `check_ab_test_resolution()` (lines 256-262)
- **Expected:** `extension_count` tracks how many times an A/B test has been extended (for the `max_extensions` force-resolve cap). It must be persisted so that `get_ab_comparison_stats()` can return it accurately.
- **Found:** `extension_count` appears in Phase 4's return dict (line 111) and Phase 5's cap logic (line 256: `extension_count = stats.get("extension_count", 0)`), but it is NOT stored in any table. The 5 tables defined in Phase 1 (`skills`, `skill_lineage`, `skill_usage_records`, `skill_triggers`, `skill_embeddings`) have no `extension_count` column. The `skills` table has `ab_test_group TEXT` (line 207/311) but no extension counter.
- **Impact:** `stats.get("extension_count", 0)` always returns `0`. The condition `extension_count >= self._config.max_extensions` (default 3) is never true. The force-resolve cap — the fix for the previous iteration's "no upper bound" note — is dead code. A/B tests with `difference < ab_min_difference` will extend infinitely, logging "extension 1/3" forever. The `max_extensions` config field has no effect.

#### 5. Skill-keeper agent has no tools to invoke `SkillEvolutionService` — cannot perform its core function
- **Where:** Phase 5 Task 1, `agents/skill-keeper/meta.json` (lines 36-38), Phase 2 tool definitions (lines 215-283)
- **Expected:** The skill-keeper agent is spawned via job queue to perform Tier 2 analysis and Tier 3 evolution. It receives messages like "Analyze skill {skill_id}. Reason: {reason}. Stats: {stats}" and must invoke `SkillEvolutionService.analyze_skill()`, `evolve_skill()`, `check_ab_test_resolution()`, etc.
- **Found:** The skill-keeper's allowed tools are `["bash", "filesystem", "self", "help", "knowledge", "dynamic-skill"]` (line 37). The `dynamic-skill` category exposes 6 tools: `skill_search`, `skill_list`, `skill_view`, `skill_create`, `skill_fix`, `skill_feedback` (Phase 2, lines 215-283). **None of these wrap any `SkillEvolutionService` method.** The skill-keeper can view and create skills, but cannot trigger analysis, perform evolution, resolve A/B tests, or execute captures. `skill_fix` enqueues a `skill_analysis` job — which would create a recursive loop (skill-keeper enqueuing a job to itself).
- **Impact:** The entire Phase 5 evolution engine is structurally unbuildable as specified. The skill-keeper agent receives a message to analyze/evolve a skill but has no tool to do so. Verified: `list_pending_by_project` at `job_queue_service.py:706` picks up non-`message` job types (only filters `job_type != "message"`), so skill-keeper jobs WILL be dispatched and spawned as LangGraph agents — agents that cannot act on their instructions.

### Notes (non-blocking)
- `check_and_capture()` body ends with placeholder `# If conditions met, call _evolve_captured with task details` (line 322). Docstring says "enqueue skill_capture job." Intent is clear (use `enqueue_capture()`), but the implementer needs to disambiguate.
- `_get_task_details(canonical_job_id)` in Phase 4 (line 295) is a forward-reference to a method that doesn't exist. The plan's Note (lines 310-312) provides approximation guidance (`iterations` = count of AI messages, `duration_seconds` = `now() - task.started_at`). Not architecturally impossible — the Task model has `started_at`/`completed_at` and `instance_id` is available — but the helper needs explicit specification.
- `create_skill()` calls `update_skill_embeddings()` with no error handling (Phase 2, line 101). The constraint "must gracefully degrade if endpoint unavailable" (line 373) is stated but not reflected in the implementation stubs. Developer would need to wrap the embedding call in try/except.

## Iteration 003 (2026-07-10)
**Verdict: REJECTED — Max iterations reached (3/3). Escalating to user.**

### Previously Fixed (verified — all iteration 002 issues correctly addressed)
1. ✅ `skill_ab_tests` table (6th table) added with `extension_count`, `comparisons`, `winner_skill_id` columns. DDL present in both SQLite (lines 319-331) and PostgreSQL (lines 446-459). `SkillABTestRepository` with `create_ab_test()`, `get_by_group()`, `increment_comparison()`, `increment_extension()`, `resolve()`, `get_active_tests()` (lines 169-200). A/B resolution reads from repository (lines 271-272, 285, 304).
2. ✅ 5 privileged `skill-evolution` tools added: `skill_analyze`, `skill_evolve`, `skill_resolve_ab`, `skill_get_metrics`, `skill_execute_capture` (lines 530-615). Full 5-step registration (lines 617-634). Skill-keeper meta.json has `"skill-evolution"` in `tools.allow` (line 37). Recursive loop broken (design note lines 636-640).
3. ✅ `check_and_capture()` body explicitly calls `enqueue_capture()` (line 355).
4. ✅ `_get_task_details()` fully implemented with JobItem lookup, message count for iterations, timestamp diff for duration (Phase 4, lines 314-370).
5. ✅ `create_skill()` wraps `update_skill_embeddings()` in try/except with graceful degradation (Phase 2, lines 103-108). Also applied in `_evolve_fix()` (Phase 5, lines 212-216).

### New Blocking Issues (iteration 003 — UNRESOLVED, max iterations reached)

#### 6. `increment_comparison()` is never called — A/B test counter stays at 0, tests never resolve
- **Where:** `SkillABTestRepository.increment_comparison()` defined in Phase 1 (line 179), `get_ab_comparison_stats()` reads `comparisons` from `skill_ab_tests.comparisons` (Phase 4, line 109), `check_ab_test_resolution()` checks `stats["ready_to_resolve"]` which requires `comparisons >= ab_sample_size` (Phase 5, line 274)
- **Expected:** When a variant is selected during injection (Phase 5 Task 5, line 525), `increment_comparison()` should be called to track that a comparison was made.
- **Found:** `increment_comparison()` is defined (Phase 1) and tested (Phase 1, line 606), but **never called** in any phase. The injection flow calls `await self._record_ab_selection(selected.id, skill.ab_test_group)` (line 525) — a method that does not exist anywhere in the plan. Even if implemented, `_record_ab_selection` is not documented to call `increment_comparison()`.
- **Impact:** `skill_ab_tests.comparisons` stays at 0 forever. `ready_to_resolve` is never true (requires `comparisons >= 10`). `needs_more_data` is never true (requires `comparisons >= 10`). The entire A/B resolution path is dead code. A/B tests run indefinitely — the `max_extensions` cap (iteration 002's fix) is also dead code because it's only checked when `needs_more_data` is true.

#### 7. `_evolve_captured()` has three incompatible call signatures — runtime TypeError
- **Where:** Phase 5, `evolve_skill()` (line 163), `_evolve_captured()` (line 229), `skill_execute_capture` tool (line 608)
- **Expected:** A single consistent interface for the CAPTURED evolution flow.
- **Found:** Three incompatible signatures:
  - `evolve_skill()` calls `self._evolve_captured(skill, direction)` — passes `(Skill, str)`
  - `_evolve_captured(self, task_details: dict)` — expects single `dict`
  - `skill_execute_capture` tool calls `SkillEvolutionService.capture_skill()` — a method that does not exist
- **Impact:** CAPTURED flow crashes at runtime. `evolve_skill()` with `evolution_type="CAPTURED"` passes `(skill, direction)` to a method expecting `(task_details: dict)` → TypeError. The `skill_execute_capture` tool references a nonexistent `capture_skill()` method → AttributeError.

### Summary of all unresolved issues across 3 iterations

| # | Issue | Iteration Found | Status |
|---|-------|-----------------|--------|
| 6 | `increment_comparison()` never called — A/B tests never resolve | 003 | UNRESOLVED |
| 7 | `_evolve_captured()` signature mismatch (3 incompatible interfaces) | 003 | UNRESOLVED |

### Additional notes (non-blocking, for implementation reference)
- No guard in `_evolve_fix()` against evolving a skill already in active A/B testing — second FIX overwrites `ab_test_group`, orphaning the first `skill_ab_tests` record.
- No `IntegrityError` handling in `_evolve_fix()` for concurrent evolution of the same skill — UNIQUE(project_id, name, generation) would crash the second job.
- Plan overview success criteria (line 129) still says "5 repositories" instead of 6; line 131 doesn't list the 5 `skill-evolution` tools.
