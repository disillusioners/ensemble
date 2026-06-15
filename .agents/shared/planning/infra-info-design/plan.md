# Infrastructure Information Storage Per Project — Design Document

**Status:** Revised per user decisions and reviewer feedback. All critical fixes, warnings, and suggestions incorporated.

---

## Executive Summary (Final Design)

**Recommended Design:** Option B (Flexible Document Model) with **three tables**:

1. `infra_assets` — Main storage (type + JSONB attributes + parent_asset_id + relationships + audit columns + UNIQUE constraint)
2. `infra_asset_types` — **Global** type registry (shared across all projects, no project_id)
3. `infra_asset_history` — Versioning/history (asset snapshots, change tracking, changed_by instance_id)

**Key Locked Decisions:**
- ✅ Schema: Option B (JSONB Document Model)
- ✅ Search: PostgreSQL JSONB + GIN indexes now; LightRAG deferred to Phase 3
- ✅ Type registry: GLOBAL (not per-project)
- ✅ Versioning: BUILD NOW (`infra_asset_history` table in initial phases)
- ✅ SQLite: Limited features OK — PostgreSQL is mainstream; SQLite uses `json_extract()` for basic filtering

**Critical Technical Fixes:**
- **C1:** GIN indexes defined via SQLAlchemy `__table_args__` with `postgresql_using='gin'` (NOT raw SQL `USING GIN`)
- **C2:** Custom `JSONBType` TypeDecorator mapping to `JSONB` on PostgreSQL and `JSON`/`TEXT` on SQLite
- **C3:** `infra_type_register` tool added to complete tool inventory (§7.1)
- **C4:** DevOps context injection via dedicated `infra_search`/`infra_list` tools (NOT `get_shared_context()` which is Explorer-only)

**Search Strategy:** PostgreSQL JSONB operators (`->`, `->>`, `@>`, `?`) + GIN indexes for Phase 1-2. SQLite fallback uses `json_extract()`. Text search tier (LIKE/ILIKE on name + type) included. LightRAG semantic search on infra docs deferred to Phase 3.

**Phased Delivery:** Phase 0 (schema-only migration), Phase 1 (models + repository + versioning + tests), Phase 2 (DevOps tools + context injection), Phase 3 (LightRAG integration).

---

## 1. Schema Design Options Comparison

### Option A: Entity-per-Type Tables (Rejected)

**Structure:**
- `infra_datacenters(id, project_id, name, location, capacity, ...)`
- `infra_racks(id, project_id, datacenter_id, name, u_height, ...)`
- `infra_servers(id, project_id, rack_id, hostname, ip, cpu, ram, ...)`
- `infra_k8s_clusters(id, project_id, name, version, node_count, ...)`
- `infra_networks(id, project_id, name, cidr, vlan, ...)`
- ... (new table per infra type)

**Pros:**
- Strong typing, schema enforcement, FK relationships
- Easy SQL joins and indexes

**Cons:**
- Table explosion (10+ infra types = 10+ tables)
- Schema rigidity — every new infra type requires migration
- Violates "flexible schema" requirement
- Violates project's preference for fewer tables (cf. `project_metadata_records` migration from JSON column)

**Verdict:** Rejected. Too many tables, too rigid.

---

### Option B: Flexible Document Model (Confirmed)

**Structure (3 Tables):**
- `infra_assets(id, project_id, type, name, parent_asset_id, attributes JSONB, relationships JSONB, created_at, updated_at, created_by, updated_by)` + UNIQUE(project_id, type, name)
- `infra_asset_types(name, schema_json, description, created_at, updated_at)` — **GLOBAL** registry (no project_id)
- `infra_asset_history(id, asset_id, project_id, snapshot JSONB, change_type, changed_fields JSONB, old_values JSONB, new_values JSONB, changed_by, timestamp)` — versioning

