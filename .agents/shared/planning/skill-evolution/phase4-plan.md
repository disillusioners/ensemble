# Phase 4: Metrics & Triggers

## Objective
Build the Tier 0 passive metrics recorder (records every skill usage after task completion), the Tier 1 rule-based trigger engine (configurable rules that flag skills for LLM analysis), and the `skill_feedback` tool for agent-subjective metrics. Also implement denormalized counter increments on the `skills` table.

## Coupling
- **Depends on**: Phase 1 (repos + models), Phase 2 (skill store for feedback tool)
- **Coupling type**: loose — only uses Phase 1 repos for recording metrics and Phase 2's store for feedback
- **Shared files with other phases**: `daemon/services/job_queue_service.py` (modified for completion hook), `daemon/tools/skill_tools.py` (feedback tool already in Phase 2)
- **Shared APIs/interfaces**: `SkillMetricsRecorder`, `SkillTriggerEngine` consumed by Phase 5 (evolution)
- **Why this coupling**: Phase 5 consumes trigger results and accumulated metrics to decide what to evolve

## Context
- Phase 1 completed: `SkillUsageRepository`, `SkillTriggerRepository`, denormalized counters on `skills` table
- Phase 2 completed: `skill_feedback` tool stub exists (this phase implements the backend). If Phase 4 runs before Phase 2, the tool returns an error.
- Hook point identified: `JobQueueService._finalize_terminal()` at `daemon/services/job_queue_service.py:1274`
- Key decision: Tier 0 and Tier 1 are FREE (no LLM) — pure DB operations
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Tasks

### Task 1: Metrics Recorder (Tier 0 — FREE)

**Create** `daemon/services/skill_metrics_service.py`:

```python
class SkillMetricsService:
    """Tier 0: Passive metrics recording — no LLM, pure DB inserts.
    
    Records every skill usage after task completion:
    - selected (was the skill injected/searched?)
    - applied (did the agent use it? — from feedback, nullable)
    - task_succeeded (did the task complete successfully?)
    - iterations (how many LLM iterations?)
    - duration_seconds (how long did the task take?)
    - fallback (did the agent fall back to non-skill approach?)
    """
    
    def __init__(self, usage_repo: SkillUsageRepository, skill_repo: SkillRepository, 
                 trigger_repo: SkillTriggerRepository, ab_test_repo: SkillABTestRepository,
                 config: SkillEvolutionConfig):
        ...
    
    async def record_task_completion(self, instance_id: str, agent_id: str, 
                                      project_id: str | None, task_succeeded: bool,
                                      iterations: int, duration_seconds: int) -> None:
        """Called after every task completion.
        
        1. Check if any skills were injected for this instance/task
           (read from instance metadata: last_injected_skill_ids)
        2. For each injected skill:
           a. Create SkillUsageRecord with:
              - selected=True
              - applied=feedback_applied (if feedback was given via skill_feedback tool, else NULL)
              - task_succeeded=task_succeeded
              - iterations=iterations
              - duration_seconds=duration_seconds
              - fallback=consecutive_failures > 0 and not task_succeeded
           b. Increment denormalized counters on skills table:
              - total_selections += 1
              - total_completions += 1 if task_succeeded
              - total_fallbacks += 1 if fallback
              - consecutive_failures = 0 if task_succeeded, else += 1
              - last_used_at = NOW()
        3. Clear instance metadata: last_injected_skill_ids = None
        4. CAPTURED flow check (W6): Check feedback_applied records for the instance.
           If no feedback exists (NULL feedback_applied), treat as "not applied".
           If task_succeeded AND not applied AND complexity thresholds met,
           enqueue skill_capture job. NOTE: injection ≠ application —
           check applied status from feedback records, NOT from injection records.
        """
    
    async def record_feedback(self, skill_id: str, instance_id: str, agent_id: str,
                               project_id: str | None, applied: bool | None, note: str) -> None:
        """Called by skill_feedback tool.
        
        1. Find the most recent SkillUsageRecord for this skill + instance
        2. Update feedback_applied and feedback_note
        3. If applied=True: increment total_applied on skills table
        4. If applied=False: this is a negative signal for the trigger engine
        """
    
    async def get_skill_stats(self, skill_id: str) -> dict:
        """Compute aggregate stats for a skill.
        
        Returns:
        {
            "total_selections": int,
            "total_applied": int,
            "total_completions": int,
            "total_fallbacks": int,
            "completion_rate": float,  # completions / selections
            "fallback_rate": float,    # fallbacks / selections
            "applied_rate": float,     # applied / selections
            "consecutive_failures": int,
        }
        """
    
    async def get_ab_comparison_stats(self, ab_test_group: str) -> dict:
        """Get comparison stats for A/B testing.
        
        Reads persistent state from skill_ab_tests table (via SkillABTestRepository)
        and computes completion rates from skill_usage_records.
        
        Returns:
        {
            "skill_id_a": str,       # skill_id_old from skill_ab_tests
            "skill_id_b": str,       # skill_id_new from skill_ab_tests
            "comparisons": int,      # from skill_ab_tests.comparisons
            "completion_rate_a": float,
            "completion_rate_b": float,
            "ready_to_resolve": bool,  # comparisons >= ab_sample_size AND difference >= ab_min_difference
            "difference": float,       # abs(completion_rate_a - completion_rate_b)
            "needs_more_data": bool,   # comparisons >= ab_sample_size but difference < ab_min_difference
            "extension_count": int,    # from skill_ab_tests.extension_count (persisted, NOT hardcoded 0)
        }
        """
```

