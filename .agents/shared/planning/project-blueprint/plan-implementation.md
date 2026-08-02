# Project Blueprint — Detailed Implementation Plan

**Status:** Draft
**Date:** 2026-08-02
**Author:** planner[v2] (synthesis of three plan-creation workers)
**Parent contract:** `plan-overview.md` (Final architecture — 14 locked decisions)
**Audience:** Implementers (developer/coder agents or humans)

> **This document is the master synthesis.** It specifies the execution order, dependency graph, cross-phase invariants, and the key implementation specs (schema outlines, signatures, fusion formula, integration points, resolved design gaps). **Exhaustive per-phase detail** (full SQLModel class definitions, complete Pydantic schemas, agent prompt text, pseudo-code blocks) lives in the three specialist files linked below. Implement each phase against its specialist file; use this document for sequencing, contracts, and cross-cutting concerns.

---

## Specialist Phase Files

| File | Scope | Lines |
|---|---|---|
| [`phase01-implementation.md`](phase01-implementation.md) | Phase 0 (Contract Spike) + Phase 1 (DB Schema + Matching Engine) | ~1284 |
| [`phase23-implementation.md`](phase23-implementation.md) | Phase 2 (Injection Integration) + Phase 3 (CRUD API + Tool Registration) | ~1402 |
| [`phase456-implementation.md`](phase456-implementation.md) | Phase 4 (Blueprinter Agent) + Phase 5 (Frontend UI) + Phase 6 (Evaluation & Tuning) | ~1100 |

---

## 1. Dependency Graph & Execution Order

```
Phase 0 (spike) ────validates────► Phase 1 (schema + matcher)
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
                   Phase 2 (injection)         Phase 3 (CRUD API + tools)
                   [INDEPENDENT — parallel]    [INDEPENDENT — parallel]
                          │                           │
                          └─────────────┬─────────────┘
                                        ▼
                                 Phase 4 (blueprinter)
                                 [needs Phase 1 + Phase 3 write tools]
                                        │
                                        ▼
                                 Phase 5 (frontend)
                                 [needs Phase 3 REST API]
                                        │
                                        ▼
                                 Phase 6 (evaluation)
                                 [needs Phase 2 + Phase 3 live data]
```

**Critical path:** Phase 0 → Phase 1 → (Phase 2 ∥ Phase 3) → Phase 4 → Phase 5 → Phase 6.

**Parallel opportunity:** Phases 2 and 3 are fully independent after Phase 1 ships and can be built in parallel. Phase 5 depends only on Phase 3's API contract (not its implementation), so a mock/stub of the REST endpoints can unblock Phase 5 earlier.

**Shared-file merge coordination (W6):** `daemon/manager.py` is edited by **both** Phase 1 (wires `_blueprint_repo` + `_blueprint_matcher`) and Phase 2 (consumption site reads `manager._blueprint_matcher` in `assemble_context_messages`). If Phases 1 and 2 are developed in parallel, **Phase 2's edits must land after Phase 1's wiring** — the `getattr(manager, "_blueprint_matcher", None)` lookup in §2a gracefully returns `None` before Phase 1 ships (no crash), but the feature is inert until Phase 1's manager init lands. Sequence the merge: Phase 1 manager.py changes first, then Phase 2 context_messages.py changes.

---

## 2. Cross-Phase Invariants

These contracts MUST hold across all phases. Violations create integration breaks.

