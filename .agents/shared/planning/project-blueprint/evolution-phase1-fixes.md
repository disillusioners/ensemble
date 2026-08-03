# Phase 1: Critical Wiring Fixes + Canonical Write Boundary for Project Blueprint Subsystem

**Date:** 2026-08-03
**Author:** Worker via plan-creation skill (rev 2 — addresses reviewer issues C2, C4, C5, C9 and 5 leader decisions)
**Status:** Ready for Review (rev 2 supersedes rev 1)
**Parent Plan:** `.agents/shared/planning/project-blueprint/plan-overview.md` + `.agents/shared/planning/project-blueprint/evolution-plan.md`

---

## Revision 2 — What Changed

| Reviewer issue | Status | Change |
|---|---|---|
| **C2** (G4 still coupled to `skill_evolution`) | FIXED | G4 rewritten: blueprint embedding service + repo are constructed independently of `skill_evolution`. `BlueprintMatcher` construction moved outside the skill guard. The `self.config.blueprint is not None` check is replaced by a real config check (`config.blueprint.embedding_model` non-None + embedding service available). New test added: `skill_evolution=None` + blueprint config set → matcher initializes. |
| **C4** (3 bugs in trigger replacement) | FIXED | G1 now treats content + trigger_queries + embeddings as ONE publish unit. Embed triggers BEFORE final commit; on embedding failure, retry then roll back. Empty `trigger_queries=[]` now calls `replace_triggers(blueprint_id, [])` to clear all old triggers (distinguished from `None` = leave unchanged). `update()` extracts `reason` from `fields` BEFORE the setattr loop in the actual code block (not a footnote). |
| **C5** (router bypasses Phase 1 fixes) | FIXED | NEW section: **Canonical Write Boundary**. All 5 write paths (REST CRUD, blueprinter tools, scan/rebuild dispatch) route through a new `BlueprintWriteService` that owns rate limiting, trigger embedding, revision capture, and the atomic publish unit. The router and tools call the service; only the service touches the repository. |
| **C9** (rate limiter budget insufficient for full rebuild) | FIXED | NEW section: **Write Budget Management**. Build-side code counts writes before publication, persists a `save_plan` to project metadata, processes bounded resumable batches, and explicitly reports `partial: X of Y, rate-limited, will continue after cooldown` instead of pretending success. A rebuild-bypass mode is described (option B) for evaluation. |
| **Leader D1** (processed_at soft-delete) | ACCEPTED | Acknowledgement-soft-delete pattern referenced in **§C4 Publish Unit** and the **Write Budget** section; full design lives in Phase 2 but Phase 1's pre-publish snapshot reads `processed_at` and treats crashed ack as recoverable. |
| **Leader D2** (G7 auto-dedup) | ACCEPTED | Noted in **§G1** as a Phase 2 prerequisite that Phase 1 must not break; Phase 1 only adds the `replace_triggers` semantics that G7 will rely on. |
| **Leader D3** (model tier `balanced` if available, else `quick`) | ACCEPTED | **§G1 / Write Budget** notes that the blueprinter's `explore` + `decide` phases should consult `agents/blueprinter/meta.json:decide_model_tier` with the documented fallback. |
| **Leader D4** (`project_history_add` hook factory threading) | ACCEPTED | Canonical write boundary is constructed via a factory function (matches `create_blueprint_tools(manager=manager)`) so the future Phase 3 history hook can reach the service. |
| **Leader D5** (queue concurrency verify during impl) | ACCEPTED | **§Testing** mandates a 5-concurrent-projects test that confirms the rebuilt `BlueprintWriteService` admits one write per project at a time. |

---

## Objective

Fix the 4 critical wiring gaps (G1–G4) that block basic Project Blueprint functionality AND introduce a **Canonical Write Boundary** (`BlueprintWriteService`) so that revisions, trigger replacement, rate limiting, and the atomic publish unit always run together across every write path (REST CRUD, blueprinter tools, scan/rebuild dispatch). After Phase 1, vector matching returns meaningful scores, revision history populates, writes are throttled, `BLUEPRINT_EMBEDDING_*` config is respected, and no write path can bypass safety controls.

**Single testable sentence:** When a blueprint is created, updated, or soft-deleted via ANY of the 5 write paths, exactly one revision row is captured, triggers are atomically replaced, the rate limiter is consulted, and the operation either completes cleanly or reports an unambiguous partial outcome.

---

## Entry Criteria

- All 4 gaps have been verified in the current source (confirmed via `grep` + source read on 2026-08-03)
- The `project_blueprint_triggers` and `project_blueprint_revisions` tables exist in the schema (models.py) and `create_all` has been run
- `BlueprintMatcher`, `BlueprintRateLimiter`, `BlueprintRepository` all exist and are instantiated in the manager
- `SkillEmbeddingService` exists with `embed_text()`, `generate_trigger_queries()`, and `cosine_similarity()` methods
- The 5 write paths have been enumerated (see §"Five Write Paths")

---

## Scope

### In Scope

- G4: Independent blueprint embedding service + repo (decoupled from `skill_evolution`)
- G1: Atomic publish unit (content + trigger_queries + embeddings) wired into `blueprint_create` / `blueprint_update`
- G2: Auto-record revision snapshots in `BlueprintRepository.update()` (post-commit, with `reason` extracted before setattr loop)
- G3: Enforce rate limiter (`can_proceed` / `record_success` / `record_failure`) at the canonical write boundary
- **Canonical Write Boundary (NEW):** introduce `BlueprintWriteService` and route all 5 write paths through it
- **Write Budget Management (NEW):** count writes before publication, persist a save plan, process bounded resumable batches, distinguish partial from full success
- Empty `trigger_queries=[]` semantics (clear all old triggers explicitly)
- `reason` field extraction in `update()` to prevent `ValueError: Unknown Blueprint field: reason`
- 1 new file: `daemon/services/blueprint_write_service.py`
- 1 new file: `daemon/services/blueprint_save_plan.py` (persisted save-plan model + helpers)

### Out of Scope

- The Blueprinter Evolution redesign (rebuild/incremental workflows, pending queue table, multi-worker fan-out) — separate plan (`evolution-phases-detailed.md`)
- Phase 2 data-layer fixes (G6/G7/G8, claim/acknowledge state machine, context-kind allowlist) — these are Phase 2 work
- Phase 3 admission coordinator + durable lease — Phase 3 work
- Phase 5 compare/stage/publish semantics — Phase 5 work
- pgvector / tsvector migration
- Automatic trigger-query generation via LLM within the tools (the blueprinter agent generates trigger queries itself and passes them as a param)
- Multi-process rate-limiter state (in-memory is acceptable; the build lease in Phase 3 will be the durable coordination primitive)

---

## Five Write Paths Enumerated

Phase 1 must route every one of these through `BlueprintWriteService`:

| # | Path | File | Current call | Phase 1 target |
|---|------|------|--------------|----------------|
| 1 | REST create | `daemon/routers/blueprints.py:204-229` | `repo.create(...)` | `service.create_blueprint(...)` |
| 2 | REST update | `daemon/routers/blueprints.py:353-378` | `repo.update(...)` | `service.update_blueprint(...)` |
| 3 | REST soft-delete | `daemon/routers/blueprints.py:381-400` | `repo.soft_delete(...)` | `service.disable_blueprint(...)` |
| 4 | Blueprinter tool create | `daemon/tools/blueprint.py:246-288` | `repo.create(...)` | `service.create_blueprint(...)` |
| 5 | Blueprinter tool update | `daemon/tools/blueprint.py:296-351` | `repo.update(...)` | `service.update_blueprint(...)` |

