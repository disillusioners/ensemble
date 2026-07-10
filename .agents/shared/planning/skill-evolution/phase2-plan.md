# Phase 2: Skill CRUD + Search

## Objective
Build the skill store service, the three-stage search pipeline (BM25 pre-filter → embedding re-rank → LLM final selection), an embedding service for multi-example embeddings, and register 6 agent tools (`skill_search`, `skill_list`, `skill_view`, `skill_create`, `skill_fix`, `skill_feedback`) plus the `dynamic-skill` innate skill doc.

## Coupling
- **Depends on**: Phase 1 (repos + models + config)
- **Coupling type**: tight — imports Phase 1 repository classes and models directly
- **Shared files with other phases**: `daemon/tools/instance.py`, `daemon/tools/_tool_registry.py`, `daemon/tools/skill_tools.py` (new)
- **Shared APIs/interfaces**: `SkillStoreService`, `SkillSearchService`, `EmbeddingService` consumed by Phase 3 (injection) and Phase 4 (feedback tool)
- **Why this coupling**: Phase 3 calls search pipeline; Phase 4 calls feedback tool — both depend on Phase 2's service layer

## Context
- Phase 1 completed: 5 repos, 5 models, config, `skill_injection` field all exist
- Key decision: Embedding service calls OpenAI-compatible `/embeddings` endpoint directly (NOT via LightRAG — skills need per-example embeddings stored in DB)
- Config access: `self._config.skill_evolution` on `InstanceManager` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Tasks

### Task 1: Embedding Service

**Create** `daemon/services/skill_embedding_service.py`:

```python
class SkillEmbeddingService:
    """Service for generating and managing skill embeddings.
    
    Calls the OpenAI-compatible /embeddings endpoint directly.
    Generates 3-10 example trigger queries per skill via LLM,
    then embeds each query and stores in skill_embeddings table.
    """
    
    def __init__(self, config: SkillEvolutionConfig, embedding_repo: SkillEmbeddingRepository, llm_config: dict):
        self._config = config
        self._embedding_repo = embedding_repo
        self._llm_config = llm_config
    
    async def generate_trigger_queries(self, skill: Skill) -> list[str]:
        """Use LLM to generate 3-10 example trigger queries for a skill.
        
        Prompt: 'Given this skill named {name} with description {description},
        generate 3-10 example user messages that would trigger this skill.'
        Returns list of query strings.
        """
    
    async def embed_text(self, text: str) -> list[float]:
        """Call OpenAI-compatible /embeddings endpoint, return embedding as list[float].
        
        Uses config.embedding_model, config.embedding_base_url (fallback to llm_config.base_url).
        Returns a plain Python list of floats (NOT bytes, NOT numpy — numpy is excluded in ensemble.spec).
        """
    
    async def update_skill_embeddings(self, skill: Skill) -> int:
        """Full pipeline: generate queries → embed each → store in DB.
        
        1. Delete existing embeddings for skill
        2. Generate 3-10 trigger queries via LLM
        3. Embed each query
        4. Store in skill_embeddings table (embedding column is JSONBType — JSON array of floats)
        Returns count of embeddings created.
        """
    
    async def embed_user_message(self, message: str) -> list[float]:
        """Embed a user message for search comparison. Returns list[float]."""
    
    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two embedding vectors (pure Python).
        
        Uses math.sqrt() and sum() — NO numpy (numpy is excluded in ensemble.spec).
        For the expected scale (hundreds of skills × 10 examples), pure Python is sufficient.
        """
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
```

**Key implementation notes:**
- Use `openai.OpenAI(api_key=..., base_url=...)` client for embedding calls
- Store embeddings as JSON arrays of floats in JSONBType column (NOT bytes, NOT pickle — numpy is excluded in `ensemble.spec`)
- Implement `cosine_similarity` in pure Python using `math.sqrt()` and `sum()` — no numpy dependency
- Embedding dimensions configurable via `SkillEvolutionConfig.embedding_dimensions`
- Fallback: if embedding endpoint unavailable, BM25-only search (degraded mode)
- Config access: `self._config` is `SkillEvolutionConfig` from `Config.skill_evolution` (NOT `EnsembleConfig`)

### Task 2: Skill Store Service

**Create** `daemon/services/skill_store_service.py`:

```python
class SkillStoreService:
    """Service layer for skill CRUD operations."""
    
    def __init__(self, skill_repo: SkillRepository, lineage_repo: SkillLineageRepository, embedding_service: SkillEmbeddingService):
        ...
    
    async def create_skill(self, name, description, content, project_id=None, category="workflow", lineage_origin="imported") -> Skill:
        """Create a new skill. Triggers embedding generation (graceful degradation)."""
        skill = self._skill_repo.create(name=name, description=description, content=content, ...)
        # Generate embeddings — gracefully degrade if embedding service fails
        try:
            await self._embedding_service.update_skill_embeddings(skill)
        except Exception as e:
            logger.warning(f"Embedding generation failed for skill {skill.id[:8]}...: {e}")
            # Skill is still usable — BM25-only search will work in degraded mode
        return skill
    
    async def get_skill(self, skill_id: str) -> Skill | None
    
    async def list_skills(self, project_id: str | None = None, active_only=True, limit=100, offset=0) -> tuple[list[dict], int]
        # Returns list of skill dicts (without full content for listing)
    
    async def update_skill(self, skill_id: str, **fields) -> Skill | None
        # If content changes, re-generate embeddings
    
    async def delete_skill(self, skill_id: str) -> bool
    
    async def deactivate_skill(self, skill_id: str) -> Skill | None
    
    async def view_skill(self, skill_id: str) -> dict
        # Returns full skill data including lineage history
```

### Task 3: Search Pipeline (3-stage)

**Create** `daemon/services/skill_search_service.py`:

```python
class SkillSearchService:
    """Three-stage skill search: BM25 → Embedding re-rank → LLM selection."""
    
    def __init__(self, skill_repo, embedding_repo, embedding_service, llm_config, config: SkillEvolutionConfig):
        ...
    
    async def search(self, user_message: str, project_id: str | None = None, max_results: int = 2) -> dict:
        """Full search pipeline.
        
        Returns:
            {
                "injected": [{"skill": Skill, "score": float}, ...],  # top 1-2
                "low_match": [{"name": str, "score": float, "description": str}, ...]  # low match list
            }
        """
    
    async def _bm25_prefilter(self, query: str, project_id, top_k: int = 10) -> list[Skill]:
        """Stage 1: BM25 keyword search over name + description + content.
        
        In-memory BM25 implementation:
        1. Fetch all active skills for project
        2. Tokenize query and skill texts
        3. Compute BM25 scores
        4. Return top_k candidates
        """
    
    async def _embedding_rerank(self, query: str, candidates: list[Skill], top_k: int = 5) -> list[tuple[Skill, float]]:
        """Stage 2: Embedding re-rank using multi-example embeddings.
        
        1. Embed user query
        2. For each candidate skill, get ALL example embeddings from skill_embeddings table
        3. Compute MAX similarity across all examples per skill
        4. Sort by max similarity, return top_k
        """
    
    async def _llm_select(self, query: str, candidates: list[tuple[Skill, float]], max_results: int = 2) -> dict:
        """Stage 3: LLM final selection.
        
        1. Build prompt with top 5 candidates (name + description only)
        2. Ask LLM: 'Which 1-2 skills are most relevant to this user message?'
        3. Parse LLM response for selected skill names + scores
        4. Return injected skills + low-match list
        """
```

**BM25 Implementation Details:**
```python
import math
from collections import Counter

def bm25_score(query_tokens: list[str], doc_tokens: list[str], doc_freqs: dict[str, int], 
               total_docs: int, avg_doc_len: float, k1: float = 1.5, b: float = 0.75) -> float:
    """Standard BM25 scoring function."""
    doc_len = len(doc_tokens)
    doc_counter = Counter(doc_tokens)
    score = 0.0
    for term in query_tokens:
        if term not in doc_counter:
            continue
        tf = doc_counter[term]
        df = doc_freqs.get(term, 0)
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
    return score
```

### Task 4: Agent Tools (6 tools)

**Create** `daemon/tools/skill_tools.py`:

```python
CATEGORY_NAME = "Dynamic Skills"
CATEGORY_DOC = """\
Dynamic skill tools for searching, listing, viewing, creating, fixing, 
and providing feedback on evolvable skills.
"""

def create_skill_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create dynamic skill tools with injected manager reference."""
    
    # Helper to get project_id from instance context
    def _get_project_id() -> str | None:
        instance_meta = manager._instance_repository.get(current_instance_id)
        return instance_meta.project_id if instance_meta else None
    
    # Helper to get skill services from manager
    def _get_skill_services():
        return manager._skill_store_service, manager._skill_search_service
    
    @register_tool_category("dynamic-skill")
    @tool
    async def skill_search(query: str) -> str:
        """Search for relevant skills by natural language query.
        
        Args:
            query: Natural language describing what you need help with.
        """
        # Calls SkillSearchService.search(query, project_id)
        # Returns formatted list of matching skills with scores
    
    @register_tool_category("dynamic-skill")
    @tool
    async def skill_list(category: str | None = None) -> str:
        """List all available skills, optionally filtered by category."""
        # Calls SkillStoreService.list_skills(project_id, ...)
    
    @register_tool_category("dynamic-skill")
    @tool
    async def skill_view(skill_id: str) -> str:
        """View full details of a specific skill including content and lineage."""
        # Calls SkillStoreService.view_skill(skill_id)
    
    @register_tool_category("dynamic-skill")
    @tool
    async def skill_create(name: str, description: str, content: str, category: str = "workflow") -> str:
        """Create a new evolvable skill.
        
        Args:
            name: Unique skill name within the project.
            description: Short description of what the skill does.
            content: Full markdown content of the skill.
            category: Skill category (default: workflow).
        """
        # Calls SkillStoreService.create_skill(...)
    
    @register_tool_category("dynamic-skill")
    @tool
    async def skill_fix(skill_id: str, issue_description: str, suggested_fix: str | None = None) -> str:
        """Report an issue with a skill and optionally suggest a fix.
        
        This is a USER-FACING tool for regular agents. It does NOT perform
        evolution directly — it enqueues a skill_analysis job to the skill-keeper
        agent. The skill-keeper then picks up the job and uses its internal
        `skill_analyze` + `skill_evolve` tools (in the `skill-evolution` category)
        to perform the actual analysis and evolution.
        
        Args:
            skill_id: The ID of the skill to fix.
            issue_description: Description of what's wrong with the skill.
            suggested_fix: Optional suggested fix content.
        """
        # Enqueues a skill_analysis job for the skill-keeper agent via SkillJobDispatcher
        # Returns confirmation with job_id
        dispatcher = getattr(manager, '_skill_job_dispatcher', None)
        if dispatcher:
            project_id = _get_project_id()
            job_id = await dispatcher.enqueue_analysis(
                project_id=project_id,
                skill_id=skill_id,
                reason=issue_description,
                stats={},
            )
            return f"✅ Fix request enqueued (job_id: {job_id[:8]}...). The skill-keeper agent will analyze and evolve the skill."
        return "ERROR: Skill job dispatcher not available."
    
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
        # NOTE: This tool is stubbed in Phase 2 (returns "not yet implemented").
        # Full backend implementation is in Phase 4 (SkillMetricsService.record_feedback).
        # If Phase 4 runs before Phase 2, the tool returns an error.
        # Creates a SkillUsageRecord with feedback
        # Updates denormalized counters on skills table
    
    # Attach _full_doc_ for each tool
    
    return [
        skill_search, skill_list, skill_view,
        skill_create, skill_fix, skill_feedback,
    ]
```

### Task 5: Tool Registration (4-step)

**Modify** `daemon/tools/_tool_registry.py:184` — `CATEGORY_MODULES`:
```python
"dynamic-skill": "daemon.tools.skill_tools",
```

**Modify** `daemon/tools/instance.py:52` — `INNATE_SKILL_TOOL_CATEGORIES`:
```python
"dynamic-skill": ["dynamic-skill"],
```

**Modify** `daemon/tools/instance.py:537` — `create_instance_tools()`:
```python
# ── Dynamic Skill tools (always available, auto-granted via INNATE_SKILL_TOOL_CATEGORIES) ──
skill_tool_list = create_skill_tools(manager, current_instance_id)
tools.extend(skill_tool_list)
```

### Task 6: Innate Skill Doc (created here, expanded in Phase 6)

**Create** `agents/_prompt_system/innate-skills/dynamic-skill/skill.md`:

> **Note:** This innate skill doc is created here in Phase 2 because the skill-keeper agent (Phase 5) needs it. Phase 6 Task 1 will expand and polish this doc with additional examples, best practices, and match score interpretation guidance.