| # | Invariant | Owner | Consumers |
|---|---|---|---|
| C1 | **Stable message IDs** — injected blueprint messages use `blueprint:{instance_id}:{slot}` (parallel to `auto_load:{iid}:{aid}` at `context_messages.py:598`) | Phase 2 | Checkpoint system, LangGraph `add_messages` reducer |
| C2 | **`CONTEXT_KIND_BLUEPRINT`** — single new context-kind constant in `context_messages.py`; all blueprint message construction routes through `_make_context_message` with it | Phase 2 | Phase 1 (matcher output format) |
| C3 | **`MatchedBlueprint` dataclass** — the contract between the matcher (Phase 1) and the message builder (Phase 2). Fields: `id, name, kind, version, content, file_refs, score`. Phase 2's `_render_blueprint_slot` is the single seam if Phase 1 field names change. | Phase 1 | Phase 2 |
| C4 | **`blueprint_inactive` field** — added to `AgentMetadata` (Pydantic) + `discover()` loader at **both** constructors (line 515 primary, line 561 retry-without-`llm_models`). True = opt-out. C6 silent-drop pattern avoided. | Phase 2 & Phase 4 (both document it; implement once) | Phase 2 (injection gate), Phase 4 (blueprinter self-guard) |
| C5 | **Opt-out is injection-only** — `blueprint_inactive` skips persistent-block injection. Read tools (`blueprint_get`, `blueprint_list`, `blueprint_search`) are ALWAYS available to all agents regardless of the flag. | Phase 2 | Phase 3 (tool auth) |
| C6 | **PostgreSQL-only storage** — all four tables use SQLModel `table=True`; embeddings are JSONB `list[float]` via `JSONBType` (NO pgvector, NO tsvector). Dual-driver (SQLite test + PostgreSQL prod) via repository pattern. | Phase 1 | All phases touching DB |
| C7 | **No new queue infrastructure** — blueprinter runs on existing `system_background_queue` (queue_type='background'). Dispatch via `JobQueueService.enqueue()` with resolved `queue_id`. | Phase 4 | — |
| C8 | **Fire-and-forget sidecar** — the post-experience trigger and any background dispatch follow the `experience()` pattern: errors logged + swallowed, never raise. `except Exception` (NOT `BaseException` — preserves `CancelledError`, per documented project bug). | Phase 4 | Phase 2 (async I/O in orchestrator) |

---

## 3. Resolved Design Gaps (from research, locked into plan)

Three points where the overview plan assumed something the codebase doesn't support. Each is resolved and documented inline in the relevant phase file.

### 3.1 `build_blueprint_query` signature (Phase 1 → Phase 2)

**Gap:** Overview §5.3.1 designed `build_blueprint_query(task_message, task_context, skill_content)`, but research confirmed `task_context` (Tier 2A `send_message` context param) and `skill_content` (dispatched skill body) are **NOT** threaded into `assemble_context_messages()` — only `user_query` is in scope at the injection hook point (`context_messages.py:1287-1289`).

**Resolution (Phase 2, §2.6):**
- **v1 = Option B:** ship `build_blueprint_query(query: str, context: str | None = None)` — `user_query`-only matching. This is explicitly permitted by overview §5.3 ("When enrichment signals are absent, only the message text is used"). `user_query` is the dominant matching signal.
- **Log `query_source="task_only"`** from v1 so a future Option-A deployment (`"task+context"` / `"task+context+skill"`) can A/B compare without re-instrumenting.
- **Enhancement path (Option A):** thread `task_context` as a new param into `assemble_context_messages` from its caller (`instance_messaging.py:~2975`). Deferred to post-Phase 6, behind a metrics-driven trigger — a permanent signature change is not justified before the A/B data exists.

> **Phase 1 ships the reduced signature; Phase 2 documents the gap + logging field. No refactor needed to extend later — only the query string richness changes, the matcher logic is identical.**

### 3.2 No scheduler/cron for daily scan (Phase 4)

**Gap:** Overview §7.2 specifies a "daily scan via scheduler tick," but research confirmed **no scheduler/cron/periodic-tick infrastructure exists** in the codebase.

**Resolution (Phase 4, §4.6):**
- **Option A (RECOMMENDED): metadata-based self-re-enqueue.** Blueprinter enqueues itself with a `metadata={"scheduled_for": <24h-later-ISO>}` timestamp. On each wake, if `scheduled_for` is in the future, no-op early exit. Idempotency key prevents duplicate future scans per project.
- **Option B (fallback): external cron** hits an admin endpoint `POST /admin/blueprints/scan`, which calls `JobQueueService.enqueue()` on `system_background_queue`.
- ⚠️ Option A costs one cheap LLM turn per premature check. If this proves wasteful in Phase 6, switch to Option B.

### 3.3 No generic rate-limit utility (Phase 4)

**Gap:** Overview §7.3 specifies a rate limiter + circuit breaker for blueprinter, but research confirmed **no generic rate-limiting/circuit-breaker utility exists** in the codebase (only ad-hoc: GII throttle, exponential backoff, semaphores).

**Resolution (Phase 4, §4.7):** Build a dedicated `BlueprintRateLimiter` in `daemon/services/blueprint_rate_limiter.py` — in-memory windowed counter (max N revisions/hour/project) + circuit breaker (3 consecutive failures → cooldown). Interface: `can_proceed(project_id) -> bool`, `record_success(project_id)`, `record_failure(project_id)`, `is_tripped(project_id)`.

