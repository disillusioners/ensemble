# Phase 5: Evolution Engine

## Objective
Build the skill-keeper agent, implement Tier 2 (cheap LLM analysis) and Tier 3 (main LLM evolution) of the evolution pipeline, implement the CAPTURED flow for automatic skill creation, manage skill lineage, and implement A/B testing with automatic winner selection.

## Coupling
- **Depends on**: Phase 1 (repos, models), Phase 4 (triggers, metrics)
- **Coupling type**: tight — consumes trigger results and metrics from Phase 4 directly
- **Shared files with other phases**: `agents/skill-keeper/` (new agent), `daemon/services/skill_evolution_service.py` (new)
- **Shared APIs/interfaces**: `SkillEvolutionService` consumed by Phase 6 (API endpoints)
- **Why this coupling**: Evolution is triggered by Phase 4's trigger engine, and results are surfaced via Phase 6's API

## Context
- Phase 4 completed: trigger engine flags skills, metrics are accumulated
- Tier 0/1 are FREE — this phase adds the LLM-powered tiers
- Job types: `skill_analysis` (Tier 2), `skill_evolution` (Tier 3), `skill_capture` (CAPTURED)
- No central job_type registry — these are string literals on JobItem
- `dynamic-skill` innate skill doc was created in Phase 2 (needed by skill-keeper agent here)
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Tasks

### Task 1: Skill-Keeper Agent

**Create** `agents/skill-keeper/meta.json`:
```json
{
  "id": "skill-keeper",
  "name": "Skill Keeper",
  "description": "Dedicated agent for skill evolution — analyzes flagged skills, performs FIX/DERIVED/CAPTURED evolution, and manages A/B testing. Not a participant in normal agent workflows.",
  "icon": "🔧",
  "color": "accent-orange",
  "version": "0.1.0",
  "innate_skills": ["dynamic-skill", "todo"],
  "system": true,
  "tools": {
    "allow": ["bash", "filesystem", "self", "help", "knowledge", "dynamic-skill", "skill-evolution"]
  }
}
```

> **Note:** `"skill-evolution"` in `tools.allow` grants access to the privileged evolution tools (`skill_analyze`, `skill_evolve`, `skill_resolve_ab`, `skill_get_metrics`, `skill_execute_capture`). These wrap `SkillEvolutionService` methods and are ONLY available to the skill-keeper. Regular agents use `skill_fix` (user-facing) which enqueues a job — the skill-keeper picks it up and uses the internal `skill_analyze` + `skill_evolve` tools.

**Create** `agents/skill-keeper/soul.md`:
```markdown
# Who I Am

**Status:** 🔧 Skill Keeper — Evolution Specialist

I am the skill-keeper agent. My purpose is to analyze, evolve, and maintain
the dynamic skill library. I am NOT a participant in normal agent workflows —
I am spawned on-demand via the job queue when the trigger engine flags a skill
for analysis or evolution.

## My Responsibilities

1. **Tier 2 Analysis**: Analyze flagged skills using a cheap LLM model.
   Determine: should_evolve, evolution_type (FIX/DERIVED/CAPTURED), direction.
2. **Tier 3 Evolution**: Perform the actual evolution using the evolution model.
   - FIX: Repair an existing skill in-place (new version, same lineage)
   - DERIVED: Create a new skill from an existing one (new lineage branch)
   - CAPTURED: Create a brand new skill from observed successful task patterns
3. **Lineage Management**: Track parent-child relationships, content diffs, change summaries.
4. **A/B Testing**: When FIX creates a new version, set up A/B testing — both
   old+new versions are served deterministically (hash of instance_id + message_id).
   After N comparisons (default 10) with sufficient difference (default 15%),
   deactivate the loser. If difference is too small, extend the test.
5. **Embedding Updates**: Re-generate trigger query embeddings for evolved skills.

## Evolution Types

| Type | Trigger | Action | Lineage |
|------|---------|--------|---------|
| FIX | Low completion rate, high fallback, consecutive failures | Repair content in-place | New generation, same name, parent = old version |
| DERIVED | Skill is useful but could be specialized | Create variant skill | New name, parent = original |
| CAPTURED | Successful task with no skill applied, high complexity | Extract pattern as new skill | New name, no parent (origin=captured) |

## Workflow

### Analysis (Tier 2)
1. Receive flagged skill + stats from trigger engine
2. Read skill content + recent usage records
3. Prompt cheap LLM: "This skill has X% completion rate. Analyze why and suggest improvements."
4. Output: {should_evolve: bool, type: FIX|DERIVED|CAPTURED|NONE, direction: str}

### Evolution (Tier 3)
1. Receive analysis result from Tier 2
2. Read skill content + usage patterns + agent feedback notes
3. Prompt evolution LLM: "Improve this skill. Current content: {...}. Issues: {...}. Feedback: {...}."
4. Generate new skill content (markdown)
5. Create new Skill record (new generation for FIX, new name for DERIVED/CAPTURED)
6. Create SkillLineage record (parent-child link, content diff, change summary)
7. Re-generate embeddings for the new skill
8. If FIX: set up A/B testing (both versions active, same ab_test_group)

### Capture (CAPTURED)
1. Receive successful task details (message, iterations, duration)
2. Check: no skill was APPLIED (via feedback records, NOT injection records), iterations > threshold, duration > threshold
3. Prompt LLM: "Extract a reusable skill from this successful task execution."
4. Generate skill content from task pattern
5. Create new Skill with lineage_origin='captured'
6. Generate embeddings

## Rules

- I use the **evolution model** (separately configured) with fallback to main model
- I always create a lineage record when evolving — never destroy history
- I never deactivate a skill without A/B testing first (unless it has 0 successful uses)
- I report what I changed, why, and what the expected improvement is
```

