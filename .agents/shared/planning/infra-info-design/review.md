# Review: Infrastructure Info Storage Design Plan

**Reviewer:** Reviewer Agent (ensemble)
**Date:** 2026-06-15
**Plan:** `.agents/shared/planning/infra-info-design/plan.md`
**Verdict:** 🔴 **NEEDS REVISION** — 4 critical defects must be fixed before discussion proceeds

---

## Verdict: 🔴 NEEDS REVISION

**4 Critical** · **4 Warnings** · **6 Suggestions**

The overall architecture (Option B: flexible document model) is sound and correctly mirrors the existing `project_metadata_records` pattern. However, the plan contains **4 critical factual errors** about how the codebase works — each would cause implementation failure. These are fixable with targeted revisions.

---

## 🔴 Critical Issues (Must Fix Before Discussion)

### C1. GIN Indexes in Migration SQL — FATAL on Both Drivers

**Location:** §2.1, lines 138–144 + Dialect Notes lines 160–163

**Issue:** Three compounding failures:

1. **PostgreSQL: Migration file is never executed.** The migration runner at `daemon/migrations/runner.py:446` is a **no-op for PostgreSQL**:
   ```python
   if "sqlite" not in str(self.engine.url):
       return []  # PostgreSQL uses create_all() instead
   ```
   The GIN indexes in the `.sql` file will **never be created** on PostgreSQL.

2. **SQLite: `USING GIN` is a syntax error.** The plan claims (line 139): *"GIN is PG-specific but harmless on SQLite."* This is **wrong**. SQLite's parser rejects `USING GIN` with `sqlite3.OperationalError: near "USING": syntax error`. It is NOT silently ignored.

3. **PostgreSQL: GIN doesn't work on `json` columns anyway.** See C2 — `Column(JSON)` renders as `json`, not `jsonb`. GIN indexes require `jsonb`.

**Fix:** Remove GIN indexes from the `.sql` migration entirely. Define them in SQLModel `__table_args__` with `postgresql_using='gin'`:
```python
__table_args__ = (
    Index("ix_infra_assets_attributes_gin", "attributes", postgresql_using="gin"),
    Index("ix_infra_assets_relationships_gin", "relationships", postgresql_using="gin"),
)
```
`SQLModel.metadata.create_all()` processes `__table_args__` indexes on both drivers — the `postgresql_using` kwarg is ignored by SQLite's dialect. This pattern is already used in 6+ existing models (`job_queue/models.py`, `source/models.py`, `task/models.py`, etc.).

---

### C2. `Column(JSON)` Renders as `json`, NOT `jsonb` on PostgreSQL

**Location:** §2.1 Dialect Notes, line 162

**Issue:** The plan states: *"PostgreSQL: JSON is jsonb alias in SQLModel."* **This is false.** SQLAlchemy's generic `from sqlalchemy.types import JSON` maps to PostgreSQL's `json` type, **not** `jsonb`. This is confirmed by the existing codebase: `project/repository.py:252–258` must explicitly `cast(Project.relationships, JSONB)` at query time because the column is `json`.

Consequences:
- `@>` containment operator and `?` key-existence operator **only work on `jsonb`** — the search queries in §4.2 (lines 322, 326) will raise `ERROR: operator does not exist: json @> unknown`
- GIN indexes **cannot be created on `json` columns**

**Fix:** Use a dialect-aware `TypeDecorator`:
```python
class JSONBType(TypeDecorator):
    impl = JSON
    cache_ok = True
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())
```
Then use `sa_column=Column(JSONBType)` for `attributes` and `relationships`.

---

### C3. `infra_type_register` Tool — Listed in Some Sections, Missing from Tool List

**Location:** §7.1 (tool list) vs §3.2 (line 288) and §8 Phase 1.5 (line 446)

