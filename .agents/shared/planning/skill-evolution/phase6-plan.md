# Phase 6: Innate Skill + Polish

## Objective
Finalize the `dynamic-skill` innate skill documentation, build the REST API endpoints for skill management (CRUD + lineage + metrics + feedback + fix + share-to-global), create the Angular frontend "Skills" page, and write comprehensive integration tests covering the full end-to-end flow.

## Coupling
- **Depends on**: Phases 1-5 (all services exist)
- **Coupling type**: loose — consumes existing service APIs, adds presentation + API layer
- **Shared files with other phases**: `daemon/routers/skills.py` (new), `daemon/api.py` (modified), `frontend/` (new components)
- **Shared APIs/interfaces**: REST API endpoints consumed by frontend and external integrations
- **Why this coupling**: This phase is the presentation/integration layer on top of all prior work

## Context
- Phases 1-5 completed: repos, services, tools, injection, metrics, evolution all functional
- Frontend is **Angular 21.2.5** with Material Design (NOT React)
- API follows existing `daemon/routers/` pattern with module-level service globals
- Existing precedent: Jobs page (`frontend/src/app/pages/jobs/jobs.component.ts`)

## Tasks

### Task 1: Expand and Polish `dynamic-skill` Innate Skill Doc

**Update** `agents/_prompt_system/innate-skills/dynamic-skill/skill.md`:

> **Note:** The initial `dynamic-skill` innate skill doc was created in Phase 2 Task 6 (needed by the skill-keeper agent in Phase 5). This task **expands and polishes** that doc — it does NOT create it from scratch.

Expand the Phase 2 doc into a complete document covering:
- What dynamic skills are and how they differ from innate skills
- Automatic injection behavior (when `skill_injection: true`)
- All 6 tools with full signatures and examples
- Feedback importance and how it drives evolution
- A/B testing explanation (why two versions might appear)
- `skill_fix` usage for reporting issues
- `skill_create` for creating new skills manually

See Phase 2 Task 6 for initial draft — expand with:
- Examples of injected skill messages
- How to interpret match scores
- Best practices for skill content authoring
- When to use `skill_search` vs relying on auto-injection

### Task 2: REST API Endpoints

**Create** `daemon/routers/skills.py`:

```python
from fastapi import APIRouter, HTTPException, Query
from typing import Any

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Module-level service globals (ensemble pattern)
_skill_store_service = None
_skill_search_service = None
_skill_metrics_service = None
_skill_evolution_service = None

def set_skill_services(store, search, metrics, evolution):
    global _skill_store_service, _skill_search_service, _skill_metrics_service, _skill_evolution_service
    _skill_store_service = store
    _skill_search_service = search
    _skill_metrics_service = metrics
    _skill_evolution_service = evolution

# ── Skill CRUD ──

@router.get("")
async def list_skills(
    project_id: str | None = Query(None),
    active_only: bool = Query(True),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List skills with optional filters."""

@router.post("")
async def create_skill(body: SkillCreateRequest):
    """Create a new skill."""

@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    """Get full skill details including content."""

@router.put("/{skill_id}")
async def update_skill(skill_id: str, body: SkillUpdateRequest):
    """Update skill content/metadata."""

@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete a skill."""

@router.post("/{skill_id}/deactivate")
async def deactivate_skill(skill_id: str):
    """Deactivate a skill (soft delete)."""

# ── Search ──

@router.post("/search")
async def search_skills(body: SkillSearchRequest):
    """Search for skills by natural language query."""
    # Returns: { injected: [...], low_match: [...] }

# ── Lineage ──

@router.get("/{skill_id}/lineage")
async def get_lineage(skill_id: str):
    """Get skill lineage (parents and children)."""
    # Returns: { parents: [...], children: [...] }

# ── Metrics ──

@router.get("/{skill_id}/metrics")
async def get_metrics(skill_id: str):
    """Get aggregated metrics for a skill."""
    # Returns: { total_selections, completion_rate, fallback_rate, ... }

@router.get("/{skill_id}/usage")
async def get_usage_records(
    skill_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get raw usage records for a skill."""

# ── Feedback ──

@router.post("/{skill_id}/feedback")
async def submit_feedback(skill_id: str, body: FeedbackRequest):
    """Submit feedback on a skill (applied + note)."""

# ── Evolution ──

@router.post("/{skill_id}/fix")
async def request_fix(skill_id: str, body: FixRequest):
    """Request skill evolution (FIX). Enqueues analysis job."""
    # Returns: { job_id: str }

@router.get("/{skill_id}/ab-test")
async def get_ab_test_status(skill_id: str):
    """Get A/B testing status for a skill."""
    # Returns: { ab_test_group, variants: [...], comparisons, ready_to_resolve }

@router.post("/{skill_id}/ab-test/resolve")
async def resolve_ab_test(skill_id: str):
    """Manually resolve A/B test (if enough comparisons)."""
    # Returns: { winner_id, loser_id, winner_rate, loser_rate }

# ── Share to Global ──

@router.post("/{skill_id}/share")
async def share_to_global(skill_id: str):
    """Share a project-scoped skill to global (project_id = None)."""
    # Creates a copy with project_id=None

# ── Triggers ──

@router.get("/triggers")
async def list_triggers(project_id: str | None = Query(None)):
    """List configured skill triggers."""

@router.post("/triggers")
async def create_trigger(body: TriggerCreateRequest):
    """Create a new skill trigger."""

@router.put("/triggers/{trigger_id}")
async def update_trigger(trigger_id: str, body: TriggerUpdateRequest):
    """Update a trigger."""

@router.delete("/triggers/{trigger_id}")
async def delete_trigger(trigger_id: str):
    """Delete a trigger."""
```

