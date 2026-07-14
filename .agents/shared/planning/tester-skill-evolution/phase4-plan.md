# Phase 4: Clone-on-Miss

## Objective

Implement the bridge between Skill Bank (templates) and the Skills Evolution System (project-scoped copies). When a skill is needed, the system clones the template from `skill_bank` into the `skills` table with `lineage_origin='bank_clone'`, propagating the `auto_load` flag from the template.

## Coupling

- **Depends on**: Phase 2 (schema: `auto_load` on both tables, `source_skill_bank_id` on skills), Phase 3 (seeded templates)
- **Coupling type**: tight
- **Shared files**: Phase 5 uses `ensure_auto_load_skills_sync()` from this phase

## Context

### Clone Flow

```
Agent needs skill (by name + agent_id)
    │
    ▼
Check skills table: get_by_name(project_id, name, generation=0)
    │
    ├── FOUND → Return existing skill (no clone)
    │
    └── NOT FOUND → Check skill_bank: get_by_name_and_agent(name, agent_id)
            │
            ├── FOUND → Clone to skills table
            │            └─ auto_load copied from template.auto_load
            │            └─ source_skill_bank_id = template.id
            │
            └── NOT FOUND → Return None
```

### auto_load Propagation (C2 fix)

The `auto_load` flag is defined in `skill-set.md`, stored on the `skill_bank` template during seeding (Phase 3), and read from the template during clone. **No hardcoding** — the value flows: skill-set.md → skill_bank.auto_load → skills.auto_load.

## New Files