**Issue:** The `infra_type_register` tool is mentioned in §3.2 ("provide a `infra_type_register` tool") and §8 Phase 1.5 deliverables, but is **NOT in the §7.1 tool list**. This inconsistency will cause confusion at implementation time — is it a tool or not?

**Fix:** Add `infra_type_register(name, schema_json, description)` to the §7.1 tool list explicitly.

---

### C4. `get_shared_context()` — Misleading Reference

**Location:** §7.2, line 429

**Issue:** The plan claims infra context flows via "existing `get_shared_context()` mechanism (cf. `daemon/services/context_injection.py`)." Verified at `context_injection.py:728`:

```python
def get_shared_context(context_key, query, audience="internal", ...):
    """Resolves context dir: {tempdir}/ensemble/context/{context_key}"""
    logger.info("[Explorer] get_shared_context called: ...")
```

This function is **Explorer-specific** (log prefix `[Explorer]`) and operates on a **filesystem context directory** — it matches files on disk, not DB rows. It is NOT a generic context injection mechanism for DevOps agent infra data. The plan's integration model for context injection needs to be rewritten with the correct mechanism.

**Fix:** Specify the actual context flow mechanism. Options: (a) inject infra summary into DevOps agent prompt at message assembly time, (b) use the existing `knowledge` tool / RAG query, or (c) create a dedicated `infra_context_get` tool. Don't reference `get_shared_context()` as the delivery mechanism.

---

## 🟡 Warnings (Should Fix)

### W1. SQLite `search_assets()` Has No Defined Query Translation

**Location:** §4.2 lines 337–344 + §5.2 line 389

The plan only shows PostgreSQL examples using `@>`, `?`, `->>`. For SQLite, it references `project/repository.py`'s `.contains(f'"instances"')` — but this is **string `LIKE` matching**, not real JSON containment. It would match any row containing the substring anywhere in the JSON text. Numeric comparisons (`{"cpu_cores": {">=": 8}}`) are **impossible** with string matching.

**Fix:** Specify SQLite fallback using `json_extract(attributes, '$.cpu_cores')` for typed comparisons (SQLite 3.38+), with Python-side post-filtering for complex conditions. Document explicitly in the plan.

---

### W2. Missing Middle-Tier Search (Text/Name Search)

**Location:** §4 (Search Strategy)

The plan jumps from "structured JSONB containment" directly to "LightRAG semantic search" (Phase 3). The most common DevOps queries — "find server mentioning postgres", "fuzzy hostname `web-0*`", "search across all attribute values for `10.0.1`" — **cannot be served** by either:

- JSONB `@>` requires exact key/value match, not substring
- LightRAG is overkill for simple name/description lookups

**Fix:** Add a `search_text` TEXT column populated at insert time (concatenation of `name` + key attribute values). Phase 1 uses `WHERE search_text LIKE '%query%'` on SQLite and optional `tsvector` index on PostgreSQL. This covers 80% of real DevOps find-queries without LightRAG.

---

### W3. Relationship Modeling — Convention Ambiguity + Orphan Risk

**Location:** §2.2 model notes, §9 Risk table, Appendix Q3

Two issues:

1. **Convention undefined:** When does a DevOps agent use `parent_asset_id` vs `relationships` JSONB? The plan says only "for complex DAGs, use relationships" — not actionable. Also, no DB constraint prevents putting a `"parent"` relationship type in the JSONB column.