### Task 2: Denormalized Counter Increments

**Implement in** `SkillRepository` (from Phase 1):

```python
def increment_counter(self, skill_id: str, counter: str, amount: int = 1) -> None:
    """Atomic counter increment using raw SQL.
    
    Pattern A (SAFE): Raw SQL with engine.begin() + atomic UPDATE.
    """
    valid_counters = {
        "total_selections", "total_applied", "total_completions", 
        "total_fallbacks", "consecutive_failures"
    }
    if counter not in valid_counters:
        raise ValueError(f"Invalid counter: {counter}")
    
    with self.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE skills SET {counter} = {counter} + :amount WHERE id = :id"),
            {"amount": amount, "id": skill_id}
        )

def reset_counter(self, skill_id: str, counter: str, value: int = 0) -> None:
    """Reset a counter to a specific value."""
    with self.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE skills SET {counter} = :value WHERE id = :id"),
            {"value": value, "id": skill_id}
        )

def touch_last_used(self, skill_id: str) -> None:
    """Update last_used_at timestamp."""
    with self.engine.begin() as conn:
        conn.execute(
            text("UPDATE skills SET last_used_at = :now WHERE id = :id"),
            {"now": datetime.now(timezone.utc).isoformat(), "id": skill_id}
        )
```

### Task 3: Trigger Engine (Tier 1 — FREE)

**Create** `daemon/services/skill_trigger_engine.py`:

```python
class SkillTriggerEngine:
    """Tier 1: Rule-based trigger engine — no LLM, pure rule evaluation.
    
    Evaluates configurable rules against accumulated metrics.
    Only skills that trip a threshold get flagged for Tier 2 LLM analysis.
    """
    
    def __init__(self, trigger_repo: SkillTriggerRepository, metrics_service: SkillMetricsService):
        ...
    
    async def evaluate_all(self, project_id: str | None = None) -> list[dict]:
        """Evaluate all enabled triggers for a project.
        
        Returns list of flagged skills:
        [
            {
                "skill_id": str,
                "skill_name": str,
                "trigger_name": str,
                "trigger_action": str,  # "analyze", "evolve_fix", "capture"
                "reason": str,
                "stats": dict,
            }
        ]
        """
        triggers = self._trigger_repo.list(project_id=project_id, enabled_only=True)
        flagged = []
        for trigger in triggers:
            skills_to_check = self._get_skills_for_trigger(trigger, project_id)
            for skill in skills_to_check:
                if self._evaluate_condition(trigger, skill):
                    flagged.append({
                        "skill_id": skill.id,
                        "skill_name": skill.name,
                        "trigger_name": trigger.name,
                        "trigger_action": trigger.action,
                        "reason": self._build_reason(trigger, skill),
                        "stats": await self._metrics_service.get_skill_stats(skill.id),
                    })
        return flagged
    
    def _evaluate_condition(self, trigger: SkillTrigger, skill: Skill) -> bool:
        """Evaluate a trigger condition against a skill's stats.
        
        Condition types:
        - "low_completion_rate": completion_rate < threshold (default 0.3)
        - "high_fallback_rate": fallback_rate > threshold (default 0.5)
        - "consecutive_failures": consecutive_failures >= threshold (default 3)
        - "task_count_scan": total_selections >= threshold (default 20)
        - "periodic_scan": always true if skill hasn't been analyzed in N days
        """
        condition = trigger.condition_json
        stats = ... # fetch stats for skill
        
        if trigger.condition_type == "low_completion_rate":
            return stats["completion_rate"] < condition.get("threshold", 0.3)
        elif trigger.condition_type == "high_fallback_rate":
            return stats["fallback_rate"] > condition.get("threshold", 0.5)
        elif trigger.condition_type == "consecutive_failures":
            return skill.consecutive_failures >= condition.get("threshold", 3)
        elif trigger.condition_type == "task_count_scan":
            return skill.total_selections >= condition.get("threshold", 20)
        elif trigger.condition_type == "periodic_scan":
            # Check if skill was analyzed recently
            days_since = ... # compute from last analysis date
            return days_since >= condition.get("interval_days", 7)
        return False
```

### Task 4: Default Triggers (Seed Data)

**Create** `daemon/services/skill_trigger_seed.py`:

```python
DEFAULT_TRIGGERS = [
    {
        "name": "low_completion_rate",
        "condition_type": "low_completion_rate",
        "condition_json": {"threshold": 0.3, "min_selections": 5},
        "action": "analyze",
    },
    {
        "name": "high_fallback_rate",
        "condition_type": "high_fallback_rate",
        "condition_json": {"threshold": 0.5, "min_selections": 5},
        "action": "analyze",
    },
    {
        "name": "consecutive_failures",
        "condition_type": "consecutive_failures",
        "condition_json": {"threshold": 3},
        "action": "evolve_fix",
    },
    {
        "name": "periodic_scan",
        "condition_type": "periodic_scan",
        "condition_json": {"interval_days": 7},
        "action": "analyze",
    },
    {
        "name": "task_count_scan",
        "condition_type": "task_count_scan",
        "condition_json": {"threshold": 20},
        "action": "analyze",
    },
]

async def seed_default_triggers(trigger_repo: SkillTriggerRepository, project_id: str | None) -> None:
    """Seed default triggers if they don't exist. Called during startup."""
    for trigger_def in DEFAULT_TRIGGERS:
        existing = trigger_repo.list(project_id=project_id, enabled_only=False)
        if not any(t.name == trigger_def["name"] for t in existing):
            trigger_repo.create(
                name=trigger_def["name"],
                condition_type=trigger_def["condition_type"],
                condition_json=trigger_def["condition_json"],
                action=trigger_def["action"],
                project_id=project_id,
            )
```

### Task 5: Hook into Task Completion

**Modify** `daemon/services/job_queue_service.py` — `_finalize_terminal()`:

```python
# At line ~1469, after finalize_active_to_done() call:
# ADD metrics recording hook:

# ── Skill Metrics Recording (Tier 0 — FREE) ──
try:
    metrics_service = getattr(self._manager, '_skill_metrics_service', None)
    if metrics_service:
        # Get task details for metrics
        task_details = await self._get_task_details(canonical_job_id)
        if task_details:
            await metrics_service.record_task_completion(
                instance_id=task_details["instance_id"],
                agent_id=task_details["agent_id"],
                project_id=task_details.get("project_id"),
                task_succeeded=(derived_status == "completed"),
                iterations=task_details.get("iterations", 0),
                duration_seconds=task_details.get("duration_seconds", 0),
            )
except Exception as e:
    logger.warning(f"Skill metrics recording failed for {canonical_job_id}: {e}")
    # Fail gracefully — don't block job finalization
```

**`_get_task_details` implementation:**

```python
async def _get_task_details(self, job_id: str) -> dict | None:
    """Extract task details from JobItem for metrics recording.
    
    Reads from the JobItem and its associated Task/instance to compute:
    - instance_id, agent_id, project_id (from JobItem fields)
    - iterations (count of AI messages in conversation — approximate via Task row)
    - duration_seconds (computed from Task.started_at to now/completed_at)
    """
    job = self._repository.get(job_id)
    if not job:
        return None
    
    # instance_id and agent_id are on the JobItem
    instance_id = job.instance_id
    agent_id = job.agent_id
    project_id = job.project_id
    
    # Duration: approximate from job created_at to now
    # (More precise: read Task row for started_at/completed_at if available)
    from datetime import datetime, timezone
    created_at = job.created_at
    if isinstance(created_at, str):
        try:
            start = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            duration_seconds = int((datetime.now(timezone.utc) - start).total_seconds())
        except (ValueError, TypeError):
            duration_seconds = 0
    else:
        duration_seconds = 0
    
    # Iterations: count AIMessage entries in the instance's message history
    # This is an approximation — read from message_queue or task table
    iterations = 0
    if instance_id:
        try:
            # Count messages of type 'assistant' for this instance
            messages = self._manager._message_queue_repository.get_messages(instance_id)
            iterations = sum(1 for m in messages if m.get("role") == "assistant")
        except Exception:
            iterations = 0
    
    return {
        "instance_id": instance_id,
        "agent_id": agent_id,
        "project_id": project_id,
        "iterations": iterations,
        "duration_seconds": duration_seconds,
    }
```

**Note**: Getting `iterations` and `duration_seconds` from the task may require reading from the task/event tables. If not readily available, approximate:
- `iterations` = count of AI messages in the conversation for this task
- `duration_seconds` = `now() - task.started_at`

### Task 6: Periodic Trigger Scan Job

**Create** job type `skill_metric_scan`:

This is a periodic job that runs the trigger engine and enqueues analysis jobs for flagged skills.

```python
# In job processor or a new service:
async def run_skill_metric_scan(project_id: str | None = None):
    """Periodic trigger scan — evaluates all triggers and enqueues analysis jobs.
    
    1. Run trigger engine → get flagged skills
    2. For each flagged skill:
       - If action == "analyze": enqueue skill_analysis job
       - If action == "evolve_fix": enqueue skill_evolution job
    3. Log results
    """
    trigger_engine = manager._skill_trigger_engine
    flagged = await trigger_engine.evaluate_all(project_id)
    
    for item in flagged:
        if item["trigger_action"] == "analyze":
            # Enqueue skill_analysis job (Tier 2 — will be handled in Phase 5)
            await enqueue_skill_job(
                manager, project_id, "skill_analysis",
                skill_id=item["skill_id"],
                reason=item["reason"],
                stats=item["stats"],
            )
        elif item["trigger_action"] == "evolve_fix":
            await enqueue_skill_job(
                manager, project_id, "skill_evolution",
                skill_id=item["skill_id"],
                evolution_type="FIX",
                reason=item["reason"],
            )
```

Schedule this job:
- Daily at configurable hour (default 3 AM)
- OR triggered after every N task completions (configurable)

### Task 7: Implement `skill_feedback` Tool Backend