```
daemon/services/skill_clone_service.py     ← NEW
tests/unit/test_skill_clone_service.py     ← NEW
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `SkillCloneService` | Clone logic with auto_load propagation | `daemon/services/skill_clone_service.py` (NEW) |
| 2 | Implement `clone_on_miss_sync()` | Sync check-then-clone (for prompt loader) | same |
| 3 | Implement `ensure_auto_load_skills_sync()` | Clone all auto_load templates for agent+project | same |
| 4 | Implement `ensure_all_skills_sync()` | Clone ALL templates for agent (for injection pipeline) | same |
| 5 | Wire service into manager | Init after skill repos + bank repo | `daemon/manager.py:~1055` |
| 6 | Integrate clone into injection pipeline | Clone before search | `daemon/services/instance_messaging.py:~1915` |
| 7 | Write unit tests | Clone correctness, auto_load propagation, idempotency | `tests/unit/test_skill_clone_service.py` (NEW) |

## Detailed Design

### 4.1 SkillCloneService — Full Implementation

**File**: `daemon/services/skill_clone_service.py` (NEW)

```python
"""Clone-on-miss service: bridges Skill Bank → Skills Evolution.

When a skill is needed but doesn't exist in the project-scoped
``skills`` table, this service clones the template from
``skill_bank`` into ``skills`` with lineage_origin='bank_clone'.

The auto_load flag propagates from the bank template to the
cloned skill — NO hardcoding.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SkillCloneService:
    """Bridges skill_bank templates to project-scoped skills.
    
    Provides BOTH sync and async methods:
    - Sync methods: for use in the synchronous prompt loader context
    - Async methods: for use in the async injection pipeline
    
    Repositories are synchronous; async wrappers bridge via asyncio.to_thread.
    """
    
    def __init__(
        self,
        skill_repo: Any,           # SkillRepository
        skill_bank_repo: Any,      # SkillBankRepository
        embedding_service: Any = None,  # SkillEmbeddingService (optional)
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_bank_repo = skill_bank_repo
        self._embedding_service = embedding_service
    
    # ================================================================
    # SYNC METHODS (for prompt loader / instance_lifecycle)
    # ================================================================
    
    def clone_on_miss_sync(
        self,
        name: str,
        agent_id: str,
        project_id: str,
    ) -> Any | None:
        """Sync clone: check project skills → miss → clone from bank.
        
        Returns the Skill (existing or cloned), or None if no template.
        """
        # Step 1: Check if skill already exists in project scope
        existing = self._skill_repo.get_by_name(
            project_id=project_id,
            name=name,
            generation=0,
        )
        if existing is not None:
            return existing
        
        # Step 2: Find template in skill_bank
        template = self._skill_bank_repo.get_by_name_and_agent(name, agent_id)
        if template is None:
            logger.debug(
                f"No skill template for clone: name={name}, agent={agent_id}"
            )
            return None
        
        # Step 3: Clone (auto_load read from template — NOT hardcoded)
        return self._clone_template_sync(template, project_id)
    
    def ensure_auto_load_skills_sync(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Any]:
        """Sync: ensure all auto_load skills for agent exist in project.
        
        Queries skill_bank for auto_load=true templates for this agent,
        clones any that don't exist in project scope. Called by
        append_auto_load_skills() before querying the skills table.
        """
        # Get auto_load templates from bank
        templates = self._skill_bank_repo.get_auto_load_by_agent(agent_id)
        results: list[Any] = []
        
        for template in templates:
            # Check if already cloned (idempotent)
            existing = self._skill_repo.get_by_name(
                project_id=project_id,
                name=template.name,
                generation=0,
            )
            if existing is not None:
                results.append(existing)
                continue
            
            # Clone — auto_load=True because these are auto_load templates
            cloned = self._clone_template_sync(template, project_id)
            if cloned is not None:
                results.append(cloned)
        
        return results
    
    def ensure_all_skills_sync(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Any]:
        """Sync: ensure ALL skills for agent exist in project.
        
        Used by the injection pipeline to guarantee skills are
        discoverable by BM25 search. Clones all templates
        (auto_load and on-demand) for the agent.
        """
        templates = self._skill_bank_repo.list_by_agent(agent_id)
        results: list[Any] = []
        
        for template in templates:
            existing = self._skill_repo.get_by_name(
                project_id=project_id,
                name=template.name,
                generation=0,
            )
            if existing is not None:
                results.append(existing)
                continue
            
            cloned = self._clone_template_sync(template, project_id)
            if cloned is not None:
                results.append(cloned)
        
        return results
    
    def _clone_template_sync(
        self,
        template: Any,  # SkillBankItem
        project_id: str,
    ) -> Any:
        """Clone a SkillBankItem into a project-scoped Skill (sync).
        
        auto_load is read from template.auto_load — NOT hardcoded.
        source_skill_bank_id links back to the template.
        Embeddings computed best-effort.
        """
        cloned = self._skill_repo.create(
            name=template.name,
            description=template.description,
            content=template.content,
            project_id=project_id,
            category=template.category,
            lineage_origin="bank_clone",
            generation=0,
            status="active",
            is_active=True,
            # C2 FIX: auto_load read from template, NOT hardcoded to False
            auto_load=template.auto_load,
            source_skill_bank_id=template.id,
        )
        logger.info(
            f"Cloned skill from bank: name={template.name}, "
            f"project={project_id[:8]}..., auto_load={template.auto_load}, "
            f"source_skill_bank_id={template.id}"
        )
        
        # Best-effort embedding computation
        self._try_compute_embeddings(cloned)
        
        return cloned
    
    # ================================================================
    # ASYNC METHODS (for injection pipeline)
    # ================================================================
    
    async def ensure_all_skills_async(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Any]:
        """Async wrapper: ensure all skills for agent exist in project."""
        return await asyncio.to_thread(
            self.ensure_all_skills_sync, agent_id, project_id
        )
    
    async def clone_on_miss_async(
        self,
        name: str,
        agent_id: str,
        project_id: str,
    ) -> Any | None:
        """Async wrapper for clone_on_miss_sync."""
        return await asyncio.to_thread(
            self.clone_on_miss_sync, name, agent_id, project_id
        )
    
    # ================================================================
    # EMBEDDING GENERATION (W3)
    # ================================================================
    
    def _try_compute_embeddings(self, skill: Any) -> None:
        """Best-effort embedding computation for a cloned skill.
        
        Generates 3-10 trigger query embeddings via the embedding
        service so the BM25→embedding→LLM search pipeline can find
        the skill. Failures are logged but don't block the clone.
        
        Embedding generation uses the LLM to produce trigger queries
        from the skill content, then embeds each query. The resulting
        vectors are stored in the skill_embeddings table.
        
        If embedding_service is None (skill_evolution partially
        configured) or the external embedding endpoint is unavailable,
        the skill is still usable — BM25 search alone will find it
        (Stage 1 of the search pipeline doesn't require embeddings).
        """
        if self._embedding_service is None:
            logger.debug(
                f"Skipping embeddings for {skill.name}: "
                f"no embedding service configured"
            )
            return
        
        try:
            # SkillEmbeddingService.refresh_embeddings() generates
            # trigger queries via LLM, embeds them, and stores in
            # skill_embeddings table. This is the same method
            # SkillStoreService calls after create_skill().
            self._embedding_service.refresh_embeddings_sync(skill)
            logger.debug(
                f"Embeddings computed for cloned skill: {skill.name}"
            )
        except Exception as e:
            logger.warning(
                f"Embedding computation failed for cloned skill "
                f"{skill.name}: {e}. Skill still usable via BM25 search."
            )
```

### 4.2 Manager Wiring

**File**: `daemon/manager.py`

In `__init__`, after skill_bank_repo creation (~line 745):
```python
        self._skill_clone_service = None  # Wired in skill_evolution block
```

In the skill_evolution config block (~line 1055, after all skill services):
```python
            self._skill_clone_service = SkillCloneService(
                skill_repo=self._skill_repo,
                skill_bank_repo=self._skill_bank_repo,
                embedding_service=self._skill_embedding_service,
            )
```

**Import**:
```python
from .services.skill_clone_service import SkillCloneService
```

### 4.3 Injection Pipeline Integration

**File**: `daemon/services/instance_messaging.py:~1915`

Before the existing injection search, trigger clone-on-miss for ALL agent skills:

```python
                        if agent_meta and getattr(
                            agent_meta, "skill_injection", False
                        ):
                            skill_project_id: str | None = None
                            if skill_instance_meta.instance_metadata:
                                skill_project_id = (
                                    skill_instance_meta.instance_metadata.get(
                                        "project_id"
                                    )
                                )
                            
                            # ── Clone-on-miss: ensure skills exist ──
                            clone_service = getattr(
                                self._manager, "_skill_clone_service", None
                            )
                            if (
                                clone_service is not None
                                and skill_project_id is not None
                            ):
                                try:
                                    await clone_service.ensure_all_skills_async(
                                        agent_id=skill_instance_meta.agent_id,
                                        project_id=skill_project_id,
                                    )
                                except Exception as clone_exc:
                                    logger.warning(
                                        f"Clone-on-miss failed for "
                                        f"{instance_id[:8]}...: {clone_exc}"
                                    )
                            
                            injection_service = getattr(
                            # ... (rest of existing injection code unchanged) ...
```

### 4.4 Embedding Generation Strategy (W3)

| Scenario | Embedding Status | Search Works? |
|----------|-----------------|---------------|
| Embedding service configured + endpoint available | ✅ Trigger queries generated + embedded | ✅ Full 3-stage pipeline (BM25 + cosine + LLM) |
| Embedding service configured but endpoint down | ❌ Warning logged | ✅ BM25-only (Stage 1 still works) |
| Embedding service is None (partial config) | ❌ Skipped | ✅ BM25-only |
| Clone succeeds but embedding fails | ❌ Skill exists without embeddings | ✅ BM25-only |

The search pipeline (`SkillSearchService`) already has graceful degradation when embeddings are missing — Stage 2 (cosine re-rank) just returns the BM25 results unmodified.

## Key Files

- `daemon/services/skill_clone_service.py` — NEW
- `daemon/manager.py:~1055` — initialization
- `daemon/services/instance_messaging.py:~1915` — injection integration

## Constraints

- `auto_load` is **always read from template.auto_load** — never hardcoded
- Clone is **idempotent**: existing skill → return it, don't re-clone
- Clone must NOT fail the injection pipeline (try/except, degrade gracefully)
- Sync methods for prompt loader; async wrappers for injection pipeline
- Embedding computation is **best-effort** — failures don't block clone
- Requires `skill_evolution` config (skills table is evolution system)

## Test Strategy

**File**: `tests/unit/test_skill_clone_service.py` (NEW)

1. **Clone new skill** — template exists, no project skill → clone succeeds
2. **Clone idempotency** — skill already exists → returns existing
3. **auto_load propagation (C2)** — template with auto_load=True → cloned skill has auto_load=True; template with auto_load=False → cloned skill has auto_load=False
4. **Missing template** — no template in bank → returns None
5. **source_skill_bank_id** — cloned skill has correct template ID
6. **ensure_auto_load_skills_sync** — clones only auto_load templates
7. **ensure_all_skills_sync** — clones ALL templates for agent
8. **Embedding failure graceful** — embedding service raises → clone succeeds
9. **No embedding service** — embedding_service=None → clone succeeds, no embeddings

## Deliverables

- [ ] `SkillCloneService` with sync + async methods
- [ ] `auto_load` propagated from template (C2 fix — no hardcoding)
- [ ] `ensure_auto_load_skills_sync()` for prompt loader (Phase 5)
- [ ] `ensure_all_skills_sync/async()` for injection pipeline
- [ ] `_try_compute_embeddings()` best-effort (W3)
- [ ] Manager initializes service when skill_evolution configured
- [ ] Injection pipeline triggers clone before search
- [ ] Unit tests pass