**Create** `daemon/routers/skill_schemas.py` — Pydantic request/response models:
```python
class SkillCreateRequest(BaseModel):
    name: str
    description: str
    content: str
    project_id: str | None = None
    category: str = "workflow"

class SkillUpdateRequest(BaseModel):
    description: str | None = None
    content: str | None = None
    category: str | None = None
    is_active: bool | None = None

class SkillSearchRequest(BaseModel):
    query: str
    project_id: str | None = None
    max_results: int = 2

class FeedbackRequest(BaseModel):
    applied: bool | None = None
    note: str = ""

class FixRequest(BaseModel):
    issue_description: str
    suggested_fix: str | None = None

class TriggerCreateRequest(BaseModel):
    name: str
    condition_type: str
    condition_json: dict
    action: str
    project_id: str | None = None
```

### Task 3: Register Router

**Modify** `daemon/api.py`:
```python
# In imports:
from daemon.routers import skills_router

# In router mounting (before SPA catch-all):
api_router.include_router(skills_router)

# In lifespan function:
from daemon.routers.skills import set_skill_services
set_skill_services(
    store=manager._skill_store_service,
    search=manager._skill_search_service,
    metrics=manager._skill_metrics_service,
    evolution=manager._skill_evolution_service,
)
```

### Task 4: Angular Frontend — Skills Page

**Create** `frontend/src/app/pages/skills/skills.component.ts`:

```typescript
@Component({
  selector: 'app-skills',
  standalone: true,
  imports: [CommonModule, MaterialModules],
  templateUrl: './skills.component.html',
  styleUrl: './skills.component.scss'  // Angular 17+ uses styleUrl (singular), not styleUrls
})
export class SkillsComponent implements OnInit {
  skills: Skill[] = [];
  selectedSkill: Skill | null = null;
  loading = false;
  
  // View modes: 'list' | 'detail' | 'create'
  viewMode: 'list' | 'detail' | 'create' = 'list';
  
  // Filters
  projectFilter: string | null = null;
  activeOnly = true;
  categoryFilter: string | null = null;
  
  ngOnInit() { this.loadSkills(); }
  
  loadSkills() { /* GET /api/skills */ }
  selectSkill(id: string) { /* GET /api/skills/{id} → detail view */ }
  createSkill(data) { /* POST /api/skills */ }
  deactivateSkill(id: string) { /* POST /api/skills/{id}/deactivate */ }
  searchSkills(query: string) { /* POST /api/skills/search */ }
}
```