The `skill_feedback` tool was stubbed in Phase 2. Now implement the backend:

```python
# In skill_tools.py, skill_feedback tool body:
@register_tool_category("dynamic-skill")
@tool
async def skill_feedback(skill_id: str, applied: bool | None = None, note: str = "") -> str:
    """Provide feedback on whether a skill was helpful.
    
    Args:
        skill_id: The skill ID that was used.
        applied: Whether the skill was actually applied/used (true/false).
                 Omit if unsure.
        note: Optional feedback note.
    """
    try:
        metrics_service = manager._skill_metrics_service
        project_id = _get_project_id()
        
        await metrics_service.record_feedback(
            skill_id=skill_id,
            instance_id=current_instance_id,
            agent_id=...,  # from instance meta
            project_id=project_id,
            applied=applied,
            note=note,
        )
        return f"✅ Feedback recorded for skill {skill_id[:8]}..."
    except Exception as e:
        return f"ERROR: Failed to record feedback: {e}"
```

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `daemon/services/skill_metrics_service.py` | Create | Tier 0 recorder + stats |
| `daemon/services/skill_trigger_engine.py` | Create | Tier 1 rule engine |
| `daemon/services/skill_trigger_seed.py` | Create | Default trigger seed data |
| `daemon/services/job_queue_service.py` | Modify | Hook into `_finalize_terminal()` |
| `daemon/manager.py` | Modify | Initialize metrics + trigger services |
| `daemon/tools/skill_tools.py` | Modify | Implement `skill_feedback` backend |

## Constraints
- Tier 0 and Tier 1 are FREE — no LLM calls, pure DB operations
- Metrics recording must fail gracefully — never block job finalization
- Counter increments must be atomic (Pattern A: raw SQL with `engine.begin()`)
- Triggers are configurable via `skill_triggers` table — rules can be added/modified at runtime
- `skill_feedback` updates both the usage record AND denormalized counters
- Periodic scan job uses `system_parallel_queue` for execution
- **A/B resolution requires BOTH**: `comparisons >= ab_sample_size` (default 10) AND `difference >= ab_min_difference` (default 0.15). If difference < threshold after N comparisons, extend the test by another N. After `max_extensions` (default 3) extensions, force-resolve by raw completion_rate.
- **Capture flow (W6)**: Check `feedback_applied` records for the instance — NOT `last_injected_skill_ids`. Injection ≠ application. If no feedback exists (NULL `feedback_applied`), treat as "not applied" for capture eligibility.
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Testing Strategy
1. **Metrics service tests**:
   - `record_task_completion()`: mock instance metadata with injected skill IDs, verify usage records created
   - `record_feedback()`: verify feedback_applied and note stored, counters incremented
   - `get_skill_stats()`: verify computed rates (completion_rate, fallback_rate, etc.)
   - `get_ab_comparison_stats()`: verify A/B comparison counting
2. **Trigger engine tests**:
   - Each condition type: `low_completion_rate`, `high_fallback_rate`, `consecutive_failures`, `task_count_scan`, `periodic_scan`
   - Verify only enabled triggers are evaluated
   - Verify project-scoped filtering
3. **Integration test**: Create skill → inject (Phase 3) → complete task → verify metrics recorded → run triggers → verify flagged
4. **Graceful failure test**: Mock DB failure, verify job finalization still completes

## Deliverables
- [ ] `daemon/services/skill_metrics_service.py` — Tier 0 recorder + stats
- [ ] `daemon/services/skill_trigger_engine.py` — Tier 1 rule engine
- [ ] `daemon/services/skill_trigger_seed.py` — default triggers
- [ ] `daemon/services/job_queue_service.py` — completion hook added
- [ ] `daemon/manager.py` — services initialized, triggers seeded
- [ ] `daemon/tools/skill_tools.py` — `skill_feedback` backend implemented
- [ ] Tests pass for metrics, triggers, and feedback
