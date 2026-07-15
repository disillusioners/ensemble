# Phase 4: Trigger & Tier 2 Enhancements

## Objective
Route the `consecutive_failures` trigger through Tier 2 analysis (like all other triggers) instead of jumping directly to Tier 3 `evolve_fix`. Enhance the Tier 2 analysis prompt with efficiency averages and applied_rate. Fix the fallback heuristic that misses first-use failures.

## Coupling
- **Depends on**: None (independent of Phases 1-3)
- **Coupling type**: mixed — Tasks 4.1, 4.2, 4.4 are independent; **Task 4.3 depends on Phase 1 Task 1.0 (schema) and Phase 3 Task 3.3 (`get_stats_filtered()`)**. Phase 4 is NOT fully parallelizable.
- **Shared files with other phases**: `daemon/services/skill_evolution_service.py` (Phase 3 also touches this — but different methods)
- **Shared APIs/interfaces**: `_build_analysis_prompt()` signature unchanged (additive data)
- **Why this coupling**: Code changes are in different methods than Phase 3. `_build_analysis_prompt` is enhanced, Phase 3 changes `_pick_winner`. No conflict.

## Context
- Trigger seed at `daemon/services/skill_trigger_seed.py:55-86` defines 5 triggers
- `consecutive_failures` has `action="evolve_fix"` — ALL others have `action="analyze"`
- `evolve_fix` dispatch at `manager.py:2184-2190` → `enqueue_evolution(evolution_type="FIX")` (Tier 3)
- `analyze` dispatch at `manager.py:2177-2183` → `enqueue_analysis()` (Tier 2)
- Tier 2 analysis prompt at `skill_evolution_service.py:1058-1127` (`_build_analysis_prompt`)
- Current prompt sends: total selections, completion_rate, fallback_rate, consecutive_failures, recent records
- Missing from prompt: applied_rate, avg_iterations, avg_duration, feedback_notes

## Tasks

### Task 4.1: Route `consecutive_failures` Through Tier 2

**File**: `daemon/services/skill_trigger_seed.py`

Change the seed for `consecutive_failures` (line 69-73):

```python
# BEFORE:
{
    "name": "consecutive_failures",
    "condition_type": "consecutive_failures",
    "condition_json": {"threshold": 3},
    "action": "evolve_fix",
},

# AFTER:
{
    "name": "consecutive_failures",
    "condition_type": "consecutive_failures",
    "condition_json": {"threshold": 3},
    "action": "analyze",  # CHANGED: Route through Tier 2 first
},
```

**Migration note**: The seed function (`seed_default_triggers`) runs at startup and is idempotent. However, existing databases may already have the old `action="evolve_fix"` row. Add an update step:

```python
# In seed_default_triggers() or a new migration helper:
def _update_consecutive_failures_action(trigger_repo):
    """Update existing consecutive_failures trigger to use 'analyze' action."""
    triggers = trigger_repo.list(enabled_only=False)
    for t in triggers:
        if t.condition_type == "consecutive_failures" and t.action == "evolve_fix":
            t.action = "analyze"
            trigger_repo.update(t)
            logger.info("Updated consecutive_failures trigger action: evolve_fix → analyze")
```

Call this update step during manager startup (near the `seed_default_triggers` call).

### Task 4.2: Enhance Tier 2 Analysis Prompt

**File**: `daemon/services/skill_evolution_service.py`

Update `_build_analysis_prompt()` (lines 1058-1127) to include additional metrics:

```python
@staticmethod
def _build_analysis_prompt(
    skill: Any,
    stats: Optional[dict],
    usage_records: list,
    reason: str,
) -> str:
    stats = stats or {}
    content = (getattr(skill, "content", "") or "")[:1500]

    # ── Existing metrics ──
    completion_rate = stats.get("completion_rate", 0.0)
    fallback_rate = stats.get("fallback_rate", 0.0)
    total = stats.get("total", 0)
    consecutive_failures = stats.get("consecutive_failures", 0)

    # ── NEW: Additional metrics (Milestone 2 Phase 4) ──
    applied_rate = stats.get("applied_rate", 0.0)
    avg_iterations = stats.get("avg_iterations", 0.0)
    avg_duration = stats.get("avg_duration", 0.0)

    # ── Enhanced recent records (include iterations + duration) ──
    recent_lines: list[str] = []
    for rec in usage_records[:10]:
        ok = getattr(rec, "task_succeeded", None)
        note = getattr(rec, "feedback_note", "") or ""
        iters = getattr(rec, "iterations", "?")
        dur = getattr(rec, "duration_seconds", "?")
        recent_lines.append(
            f"- succeeded={ok} iterations={iters} duration={dur}s feedback={note!r}"
        )
    recent_block = "\n".join(recent_lines) or "(no recent records)"

    return (
        "You are an expert at analyzing skill performance.\n\n"
        f"Skill name: {skill.name}\n"
        f"Skill description: {skill.description}\n"
        f"Skill content (first 1500 chars):\n{content}\n\n"
        f"Stats:\n"
        f"- total selections: {total}\n"
        f"- completion_rate: {completion_rate}\n"
        f"- applied_rate: {applied_rate}\n"              # NEW
        f"- fallback_rate: {fallback_rate}\n"
        f"- avg_iterations: {avg_iterations}\n"           # NEW
        f"- avg_duration: {avg_duration}s\n"              # NEW
        f"- consecutive_failures: {consecutive_failures}\n\n"
        f"Reason for this analysis: {reason or '(none)'}\n\n"
        f"Recent usage (up to 10 records):\n{recent_block}\n\n"
        "Decide whether this skill should evolve. Reply with a "
        "single JSON object with exactly these keys:\n"
        '  "should_evolve": <true|false>,\n'
        '  "evolution_type": "FIX" | "DERIVED" | "CAPTURED" | "NONE",\n'
        '  "direction": "<short instruction for the evolution LLM>",\n'
        '  "analysis_summary": "<one paragraph rationale>"\n\n'
        "Definitions:\n"
        '- FIX: the skill is broken or underperforming; tweak '
        'it in place (A/B test against the current version).\n'
        '- DERIVED: the skill is fine but a specialized sibling '
        'would help on a sub-task.\n'
        '- CAPTURED: a NEW pattern should be extracted from '
        'observed usage (do not use here — use the capture flow).\n'
        '- NONE: the skill is healthy, no action needed.\n\n'
        "Return ONLY the JSON object. No markdown fences, no prose."
    )
```

**Key additions:**
1. `applied_rate` — shows agent adoption signal
2. `avg_iterations` — efficiency signal (high iterations = skill unclear)
3. `avg_duration` — speed signal
4. Recent records now include `iterations` and `duration` per record

### Task 4.3: Switch Tier 2 Stats Source to Aggregation Queries

> **⚠️ BLOCKING PREREQUISITE: Depends on Phase 1 Task 1.0 (schema) AND Phase 3 Task 3.3 (`get_stats_filtered()`).** This task CANNOT be implemented until both are complete. Phase 4 Task 4.3 is NOT independent of Phase 3.

**Problem (Issue 3)**: `get_skill_stats()` reads from denormalized counter columns (`total_selections`, `total_completions`, etc.). There are NO `avg_iterations` or `avg_duration` counter columns — these can only come from aggregation queries. The Tier 2 prompt (Task 4.2) needs `applied_rate`, `avg_iterations`, `avg_duration` which are NOT available from counters.

**Solution**: Switch the trigger engine's stats source from counter-based `get_skill_stats()` to aggregation-based `get_stats_filtered()`. This requires:

**File**: `daemon/services/skill_metrics_service.py`

Update `get_skill_stats()` (the method the trigger engine calls at line ~155) to delegate to `get_stats_filtered()`:

```python
def get_skill_stats(self, skill_id: str) -> dict[str, Any]:
    """Get aggregated stats for a skill (trigger engine entry point).

    Issue 3 FIX: Switched from counter-based reads to SQL aggregation
    via get_stats_filtered(). This provides avg_iterations, avg_duration,
    and applied_rate which are NOT available from denormalized counters.

    The aggregation query uses the ix_skill_usage_records_skill_created
    composite index (added in Phase 1 Task 1.0) for efficient lookups
    as usage records grow.

    Args:
        skill_id: The skill to get stats for.

    Returns:
        Dict with: total, selected, applied, completions, fallbacks,
        avg_iterations, avg_duration, completion_rate, applied_rate,
        fallback_rate.
    """
    return self.usage_repo.get_stats_filtered(skill_id, ab_test_group=None)
```

**Issue 5 FIX — `analyze_skill()` uses wrong stats method (THE ACTUAL TIER 2 PATH)**:

The `get_skill_stats()` fix above handles external callers (e.g. API endpoints).
But the **Tier 2 analysis path** — the one that actually feeds the prompt —
does NOT go through `get_skill_stats()`. It goes through `analyze_skill()`
in `skill_evolution_service.py`, which fetches stats from a DIFFERENT method:

```python
# skill_evolution_service.py line 187 (CURRENT — BROKEN):
stats = self._usage_repo.get_stats(skill_id)  # OLD method — no averages
```

This is the OLD `SkillUsageRepository.get_stats()` that returns only
`{total, selected, applied, completions, fallbacks, completion_rate, fallback_rate}`.
The new metrics (`applied_rate`, `avg_iterations`, `avg_duration`) are always 0.0.

**Full call chain that must be fixed:**
```
skill_analyze tool
  → analyze_skill(stats=None) [skill_evolution_service.py:158]
    → self._usage_repo.get_stats(skill_id) [line 187]  ← MUST CHANGE
    → _build_analysis_prompt(stats) [line ~200]
      → stats.get("applied_rate", 0.0)   ← always 0.0 without fix
      → stats.get("avg_iterations", 0)   ← always 0
      → stats.get("avg_duration", 0)     ← always 0
```

**File**: `daemon/services/skill_evolution_service.py`

Change line 187 in `analyze_skill()`:

```python
# BEFORE (line 187):
stats = self._usage_repo.get_stats(skill_id)

# AFTER:
stats = self._usage_repo.get_stats_filtered(skill_id, ab_test_group=None)
```

This is the fix that actually delivers new metrics to the Tier 2 prompt.
Both changes are needed:
1. `SkillMetricsService.get_skill_stats()` — for external callers (API, trigger engine stats payload)
2. `SkillEvolutionService.analyze_skill()` line 187 — for Tier 2 analysis prompt (THE critical path)

**Updated dependency chain**:
```
Phase 1 Task 1.0 (schema: ab_test_group + superseded + indexes)
    ↓
Phase 3 Task 3.3 (get_stats_filtered() implementation)
    ↓
Phase 4 Task 4.3:
    4.3a: SkillMetricsService.get_skill_stats() delegates to get_stats_filtered()
    4.3b: SkillEvolutionService.analyze_skill() line 187 → get_stats_filtered()  ← THE FIX
    ↓
Phase 4 Task 4.2 (Tier 2 prompt reads new metrics — NOW actually receives them)
```

**File**: `daemon/repositories/skill/repository.py` — Index requirement

The `get_stats_filtered()` query (Phase 3 Task 3.3) filters by `skill_id` and computes `AVG()`, `SUM(CASE...)`. Without an index, this is a full-table scan per skill. The composite index added in Phase 1 Task 1.0 solves this:

```sql
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_skill_created
    ON skill_usage_records(skill_id, created_at)
```

This index supports:
- Fast `WHERE skill_id = ?` lookups (leftmost prefix)
- Future time-window queries (`WHERE skill_id = ? AND created_at > ?`)

**Performance note**: The switch from O(1) counter reads to O(log n + k) aggregation queries is a deliberate tradeoff. Counter reads are fast but can't provide averages. The composite index keeps aggregation efficient. For very large datasets (>100K records per skill), consider materialized views — but that's a future optimization, not needed now.

**Dependency chain**:
```
Phase 1 Task 1.0 (schema: ab_test_group + superseded + indexes)
    ↓
Phase 3 Task 3.3 (get_stats_filtered() implementation)
    ↓
Phase 4 Task 4.3 (switch trigger engine to use get_stats_filtered())
    ↓
Phase 4 Task 4.2 (Tier 2 prompt reads new metrics from get_skill_stats())
```

### Task 4.4: Fallback Heuristic — Option C (Worker Feedback-Driven)

> **⚠️ Issue 4 FIX.** The previous plan proposed `fallback = not task_succeeded` — marking EVERY failed task as a fallback. This is WRONG: it corrupts the `high_fallback_rate` trigger (threshold 0.5). A skill tested on 10 difficult tasks where 6 fail → `fallback_rate = 0.6` → triggers evolution. But the skill might be performing well — the tasks were just hard. The metric becomes non-discriminating.

**Decision: Option C — Worker feedback determines fallback.**

The worker agent already has dense `skill_feedback` reinforcement (in `rule.md` and `workflow.md`). The worker's explicit judgment (`applied=False`, meaning "skill was not relevant/helpful") is a far more discriminating signal than task success/failure.

