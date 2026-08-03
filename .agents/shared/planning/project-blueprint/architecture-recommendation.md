# Architecture Recommendation: Project Blueprint Evolution

**Date:** 2026-08-03  
**Architect Instance:** architect (via 3-worker competitive fan-out)  
**Worker Reports:** data-flow-design (Areas A+E), system-decomposition (Areas B+D), resilience-design (Areas C+F)  
**Status:** Complete  
**Scope:** 4 critical wiring gaps (G1-G4), pending queue design (B), two-workflow modes (C), blueprinter skill set & workers (D), matching hardening (G6-G8), daily scan scheduler (F)

---

## Executive Summary

The Project Blueprint subsystem has one 🔴 **critical blocker** (G1: trigger generation not wired — vector matching is dead for all tool-created blueprints) and ten additional gaps spanning wiring, evolution architecture, and operational resilience. All are fixable within existing codebase patterns — no new infrastructure, no new dependencies. The recommended approach leverages the job queue's existing idempotency mechanism, `MaintenanceService` for scheduling, SQLModel patterns for the pending queue, and the `SkillEmbeddingService` pattern for trigger embeddings.

The evolution architecture (pending queue + two-workflow + skill-driven multi-worker) is sound as designed in `blueprinter-evolution.md`. The architecture recommendation refines specific implementation choices and flags four risks that need leader decisions before implementation.

---

## Area A — Critical Wiring Gaps (G1-G4)

### G1 — Trigger Generation Not Wired 🔴 BLOCKER

**Recommendation: G1(a) — Add `trigger_queries: list[str]` parameter to `blueprint_create` and `blueprint_update` tools; compute embeddings server-side.**

The blueprinter already generates 3-10 trigger queries in its workflow (soul.md:17, workflow.md:89). The LLM cost is already paid. Adding a tool parameter and computing embeddings server-side via `SkillEmbeddingService.embed_text()` is the smallest delta that closes the gap. The implementation follows the exact pattern of `update_skill_embeddings()` (skill_embedding_service.py:320-405): per-query `embed_text` wrapped in `asyncio.to_thread`, per-row failure logged + skipped.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **G1(a)** Add trigger_queries to tools | Low | High | High | Low | Low | ✅ **Recommended** |
| G1(b) Auto-generate server-side | Medium | Medium | Medium | Medium | High (duplicate LLM call) | Rejected — duplicated cost, extra failure surface |
| G1(c) Separate blueprint_set_triggers tool | Medium | Medium | Low (sync race window) | Medium | Low | Rejected — more tool surface, two round-trips |

**Key decisions:**
- On `blueprint_create`: use `repo.add_triggers()` (table is empty for new blueprints)
- On `blueprint_update`: use `repo.replace_triggers()` (delete-all + insert-all — triggers are a complete-set concept)
- Embedding failures are fire-and-forget (matcher degrades to BM25-only gracefully — already designed)
- Dual-write (Blueprint row + BlueprintTrigger rows) NOT in one transaction — matches `update_skill_embeddings` pattern

**Risk:** 🟡 Embedding API failure leaves BM25-only matching. Acceptable — the matcher already handles `query_emb = None`.

---

### G2 — Revision History Not Automatic 🟡

**Recommendation: Auto-capture revision inside `BlueprintRepository.update()` after the row commits.**

The version-bump site (repository.py:79-80) is the natural seam — same session, no extra transaction. After `session.refresh(blueprint)`, read the committed state and write a `BlueprintRevision` snapshot. Wrap in `try/except` and log+swallow failures so a revision-write error never rolls back a content update.

| Approach | Complexity | Maintainability | Risk | Recommendation |
|----------|------------|-----------------|------|----------------|
| Auto-capture in repo `update()` | Low | High (guaranteed coverage) | Low (revision gap on failure) | ✅ **Recommended** |
| Explicit in tools layer | Low | Low (easy to miss on refactor) | Medium | Rejected — caller-dependent |

**Implementation:** Pass `version`, `content_snapshot`, `tags`, `file_refs`, `trigger_queries`, `source`, optional `reason` from the update fields dict. If `add_revision` fails, the update stands — version increments but no snapshot exists (detectable gap, not corruption).