### Task 2: Evolution Service

**Create** `daemon/services/skill_evolution_service.py`:

```python
class SkillEvolutionService:
    """Manages Tier 2/3 evolution, CAPTURED flow, lineage, and A/B testing."""
    
    def __init__(self, skill_repo, lineage_repo, usage_repo, embedding_service,
                 metrics_service, ab_test_repo: SkillABTestRepository,
                 config: SkillEvolutionConfig, llm_config: dict):
        ...
    
    # ── Tier 2: Analysis ──
    
    async def analyze_skill(self, skill_id: str, reason: str, stats: dict) -> dict:
        """Tier 2: Cheap LLM analysis of a flagged skill.
        
        Uses analysis_model (cheap, configurable) with fallback to main model.
        
        Returns:
        {
            "should_evolve": bool,
            "evolution_type": "FIX" | "DERIVED" | "CAPTURED" | "NONE",
            "direction": str,  # e.g., "Add error handling section", "Simplify step 3"
            "analysis_summary": str,
        }
        """
        skill = self._skill_repo.get(skill_id)
        recent_usage = self._usage_repo.get_by_skill(skill_id, limit=10)
        
        prompt = self._build_analysis_prompt(skill, stats, recent_usage, reason)
        response = await self._call_llm(prompt, model=self._config.analysis_model)
        return self._parse_analysis_response(response)
    
    # ── Tier 3: Evolution ──
    
    async def evolve_skill(self, skill_id: str, evolution_type: str, direction: str) -> Skill:
        """Tier 3: Perform actual evolution using evolution model.
        
        For FIX: Create new generation of same skill, set up A/B testing.
        For DERIVED: Create new skill variant.
        For CAPTURED: Create brand new skill (called from capture flow).
        """
        skill = self._skill_repo.get(skill_id)
        
        if evolution_type == "FIX":
            return await self._evolve_fix(skill, direction)
        elif evolution_type == "DERIVED":
            return await self._evolve_derived(skill, direction)
        elif evolution_type == "CAPTURED":
            return await self._evolve_captured(skill, direction)
    
    async def _evolve_fix(self, skill: Skill, direction: str) -> Skill:
        """FIX: Create new version (generation + 1) of existing skill.
        
        1. Prompt evolution LLM to improve skill content
        2. Create new Skill record: same name, generation + 1
        3. Create SkillLineage: parent = old skill, content_diff, change_summary
        4. Deactivate old skill? NO — set up A/B testing instead
        5. Set ab_test_group on BOTH old and new skill (same UUID)
        6. Generate embeddings for new skill
        7. Return new skill
        """
        new_content = await self._generate_evolved_content(skill, direction)
        new_generation = skill.generation + 1
        ab_group = str(uuid.uuid4())
        
        # Create new version
        new_skill = self._skill_repo.create(
            name=skill.name,
            description=skill.description,
            content=new_content,
            project_id=skill.project_id,
            category=skill.category,
            lineage_origin="fix",
            generation=new_generation,
            ab_test_group=ab_group,
        )
        
        # Set A/B group on old skill too
        self._skill_repo.update(skill.id, ab_test_group=ab_group)
        
        # Create A/B test record for persistent tracking (BI4 fix)
        self._ab_test_repo.create_ab_test(
            ab_test_group=ab_group,
            skill_id_old=skill.id,
            skill_id_new=new_skill.id,
        )
        
        # Create lineage record
        diff = self._compute_diff(skill.content, new_content)
        self._lineage_repo.create(
            skill_id=new_skill.id,
            parent_skill_id=skill.id,
            change_summary=f"FIX: {direction}",
            content_diff=diff,
        )
        
        # Generate embeddings for new skill (graceful degradation — Note 3)
        try:
            await self._embedding_service.update_skill_embeddings(new_skill)
        except Exception as e:
            logger.warning(f"Embedding generation failed for evolved skill {new_skill.id[:8]}...: {e}")
            # Skill is still usable — BM25-only search will work in degraded mode
        
        return new_skill
    
    async def _evolve_derived(self, skill: Skill, direction: str) -> Skill:
        """DERIVED: Create a new specialized variant.
        
        1. Prompt LLM to create a specialized variant
        2. Create new Skill with new name (e.g., "{original_name}-specialized")
        3. Create SkillLineage: parent = original
        4. Generate embeddings
        """
    
    async def _evolve_captured(self, task_details: dict) -> Skill:
        """CAPTURED: Create brand new skill from successful task.
        
        1. Prompt LLM: "Extract a reusable skill from this task execution"
        2. Create new Skill with lineage_origin='captured'
        3. No parent — standalone skill
        4. Generate embeddings
        """
    
    # ── A/B Testing ──
    
    async def check_ab_test_resolution(self, ab_test_group: str) -> dict | None:
        """Check if A/B test has enough comparisons to resolve.
        
        Uses SkillABTestRepository for persistent state (extension_count, comparisons).
        
        Resolution requires BOTH conditions:
        1. comparisons >= ab_sample_size (default 10)
        2. difference >= ab_min_difference (default 0.15 — loser must be at least 15% worse)
        
        If comparisons >= ab_sample_size but difference < ab_min_difference,
        extend the test by another N (increment extension_count in DB).
        
        Force-resolve cap: after max_extensions (default 3) extensions (30 total
        comparisons with default ab_sample_size=10), force-resolve by picking
        the one with better raw completion_rate even if difference < threshold.
        
        1. Get comparison stats from metrics service (reads from skill_ab_tests table)
        2. If ready_to_resolve (both conditions met):
           a. Compare completion rates
           b. Deactivate the loser
           c. Mark A/B test resolved in skill_ab_tests table
           d. Clear ab_test_group on winner
        3. If needs_more_data (enough comparisons but difference too small):
           a. Read extension_count from skill_ab_tests table
           b. If extension_count < max_extensions: increment_extension() in DB, return None
           c. If >= max_extensions: force-resolve by raw completion_rate
        4. Return resolution result or None if not ready
        """
        stats = await self._metrics_service.get_ab_comparison_stats(ab_test_group)
        
        # Read persistent extension_count from skill_ab_tests table
        ab_test = self._ab_test_repo.get_by_group(ab_test_group)
        extension_count = ab_test.extension_count if ab_test else 0
        
        if not stats["ready_to_resolve"]:
            if stats.get("needs_more_data"):
                # Enough comparisons but difference too small
                if extension_count >= self._config.max_extensions:
                    # Force-resolve: pick better raw completion_rate despite small difference
                    logger.info(f"A/B test {ab_test_group}: max_extensions ({self._config.max_extensions}) "
                               f"reached, force-resolving by raw completion_rate "
                               f"(difference {stats['difference']:.2f} < threshold {self._config.ab_min_difference})")
                    # Fall through to resolution below
                else:
                    # Extend: persist the extension count
                    self._ab_test_repo.increment_extension(ab_test_group)
                    logger.info(f"A/B test {ab_test_group}: difference {stats['difference']:.2f} < "
                               f"threshold {self._config.ab_min_difference}, extending test "
                               f"(extension {extension_count + 1}/{self._config.max_extensions})")
                    return None
            else:
                return None
        
        # Determine winner
        if stats["completion_rate_a"] >= stats["completion_rate_b"]:
            winner_id = stats["skill_id_a"]
            loser_id = stats["skill_id_b"]
        else:
            winner_id = stats["skill_id_b"]
            loser_id = stats["skill_id_a"]
        
        # Deactivate loser
        self._skill_repo.deactivate(loser_id)
        # Mark A/B test resolved in skill_ab_tests table (BI4: persistent state)
        self._ab_test_repo.resolve(ab_test_group, winner_id)
        # Clear A/B group on winner
        self._skill_repo.update(winner_id, ab_test_group=None)
        
        return {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "winner_rate": max(stats["completion_rate_a"], stats["completion_rate_b"]),
            "loser_rate": min(stats["completion_rate_a"], stats["completion_rate_b"]),
        }
    
    # ── Capture Flow ──
    
    async def check_and_capture(self, instance_id: str, agent_id: str, 
                                 project_id: str | None, task_message: str,
                                 task_succeeded: bool, iterations: int, 
                                 duration_seconds: int) -> Skill | None:
        """Check if a successful task should be captured as a new skill.
        
        Conditions:
        1. task_succeeded == True
        2. No skill was applied — check feedback_applied records for the instance.
           If no feedback exists (NULL feedback_applied), treat as "not applied".
           NOTE: injection ≠ application. We check feedback records, NOT last_injected_skill_ids.
        3. Agent has skill_injection=true
        4. iterations > capture_min_iterations (default 5) OR duration > capture_min_duration (default 60s)
        
        If all conditions met, enqueue skill_capture job.
        """
        if not task_succeeded:
            return None
        if iterations <= self._config.capture_min_iterations and \
           duration_seconds <= self._config.capture_min_duration_seconds:
            return None
        
        # Check no skill was APPLIED (not just injected)
        # Query SkillUsageRecord for this instance where feedback_applied = True
        # If any record has feedback_applied=True, a skill was applied → skip capture
        applied_records = self._usage_repo.get_applied_for_instance(instance_id)
        if applied_records:
            return None  # A skill was applied, no capture needed
        
        # All conditions met — enqueue capture job for the skill-keeper agent
        task_details = {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "project_id": project_id,
            "task_message": task_message,
            "iterations": iterations,
            "duration_seconds": duration_seconds,
        }
        await self._job_dispatcher.enqueue_capture(project_id, task_details)
        logger.info(f"Skill capture enqueued for instance {instance_id[:8]}... "
                    f"(iterations={iterations}, duration={duration_seconds}s)")
    
    # ── LLM Helpers ──
    
    async def _call_llm(self, prompt: str, model: str | None = None) -> str:
        """Call LLM with specified model, fallback to main model.
        
        Uses ThinkingChatOpenAI with evolution_model or analysis_model.
        """
    
    def _build_analysis_prompt(self, skill, stats, usage_records, reason) -> str:
        """Build prompt for Tier 2 analysis."""
    
    def _generate_evolved_content(self, skill, direction) -> str:
        """Prompt LLM to generate evolved skill content."""
    
    def _compute_diff(self, old_content: str, new_content: str) -> str:
        """Compute unified diff between old and new content."""
        import difflib
        diff = difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="old", tofile="new"
        )
        return "".join(diff)
```

