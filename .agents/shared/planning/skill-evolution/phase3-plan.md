# Phase 3: Injection System

## Objective
Build the message interceptor that hooks into `_process_message_with_tracking()` to automatically search for relevant skills before real user messages are processed, and inject matching skills as a `HumanMessage` (NOT assistant message — LangGraph system messages can only be in the system prompt, not mid-conversation) prepended before the user's message in `graph_input`.

## Coupling
- **Depends on**: Phase 2 (SkillSearchService, SkillStoreService)
- **Coupling type**: tight — calls search pipeline from Phase 2 directly
- **Shared files with other phases**: `daemon/services/instance_messaging.py` (modified)
- **Shared APIs/interfaces**: `SkillInjectionService` consumed by the message processing pipeline
- **Why this coupling**: The injection hook must call the Phase 2 search service and format results as a `HumanMessage`

## Context
- Phase 2 completed: search pipeline, skill store, embedding service all functional
- Hook point identified: `daemon/services/instance_messaging.py:1691-1716` (graph_input construction)
- Precedent pattern: project-context injection at lines 1591-1690 (prepends to `message` string)
- Key decision: Use Approach 2 (inject separate `HumanMessage`) for cleaner separation — skill content lives as its own message in history

## Tasks

### Task 1: Skill Injection Service

**Create** `daemon/services/skill_injection_service.py`:

```python
from langchain_core.messages import HumanMessage

class SkillInjectionService:
    """Service for injecting relevant skills into agent conversations.
    
    Called before processing real user messages. Searches for relevant skills
    and formats them as an injected HumanMessage (NOT assistant message —
    LangGraph system messages can only be in the system prompt, not mid-conversation).
    """
    
    def __init__(self, search_service: SkillSearchService, config: SkillEvolutionConfig):
        self._search_service = search_service
        self._config = config
    
    async def should_inject(self, instance_meta) -> bool:
        """Check if this instance should have skills injected.
        
        Conditions:
        1. Agent's meta.json has skill_injection: true
        2. Message is a real user message (not a child report, not system, not retry)
        """
        # Check instance_meta for agent_id, then look up AgentMetadata
        # Return True only if skill_injection == True
    
    async def inject_skills(self, user_message: str, project_id: str | None) -> str | None:
        """Search for skills and format injection message.
        
        Returns formatted injection string, or None if no skills found.
        """
        results = await self._search_service.search(
            user_message, 
            project_id=project_id,
            max_results=self._config.max_inject_skills
        )
        
        if not results["injected"] and not results["low_match"]:
            return None
        
        return self._format_injection(results)
    
    def _format_injection(self, results: dict) -> str:
        """Format skills as injected message text (wrapped in HumanMessage by caller).
        
        Format:
        [System Inject] Relevant skills loaded:
        
        📋 **Skill: {name}** (match score: {score})
        ────────────────────────────
        {full markdown content}
        
        📋 **Other available skills** (low match):
        • {name} ({score}) — {description}
        """
        parts = ["[System Inject] Relevant skills loaded:\n"]
        
        for item in results["injected"]:
            skill = item["skill"]
            score = item["score"]
            parts.append(f"📋 **Skill: {skill.name}** (match score: {score:.2f})")
            parts.append("─" * 30)
            parts.append(skill.content)
            parts.append("")
        
        if results["low_match"]:
            parts.append("📋 **Other available skills** (low match):")
            for item in results["low_match"]:
                parts.append(f"• {item['name']} ({item['score']:.2f}) — {item['description']}")
        
        return "\n".join(parts)
```

### Task 2: Hook into Message Processing Pipeline

**Modify** `daemon/services/instance_messaging.py` — `_process_message_with_tracking()`:

The injection must happen AFTER the existing project-context injection (lines 1591-1690) but BEFORE `graph_input` construction (lines 1691-1716).

