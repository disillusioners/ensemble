# Phase 0 + Phase 1 Implementation Detail — Project Blueprint

Date: 2026-08-02
Author: planner[v2] via plan-creation worker
Status: Draft
Parent contract: `.agents/shared/planning/project-blueprint/plan-overview.md` (Final architecture)
Scope: Phase 0 (Contract Spike) + Phase 1 (DB Schema + Matching Engine) ONLY.

---

## 0. Conformance Notes — How This Section Maps to the Locked Overview

This plan implements **Section 4 (Data Model)**, **Section 5 (Multi-Algorithm Matching Engine)**, **Section 12 Phase 0**, and **Section 12 Phase 1** of `plan-overview.md`. The architectural decisions (D1–D14) are locked and not re-litigated here. Implementation-level specifics (exact SQLModel field types, fusion formula, prompt templates, file paths) are the contribution of this document.

**Research-grounded constraints that override any ambiguity in the overview:**

1. **No pgvector / no tsvector installed.** The overview Section 4.4 says "standard PostgreSQL extensions for BM25-style scoring and pgvector (or equivalent)". The reality (verified in `daemon/repositories/skill/models.py:563-620` and `daemon/repositories/infra/types.py`) is that embeddings today are stored as **JSONB arrays of float** via `JSONBType`, and BM25 is **pure-Python in-memory** (`daemon/services/skill_search_service.py:123-185`). Blueprint MUST follow the same approach — do NOT assume pgvector.
2. **New tables use SQLModel `table=True`** registered via `daemon/repositories/__init__.py`; `SQLModel.metadata.create_all()` handles them on fresh + existing backends. NO `.sql` migration, NO `_ensure_postgres_columns` for brand-new tables.
3. **Repositories** live in `daemon/repositories/<name>/repository.py`, accept an `Engine` in `__init__`, session-per-method, synchronous, callers bridge via `asyncio.to_thread`.
4. The overview's `build_blueprint_query(task_message, task_context, skill_content)` (Section 5.3.1) is the **target** signature. Phase 1 ships a reduced `build_blueprint_query(query, context=None)` because task_context and skill_content are not threaded to the injection hook in Phase 1 scope. The three-signal form is realized in Phase 2. This limitation is documented inline and is the single deliberate scope gap.

---

## PHASE 0 — Contract Spike

### P0.1 Objective

Validate, offline and on real data, that the BM25 + vector fusion over pre-generated trigger queries produces acceptable recall **before** committing to the production schema and engine in Phase 1. The spike de-risks decisions O1 (threshold value) and O2 (fusion weights α, β) enough to seed Phase 1 defaults; final calibration is Phase 6.

**Exit criteria (from overview Section 12, Phase 0):**
- Multi-algorithm recall ≥ 80% top-1 on the curated sample set.
- A threshold value is chosen and documented.
- A fusion-weight pair (α, β) is chosen and documented.

### P0.2 Deliverable Artifacts

| Artifact | Path | Purpose |
|---|---|---|
| Spike script | `scripts/blueprint_contract_spike.py` | Self-contained, runnable offline; loads seed blueprints, runs the pipeline against the query set, prints + writes metrics. |
| Seed blueprints (3–5) | `scripts/blueprint_seed/core.md` + 2–4 area blueprints | Hand-curated, conform to overview Section 3 (200–500 words, file refs required). |
| Sample queries | `scripts/blueprint_seed/queries.json` | 5–10 real-ish agent task messages with the expected-match blueprint id annotated. |
| Results file (THE deliverable) | `scripts/blueprint_seed/spike_results.md` | Chosen threshold, α, β, per-query scoring, pass/fail vs exit criteria. |

`scripts/` is the project's existing convention for standalone tooling (see `migrate_memory_to_rag.py`, `remediate_pause_report_orphans.py` already there). The `blueprint_seed/` subdirectory keeps the spike's data isolated from production code.

### P0.3 Procedure

**Step 1 — Curate seed blueprints (3–5 incl. `core.md`).**

Write 3–5 blueprints for THIS project (agents-ensemble), since the spike must run on realistic content. Recommended set:

| File | kind | What it captures (source: real project knowledge) |
|---|---|---|
| `core.md` | `core` | Tech stack (Python, LangGraph, FastAPI, PostgreSQL+SQLite), top-level dirs (`daemon/`, `agents/`, `frontend/`), entry point (`dev.sh` → port 8079), key invariants (JAFP, persistent block, checkpoint). |
| `job-queue.md` | `area` | 7-state lifecycle, `system_parallel_queue` vs `system_fifo_queue`, JobItem as public primitive, PG trigger skips message jobs. |
| `context-injection.md` | `area` | `assemble_context_messages()` persistent block, skill injection 3-stage, critical notes, shared context, match-once-per-instance. |
| `skill-system.md` | `area` | Skill evolution, BM25+embedding+LLM pipeline, `SkillEmbeddingService`, A/B testing. |
| `db-repositories.md` | `area` | Repository pattern, SQLModel `table=True`, `JSONBType`, `_ensure_postgres_columns` for new columns on existing tables only. |

Each blueprint:
- 200–500 words (core.md 300–500).
- Declares `file_refs` (the canonical deepening path — overview Section 3.2), e.g. `{"path": "daemon/services/context_messages.py", "function": "assemble_context_messages"}`.
- Declares `tags` (3–6 short keywords).
- Declares `trigger_queries` (3–10) — written by hand for the spike (Phase 1 will generate via LLM). These simulate the LLM output so the spike tests the **matching** logic, not the generation quality.

**Step 2 — Curate the query set (5–10).**

`queries.json` shape:
```json
[
  {
    "query": "Fix the job queue pause deadlock where the cross-system guard blocks its own JobItem",
    "expected_blueprint_ids": ["db-id-of-job-queue", "db-id-of-context-injection"],
    "notes": "real recent task (Guard self-deadlock hotfix)"
  }
]
```
- 5–10 entries, drawn from real recent project history (the history list in the system context is a good source).
- `expected_blueprint_ids` is the **ground truth** the human curator asserts should match. Multi-label allowed (a task can legitimately match 2 blueprints).
- Include at least 1 **paraphrased** query (different vocabulary than the blueprint content) to test trigger-query recall, and at least 1 **unrelated** query (expected: only core.md / no area match) to test the threshold gate.

**Step 3 — Implement the spike script.**