### Task 3: Job Dispatch for Evolution

**Create** `daemon/services/skill_job_dispatcher.py`:

```python
class SkillJobDispatcher:
    """Dispatches skill-related jobs via the job queue.
    
    CRITICAL: All skill jobs MUST route to system_parallel_queue (concurrency=5),
    NOT the default system_fifo_queue (concurrency=1). JobQueueService.enqueue()
    defaults queue_id=None → resolves to system_fifo_queue. We MUST explicitly
    resolve and pass system_parallel_queue.
    
    Precedent: instance_messaging.py:1332-1353 resolves system_parallel_queue
    via queue_repo.get_by_name(project_id, "system_parallel_queue") before
    calling enqueue.
    """
    
    def __init__(self, job_service, queue_repo, ...):
        self._job_service = job_service
        self._queue_repo = queue_repo  # JobQueueRepository for queue resolution
        ...
    
    async def _resolve_parallel_queue_id(self, project_id: str | None) -> str | None:
        """Resolve system_parallel_queue ID for the project.
        
        Follows the precedent at instance_messaging.py:1332-1353.
        Returns queue_id or None if not found (will fall back to default).
        """
        if project_id is None:
            return None
        queue = await asyncio.to_thread(
            self._queue_repo.get_by_name, project_id, "system_parallel_queue"
        )
        return queue.queue_id if queue else None
    
    async def enqueue_analysis(self, project_id: str | None, skill_id: str, 
                                reason: str, stats: dict) -> str:
        """Enqueue skill_analysis job (Tier 2).
        
        Creates a JobItem with job_type='skill_analysis' on system_parallel_queue.
        The skill-keeper agent processes this.
        """
        message = f"Analyze skill {skill_id}. Reason: {reason}. Stats: {stats}"
        return await self._enqueue_skill_keeper_job(project_id, "skill_analysis", message, 
                                                      metadata={"skill_id": skill_id, "reason": reason})
    
    async def enqueue_evolution(self, project_id: str | None, skill_id: str,
                                 evolution_type: str, direction: str) -> str:
        """Enqueue skill_evolution job (Tier 3)."""
        message = f"Evolve skill {skill_id}. Type: {evolution_type}. Direction: {direction}"
        return await self._enqueue_skill_keeper_job(project_id, "skill_evolution", message,
                                                      metadata={"skill_id": skill_id, "evolution_type": evolution_type})
    
    async def enqueue_capture(self, project_id: str | None, task_details: dict) -> str:
        """Enqueue skill_capture job."""
        message = f"Capture skill from task. Details: {task_details}"
        return await self._enqueue_skill_keeper_job(project_id, "skill_capture", message,
                                                      metadata={"task_details": task_details})
    
    async def enqueue_metric_scan(self, project_id: str | None = None) -> str:
        """Enqueue skill_metric_scan job (periodic trigger scan)."""
        message = f"Run skill metric scan for project {project_id or 'all'}"
        return await self._enqueue_skill_keeper_job(project_id, "skill_metric_scan", message)
    
    async def _enqueue_skill_keeper_job(self, project_id, job_type, message, metadata=None) -> str:
        """Create JobItem for skill-keeper agent on system_parallel_queue.
        
        CRITICAL: Must explicitly resolve and pass queue_id for system_parallel_queue.
        Without this, enqueue() defaults to system_fifo_queue (concurrency=1),
        which would serialize all skill evolution jobs.
        """
        queue_id = await self._resolve_parallel_queue_id(project_id)
        
        job = await self._job_service.enqueue(
            agent_id="skill-keeper",
            message=message,
            source="skill_evolution",
            project_id=project_id,
            queue_id=queue_id,  # ← MUST pass system_parallel_queue ID, NOT None
            job_type=job_type,
            metadata=metadata or {},
        )
        return job.job_id
```