---

### G3 — Rate Limiter Not Enforced 🟡

**Recommendation: Tools-layer gate in `blueprint_create` and `blueprint_update`.**

The rate limiter is a write-time concern. Tools are the only ingress for the blueprinter agent. Route-layer enforcement would miss in-process tool calls; repo-layer enforcement would block legitimate admin paths.

| Approach | Complexity | Coverage | Risk | Recommendation |
|----------|------------|----------|------|----------------|
| Tools-layer gate | Low | Agent writes only | Low | ✅ **Recommended** |
| Repository-layer | Low | All writes (incl. admin) | Medium (blocks admin imports) | Rejected — too broad |
| API route-layer | Low | HTTP only | High (misses tool calls) | Rejected — bypassed |

**Implementation:** `limiter.can_proceed(pid)` BEFORE `repo.create/update`; call `limiter.record_success(pid)` or `record_failure(pid)` after the write. **Fail-open on limiter exception** — a broken limiter never blocks a write.

**Risk:** 🟡 **Thread safety:** `BlueprintRateLimiter` uses `threading.Lock` but tools are async. If the blueprinter spawns concurrent workers in Phase 2 evolution, the lock may not serialize correctly. Verify same-thread execution or switch to `asyncio.Lock`.

---

### G4 — Embedding Config Disconnected 🟡

**Recommendation: Separate `SkillEmbeddingService` instance bound to `BlueprintConfig`.**

`BlueprintConfig` inherits `EmbeddingConfig` with prefix `BLUEPRINT_`. The `_shared_embedding_fallback` validator (config.py:526-592) already implements precedence: `BLUEPRINT_EMBEDDING_*` → `EMBEDDING_*` → field defaults. The matcher should receive `self._blueprint_embedding_service` (constructed from `self.config.blueprint`), not `self._skill_embedding_service`.

| Approach | Complexity | Flexibility | Risk | Recommendation |
|----------|------------|-------------|------|----------------|
| Separate service instance from BlueprintConfig | Low | High (Phase 6 optimization possible) | Low | ✅ **Recommended** |
| Remove misleading config fields | Low | None (forecloses per-subsystem config) | Medium | Rejected — breaks undocumented operator contracts |

**Implementation:** In manager.py, construct `self._blueprint_embedding_service = SkillEmbeddingService(config=self.config.blueprint, embedding_repo=self._skill_embedding_repo)`. Update matcher construction at manager.py:936 to use the new service. Guard on `_skill_embedding_repo is not None` — never None in practice when blueprints are configured.

---

## Area B — Pending Queue Design

### B1 — Schema

**Recommendation: SQLModel table with `processed_at` soft-delete column for audit + crash recovery.**

```python
class ProjectBlueprintPendingUpdate(SQLModel, table=True):
    __tablename__ = "project_blueprint_pending_updates"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=64)
    project_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    source: str = Field(default="experience", max_length=32)  # 'experience' | 'history' — plain string (matches Blueprint.status convention)
    text: str = Field(sa_column=Column(Text, nullable=False))  # truncated to 10k by helper
    created_at: str = Field(default_factory=_now_iso)
    processed_at: Optional[str] = Field(default=None)  # soft-delete marker for audit + crash recovery
    __table_args__ = (
        Index("ix_pending_project_created", "project_id", "created_at"),
    )
```

**Key decisions:**
- `source` is plain string (matches `Blueprint.status` convention — no enum type in the codebase)
- `processed_at` retained despite plan saying "DELETE-after-process" — enables crash recovery (B4) and audit trail
- Composite index covers the scan query: `WHERE project_id=? AND processed_at IS NULL ORDER BY created_at`
- New table uses SQLModel `table=True` — `metadata.create_all()` handles creation (no `.sql` migration needed for brand-new tables)

---

### B2 — Hook Architecture

**Recommendation: Shared helper function `emit_blueprint_signal()` — fire-and-forget, fail-silent.**

```python
# daemon/repositories/blueprint/pending_signals.py
def emit_blueprint_signal(project_id: str, source: str, text: str, manager) -> None:
    """Insert a pending update. Fails silently — never blocks the caller."""
    try:
        repo = manager._blueprint_pending_repo
        repo.create(project_id=project_id, source=source, text=text[:10000])
    except Exception:
        logger.warning("blueprint signal emit failed", exc_info=True)
```