(Other write entry points — `/initialize`, `/scan` — are dispatch-only and eventually enqueue a blueprinter job whose create/update calls go through paths #4 and #5. They do not need direct service calls.)

After Phase 1, `grep -rE "BlueprintRepository\\.(create|update|soft_delete)" daemon/` should return **only matches inside `daemon/services/blueprint_write_service.py`**.

---

## Fix Ordering

```
G4 → Canonical Write Boundary skeleton → G1 (publish unit) → G2 → G3 → Write Budget Management
```

**Why this order:**

| Order | Fix | Rationale |
|-------|-----|-----------|
| **1st** | G4 | Embedding config + repo must be independent of `skill_evolution` so the canonical write boundary can call a stable service. |
| **2nd** | Canonical Write Boundary skeleton | Build the `BlueprintWriteService` class, factory, and manager wiring **before** adding business logic. Subsequent fixes (G1/G2/G3) plug into the service. Avoids the rev-1 mistake of patching individual tool/router calls and leaving a third bypass path open. |
| **3rd** | G1 (publish unit) | Depends on G4 (needs the service) and the service skeleton. Atomic publish unit is the most complex piece; build it first so the integration is observable. |
| **4th** | G2 | Independent of G1, but must run through the service so both REST and tools benefit. |
| **5th** | G3 | Independent; rate-limiter is the last invariant to centralize. |
| **6th** | Write Budget Management | Builds on G3 (rate limiter) and the canonical service. Adds pre-publication counting + save plan. |

G2 + G3 can be implemented in any order on top of the service; the service skeleton makes them parallelizable.

---

# G4 — Independent Blueprint Embedding Service (rev 2: decoupled from `skill_evolution`)

## Root Cause Analysis

**What was intended:** `BlueprintConfig` (config.py:703) extends `EmbeddingConfig` with the `BLUEPRINT_` env prefix so operators can configure a separate embedding model for blueprints via `BLUEPRINT_EMBEDDING_MODEL`, `BLUEPRINT_EMBEDDING_BASE_URL`, etc. The blueprint embedding service should be **completely independent of `skill_evolution`** — operators should be able to use the blueprint subsystem without ever enabling skill evolution.

**What shipped (rev 1 mistake):** The manager creates ONE `SkillEmbeddingService` instance configured with `self.config.skill_evolution` (manager.py:907-911), then reuses that same instance for the `BlueprintMatcher` (manager.py:934-938). The rev-1 "fix" wrapped the new `_blueprint_embedding_service` in `if self.config.blueprint is not None and self._skill_embedding_repo is not None` — but:

1. `self.config.blueprint` is **never None** because `Config.blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)` (config.py:743). The guard is vacuous.
2. The guard still depends on `self._skill_embedding_repo is not None`, which is only set when `self.config.skill_evolution is not None` (manager.py:749-756). So the "fix" did not actually decouple blueprints from skill evolution.

**Why it happened:** During initial development, the blueprint matcher was wired to reuse the existing skill embedding service as a shortcut. The separate `BlueprintConfig` with `BLUEPRINT_` prefix was designed but the service instance was never instantiated with it.

## Files Touched

| File | Change |
|------|--------|
| `daemon/repositories/blueprint/__init__.py` (new export) | Export a `create_blueprint_embedding_repository(engine)` factory (the `project_blueprint_triggers` table is reused; we want a dedicated, well-typed repo handle) |
| `daemon/repositories/blueprint/embedding_repository.py` (NEW) | Thin wrapper over the `project_blueprint_triggers` table. Provides `add_trigger(blueprint_id, query_text, embedding)`, `replace_triggers(blueprint_id, items)`, `get_triggers(blueprint_id)`. Schema-compatible with the existing trigger rows. |
| `daemon/manager.py` (~line 749-944) | Construct `_blueprint_embedding_repo` and `_blueprint_embedding_service` independently of `skill_evolution`; move `BlueprintMatcher` construction out of the `if self._skill_embedding_service is not None` guard |

## Key Changes

### `daemon/repositories/blueprint/embedding_repository.py` (NEW)

```python
"""Trigger embedding repository for project blueprints.

Stores (blueprint_id, query_text, embedding) rows in the existing
``project_blueprint_triggers`` table. Schema-compatible with
``SkillEmbeddingRepository`` so we do not need a new migration, but
logically independent: this repo is created whenever the blueprint
embedding model is configured, regardless of whether skill_evolution
is enabled.
"""
from __future__ import annotations
import logging
from typing import Any, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from .models import BlueprintTrigger

logger = logging.getLogger(__name__)


class BlueprintEmbeddingRepository:
    """CRUD for ``project_blueprint_triggers`` rows."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def replace_triggers(
        self,
        blueprint_id: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        """Atomically delete and replace all trigger rows for a blueprint."""
        with Session(self.engine) as session:
            session.execute(
                text(
                    "DELETE FROM project_blueprint_triggers "
                    "WHERE blueprint_id = :bid"
                ),
                {"bid": blueprint_id},
            )
            for query_text, embedding in items:
                session.add(
                    BlueprintTrigger(
                        blueprint_id=blueprint_id,
                        query_text=query_text,
                        embedding=list(embedding),
                    )
                )
            session.commit()
        return len(items)

    def get_triggers(self, blueprint_id: str) -> list[BlueprintTrigger]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintTrigger).where(
                        BlueprintTrigger.blueprint_id == blueprint_id
                    )
                )
            )


def create_blueprint_embedding_repository(engine: Engine) -> BlueprintEmbeddingRepository:
    return BlueprintEmbeddingRepository(engine=engine)
```

### `daemon/manager.py` — construction block (rev 2)

```python
# After blueprint_repo creation (manager.py ~line 759)

# Project Blueprint: embedding repo + service, INDEPENDENT of skill_evolution.
# Operates on the same ``project_blueprint_triggers`` table that the skill
# embedding service uses, but is constructed whenever a blueprint embedding
# model is configured. skill_evolution is NOT a prerequisite.
_blueprint_embedding_configured = (
    getattr(self.config.blueprint, "embedding_model", None) is not None
)
if _blueprint_embedding_configured:
    self._blueprint_embedding_repo = create_blueprint_embedding_repository(
        engine=self._engine,
    )
    blueprint_llm_config: dict[str, Any] = {
        "base_url": self.config.llm.base_url,
        "api_key": self.config.llm.api_key,
        "model": self.config.llm.model,
        "model_vision": self.config.llm.model_vision,
        "temperature": self.config.llm.temperature,
        "request_timeout": self.config.llm.request_timeout,
    }
    self._blueprint_embedding_service = SkillEmbeddingService(
        config=self.config.blueprint,  # ← BlueprintConfig, not skill_evolution
        embedding_repo=self._blueprint_embedding_repo,
        llm_config=blueprint_llm_config,
    )
else:
    self._blueprint_embedding_repo = None
    self._blueprint_embedding_service = None

# BlueprintMatcher: construct it whenever the embedding service is
# available, REGARDLESS of whether skill_evolution is configured.
# ``self._blueprint_matcher`` is no longer gated on
# ``self._skill_embedding_service is not None`` (rev 1 mistake).
if self._blueprint_embedding_service is not None:
    self._blueprint_matcher = BlueprintMatcher(
        repository=self._blueprint_repo,
        embedding_service=self._blueprint_embedding_service,
        config=self.config.blueprint,
    )
else:
    self._blueprint_matcher = None
    logger.info(
        "BlueprintMatcher not initialized — "
        "BLUEPRINT_EMBEDDING_MODEL not set"
    )

# Rate limiter (unchanged) — still constructed unconditionally
self._blueprint_rate_limiter = BlueprintRateLimiter()
```

**Key changes vs rev 1:**

1. The guard `_blueprint_embedding_configured` is a real config check (`embedding_model is not None`), not the vacuous `self.config.blueprint is not None`.
2. `self._blueprint_embedding_repo` is constructed by a dedicated factory (`create_blueprint_embedding_repository`) — it does NOT depend on `self._skill_embedding_repo` and is therefore usable when `skill_evolution` is disabled.
3. `BlueprintMatcher` construction is now gated only on `self._blueprint_embedding_service is not None`, not on the skill service.

### `daemon/manager.py` — factory method (rev 2: enables the canonical write service to be constructed without going through the manager's `__init__`)

```python
def get_blueprint_write_service(
    self,
    project_id: str,
) -> "BlueprintWriteService":
    """Factory for the canonical write boundary.

    Returns a service bound to ``project_id`` and the manager's
    blueprint subsystem. Used by the REST router, the blueprinter
    tools, and (in Phase 3) the admission coordinator.
    """
    return BlueprintWriteService(
        repository=self._blueprint_repo,
        embedding_repository=self._blueprint_embedding_repo,
        embedding_service=self._blueprint_embedding_service,
        rate_limiter=self._blueprint_rate_limiter,
        config=self.config.blueprint,
        project_id=project_id,
        manager=self,  # for save-plan metadata + future history hooks
    )
```

## Dependencies

- **No dependencies within Phase 1** — G4 is the foundational fix.
- G1 depends on G4 being complete (G1's publish unit calls the embedding service).

## Testing Approach

**Unit tests** — `tests/manager/test_skill_service_init.py` (extend or add a sibling `test_blueprint_service_init.py`):

| Test | What to Assert |
|------|----------------|
| `test_blueprint_embedding_service_uses_blueprint_config` | When `BLUEPRINT_EMBEDDING_MODEL=foo`, `manager._blueprint_embedding_service.config.embedding_model == "foo"`, NOT `skill_evolution.embedding_model` |
| `test_blueprint_embedding_service_independent_of_skill_evolution` | When `skill_evolution=None` but `BLUEPRINT_EMBEDDING_MODEL=foo`, `manager._blueprint_embedding_service is not None` and uses `foo` |
| `test_blueprint_matcher_constructed_without_skill_evolution` | When `skill_evolution=None` but blueprint embedding configured, `manager._blueprint_matcher is not None` |
| `test_blueprint_embedding_repo_independent` | `_blueprint_embedding_repo` is a `BlueprintEmbeddingRepository`, NOT a `SkillEmbeddingRepository` |
| `test_blueprint_embedding_service_none_when_config_missing` | When `BLUEPRINT_EMBEDDING_MODEL` is unset, `_blueprint_embedding_service is None` AND `_blueprint_matcher is None` |
| `test_vacuous_guard_removed` | Setting `Config.blueprint = None` (or removing the field) — the test ensures the old `if self.config.blueprint is not None` check no longer controls behavior; only `embedding_model` does |

**Verification step:** Set `BLUEPRINT_EMBEDDING_MODEL=different-model`, leave `skill_evolution` unconfigured, start the daemon, and confirm `_blueprint_embedding_service` is built and the matcher is usable.

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Two embedding service instances double API costs if both skill and blueprint use the same model | Low | Acceptable — both services cache embeddings. If operators want one model, they set `EMBEDDING_MODEL` (shared fallback) and both resolve to the same value. The cost is negligible since blueprints are written infrequently. |
| `BlueprintEmbeddingRepository` is not wired into the manager facade in Phase 1 | Low | We only need it in Phase 1 to satisfy the `SkillEmbeddingService` constructor (which requires an `embedding_repo`). The service's trigger CRUD delegates to `embedding_repo`; the public API (`replace_triggers`) is exercised by the canonical write service. |
| Existing tests that mock `_skill_embedding_service` on the matcher break | Low | Update test doubles to use `_blueprint_embedding_service`. The existing matcher test (`test_blueprint_matcher.py`) injects its own `Embed()` mock directly into the constructor, so it's unaffected. |
| `BlueprintConfig.embedding_model` default is `None` — operators who never set it will have no matcher | Low | Documented behavior. The default `text-embedding-3-small` lives in `EmbeddingConfig` and the shared fallback (config.py:526-540) populates it from `EMBEDDING_MODEL`. The new guard specifically checks the resolved value, not the field declaration. |

---

# Canonical Write Boundary — `BlueprintWriteService` (NEW, addresses C5)

## Why this exists (rev 1 mistake)

Rev 1 patched the blueprinter tools only. The production REST CRUD routes in `daemon/routers/blueprints.py` continued to call `BlueprintRepository` directly, bypassing G1, G2, and G3. A user-driven `POST /api/projects/{pid}/blueprints` produced no revision row, no trigger embeddings, and no rate-limit accounting. **No write path should reach the repository without going through this service.**

## Service surface

```python
class BlueprintWriteService:
    """Canonical write boundary for project blueprints.

    ALL writes — REST CRUD, blueprinter tools, scan/rebuild dispatch — must
    route through this service. Direct ``BlueprintRepository`` writes are
    forbidden after Phase 1 ships (CI grep + tests assert this).
    """

    def __init__(
        self,
        repository: BlueprintRepository,
        embedding_repository: BlueprintEmbeddingRepository | None,
        embedding_service: SkillEmbeddingService | None,
        rate_limiter: BlueprintRateLimiter,
        config: BlueprintConfig,
        project_id: str,
        manager: Any,  # for save-plan persistence
    ) -> None: ...

    # ── High-level operations ──────────────────────────────────────────

    async def create_blueprint(
        self,
        slug: str,
        name: str,
        kind: str,
        content: str,
        trigger_queries: list[str] | None = None,
        tags: list[dict] | None = None,
        file_refs: list[str] | None = None,
        reason: str | None = None,
    ) -> Blueprint: ...

    async def update_blueprint(
        self,
        blueprint_id: str,
        content: str | None = None,
        name: str | None = None,
        trigger_queries: list[str] | None = None,  # see C4: [] means clear all
        tags: list[dict] | None = None,
        file_refs: list[str] | None = None,
        reason: str | None = None,
    ) -> Blueprint: ...

    async def disable_blueprint(
        self,
        blueprint_id: str,
        reason: str | None = None,
    ) -> bool: ...

    # ── Build budget (C9) ───────────────────────────────────────────────

    async def plan_publication(
        self,
        operations: list[WriteOp],
    ) -> SavePlan: ...

    async def execute_save_plan(
        self,
        save_plan: SavePlan,
        resume: bool = False,
    ) -> SavePlanResult: ...
```

## Five invariants enforced by the service

| # | Invariant | Where it runs |
|---|-----------|---------------|
| 1 | **Rate limiter check** before any write | First line of every operation |
| 2 | **Trigger embedding generation** (atomic with content) | `create_blueprint` + `update_blueprint` publish unit |
| 3 | **Revision snapshot capture** (post-commit) | After every successful `update_blueprint` |
| 4 | **Atomic publish unit** (content + triggers + embeddings) | `create_blueprint` + `update_blueprint` |
| 5 | **Rate limiter record** (success/failure) | Last line of every operation |

Each invariant is a private method (`_check_rate_limit`, `_embed_and_store_triggers`, `_capture_revision`, `_publish_unit`, `_record_rate_result`). Operations compose them. **No operation may bypass any invariant.**

## `create_blueprint` flow (canonical)

```python
async def create_blueprint(
    self,
    slug: str,
    name: str,
    kind: str,
    content: str,
    trigger_queries: list[str] | None = None,
    tags: list[dict] | None = None,
    file_refs: list[str] | None = None,
    reason: str | None = None,
) -> Blueprint:
    """Publish a new blueprint as ONE logical operation.

    Atomic publish unit (C4 fix 1):
      1. Check rate limit
      2. Embed trigger queries (BEFORE commit)
      3. Insert blueprint row
      4. Replace triggers atomically
      5. Record rate-limiter success

    If embedding fails, the row is NOT inserted — we never have a
    blueprint without vectors when vectors were requested.
    """
    # 1. Rate limit (C8 fail-open)
    await self._check_rate_limit()

    # 2. Embed BEFORE commit (C4 fix 1)
    trigger_items: list[tuple[str, list[float]]] = []
    if trigger_queries:
        trigger_items = await self._embed_queries(trigger_queries)
        if not trigger_items and self.embedding_service is not None:
            # Embedding was requested but every query failed → abort cleanly
            raise BlueprintPublishError(
                "All trigger embeddings failed; blueprint not created. "
                "Retry or call without trigger_queries."
            )

    # 3. Insert blueprint (sync via to_thread, fire-and-forget per C8)
    bp = await asyncio.to_thread(
        self.repository.create,
        project_id=self.project_id,
        slug=slug,
        name=name,
        kind=kind,
        content=content,
        tags=tags or [],
        file_refs=file_refs or [],
    )

    # 4. Replace triggers atomically (delete-then-insert in one tx)
    if trigger_items:
        try:
            await asyncio.to_thread(
                self.embedding_repository.replace_triggers,
                bp.id, trigger_items,
            )
        except Exception as e:
            # Roll back the blueprint row so we never have a
            # trigger-less blueprint when triggers were requested
            await asyncio.to_thread(self.repository.soft_delete, bp.id)
            raise BlueprintPublishError(
                f"Trigger storage failed; blueprint rolled back: {e}"
            )

    # 5. Record success
    self._record_rate_result(success=True)
    return bp
```

## `update_blueprint` flow (canonical, addresses C4 bugs)

```python
async def update_blueprint(
    self,
    blueprint_id: str,
    content: str | None = None,
    name: str | None = None,
    trigger_queries: list[str] | None = None,  # None = unchanged, [] = clear all
    tags: list[dict] | None = None,
    file_refs: list[str] | None = None,
    reason: str | None = None,
) -> Blueprint:
    """Update a blueprint. All 5 invariants enforced.

    C4 fixes:
      - Empty list (trigger_queries=[]) explicitly clears all triggers.
        None means "leave triggers unchanged". The repository path
        no longer falls through ``if not fields: return early`` —
        it sees the explicit [] and calls replace_triggers(id, []).
      - ``reason`` is extracted from ``fields`` BEFORE the setattr
        loop (handled in ``update()`` below, not just a footnote).
    """
    await self._check_rate_limit()

    # Separate: scalar fields vs trigger-queries flag
    fields: dict = {}
    trigger_clear = False
    if content is not None:
        fields["content"] = content
    if name is not None:
        fields["name"] = name
    if tags is not None:
        fields["tags"] = tags
    if file_refs is not None:
        fields["file_refs"] = file_refs
    # C4 fix 2: distinguish None (no change) from [] (clear all)
    if trigger_queries is not None:
        if trigger_queries == []:
            trigger_clear = True
            fields["trigger_queries"] = []  # pass through to repository
        else:
            fields["trigger_queries"] = trigger_queries

    if not fields and not trigger_clear:
        raise ValueError("No fields to update")

    # Embed BEFORE commit (C4 fix 1)
    new_trigger_items: list[tuple[str, list[float]]] = []
    if trigger_queries and not trigger_clear:
        new_trigger_items = await self._embed_queries(trigger_queries)
        if not new_trigger_items and self.embedding_service is not None:
            raise BlueprintPublishError(
                "All trigger embeddings failed; update not applied. "
                "Retry or pass trigger_queries=[] to skip re-embedding."
            )

    # Apply update (sync, fire-and-forget per C8)
    try:
        bp = await asyncio.to_thread(
            self.repository.update, blueprint_id, reason=reason, **fields
        )
    except Exception as e:
        self._record_rate_result(success=False)
        raise

    if bp is None:
        raise BlueprintNotFoundError(blueprint_id)

    # Replace triggers (or clear)
    try:
        await asyncio.to_thread(
            self.embedding_repository.replace_triggers,
            blueprint_id,
            new_trigger_items if not trigger_clear else [],
        )
    except Exception as e:
        # Update succeeded; trigger replace failed. Roll back the
        # update by re-applying the pre-update state from the prior
        # revision. The update session has already committed, so we
        # do a second update that restores the previous content.
        # (Idempotent: at most one rollback per failed publish.)
        try:
            prior_revisions = await asyncio.to_thread(
                self.repository.list_revisions, blueprint_id, limit=1
            )
            if prior_revisions:
                await asyncio.to_thread(
                    self.repository.update,
                    blueprint_id,
                    content=prior_revisions[0].content_snapshot,
                    trigger_queries=prior_revisions[0].trigger_queries,
                )
        except Exception:
            pass  # C8: log + swallow; operator inspects DB
        raise BlueprintPublishError(
            f"Trigger replace failed; update rolled back: {e}"
        )

    self._record_rate_result(success=True)
    return bp
```

## `disable_blueprint` flow (canonical, NEW for C5)

```python
async def disable_blueprint(
    self,
    blueprint_id: str,
    reason: str | None = None,
) -> bool:
    """Soft-delete a blueprint via the canonical service.

    Records a final revision (status change) so the revision view
    shows the disable event.
    """
    await self._check_rate_limit()
    try:
        ok = await asyncio.to_thread(
            self.repository.soft_delete, blueprint_id
        )
    except Exception as e:
        self._record_rate_result(success=False)
        raise
    if not ok:
        raise BlueprintNotFoundError(blueprint_id)

    # Capture a revision so the audit trail shows the disable event
    try:
        await asyncio.to_thread(
            self.repository.add_revision,
            blueprint_id=blueprint_id,
            version=-1,  # sentinel: "deletion event"
            content_snapshot="",
            source="disable",
            file_refs=[],
            tags=[],
            trigger_queries=[],
            reason=reason or "blueprint disabled",
        )
    except Exception:
        pass  # C8: revision capture never blocks the disable

    self._record_rate_result(success=True)
    return True
```

## Repository changes for C4 (rev 2)

### `daemon/repositories/blueprint/repository.py` — `update()` with `reason` extracted

```python
def update(
    self,
    blueprint_id: str,
    reason: str | None = None,
    **fields: Any,
) -> Optional[Blueprint]:
    """Update a blueprint; capture a revision snapshot post-commit.

    C4 fix 3: ``reason`` is a revision metadata field, NOT a Blueprint
    field. It is extracted from kwargs BEFORE the setattr loop so the
    setattr validation does not raise ``ValueError: Unknown Blueprint
    field: reason``.
    """
    # Pop reason FIRST so it is not in ``fields`` when we setattr
    fields.pop("reason", None)
    # C4 fix 2: trigger_queries=[] is a valid clear-all signal; pass
    # through to the setattr loop. The caller (BlueprintWriteService)
    # has already distinguished [] from None.

    with Session(self.engine) as session:
        blueprint = session.get(Blueprint, blueprint_id)
        if blueprint is None:
            return None

        version_incremented = False
        if {"content", "file_refs", "tags", "trigger_queries"}.intersection(fields):
            blueprint.version += 1
            version_incremented = True

        for name, value in fields.items():
            if not hasattr(blueprint, name):
                raise ValueError(f"Unknown Blueprint field: {name}")
            setattr(blueprint, name, value)
        blueprint.updated_at = _now_iso()
        session.add(blueprint)
        session.commit()
        session.refresh(blueprint)

    # Capture revision snapshot OUTSIDE the update session (C8: never
    # roll back the update on revision failure).
    if version_incremented:
        try:
            self.add_revision(
                blueprint_id=blueprint.id,
                version=blueprint.version,
                content_snapshot=blueprint.content,
                source=blueprint.source,
                file_refs=list(blueprint.file_refs or []),
                tags=list(blueprint.tags or []),
                trigger_queries=list(blueprint.trigger_queries or []),
                reason=reason,
            )
        except Exception as e:
            logger.warning(
                "add_revision failed for blueprint %s v%d: %s",
                blueprint_id, blueprint.version, e, exc_info=True,
            )

    return blueprint
```

### `daemon/repositories/blueprint/repository.py` — `soft_delete()` records the disable event

`soft_delete()` is unchanged in body, but the canonical service wraps it with a `add_revision(version=-1, source="disable", reason=...)` call. The repo's own `add_revision` already exists and accepts any version number.

## Files Touched (C5)

| File | Change |
|------|--------|
| `daemon/services/blueprint_write_service.py` (NEW) | The service itself |
| `daemon/services/blueprint_save_plan.py` (NEW) | `SavePlan`, `WriteOp`, `SavePlanResult` dataclasses + persistence helpers |
| `daemon/manager.py` | Add `get_blueprint_write_service(project_id)` factory |
| `daemon/tools/blueprint.py` | Replace direct `repo.create/update` with `manager.get_blueprint_write_service(pid).create_blueprint/update_blueprint` |
| `daemon/routers/blueprints.py` | Replace direct `repo.create/update/soft_delete` with `manager.get_blueprint_write_service(project_id).create/update/disable` |
| `daemon/repositories/blueprint/repository.py` | `update()` extracts `reason` before setattr; new `disable_with_reason` is unnecessary (service wraps `soft_delete` with `add_revision`) |

## Testing Approach (C5)

**Unit tests** — `tests/services/test_blueprint_write_service.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_create_calls_all_5_invariants` | Mock repo, embedder, rate_limiter; assert `check_rate_limit → embed → create → replace_triggers → record_success` order |
| `test_update_with_empty_trigger_queries_clears_triggers` | `update(trigger_queries=[])` calls `replace_triggers(id, [])`, NOT `replace_triggers(id, items)` |
| `test_update_with_none_trigger_queries_leaves_triggers` | `update(trigger_queries=None)` does NOT call `replace_triggers` |
| `test_update_with_trigger_queries_replaces` | `update(trigger_queries=[a,b])` calls `replace_triggers(id, [(a,vec),(b,vec)])` |
| `test_update_extracts_reason_before_setattr` | `update(content="x", reason="y")` does NOT raise `ValueError: Unknown Blueprint field: reason`; revision row has `reason="y"` |
| `test_create_rolls_back_on_trigger_storage_failure` | Mock `replace_triggers` to raise; assert `soft_delete` was called for the new blueprint |
| `test_create_aborts_on_all_embeddings_failed` | Mock `embed_text` to always raise; assert `repo.create` is NOT called |
| `test_disable_records_revision` | `disable_blueprint(id)` calls `add_revision` with `version=-1, source="disable"` |
| `test_rate_limit_failure_aborts` | `can_proceed` returns False; `repo.create` is NOT called; returns rate-limit error |
| `test_rate_limiter_recorded_on_failure` | `repo.create` raises; `record_failure` is called |
| `test_rate_limiter_fail_open` | `can_proceed` raises; operation proceeds; `record_success` is called |
| `test_router_uses_service` | `daemon/routers/blueprints.py` does NOT import `BlueprintRepository`; `create_blueprint` calls `manager.get_blueprint_write_service` |
| `test_tools_use_service` | `daemon/tools/blueprint.py` blueprint_create/update do NOT call `manager._blueprint_repo` directly |
| `test_no_direct_repo_writes` | `grep -rE "BlueprintRepository\\.(create|update|soft_delete)" daemon/ --include="*.py" \| grep -v blueprint_write_service` returns ZERO matches |

**Integration tests** — `tests/integration/test_blueprint_canonical_write.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_rest_create_captures_revision` | `POST /api/projects/{pid}/blueprints` → 1 revision row exists |
| `test_rest_create_embeds_triggers` | `POST /api/projects/{pid}/blueprints` with `trigger_queries=[a,b]` → 2 trigger rows |
| `test_rest_create_rate_limited` | Limiter at capacity → `POST` returns 429 |
| `test_rest_update_empty_triggers_clears` | `PUT /api/projects/{pid}/blueprints/{id}` with `trigger_queries=[]` → 0 trigger rows |
| `test_rest_disable_records_revision` | `DELETE /api/projects/{pid}/blueprints/{id}` → 1 revision row with `source="disable"` |
| `test_all_5_paths_produce_identical_revision_count` | Drive 5 paths (REST create, REST update, REST delete, tool create, tool update) once each; assert 5 revisions, 5 rate-limiter ticks |

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Service introduces a sync/async boundary that complicates the call chain | Medium | Service is the only async-aware surface; the repository remains sync. All public methods are `async def` and wrap sync calls in `asyncio.to_thread`. |
| `replace_triggers(id, [])` does an explicit DELETE — must run in a transaction | Medium | The existing `replace_triggers` already does DELETE + INSERT in a single session/transaction (repository.py:148-164). Empty `items` → no INSERTs, just the DELETE; this is the correct behavior. |
| Tests that mocked `repo.update` directly will need updating | Low | Add a `BlueprintWriteService` fixture; the tests use the service mock. The repo tests still exist for the repo's own behavior; the service tests cover the integration. |
| Multiple write paths increase the surface area for bugs | Low | All paths funnel through one service; the only thing that varies is the caller. The grep test in CI prevents accidental bypass. |
| `disable_blueprint` adds a revision row → revision table grows | Low | Acceptable: revision view now shows lifecycle events. Add archival in Phase 6 tuning. |

---

# G1 — Atomic Publish Unit (rev 2: addresses C4 bugs)

## Root Cause Analysis

**What was intended:** When a blueprint is created or updated with new `trigger_queries`, the system publishes the **content + trigger_queries + embeddings** as one logical unit. The matcher's vector stage reads the resulting trigger embeddings; if any of the three pieces is missing, the blueprint is in an inconsistent state.

**What shipped (rev 1 + 3 C4 bugs):**

1. **Commit-before-embed (C4 bug 1):** Rev 1 committed the blueprint row, then tried to embed triggers as a fire-and-forget side effect. If embedding failed, the blueprint existed with `trigger_queries` in its content but no vectors in `project_blueprint_triggers`. Vector matching silently scored `0` on this blueprint.
2. **Empty-list bug (C4 bug 2):** `if trigger_queries is not None: fields["trigger_queries"] = trigger_queries` — `[]` passes the check, but then `if not fields: return early` (rev 1's early-exit) never sees `trigger_queries=[]` as the only field. The update applied other content changes, but old trigger vectors remained stale (deletions never happened).
3. **`reason` field crash (C4 bug 3):** Rev 1's `update()` code block did NOT extract `reason` from `fields` before the `setattr` loop. Rev 1 documented the fix in a footnote only. `setattr(blueprint, "reason", value)` would raise `ValueError: Unknown Blueprint field: reason` — the canonical write service's `update_blueprint(reason=...)` would crash.

**Why they happened:** Rev 1 treated the publish unit as three independent side effects (commit content, then embed, then store). It also conflated `None` (no change) with `[]` (explicit clear) for the trigger-queries signal, and treated `reason` as a Blueprint field rather than a revision metadata field.

## Files Touched (G1)

| File | Change |
|------|--------|
| `daemon/services/blueprint_write_service.py` (NEW) | Owns the publish unit; called by router + tools |
| `daemon/repositories/blueprint/embedding_repository.py` (NEW) | `replace_triggers(id, items)` is the atomic store (existing repo method relocated) |
| `daemon/repositories/blueprint/repository.py` | `update()` extracts `reason` before setattr; handles `trigger_queries=[]` correctly via the canonical service (which calls `replace_triggers(id, [])` explicitly) |

## Key Changes (G1, rev 2)

The publish unit is implemented in `BlueprintWriteService.create_blueprint` / `update_blueprint` (see **Canonical Write Boundary** above). Specifically:

- **Embed BEFORE commit.** Trigger vectors are computed before the blueprint row is inserted. If embedding fails completely, the row is never created.
- **Atomic store.** Trigger replacement uses `replace_triggers(id, items)` which is a single DELETE + INSERT in one transaction (already implemented in `repository.py:143-164`).
- **Rollback on partial failure.** If the blueprint is created but the trigger replace fails, the service calls `soft_delete(id)` to roll back (or restores the prior content via the most recent revision).
- **Empty `[]` clears explicitly.** `update_blueprint(trigger_queries=[])` calls `replace_triggers(id, [])` (empty list, no inserts, just the DELETE).
- **`None` is a no-op.** `update_blueprint(trigger_queries=None)` does not touch the trigger table at all.

The repository's `update()` extracts `reason` from kwargs before the setattr loop (see **Canonical Write Boundary → Repository changes** above).

## Dependencies

- **Depends on G4** (uses `manager._blueprint_embedding_service` and `_blueprint_embedding_repo`).
- **Depends on Canonical Write Boundary skeleton** (the publish unit lives in the service).
- **Does NOT depend on G2 or G3** — the publish unit is complete in isolation.

## Testing Approach (G1, rev 2)

**Unit tests** — `tests/services/test_blueprint_publish_unit.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_create_embeds_before_commit` | `embed_text` is called BEFORE `repo.create`; if embed fails, `repo.create` is not called |
| `test_create_rolls_back_on_trigger_storage_failure` | `replace_triggers` raises after `repo.create`; `soft_delete(id)` is called for the new blueprint |
| `test_create_aborts_on_all_embeddings_failed` | All `embed_text` calls raise; `repo.create` is not called; `BlueprintPublishError` raised |
| `test_create_partial_embeddings_succeeds` | Some `embed_text` calls succeed, some fail; `repo.create` IS called with the successful subset |
| `test_update_empty_triggers_clears` | `update_blueprint(trigger_queries=[])` calls `replace_triggers(id, [])` (empty) |
| `test_update_none_triggers_noop` | `update_blueprint(trigger_queries=None)` does NOT call `replace_triggers` |
| `test_update_with_triggers_replaces` | `update_blueprint(trigger_queries=[a,b])` calls `replace_triggers(id, [(a,vec),(b,vec)])` |
| `test_update_rolls_back_on_trigger_storage_failure` | After `repo.update` succeeds, `replace_triggers` raises; service restores prior content from `list_revisions` |
| `test_update_reason_passed_through` | `update_blueprint(reason="...")` calls `repo.update(reason="...", ...)`; revision row has the reason |

**Integration tests** — extend `tests/integration/test_blueprint_trigger_wiring.py`:

| Test | What to Assert |
|------|----------------|
| `test_atomic_publish_no_partial_state` | Simulate embedder failure (transient) → assert DB has either the full blueprint (with vectors) OR nothing — never a content-only row |
| `test_clear_triggers_via_update` | Create with 3 triggers → `update_blueprint(trigger_queries=[])` → 0 trigger rows |
| `test_no_op_update_preserves_triggers` | `update_blueprint(name="x")` (no trigger_queries) → trigger table unchanged |

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Embedding API calls slow down create/update | Medium | Each trigger query is a separate `embed_text` call (no batch endpoint). 3-10 queries = 3-10 API calls. Acceptable for a background maintenance agent. If latency is an issue, batch embedding can be added later (the `openai` SDK supports `input=[list]`). |
| Dimension mismatch between blueprint embeddings and query embeddings | Medium | Both use the same `SkillEmbeddingService.embed_text()` → same model → same dimensions. G4 ensures the model is correct. Add an assertion in the service that `len(vector) == config.embedding_dimensions` (soft check, warn on mismatch). |
| Rollback via `soft_delete` does not undo the row's UUID/id | Low | Acceptable: soft-deleted blueprints are invisible to the matcher (`is_active=False`). The id is never reused. |
| `replace_triggers(id, [])` issues a DELETE-only transaction; if the blueprint had no triggers, it's a no-op DELETE | Low | The DELETE is a no-op when no rows match; no harm. |
| `reason` field extracted in `update()` could mask a future API contract bug | Low | Unit test `test_update_reason_passed_through` asserts `reason` is threaded; if a future caller passes a different metadata field the test catches it. |

---

# G2 — Revision History Not Automatic (rev 2: `reason` extracted in code block)

## Root Cause Analysis

**What was intended:** Every time a blueprint's content changes (via `update()`), the repository captures a snapshot in `project_blueprint_revisions` via `add_revision()`. The frontend revision view reads this table to show change history.

**What shipped:** `BlueprintRepository.update()` (repository.py:73-89) increments the `version` counter when `content`, `file_refs`, `tags`, or `trigger_queries` change (line 79-80), but **never calls `add_revision()`. Rev 1 documented the fix in a footnote only; the actual code block did not extract `reason` from `fields` before the setattr loop, so any caller passing `reason="..."` would hit `ValueError: Unknown Blueprint field: reason`.

## Fix (rev 2)

The fix is the `update()` method in the **Canonical Write Boundary → Repository changes** section above. The relevant line:

```python
# Pop reason FIRST so it is not in ``fields`` when we setattr
fields.pop("reason", None)
```

This now lives in the actual code block (not a footnote). Everything else from rev 1 — post-commit capture, `add_revision` uses its own session, snapshot captures the new state, C8 compliance — is preserved.

## Files Touched (G2)

| File | Change |
|------|--------|
| `daemon/repositories/blueprint/repository.py` | `update()` extracts `reason` BEFORE the setattr loop; capture revision post-commit; integrate with the canonical service |

## Testing Approach (G2)

**Unit tests** — extend `tests/unit/test_blueprint_repository.py`:

| Test | What to Assert |
|------|----------------|
| `test_update_creates_revision_on_content_change` | `repo.update(id, content="new")` → 1 revision with `content_snapshot="new"`, `version=2` |
| `test_update_creates_revision_on_trigger_change` | `repo.update(id, trigger_queries=["q"])` → revision created |
| `test_update_no_revision_on_metadata_change` | `repo.update(id, name="x")` → no revision (name is not in the version-increment set) |
| `test_update_with_reason_does_not_raise` | `repo.update(id, content="x", reason="y")` → does NOT raise `ValueError: Unknown Blueprint field: reason`; revision has `reason="y"` |
| `test_update_with_empty_triggers_clears` | `repo.update(id, trigger_queries=[])` → version increments; revision captures the empty list |
| `test_multiple_updates_multiple_revisions` | 3 updates → 3 revisions ordered by version desc |
| `test_revision_failure_does_not_block_update` | Mock `add_revision` to raise → `update` still returns updated blueprint; revision table empty |
| `test_revision_captures_new_state` | Update content "old" → "new" → revision `content_snapshot == "new"` |

**Integration tests** — extend `tests/unit/test_blueprint_api.py` or `test_blueprint_tools.py`:

| Test | What to Assert |
|------|----------------|
| `test_blueprint_update_creates_revision_via_tool` | `blueprint_update(content="x", reason="y")` → revision row exists with `reason="y"` |

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Revision capture adds latency to every update | Low | One additional INSERT per version-incrementing update. Blueprint updates are infrequent (background agent). Negligible. |
| `add_revision` session conflicts with the still-open update session | Medium | Mitigated by design: revision capture happens AFTER `session.commit()` + `session.refresh()` in the update method, and `add_revision` opens its own fresh `Session`. No shared transaction. |
| Existing tests that call `update()` and assert only the blueprint return value | Low | `add_revision` is additive. Tests that also check the revision table need updating (and they should — that's the fix). |

---

# G3 — Rate Limiter Not Enforced (rev 2: enforced at canonical service)

## Root Cause Analysis

**What was intended:** The `BlueprintRateLimiter` caps revisions per hour per project (default 5/hour) and trips a circuit breaker after 3 consecutive failures (10-minute cooldown). The blueprinter agent checks `can_proceed()` before each write and calls `record_success()` / `record_failure()` after.

**What shipped:** The rate limiter is instantiated (manager.py:951) but its three methods have zero production callers. Rev 1 wired the limiter into the tools only, leaving the REST CRUD path completely unthrottled.

## Fix (rev 2)

The rate-limiter check is the **first line of every operation in `BlueprintWriteService`**. See **Canonical Write Boundary → Five invariants** above. The service exposes a `_check_rate_limit` private method that:

1. Reads the rate limiter from the manager.
2. Returns silently if the limiter is None (graceful degradation).
3. Calls `can_proceed(project_id)` inside a `try / except Exception` (C8 fail-open).
4. Raises `BlueprintRateLimitError` (a domain exception) if the limiter denies the write.

A second private method, `_record_rate_result(success: bool)`, calls `record_success` or `record_failure` (also inside `try / except Exception` for fail-open).

Both methods are called by every public operation (`create_blueprint`, `update_blueprint`, `disable_blueprint`, `execute_save_plan`).

## Files Touched (G3)

| File | Change |
|------|--------|
| `daemon/services/blueprint_write_service.py` (NEW) | `_check_rate_limit`, `_record_rate_result` private methods; called by every public operation |

## Dependencies

- **No dependencies on G1, G2, or G4.** G3 is entirely inside the canonical service.
- G3 is parallelizable with G2.

## Testing Approach (G3)

**Unit tests** — `tests/services/test_blueprint_rate_limit_wiring.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_create_rate_limited_aborts` | Limiter at capacity → `create_blueprint` raises `BlueprintRateLimitError`; `repo.create` is NOT called |
| `test_create_after_cooldown_succeeds` | Limiter tripped → advance time past cooldown → `create_blueprint` succeeds |
| `test_create_records_success` | Successful create → `record_success` called with the project_id |
| `test_create_records_failure` | `repo.create` raises → `record_failure` called |
| `test_create_fail_open_on_limiter_error` | `can_proceed` raises → `create_blueprint` proceeds; `record_success` called |
| `test_create_no_limiter` | `_blueprint_rate_limiter is None` → `create_blueprint` proceeds normally |
| `test_update_records_success_failure` | Same pattern as create |
| `test_disable_records_success_failure` | Same pattern as create |
| `test_rate_limit_fires_for_all_5_paths` | Drive 5 paths (REST create, REST update, REST delete, tool create, tool update) with limiter at capacity-1; all 5 succeed (5 ticks); 6th from any path fails with rate-limit error |

**Existing tests:** `tests/unit/test_blueprint_rate_limiter.py` already tests the limiter's internal logic. Those remain unchanged — the new tests verify the WIRING, not the limiter logic.

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Rate limiting too aggressive — blocks legitimate blueprint writes | Medium | Default is 5/hour. The blueprinter's save-plan (C9) handles this by counting writes before publication. Configurable via `BlueprintRateLimiter.__init__` params. |
| Fail-open behavior defeats the purpose if the limiter is consistently broken | Low | The limiter is a pure-Python in-memory dataclass with no external dependencies. The only failure mode is a programming bug, which tests catch. |
| Rate limiter state resets on daemon restart | Low | By design. Acceptable for a background maintenance agent. The Phase 3 durable lease will be the cross-restart coordination primitive. |

---

# Write Budget Management (NEW, addresses C9)

## Root Cause Analysis

**Problem:** A full rebuild creates one core + four areas = 5 writes. This consumes the **entire** default budget (5/hour). Additional areas, retries, or disable operations are blocked. Worse: rate-limited partial work would be reported as success → permanently partial rebuild.

**Example failure mode:** The blueprinter finishes creating the core + 3 of 4 areas (4 writes), then the 5th write hits the limiter. Without a save plan, the 4 successful writes are committed and the 5th is silently dropped. The next rebuild would also stop at 5. The corpus is permanently incomplete, and the user sees "build successful" with no error.

## Required behavior

1. **Calculate and reserve write budget BEFORE publication.** The blueprinter (or any caller) counts the operations it intends to perform (`create`, `update`, `disable`), checks the rate limiter's current budget, and reports whether all writes will fit.
2. **Persist the save plan** so it can resume after a cooldown or a daemon restart.
3. **Process bounded resumable batches** — write N blueprints, then if rate-limited, pause and schedule a coalesced continuation.
4. **Rate-limited partial work must NOT report as success.** The service returns a structured `SavePlanResult` with a clear `partial` status.
5. **Optional:** A "rebuild budget" that bypasses the per-write limiter in favor of the build lease (Phase 3) as the primary control. This is documented as Option B below.

## `SavePlan` and `WriteOp` (rev 2)

```python
# daemon/services/blueprint_save_plan.py

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone


@dataclass
class WriteOp:
    """One unit of work in a save plan."""
    op: str  # "create" | "update" | "disable"
    blueprint_id: str | None = None  # for update/disable
    payload: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    error: str | None = None
    completed_at: str | None = None


@dataclass
class SavePlan:
    """Persisted save plan for a single blueprinter run."""
    project_id: str
    run_id: str  # UUID; ties the plan to the JobItem
    mode: str  # "rebuild" | "incremental" | "manual"
    operations: list[WriteOp]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None
    total_operations: int = 0
    completed_operations: int = 0

    def __post_init__(self) -> None:
        if not self.total_operations:
            self.total_operations = len(self.operations)


@dataclass
class SavePlanResult:
    """Outcome of executing a SavePlan."""
    status: str  # "complete" | "partial_rate_limited" | "aborted" | "error"
    completed: int
    total: int
    rate_limited_at_index: int | None = None
    cooldown_resume_at: str | None = None  # when to retry
    save_plan: SavePlan | None = None
    message: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"
```

## `BlueprintWriteService.plan_publication` (NEW, rev 2)

```python
async def plan_publication(
    self,
    operations: list[WriteOp],
    mode: str = "manual",
) -> SavePlan:
    """Count the operations against the current rate-limit budget.

    Returns a SavePlan with a ``budget_status`` field. The caller
    inspects the plan; if budget is insufficient, the caller
    schedules a continuation job for after the cooldown.

    NOTE: This does NOT reserve budget. Reservation is implicit: when
    ``execute_save_plan`` actually performs writes, each successful
    write consumes one tick. The plan simply predicts whether all
    writes will fit and how many will be rate-limited.
    """
    with self.rate_limiter._lock:
        state = self.rate_limiter._state[self.project_id]
        # C8 fail-open: if state is unavailable, assume full budget
        if not state.revision_timestamps:
            current = 0
        else:
            cutoff = time.time() - 3600
            current = sum(1 for ts in state.revision_timestamps if ts > cutoff)
        budget = self.rate_limiter._max_per_hour - current

    save_plan = SavePlan(
        project_id=self.project_id,
        run_id=str(uuid.uuid4()),
        mode=mode,
        operations=operations,
    )
    # Annotate each operation with its planned index so we can resume
    for i, op in enumerate(save_plan.operations):
        op.planned_index = i  # type: ignore[attr-defined]
    save_plan.budget_available = budget  # type: ignore[attr-defined]
    save_plan.will_rate_limit_at = max(0, len(operations) - budget)  # type: ignore[attr-defined]
    return save_plan
```

## `BlueprintWriteService.execute_save_plan` (NEW, rev 2)

```python
async def execute_save_plan(
    self,
    save_plan: SavePlan,
    resume: bool = False,
) -> SavePlanResult:
    """Execute a SavePlan, persisting progress for resumability.

    On rate-limit hit:
      - Persist the SavePlan to project metadata (key: blueprint.save_plan.<run_id>).
      - Compute cooldown_resume_at = time.time() + rate_limiter._cooldown_seconds.
      - Return SavePlanResult(status="partial_rate_limited", ...).

    On all-complete:
      - Mark SavePlan.completed_at; remove from metadata.
      - Return SavePlanResult(status="complete", ...).

    C8 fail-open: any per-operation failure logs + continues; the
    SavePlan records the error in the WriteOp.

    Leader D4: The manager reference enables the future history hook
    (Phase 3) to call execute_save_plan when processing project_history
    events; we do not implement the hook in Phase 1, but the service
    is wired for it.
    """
    completed = 0
    if resume:
        # Skip operations already marked completed
        operations = [op for op in save_plan.operations if not op.completed]
    else:
        operations = list(save_plan.operations)

    for idx, op in enumerate(operations):
        try:
            await self._check_rate_limit()  # raises on rate-limit
        except BlueprintRateLimitError as e:
            # Persist progress and bail with partial result
            await self._persist_save_plan(save_plan)
            return SavePlanResult(
                status="partial_rate_limited",
                completed=completed,
                total=save_plan.total_operations,
                rate_limited_at_index=idx,
                cooldown_resume_at=(
                    datetime.fromtimestamp(
                        time.time() + self.rate_limiter._cooldown_seconds,
                        tz=timezone.utc,
                    ).isoformat()
                ),
                save_plan=save_plan,
                message=(
                    f"Rate-limited after {completed} of "
                    f"{save_plan.total_operations} writes. "
                    f"Will resume at {cooldown_resume_at}."
                ),
            )

        try:
            if op.op == "create":
                await self.create_blueprint(**op.payload)
            elif op.op == "update":
                await self.update_blueprint(**op.payload)
            elif op.op == "disable":
                await self.disable_blueprint(**op.payload)
            op.completed = True
            op.completed_at = _now_iso()
            completed += 1
            save_plan.completed_operations = completed
        except Exception as e:
            op.error = str(e)
            # C8: continue with the next op; record the failure
            logger.warning(
                "save plan op %d failed: %s", idx, e, exc_info=True
            )

    # All operations processed
    if all(op.completed for op in save_plan.operations):
        save_plan.completed_at = _now_iso()
        await self._clear_save_plan(save_plan)
        return SavePlanResult(
            status="complete",
            completed=completed,
            total=save_plan.total_operations,
            save_plan=save_plan,
            message=f"All {completed} writes completed.",
        )
    else:
        # Some failed (not rate-limited, just errored)
        await self._persist_save_plan(save_plan)
        return SavePlanResult(
            status="partial_rate_limited",
            completed=completed,
            total=save_plan.total_operations,
            save_plan=save_plan,
            message=(
                f"Partial: {completed} of "
                f"{save_plan.total_operations} writes succeeded; "
                f"errors recorded. Will not auto-resume."
            ),
        )
```

## Persistence helper (rev 2)

```python
async def _persist_save_plan(self, save_plan: SavePlan) -> None:
    """Persist the save plan to project metadata for resume."""
    key = f"blueprint.save_plan.{save_plan.run_id}"
    # Use the project metadata pattern (create_or_get_by_idempotency_key)
    # Leader D4: manager reference is available.
    await asyncio.to_thread(
        self.manager._project_repository.set_metadata,
        self.project_id, key, save_plan.__dict__,
    )

async def _clear_save_plan(self, save_plan: SavePlan) -> None:
    key = f"blueprint.save_plan.{save_plan.run_id}"
    await asyncio.to_thread(
        self.manager._project_repository.delete_metadata,
        self.project_id, key,
    )
```

## Option B — rebuild budget (deferred, recommended evaluation)

**Option B description:** During a `mode="rebuild"` save plan, bypass the per-write rate limiter in favor of the build lease (Phase 3 durable lease). The build lease is the cross-restart coordination primitive; if it holds the project, the build is authorized. The per-write limiter remains for `mode="manual"` and `mode="incremental"`.

**Status:** Option B is **described but NOT implemented in Phase 1.** Phase 1 implements the save plan with per-write rate limiting (Option A). Option B requires the build lease from Phase 3; implementing it in Phase 1 would couple Phase 1 to Phase 3.

**Evaluation criteria for Phase 3:** If the save-plan pause/resume cycle is cumbersome in practice, Phase 3 introduces Option B as part of the admission coordinator. The current rev-2 plan does not commit to Option B; it documents the design space.

## Files Touched (C9)

| File | Change |
|------|--------|
| `daemon/services/blueprint_save_plan.py` (NEW) | `SavePlan`, `WriteOp`, `SavePlanResult` dataclasses + persistence helpers |
| `daemon/services/blueprint_write_service.py` (NEW) | `plan_publication`, `execute_save_plan`, `_persist_save_plan`, `_clear_save_plan` |
| `daemon/manager.py` | `get_blueprint_write_service` factory (already added for C5) exposes the service for the blueprinter agent |

## Dependencies

- **Depends on G3** (the service uses the rate limiter).
- **Depends on Canonical Write Boundary skeleton** (the save plan executes through the service).
- **Independent of G1, G2** at the unit level — the save plan orchestrates them, but each is testable in isolation.

## Testing Approach (C9)

**Unit tests** — `tests/services/test_blueprint_save_plan.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_plan_publication_calculates_budget` | Limiter at 3/5 → plan with 5 ops → `will_rate_limit_at == 2` |
| `test_plan_publication_budget_sufficient` | Limiter at 0/5 → plan with 5 ops → `will_rate_limit_at == 0` |
| `test_execute_save_plan_complete` | Plan with 3 ops, limiter at 0/5 → all 3 succeed; `status == "complete"`; `_clear_save_plan` called |
| `test_execute_save_plan_partial_rate_limited` | Plan with 5 ops, limiter at 4/5 → 5 succeed (4th fills the 5th slot; 5th hits rate limit). Wait — 5 ops + 4 already used = limiter at 5 before op 1. So all 5 fail. Reset limiter to 0/5; plan with 7 ops; 5 succeed, 6th rate-limited → `status == "partial_rate_limited"`, `completed == 5`; SavePlan persisted to metadata |
| `test_execute_save_plan_resume` | Persist a save plan with 3 ops completed and 2 remaining; `execute_save_plan(resume=True)`; only 2 ops run; `status == "complete"` |
| `test_execute_save_plan_per_op_failure_logged` | One op raises; `status == "partial_rate_limited"`, `completed < total`; failed op has `error` field populated |
| `test_execute_save_plan_persists_on_rate_limit` | On rate-limit hit, the SavePlan is persisted to project metadata with `cooldown_resume_at` set |
| `test_save_plan_not_reported_as_success_on_partial` | When `status == "partial_rate_limited"`, the result's `is_complete` is False; caller code MUST treat this as NOT a successful build |

**Integration tests** — `tests/integration/test_blueprint_save_plan_e2e.py` (NEW):

| Test | What to Assert |
|------|----------------|
| `test_full_rebuild_with_save_plan` | Build a plan for core + 4 areas; execute; assert `status == "complete"` (with default budget of 5/hour, 5 writes fit exactly) |
| `test_rebuild_paused_at_rate_limit_resumes` | Build a plan for core + 6 areas; pre-fill limiter to 4/5; execute; assert 1 write completes, 6 rate-limited; persist; advance time past cooldown; resume; assert all complete |
| `test_save_plan_removed_on_complete` | After a complete execution, the metadata key is deleted |

## Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Save plan persistence adds a metadata write per rate-limit hit | Low | One DB write per rate-limit event (rare). Acceptable. |
| Save plan grows large for big rebuilds (10+ areas) | Low | One row per operation; 10 ops = 10 rows in metadata. Not a concern at this scale. |
| `execute_save_plan` could be called concurrently for the same `run_id` | Medium | Phase 3 admission coordinator will add a `run_id` lock. Phase 1's check is "best-effort" — concurrent calls with the same run_id are unlikely (the blueprinter is single-writer per job). Document this in a comment. |
| Save plan schema evolves — old plans in metadata may be unreadable | Low | On resume, validate each op's payload against the current schema; skip unparseable ops. |

---

# Cross-Cutting Concerns

## How the Fixes Interact (rev 2)

```
                ┌──────────────┐
                │      G4      │ Independent blueprint embedding
                │  (config +   │ service + repo. NOT coupled to
                │  embedding   │ skill_evolution anymore.
                │  service)    │
                └──────┬───────┘
                       │ _blueprint_embedding_service + repo
                       ▼
        ┌─────────────────────────────────────┐
        │  Canonical Write Boundary           │
        │  BlueprintWriteService (NEW)        │
        │  ┌───────────────────────────────┐  │
        │  │ 1. _check_rate_limit          │◀─┐
        │  │ 2. _embed_queries (BEFORE     │  │ G3
        │  │    commit — C4 fix 1)         │  │ invariants
        │  │ 3. _publish_unit (atomic)     │  │ enforced
        │  │ 4. _capture_revision (post-   │  │ here for
        │  │    commit)                    │  │ ALL 5 paths
        │  │ 5. _record_rate_result        │◀─┘
        │  └───────────────────────────────┘  │
        └────┬─────────────┬──────────┬───────┘
             │             │          │
             ▼             ▼          ▼
        create_blueprint  update  disable
        (G1 publish       (G1+G2)  (revision
        unit)                       capture)
                                ┌──────────────┐
                                │ Save Plan    │
                                │ (C9)         │
                                │ counts ops,  │
                                │ persists,    │
                                │ resumes,     │
                                │ never lies   │
                                └──────────────┘
```

## Five Write Paths (post-Phase-1 routing)

| # | Path | Routed via |
|---|------|------------|
| 1 | REST create | `router.create_blueprint` → `service.create_blueprint` |
| 2 | REST update | `router.update_blueprint` → `service.update_blueprint` |
| 3 | REST disable | `router.delete_blueprint` → `service.disable_blueprint` |
| 4 | Tool create | `blueprint_create` → `service.create_blueprint` |
| 5 | Tool update | `blueprint_update` → `service.update_blueprint` |

A grep test in CI enforces this routing:

```python
# tests/lint/test_no_direct_repo_writes.py
def test_no_direct_blueprint_writes():
    """All blueprint writes must route through BlueprintWriteService."""
    out = subprocess.check_output([
        "grep", "-rE",
        r"BlueprintRepository\.(create|update|soft_delete)",
        "daemon/", "--include=*.py",
    ], text=True)
    violations = [
        line for line in out.splitlines()
        if "blueprint_write_service" not in line
        and "test_" not in line
    ]
    assert not violations, f"Direct repo writes found: {violations}"
```

## Shared Code Surface

The `daemon/services/blueprint_write_service.py` is the single new code surface. The router and tools both depend on it; they no longer import `BlueprintRepository` for write operations (read operations like `get_by_id` are still OK — they don't mutate state).

## C8 Invariant Compliance

All fixes honor C8 (fire-and-forget errors logged + swallowed, use `except Exception` NOT `BaseException`):

| Fix | Error Path | C8 Compliance |
|-----|------------|----------------|
| G1 (embed-before-commit) | Embedding API failure → `BlueprintPublishError` raised (NOT fire-and-forget; this is a publish-unit error) | The publish unit treats embedding failure as a write-blocking error because partial state is unsafe. Other errors (per-query embed failures within a batch) are caught per-query. |
| G2 (revision capture) | `add_revision` failure → revision not recorded, update persists | `except Exception` after commit, logged, update returned |
| G3 (rate limiter) | `can_proceed` failure → fail-open, write proceeds | `except Exception` around limiter check, logged |
| G4 (embedding service) | Config issue → embedding service None, matcher None | Existing `if ... is not None` guard pattern |
| C5 (service) | Repository error → rate-limiter `record_failure` called; revision capture skipped | `except Exception` in service methods; operation raises a domain exception |
| C9 (save plan) | Per-op failure → `op.error` populated, next op proceeds | `except Exception` in `execute_save_plan`; save plan persisted with `partial_rate_limited` status |

## `except Exception` vs `except BaseException`

Per the project C8 invariant and the project-wide convention (see critical note: "BUG: `except BaseException: pass` ... Use `except Exception`"), ALL exception handlers in these fixes use `except Exception`. Never `BaseException` — it swallows `CancelledError` and breaks async cancellation.

---

# Phase 1 Exit Criteria (rev 2)

| # | Criterion | How to Measure | Pass Threshold |
|---|-----------|----------------|----------------|
| 1 | G4: Blueprint embedding service uses `BLUEPRINT_EMBEDDING_*` config and is independent of `skill_evolution` | Set `BLUEPRINT_EMBEDDING_MODEL=test-model`, set `skill_evolution=None`; inspect `manager._blueprint_embedding_service.config.embedding_model` AND `manager._blueprint_matcher` | Service exists with `embedding_model == "test-model"`; matcher exists; `skill_evolution` is None |
| 2 | G1: Create with `trigger_queries` populates `project_blueprint_triggers` atomically | Create blueprint with 3 triggers; query `project_blueprint_triggers` | Row count == 3, all `embedding` non-empty; if `embed_text` fails mid-batch, the blueprint is NOT created (verify with mock) |
| 3 | G1: Update with `trigger_queries=[]` clears all triggers | Create with 3 triggers; `update(trigger_queries=[])` | Row count == 0 |
| 4 | G1: Update with `trigger_queries=None` leaves triggers unchanged | Create with 3 triggers; `update(name="x")` | Row count == 3 |
| 5 | G1: Atomic publish — no partial state | Mock `embed_text` to fail for 2nd of 3 queries; assert `repo.create` is called with only the 1 successful embedding OR not called at all (depending on policy) — never with a partial row | Either 0 blueprints or 1 blueprint with 1 trigger; never a content-only row |
| 6 | G1: `update()` does NOT raise on `reason=...` | `repo.update(id, content="x", reason="y")` | Returns blueprint; no `ValueError`; revision has `reason="y"` |
| 7 | G2: `update()` with content change creates a revision row | `repo.update(id, content="new")` then `repo.list_revisions(id)` | 1 revision with `content_snapshot="new"`, `version=2` |
| 8 | G2: Revision failure doesn't block update | Mock `add_revision` to raise; `repo.update` | Returns updated blueprint; no exception raised |
| 9 | G3: Rate limiter blocks writes at capacity | Fill limiter to 5/5; call `service.create_blueprint` | Raises `BlueprintRateLimitError`; `repo.create` NOT called |
| 10 | G3: Rate limiter is enforced for ALL 5 write paths | Drive 5 paths; fill limiter to 4/5; run one write per path | 5 successful writes, 1 from each path; 6th from any path raises rate-limit error |
| 11 | C5: All 5 write paths route through `BlueprintWriteService` | `grep -rE "BlueprintRepository\.(create\|update\|soft_delete)" daemon/ --include="*.py" \| grep -v blueprint_write_service` | Zero matches (excluding the service itself and tests) |
| 12 | C5: REST CRUD calls the service | `daemon/routers/blueprints.py` does NOT import or call `BlueprintRepository.create/update/soft_delete` directly | Verified by inspection + integration test |
| 13 | C5: Tool calls the service | `daemon/tools/blueprint.py` `blueprint_create` / `blueprint_update` do NOT call `manager._blueprint_repo.create/update` directly | Verified by inspection + integration test |
| 14 | C9: Save plan counts writes before publication | Build a plan for 5 ops; limiter at 3/5; inspect `plan.will_rate_limit_at` | `will_rate_limit_at == 2` |
| 15 | C9: Rate-limited partial work reports `partial_rate_limited` | Build a plan for 7 ops; limiter at 5/5; execute; inspect `SavePlanResult.status` | `status == "partial_rate_limited"`, `completed == 0`, `rate_limited_at_index == 0` |
| 16 | C9: Save plan persists and resumes | Execute with rate limit hit; persist; advance time; resume | Final result `status == "complete"`; metadata key deleted |
| 17 | C9: Partial work is NOT reported as success | When `status == "partial_rate_limited"`, `SavePlanResult.is_complete` is False | `is_complete is False` |
| 18 | C8: All error paths use `except Exception` | `grep -rE "BaseException" daemon/services/blueprint_write_service.py daemon/tools/blueprint.py daemon/routers/blueprints.py` | Zero matches |
| 19 | Leader D1 hook: `processed_at` soft-delete referenced | Phase 2's `add_revision` uses `processed_at`; Phase 1's service does not block this | Phase 2 design (referenced) |
| 20 | Leader D3 hook: model tier config referenced | `agents/blueprinter/meta.json:decide_model_tier` documented in Write Budget section | Section present |
| 21 | Leader D4: Factory pattern matches `create_blueprint_tools(manager=...)` | `get_blueprint_write_service(manager=manager, project_id=...)` exists | Verified |
| 22 | Leader D5: Queue concurrency verified | Integration test: 5 concurrent `execute_save_plan` calls across 4 projects | Exactly one write per project at a time; no cross-project contention |
| 23 | All existing blueprint tests pass | `pytest tests/unit/test_blueprint_* tests/unit/test_blueprint_matcher.py` | 0 failures |

---

# Risks (Phase-Level, rev 2)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | Tool signature change (`trigger_queries` param) breaks the blueprinter agent's tool calls | Medium | Low | `trigger_queries` has default `None`; existing calls without it work. The blueprinter soul.md already instructs trigger generation. |
| R2 | Embedding latency slows down blueprint writes noticeably | Medium | Low | 3-10 sequential `embed_text` calls per write. Acceptable. If needed, batch embedding later. |
| R3 | Revision table grows unbounded over time | Low | Medium | Add archival/cleanup in Phase 6. At 5 revisions/hour max, ~44k rows/year. |
| R4 | Rate limiter state lost on restart causes a burst of writes | Low | Medium | By design. The save plan (C9) provides restart-safe recovery. |
| R5 | `BlueprintWriteService` complexity — five invariants in one service | Medium | Medium | Each invariant is a private method with its own unit test; mutation tests on the rate-limiter and revision-capture paths. The grep test (C5) prevents bypass. |
| R6 | Save plan schema evolution — old plans may be unreadable after field changes | Low | Medium | On resume, validate each op's payload; skip unparseable ops. Document the schema in a comment. |
| R7 | Option B (rebuild budget) is the right long-term design but is deferred to Phase 3 | Low | Low | Phase 1 implements Option A (per-write limiter). Phase 3 admission coordinator introduces Option B if the pause/resume cycle proves cumbersome. |
| R8 | Empty `trigger_queries=[]` semantics differ from "leave unchanged" | Low | Low | Explicitly documented in the service docstring; covered by `test_update_empty_triggers_clears` and `test_update_none_triggers_noop`. |
| R9 | `reason` field is a revision metadata, not a Blueprint field | Low | Low | Extracted before setattr in `update()`; covered by `test_update_with_reason_does_not_raise`. |
| R10 | `disable_blueprint` adds a revision row → revision table grows faster | Low | Low | Acceptable: revision view now shows lifecycle events. Add archival in Phase 6. |

---

# Open Questions (rev 2)

| # | Question | Needs Input From |
|---|----------|------------------|
| Q1 | Should `Option B` (rebuild budget bypass) be implemented in Phase 1 instead of Phase 3? | Planner |
| Q2 | Should the save-plan metadata key include the daemon's restart count for resumability across restarts? | Developer |
| Q3 | Should `disable_blueprint` skip revision capture if the blueprint is already disabled (idempotency)? | Developer |
| Q4 | Should the per-write limiter be configurable per `mode` (rebuild vs incremental vs manual)? | Planner / Developer |
| Q5 | Should the canonical service expose a synchronous facade for non-async callers (e.g., the existing router before full async migration)? | Developer |