### Task 4: Capture Flow Integration

**Modify** `daemon/services/skill_metrics_service.py` (from Phase 4):

In `record_task_completion()`, after recording metrics, check for capture:

```python
async def record_task_completion(self, ...):
    # ... existing metrics recording ...
    
    # ── Capture Check (CAPTURED flow) ──
    # Only for skill_injection=true agents
    if task_succeeded:
        agent_meta = registry.get_resolved(agent_id)
        if agent_meta and agent_meta.skill_injection:
            # Check no skill was APPLIED (via feedback records, NOT injection records)
            # Query SkillUsageRecord for this instance where feedback_applied = True
            no_skill_applied = not self._usage_repo.has_applied_for_instance(instance_id)
            
            if no_skill_applied:
                # Check complexity threshold
                if iterations > self._config.capture_min_iterations or \
                   duration_seconds > self._config.capture_min_duration_seconds:
                    # Enqueue capture job
                    await self._evolution_service.check_and_capture(
                        instance_id, agent_id, project_id,
                        task_message, task_succeeded, iterations, duration_seconds
                    )
```

### Task 5: A/B Testing in Injection

**Modify** `daemon/services/skill_injection_service.py` (from Phase 3):

When injecting skills, if a skill has an `ab_test_group`, **deterministically** select which variant to serve using hash of `(instance_id + message_id)`:

```python
async def inject_skills(self, user_message: str, project_id: str | None,
                        instance_id: str = None, message_id: str = None) -> str | None:
    results = await self._search_service.search(user_message, project_id)
    
    # A/B testing: for skills with ab_test_group, deterministically pick a variant
    # Use hash of (instance_id + message_id) — NOT random.choice()
    # This ensures the same message always gets the same variant, even on checkpoint retry
    for item in results["injected"]:
        skill = item["skill"]
        if skill.ab_test_group:
            variants = self._skill_repo.get_ab_variants(skill.ab_test_group)
            if len(variants) > 1:
                import hashlib
                hash_input = f"{instance_id}:{message_id}:{skill.ab_test_group}"
                hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                selected = variants[hash_val % len(variants)]
                item["skill"] = selected
                # Record which variant was selected for A/B comparison
                await self._record_ab_selection(selected.id, skill.ab_test_group)
    
    return self._format_injection(results)
```

### Task 6: Skill-Evolution Tools (privileged, skill-keeper only)

**Create** `daemon/tools/skill_evolution_tools.py`:

These are privileged tools that wrap `SkillEvolutionService` methods. They are ONLY available to the skill-keeper agent (via `"skill-evolution"` in `tools.allow`). Regular agents do NOT get these — they use `skill_fix` (user-facing) which enqueues a job, and the skill-keeper picks it up and uses these internal tools.