**Example rows in `infra_assets`:**
```json
{
  "id": "uuid-1",
  "project_id": "proj-abc",
  "type": "datacenter",
  "name": "us-east-1",
  "parent_asset_id": null,
  "attributes": {"location": "Virginia", "capacity_mw": 50},
  "relationships": {"networks": ["uuid-net1"]},
  "created_by": "instance-xyz",
  "updated_by": "instance-xyz"
}
{
  "id": "uuid-2",
  "project_id": "proj-abc",
  "type": "server",
  "name": "web-01",
  "parent_asset_id": "uuid-rack-7",
  "attributes": {"hostname": "web-01", "ip": "10.0.1.10", "cpu_cores": 16, "ram_gb": 64},
  "relationships": {"k8s_nodes": ["uuid-cluster-1"]},
  "created_by": "instance-abc",
  "updated_by": "instance-def"
}
```

**Pros:**
- Single main table, arbitrary types via `type` + `attributes` JSONB
- Class/type definitions live in Python code (Pydantic models) + global registry table
- Easy to add new infra types without migrations
- Matches project's `project_metadata_records` pattern (dedicated table + JSON value)
- Versioning built-in from Day 1

**Cons:**
- Weaker typing (mitigated by class registry + validation layer)
- JSONB queries require dialect guards (already solved pattern in `project/repository.py`)

**Verdict:** Confirmed. Matches codebase conventions, flexible, minimal tables, versioning included.

---

## 2. Table Schema Definitions (Revised)

### 2.0 Phase 0: Schema-Only Migration (S1 — Incorporated)

**Migration File:** `20260616_000001_create_infra_assets_table.sql` (schema only, no application code yet)

**Purpose:** Create the three tables with all constraints, indexes, and dialect-aware column types. This phase allows DB schema review before repository/tool implementation.

---

### 2.1 Custom TypeDecorator: `JSONBType` (C2 — Critical Fix)

**Location:** `daemon/repositories/infra/types.py` (or `daemon/db_types.py`)

```python
from sqlalchemy import TypeDecorator, JSON
from sqlalchemy.dialects.postgresql import JSONB


class JSONBType(TypeDecorator):
    """
    Dialect-aware JSON column type.

    - PostgreSQL: Uses JSONB (enables @>, ?, GIN indexes, containment queries)
    - SQLite: Uses JSON (TEXT storage with json_extract/json_set support)

    This ensures PostgreSQL operators (@>, ?, ->, ->>) and GIN indexes work
    correctly while SQLite gracefully degrades to json_extract() for basic filtering.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB)
        return dialect.type_descriptor(JSON)

    def process_bind_param(self, value, dialect):
        return value

    def process_result_value(self, value, dialect):
        return value
```

**Usage in models:** Replace `sa_column=Column(JSON)` with `sa_column=Column(JSONBType)` for `attributes` and `relationships` columns.

---

### 2.2 Migration File (Next Number: `20260616_000001_create_infra_assets_table.sql`)

```sql
-- Migration: create infra_assets, infra_asset_types, infra_asset_history tables
-- Created: 2026-06-16
-- Author: system
-- Description: Infrastructure information storage per project (datacenters, servers, K8s, networks, etc.)
--              Uses flexible document model (type + JSONB attributes) per Option B design.
--              Includes versioning/history via infra_asset_history table.
--              Phase 0: Schema-only migration (no application code yet).

-- UP

-- ============================================================================
-- 1. infra_asset_types — GLOBAL type registry (shared across all projects)
-- ============================================================================
CREATE TABLE IF NOT EXISTS infra_asset_types (
    name TEXT PRIMARY KEY,
    schema_json JSON,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_infra_asset_types_updated_at
    ON infra_asset_types(updated_at);

-- ============================================================================
-- 2. infra_assets — Main storage with UNIQUE constraint + audit columns
-- ============================================================================
CREATE TABLE IF NOT EXISTS infra_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_asset_id TEXT REFERENCES infra_assets(id) ON DELETE SET NULL,
    attributes JSON,
    relationships JSON,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    updated_by TEXT
);

-- Project isolation + uniqueness
CREATE INDEX IF NOT EXISTS ix_infra_assets_project_id
    ON infra_assets(project_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infra_assets_project_type_name
    ON infra_assets(project_id, type, name);

-- Type + parent lookups
CREATE INDEX IF NOT EXISTS ix_infra_assets_type
    ON infra_assets(type);

CREATE INDEX IF NOT EXISTS ix_infra_assets_parent_asset_id
    ON infra_assets(parent_asset_id);

-- Audit + timestamp
CREATE INDEX IF NOT EXISTS ix_infra_assets_updated_at
    ON infra_assets(updated_at);

-- Text search tier (W2)
CREATE INDEX IF NOT EXISTS ix_infra_assets_name
    ON infra_assets(name);

-- ============================================================================
-- 3. infra_asset_history — Versioning / change tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS infra_asset_history (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES infra_assets(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    snapshot JSON,
    change_type TEXT NOT NULL,
    changed_fields JSON,
    old_values JSON,
    new_values JSON,
    changed_by TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_asset_id
    ON infra_asset_history(asset_id);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_project_id
    ON infra_asset_history(project_id);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_timestamp
    ON infra_asset_history(timestamp);

CREATE INDEX IF NOT EXISTS ix_infra_asset_history_change_type
    ON infra_asset_history(change_type);

-- DOWN

DROP TABLE IF EXISTS infra_asset_history;
DROP INDEX IF EXISTS ix_infra_assets_name;
DROP INDEX IF EXISTS uq_infra_assets_project_type_name;
DROP INDEX IF EXISTS ix_infra_assets_updated_at;
DROP INDEX IF EXISTS ix_infra_assets_parent_asset_id;
DROP INDEX IF EXISTS ix_infra_assets_type;
DROP INDEX IF EXISTS ix_infra_assets_project_id;
DROP TABLE IF EXISTS infra_assets;

DROP INDEX IF EXISTS ix_infra_asset_types_updated_at;
DROP TABLE IF EXISTS infra_asset_types;
```