```python
# ADD after line 1690 (after project-context injection block), before graph_input construction:

# ── Skill Injection (Phase 3: dynamic skill evolution) ──
# Inject relevant skills as a separate message before the user message.
# Gated by agent's skill_injection=true in meta.json.
# Only for real user messages (not child reports, not system messages, not retries).
if not is_retry and not is_completion_report:
    try:
        # Get agent metadata to check skill_injection flag
        instance_meta = await asyncio.to_thread(
            self._manager._instance_repository.get, instance_id
        )
        
        if instance_meta:
            from ..registry import get_registry
            registry = get_registry()
            agent_meta = registry.get_resolved(instance_meta.agent_id)
            
            if agent_meta and agent_meta.skill_injection:
                # Get project_id for project-scoped search
                project_id = (
                    instance_meta.instance_metadata.get("project_id")
                    if instance_meta.instance_metadata else None
                )
                
                injection_service = getattr(self._manager, '_skill_injection_service', None)
                if injection_service:
                    injection_text = await injection_service.inject_skills(
                        message, project_id
                    )
                    if injection_text:
                        # Create skill injection as a separate HumanMessage
                        # that precedes the real user message in graph_input.
                        # MUST include id= parameter — existing messages use id=message_id.
                        # A unique UUID prevents LangGraph message deduplication issues.
                        import uuid as _uuid
                        skill_message = HumanMessage(
                            content=injection_text,
                            id=str(_uuid.uuid4())
                        )
                        # Will be prepended to graph_input messages below
                        _skill_injection_msg = skill_message
                    else:
                        _skill_injection_msg = None
                else:
                    _skill_injection_msg = None
            else:
                _skill_injection_msg = None
        else:
            _skill_injection_msg = None
    except Exception as e:
        logger.warning(f"Skill injection failed for {instance_id[:8]}...: {e}")
        _skill_injection_msg = None  # Fail gracefully — don't block message processing
else:
    _skill_injection_msg = None
```

Then modify the `graph_input` construction to prepend the skill message:

```python
# MODIFY lines 1705, 1712, 1716 — prepend skill message if present:

# Line 1705 (retry with checkpoint, non-silent):
if content and not silent:
    messages_list = [HumanMessage(content=content, id=message_id)]
    if _skill_injection_msg:
        messages_list = [_skill_injection_msg] + messages_list
    graph_input = {"messages": messages_list}

# Line 1712 (retry without checkpoint):
content = _build_message_content(message, images)
messages_list = [HumanMessage(content=content, id=message_id)]
if _skill_injection_msg:
    messages_list = [_skill_injection_msg] + messages_list
graph_input = {"messages": messages_list}

# Line 1716 (first attempt — most common):
content = _build_message_content(message, images)
messages_list = [HumanMessage(content=content, id=message_id)]
if _skill_injection_msg:
    messages_list = [_skill_injection_msg] + messages_list
graph_input = {"messages": messages_list}
```

### Task 3: Wire Injection Service into InstanceManager

**Modify** `daemon/manager.py`:
```python
# In __init__ or startup method:
self._skill_injection_service = SkillInjectionService(
    search_service=self._skill_search_service,
    config=self._config.skill_evolution,  # Config from daemon/config.py:473, NOT EnsembleConfig
)
```

### Task 4: Track Injection for Metrics + A/B Variant Selection

When skills are injected, record that they were "selected" for metrics (Phase 4) and handle A/B variant selection:

```python
# In SkillInjectionService.inject_skills(), after search:

# A/B testing: for skills with ab_test_group, deterministically select variant
# Use hash of (instance_id + message_id) — NOT random.choice()
# This ensures the same message always gets the same variant, even on checkpoint retry
for item in results["injected"]:
    skill = item["skill"]
    if skill.ab_test_group:
        variants = self._skill_repo.get_ab_variants(skill.ab_test_group)
        if len(variants) > 1:
            # Deterministic selection via hash
            import hashlib
            hash_input = f"{instance_id}:{message_id}:{skill.ab_test_group}"
            hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
            selected = variants[hash_val % len(variants)]
            item["skill"] = selected
            # Record which variant was selected for A/B comparison
            # (Phase 5 will use this for resolution)

# Record usage records with selected=True for each injected skill
# This connects to Phase 4's metrics system
# For now, just store the skill IDs in instance metadata for later correlation

# In the injection hook, after successful injection:
await asyncio.to_thread(
    self._manager._instance_repository.set_metadata,
    instance_id, 
    "last_injected_skill_ids",
    [s["skill"].id for s in results["injected"]],
)
```