```python
CATEGORY_NAME = "Skill Evolution"
CATEGORY_DOC = """\
Privileged skill evolution tools for the skill-keeper agent.
These wrap SkillEvolutionService methods for Tier 2 analysis, Tier 3 evolution,
A/B test resolution, metrics retrieval, and CAPTURED flow execution.
NOT available to regular agents — only skill-keeper.
"""

def create_skill_evolution_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create skill evolution tools with injected manager reference."""

    def _get_evolution_service():
        return manager._skill_evolution_service

    @register_tool_category("skill-evolution")
    @tool
    async def skill_analyze(skill_id: str) -> str:
        """Analyze a flagged skill for evolution (Tier 2 — cheap LLM).
        
        Args:
            skill_id: The skill to analyze.
        """
        # Calls SkillEvolutionService.analyze_skill()
        # Returns: {should_evolve, evolution_type, direction, analysis_summary}

    @register_tool_category("skill-evolution")
    @tool
    async def skill_evolve(skill_id: str, evolution_type: str, direction: str) -> str:
        """Perform skill evolution (Tier 3 — main LLM).
        
        Args:
            skill_id: The skill to evolve.
            evolution_type: One of FIX, DERIVED, CAPTURED.
            direction: Description of what to change.
        """
        # Calls SkillEvolutionService.evolve_skill()
        # Creates new version, lineage record, A/B test record, embeddings

    @register_tool_category("skill-evolution")
    @tool
    async def skill_resolve_ab(ab_test_group: str) -> str:
        """Check and resolve an A/B test if thresholds are met.
        
        Args:
            ab_test_group: The A/B test group UUID.
        """
        # Calls SkillEvolutionService.check_ab_test_resolution()
        # Reads from skill_ab_tests table, resolves if ready

    @register_tool_category("skill-evolution")
    @tool
    async def skill_get_metrics(skill_id: str) -> str:
        """Get detailed metrics for a skill.
        
        Args:
            skill_id: The skill to get metrics for.
        """
        # Returns usage records, completion_rate, fallback_rate, A/B stats

    @register_tool_category("skill-evolution")
    @tool
    async def skill_execute_capture(instance_id: str, task_message: str, 
                                     iterations: int, duration_seconds: int) -> str:
        """Execute CAPTURED flow — extract a new skill from a successful task.
        
        Args:
            instance_id: The instance that completed the task.
            task_message: The original task message.
            iterations: Number of LLM iterations.
            duration_seconds: Task duration in seconds.
        """
        # Calls SkillEvolutionService.capture_skill()
        # Creates brand new skill from task pattern

    return [
        skill_analyze, skill_evolve, skill_resolve_ab,
        skill_get_metrics, skill_execute_capture,
    ]
```