`scripts/blueprint_contract_spike.py` is a **pure-Python script** that re-implements the fusion inline (it does NOT import the Phase 1 service — Phase 1 doesn't exist yet; the spike is the proof the Phase 1 design works). Structure:

```python
# scripts/blueprint_contract_spike.py
# Loads seed blueprints from scripts/blueprint_seed/*.md (front-matter + body),
# runs BM25 + vector fusion against queries.json, reports metrics.

# Imports the REAL embedding service (daemon.services.skill_embedding_service.SkillEmbeddingService)
#   — reuse, don't reimplement. Construct it with a SkillEvolutionConfig + a throwaway
#   in-memory engine (SQLite) + a SkillEmbeddingRepository so we exercise the real embed path.
# Reuses _tokenize + _bm25_score by importing from daemon.services.skill_search_service
#   (they are module-level functions — importable directly).
```

Pipeline per query (mirrors Phase 1 §1c):
1. Build candidate corpus: each blueprint's `content + " " + " ".join(trigger_queries) + " " + name + " " + " ".join(tags)`.
2. BM25 score (reuse `_bm25_score` from `skill_search_service`) → normalize via min-max across the candidate set for this query.
3. Vector score: embed query once via `SkillEmbeddingService.embed_text`; embed each trigger_query once at load; per-blueprint vector score = max cosine over its trigger queries (mirror `_embedding_rerank` aggregation).
4. Fusion: `final = α * bm25_norm + β * vector_score`.
5. Gate: drop candidates with `final < threshold`; keep top-4 area blueprints; core.md always passes through separately.

**Step 4 — Sweep α, β, threshold and capture metrics.**

The script iterates a small grid:
- `α ∈ {0.3, 0.4, 0.5}`, `β = 1 - α` (independent weights add complexity without spike value; the constrained form `α+β=1` is recommended and justified in §1c).
- `threshold ∈ {0.20, 0.25, 0.30, 0.35, 0.40}`.

For each (α, β, threshold) combo, compute over the query set:
- **Top-1 accuracy**: fraction of queries where the highest-scoring area blueprint is in `expected_blueprint_ids`.
- **Top-4 coverage**: fraction of `expected_blueprint_ids` (union across queries) that appear in the top-4 matches.
- **No-match rate**: fraction of queries where zero area blueprints clear threshold (core.md-only fallback).
- **False-positive rate**: fraction of injected area blueprints NOT in any expected set (noise indicator).

**Step 5 — Document the chosen values in `spike_results.md`.**

The deliverable records the (α, β, threshold) triple that maximizes top-1 accuracy subject to no-match-rate ≤ 20% and false-positive-rate ≤ 15%. These become Phase 1's `BlueprintConfig` defaults (see §1c). If no triple clears the 80% top-1 exit bar, the spike FAILS and the plan pauses for design review (do not proceed to Phase 1 with an unvalidated matcher).

### P0.4 Exit Criteria Checklist (Phase 0)

- [ ] ≥ 3 seed blueprints (incl. core.md) written to `scripts/blueprint_seed/`.
- [ ] 5–10 queries with expected-match annotations in `queries.json`.
- [ ] `scripts/blueprint_contract_spike.py` runs end-to-end without errors.
- [ ] `spike_results.md` records: chosen α, chosen β, chosen threshold, per-query scores, top-1 accuracy (≥ 80%).
- [ ] If exit bar not met: documented failure + the specific failure mode (e.g. "trigger queries too generic", "vector dominates short queries").

---

## PHASE 1 — DB Schema + Matching Engine

### P1.1 Objective

Persistent storage (4 tables) and the production matching engine (`BlueprintMatcher`), with trigger-query generation and core.md reserved-slot logic. This phase delivers the **non-injection** substrate: Phase 2 wires it into `assemble_context_messages()`.

**Exit criteria (from overview Section 12, Phase 1):**
- Matching service passes unit tests.
- Trigger-query generation produces useful (3–10) queries on sample blueprint content.
- Threshold gate uses the Phase-0-chosen default.

### P1.2 New Files (Phase 1 surface)

```
daemon/repositories/blueprint/
├── __init__.py            # imports models so SQLModel.metadata.create_all registers tables
├── models.py              # 4 SQLModel table classes
└── repository.py          # class BlueprintRepository
daemon/services/
├── blueprint_matcher.py   # class BlueprintMatcher + MatchedBlueprint dataclass
└── blueprint_query.py     # build_blueprint_query() + trigger-query generation helper
daemon/
└── config.py              # + BlueprintConfig (new section)
daemon/
└── manager.py             # wiring: _blueprint_repo, _blueprint_matcher (parallel to skill services)
daemon/repositories/
└── __init__.py            # register blueprint models + repository + factory fns
tests/
└── (new test files — see P1.7)
```

### P1.3 Database Schema — `daemon/repositories/blueprint/models.py`

Four SQLModel `table=True` classes. **No `.sql` migration** — the models register via `daemon/repositories/blueprint/__init__.py` → `daemon/repositories/__init__.py`, and `SQLModel.metadata.create_all()` (called from `daemon/manager.py`) creates them on every backend. This is the established pattern for brand-new tables (see the `instance_ui_prefs` precedent documented at `daemon/repositories/__init__.py:84-91`).

**Tag storage decision — inline JSONB (NOT a separate tags table).** The existing skill system has NO tags table (`daemon/repositories/skill/models.py` has no tag model). Tags live inline as a JSONB list on the parent row, using `JSONBType` from `daemon/repositories/infra/types.py:35-89` (the dual-driver JSONB decorator that works on SQLite + PostgreSQL). Blueprint follows the same precedent: a separate `BlueprintTag` table adds a join cost and a maintenance surface (cascading deletes, dedup) for zero query benefit — no query in Phase 1 filters on a single tag value. **Recommendation: inline JSONB `tags` on `Blueprint`. Drop `BlueprintTag`.** This reduces the table count from 4 to 3 and matches the skill-system precedent exactly. (The task brief lists `BlueprintTag` as a candidate; the skill-system precedent resolves it.)

The 3 tables are therefore: `Blueprint`, `BlueprintEmbedding`, `BlueprintRevision`.

#### P1.3.1 `Blueprint` (table `project_blueprints`)

Mirrors `Skill` (`daemon/repositories/skill/models.py:81-238`) in structure: UUID-str PK, ISO-8601 string timestamps, soft-delete via `is_active`, JSONB for structured columns.

```python
# daemon/repositories/blueprint/models.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Blueprint(SQLModel, table=True):
    """A blueprint document — the atomic unit of the Blueprint system.

    Lifecycle parallels :class:`Skill`: inserted (kind='core'|'area'),
    updated (version bumped, revision row appended), soft-deleted
    (is_active=False). Manual edits set source='manual'; auto edits
    set source='auto'. Per overview D7/D10, at most 1 'core' blueprint
    per project is expected (enforced at the tool layer, not the DB,
    to keep migrations trivial).
    """

    __tablename__ = "project_blueprints"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", name="uq_project_blueprints_project_name"
        ),
        Index("ix_project_blueprints_project_id", "project_id"),
        Index("ix_project_blueprints_kind", "kind"),
        Index("ix_project_blueprints_is_active", "is_active"),
        # Composite index for the core.md reserved-slot lookup:
        # WHERE project_id = ? AND kind = 'core' AND is_active = true.
        Index(
            "ix_project_blueprints_project_kind_active",
            "project_id", "kind", "is_active",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    kind: str = Field(default="area", max_length=16)  # 'core' | 'area'
    content: str = Field(sa_column=Column(Text, nullable=False))
    # file_refs: list of {"path": str, "function"?: str, "line"?: int}
    file_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column("file_refs", JSONBType, nullable=False),
    )
    # tags: inline JSONB list (parallels Skill — no separate tags table)
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column("tags", JSONBType, nullable=False),
    )
    # trigger_queries: LLM-generated at create/update; stored on the row
    # so the matcher can include them in the BM25 corpus without a join.
    trigger_queries: list[str] = Field(
        default_factory=list,
        sa_column=Column("trigger_queries", JSONBType, nullable=False),
    )
    version: int = Field(default=1)
    is_active: bool = Field(default=True)
    source: str = Field(default="auto", max_length=16)  # 'auto' | 'manual'

    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "kind": self.kind,
            "content": self.content,
            "file_refs": list(self.file_refs) if self.file_refs else [],
            "tags": list(self.tags) if self.tags else [],
            "trigger_queries": list(self.trigger_queries) if self.trigger_queries else [],
            "version": self.version,
            "is_active": self.is_active,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

**Field notes:**
- `kind` is a plain `str` (not a DB enum) — value validation lives in the repository/tool layer, matching how `Skill.status` / `Skill.lineage_origin` are handled (`daemon/repositories/skill/models.py:172-173`).
- `file_refs`, `tags`, `trigger_queries` use `JSONBType` — the exact same decorator as `SkillEmbedding.embedding` and `SkillTrigger.condition_json`. Works on SQLite + PostgreSQL without per-dialect forks.
- `UniqueConstraint("project_id", "name")` — prevents duplicate blueprint names within a project. Mirrors `uq_skills_project_name_gen`.
- The composite index `ix_project_blueprints_project_kind_active` serves the `get_core(project_id)` lookup (the hot path, since core.md is injected on every first message).

#### P1.3.2 `BlueprintEmbedding` (table `project_blueprint_embeddings`)

Direct structural parallel of `SkillEmbedding` (`daemon/repositories/skill/models.py:563-620`). One row per trigger query; embedding stored as JSONB list[float].

```python
class BlueprintEmbedding(SQLModel, table=True):
    """Cached per-blueprint embedding of one trigger query.

    Structural mirror of :class:`SkillEmbedding`. The embedding column
    is a plain JSON array of floats via :class:`JSONBType` — NOT BYTEA,
    NOT pickle, NOT pgvector — so the same schema works on SQLite and
    PostgreSQL (the project standard, verified in
    daemon/repositories/skill/models.py:570-576).

    One row per trigger query. At match time, the query is embedded
    once and compared (cosine) against all rows for a blueprint; the
    per-blueprint score is the MAX similarity across its trigger
    queries (mirrors SkillSearchService._embedding_rerank aggregation
    at daemon/services/skill_search_service.py:632-638).
    """

    __tablename__ = "project_blueprint_embeddings"
    __table_args__ = (
        Index("ix_project_blueprint_embeddings_blueprint_id", "blueprint_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    blueprint_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("project_blueprints.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    trigger_query: str = Field(sa_column=Column(String, nullable=False), max_length=512)
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "blueprint_id": self.blueprint_id,
            "trigger_query": self.trigger_query,
            "embedding": list(self.embedding) if self.embedding else [],
            "created_at": self.created_at,
        }
```

#### P1.3.3 `BlueprintRevision` (table `project_blueprint_revisions`)

Append-only history. Mirrors the conceptual model in overview Section 4.3. Fields trimmed to what Phase 1 + the Phase 3 revision-history API need.

```python
class BlueprintRevision(SQLModel, table=True):
    """Append-only revision snapshot for a blueprint.

    One row per revision. The full content + structured fields are
    snapshotted so the Phase 3 revision-history endpoint can render
    diffs without joining historical state from elsewhere. ``change_source``
    is a plain string (validation at the tool layer), paralleling
    ``Skill.lineage_origin``.
    """

    __tablename__ = "project_blueprint_revisions"
    __table_args__ = (
        Index("ix_project_blueprint_revisions_blueprint_id", "blueprint_id"),
        Index(
            "ix_project_blueprint_revisions_blueprint_version",
            "blueprint_id", "version",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    blueprint_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("project_blueprints.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: int = Field(nullable=False)
    content_snapshot: str = Field(sa_column=Column(Text, nullable=False))
    file_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column("file_refs", JSONBType, nullable=False),
    )
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column("tags", JSONBType, nullable=False),
    )
    trigger_queries: list[str] = Field(
        default_factory=list,
        sa_column=Column("trigger_queries", JSONBType, nullable=False),
    )
    change_source: str = Field(default="auto", max_length=16)  # 'auto' | 'manual' | 'rollback'
    changed_by: Optional[str] = Field(default=None, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=512)
    changed_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "blueprint_id": self.blueprint_id,
            "version": self.version,
            "content_snapshot": self.content_snapshot,
            "file_refs": list(self.file_refs) if self.file_refs else [],
            "tags": list(self.tags) if self.tags else [],
            "trigger_queries": list(self.trigger_queries) if self.trigger_queries else [],
            "change_source": self.change_source,
            "changed_by": self.changed_by,
            "reason": self.reason,
            "changed_at": self.changed_at,
        }
```

#### P1.3.4 `daemon/repositories/blueprint/__init__.py`

```python
"""Blueprint repository package.

Importing the models here registers them with ``SQLModel.metadata``
so ``SQLModel.metadata.create_all()`` (called from ``daemon/manager.py``)
creates the three new tables on every backend. Brand-new tables need
NO ``.sql`` migration and NO ``_ensure_postgres_columns`` entry — same
precedent as ``instance_ui_prefs`` (daemon/repositories/__init__.py:84-91).
"""
from .models import Blueprint, BlueprintEmbedding, BlueprintRevision
from .repository import BlueprintRepository

__all__ = [
    "Blueprint",
    "BlueprintEmbedding",
    "BlueprintRevision",
    "BlueprintRepository",
]
```

Registration in `daemon/repositories/__init__.py` — add a block parallel to the `instance_ui_prefs` block (lines 84-91), and add the names to `__all__`:

```python
# Blueprint repository (Project Blueprint system).
# Imported here so SQLModel.metadata.create_all() registers the three
# new tables (project_blueprints, project_blueprint_embeddings,
# project_blueprint_revisions). Brand-new tables — no _ensure_postgres_columns.
from .blueprint.models import Blueprint, BlueprintEmbedding, BlueprintRevision
from .blueprint.repository import BlueprintRepository
```

### P1.4 Repository — `daemon/repositories/blueprint/repository.py`

Structural mirror of `InstanceUiPrefsRepository` (`daemon/repositories/instance_ui_prefs/repository.py:42-59`) and `SkillEmbeddingRepository` (`daemon/repositories/skill/repository.py:1814-1956`): accept `Engine` in `__init__`, session-per-method, synchronous, no base class.

```python
# daemon/repositories/blueprint/repository.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import Blueprint, BlueprintEmbedding, BlueprintRevision

logger = logging.getLogger(__name__)


class BlueprintRepository:
    """SQLModel-based repository for the project_blueprints family of tables.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` (same pattern as SkillEmbeddingRepository,
    daemon/repositories/skill/repository.py:1814-1820).
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---- READ -------------------------------------------------

    def get_by_id(self, blueprint_id: str) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            return session.exec(
                select(Blueprint).where(Blueprint.id == blueprint_id)
            ).first()

    def get_by_name(
        self, project_id: str, name: str
    ) -> Optional[Blueprint]:
        with Session(self.engine) as session:
            return session.exec(
                select(Blueprint).where(
                    Blueprint.project_id == project_id,
                    Blueprint.name == name,
                )
            ).first()

    def get_core(self, project_id: str) -> Optional[Blueprint]:
        """Return the active 'core' blueprint for the project, or None.

        Hot path: invoked on every first-message receipt. Backed by the
        composite index ix_project_blueprints_project_kind_active.
        """
        with Session(self.engine) as session:
            return session.exec(
                select(Blueprint).where(
                    Blueprint.project_id == project_id,
                    Blueprint.kind == "core",
                    Blueprint.is_active == True,  # noqa: E712
                )
            ).first()

    def get_by_project(
        self,
        project_id: str,
        kind: Optional[str] = None,
        active_only: bool = True,
    ) -> list[Blueprint]:
        with Session(self.engine) as session:
            stmt = select(Blueprint).where(Blueprint.project_id == project_id)
            if kind is not None:
                stmt = stmt.where(Blueprint.kind == kind)
            if active_only:
                stmt = stmt.where(Blueprint.is_active == True)  # noqa: E712
            return list(session.exec(stmt))

    # ---- WRITE ------------------------------------------------

    def create(self, **fields: Any) -> Blueprint:
        """Insert a new blueprint. Caller validates kind/source values."""
        row = Blueprint(**fields)
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def update(self, blueprint_id: str, **fields: Any) -> Optional[Blueprint]:
        """Partial update. Bumps version + updated_at if content-shaped fields change."""
        with Session(self.engine) as session:
            row = session.exec(
                select(Blueprint).where(Blueprint.id == blueprint_id)
            ).first()
            if row is None:
                return None
            content_shaped = {"content", "file_refs", "tags", "trigger_queries"}
            bump_version = any(k in content_shaped for k in fields)
            for k, v in fields.items():
                setattr(row, k, v)
            if bump_version:
                row.version = (row.version or 1) + 1
            row.updated_at = self._now_iso()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def soft_delete(self, blueprint_id: str) -> bool:
        with Session(self.engine) as session:
            row = session.exec(
                select(Blueprint).where(Blueprint.id == blueprint_id)
            ).first()
            if row is None:
                return False
            row.is_active = False
            row.updated_at = self._now_iso()
            session.add(row)
            session.commit()
            return True

    # ---- REVISIONS --------------------------------------------

    def add_revision(self, **fields: Any) -> BlueprintRevision:
        row = BlueprintRevision(**fields)
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def list_revisions(
        self, blueprint_id: str, limit: int = 50
    ) -> list[BlueprintRevision]:
        with Session(self.engine) as session:
            stmt = (
                select(BlueprintRevision)
                .where(BlueprintRevision.blueprint_id == blueprint_id)
                .order_by(BlueprintRevision.version.desc())
            )
            return list(session.exec(stmt).limit(limit))

    # ---- EMBEDDINGS -------------------------------------------
    # Mirrors SkillEmbeddingRepository (daemon/repositories/skill/repository.py:1814-1956).

    def add_embedding(
        self, blueprint_id: str, trigger_query: str, embedding: list[float]
    ) -> BlueprintEmbedding:
        row = BlueprintEmbedding(
            blueprint_id=blueprint_id,
            trigger_query=trigger_query,
            embedding=list(embedding),
            created_at=self._now_iso(),
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def get_embeddings(self, blueprint_id: str) -> list[BlueprintEmbedding]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintEmbedding).where(
                        BlueprintEmbedding.blueprint_id == blueprint_id
                    )
                )
            )

    def replace_embeddings(
        self, blueprint_id: str, items: list[tuple[str, list[float]]]
    ) -> int:
        """Delete all embeddings for a blueprint, then insert the new set.

        Used when trigger_queries change: recompute embeddings for all of
        them and replace atomically. Parallels SkillEmbeddingRepository.delete_by_skill
        + create (raw-SQL DELETE because SQLModel's delete() is awkward for
        batched deletes — same rationale as
        daemon/repositories/skill/repository.py:1881-1906).
        """
        with Session(self.engine) as session:
            session.execute(
                text(
                    "DELETE FROM project_blueprint_embeddings "
                    "WHERE blueprint_id = :bid"
                ),
                {"bid": blueprint_id},
            )
            for trigger_query, embedding in items:
                session.add(
                    BlueprintEmbedding(
                        blueprint_id=blueprint_id,
                        trigger_query=trigger_query,
                        embedding=list(embedding),
                        created_at=self._now_iso(),
                    )
                )
            session.commit()
        return len(items)

    # ---- MATCH CANDIDATE LOADER -------------------------------

    def search_candidates(
        self, project_id: str
    ) -> list[tuple[Blueprint, list[BlueprintEmbedding]]]:
        """Load all active area blueprints for a project + their embeddings.

        Single round-trip-friendly batch load for the matcher. Returns
        (blueprint, embeddings) pairs. 'core' blueprints are EXCLUDED —
        core.md is handled separately by the matcher (reserved slot 1).
        Parallels SkillEmbeddingRepository.get_all_for_project in intent
        but returns embeddings already grouped per blueprint.
        """
        with Session(self.engine) as session:
            bps = list(
                session.exec(
                    select(Blueprint).where(
                        Blueprint.project_id == project_id,
                        Blueprint.kind == "area",
                        Blueprint.is_active == True,  # noqa: E712
                    )
                )
            )
            if not bps:
                return []
            bp_ids = [b.id for b in bps]
            embs = list(
                session.exec(
                    select(BlueprintEmbedding).where(
                        col(BlueprintEmbedding.blueprint_id).in_(bp_ids)
                    )
                )
            )
        by_bp: dict[str, list[BlueprintEmbedding]] = {b.id: [] for b in bps}
        for e in embs:
            if e.blueprint_id in by_bp:
                by_bp[e.blueprint_id].append(e)
        return [(b, by_bp[b.id]) for b in bps]
```

### P1.5 Config — `BlueprintConfig` (new section in `daemon/config.py`)

Parallels `SkillEvolutionConfig` (`daemon/config.py:473-521`). Defaults seeded from Phase 0's `spike_results.md`. If Phase 0 hasn't run, use the conservative defaults below and let Phase 6 tune.

```python
# daemon/config.py — new section, registered on Config (parallel to skill_evolution)
class BlueprintConfig(BaseSettings):
    """Configuration for the Project Blueprint system.

    Embedding settings mirror SkillEvolutionConfig — Blueprint reuses the
    same embedding model/endpoint by default but can be overridden. The
    threshold + fusion weights are seeded from Phase 0 calibration
    (scripts/blueprint_seed/spike_results.md) and tuned in Phase 6.
    """

    model_config = SettingsConfigDict(env_prefix="BLUEPRINT_")

    # Embedding — defaults fall through to the skill-system embedding config
    # so Blueprint and Skill share the same vector space. Override per-deploy.
    embedding_model: str | None = Field(default=None)  # None => reuse skill embedding_model
    embedding_base_url: str | None = Field(default=None)
    embedding_api_key: str | None = Field(default=None)

    # Matching
    max_area: int = Field(default=4)           # slots 2-5
    threshold: float = Field(default=0.30)     # seeded from Phase 0; tuned Phase 6
    bm25_alpha: float = Field(default=0.4)     # α in fusion; β = 1 - α
    bm25_top_k: int = Field(default=10)        # BM25 candidate cap before vector stage
    embedding_beta: float = Field(default=0.6)  # β = 1 - α; kept explicit for clarity

    # Trigger-query generation
    min_trigger_queries: int = Field(default=3)
    max_trigger_queries: int = Field(default=10)
    trigger_query_max_chars: int = Field(default=200)
    trigger_gen_model: str | None = Field(default=None)  # None => reuse main LLM model
```

Add to `Config` (`daemon/config.py:568-583`), parallel to `skill_evolution`:
```python
blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)
```

**None-stripping in `load_config()`** — add a `"blueprint"` block mirroring the existing `skill_evolution` None-stripping at `daemon/config.py:677-691`. This strips `None` values from env-loaded config so that `Field(default=None)` fields don't clobber the explicit defaults when the env var is unset:

```python
if "blueprint" in processed_config:
    bp = processed_config["blueprint"]
    bp = {k: v for k, v in bp.items() if v is not None}
    processed_config["blueprint"] = bp
```

### P1.6 Matching Engine — `daemon/services/blueprint_matcher.py`

#### P1.6.1 `MatchedBlueprint` dataclass

```python
# daemon/services/blueprint_matcher.py
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from daemon.services.skill_search_service import _bm25_score, _tokenize
# ^ Reuse the EXACT BM25 + tokenizer already proven in production.
#   Module-level functions in skill_search_service.py:96-185 — importable directly.

logger = logging.getLogger(__name__)


@dataclass
class MatchedBlueprint:
    """One blueprint selected by the matcher for injection.

    Fields aligned with master-plan invariant C3
    (id, name, kind, version, content, file_refs, score). `lineage`
    and `rank` are extra metadata for logging/analysis, not part of
    the C3 contract.
    """
    id: str
    name: str
    kind: str = "area"             # 'core' | 'area'
    content: str = ""
    file_refs: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: int = 1
    score: float = 0.0
    lineage: str = "matched"       # 'core' | 'matched' — extra, for logging
    rank: int = 0                  # extra, for logging
```

#### P1.6.2 `BlueprintMatcher` class

```python
class BlueprintMatcher:
    """Multi-algorithm blueprint matcher: BM25 + vector fusion + threshold gate.

    Architectural parallel of SkillSearchService
    (daemon/services/skill_search_service.py:258-641) with TWO differences
    mandated by the locked overview (D13):
      1. NO LLM rerank stage (deferred to Phase 6).
      2. core.md is ALWAYS included separately (reserved slot 1).

    Constructor dependencies are duck-typed (Any) so this service is
    unit-testable with lightweight mocks, exactly like SkillSearchService
    (daemon/services/skill_search_service.py:261-268).

    Attributes:
        _repo: BlueprintRepository. Expected methods: get_core(project_id),
            search_candidates(project_id).
        _embedding_service: object with async embed_text(text) -> list[float]
            and static cosine_similarity(a, b) -> float. Reuses
            SkillEmbeddingService (daemon/services/skill_embedding_service.py:87)
            — Blueprint does NOT reimplement embedding math.
        _config: BlueprintConfig.
        _llm_config: dict for trigger-query generation fallback.
    """

    def __init__(
        self,
        repo: Any,                       # BlueprintRepository
        embedding_service: Any,          # SkillEmbeddingService (reused)
        config: Any,                     # BlueprintConfig
        llm_config: dict[str, Any],
    ) -> None:
        self._repo = repo
        self._embedding_service = embedding_service
        self._config = config
        self._llm_config = dict(llm_config) if llm_config else {}

    async def match(
        self,
        project_id: str,
        query: str,
        max_area: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> list[MatchedBlueprint]:
        """Run the full match pipeline. Returns blueprints in slot order.

        Slot 1 is ALWAYS core.md if it exists (lineage='core'). Slots 2-5
        are the top area matches above threshold (lineage='matched').
        Returns [core_only] when no area match clears the threshold
        (overview Section 5.3 "No-match fallback"). Returns [] only when
        the project has no core.md AND no area matches.
        """
        t0 = time.perf_counter()
        max_area = max_area if max_area is not None else self._config.max_area
        threshold = threshold if threshold is not None else self._config.threshold
        alpha = self._config.bm25_alpha
        beta = self._config.embedding_beta

        # --- Slot 1: core.md (reserved, always) ---
        core = await asyncio.to_thread(self._repo.get_core, project_id)
        results: list[MatchedBlueprint] = []
        had_core = False
        if core is not None:
            results.append(MatchedBlueprint(
                id=core.id,
                name=core.name,
                kind="core",
                content=core.content,
                file_refs=list(core.file_refs or []),
                tags=list(core.tags or []),
                version=core.version,
                lineage="core",
                score=1.0,   # core is unconditional
                rank=1,
            ))
            had_core = True

        # --- Slots 2-5: area matches ---
        candidates = await asyncio.to_thread(
            self._repo.search_candidates, project_id
        )
        matched: list[MatchedBlueprint] = []
        top_scores: list[float] = []

        if candidates and query and query.strip():
            matched, top_scores = await self._match_area(
                query, candidates, alpha, beta, threshold, max_area
            )

        # Rank area matches starting after core.
        base_rank = 2 if had_core else 1
        for i, m in enumerate(matched):
            m.rank = base_rank + i
        results.extend(matched)

        # --- Structured logging (overview Section 5.4.1, from v1) ---
        latency_ms = (time.perf_counter() - t0) * 1000.0
        # Hash/truncate the query for privacy + log line length.
        query_safe = (query[:80] + "...") if len(query) > 80 else query
        logger.info(
            "blueprint_match",
            extra={
                "project_id": project_id,
                "query_length": len(query),
                "query_preview": query_safe,
                "candidate_count": len(candidates),
                "matched_count": len(matched),
                "top_scores": [round(s, 4) for s in top_scores[:5]],
                "threshold": threshold,
                "had_core": had_core,
                "latency_ms": round(latency_ms, 2),
            },
        )
        return results

    async def _match_area(
        self,
        query: str,
        candidates: list[tuple[Any, list[Any]]],
        alpha: float,
        beta: float,
        threshold: float,
        max_area: int,
    ) -> tuple[list[MatchedBlueprint], list[float]]:
        """BM25 + vector fusion over area candidates. Returns (matched, top_scores)."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return [], []

        # --- Stage 1: BM25 (reuse _bm25_score from skill_search_service) ---
        # Corpus per blueprint = content + trigger_queries + name + tags.
        tokenized_docs: list[tuple[Any, list[str]]] = []
        for bp, _embs in candidates:
            doc_text = " ".join([
                bp.name or "",
                bp.content or "",
                " ".join(bp.trigger_queries or []),
                " ".join(bp.tags or []),
            ])
            tokenized_docs.append((bp, _tokenize(doc_text)))

        df: dict[str, int] = {}
        for _bp, toks in tokenized_docs:
            unique = set(toks)
            for term in query_tokens:
                if term in unique:
                    df[term] = df.get(term, 0) + 1
        n_docs = len(tokenized_docs)
        total_tokens = sum(len(t) for _, t in tokenized_docs)
        avgdl = total_tokens / n_docs if n_docs else 1.0

        bm25_raw: dict[str, float] = {}  # blueprint_id -> raw bm25 score
        for bp, toks in tokenized_docs:
            s = _bm25_score(
                query_tokens=query_tokens,
                doc_tokens=toks,
                doc_freqs=df,
                total_docs=n_docs,
                avg_doc_len=avgdl,
            )
            bm25_raw[bp.id] = s

        # --- Stage 2: vector similarity ---
        # Embed the query ONCE. Per-blueprint score = MAX cosine over its
        # trigger-query embeddings (mirrors SkillSearchService._embedding_rerank,
        # daemon/services/skill_search_service.py:632-638).
        try:
            query_emb = await self._embedding_service.embed_text(query)
        except Exception as e:
            logger.warning(f"[Blueprint] query embed failed, vector stage skipped: {e}")
            query_emb = None

        cos = self._embedding_service.cosine_similarity  # staticmethod, reuse
        vector_scores: dict[str, float] = {}
        for bp, embs in candidates:
            if not embs or query_emb is None:
                vector_scores[bp.id] = 0.0
                continue
            best = max(
                (cos(query_emb, e.embedding) for e in embs),
                default=0.0,
            )
            vector_scores[bp.id] = best

        # --- Fusion ---
        # BM25 is min-max normalized across the candidate set so it shares
        # the [0,1] range with cosine. Vector scores are already in [-1,1]
        # (cosine); we clip to [0,1] since negative cosine = irrelevant.
        bm25_vals = list(bm25_raw.values())
        bm25_min = min(bm25_vals) if bm25_vals else 0.0
        bm25_max = max(bm25_vals) if bm25_vals else 1.0
        bm25_range = (bm25_max - bm25_min) or 1.0  # avoid /0 when all equal

        # NOTE — single-candidate degenerate case: when there is exactly ONE
        # area blueprint in the candidate set, min==max so bm25_range→0→1.0
        # and bm25_norm = (x - x)/1.0 = 0.0. The single candidate's fused
        # score collapses to β·vec (vector only). This is INTENTIONAL:
        # a lone area blueprint is injected only if its vector score clears
        # the threshold on its own merit. Phase 6 should verify that
        # single-area-blueprint projects still inject their area content
        # (if not, the threshold is too high for the vector-only floor).

        scored: list[tuple[float, Any]] = []
        for bp, _embs in candidates:
            bm25_norm = (bm25_raw[bp.id] - bm25_min) / bm25_range
            vec = max(0.0, min(1.0, vector_scores[bp.id]))
            final = alpha * bm25_norm + beta * vec
            scored.append((final, bp))

        scored.sort(key=lambda p: p[0], reverse=True)
        top_scores = [s for s, _ in scored]

        matched = [
            MatchedBlueprint(
                id=bp.id,
                name=bp.name,
                kind="area",
                content=bp.content,
                file_refs=list(bp.file_refs or []),
                tags=list(bp.tags or []),
                version=bp.version,
                lineage="matched",
                score=round(final, 4),
            )
            for final, bp in scored
            if final >= threshold
        ][:max_area]
        return matched, top_scores
```

**Fusion formula (exact, restated):**
```
bm25_norm = (bm25_raw_i - min(bm25_raw)) / (max(bm25_raw) - min(bm25_raw))
vec_i     = clip(cosine(query_emb, best_trigger_emb_i), 0, 1)
final_i   = α * bm25_norm_i + β * vec_i
```
- **α + β = 1 is recommended** (constrained form). Justification: with two signals both normalized to [0,1], the constrained form keeps `final` in [0,1] and makes the threshold interpretable as an absolute confidence. Independent weights (α + β ≠ 1) would let `final` exceed 1.0, forcing a separate normalization step before the threshold gate — added complexity for no precision gain at the small candidate counts (≤ a few dozen blueprints per project) Blueprint operates on. The spike (P0) validates this choice empirically.
- **Min-max normalization for BM25** (not z-score): min-max preserves the [0,1] range needed for the constrained fusion, and is robust to the small-corpus case where z-score is unstable. Matches the normalization convention implicit in the skill system's score handling.

#### P1.6.3 `build_blueprint_query` — `daemon/services/blueprint_query.py`

```python
# daemon/services/blueprint_query.py
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_blueprint_query(query: str, context: str | None = None) -> str:
    """Build the matcher query from available signals.

    TARGET signature (overview Section 5.3.1):
        build_blueprint_query(task_message, task_context, skill_content)

    PHASE 1 signature (this function):
        build_blueprint_query(query, context=None)

    SCOPE LIMITATION (documented): In Phase 1, the matcher is exercised
    directly (tests, spike replay) and is NOT yet wired into the
    assemble_context_messages() injection hook. task_context (the Tier 2A
    context param) and skill_content (from load_skill) are available in
    the caller's scope but NOT threaded into assemble_context_messages()
    — see gap resolution in phase23-implementation.md §2.6 (Option B for
    v1: user_query only; Option A deferred). Phase 1 accepts whatever
    the caller passes; the Phase 2 wiring decides whether to thread
    more. The function is written so the Phase 2 upgrade is a pure
    signature extension — no fusion logic changes.

    The skill_content cap (2000 chars, overview Section 5.3.1) is applied
    here when skill_content is added in Phase 2. task_context is already
    capped at 4000 chars by _format_task_context()
    (daemon/tools/instance.py:53) before it reaches this builder.
    """
    parts: list[str] = [query]
    if context and context.strip():
        parts.append(context)
    return "\n\n".join(parts)
```

#### P1.6.4 Trigger-query generation — `generate_trigger_queries`

Lives in `daemon/services/blueprint_query.py` (or a small `daemon/services/blueprint_trigger.py`). **Reuses the same LLM helper pattern as `SkillEmbeddingService.generate_trigger_queries`** (`daemon/services/skill_embedding_service.py:144-220`) — same prompt structure, same defensive JSON parsing, same fallback to empty list on failure. Blueprint's variant grounds on `name + content + file_refs` instead of `name + description + content`.

```python
# daemon/services/blueprint_query.py (continued)

import asyncio
import json
import re
from typing import Any

import openai

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


async def generate_trigger_queries(
    blueprint: Any,
    llm_config: dict[str, Any],
    min_queries: int = 3,
    max_queries: int = 10,
    max_chars: int = 200,
) -> list[str]:
    """Generate 3-10 example queries that should match this blueprint.

    Reuses the SAME prompt pattern + defensive parsing as
    SkillEmbeddingService.generate_trigger_queries
    (daemon/services/skill_embedding_service.py:144-220). Differences:
      - Grounds on name + content + file_refs (blueprints have no
        separate description field; file_refs add path vocabulary).
      - Returns plain list[str]; caller (BlueprintRepository.update or
        the create flow) stores on the row + recomputes embeddings.

    Returns [] on any failure (LLM unreachable, malformed response).
    """
    name = getattr(blueprint, "name", "") or "unnamed blueprint"
    content = getattr(blueprint, "content", "") or ""
    file_refs = getattr(blueprint, "file_refs", []) or []
    refs_text = " ".join(
        f.get("path", "") for f in file_refs if isinstance(f, dict)
    )

    system_prompt = (
        "You are an assistant that generates realistic example task messages. "
        f"Given a project blueprint's content, produce a JSON array of between "
        f"{min_queries} and {max_queries} short task messages (each <= {max_chars} "
        "characters) that a developer agent might receive and that should match "
        "this blueprint. Return ONLY a JSON array of strings. No prose, no "
        "markdown fences, no comments."
    )
    user_prompt = (
        f"Blueprint name: {name}\n"
        f"Blueprint content (excerpt): {content[:1500]}\n"
        f"File references: {refs_text[:500]}\n\n"
        f"Return a JSON array of {min_queries}-{max_queries} example task "
        "messages that would match this blueprint."
    )

    try:
        model = llm_config.get("model") or "gpt-4o-mini"
        base_url = llm_config.get("base_url")
        api_key = llm_config.get("api_key") or ""

        def _call() -> Any:
            client = openai.OpenAI(api_key=api_key, base_url=base_url or None)
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
            )

        response = await asyncio.to_thread(_call)
    except Exception as e:
        logger.warning(f"[Blueprint] trigger-query LLM call failed: {e}")
        return []

    raw = _extract_chat_content(response)
    queries = _parse_trigger_queries(raw, max_queries)
    if len(queries) < min_queries:
        logger.info(
            f"[Blueprint] trigger-query gen produced {len(queries)} < {min_queries}; "
            f"using what we have"
        )
    return queries[:max_queries]


def _extract_chat_content(response: Any) -> str:
    """Mirror SkillEmbeddingService._extract_chat_content (skill_embedding_service.py:479-518)."""
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", "") if message else getattr(first, "text", "")
    except Exception:
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text", "")
                if t:
                    parts.append(str(t))
            else:
                parts.append(str(block))
        content = " ".join(parts)
    return _THINK_BLOCK_RE.sub("", str(content or ""))


def _parse_trigger_queries(raw_text: str, max_queries: int) -> list[str]:
    """Parse LLM output into a clean list. Tries JSON array, then bulleted fallback."""
    if not raw_text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw_text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = raw_text.find("[")
        end = raw_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = raw_text[start : end + 1]
    if candidate:
        try:
            arr = json.loads(candidate)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()][:max_queries]
        except json.JSONDecodeError:
            pass
    # Fallback: numbered/bulleted lines.
    lines = re.findall(r"(?:^|\n)[-*\d]+[.)]\s*(.+)", raw_text)
    return [ln.strip() for ln in lines if ln.strip()][:max_queries]