```markdown
# Dynamic Skill System

Dynamic skills are living, evolvable capabilities stored in the ensemble database.
Unlike innate skills (which are static and human-authored), dynamic skills can be
created, searched, and improved over time based on real usage outcomes.

## When to Use Dynamic Skills

- **Automatic injection**: If your agent has `skill_injection: true` in meta.json,
  relevant skills are automatically injected as a `HumanMessage` before each
  real user message. You don't need to do anything — just read and apply them.
- **Explicit search**: Use `skill_search()` when you need to find a skill for a
  specific task that wasn't auto-injected.
- **Browse available skills**: Use `skill_list()` to see all skills, `skill_view()`
  to read full content.

## Tool Reference

| Tool | Purpose |
|------|---------|
| `skill_search(query)` | Search skills by natural language query |
| `skill_list(category?)` | List all available skills |
| `skill_view(skill_id)` | View full skill content + lineage |
| `skill_create(name, description, content)` | Create a new skill |
| `skill_fix(skill_id, issue_description)` | Report a skill issue for evolution |
| `skill_feedback(skill_id, applied?, note?)` | Provide feedback on skill usefulness |

## Feedback is Critical

After using an injected skill, call `skill_feedback()` to report whether it was
helpful. This feedback drives the evolution system — skills that consistently
fail get fixed or deactivated, while successful patterns are reinforced.

- `applied=true` → skill was directly useful
- `applied=false` → skill was not relevant or unhelpful  
- `applied=null` (omit) → unsure, just leaving a note
```

### Task 7: Wire Services into InstanceManager

**Modify** `daemon/manager.py`:
- Initialize `SkillStoreService`, `SkillSearchService`, `SkillEmbeddingService` during startup
- Store as `self._skill_store_service`, `self._skill_search_service`, `self._skill_embedding_service`
- Pass engine and config to service constructors

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `daemon/services/skill_embedding_service.py` | Create | Embedding generation + management |
| `daemon/services/skill_store_service.py` | Create | Skill CRUD service layer |
| `daemon/services/skill_search_service.py` | Create | 3-stage search pipeline |
| `daemon/tools/skill_tools.py` | Create | 6 agent tools |
| `daemon/tools/_tool_registry.py` | Modify | Add `"dynamic-skill"` to `CATEGORY_MODULES` |
| `daemon/tools/instance.py` | Modify | Add to `INNATE_SKILL_TOOL_CATEGORIES` + wire in `create_instance_tools` |
| `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` | Create | Innate skill doc (created in Phase 2, expanded in Phase 6) |
| `daemon/manager.py` | Modify | Initialize skill services |

## Constraints
- BM25 must be in-memory (no external search engine dependency)
- Embedding service must gracefully degrade if endpoint unavailable
- **No numpy** — all vector math in pure Python (`math.sqrt()`, `sum()`). Store embeddings as JSON arrays of floats in JSONBType column.
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)
- Tools follow exact pattern: `@register_tool_category` (outermost) → `@tool` → async function → `_full_doc_` attribute
- `dynamic-skill` category name must match across `CATEGORY_MODULES`, `INNATE_SKILL_TOOL_CATEGORIES`, and skill doc directory name
- `skill_feedback` tool is stubbed in Phase 2, fully implemented in Phase 4. If Phase 4 runs before Phase 2, the tool returns an error.
- `skill_feedback` tool must update denormalized counters atomically (when fully implemented in Phase 4)

## Testing Strategy
1. **Embedding service tests**: Mock OpenAI client, test `generate_trigger_queries`, `embed_text`, `cosine_similarity`
2. **Search service tests**: 
   - BM25 pre-filter with known corpus
   - Embedding re-rank with mock embeddings
   - LLM selection with mock LLM response
   - Full pipeline end-to-end
3. **Store service tests**: CRUD operations, embedding trigger on content change
4. **Tool tests**: Each tool function tested with mocked services
5. **Integration test**: Create skill → generate embeddings → search → verify results

## Deliverables
- [ ] `daemon/services/skill_embedding_service.py` — embedding generation + cosine similarity
- [ ] `daemon/services/skill_store_service.py` — CRUD service
- [ ] `daemon/services/skill_search_service.py` — 3-stage search pipeline
- [ ] `daemon/tools/skill_tools.py` — 6 tools registered (`skill_feedback` stubbed here, backend in Phase 4)
- [ ] `daemon/tools/_tool_registry.py` — `dynamic-skill` in `CATEGORY_MODULES`
- [ ] `daemon/tools/instance.py` — `dynamic-skill` in `INNATE_SKILL_TOOL_CATEGORIES` + wired in factory
- [ ] `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` — innate skill doc
- [ ] `daemon/manager.py` — services initialized
- [ ] Tests pass for all services and tools