---

## 4. Phase Summaries (key specs inline; see specialist files for full detail)

### Phase 0 — Contract Spike
**Objective:** Validate BM25 + vector fusion on 5–10 real queries before committing production schema. De-risks threshold (O1) and fusion weights (O2).

| Artifact | Path |
|---|---|
| Spike script | `scripts/blueprint_contract_spike.py` |
| Seed blueprints (3–5) | `scripts/blueprint_seed/{core,job-queue,context-injection,skill-system,db-repositories}.md` |
| Query set | `scripts/blueprint_seed/queries.json` |
| Results | `scripts/blueprint_seed/spike_results.md` |

**Procedure:** curate 3–5 blueprints for THIS project (agents-ensemble) → write 5–10 queries with expected-match annotations → grid-sweep α/β/threshold → measure top-1 accuracy, top-4 coverage, no-match rate, false-positive rate.

**Exit gate:** recall ≥ 80% top-1; threshold value chosen; α/β pair chosen.

**Key reuse:** the spike imports the REAL `_tokenize` + `_bm25_score` from `daemon/services/skill_search_service.py:96-185` and `SkillEmbeddingService.embed_text()` — it tests the matching logic against production embeddings, not a toy reimplementation.

> **Full detail:** [`phase01-implementation.md`](phase01-implementation.md) §PHASE 0.

---

### Phase 1 — DB Schema + Matching Engine
**Objective:** Persistent storage + production matching engine. The foundation every other phase depends on.

#### 1a. Database Schema — `daemon/repositories/blueprint/`

**3 tables** (the 4th conceptual table, `project_blueprint_tags`, is **resolved to inline JSONB** on `Blueprint` — dropping the separate tags table, citing the skill-system precedent in `daemon/repositories/skill/models.py` which has no tags table):

| Table | SQLModel class | Key columns |
|---|---|---|
| `project_blueprints` | `Blueprint` | `id`(str PK UUID), `project_id`(str FK), `name`(str), `kind`(str enum `core`\|`area`), `content`(str TEXT), `file_refs`(JSONB list[dict]), `tags`(JSONB list[str]), `trigger_queries`(JSONB list[str]), `version`(int default 1), `is_active`(bool default True), `source`(str enum `auto`\|`manual` default `auto`), `created_at`, `updated_at`. **Unique:** `(project_id, name)`. |
| `project_blueprint_embeddings` | `BlueprintEmbedding` | `id`, `blueprint_id`(FK), `trigger_query`(str), `embedding`(JSONB list[float] via `JSONBType`), `created_at`. One row per trigger query. |
| `project_blueprint_revisions` | `BlueprintRevision` | `id`, `blueprint_id`(FK), `version`(int), `content_snapshot`(str — full content at revision time), `file_refs`(JSONB), `tags`(JSONB), `trigger_queries`(JSONB), `change_source`(str enum `auto`\|`manual`\|`rollback`), `changed_at`(str). |

**Indexes:** `(project_id)` on blueprints; `(blueprint_id)` on embeddings + revisions; **unique** `(project_id, name)`; `(project_id, kind)` for core.md hot-path lookup.

**Registration:** import in `daemon/repositories/blueprint/__init__.py` → `SQLModel.metadata.create_all()` handles creation on SQLite + PostgreSQL. **NO `.sql` migration, NO `_ensure_postgres_columns`** (those are for new columns on *existing* tables only — `daemon/manager.py:3146-3221`).

> **Full SQLModel class definitions** (field types, `JSONBType` usage, `table=True`): [`phase01-implementation.md`](phase01-implementation.md) §P1.3.

#### 1b. Repository — `daemon/repositories/blueprint/repository.py`

```python
class BlueprintRepository:
    def __init__(self, engine: Engine): ...
    # CRUD
    def get_by_id(self, blueprint_id: str) -> Blueprint | None
    def get_by_name(self, project_id: str, name: str) -> Blueprint | None
    def get_core(self, project_id: str) -> Blueprint | None           # hot path
    def list_by_project(self, project_id, kind=None, active_only=True) -> list[Blueprint]
    def create(self, ...) -> Blueprint
    def update(self, blueprint_id, ...) -> Blueprint                   # bumps version
    def soft_delete(self, blueprint_id) -> None                        # is_active=False
    # Embeddings
    def get_embeddings(self, blueprint_id) -> list[BlueprintEmbedding]
    def add_embedding(self, blueprint_id, trigger_query, embedding) -> None
    def replace_embeddings(self, blueprint_id, items: list[tuple[str, list[float]]]) -> None  # delete-all + insert
    # Matching
    def search_candidates(self, project_id: str) -> list[tuple[Blueprint, list[BlueprintEmbedding]]]
    # Revisions
    def add_revision(self, blueprint_id, ...) -> BlueprintRevision
    def list_revisions(self, blueprint_id, limit=50, offset=0) -> list[BlueprintRevision]
```