```

### P1.7 Wiring in `daemon/manager.py`

Parallel to the skill-service wiring block at `daemon/manager.py:891-921` and the `InstanceUiPrefsRepository` wiring at `daemon/manager.py:563`.

**Repository wiring** (alongside line 563, the `InstanceUiPrefsRepository` precedent):
```python
# Blueprint repository. Brand-new tables (project_blueprints + 2) created by
# SQLModel.metadata.create_all() via daemon/repositories/__init__.py registration.
# Same precedent as instance_ui_prefs (daemon/manager.py:557-563).
self._blueprint_repo = BlueprintRepository(engine=self._engine)
```

**Service wiring** (alongside the skill block at line 891):
```python
# Blueprint matcher. Reuses the skill embedding service (same vector space)
# per BlueprintConfig defaults. Only constructed when config.skill_evolution is
# set (embedding service dependency) — graceful-disabled otherwise, mirroring
# the skill services' None-on-disabled pattern (daemon/manager.py:918-921).
if self.config.skill_evolution is not None and self._skill_embedding_service is not None:
    self._blueprint_matcher = BlueprintMatcher(
        repo=self._blueprint_repo,
        embedding_service=self._skill_embedding_service,
        config=self.config.blueprint,
        llm_config=skill_llm_config,
    )