**Important Notes (C1, C2):**
- **NO `USING GIN` in raw SQL.** GIN indexes are defined via SQLAlchemy `__table_args__` in the model (see §2.3). The migration runner will skip PG-specific indexes on SQLite.
- **Column type `JSON` in SQL** is a placeholder. SQLAlchemy model uses `JSONBType` TypeDecorator which maps to `JSONB` on PostgreSQL at runtime.

---

### 2.3 SQLModel Models (Revised with GIN Indexes + JSONBType)

**Location:** `daemon/repositories/infra/models.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, String, UniqueConstraint
from sqlmodel import SQLModel, Field

from .types import JSONBType  # Custom TypeDecorator


class InfraAssetType(SQLModel, table=True):
    """Registry of infrastructure asset types (classes). GLOBAL — no project_id."""
    __tablename__ = "infra_asset_types"

    name: str = Field(primary_key=True, max_length=64)
    schema_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONBType))
    description: str | None = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class InfraAsset(SQLModel, table=True):
    """Infrastructure asset (datacenter, server, K8s cluster, network, etc.)."""
    __tablename__ = "infra_assets"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    type: str = Field(index=True, max_length=64)
    name: str = Field(max_length=256)
    parent_asset_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("infra_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    attributes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONBType))
    relationships: dict[str, list[str]] = Field(default_factory=dict, sa_column=Column(JSONBType))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str | None = Field(default=None)  # instance_id
    updated_by: str | None = Field(default=None)  # instance_id

    __table_args__ = (
        UniqueConstraint("project_id", "type", "name", name="uq_infra_assets_project_type_name"),
        # GIN indexes (C1): PostgreSQL only; SQLite skips these
        Index("ix_infra_assets_attributes_gin", "attributes", postgresql_using="gin"),
        Index("ix_infra_assets_relationships_gin", "relationships", postgresql_using="gin"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type,
            "name": self.name,
            "parent_asset_id": self.parent_asset_id,
            "attributes": dict(self.attributes),
            "relationships": dict(self.relationships),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }


class InfraAssetHistory(SQLModel, table=True):
    """Versioning/history for infra asset changes."""
    __tablename__ = "infra_asset_history"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    asset_id: str = Field(
        sa_column=Column(String, ForeignKey("infra_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    snapshot: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONBType))
    change_type: str = Field(max_length=16)  # 'created', 'updated', 'deleted'
    changed_fields: list[str] | None = Field(default=None, sa_column=Column(JSONBType))
    old_values: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONBType))
    new_values: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONBType))
    changed_by: str | None = Field(default=None)  # instance_id
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), index=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "project_id": self.project_id,
            "snapshot": self.snapshot,
            "change_type": self.change_type,
            "changed_fields": self.changed_fields,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "changed_by": self.changed_by,
            "timestamp": self.timestamp,
        }
```