**File**: `daemon/services/skill_metrics_service.py`

**Change 1**: Update `record_task_completion()` → `_record_one()` fallback computation:

```python
# BEFORE (current):
# fallback = consecutive_failures > 0 and not task_succeeded
# (misses first-use failures, but at least doesn't over-count)

# REJECTED (previous plan revision):
# fallback = not task_succeeded
# (Issue 4: corrupts high_fallback_rate trigger — every hard task = fallback)

# AFTER (Option C — worker feedback-driven):
# Fallback is determined by the worker's explicit skill_feedback call,
# NOT by task success/failure. The record_task_completion() method
# creates the usage record with fallback=False (default). The
# record_feedback() method (called when worker calls skill_feedback)
# updates the record's fallback field based on the worker's judgment.
#
# record_task_completion() sets fallback=False (neutral default):
fallback = False  # Will be updated by record_feedback() if worker reports

# record_feedback() sets fallback based on worker's applied flag:
#   applied=True  → fallback stays False (skill helped)
#   applied=False → fallback=True (skill did NOT help — real quality signal)
#   applied=None  → fallback stays False (no feedback — neutral)
```

**Change 2**: Update `record_feedback()` to set `fallback` on the usage record AND increment/decrement the `total_fallbacks` counter (Issue 6 FIX):

> **⚠️ Issue 6 FIX — Counter increment is REQUIRED.** The trigger engine reads `total_fallbacks` COUNTER directly from the skills table (`trigger_engine.py` line 404: `getattr(skill, "total_fallbacks", 0)`). Under Option C, `record_task_completion()` sets `fallback=False` (no counter bump). If `record_feedback()` only updates the record but NOT the counter, the counter stays permanently 0 → `high_fallback_rate` trigger is permanently dead.

```python
# In record_feedback(), after updating feedback_applied:

# Fetch the existing record to check if fallback is changing
existing_record = usage_repo.get_latest_for_skill_instance(skill_id, instance_id)
_prev_fallback = getattr(existing_record, "fallback", False) if existing_record else False

if applied_bool is False:
    # Worker explicitly said skill was NOT applied/helpful.
    # This is a discriminating fallback signal (Option C).
    usage_repo.update_feedback(
        record_id,
        applied=applied_bool,
        note=note,
        fallback=True,
    )
    # Issue 6: Increment total_fallbacks counter ONLY if this is a
    # state change (was not fallback before, now is).
    if not _prev_fallback:
        self.skill_repo.increment_counter(skill_id, "total_fallbacks", 1)

elif applied_bool is True:
    usage_repo.update_feedback(
        record_id,
        applied=applied_bool,
        note=note,
        fallback=False,
    )
    # Issue 6: Decrement total_fallbacks counter if this is a reversal
    # (was fallback before, now worker says skill helped).
    if _prev_fallback:
        self.skill_repo.increment_counter(skill_id, "total_fallbacks", -1)

else:
    # applied=None — no feedback, leave fallback as default (False)
    usage_repo.update_feedback(
        record_id,
        applied=applied_bool,
        note=note,
    )
```

**Why both record AND counter must be updated:**

| What reads it | Where it reads | What Option C must update |
|---------------|---------------|--------------------------|
| Tier 2 prompt stats | `skill_usage_records` table (via aggregation) | Record `fallback` field ✓ |
| `high_fallback_rate` trigger | `skills.total_fallbacks` COUNTER | Counter via `increment_counter()` ✓ |
| `_eval_high_fallback_rate` | `total_fallbacks / total_selections` ratio | Both counters must be accurate ✓ |

Without the counter increment, the trigger computes `0 / N = 0.0` → `0.0 > 0.5` is always False → **trigger permanently dead**. The counter is what makes the trigger fire, not just the record field.

**Guard against double-counting**: The `_prev_fallback` check ensures the counter only changes on state transitions (False→True or True→False), not on redundant calls with the same value. This handles the edge case where `record_feedback()` is called multiple times for the same skill/instance pair.

**Change 3**: Update `SkillUsageRepository.update_feedback()` to accept `fallback` parameter:

```python
def update_feedback(
    self,
    record_id: str,
    applied: Optional[bool],
    note: str,
    fallback: Optional[bool] = None,  # NEW — Option C
) -> None:
    """Update feedback fields on a usage record.

    Args:
        record_id: The usage record ID.
        applied: Worker's applied judgment (True/False/None).
        note: Worker's free-text feedback.
        fallback: When provided, sets the fallback flag based on
            worker judgment (Option C). True = skill was NOT helpful
            (applied=False). False = skill helped (applied=True).
            None = no change (leave existing value).
    """
    updates = {"feedback_applied": applied, "feedback_note": note}
    if fallback is not None:
        updates["fallback"] = fallback
    # ... apply updates ...
```

**Why Option C is correct:**

| Scenario | Task Result | Worker Feedback | Fallback | high_fallback_rate trigger |
|----------|-------------|-----------------|----------|---------------------------|
| Skill helped, task succeeded | ✅ | applied=True | False | ✗ Correct — no trigger |
| Skill helped, task failed (hard task) | ❌ | applied=True | False | ✗ Correct — no trigger |
| Skill NOT helpful, task succeeded anyway | ✅ | applied=False | True | ✓ Correct — quality signal |
| Skill NOT helpful, task failed | ❌ | applied=False | True | ✓ Correct — quality signal |
| No feedback (5-15% of time) | any | None | False | ✗ Neutral — no false trigger |

**Key insight**: The worker's `applied` judgment is INDEPENDENT of task success. A skill can be helpful (applied=True) even on a failed task. This decouples fallback from task difficulty.

**Impact on existing triggers:**

| Trigger | Current Threshold | Impact of Option C |
|---------|------------------|--------------------|
| `high_fallback_rate` | > 0.5 | Now meaningful — fires when `total_fallbacks / total_selections > 0.5`. Counter incremented in `record_feedback()` when `applied=False` (Issue 6 fix). Only fires on genuine worker-reported unhelpfulness, not hard tasks. |
| `consecutive_failures` | >= 3 | Unchanged — still based on `consecutive_failures` counter, not fallback field |

**No threshold change needed**: The `high_fallback_rate` threshold of 0.5 remains appropriate. Under Option C, a 50% fallback rate means the worker reported the skill as unhelpful on half the tasks — a genuine quality signal warranting evolution.

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `daemon/services/skill_trigger_seed.py` | MODIFY | Change consecutive_failures action to "analyze" |
| `daemon/services/skill_evolution_service.py` | MODIFY | Enhance `_build_analysis_prompt()` with new metrics |
| `daemon/services/skill_metrics_service.py` | MODIFY | Fix fallback heuristic, ensure stats include new fields |
| `daemon/manager.py` (or seed function) | MODIFY | Migration: update existing trigger rows |

## Constraints
- Trigger seed is idempotent — safe to re-run
- Prompt enhancement is additive — the LLM gets MORE data, same JSON schema output
- Fallback heuristic change affects `skill_usage_records.fallback` column values
- All changes soft-fail — analysis failures never block job processing
- **Issue 3**: Task 4.3 depends on Phase 1 Task 1.0 (schema columns + indexes) AND Phase 3 Task 3.3 (`get_stats_filtered()`). Do NOT implement Task 4.3 until both are complete.
- **Issue 3**: The composite index `ix_skill_usage_records_skill_created` (Phase 1 Task 1.0) is REQUIRED for efficient aggregation queries. Without it, `get_stats_filtered()` degrades to full-table scans.

## Deliverables
- [ ] `consecutive_failures` trigger action changed to `analyze`
- [ ] Migration: existing trigger rows updated to new action
- [ ] Tier 2 prompt includes `applied_rate`, `avg_iterations`, `avg_duration`
- [ ] Recent records in prompt include per-record iterations + duration
- [ ] Fallback heuristic counts first-use failures
- [ ] Unit test: consecutive_failures routes to analyze (not evolve_fix)
- [ ] Unit test: analysis prompt contains applied_rate
- [ ] **Issue 4**: Fallback determined by worker `skill_feedback(applied=False)`, not task success
- [ ] **Issue 4**: `record_feedback()` sets fallback based on worker judgment
- [ ] **Issue 6**: `record_feedback()` increments/decrements `total_fallbacks` counter on state change
- [ ] **Issue 6**: Guard against double-counting via `_prev_fallback` check
- [ ] **Issue 4**: `update_feedback()` accepts `fallback` parameter
- [ ] Unit test: skill_feedback(applied=False) → fallback=True
- [ ] Unit test: skill_feedback(applied=True) → fallback=False
- [ ] Unit test: no feedback → fallback stays False (neutral default)
- [ ] Unit test: hard task (failed, applied=True) → fallback=False (skill helped)