**Hooked from:**
- `experience()` in knowledge_tools.py — after kb-writer enqueue (additive, non-breaking)
- `project_history_add()` — after history insert, when `entry_type` is `feature` or `milestone`

| Approach | Coupling | Failure mode | Recommendation |
|----------|----------|--------------|----------------|
| Shared helper | Low (tool → repo only) | Fail-silent + logged | ✅ **Recommended** |
| Event bus / pub-sub | Lowest | Requires new infra | Rejected — overkill for one table |
| Direct repo call from tools | Medium | Tight coupling | Rejected — harder to test |

**Risk:** 🟡 Fail-silent means lost signals if repo is broken. Mitigation: daily reconcile job (see F2) scans for stale signals.

---

### B3 — Growth Management

**Recommendation: FIFO cap at 100 entries per project + daily maintenance prune.**

- At insert: if count for `project_id` exceeds 100, delete oldest beyond 100 (bounded `DELETE ... ORDER BY created_at OFFSET 100`)
- Daily prune: delete rows where `processed_at < now() - 30d` OR (`processed_at IS NULL AND created_at < now() - 90d`)
- 100 × 10k chars = ~1MB per project — comfortable for PostgreSQL

**Risk:** 🟢 Dormant-project stale records resolved by daily prune — acceptable lag.

---

### B4 — Consumption & Clearing

**Recommendation: Soft-delete via `processed_at` stamp in a transaction; blueprinter calls `consume_pending_signals()` which returns rows + stamps them.**

```python
def consume_pending_signals(self, project_id: str) -> list[ProjectBlueprintPendingUpdate]:
    with Session(self.engine) as session:
        rows = session.exec(
            select(ProjectBlueprintPendingUpdate)
            .where(
                ProjectBlueprintPendingUpdate.project_id == project_id,
                ProjectBlueprintPendingUpdate.processed_at.is_(None),
            )
            .order_by(ProjectBlueprintPendingUpdate.created_at)
        ).all()
        now = _now_iso()
        for row in rows:
            row.processed_at = now
            session.add(row)
        session.commit()
        return rows
```

| Approach | Crash recovery | Complexity | Recommendation |
|----------|---------------|------------|----------------|
| Soft-delete (`processed_at`) | ✅ Signals preserved on crash | Low + 1 column | ✅ **Recommended** |
| Hard DELETE-after-process | ❌ Signals lost if crash between process and delete | Lowest | Rejected — data loss on crash |

**Risk:** 🟡 Crash between `consume` (marks `processed_at`) and `apply_blueprint_update` (saves blueprints) → signal marked consumed but not applied. Mitigation: blueprint update is idempotent via versioning; the daily reconcile (F2) can re-emit signals from `project_history_add()` as fallback.

---

## Area C — Two Workflow Modes

### C1 — API Backward Compatibility

**Recommendation: Replace `/initialize` entirely with `/rebuild` + `/update`. Coordinate frontend button changes in the same PR.**

`/initialize` is an 18-hour-old greenfield feature with no external consumers. The only first-party consumer is `frontend/src/app/pages/blueprint/` — same repo, shipped in lockstep. No sunset window needed.

**Risk:** 🟡 Frontend hard-codes `/initialize` — PR must touch both backend and frontend.

---

### C2 — Concurrent Rebuild Guard

**Recommendation: C2(c) — Job-queue-level check via `idempotency_key`.**

The job queue already has atomic `create_or_get_by_idempotency_key` (job_queue_service.py:676-691) backed by a partial UNIQUE index with 24h TTL. Before enqueueing a rebuild, check for an existing non-terminal job with key `f"{project_id}:rebuild:{date_bucket}"`. If found → return 409 with existing job_id.

| Approach | Crash safety | Complexity | Ops overhead | Recommendation |
|----------|-------------|------------|--------------|----------------|
| C2(c) Job-queue idempotency | ✅ DB-backed, restart-safe | Lowest | None | ✅ **Recommended** |
| C2(a) DB advisory lock | ✅ Strong | Medium | Manual cleanup | Rejected — reimplements existing infra |
| C2(b) In-memory flag | ❌ Lost on restart | Lowest | Stale-lock risk | Rejected — unreliable |

