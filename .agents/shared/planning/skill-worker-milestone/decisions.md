# Architecture Decisions — Skill-Per-Worker Milestone 2

## D1: Skill Loading via Message Meta Tag

**Decision**: Use a `<meta>{"load_skill": "skill-name"}</meta>` tag embedded in the message body, NOT a new API parameter.

**Rationale**: 
- The `send_message` API is used by many callers (agents, API endpoints, job queue). Adding a new parameter touches every call site.
- The meta tag is parsed at the message-processing layer, transparent to the API surface.
- The tag is stripped from the message before the agent sees it — no prompt pollution.
- Enables ANY message sender to specify a skill, not just API callers.

**Alternatives considered**:
- New `skill` parameter on `send_message()` API → too invasive, touches all callers
- System message injection → conflicts with existing system prompt composition
- Skill name in message subject/headers → no subject/header concept in the message model

**Implementation**: `daemon/services/skill_meta_parser.py` — regex extraction + JSON parse + strip

---

## D2: REPLACE vs MERGE for Meta-Tag Skill Scope

**Decision**: When a meta-tag skill is loaded, REPLACE `last_injected_skill_ids` (not dedup-merge).

**Rationale**:
- Meta-tag loading establishes a NEW skill scope — the old skill is no longer active
- The worker is executing a different task with a different skill
- Merging would pollute metrics: both old and new skills would get attributed to the new task's outcome
- The first-message injection pipeline (search-based) still uses dedup-merge — only meta-tag loading replaces

**Contrast**: Phase 2's auto_load tracking uses DEDUP-MERGE because auto_load + on-demand skills coexist on the tester instance. Different instance types, different strategies.

---

## D3: Injection Pipeline Extension — Separate Block, Not Inside `if not is_retry`

**Decision**: The meta-tag skill injection runs as a SEPARATE code block AFTER the existing `if not is_retry:` block, NOT inside it.

**Rationale**:
- The `if not is_retry:` gate at line 1680 covers project-context, shared-context, AND first-message skill injection
- Moving skill injection out of this gate would require restructuring the shared block
- A separate block is additive — doesn't touch existing first-message logic
- The separate block runs on ANY message (including retries) where `_meta_skill` is set
- `_skill_injection_msg` is a shared variable — the separate block can overwrite it

---

## D4: Composite Score Weights — 35/20/20/15/10

**Decision**: Default weights: completion_rate 35%, applied_rate 20%, efficiency_score 20%, low_fallback_rate 15%, speed_score 10%.

**Rationale**:
- Completion rate is the primary signal — task success matters most (35%)
- Applied rate is the adoption signal — if agents ignore the skill, it's not useful (20%)
- Efficiency (iterations) and fallback rate are reliability signals (20% + 15%)
- Speed is a secondary quality signal (10%)
- All configurable via `SKILL_EVOLUTION_AB_WEIGHT_*` env vars

---

## D5: Tie-Breaking — Challenger Wins Ties

**Decision**: When composite scores are equal, the NEW variant (challenger) wins, not the incumbent.

**Rationale**:
- Original: `rate_a >= rate_b` → incumbent wins ties (new must be *strictly* better)
- New: `score_b >= score_a` → challenger wins ties
- A new skill that is *equal* in performance may have improvements that don't show up yet in metrics
- Favoring evolution over stagnation

---

## D6: consecutive_failures → Tier 2 Analysis (Not Direct evolve_fix)

**Decision**: Route `consecutive_failures` through Tier 2 analysis like all other triggers.

**Rationale**:
- It was the ONLY trigger that skipped Tier 2 and went directly to Tier 3 variant creation
- Three unlucky failures → automatic variant creation (wasteful)
- Tier 2 cheap LLM gets a chance to say "this skill is fine, just unlucky"
- Consistency: all triggers now go through the same analyze → evolve path

---

## D7: Fallback Heuristic — `not task_succeeded`

**Decision**: Mark `fallback=True` when the task fails, regardless of `consecutive_failures` count.

**Rationale**:
- Original: `consecutive_failures > 0 AND not task_succeeded` — misses first-use failures
- In the skill-per-worker model, a single skill is responsible for the outcome
- If the task fails with this skill active, it's a fallback signal
- Aligns with clean 1:1 attribution: one skill = one outcome

---

## D8: Tester Keeps Only test-strategy as Auto_Load

**Decision**: Reduce tester auto_load from 4 to 1. Keep only `test-strategy`.

**Rationale**:
- `test-strategy` is used for PLANNING decisions (blast radius, change-set derivation)
- The other 3 (test-pack-execution, mock-test, unit-test) are EXECUTION skills
- Execution skills should be on workers for clean attribution
- Tester becomes a planner + dispatcher, not a direct executor

---