**Tool Registration (5-step pattern):**

1. **Tool module**: `daemon/tools/skill_evolution_tools.py` (above)
2. **Add to `CATEGORY_MODULES`** in `daemon/tools/_tool_registry.py:184`:
   ```python
   "skill-evolution": "daemon.tools.skill_evolution_tools",
   ```
3. **Add to `INNATE_SKILL_TOOL_CATEGORIES`** in `daemon/tools/instance.py:52`:
   ```python
   "skill-evolution": ["skill-evolution"],
   ```
4. **Wire factory** in `create_instance_tools()` at `daemon/tools/instance.py:537`:
   ```python
   # ── Skill Evolution tools (privileged, skill-keeper only) ──
   skill_evolution_tool_list = create_skill_evolution_tools(manager, current_instance_id)
   tools.extend(skill_evolution_tool_list)
   ```
5. **Agent meta.json**: Add `"skill-evolution"` to skill-keeper's `tools.allow` (see Task 1 update below)

**Design note — breaking the recursive loop:**
- `skill_fix` (in `dynamic-skill` category, user-facing) → regular agents call this to REQUEST a fix → enqueues `skill_analysis` job
- Skill-keeper picks up the job → uses `skill_analyze` (in `skill-evolution` category, internal) to do the analysis
- If analysis says evolve → skill-keeper uses `skill_evolve` (internal) to perform the evolution
- This breaks the recursive loop: `skill_fix` (user-facing) → job queue → skill-keeper uses `skill_analyze` + `skill_evolve` (internal tools)

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `agents/skill-keeper/meta.json` | Create | Skill-keeper agent config (includes `skill-evolution` tools) |
| `agents/skill-keeper/soul.md` | Create | Skill-keeper persona + workflow |
| `daemon/services/skill_evolution_service.py` | Create | Tier 2/3 evolution + A/B + capture |
| `daemon/services/skill_job_dispatcher.py` | Create | Job queue dispatch for evolution (MUST resolve system_parallel_queue) |
| `daemon/services/skill_metrics_service.py` | Modify | Add capture check to record_task_completion |
| `daemon/services/skill_injection_service.py` | Modify | A/B variant selection in injection |
| `daemon/tools/skill_evolution_tools.py` | Create | 5 privileged tools for skill-keeper (skill_analyze, skill_evolve, skill_resolve_ab, skill_get_metrics, skill_execute_capture) |
| `daemon/tools/_tool_registry.py` | Modify | Add `"skill-evolution"` to `CATEGORY_MODULES` |
| `daemon/tools/instance.py` | Modify | Add `"skill-evolution"` to `INNATE_SKILL_TOOL_CATEGORIES` + wire factory at line 537 |
| `daemon/manager.py` | Modify | Initialize evolution service + dispatcher |

## Constraints
- Skill-keeper uses **evolution model** (separately configured) with fallback to main model
- Tier 2 uses **analysis model** (cheap, configurable) with fallback to main model
- A/B testing: both versions served **deterministically** (hash of instance_id + message_id), NOT randomly
- A/B resolution requires BOTH: `comparisons >= ab_sample_size` (default 10) AND `difference >= ab_min_difference` (default 0.15). If difference < threshold after N, extend by another N. After `max_extensions` (default 3) extensions (30 total comparisons), force-resolve by raw completion_rate.
- CAPTURED flow only triggers on `skill_injection=true` agents
- **Capture checks `feedback_applied` records** (NOT `last_injected_skill_ids`). Injection ≠ application. NULL `feedback_applied` = "not applied" for capture eligibility.
- Lineage records are immutable — never delete or modify
- Embeddings must be re-generated when skill content changes (stored as JSON arrays of floats in JSONBType — pure Python cosine similarity, no numpy)
- Job types are string literals — no central registry update needed
- Use `job_service.enqueue()` for job dispatch — NOT direct `job_repo.create()` (handles idempotency and validation). BUT `enqueue()` defaults `queue_id=None` → `system_fifo_queue` (concurrency=1). **MUST explicitly resolve `system_parallel_queue` via `queue_repo.get_by_name()` and pass `queue_id` parameter.** Precedent: `instance_messaging.py:1332-1353`.
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Evolution Flow