**Risk:** 🟡 Stuck job holds idempotency slot for 24h after crash → operator must cancel via job queue API.

---

### C3 — Blueprint Deletion Strategy During Rebuild

**Recommendation: C3(b) — In-place update + prune stale.**

The blueprinter compares old vs new exploration results, updates matching blueprints (preserving revision history via versioning), soft-deletes ones no longer needed, and creates new ones. This preserves revision history continuity and avoids the "gap" problem of soft-delete-all-then-rebuild.

| Approach | Data safety | Revision history | Blueprinter load | Recommendation |
|----------|-------------|------------------|-----------------|----------------|
| C3(b) In-place + prune | High | ✅ Continuous | High (must compare) | ✅ **Recommended** |
| C3(a) Soft-delete all + rebuild | High | ❌ Broken (gaps) | Low (no comparison) | Rejected — loses history |

**Risk:** 🟡 LLM cognitive load — the `decide-changes` skill must enforce: only disable if area is absent from new exploration OR has persistently low match rate. Never disable on first rebuild.

---

## Area D — Blueprinter Skill Set & Workers

### D1 — Skill Granularity

**Recommendation: Keep 4 skills, owned by the blueprinter in `agents/blueprinter/skills/`.**

| Skill | Owner | Used by workers? | Output contract |
|-------|-------|-----------------|-----------------|
| `explore-for-rebuild` | blueprinter | ✅ (load_skill) | Full blueprint shape (sections + entities) |
| `explore-for-incremental` | blueprinter | ✅ (load_skill) | Delta vs current blueprint |
| `build-blueprint` | blueprinter | ✅ (load_skill) | Blueprint content (200-500 words) + trigger queries |
| `decide-changes` | blueprinter | ❌ (internal) | Decision log + blueprint delta plan |

**Why not merge `explore-for-rebuild` and `explore-for-incremental`?** They produce fundamentally different output contracts — rebuild produces full structural maps, incremental produces delta-focused change analysis. Merging forces every prompt to branch on mode, reducing clarity.

**Location:** `agents/blueprinter/skills/` — blueprinter-owned, consistent with v2 agent convention (developer, tester own their skills). Workers load them via `send_message(..., load_skill="explore-for-rebuild")`.

---

### D2 — Worker Coordination

**Recommendation: Architect-style fan-out/fan-in, structured JSON worker reports, blueprinter tracks via conversation history.**

**Worker report schema (structured JSON):**
```json
{
  "worker_id": "...",
  "phase": "rebuild|incremental",
  "findings": [
    {"topic": "...", "evidence": "file:line or fact", "confidence": "high|medium|low"}
  ],
  "summary": "1-2 sentence overview"
}
```

| Approach | Parseability | LLM flexibility | Recommendation |
|----------|-------------|-----------------|----------------|
| Structured JSON | ✅ Uniform across 4 workers | Lower (must conform) | ✅ **Recommended** |
| Free-form text | Lower (varies per worker) | Higher | Rejected — harder to synthesize from 4 sources |

**Coordination pattern (mirrors `agents/architect/`):**
1. Blueprinter lists top-level directories or pending records → splits into ≤4 groups
2. Fan-out: `send_message(to=worker, load_skill="explore-for-...", task=group)` for each
3. Fan-in: blueprinter receives reports as messages, synthesizes via `decide-changes` skill
4. Fan-out phase 2: `send_message(to=worker, load_skill="build-blueprint", task=blueprint_area)`
5. Fan-in phase 2: blueprinter saves via `blueprint_create`/`blueprint_update` tools

**Risk:** 🟡 Malformed JSON from a worker wastes 1/4 slots. Mitigation: skill body enforces strict JSON; blueprinter retries with a clarifying prompt on parse failure.

---

### D3 — Blueprinter Model Upgrade

**Recommendation: Upgrade from `quick` to mid-tier model.**