**GIN Index Definition (C1):** Defined in `__table_args__` with `postgresql_using="gin"`. SQLAlchemy automatically skips this on SQLite (no error).

---

## 3. Class/Type Registry Design (GLOBAL — Confirmed)

### 3.1 Python Class Definitions (Pydantic Models)

**Location:** `daemon/repositories/infra/types.py`

```python
from pydantic import BaseModel
from typing import Any


class InfraTypeDefinition(BaseModel):
    """Base class for infrastructure type definitions."""
    type_name: str
    description: str
    schema: dict[str, Any]


# Pre-defined infrastructure types (seeded at startup)
INFRA_TYPE_DEFINITIONS: list[InfraTypeDefinition] = [
    InfraTypeDefinition(
        type_name="datacenter",
        description="Physical or cloud datacenter/region",
        schema={
            "location": {"type": "string"},
            "provider": {"type": "string", "enum": ["aws", "gcp", "azure", "onprem"]},
            "capacity_mw": {"type": "number"},
        }
    ),
    InfraTypeDefinition(
        type_name="server",
        description="Physical or virtual server",
        schema={
            "hostname": {"type": "string"},
            "ip": {"type": "string"},
            "cpu_cores": {"type": "integer"},
            "ram_gb": {"type": "integer"},
            "disk_gb": {"type": "integer"},
        }
    ),
    InfraTypeDefinition(
        type_name="k8s_cluster",
        description="Kubernetes cluster",
        schema={
            "version": {"type": "string"},
            "node_count": {"type": "integer"},
            "cloud_provider": {"type": "string"},
        }
    ),
]
```

### 3.2 Registry Seeding + Dynamic Registration

- Seed `infra_asset_types` table at application startup (global, once)
- Provide `infra_type_register` tool for DevOps agents to add custom types at runtime (C3)

---

## 4. Search Strategy Analysis (Revised)

### 4.1 Option Comparison (Unchanged)

| Option | Capabilities | Verdict |
|--------|--------------|---------|
| **PostgreSQL JSONB + GIN** | `->`, `->>`, `@>`, `?`, GIN indexes | **Recommended for Phase 1-2** |
| **LightRAG** | Semantic search, knowledge graph | **Deferred to Phase 3** |
| **Elasticsearch/Meilisearch** | Full-text, fuzzy, facets | Rejected (adds infra) |
| **Delay search** | Basic SQL `WHERE type=? AND name LIKE ?` | Rejected (insufficient) |

### 4.2 PostgreSQL JSONB Query Capabilities (Unchanged)

**Structured queries (sufficient for DevOps):**
```sql
-- Containment, existence, path extraction
SELECT * FROM infra_assets WHERE attributes @> '{"provider": "aws"}';
SELECT * FROM infra_assets WHERE attributes ? 'ip';
SELECT * FROM infra_assets WHERE (attributes->>'cpu_cores')::int >= 8;
```

**Project precedent:** `project/repository.py` already uses dialect-aware JSON containment with `session.bind.dialect.name == "postgresql"`.

---

### 4.3 SQLite Search Implementation (W1, W2 — Warnings Addressed)

**SQLite Limitation (Confirmed):** SQLite does not support JSONB operators (`@>`, `?`, `->>`) or GIN indexes. Use `json_extract()` for basic attribute filtering.

**Repository `search_assets()` Implementation:**