> **Note on capture flow (W6):** When Phase 4's `SkillMetricsService.record_task_completion()` checks whether to trigger the CAPTURED flow, it checks `feedback_applied` records for the instance — NOT `last_injected_skill_ids`. Injection ≠ application. If no feedback exists (NULL `feedback_applied`), treat as "not applied" — this is the correct default for capture eligibility.

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `daemon/services/skill_injection_service.py` | Create | Injection service: search + format |
| `daemon/services/instance_messaging.py` | Modify | Hook into `_process_message_with_tracking()` |
| `daemon/manager.py` | Modify | Initialize injection service |

## Constraints
- **Fail gracefully**: If injection fails, the user message must still be processed normally
- **Only real user messages**: Skip injection for child reports (`internal_report:*`), system messages, and retries
- **Gated by meta.json**: Only agents with `skill_injection: true` get injection (access `agent_meta.skill_injection` directly — field is properly defined on `AgentMetadata`, no `getattr` fallback needed)
- **Each real user message triggers new search**: Skills can change as conversation evolves
- **Max skills injected**: Configurable via `max_inject_skills` (default 2)
- **Precedent pattern**: Follow exact structure of project-context injection (lines 1591-1690)
- **LangGraph checkpoint**: The skill message persists in checkpoint state, so it's available on resume/retry
- **HumanMessage, NOT assistant message**: LangGraph system messages can only be in the system prompt, not mid-conversation. The injection is a `HumanMessage` prepended before the user's message.
- **Injected HumanMessage MUST have `id=` parameter**: Use `id=str(uuid.uuid4())` — existing messages use `id=message_id`. A unique UUID prevents LangGraph message deduplication issues on checkpoint resume.
- **`is_retry` gating verified**: The injection hook condition `if not is_retry and not is_completion_report` (line 110) correctly prevents duplicate skill messages on checkpoint retry. On retry, `is_retry=True` → entire injection block skipped → `_skill_injection_msg = None` → no skill message prepended to graph_input. The original skill message from the first attempt persists in the LangGraph checkpoint, so the agent still has access to it.
- **A/B variant selection must be deterministic**: Use hash of `(instance_id + message_id)` to select variant, NOT `random.choice()`. This ensures the same message always gets the same variant, even on checkpoint retry.
- **Config access**: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Injection Flow Diagram

```
User sends message
    ↓
_process_message_with_tracking()
    ↓
[1] Project-context injection (existing, lines 1591-1690)
    ↓
[2] Skill injection (NEW):
    a. Check: is_retry? is_completion_report? → skip
    b. Check: agent_meta.skill_injection == True? → skip if False
    c. Search: SkillSearchService.search(user_message, project_id)
    d. Format: injection_text = _format_injection(results)
    e. Create: _skill_injection_msg = HumanMessage(content=injection_text)
    ↓
[3] Build graph_input:
    graph_input = {"messages": [_skill_injection_msg, HumanMessage(user_message)]}
    ↓
[4] graph.astream(graph_input, config)  →  LangGraph processes both messages
```

## Testing Strategy
1. **Unit tests** (`tests/services/test_skill_injection_service.py`):
   - `should_inject()`: test with `skill_injection=true/false`, various message sources
   - `inject_skills()`: mock search service, verify formatted output
   - `_format_injection()`: test with 0, 1, 2 injected skills + low match list
2. **Integration tests** (`tests/services/test_injection_hook.py`):
   - Mock `_process_message_with_tracking` pipeline
   - Verify skill message is prepended to graph_input
   - Verify graceful failure when search service throws
   - Verify no injection for child reports / system messages / retries
3. **End-to-end test**:
   - Create a skill, send a matching user message, verify skill content appears in LLM input

## Deliverables
- [ ] `daemon/services/skill_injection_service.py` — injection service
- [ ] `daemon/services/instance_messaging.py` — hook added to `_process_message_with_tracking()`
- [ ] `daemon/manager.py` — injection service initialized
- [ ] Tests pass: injection triggers correctly, fails gracefully, formats properly