**Create** `frontend/src/app/pages/skills/skills.component.html`:
- List view: **Card-based list** (NOT MatTableModule — existing pages like Jobs use custom card components like `JobCardComponent`). Create a `SkillCardComponent` following the same pattern. Each card shows: name, description, category, status badge, metrics summary (completion rate, selections).
- Detail view: Full skill content (markdown rendered), lineage tree, usage metrics, A/B test status
- Create form: Name, description, content (markdown editor), category
- Search bar: Natural language search with results display
- Action buttons: View, Edit, Deactivate, Fix, Share to Global
- Use Angular Material components (not ng-zorro-antd) to match existing pages

**Create** `frontend/src/app/services/skill.service.ts`:
```typescript
@Injectable({ providedIn: 'root' })
export class SkillService {
  private apiUrl = '/api/skills';
  
  list(params): Observable<{items: Skill[], total: number}> { ... }
  get(id: string): Observable<SkillDetail> { ... }
  create(data: SkillCreate): Observable<Skill> { ... }
  update(id: string, data): Observable<Skill> { ... }
  delete(id: string): Observable<void> { ... }
  search(query: string): Observable<SearchResults> { ... }
  getMetrics(id: string): Observable<SkillMetrics> { ... }
  getLineage(id: string): Observable<Lineage> { ... }
  submitFeedback(id: string, data): Observable<void> { ... }
  requestFix(id: string, data): Observable<{job_id: string}> { ... }
  getAbTestStatus(id: string): Observable<AbTestStatus> { ... }
}
```

**Create** `frontend/src/app/models/skill.model.ts`:
```typescript
export interface Skill {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  category: string;
  is_active: boolean;
  status: string;
  lineage_origin: string;
  generation: number;
  ab_test_group: string | null;
  total_selections: number;
  total_applied: number;
  total_completions: number;
  total_fallbacks: number;
  consecutive_failures: number;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface SkillDetail extends Skill {
  content: string;
  lineage: { parents: any[]; children: any[] };
  metrics: SkillMetrics;
}

export interface SkillMetrics {
  total_selections: number;
  total_applied: number;
  total_completions: number;
  total_fallbacks: number;
  completion_rate: number;
  fallback_rate: number;
  applied_rate: number;
  consecutive_failures: number;
}
```

### Task 5: Frontend Routing

**Modify** `frontend/src/app/app.routes.ts` (or equivalent routing config):
```typescript
{ 
  path: 'skills', 
  loadComponent: () => import('./pages/skills/skills.component').then(m => m.SkillsComponent),
  title: 'Skills'
},
```

**Modify** `frontend/src/app/app.html:11-16` — navigation is inline in `app.html` (NOT a separate component):
- Add "Skills" link directly in `app.html` after the "Jobs" link
- Icon: `🧠` or Material icon `school`
- Route: `/skills`
- Example:
```html
<!-- Add after the Jobs link in app.html -->
<a routerLink="/skills" routerLinkActive="active">
  <mat-icon>school</mat-icon>
  <span>Skills</span>
</a>
```

### Task 6: Integration Tests

**Create** `tests/integration/test_skill_evolution_e2e.py`:

```python
class TestSkillEvolutionE2E:
    """End-to-end integration test: create → inject → metrics → evolve → A/B → resolve."""
    
    async def test_full_lifecycle(self, test_client, test_db):
        # 1. Create a skill
        skill = await create_skill(test_client, name="code-review", 
                                    description="How to review code",
                                    content="Review code by checking...")
        assert skill["id"]
        
        # 2. Verify embeddings generated
        embeddings = await get_skill_embeddings(test_db, skill["id"])
        assert len(embeddings) >= 3  # At least 3 trigger queries
        
        # 3. Search for the skill
        results = await search_skills(test_client, query="review my code")
        assert any(s["name"] == "code-review" for s in results["injected"])
        
        # 4. Simulate injection + task completion
        await simulate_task_completion(test_db, skill_id=skill["id"],
                                       task_succeeded=True, iterations=3, duration=30)
        
        # 5. Verify metrics recorded
        metrics = await get_metrics(test_client, skill["id"])
        assert metrics["total_selections"] == 1
        assert metrics["total_completions"] == 1
        
        # 6. Submit feedback
        await submit_feedback(test_client, skill["id"], applied=True, note="Helpful")
        metrics = await get_metrics(test_client, skill["id"])
        assert metrics["total_applied"] == 1
        
        # 7. Trigger failure metrics
        for _ in range(5):
            await simulate_task_completion(test_db, skill_id=skill["id"],
                                           task_succeeded=False, iterations=10, duration=120)
        
        # 8. Run trigger engine
        flagged = await run_triggers(test_db, project_id=skill["project_id"])
        assert any(f["skill_id"] == skill["id"] for f in flagged)
        
        # 9. Evolve (FIX)
        evolution = await evolve_skill(test_db, skill["id"], "FIX", "Add error handling")
        assert evolution["generation"] == 1
        assert evolution["ab_test_group"]
        
        # 10. Verify A/B testing
        ab_status = await get_ab_test_status(test_client, skill["id"])
        assert len(ab_status["variants"]) == 2
        
        # 11. Simulate A/B comparisons
        # In production, variant selection is deterministic (hash-based).
        # In tests, we simulate by assigning tasks to each variant.
        import hashlib
        for i in range(10):
            # Alternate between variants to simulate deterministic selection
            variant = ab_status["variants"][i % 2]
            # Make variant A consistently better to ensure resolution
            success = (i % 2 == 0)  # variant 0 always succeeds, variant 1 always fails
            await simulate_task_completion(test_db, skill_id=variant["id"],
                                           task_succeeded=success,
                                           iterations=3, duration=30)
        
        # 12. Resolve A/B test
        resolution = await resolve_ab_test(test_client, skill["id"])
        assert resolution["winner_id"]
        assert resolution["loser_id"]
        
        # 13. Verify loser deactivated
        loser = await get_skill(test_client, resolution["loser_id"])
        assert not loser["is_active"]
```

**Create** `tests/integration/test_skill_capture.py`:
```python
class TestSkillCapture:
    async def test_capture_on_complex_success(self, ...):
        # Simulate: successful task, no skill, high complexity → capture triggered
    
    async def test_no_capture_on_simple_task(self, ...):
        # Simulate: successful task, no skill, low complexity → no capture
    
    async def test_no_capture_when_skill_applied(self, ...):
        # Simulate: successful task, skill applied → no capture
```

### Task 7: Config Documentation