```python
def search_assets(self, project_id: str, query: dict, limit: int = 50, offset: int = 0) -> list[InfraAsset]:
    """
    Search infra assets with structured query.

    Query format:
        {
            "type": "server",                    # exact match on type
            "name": "web",                       # LIKE/ILIKE on name (W2)
            "parent_asset_id": "uuid-xxx",       # exact match
            "attributes": {                      # attribute filters
                "cpu_cores": {">=": 8},
                "provider": "aws"
            }
        }

    Operators supported: "=", "!=", ">", ">=", "<", "<=", "in", "contains"
    """
    with Session(self.engine) as session:
        stmt = select(InfraAsset).where(InfraAsset.project_id == project_id)

        # Type filter
        if "type" in query:
            stmt = stmt.where(InfraAsset.type == query["type"])

        # Name text search tier (W2)
        if "name" in query:
            name_pattern = f"%{query['name']}%"
            if session.bind.dialect.name == "postgresql":
                stmt = stmt.where(InfraAsset.name.ilike(name_pattern))
            else:
                stmt = stmt.where(InfraAsset.name.like(name_pattern))

        # Parent filter
        if "parent_asset_id" in query:
            stmt = stmt.where(InfraAsset.parent_asset_id == query["parent_asset_id"])

        # Attribute filters (W1, W4)
        if "attributes" in query:
            for key, condition in query["attributes"].items():
                if isinstance(condition, dict):
                    # Operator form: {"cpu_cores": {">=": 8}}
                    for op, val in condition.items():
                        if session.bind.dialect.name == "postgresql":
                            # PostgreSQL JSONB path extraction
                            col = InfraAsset.attributes[key].astext.cast(Integer)
                            if op == ">=": stmt = stmt.where(col >= val)
                            elif op == ">": stmt = stmt.where(col > val)
                            # ... other operators
                        else:
                            # SQLite: json_extract()
                            json_path = f"$.{key}"
                            # Use raw SQL with json_extract for SQLite
                            stmt = stmt.where(
                                text(f"json_extract(attributes, :path) {op} :val")
                                .bindparams(path=json_path, val=val)
                            )
                else:
                    # Exact match: {"provider": "aws"}
                    if session.bind.dialect.name == "postgresql":
                        stmt = stmt.where(InfraAsset.attributes.contains({key: condition}))
                    else:
                        # SQLite fallback: json_extract() = value
                        json_path = f"$.{key}"
                        stmt = stmt.where(
                            text("json_extract(attributes, :path) = :val")
                            .bindparams(path=json_path, val=condition)
                        )

        stmt = stmt.order_by(InfraAsset.updated_at.desc()).offset(offset).limit(limit)
        return list(session.exec(stmt))
```

**Query Interface Definition (W4):**
- **Supported operators:** `=`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `contains`
- **Attribute filter types:** `dict[str, Any]` where value is literal (exact match) or `dict[str, Any]` (operator form)
- **Text search:** `name` field uses LIKE/ILIKE (case-insensitive on PostgreSQL)
- **Pagination:** `limit` (default 50), `offset` (default 0) — S4 incorporated

---

### 4.4 parent_asset_id vs relationships JSONB (W3 — Clarified)

| Use Case | Column | Rationale |
|----------|--------|-----------|
| **Simple hierarchy** (rack belongs to datacenter, server belongs to rack) | `parent_asset_id` | Single-parent, FK-enforced, easy traversal, cascade delete |
| **Multi-parent / cross-entity links** (server belongs to multiple networks, K8s node linked to multiple clusters) | `relationships` JSONB | `{"networks": ["uuid1", "uuid2"], "k8s_nodes": [...]}` — flexible, no FK enforcement |

**Rule of thumb:** Use `parent_asset_id` when there is a clear single-parent tree. Use `relationships` for many-to-many or DAG relationships.

---

### 4.5 LightRAG Integration (Phase 3 — Deferred)

Unchanged from original. Application-layer sync via `rag_insert_text` with `workspace=project_name`. Avoid DB triggers.

---

## 5. Repository Pattern Design (Revised)

### 5.1 Repository Location & Structure

```
daemon/repositories/infra/
├── __init__.py
├── models.py          # InfraAsset, InfraAssetType, InfraAssetHistory + JSONBType
├── repository.py      # SQLModelInfraRepository (CRUD + search + history)
└── types.py           # Pydantic type definitions, INFRA_TYPE_DEFINITIONS, JSONBType
```

### 5.2 Repository Methods (Key Operations + Pagination + Audit)