**Wiring:** `manager._blueprint_repo = BlueprintRepository(engine=self._engine)` in `daemon/manager.py` — parallel to `InstanceUiPrefsRepository` (line 557-563).

> **Full method implementations:** [`phase01-implementation.md`](phase01-implementation.md) §P1.4.

#### 1c. Matching Engine — `daemon/services/blueprint_matcher.py`

**Data class (contract C3):**
```python
@dataclass
class MatchedBlueprint:
    id: str; name: str; kind: str; version: int
    content: str; file_refs: list[dict]; score: float
```

**Main entry point:**
```python
class BlueprintMatcher:
    def __init__(self, repository: BlueprintRepository,
                 embedding_service: SkillEmbeddingService,
                 config: BlueprintConfig): ...

    async def match(self, project_id: str, query: str,
                    max_area: int = 4, threshold: float | None = None
    ) -> list[MatchedBlueprint]:
        # 1. core.md → reserved slot 1 (always included if exists)
        # 2. area candidates → BM25 + vector fusion → threshold gate → top-4
        # 3. structured log: blueprint_match event
```

**Score fusion formula:**
```
final = α · normalize_min_max(bm25_score) + β · vector_score
```
- BM25 normalized via min-max across the candidate set (maps to [0,1]).
- Vector score = max cosine similarity over the blueprint's trigger-query embeddings.
- `α + β = 1` (justified: a single hyperparameter controls the BM25/vector tradeoff; Phase 0 seeds default, e.g. α=0.4, β=0.6).
- Threshold gate: candidates with `final < threshold` dropped. At most `max_area=4` returned.

**BM25:** pure-Python, reuses `_tokenize` + `_bm25_score` imported from `daemon/services/skill_search_service.py` (module-level functions, importable directly). Corpus = blueprint content + concatenated trigger_queries per blueprint.

**Vector matching:** query embedded via `SkillEmbeddingService.embed_text(query)`; cosine similarity against stored `BlueprintEmbedding.embedding` rows; aggregate per-blueprint by **max** over its trigger-query scores.

**Trigger-query generation** (`daemon/services/blueprint_query.py`):
```python
async def generate_trigger_queries(content: str, llm_service) -> list[str]:
    # LLM generates 3-10 example natural-language queries that should match this blueprint
    # Invoked at create/update time (Phase 1) by the repository/service layer
    # Defensive parsing: fenced-JSON or bulleted list fallback
```

**Embedding recompute on trigger-query change:** when `trigger_queries` change, recompute embeddings for all of them via `embed_text()`, then `repository.replace_embeddings()` (delete old + insert new).

**Structured logging** — `blueprint_match` event:
```
{event: "blueprint_match", project_id, query_source: "task_only",
 query_hash, candidate_count, matched_count, top_scores: [...],
 threshold, had_core: true, latency_ms}
```

**Config** — `BlueprintConfig` section in `daemon/config.py`:
```python
class BlueprintConfig:
    enabled: bool = True
    alpha: float = 0.4          # BM25 weight (Phase-0-seeded)
    beta: float = 0.6           # vector weight
    threshold: float = 0.30     # Phase-0-seeded; recalibrated Phase 6
    max_area_slots: int = 4
    max_trigger_queries: int = 10
```

> **Full matcher implementation, prompt template, config, wiring:** [`phase01-implementation.md`](phase01-implementation.md) §P1.5–P1.8.

---

### Phase 2 — Injection Integration
**Objective:** Wire blueprint into `assemble_context_messages()` persistent block. Match at first-message receipt, freeze for instance lifetime.

**Touch surface:** `daemon/services/context_messages.py` (insertion point), `daemon/registry.py` (AgentMetadata field), `daemon/manager.py` (matcher attribute wiring).

#### 2a. Integration point — `assemble_context_messages()`

File: `daemon/services/context_messages.py`, function at lines 1052-1376. Persistent block order: (1) project context 1239-1254, (2) shared context RAG 1256-1287, **(3) ← BLUEPRINT INSERTS HERE →**, (4) auto-load skills 1289-1328, (5) BM25 skills 1330-1374.