2. **Orphaned relationships:** `relationships` JSONB contains UUIDs that **cannot have FK constraints** (PostgreSQL doesn't support FK on JSONB array elements). When an asset is deleted, every asset referencing it in `relationships` silently dangles. No cleanup mechanism specified.

**Fix:**
- Document the convention: `parent_asset_id` = containment hierarchy (rack ⊃ server). `relationships` = typed cross-cutting edges only (e.g., `{"peers_with": [...]}`). Add a rule in the repository: reject `"parent"` or `"contains"` as relationship types.
- Add a `pre_delete` hook or periodic maintenance job to scan `relationships` UUIDs and clean/log orphans.

---

### W4. `search_assets(query_dict)` — Undocumented Mini Query Language

**Location:** §5.2, line 389

The example `{"type": "server", "attributes": {"cpu_cores": {">=": 8}}}` implies a query DSL, but operators, type coercion, NULL handling, IN-lists, and AND/OR composition are all undefined. This is a significant API surface hiding behind a one-liner.

**Fix:** Pin to a documented minimal DSL: equality (`=`) + containment (`IN`) + range (`>=`, `<=`) on a single attribute key, AND-composed only. Encode in a dedicated `query.py` module with explicit validation and error messages.

---

## 🟢 Suggestions (Consider)

| # | Location | Suggestion |
|---|----------|------------|
| S1 | §8 | **Add Phase 0** (schema + migration only, no tools). Bundling schema with repository in Phase 1 makes rollback harder. |
| S2 | §8 | **Tests missing** from all phase estimates. Add explicit test deliverables per phase. |
| S3 | §2.1 | **Missing UNIQUE constraint** on `(project_id, type, name)`. Two agents can create duplicate-name assets with no conflict detection. |
| S4 | §2.1 | **Missing pagination** for `infra_asset_list` / `infra_search`. With thousands of assets, returning all is problematic. |
| S5 | §2.1 | **Missing audit columns** (`created_by`, `updated_by`/`agent_id`). Cannot trace which DevOps agent created/modified an asset. |
| S6 | §Appendix | **Missing open questions:** pagination strategy, soft vs hard delete, concurrent access/locking, bulk import (terraform state/kubectl), performance ceiling for JSONB containment. |

---

## Open Questions Worth Discussing (Additional)

Beyond the plan's 4 open questions, these deserve discussion:

1. **Global vs Per-Project type registry (Q1 in plan):** Recommend **hybrid** — global defaults + optional `project_id` column on `infra_asset_types` for project-specific overrides. Different projects (IoT vs Web vs ML) have wildly different infra types. Adding `project_id` now is cheap; adding it later requires migration.

2. **Bulk import strategy:** DevOps agents discover existing infra from `terraform state pull`, `kubectl get all`, AWS CLI. Single-asset creation is impractical for initial inventory. Consider a `infra_asset_bulk_import(assets: list[dict])` tool.

3. **Phased plan realism:** Phase 1 (1-2 days) includes 5+ files (migration, models, repository, factory wiring, manager). This is realistic only if tests are excluded. Phase 2 (1 day) is tight if context injection mechanism needs redesign (C4).

4. **SQLite dev experience:** Since SQLite can't do GIN indexes or `@>` containment, local dev search will be fundamentally different from production. Is this acceptable? Consider documenting expected behavior differences explicitly.

---

## What's Good

- ✅ Option B (flexible document model) is the right choice — correctly rejects Option A's table explosion
- ✅ `project_metadata_records` pattern is the right precedent to follow
- ✅ Rejection of Elasticsearch/Meilisearch is sound given existing LightRAG
- ✅ Project isolation via `project_id` FK + query scoping is correct
- ✅ Repository location (`daemon/repositories/infra/`) follows existing conventions
- ✅ Phased approach (defer LightRAG to Phase 3) is reasonable

---

## Recommended Revision Steps

1. **Fix C1 + C2 together:** Move GIN indexes to `__table_args__`, switch to `JSONBType` TypeDecorator — these are interdependent
2. **Fix C3:** Add `infra_type_register` to §7.1 tool list
3. **Fix C4:** Rewrite §7.2 context injection with the correct mechanism
4. **Fix W1:** Specify SQLite `search_assets()` fallback strategy
5. **Add S3:** UNIQUE constraint on `(project_id, type, name)`
6. **Re-estimate phases** with test deliverables included

---

*Review performed by 2 parallel opencode sessions (review-schema + review-arch) cross-referencing the plan against actual codebase patterns.*