```python
class SQLModelInfraRepository:
    def __init__(self, engine: Engine): ...

    # CRUD (with created_by/updated_by audit — S5)
    def create_asset(self, project_id: str, type: str, name: str, attributes: dict,
                     parent_asset_id: str | None = None, relationships: dict | None = None,
                     created_by: str | None = None) -> InfraAsset: ...

    def get_asset(self, asset_id: str) -> InfraAsset | None: ...

    def list_assets(self, project_id: str, type: str | None = None,
                    parent_asset_id: str | None = None, limit: int = 50, offset: int = 0) -> list[InfraAsset]: ...

    def update_asset(self, asset_id: str, updated_by: str | None = None, **updates) -> InfraAsset | None: ...

    def delete_asset(self, asset_id: str, deleted_by: str | None = None) -> bool: ...

    # Search (structured + text tier + pagination)
    def search_assets(self, project_id: str, query: dict, limit: int = 50, offset: int = 0) -> list[InfraAsset]: ...

    # Type registry (GLOBAL)
    def register_type(self, name: str, schema_json: dict | None, description: str | None) -> InfraAssetType: ...
    def list_types(self) -> list[InfraAssetType]: ...

    # History / Versioning
    def get_history(self, asset_id: str, limit: int = 20, offset: int = 0) -> list[InfraAssetHistory]: ...
    def record_change(self, asset_id: str, change_type: str, changed_fields: list[str] | None = None,
                      old_values: dict | None = None, new_values: dict | None = None,
                      changed_by: str | None = None, snapshot: dict | None = None) -> InfraAssetHistory: ...
```

**Dialect Helpers:** Reuse `_get_dialect_insert` and JSON containment patterns from `project/repository.py`. Add SQLite `json_extract()` path for `search_assets()`.

---

## 6. Migration Plan (Revised with Phase 0 + Tests + S2)

1. **Phase 0 (Schema-Only):** Create `20260616_000001_create_infra_assets_table.sql` — tables, constraints, indexes. No application code.
2. Add `create_infra_repository(engine)` factory in `daemon/repositories/factory.py`
3. Add `self._infra_repository = create_infra_repository(engine=self._engine, create_tables=False)` in `daemon/manager.py`
4. Create `daemon/tools/infra.py` with `create_infra_tools(repository, current_instance_id, agent_id)`
5. Wire `create_infra_tools` into `daemon/tools/instance.py:create_instance_tools`
6. Seed `infra_asset_types` at startup (global)
7. Implement versioning/history recording in repository `create/update/delete`
8. Write tests for repository (CRUD, search, history, dialect guards) — S2 incorporated
9. (Phase 3) Add RAG indexing hooks

---

## 7. DevOps Agent Integration (Revised)

### 7.1 Complete Tool Inventory (C3 — `infra_type_register` Added)

- `infra_asset_create(type, name, attributes, parent_asset_id?, project_id?, created_by?)`
- `infra_asset_get(id)`
- `infra_asset_list(type?, project_id?, limit?, offset?)`
- `infra_asset_update(id, attributes_patch, updated_by?)`
- `infra_asset_delete(id, deleted_by?)`
- `infra_type_list()` — list global types
- `infra_type_register(name, schema_json?, description?)` — **ADDED (C3)**
- `infra_search(query_dict, limit?, offset?)` — structured search
- `infra_asset_history(asset_id, limit?, offset?)` — view change history

### 7.2 Context Injection for DevOps (C4 — Critical Fix)

**Problem:** `get_shared_context()` is Explorer-only (filesystem context directory). It is not designed for DevOps infra context.

**Recommended Solution:** DevOps agent loads infra context **directly via dedicated tools**:

- `infra_list(project_id?, type?)` — enumerate assets in current project
- `infra_search(query_dict)` — structured search (e.g., "all servers with >= 8 CPU cores")
- `infra_asset_get(id)` — fetch specific asset details

**Alternative (Future Enhancement):** Preload a lightweight infra summary into the agent's system prompt or shared context file at instance spawn time (e.g., "This project has 3 datacenters, 12 K8s clusters, 45 servers"). This is a **separate mechanism** from `get_shared_context()`.

**Do NOT** route DevOps infra context through `get_shared_context()`.

---

### 7.3 Workflow Integration (Unchanged)

DevOps `workflow.md` Step 1 routes `infrastructure` / `Docker` / `K8s` / `Terraform` → DevOps. Infra tools enable environment queries before `terraform apply` and post-deploy recording.

---

## 8. Phased Implementation Plan (Revised with Tests + S2)