The `decide-changes` and `build-blueprint` phases are synthesis over multi-source signals (4 worker reports + pending queue + current blueprints). `quick` may hallucinate decisions or produce shallow merges. Workers do the heavy reading with their own models; the blueprinter's job is judgment. Mid-tier is the right cost/quality tradeoff.

| Approach | Cost | Judgment quality | Complexity | Recommendation |
|----------|------|-----------------|------------|----------------|
| Mid-tier (single model) | Medium per scan | Good | Low | ✅ **Recommended** |
| Stay on `quick` | Lowest | Risky for synthesis | Lowest | Rejected — synthesis quality concern |
| Two-model (quick + strong) | Higher | Best | High (meta.json knob) | Rejected — marginal gain for complexity |

**Risk:** 🟡 Cost increase per scan — but blueprinter runs daily/on-demand, not per-message. 🟢 Fallback to `strong` model for `decide-changes` if mid-tier underperforms (escalation hook).

---

## Area E — Matching Algorithm Hardening

### G6 — BM25 Single-Candidate Edge Case 🟡

**Recommendation: Special-case `n_docs == 1` — `bm25_norm = 1.0` if raw BM25 > 0, else 0.0.**

When there's only 1 area blueprint candidate, min-max normalization is undefined (span=0) and currently maps everything to 0.0 — silently dropping the single most relevant blueprint. The single-candidate case is where we most want a confident score.

```python
# In _match_area, after computing span:
if n_docs == 1:
    bm25_norm = 1.0 if bm25_raw.get(0, 0.0) > 0 else 0.0
else:
    bm25_norm = (bm25_raw[idx] - bm25_min) / span if span > 0 else 0.0
```

**Risk:** 🟢 Rescued single candidates may shift baseline recall — calibrate `match_threshold` in Phase 6 after the fix lands. The multi-doc path is unchanged.

---

### G7 — One Core Per Project 🟡

**Recommendation: DB partial unique index on `(project_id) WHERE kind = 'core' AND is_active = true`.**

Application-level checks in `create()` are racy under concurrent writes (two blueprinter runs, admin + agent). A partial unique index is the only race-free guarantee. Both PostgreSQL and SQLite support partial indexes.

```python
# In Blueprint.__table_args__:
Index(
    "uq_project_blueprints_one_active_core",
    "project_id",
    unique=True,
    postgresql_where=text("kind = 'core' AND is_active = true"),
    sqlite_where=text("kind = 'core' AND is_active = 1"),
)
```

**Risk:** 🟡 **Existing databases may have multiple cores.** Run a pre-flight dedup query before creating the index:
```sql
SELECT project_id, COUNT(*) FROM project_blueprints
WHERE kind='core' AND is_active=true
GROUP BY project_id HAVING COUNT(*) > 1;
```
Soft-delete extras (keep the one with the highest version) BEFORE creating the index.

---

### G8 — Status Semantics 🟡

**Recommendation: Filter `search_candidates()` by `status == 'published'`. Treat `is_active` (soft-delete) and `status` (lifecycle) as orthogonal gates.**

```python
# In repository.search_candidates():
Blueprint.kind == "area",
Blueprint.is_active == True,
Blueprint.status == "published",   # NEW — lifecycle gate
```

`is_active=False` means "retired" (blueprint_delete). `status='draft'` means "in review". Both must be true to surface. This prevents a draft blueprint accidentally flagged `is_active=True` from injecting into agent context.

**Risk:** 🟢 Zero query cost — covered by existing `ix_project_blueprints_status`. 🟡 Need to document status values (`published`, `draft`, `archived`) in the model docstring. Currently only `published` is written — the filter is a no-op until status semantics are actively used.

---

## Area F — Daily Scan Scheduler

### F1 — Built-in Scheduler

**Recommendation: Built-in scheduler via `MaintenanceService` registration.**

The current self-scheduling pattern (blueprinter re-enqueues itself in workflow.md Phase 6) is fragile — if the blueprinter crashes during a run, the chain breaks and no scan runs for 24h+. `MaintenanceService` (daemon/services/maintenance.py:68-225) already provides: `register(name, min_interval_hours, execute_fn)`, idle gating via `_is_idle()`, graceful shutdown, and restart-aware `_is_due` checks.