```
Trigger Engine (Phase 4)
    │
    ├─ action="analyze" ──→ enqueue skill_analysis job
    │                           ↓
    │                      Skill-Keeper Agent (Tier 2)
    │                      - Read skill + stats + usage
    │                      - Call cheap LLM for analysis
    │                      - Output: should_evolve, type, direction
    │                           │
    │                           ├─ should_evolve=false → log + done
    │                           └─ should_evolve=true → enqueue skill_evolution job
    │                                                      ↓
    │                                                 Skill-Keeper Agent (Tier 3)
    │                                                 - Call evolution LLM
    │                                                 - Generate new content
    │                                                 - Create new Skill (generation + 1)
    │                                                 - Create SkillLineage
    │                                                 - Generate embeddings
    │                                                 - Set up A/B testing (FIX only)
    │                                                      │
    │                                                      ↓
    │                                                 A/B Testing Active
    │                                                 - Both versions served deterministically (hash-based)
    │                                                 - Metrics tracked per variant
    │                                                      │
    │                                                      ↓ (after N comparisons + min difference)
    │                                                 A/B Resolution
    │                                                 - Compare completion rates
    │                                                 - If difference >= ab_min_difference: deactivate loser
    │                                                 - If difference < threshold: extend by another N
    │                                                 - Clear ab_test_group on winner
    │
    └─ action="evolve_fix" ──→ directly enqueue skill_evolution job
                                    (skip Tier 2, go straight to Tier 3)

Capture Flow (separate path):
    Task completes successfully
        ↓
    No skill APPLIED (check feedback_applied records, NOT injection records)
        + high complexity
        ↓
    Enqueue skill_capture job
        ↓
    Skill-Keeper Agent (CAPTURED)
    - Extract pattern from task
    - Create new Skill (origin=captured)
    - Generate embeddings
```

## Testing Strategy
1. **Evolution service tests**:
   - `analyze_skill()`: mock LLM, verify analysis parsing
   - `_evolve_fix()`: verify new generation, lineage record, A/B group setup
   - `_evolve_derived()`: verify new name, lineage record
   - `_evolve_captured()`: verify standalone skill creation
   - `check_ab_test_resolution()`: verify winner/loser selection, deactivation
2. **Job dispatcher tests**: Verify JobItems created with correct job_type, queue_id
3. **Capture flow tests**: 
   - Skill applied → no capture
   - Low complexity → no capture
   - High complexity, no skill → capture triggered
4. **A/B injection tests**: Verify random variant selection, selection recording
5. **Integration test**: Create skill → inject → fail → trigger → analyze → evolve → A/B → resolve

## Deliverables
- [ ] `agents/skill-keeper/meta.json` — agent config (includes `skill-evolution` in tools.allow)
- [ ] `agents/skill-keeper/soul.md` — agent persona
- [ ] `daemon/services/skill_evolution_service.py` — Tier 2/3 + A/B + capture (uses SkillABTestRepository)
- [ ] `daemon/services/skill_job_dispatcher.py` — job queue dispatch (resolves system_parallel_queue)
- [ ] `daemon/services/skill_metrics_service.py` — capture check added
- [ ] `daemon/services/skill_injection_service.py` — A/B variant selection
- [ ] `daemon/tools/skill_evolution_tools.py` — 5 privileged tools for skill-keeper
- [ ] `daemon/tools/_tool_registry.py` — `"skill-evolution"` in `CATEGORY_MODULES`
- [ ] `daemon/tools/instance.py` — `"skill-evolution"` in `INNATE_SKILL_TOOL_CATEGORIES` + wired at line 537
- [ ] `daemon/manager.py` — evolution service + dispatcher initialized
- [ ] Tests pass for evolution, A/B testing, capture flow, skill-evolution tools