**Create** `docs/skill-evolution-config.md`:
```markdown
# Skill Evolution Configuration

## SkillEvolutionConfig

All settings are in `SkillEvolutionConfig` and can be overridden via environment variables
or config file.

### Embedding Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `embedding_model` | `text-embedding-3-small` | OpenAI-compatible embedding model |
| `embedding_dimensions` | `1536` | Embedding vector dimensions |
| `embedding_base_url` | `None` (falls back to LLM base_url) | Embedding API endpoint |
| `embedding_api_key` | `None` (falls back to LLM api_key) | Embedding API key |

### Evolution Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `evolution_model` | `None` (main model) | Model for Tier 3 evolution |
| `analysis_model` | `None` (main model) | Cheap model for Tier 2 analysis |

### Injection Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `max_inject_skills` | `2` | Max skills fully injected per message |
| `min_score_full_inject` | `0.7` | Min score for full injection |
| `min_score_low_match` | `0.3` | Min score for low-match list |
| `bm25_top_k` | `10` | BM25 pre-filter candidate count |
| `llm_select_top_k` | `5` | LLM selection candidate count |

### Trigger Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `default_task_count_threshold` | `20` | Tasks before periodic analysis |
| `default_daily_scan_hour` | `3` | Hour for daily trigger scan |

### A/B Testing
| Setting | Default | Description |
|---------|---------|-------------|
| `ab_sample_size` | `10` | Comparisons before A/B resolution |
| `ab_min_difference` | `0.15` | Minimum completion_rate difference (15%) to resolve. If difference < threshold after N comparisons, extend by another N. |
| `max_extensions` | `3` | After 3 extensions (30 total comparisons), force-resolve by raw completion_rate even if difference < threshold. |

### Capture
| Setting | Default | Description |
|---------|---------|-------------|
| `capture_min_iterations` | `5` | Min iterations for capture |
| `capture_min_duration_seconds` | `60` | Min duration for capture |

## Enabling Skill Injection per Agent

Add to agent's `meta.json`:
```json
{
  "skill_injection": true
}
```
```

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` | Update | Expand innate skill doc (created in Phase 2) |
| `daemon/routers/skills.py` | Create | REST API endpoints |
| `daemon/routers/skill_schemas.py` | Create | Pydantic request/response models |
| `daemon/api.py` | Modify | Register skills router + init services |
| `frontend/src/app/pages/skills/skills.component.ts` | Create | Angular skills page (standalone, `styleUrl` singular) |
| `frontend/src/app/pages/skills/skills.component.html` | Create | Page template |
| `frontend/src/app/components/skill-card/` | Create | Skill card component (follows `JobCardComponent` pattern) |
| `frontend/src/app/services/skill.service.ts` | Create | API service |
| `frontend/src/app/models/skill.model.ts` | Create | TypeScript models |
| `frontend/src/app/app.routes.ts` | Modify | Add /skills route |
| `frontend/src/app/app.html` | Modify | Add "Skills" nav link inline (after "Jobs" link) |
| `tests/integration/test_skill_evolution_e2e.py` | Create | E2E integration test |
| `tests/integration/test_skill_capture.py` | Create | Capture flow test |
| `docs/skill-evolution-config.md` | Create | Config documentation |

## Constraints
- Frontend is Angular 21.2.5 with Material Design (NOT React)
- API follows `daemon/routers/` pattern with module-level service globals
- Router must be registered BEFORE SPA catch-all route in `daemon/api.py`
- Frontend uses standalone components (no NgModule)
- Use `styleUrl` (singular), NOT `styleUrls` — Angular 17+ syntax
- Navigation: "Skills" link added inline in `frontend/src/app/app.html:11-16` (NOT a separate component)
- Use **card-based list pattern** (e.g., `SkillCardComponent`), NOT `MatTableModule` — matches existing pages like Jobs (`JobCardComponent`)
- Use Angular Material components (not ng-zorro-antd) to match existing pages
- Integration tests must run against PostgreSQL (primary test DB)
- All API endpoints must have proper error handling (503 if services not initialized)
- Config access: `self._config.skill_evolution` (where `self._config` is `Config` from `daemon/config.py:473`, NOT `EnsembleConfig`)

## Testing Strategy
1. **API endpoint tests** (`tests/unit/routers/test_skills.py`):
   - Each endpoint: success case + error cases
   - Verify 503 when services not initialized
   - Verify pagination, filtering
2. **Frontend tests**:
   - Component rendering
   - Service API calls (mock HttpClient)
   - Navigation routing
3. **Integration tests** (`tests/integration/`):
   - Full lifecycle: create → inject → metrics → evolve → A/B → resolve
   - Capture flow: complex success → capture → new skill created
   - Trigger engine: accumulate failures → trigger fires → analysis enqueued
4. **Regression tests**: All existing tests still pass

## Deliverables
- [ ] `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` — expanded doc (from Phase 2 stub)
- [ ] `daemon/routers/skills.py` — all REST endpoints
- [ ] `daemon/routers/skill_schemas.py` — request/response models
- [ ] `daemon/api.py` — router registered + services initialized
- [ ] Angular frontend: skills page (`styleUrl` singular), `SkillCardComponent`, service, models, routing
- [ ] Navigation: "Skills" link added inline in `app.html`
- [ ] `tests/integration/test_skill_evolution_e2e.py` — E2E test passing
- [ ] `tests/integration/test_skill_capture.py` — capture test passing
- [ ] `docs/skill-evolution-config.md` — config documentation
- [ ] All existing tests pass (0 regressions)