**Risk:** 🟡 In-memory `last_run` resets on restart → first cycle after restart may run immediately. Acceptable — one extra scan on restart is preferable to a missed run.

---

### F2 — Scheduler Pattern

**Recommendation: Register `blueprint_daily_scan` with `MaintenanceService` — no new dependencies.**

```python
# In manager startup, near MaintenanceService registration:
self._maintenance_service.register(
    name="blueprint_daily_scan",
    min_interval_hours=24,
    execute_fn=self._trigger_blueprint_daily_scans,
)

async def _trigger_blueprint_daily_scans(self):
    """Enqueue one daily scan per project with idle gating."""
    projects = await asyncio.to_thread(self._project_repository.list_all)
    for project in projects:
        await self._enqueue_blueprint_scan(project.id, trigger="daily-scan")
```

| Approach | Restart recovery | Persistence | Dependencies | Recommendation |
|----------|-----------------|-------------|--------------|----------------|
| MaintenanceService (asyncio) | ✅ Re-runs after restart | In-memory `last_run` | None (existing) | ✅ **Recommended** |
| APScheduler | ✅ DB-backed | Full | New dep | Rejected — wrong layer |
| Job-queue poller | ✅ DB-backed | DB | None | Rejected — duplicates `/scan` |
| Self-scheduling (current) | ❌ Fragile | N/A | None | Rejected — drops chain on crash |

**Implementation:** Drop Phase 6 self-scheduling from `workflow.md`. Each scan job uses `idempotency_key=f"{project_id}:daily_scan:{date}"`. Keep `/scan` endpoint as manual fallback.

---

## Cross-Cutting: Approach Comparison

### Wiring Gaps (A) — Implementation Order

| Fix | Priority | Effort | Blocks | Recommended order |
|-----|----------|--------|--------|-------------------|
| **G1** Trigger wiring | 🔴 P0 | Medium | All area matching | **1st** — unblocks vector stage |
| **G4** Embedding service | 🟡 P1 | Low | G1 (G1 needs the service) | **2nd** — prerequisite for G1 |
| **G3** Rate limiter | 🟡 P1 | Low | G1 writes (should gate them) | **3rd** — parallel with G1 |
| **G2** Revision history | 🟡 P2 | Low | None | **4th** — independent |

**Critical dependency:** G4 must land before or with G1 — G1's embedding computation needs `self._blueprint_embedding_service`.

### Matching Hardening (E) — Implementation Order

| Fix | Priority | Effort | Depends on | Recommended order |
|-----|----------|--------|------------|-------------------|
| **G6** BM25 single-candidate | 🟡 P1 | Trivial (3 lines) | None | **1st** — immediate impact |
| **G8** Status filter | 🟡 P2 | Trivial (1 line) | None | **2nd** — preventive |
| **G7** Core uniqueness | 🟡 P1 | Low + migration | Pre-flight dedup | **3rd** — needs data check |

---

## Risk Register

### 🔴 Critical

| Risk | Area | Mitigation |
|------|------|------------|
| **G1: Vector stage is dead** — every tool-created blueprint gets 60% weight of zero. The entire area-matching subsystem produces no value until fixed. | A | Implement G1(a) first. This is the unblocker for the subsystem's value proposition. |

### 🟡 Significant

| Risk | Area | Mitigation |
|------|------|------------|
| **G3 thread safety:** `BlueprintRateLimiter` uses `threading.Lock` but tools are async. Concurrent blueprinter workers in Phase 2 may bypass the lock. | A | Verify same-thread execution, or switch to `asyncio.Lock`. Flag for Phase 2 worker concurrency. |
| **G7 existing duplicates:** Dev DBs may have multiple cores, blocking the partial unique index. | E | Run pre-flight dedup query; soft-delete extras before index creation. |
| **C2 stuck job:** A crashed rebuild job holds the idempotency slot for 24h. | C | Operator cancels via job queue API. Document in operational runbook. |
| **D2 malformed JSON:** A worker returns unparseable JSON, wasting 1/4 fan-out slots. | D | Skill body enforces strict JSON; blueprinter retries with clarifying prompt. |
| **B4 crash gap:** Signal marked consumed but blueprint not updated (crash between consume and apply). | B | Daily reconcile job re-emits from `project_history_add()` as fallback. |
| **C3 LLM over-disables:** Blueprinter disables useful blueprints during rebuild. | C | `decide-changes` skill enforces: only disable if absent from exploration OR persistent low-match. Never on first rebuild. |