| Phase | Scope | Deliverables | Est. Time |
|-------|-------|--------------|-----------|
| **0** | Schema-only | Migration file with 3 tables, UNIQUE constraint, GIN indexes via `__table_args__`, JSONBType column type | 0.5 day |
| **1** | Core + Versioning | Models, repository (CRUD + search + history), factory wiring, InstanceManager singleton, SQLite `json_extract()` path, tests | 2 days |
| **1.5** | Type registry | `infra_asset_types` global seeding, `infra_type_register` tool, Pydantic definitions, tests | 0.5 day |
| **2** | DevOps integration | `daemon/tools/infra.py`, tool wiring, DevOps `tools.allow` update, dedicated context loading via `infra_*` tools, tests | 1.5 days |
| **3** | Semantic search | LightRAG indexing of infra docs, `rag_insert_text` on asset create/update, DevOps semantic query tool | 2-3 days |
| **4** | Polish | Full-text `search_text` column, advanced relationship queries, UI/CLI | Deferred |

**Total (Phases 0-2):** ~4.5 days including tests (S2)

---

## 9. Risks & Mitigations (Revised)

| Risk | Impact | Mitigation |
|------|--------|------------|
| **SQLite GIN index syntax error** | Migration fails on SQLite | **C1 Fix:** GIN indexes via `__table_args__` with `postgresql_using="gin"` — SQLAlchemy skips on SQLite |
| **Column(JSON) ≠ JSONB on PG** | `@>`, `?`, GIN operators fail | **C2 Fix:** `JSONBType` TypeDecorator maps to `JSONB` on PostgreSQL |
| **Weak typing leads to inconsistent attributes** | DevOps queries fail due to schema drift | Enforce via Pydantic validation in tool layer; `infra_asset_types.schema_json` for runtime validation |
| **Relationship cycles / orphans** | Data integrity issues | Application-level validation (no DB constraint for DAGs); `parent_asset_id` FK prevents simple orphans |
| **RAG sync complexity (Phase 3)** | Infra docs out of sync with `infra_assets` | Application-layer hook in repository `create/update`; avoid DB triggers |
| **Project_id isolation leak** | Cross-project infra data exposure | All queries MUST filter by `project_id`; repository enforces via FK + query patterns |
| **JSONB query dialect divergence** | SQLite vs PG behavior mismatch | Reuse dialect guard pattern; explicit `json_extract()` path for SQLite in `search_assets()` (W1) |
| **Versioning table bloat** | History grows unbounded | Application-level retention policy (e.g., keep last N changes per asset, or archive after 90 days) |

---

## 10. Success Criteria (Revised)

- [ ] Phase 0 migration creates 3 tables with UNIQUE constraint, GIN indexes via `__table_args__`, and `JSONBType` column type
- [ ] Repository implements CRUD + structured search (with `json_extract()` for SQLite) + history recording
- [ ] DevOps agent can create/query infra assets and register types via new tools
- [ ] Project isolation enforced (all queries scoped by `project_id`)
- [ ] Type registry is GLOBAL (no project_id, shared across all projects)
- [ ] Versioning/history table records create/update/delete with snapshots, changed_fields, old/new values, changed_by
- [ ] (Phase 3) LightRAG semantic search works on infra documentation within project workspace

---

## Appendix: Open Questions (Updated)

**RESOLVED (per user decisions):**

1. **Should `infra_asset_types` be per-project or global?** → **RESOLVED: GLOBAL** (shared across all projects, no project_id column)

2. **Should we support versioning of infra asset attributes (history of changes)?** → **RESOLVED: BUILD NOW** (dedicated `infra_asset_history` table with snapshots, change_type, changed_fields, old/new values, changed_by instance_id)

**Still Open:**

3. How deep should hierarchy validation go (prevent cycles in `parent_asset_id` + `relationships`)? (Recommendation: simple FK + application-level DAG check on write)

4. Should `attributes` be validated against `infra_asset_types.schema_json` at insert/update time? (Recommendation: yes, in tool layer, optional strict mode)

**Additional Open Questions (S6):**

5. Should `infra_asset_history` also track relationship changes (parent_asset_id, relationships JSONB) or only attribute changes?

6. Should we provide a bulk import tool (e.g., `infra_import_from_terraform_state`) for seeding infra data from existing IaC state files?

---

**Document Status:** Revised and finalized. All user decisions, critical fixes (C1-C4), warnings (W1-W4), and suggestions (S1-S6) incorporated. Ready for implementation planning.