**New code block (between line 1287 and 1289):**
```python
# --- Blueprint persistent injection (match-once) ---
if not project_already_injected:                      # match-once gate
    blueprint_inactive = bool(getattr(agent_meta, "blueprint_inactive", False))
    if not blueprint_inactive:
        try:
            matcher = manager._blueprint_matcher      # None if disabled
            if matcher is not None:
                matched = await matcher.match(project_id=project_id, query=user_query)
                blueprint_msgs = build_blueprint_message(matched, instance_id)
                persistent_msgs.extend(blueprint_msgs)
        except Exception:                             # NOT BaseException (C8)
            logger.warning("blueprint injection failed", exc_info=True)
```

> ⚠️ `assemble_context_messages()` is already async, so `matcher.match()` is awaited **directly** — no `asyncio.to_thread` wrapper around the call. `BlueprintMatcher.match()` is an `async def`; any sync DB calls inside it are individually wrapped in `asyncio.to_thread(...)` internally. Wrapping the await in `asyncio.to_thread(lambda: asyncio.run(...))` would create a **nested event loop** and crash.

#### 2b. Message builder — `build_blueprint_message`

```python
def build_blueprint_message(matched: list[MatchedBlueprint], instance_id: str
                            ) -> list[HumanMessage]:
    # 5-slot allocation: slot 1 = core (always if present), slots 2-5 = area by score
    # Each slot → one HumanMessage with stable ID `blueprint:{instance_id}:{slot}`
    # Format per overview §6.3:
    #   header [BLUEPRINT core|matched], name+version, markdown body, file refs,
    #   footer "Source: blueprint:{name} v{version} | lineage:{core|matched}"
    # Routes through _make_context_message(kind=CONTEXT_KIND_BLUEPRINT, ...)
```

#### 2c. Match-once gate (decision)

**Piggyback on `project_already_injected`** (the existing `project_injected` instance-metadata flag, set at `instance_messaging.py:2337` after successful injection). **Zero new DB writes.** Blueprint is part of the persistent block assembled once; it naturally rides the same gate. On turn 2+, `assemble_context_messages` returns `([],[])` early (line 1188) — blueprint is never re-matched. The stable message IDs ensure checkpointed messages persist correctly.

> **Alternative considered:** separate `blueprint_injected` flag. Rejected — adds a DB write for no benefit since blueprint is always part of the one-shot persistent block.

#### 2d. `build_blueprint_query` gap resolution

See §3.1 above. v1 = `user_query`-only (Option B); log `query_source="task_only"`.

> **Full builder pseudo-code, opt-out flow, async contract, 6 e2e tests:** [`phase23-implementation.md`](phase23-implementation.md) §Phase 2.

---

### Phase 3 — CRUD API + Tool Registration
**Objective:** REST API for blueprint management + agent-callable tools.

#### 3a. REST API — `daemon/routers/blueprints.py`

```python
router = APIRouter(prefix="/api/projects/{project_id}/blueprints")
# Mounted in daemon/api.py via api_router.include_router(blueprints_router)
```

| Method | Path | Request → Response | Notes |
|---|---|---|---|
| `GET` | `/` | `?kind=&active_only=&search=&limit=&offset=` → `BlueprintListResponse` | List (lightweight items) |
| `GET` | `/{blueprint_id}` | → `BlueprintDetailResponse` | Full content + file_refs + tags + trigger_queries |
| `POST` | `/` | `BlueprintCreateRequest` → `BlueprintDetailResponse` | Server generates trigger_queries + embeddings |
| `PUT` | `/{blueprint_id}` | `BlueprintUpdateRequest` → `BlueprintDetailResponse` | Writes revision row, `source=manual`, bumps version |
| `DELETE` | `/{blueprint_id}` | → `204` | Soft-delete (`is_active=False`) |
| `GET` | `/{blueprint_id}/revisions` | `?limit=&offset=` → `BlueprintRevisionListResponse` | Paginated history |

**Pydantic schemas** (in `daemon/routers/schemas.py`): `BlueprintListItem`, `BlueprintDetailResponse`, `BlueprintCreateRequest`, `BlueprintUpdateRequest`, `BlueprintRevisionResponse`, `BlueprintListResponse`, `BlueprintRevisionListResponse`, plus `FileRef` helper.