### 🟢 Improvement Opportunities

| Risk | Area | Mitigation |
|------|------|------------|
| G2 revision gap: pre-fix updates have no revision history. | A | Acceptable for a subsystem in flux. |
| G8 status values undocumented: only `published` exists currently. | E | Document in model docstring; filter is a no-op until used. |
| B3 dormant-project stale records. | B | Daily prune handles it with acceptable lag. |
| F1 one extra scan on restart. | F | Acceptable — preferable to missed runs. |

---

## Decisions Pending (Leader)

1. **Model tier name:** The codebase uses `quick` for the blueprinter. What is the canonical mid-tier model name in the model registry? (e.g., `balanced`, `standard`, `agentic`?) The recommendation is "upgrade from quick" — the exact model ID needs confirmation.

2. **Pending queue `processed_at` vs DELETE:** The evolution plan specifies "DELETE all pending records for that project after incremental update completes." The architecture recommendation suggests `processed_at` soft-delete for crash recovery. **Leader decision:** accept the soft-delete refinement (recommended), or stick with hard DELETE per the original plan?

3. **G7 partial unique index migration:** If existing databases have duplicate cores, the index creation will fail. **Leader decision:** soft-delete extras automatically (keep highest version), or fail and require manual cleanup?

4. **`project_history_add()` location:** The hook for inserting into the pending queue needs to be added to `project_history_add()`. **Where does this function live?** (Tool or repository?) The worker could not verify this — it affects where the hook line goes.

5. **System background queue concurrency:** If `system_background_queue` has concurrency=1, daily scans serialize behind other background work. **Leader decision:** verify and potentially increase concurrency for the blueprinter queue.

---

## Open Questions

1. **G1 dual-write atomicity:** The Blueprint row and BlueprintTrigger rows are written in separate sessions (fire-and-forget). Is this acceptable operationally, or should we wrap in a single transaction? The codebase pattern (`update_skill_embeddings`) uses separate sessions — but that's for embeddings, not primary content.

2. **Skill search for blueprinter:** The blueprinter has `"tools": {"allow": ["blueprint", "knowledge", ...]}`. When it loads `decide-changes` as a skill via `skill-set.yaml`, does the skill injection system find it? The skill lives in `agents/blueprinter/skills/` — is this path registered for skill search?

3. **Worker skill loading path:** Workers receive `load_skill="explore-for-rebuild"` — but that skill is owned by the blueprinter agent. Does the skill bank resolve cross-agent skills by name, or must it be in a shared location? (Evolution plan Open Question #1.)

4. **Frontend coordination timing:** C1 recommends replacing `/initialize` with `/rebuild` + `/update` in the same PR as frontend changes. Is there a deployment sequence concern (backend deployed before frontend)?

---

## Implementation Patterns to Follow

| Pattern | Source | Applies to |
|---------|--------|------------|
| `update_skill_embeddings()` fire-and-forget embedding | `skill_embedding_service.py:320-405` | G1 trigger embedding |
| Repository `update()` auto-version-bump | `repository.py:79-80` | G2 revision capture |
| `BlueprintRateLimiter` with circuit breaker | `blueprint_rate_limiter.py` | G3 enforcement |
| `EmbeddingConfig` prefix-scoped config | `config.py:473-595` | G4 separate service |
| SQLModel `table=True` + `metadata.create_all()` | `models.py`, plan-overview.md §12 | B1 pending table |
| Job-queue `idempotency_key` with 24h TTL | `job_queue_service.py:676-691` | C2 concurrent guard |
| `MaintenanceService.register()` with idle gating | `maintenance.py:68-225` | F1/F2 scheduler |
| `search_candidates()` filter chain | `repository.py:189-222` | G8 status filter |
| Partial unique index (dialect-specific) | SQLAlchemy `postgresql_where`/`sqlite_where` | G7 core uniqueness |
| Architect fan-out/fan-in pattern | `agents/architect/workflow.md` | D2 worker coordination |