## D9: A/B Test-Period Isolation via `ab_test_group` Column

**Decision**: Add `ab_test_group TEXT NULL` column to `skill_usage_records`. Tag records during A/B testing. Filter stats by group.

**Rationale**:
- Current: `_completion_rate_for()` uses ALL historical records — pre-test data pollutes comparison
- With the column: `get_stats_filtered(skill_id, ab_test_group=group)` returns only test-period records
- Clean comparison: "variant A during test" vs "variant B during test"
- Migration-safe: column defaults to NULL, existing records are untagged

---

## D10: SQL Aggregation Replaces Python-Side Counting

**Decision**: Move rate computation from Python (`sum(1 for r in records if ...)`) to SQL (`func.sum(case(...))`).

**Rationale**:
- Python-side: O(n) — loads ALL records into memory per comparison check
- SQL-side: single GROUP BY query — database handles optimization
- Returns all needed metrics in one query (total, completions, applied, fallbacks, avg_iterations, avg_duration)
- Compatible with both SQLite and PostgreSQL via SQLAlchemy `func` + `case`


---

## D11: SUPERSEDED Outcome — Neutral Usage Record (C2)

**Decision**: Add a `superseded BOOLEAN DEFAULT FALSE` column to `skill_usage_records`. When a worker is reused with a different skill, dropped skills get a usage record with `superseded=True`.