**Project-scoping:** all queries filter by `project_id` from the path. No central auth; per-endpoint repository queries.

> **Full schemas (field-level), router code, mount wiring:** [`phase23-implementation.md`](phase23-implementation.md) §3.2–3.5.

#### 3b. Agent Tools — `daemon/tools/blueprint.py`

```python
def create_blueprint_tools(manager, current_instance_id, agent_id="", version_tag=None
                           ) -> list[StructuredTool]:
    # 5 tools, each: @register_tool_category("blueprint") + @tool(args_schema=...)
    # Context via closure (manager, current_instance_id) — NOT explicit args
    # blueprint_get(name)      → str (markdown)         [read — unrestricted]
    # blueprint_list(project_id?) → str                 [read — unrestricted]
    # blueprint_search(query, limit?) → str             [read — unrestricted]
    # blueprint_create(...)    → dict                   [write — runtime auth check]
    # blueprint_update(...)    → dict                   [write — runtime auth check]
```

**Write authorization:** runtime check `_is_writer_authorized(agent_id) -> bool` — returns True only for `agent_id == "blueprinter"`. HTTP write auth (user-mediated) is the REST API's responsibility.

#### 3c. 3-Step Tool Registration (the canonical path — all three required)

1. **`@register_tool_category("blueprint")`** decorator on each tool function (`daemon/tools/_tool_registry.py:18-43`).
2. **`"blueprint": "daemon.tools.blueprint"`** added to `CATEGORY_MODULES` dict (`daemon/tools/_tool_registry.py:232-249`).
3. **Tools added to the `tools = [...]` list** inside `create_instance_tools()` (`daemon/tools/instance.py`). ⚠️ **Decorators alone only stamp metadata — omission from the list = dead code.**

> **Full tool implementations, auth model, registration steps, 6 tests:** [`phase23-implementation.md`](phase23-implementation.md) §3.5–3.8.

---

### Phase 4 — Blueprinter Agent
**Objective:** Automatic blueprint maintenance on `system_background_queue`.

#### 4a. Agent definition — `agents/blueprinter/`

Files (follow `docs/agent-prompt-writing-guide.md` convention): `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md` (recommended).

**`meta.json` key fields:**
| Field | Value | Rationale |
|---|---|---|
| `id` | `"blueprinter"` | |
| `tools.allow` | `["blueprint", "knowledge", "filesystem", "time", "self", "help"]` | `"blueprint"` category includes read + write tools; `knowledge`/`filesystem` for drift detection |
| `blueprint_inactive` | `true` | Self-referential guard (overview §7.3) — blueprinter generates, doesn't consume |
| `team_members` | `[]` | Works alone |
| `llm_model` | configured evolution model | Parallel to skill-keeper pattern |

**`soul.md` / `rule.md` / `workflow.md`:** identity = "I maintain the blueprint corpus." Workflow = receive drift signal → gather candidate facts (recent experience entries, file structure changes) → decide no-op/create/update/disable → for each action generate content (200-500 words) + trigger queries + recompute embeddings → call `blueprint_create`/`blueprint_update` tools → respect rate limit. **core.md highest priority.** No approval flow.

#### 4b. Post-experience trigger — sidecar in `experience()`

File: `daemon/tools/knowledge_tools.py`, function `_enqueue_experience_job()` (~line 341-403). **Insert AFTER** the `await job_service.enqueue()` call (~line 403):

```python
# --- Blueprinter post-experience sidecar (fire-and-forget) ---
try:
    BLUEPRINT_KEYWORDS = ["architecture", "pattern", "module", "service",
        "directory structure", "entry point", "lifecycle", "protocol",
        "schema", "migration", "queue", "repository", "embedding"]
    text_lower = text.lower()
    if any(kw in text_lower for kw in BLUEPRINT_KEYWORDS):
        bg_queue = await queue_repo.get_by_name(project_id, "system_background_queue")
        if bg_queue:
            await job_service.enqueue(
                agent_id="blueprinter",
                message=f"Drift signal — review blueprints for: {text[:500]}",
                project_id=project_id,
                queue_id=bg_queue.queue_id,
                priority=0,
                metadata={"trigger": "post-experience", "source_text_preview": text[:100]},
            )
except Exception:                              # NOT BaseException (C8)
    logger.warning("blueprinter sidecar enqueue failed", exc_info=True)
```

#### 4c. Daily scan — self-re-enqueue (see §3.2)