else:
    self._blueprint_matcher = None
```

Add imports near the top of `manager.py` (alongside the skill imports at lines 82-84):
```python
from .repositories.blueprint import BlueprintRepository
from .services.blueprint_matcher import BlueprintMatcher
```

### P1.8 Embedding Recompute on Trigger-Query Change

When `trigger_queries` change (create or update), recompute embeddings for all of them and call `replace_embeddings` (P1.4). This is a **write-path** concern, exercised by:
- The Phase 3 CRUD API (`POST`/`PUT`).
- The Phase 4 Blueprinter agent.

Phase 1 provides the primitive (`replace_embeddings`) and the generator (`generate_trigger_queries`), but does NOT wire the full create/update pipeline — that lives in Phase 3/4. Phase 1's contract is: the matcher reads whatever embeddings exist; missing embeddings degrade gracefully to BM25-only (vector score 0.0 for that blueprint, mirroring `SkillSearchService._embedding_rerank` at `daemon/services/skill_search_service.py:620-626`).

The intended write flow (documented for Phase 3, not implemented in Phase 1):
```
1. generate_trigger_queries(blueprint, llm_config) -> list[str]
2. embed each via SkillEmbeddingService.embed_text (best-effort per-query)
3. repo.replace_embeddings(blueprint_id, [(q, emb), ...])
4. repo.update(blueprint_id, trigger_queries=[...], ...)
5. repo.add_revision(blueprint_id=..., version=..., content=..., ...)
```

### P1.9 Phase 1 Exit Criteria Checklist

- [ ] `daemon/repositories/blueprint/models.py` defines `Blueprint`, `BlueprintEmbedding`, `BlueprintRevision` with the exact fields/indexes in §P1.3.
- [ ] `daemon/repositories/blueprint/__init__.py` + `daemon/repositories/__init__.py` registration block added; `SQLModel.metadata.create_all()` creates the 3 tables on a fresh SQLite AND a fresh PostgreSQL DB (verify with a one-off script).
- [ ] `BlueprintRepository` implements all methods in §P1.4; `search_candidates` returns `(blueprint, embeddings)` pairs excluding core.
- [ ] `BlueprintConfig` added to `daemon/config.py` with the §P1.5 fields; registered on `Config`.
- [ ] `BlueprintMatcher.match()` returns core.md (slot 1, lineage='core') + up to 4 area matches (lineage='matched'); threshold gate drops sub-threshold candidates; no-match returns core-only.
- [ ] `build_blueprint_query(query, context=None)` exists with the §P1.6.3 scope-limitation docstring.
- [ ] `generate_trigger_queries()` produces 3-10 queries on a sample blueprint; defensive parsing handles fenced JSON + bulleted fallback.
- [ ] `manager.py` wires `_blueprint_repo` + `_blueprint_matcher` (None when skill_evolution disabled).
- [ ] Structured `blueprint_match` log emitted on every match with the §P1.6.2 fields.
- [ ] Unit tests pass: (a) matcher with mocked embedding service returns expected slot order; (b) threshold gate; (c) core.md always present; (d) trigger-query generation parses a fenced-JSON LLM response.

---

## Phase 0 → Phase 1 Dependency Contract

| Phase 0 output | Phase 1 consumer | Default if Phase 0 skipped |
|---|---|---|
| `spike_results.md` → chosen `threshold` | `BlueprintConfig.threshold` | `0.30` (conservative) |
| `spike_results.md` → chosen `α` | `BlueprintConfig.bm25_alpha` | `0.4` |
| `spike_results.md` → chosen `β` | `BlueprintConfig.embedding_beta` | `0.6` |
| Validated BM25 corpus formula (content + trigger_queries + name + tags) | `BlueprintMatcher._match_area` | As specified in §P1.6.2 |
| Validated aggregation (max cosine over trigger embeddings) | `BlueprintMatcher._match_area` | As specified in §P1.6.2 |

If Phase 0 fails to clear the 80% top-1 bar, Phase 1 may still proceed with conservative defaults, but the threshold/weights are flagged as "uncalibrated" and Phase 6 calibration becomes mandatory before any production rollout.

---

## Coupling Map (Phase 0 ↔ Phase 1)

| | Phase 0 (spike) | Phase 1 (schema + engine) |
|---|---|---|
| **Phase 0** | — | Loose: spike validates the fusion formula; Phase 1 implements it in production code. Phase 0 reuses `SkillEmbeddingService` + `_bm25_score` inline; Phase 1 imports them. No shared state. |
| **Phase 1** | Loose (downstream) | — |

**Independent of:** Phase 2 (injection), Phase 3 (CRUD API), Phase 4 (Blueprinter), Phase 5 (UI). Phase 1's matcher is callable but not invoked by any production path until Phase 2 wires it.

---

## Risks (Phase 0 + Phase 1 specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R-P0-1 | Spike shows < 80% top-1 recall → matcher design is insufficient | High | Medium | Pause; investigate whether trigger queries are too generic or vector model is wrong domain. LLM rerank (deferred) is the fallback per overview O5. |
| R-P0-2 | Seed blueprints are unrepresentative → spike results don't generalize | Medium | Medium | Use THIS project's real recent tasks as queries (system-context history provides them). Document the query provenance in `spike_results.md`. |
| R-P1-1 | `SQLModel.metadata.create_all()` doesn't pick up the new tables on PostgreSQL | High | Low | The `instance_ui_prefs` precedent (verified working) uses the identical registration path. Verify with a one-off script on a fresh PG DB before declaring Phase 1 done. |
| R-P1-2 | Reusing `SkillEmbeddingService` couples Blueprint to skill-system config | Medium | Low | `BlueprintConfig` has its own `embedding_*` overrides that fall through to skill config only when `None`. Decoupling is a config change, not a code change. |
| R-P1-3 | `build_blueprint_query` Phase-1 limitation (no task_context/skill_content) weakens early matching | Medium | Medium | Documented; Phase 2 extends the signature. The matcher logic is identical — only the query string richness changes. No refactor risk. |
| R-P1-4 | Min-max BM25 normalization is unstable when all candidates score 0 (no token overlap) | Low | Medium | The `bm25_range = (max - min) or 1.0` guard prevents div-by-zero; all-zero candidates score 0 after normalization and are dropped by the threshold gate. |

---

## Open Questions (Phase 0 + Phase 1 scope)

| # | Question | Resolution path |
|---|---|---|
| Q-P0-1 | Exact α, β, threshold values | Resolved by Phase 0 spike (`spike_results.md`). |
| Q-P1-1 | Should `BlueprintMatcher` cache the query embedding within a single `match()` call? | Yes — already specified (embed once, reuse across candidates). No cross-call cache in Phase 1. |
| Q-P1-2 | Should `search_candidates` paginate for projects with 100+ blueprints? | No — blueprint corpora are expected to stay small (10s). Add pagination if a project exceeds ~50 active area blueprints. |