**Rationale**:
- Without this, dropped skills have NO usage record — their contribution is invisible
- A SUPERSEDED record is NEUTRAL: increments `total_selections` (skill WAS selected) but excluded from `completion_rate` denominator (task didn't complete with this skill — it was replaced)
- `get_stats_filtered()` queries always include `WHERE superseded = FALSE` for rate calculations
- Prevents "orphaned pending record" problem identified in C2

---

## D12: Schema Allow-List for Meta Tag Keys (C1)

**Decision**: Only `load_skill` is a recognized meta tag key. Unknown keys are logged as warnings and silently ignored.

**Rationale**:
- Defense-in-depth: if an agent crafts a meta tag with dangerous keys (e.g. `{"load_skill":"x","role":"admin"}`), the unknown keys are ignored
- The parser extracts what it knows, logs what it doesn't, and never crashes
- Future keys can be added to `_ALLOWED_META_KEYS` frozenset

---

## D13: Last-Wins Policy for Multiple Meta Tags (C1)

**Decision**: If multiple `<meta>` tags exist in a message, the LAST valid one wins. ALL tags are stripped from the message.

**Rationale**:
- Multiple tags is an edge case (likely error or adversarial)
- Last-wins is deterministic and intuitive (most recent override)
- ALL tags stripped regardless of validity — agent should never see control data

---

## D14: Setter Pattern for Clone Service (W1)

**Decision**: Inject `SkillCloneService` into `SkillInjectionService` via a setter method (`set_clone_service()`), not constructor injection.

**Rationale**:
- Constructor chicken-and-egg: `SkillInjectionService` is constructed in `manager.py` BEFORE `_skill_clone_service` is available
- The clone service needs the skill repo, which may be wired after the injection service
- Setter injection decouples construction order — `set_clone_service()` called after both services exist

---

## D15: Silent Upgrade for ab_sample_size 10→20 (W5)

**Decision**: Existing open A/B tests silently upgrade to the new sample size (20). No per-test threshold storage; config is read at resolution-check time.

**Rationale**:
- Simplest implementation — no schema change needed for per-test threshold
- Tests with <20 comparisons simply collect more data
- Tests near 10 get 10 more comparisons — acceptable delay
- No force-resolve needed (no premature decisions)
- Manual resolution available via `winner_id` parameter for urgent cases

---

## D16: ab_test_group NULL = Not Under Test (W6)

**Decision**: `ab_test_group IS NULL` means "not under test". Records with NULL ab_test_group are EXCLUDED from A/B-scoped queries.

**Rationale**:
- Clean semantics: only non-NULL values participate in A/B comparison stats
- Pre-test records (before A/B test started) have NULL — correctly excluded
- Post-test records (after test resolved) have NULL — correctly excluded
- Only records created DURING the test period have the group UUID
- `get_stats_filtered(skill_id, ab_test_group="uuid")` returns only test-period records
- `get_stats_filtered(skill_id, ab_test_group=None)` returns ALL non-superseded records (general stats)

---

## D17: Schema Migration as Phase 1 Prerequisite (Issue 1)

**Decision**: Both `ab_test_group` and `superseded` columns (plus the `ix_skill_usage_records_skill_created` composite index) are added in Phase 1 Task 1.0, NOT Phase 3.

**Rationale**:
- Phase 1 Task 1.5 (`finalize_superseded_skills`) writes `superseded=True` — the column must exist
- Under the original plan, parallel execution of Phase 1 and Phase 3 would cause failed INSERTs in Phase 1.5
- Moving schema to Phase 1 eliminates the contradiction
- Phase 3 Task 3.1 becomes a verification task (columns already exist)
- All phases that write or read usage records now have a single schema prerequisite

---

## D18: Checkpoint Restore Safety via explicitly_replaced_ids (Issue 2)

**Decision**: Persist `explicitly_replaced_ids` in instance metadata on every explicit REPLACE. Phase 2's auto_load DEDUP-MERGE reads this set and skips any skill IDs in it.

**Rationale**:
- When an instance crashes and restores, `_apply_post_cache_appends()` re-runs `append_auto_load_skills()` with DEDUP-MERGE
- Without this set, auto_load would silently re-introduce skills that were explicitly REPLACED via `<meta>` tag
- This corrupts the REPLACE semantics and re-creates the multi-skill attribution problem
- The `explicitly_replaced_ids` set is overwritten on each new REPLACE (contains only the latest dropped set)

---

## D19: Aggregation Queries for Tier 2 Stats (Issue 3)

**Decision**: Switch the trigger engine's stats source from O(1) counter reads to O(log n + k) SQL aggregation queries via `get_stats_filtered()`.

**Rationale**:
- The Tier 2 prompt needs `avg_iterations`, `avg_duration`, `applied_rate` — these are NOT available from denormalized counter columns
- Adding counter columns for averages would require maintaining running averages (complex, race-prone)
- SQL aggregation via `SUM(CASE...)` and `AVG()` is simpler and correct
- The composite index `ix_skill_usage_records_skill_created` keeps aggregation efficient
- Performance tradeoff is acceptable: O(log n + k) with index vs O(1) counter read — the difference is negligible for typical skill usage volumes (<10K records per skill)

---

## D20: Fallback Heuristic — Option C, Worker Feedback-Driven (Issue 4)

**Decision**: Fallback is determined by the worker's explicit `skill_feedback(applied=False)` call, NOT by task success/failure.

**Rationale**:
- `fallback = not task_succeeded` (previous plan) makes the `high_fallback_rate` trigger (threshold 0.5) non-discriminating — every skill tested on hard tasks would eventually trigger
- The worker agent already has dense `skill_feedback` reinforcement — its `applied` judgment is a high-quality signal
- Worker judgment is INDEPENDENT of task difficulty: a skill can be helpful (applied=True) even on a failed task
- This decouples fallback from task outcome, making `high_fallback_rate` a genuine quality signal

**Rejected alternatives**:
- Option A (A/B loser = fallback): Requires completed A/B tests; doesn't help pre-A/B skills
- Option B (SUPERSEDED = fallback): SUPERSEDED is already a neutral outcome; conflating it with fallback would double-count quality signals
- `fallback = not task_succeeded`: Over-counts on hard tasks (Issue 4)

**Implementation**: `record_task_completion()` sets `fallback=False` (neutral default). `record_feedback()` sets `fallback=True` when `applied=False`, `fallback=False` when `applied=True`. `update_feedback()` gains a `fallback` parameter.

---

## D21: Two-Site Stats Source Fix (Issue 5)

**Decision**: Both `SkillMetricsService.get_skill_stats()` AND `SkillEvolutionService.analyze_skill()` line 187 must switch to `get_stats_filtered()`.

**Rationale**:
- The Tier 2 analysis path (`analyze_skill()`) fetches stats from `_usage_repo.get_stats()` (the OLD method) — NOT from `get_skill_stats()`
- Fixing only `get_skill_stats()` leaves the actual Tier 2 prompt path broken — new metrics are always 0.0
- Both call sites must be changed: `get_skill_stats()` for external callers, `analyze_skill()` for the Tier 2 prompt
- The dependency chain is: Phase 1 Task 1.0 (schema) → Phase 3 Task 3.3 (`get_stats_filtered()`) → Phase 4 Task 4.3 (both sites) → Phase 4 Task 4.2 (prompt reads real metrics)

---

## D22: Counter Increment for Fallback Trigger (Issue 6)

**Decision**: `record_feedback()` must increment/decrement `total_fallbacks` counter on state change, in addition to updating the usage record's `fallback` field.

**Rationale**:
- The `high_fallback_rate` trigger reads `total_fallbacks` COUNTER directly from the skills table — NOT from usage record aggregation
- Under Option C, `record_task_completion()` sets `fallback=False` (no counter bump)
- If `record_feedback()` only updates the record but not the counter, the counter stays permanently 0 → trigger permanently dead
- The `_prev_fallback` guard prevents double-counting: counter only changes on state transitions (False→True or True→False)
- This connects Option C's record-level fallback semantics to the trigger engine's counter-based evaluation