Blueprinter enqueues itself with `metadata={"scheduled_for": <24h-later-ISO>}`. On wake, if `scheduled_for` is future, no-op exit. Idempotency key prevents duplicates.

#### 4d. Rate limiter — `daemon/services/blueprint_rate_limiter.py`

```python
class BlueprintRateLimiter:
    def can_proceed(self, project_id: str) -> bool       # windowed: <N revisions/hour
    def record_success(self, project_id: str) -> None
    def record_failure(self, project_id: str) -> None
    def is_tripped(self, project_id: str) -> bool        # circuit breaker: 3 fails → cooldown
```

Called in blueprinter workflow before each write action.

#### 4e. core.md logic

Blueprinter checks core.md first; if drift detected anywhere, review core before area blueprints. No self-referential core edits. Manual edits (`source=manual`) preserved unless blueprinter regenerates with higher confidence.

> **Full agent prompts, sidecar pseudo-code, rate-limiter implementation, daily-scan detail:** [`phase456-implementation.md`](phase456-implementation.md) §4.

---

### Phase 5 — Frontend UI
**Objective:** Per-project blueprint management panel.

**Stack (confirmed from codebase):** Angular 21 + Angular Material + CodeMirror 6 + ngx-markdown. (**NOT React** — worker corrected the task brief's assumption.)

**File additions** (in `frontend/src/`):
| File | Responsibility |
|---|---|
| `app/core/models/blueprint.model.ts` | TypeScript interfaces (`Blueprint`, `BlueprintRevision`, etc.) |
| `app/core/services/blueprint.service.ts` | CRUD + revision history (follows `SkillService` signal pattern) |
| `app/features/blueprints/blueprints.component.ts` | Page container |
| `app/features/blueprints/blueprint-list.component.ts` | List view |
| `app/features/blueprints/blueprint-detail.component.ts` | Detail view |
| `app/features/blueprints/blueprint-editor.component.ts` | Markdown editor (CodeMirror 6) + live preview (ngx-markdown) |
| `app/features/blueprints/blueprint-tag-editor.component.ts` | Tag chip editor |
| `app/features/blueprints/revision-history.component.ts` | Revision list + diff |
| `app/features/blueprints/create-blueprint-form.component.ts` | Create form |

**Routing:** `loadComponent` in `app.routes.ts`, per-project scoping `projects/:projectId/blueprints/...`.

**State management:** component-local Angular signals (no store — matches existing pattern).

> **Full component specs, routing, model types, service signatures:** [`phase456-implementation.md`](phase456-implementation.md) §5.

---

### Phase 6 — Evaluation & Tuning
**Objective:** Calibrate thresholds, weights, and rates from production behavior.

| Task | Method | Deliverable |
|---|---|---|
| Threshold calibration | Elbow detection on `blueprint_match` log score distributions | `scripts/blueprint_threshold_analysis.py` |
| No-match rate analysis | Count matches where only core.md injected (no area) | Decision tree: lower threshold or improve trigger queries |
| Fusion weight tuning (α/β) | Grid search / bandit over historical matches | Updated `BlueprintConfig` defaults |
| Trigger-query quality audit | Sample blueprints, check if trigger queries match real tasks | Regenerate low-quality trigger queries |
| Rate limit calibration | Observe drift frequency in production | Tune N revisions/hour |
| LLM rerank fallback | **Adopt ONLY if** BM25+vector recall < 80% after tuning | Decision gate (deferred, not default) |

**Metrics table (9 metrics):** no-match rate, top-1 accuracy (if labeled), top-4 coverage, blueprint revision quality (manual sample), token budget per first-turn (5-slot cap impact), query-source effectiveness, fusion-weight sensitivity, trigger-query match rate, blueprinter revision frequency.

> **Full calibration scripts, decision trees, metrics table:** [`phase456-implementation.md`](phase456-implementation.md) §6.

---

## 5. Complete File Manifest

All new files and edits across all phases. **~2800 LOC, all additive** (no existing logic removed).

### New files
| Path | Phase |
|---|---|
| `daemon/repositories/blueprint/__init__.py` | 1 |
| `daemon/repositories/blueprint/models.py` | 1 |
| `daemon/repositories/blueprint/repository.py` | 1 |
| `daemon/services/blueprint_matcher.py` | 1 |
| `daemon/services/blueprint_query.py` | 1 |
| `daemon/services/blueprint_rate_limiter.py` | 4 |
| `daemon/routers/blueprints.py` | 3 |
| `daemon/tools/blueprint.py` | 3 |
| `agents/blueprinter/meta.json` | 4 |
| `agents/blueprinter/soul.md` | 4 |
| `agents/blueprinter/rule.md` | 4 |
| `agents/blueprinter/workflow.md` | 4 |
| `agents/blueprinter/tools_note.md` | 4 |
| `scripts/blueprint_contract_spike.py` | 0 |
| `scripts/blueprint_seed/*.md` + `queries.json` | 0 |
| `frontend/src/app/core/models/blueprint.model.ts` | 5 |
| `frontend/src/app/core/services/blueprint.service.ts` | 5 |
| `frontend/src/app/features/blueprints/*.component.ts` (7 files) | 5 |

### Edits to existing files
| Path | Phase | Change |
|---|---|---|
| `daemon/repositories/__init__.py` | 1 | Import blueprint models (registers with SQLModel.metadata) |
| `daemon/config.py` | 1 | Add `BlueprintConfig` section |
| `daemon/manager.py` | 1 | Wire `_blueprint_repo` + `_blueprint_matcher` (parallel to InstanceUiPrefsRepository:557, skill services:891) |
| `daemon/services/context_messages.py` | 2 | Insert blueprint block between lines 1287-1289; add `CONTEXT_KIND_BLUEPRINT` constant; add `build_blueprint_message()` |
| `daemon/registry.py` | 2 | Add `blueprint_inactive: bool = False` to `AgentMetadata` + loader in `discover()` at **both** constructors (515, 561) |
| `daemon/tools/_tool_registry.py` | 3 | Add `"blueprint": "daemon.tools.blueprint"` to `CATEGORY_MODULES` (line 232-249) |
| `daemon/tools/instance.py` | 3 | Add blueprint tools to `tools = [...]` list in `create_instance_tools()` |
| `daemon/api.py` | 3 | Import + `api_router.include_router(blueprints_router)` (line 1354-1373) |
| `daemon/routers/schemas.py` | 3 | Add blueprint Pydantic schemas |
| `daemon/tools/knowledge_tools.py` | 4 | Insert sidecar enqueue after `_enqueue_experience_job()` enqueue (~line 403) |
| `frontend/src/app/app.routes.ts` | 5 | Add blueprint `loadComponent` routes |
| `agents/developer/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |
| `agents/tester/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |
| `agents/explorer/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |
| `agents/wanderer/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |
| `agents/planner/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |
| `agents/reviewer/meta.json` | 3 | Append `"blueprint"` to `tools.allow` |

---

## 6. Key Risks (cross-phase)

| Risk | Phase | Mitigation |
|---|---|---|
| Token budget pressure (5 blueprints ≈ 3000-3500 tokens in persistent block) | 2 | Threshold gate keeps low-quality matches out; per-blueprint char cap (2K); 5-slot hard ceiling; monitor via Phase 6 metrics |
| Worker reuse staleness (blueprint frozen for instance lifetime; reused worker gets stale match) | 2 | Document task-affine reuse invariant; not mechanically enforced at this scope |
| Self-re-enqueue premature-wake cost (daily scan Option A costs 1 LLM turn per early wake) | 4 | Switch to Option B (external cron) if wasteful in Phase 6 |
| `source=manual` thrash (blueprinter overwrites manual edits) | 4 | Higher confidence threshold for manual blueprints; revision history for rollback |
| Threshold calibration drift (Phase 0 seeds may not hold in production) | 6 | Phase 6 recalibration; structured logging from v1 enables data-driven tuning |
| `total` field in `BlueprintListResponse` reflects page length in v1, not true DB count | 3 | Documented; upgrade to true count post-Phase 3 |

---

## 7. Research Credits

This plan was synthesized from three parallel codebase investigations:
- **Injection pipeline research** — `assemble_context_messages()` structure, skill-injection pattern, first-message gate, the `build_blueprint_query` scope gap (explorer instance).
- **DB/tools/router research** — dual-driver repository pattern, SQLModel conventions, `JSONBType`, no-pgvector finding, tool registry 3-step path, FastAPI router conventions (explorer instance).
- **Agent/queue/experience research** — agent dir structure, `AgentMetadata` C6 pattern, `system_background_queue` dispatch, no-scheduler finding, `experience()` sidecar hook point, no-rate-limiter finding (explorer instance).

All three research reports are reflected in the specialist phase files and the resolved design gaps (§3).
