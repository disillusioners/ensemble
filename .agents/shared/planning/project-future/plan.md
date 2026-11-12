# Plan Overview: Project Future — dev-team forward-improvement wishlist tool

Date: 2026-11-12 (UTC)
Author: plan-creation worker (planner commission, setup+census lane)
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-project-future` (branch `feature/project-future`, base `ac9849bf7` == origin/latest)
Companion files: `decisions.md` (D1–D15), `research-tool-lane.md`, `research-data-layer.md`, `research-prompt-docs.md` (same directory — the research set is the evidence base; this plan cites it, never re-derives)

---

## Objective

Give dev-team agents a third recording lane — `project_future` — a per-project, forward-looking
improvement wishlist (MAX 32 items, FIFO eviction with mandatory disclosure) so that future ideas
stop being shoehorned into critical notes (a pinned, context-injected, blast-radius surface) or
project history (a backward-looking change log). A developer must be able to execute this plan
without re-deriving any design.

**User law (non-negotiable):** MAX 32 items per project; when full, the FIRST (oldest by creation
order) item is evicted to make room, and the add response MUST disclose the evicted item
(id + summary) — no silent loss.

---

## Scope

### In Scope

- New SQLModel table `ProjectFutureEntry` (`project_future`) + package exports + create_all registration (ONE lane).
- New migration `daemon/migrations/versions/{YYYYMMDD_HHMMSS}_create_project_future.sql` (full DDL below).
- Three repository methods on `SQLModelProjectRepository` (add-with-prune / list / remove) + the 32-cap constant.
- New tool module `daemon/tools/project_future.py` — `project_future_add`, `project_future_list`, `project_future_remove`.
- Registration: the FIVE steps (module+decorators, `_full_doc_`, CATEGORY_MODULES, KNOWN_TOOL_NAMES, construct+extend).
- Allow grants: 14 `meta.json` files (category-key style).
- Prompt/doc discoverability: leader rule.md Decision-Table row; BOTH reviewer workflow.md twins :286; docs/agents.md row.
- Prompt-artifact baseline re-stamp (181 rows; exactly 3 hashes change).
- Tests: tool-layer, repo-layer, migration-structure, registration/filter pins (AC1–AC24).
- Proof obligations PO1–PO7 (tester-executable, zero network, zero live DB).

### Out of Scope (with reasons)

- REST/API endpoints — commission is tool-lane only (D6).
- Dedupe / fuzzy matching — forbidden in v1; the 32-cap + FIFO IS the discipline (D5).
- `project-manager` grant — verb-style allow idiom, not dev-team; easy follow-up (D8).
- Reviewer severity-tier mentions in 7 `skills-template/*.md` files + `agents/reviewer/rule.md:22,:94` — doctrine sweep is a separate follow-up (research-prompt-docs.md §Q2/#7).
- Blueprint pending-queue sidecar (`project_history.py:163-216`) — deliberately NOT copied (D10).
- Any `_ensure_postgres_columns` entry — brand-new tables need none (research-data-layer.md Q4, manager.py:1025-1029/:1045-1049/:8421-8429).
- Any contact with the live daemon, `/home/nea/agents-ensemble`, canonical `/home/nea/ensemble-src`, pushes, merges, promotes — ZERO-CONTACT discipline (D15).

---

## Verified Anchors (file:line — all three research files read end-to-end today; 9 highest-load anchors additionally spot-checked directly in this worktree)

| # | Anchor | File:Line | Evidence |
|---|--------|-----------|----------|
| A1 | `CATEGORY_NAME`/`CATEGORY_DOC`, caps 300/5000 | daemon/tools/project_history.py:19-25 | tool-lane Q1 |
| A2 | `_FULL_DOCS` dict + `_full_doc_` attachments | project_history.py:29-91; :219/:253/:282/:303 | tool-lane Q1 |
| A3 | Factory `create_project_history_tools(store, current_instance_id="", agent_id="", manager=None)` | project_history.py:106-109 | tool-lane Q1 |
| A4 | Decorator stack `@register_tool_category` outermost / `@tool` innermost | project_history.py:123-124 (etc.) | tool-lane Q1 |
| A5 | add validation pipeline + truncate-not-reject | project_history.py:134-150 (:148-150 truncation) | tool-lane Q1 |
| A6 | add returns flat `to_dict()`; list `{entries,total,limit,offset}` newest-first; limit clamp `max(1,min(limit,100))` | project_history.py:218/:236; repository.py:1392/:1395-1400 | tool-lane Q1 |
| A7 | delete args/errors/success shape | project_history.py:286-302 (`{"success": True, "deleted_entry_id": …}` :302; error :300) | tool-lane Q1 |
| A8 | Blueprint sidecar (DO NOT copy) | project_history.py:103,:163-216; instance.py:6648-6650 | tool-lane Q1 |
| A9 | CATEGORY_MODULES dict opens :1174; project_history entry :1189; value must be `daemon.tools.*` (:1207-1211) | daemon/tools/_tool_registry.py:1174/:1189 | tool-lane Q2 (spot-checked) |
| A10 | KNOWN_TOOL_NAMES frozenset :1565; project_history names :1772-1775; regen cmd :1550-1553; drift pin :1559-1560 | _tool_registry.py | tool-lane Q2 (spot-checked) |
| A11 | Construction+extend seam (import :281; extend :6651-6655) — CORRECTED location, NOT _tool_registry.py | daemon/tools/instance.py:281,:6651-6655 | tool-lane Q2 + data-layer Q6 (spot-checked) |
| A12 | Ordering law: extend BEFORE create_help_tool :7620 / scan_tools_for_full_docs :7625 / _apply_tool_filter :7628; late extend = SILENTLY INVISIBLE (:6665-6668/:6677-6680/:6698-6701) | instance.py | tool-lane Q2 |
| A13 | Distributed enforcement (missing CATEGORY_MODULES → allow WARNING registry.py:1429-1439; missing KNOWN → frozen-binary warnings; missing extend → invisible, NO test) | tool-lane Q2 §Enforcement | tool-lane Q2 |
| A14 | critical_notes REFUSE-on-full contrast (`_MAX_ENTRIES=200` :70; refusal :538-552; "No silent eviction" :578-579) | daemon/tools/critical_notes.py | tool-lane Q3 (spot-checked :70) |
| A15 | Category is NON-privileged → empty-allow agents inherit; explicit grants needed for non-empty allow lists | instance.py:7697-7701,:7633-7668; registry :823; census prompt-docs Q4 | tool-lane Q4 |
| A16 | `ProjectHistoryEntry` model (uuid4 PK :483; FK sa_column ondelete CASCADE :484-486; created_at **datetime** default_factory :496; to_dict :498-510 isoformats created_at :509); LAST class in shared file → append at :512+ | daemon/repositories/project/models.py:479-510 | data-layer Q1 (spot-checked) |
| A17 | `add_history_entry` :1285 (repo truncates :1309-1310); `delete_history_entry` :1340 (session.get :1351, project scope :1355); `list_history_entries` :1362 (count idiom :1388, order :1392) | daemon/repositories/project/repository.py | data-layer Q2 |
| A18 | ⭐ FIFO template `BlueprintPendingRepository.prune_excess` (count→overflow→oldest ASC LIMIT→DELETE IN→commit; "FIFO" docstring :426-433) | daemon/repositories/blueprint/pending_repository.py:421-477 | data-layer Q2 (spot-checked head) |
| A19 | Multi-statement transactions are house norm (`_sync_tags_bulk` :103-113) | repository.py:103-113 | data-layer Q2 |
| A20 | `store` = `manager.project_store` = `self._project_repository` | daemon/manager.py:2151 | data-layer Q6 |
| A21 | Model registration: project `__init__.py` import :4 + `__all__` :6-15; create_all lane = mission-record-style import block (repositories `__init__.py` :95-107); SECOND lane exists (api.py:1220-1383) — pick ONE | repositories/__init__.py; daemon/api.py | data-layer Q3 |
| A22 | Migration template mission_records (header/-- UP :46/CREATE IF NOT EXISTS :57-73/indexes :76-84/-- DOWN :86-90) + memoir doctrine (TEXT-never-VARCHAR :42; IF-NOT-EXISTS mandatory :64-73) | daemon/migrations/versions/20261025_122631_create_mission_records.sql; 20261109_021154_create_memoir_v2_tables.sql | data-layer Q4 |
| A23 | Duplicate-prefix guard | daemon/migrations/runner.py:420 (`DuplicateMigrationVersionError`) | blueprint + data-layer Q4 |
| A24 | Leader rule.md Decision Table (:127-143; headers :131-132; rows :133-137); leader[v2] DOES NOT EXIST | agents/leader/rule.md; agents/ tree | prompt-docs Q1 (spot-checked meta) |
| A25 | Reviewer Suggestion row :286 — workflow.md twins BYTE-IDENTICAL (same sha 24e40e96…; baseline rows 143/147) | agents/reviewer/workflow.md:286; agents/reviewer[v2]/workflow.md:286 | prompt-docs Q2 (spot-checked) |
| A26 | docs/agents.md category table (headers :336-337; rows :346-347); docs/ NOT baseline-hashed (generator hashes agents/ only, :105) | docs/agents.md; scripts/protection/generate_prompt_artifact_baseline.py | prompt-docs Q3 |
| A27 | Allow census: NO dev-spine agent holds a recording category; leader category-key style (meta.json:15); PM verb-style (meta.json:25-33, denies :84); leader team :17 | agents/**/meta.json | prompt-docs Q4 (spot-checked) |
| A28 | Baseline machinery: regen bare; verify `--check --tree`; 181 rows / 47 dirs; meta.json NOT hashed; depth-2 strict | scripts/protection/generate_prompt_artifact_baseline.py; prompt_artifacts.py:119-133/:352 | prompt-docs Q5 |
| A29 | Test idioms: MagicMock store + direct dict asserts + cap import (:9); repo tests at tests/ ROOT (in-memory sqlite + create_all, NO migrations — fresh-SQLite boot broken by 20260714_000001); migration greps pattern :583-611; FK pin `fk.ondelete=="CASCADE"` :1039 | tests/unit/test_project_history_tools.py; tests/test_project_history.py; tests/unit/test_project_metadata_table.py; tests/unit/test_snapshot_search_service.py | data-layer Q5 |
| A30 | Name-collision scan: no `project_future`/`project-future` hits in agents/ or daemon/ (one >1.4MB file skipped; brand-new name ⇒ negligible) | tool-lane §Surprises #3 | tool-lane |

---

## Phases & Ordering (coupling view)

| Phase | Work Items | Objective | Coupling |
|-------|-----------|-----------|----------|
| 1. Data layer | W1, W2 | Model + migration + exports exist; `create_all` yields the table | Tight W1↔W2 (column/name lockstep); W1 feeds W3 |
| 2. Repository | W3 | Cap + FIFO + remove semantics on the store | Tight W1↔W3 (model fields); feeds W4, W9 |
| 3. Tool + registration | W4, W5 | Tool surface exists, registered, extendable, visible | Tight W3↔W4 (store contract); W5 depends on W4 |
| 4. Surfac­e + proof | W6, W7, W8, W9, W10, W11 | Discoverable, granted, tested, proven | W6↔W7 tight (baseline); W8 independent; W9/W10 depend on W1–W5 |

W1 → W2/W3 → W4 → W5 → (W6+W7) → W8 → W9 → W10 → W11. No phase starts before its predecessor's acceptance hooks pass.

---

## Work Items

### W1 — Model `ProjectFutureEntry` + registration lanes

Files:
- `daemon/repositories/project/models.py` — append AFTER `ProjectHistoryEntry` (:510; it is the LAST class; shared multi-domain file, A16):

```python
class ProjectFutureEntry(SQLModel, table=True):
    """SQLModel ProjectFutureEntry table - per-project forward-improvement wishlist (32-cap FIFO)."""
    __tablename__ = "project_future"

    __table_args__ = (
        Index("uq_project_future_project_seq", "project_id", "seq", unique=True),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    source_agent: str = Field(default="")          # NOT NULL in DDL — "" when unknown (D2)
    source_instance_id: str = Field(default="")    # NOT NULL in DDL — "" when unknown (D2)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))  # history-sibling idiom :496
    seq: int = Field(nullable=False)               # app-assigned monotonic per project (D2/D3)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "summary": self.summary,
            "details": self.details,
            "source_agent": self.source_agent,
            "source_instance_id": self.source_instance_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,  # mirror :509
            "seq": self.seq,
        }
```

- Add `Index` to the sqlalchemy imports at the top of models.py **if not already imported** (developer verifies current import block; memoir 3-site index-lockstep precedent, data-layer Q4).
- `daemon/repositories/project/__init__.py` — append `ProjectFutureEntry` to the :4 model-import line and to `__all__` (:6-15; `ProjectHistoryEntry` sits at :13).
- `daemon/repositories/__init__.py` — add the mission-record-style import+comment block (idiom :95-107), e.g. after the :107 block:
  `# Project-future wishlist table (forward-improvement lane, 32-cap FIFO). Imported here so` /
  `# SQLModel.metadata.create_all() registers project_future. Brand-new table — no` /
  `# _ensure_postgres_columns entry is needed. SQLite-side canonical DDL ships in the migration runner.`
  `from .project.models import ProjectFutureEntry  # noqa: F401  # register before create_all`
  Do **NOT** also add a `daemon/api.py` per-model create_all block — ONE lane only (A21; api.py:1220-1383 is the second lane; mixing is harmless but the house comments cite exactly one).

Acceptance: `SQLModel.metadata.create_all` on a fresh in-memory SQLite engine creates `project_future` with the unique index `uq_project_future_project_seq`, FK `ondelete="CASCADE"`, and column set exactly as above (AC16 introspection pin; AC19 uniqueness; repo tests load).

### W2 — Migration file

File: `daemon/migrations/versions/{YYYYMMDD_HHMMSS}_create_project_future.sql` — developer stamps the REAL current UTC time at file creation (duplicate-prefix guard, A23). Content (copy verbatim, adjusting only the `Created:` line):

```sql
-- Migration: create_project_future
-- Created: <developer stamps UTC YYYYMMDD_HHMMSS>
-- Author: feature/project-future implementation lane
-- Description: ProjectFutureEntry — per-project forward-improvement wishlist (MAX 32, FIFO eviction).
--   On PostgreSQL the table is created by ``SQLModel.metadata.create_all()`` (the model is
--   registered before create_all at manager boot — mission-records precedent for brand-new
--   tables, NO ``_ensure_postgres_columns`` mirror); the migration runner is a NO-OP there.
--   Schema mirrors daemon/repositories/project/models.py ``ProjectFutureEntry``.
--   EVERY STRING COLUMN IS TEXT (memoir doctrine D1.2/AM-6) — never VARCHAR, on either driver.
--   WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY): create_all runs FIRST on the boot
--   path; on a SQLite dev DB where create_all already made the table, a bare CREATE would
--   raise "already exists" and the runner would swallow it on its idempotent-skip branch.

-- UP
CREATE TABLE IF NOT EXISTS project_future (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    details TEXT,
    source_agent TEXT NOT NULL,
    source_instance_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_project_future_project_id ON project_future(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_future_project_seq ON project_future(project_id, seq);

-- DOWN
DROP INDEX IF EXISTS uq_project_future_project_seq;
DROP INDEX IF EXISTS ix_project_future_project_id;
DROP TABLE IF EXISTS project_future;
```

Notes: model↔DDL lockstep — `ix_project_future_project_id` is SQLAlchemy's default auto-name for the `index=True` FK column; `uq_project_future_project_seq` is the explicit model `Index(..., unique=True)` name (exact 3-site lockstep: model ↔ migration ↔ create_all, memoir :50-58 doctrine). `created_at TIMESTAMP` matches SQLAlchemy `DateTime` mapping from the sibling's datetime idiom (:496). DOWN drops exactly what UP created. Tests build schemas via `create_all`, NOT the runner — the pre-existing fresh-SQLite breaker `20260714_000001` is untouched and irrelevant to this file (A29).

Acceptance: AC20 migration-structure greps; no other file shares the timestamp prefix.

### W3 — Repository methods on `SQLModelProjectRepository`

File: `daemon/repositories/project/repository.py` — insert after `get_recent_history` (:1451-1469), keeping the history block contiguous. Per-call `with Session(self.engine)` idiom; multi-statement transactions are house norm (A19); FIFO template = `prune_excess` (A18).

Module-level constant near the block:

```python
PROJECT_FUTURE_DEFAULT_MAX_ENTRIES = 32  # defensive default; the TOOL module's _MAX_ENTRIES=32 is the single source of truth (D8/D13)
```

```python
def add_future_entry(
    self,
    project_id: str,
    summary: str,
    details: str | None = None,
    source_agent: str = "",
    source_instance_id: str = "",
    max_entries: int = PROJECT_FUTURE_DEFAULT_MAX_ENTRIES,
) -> dict:
    """Add a wishlist item, FIFO-pruning the OLDEST rows over the cap IN THE SAME TRANSACTION.

    Returns the new entry's to_dict() flat dict plus ``"evicted"``: a list of
    {id, summary, seq, created_at} for every row removed to make room (empty when not full).
    Eviction is disclosed, never silent. The table NEVER holds more than max_entries rows
    after this call; a drifted table (count > cap) self-heals in the same pass.
    """
    summary = summary[:300]                      # house double-layer truncation (A17 :1309-1310)
    details = details[:5000] if details else None
    with Session(self.engine) as session:
        count_stmt = select(func.count()).select_from(
            select(ProjectFutureEntry).where(ProjectFutureEntry.project_id == project_id).subquery()
        )                                        # inline count idiom (A17 :1388)
        existing = session.exec(count_stmt).one()
        overflow = max(0, existing - max_entries + 1)      # make room for the incoming row
        evicted: list[dict] = []
        if overflow > 0:
            victims = list(session.exec(
                select(ProjectFutureEntry)
                .where(ProjectFutureEntry.project_id == project_id)
                .order_by(ProjectFutureEntry.seq.asc())    # insertion order — same-timestamp-safe (D2)
                .limit(overflow)
            ))
            evicted = [
                {"id": v.id, "summary": v.summary, "seq": v.seq,
                 "created_at": v.created_at.isoformat() if v.created_at else None}
                for v in victims
            ]
            for v in victims:
                session.delete(v)
        max_seq = session.exec(
            select(func.max(ProjectFutureEntry.seq)).where(ProjectFutureEntry.project_id == project_id)
        ).one()
        entry = ProjectFutureEntry(
            project_id=project_id, summary=summary, details=details,
            source_agent=source_agent, source_instance_id=source_instance_id,
            seq=(max_seq or 0) + 1,                    # app-assigned monotonic per project (D2)
        )
        session.add(entry)
        session.commit()                               # ONE transaction: deletes + insert land together
        session.refresh(entry)
        payload = entry.to_dict()
        payload["evicted"] = evicted
        return payload
```

```python
def list_future_entries(
    self,
    project_id: str,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List wishlist items NEWEST-FIRST (seq DESC, created_at DESC tiebreak). {entries,total,limit,offset}."""
    with Session(self.engine) as session:
        stmt = select(ProjectFutureEntry).where(ProjectFutureEntry.project_id == project_id)
        count_stmt = select(func.count()).select_from(stmt.subquery())   # A17 :1388
        total = session.exec(count_stmt).one()
        stmt = (
            stmt.order_by(ProjectFutureEntry.seq.desc(), ProjectFutureEntry.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        entries = list(session.exec(stmt))
        return {"entries": [e.to_dict() for e in entries], "total": total, "limit": limit, "offset": offset}
```

```python
def remove_future_entry(
    self,
    item_id: str,
    project_id: str | None = None,
) -> dict | None:
    """Remove a wishlist item (mirrors delete_history_entry :1340, project-scoped).

    Returns the REMOVED item's dict (id, summary, seq, created_at, project_id, …) so the
    caller can identify what was retired; None when absent or project-mismatch.
    Deliberately NO agent-ownership requirement — any granted agent may retire an
    obsolete idea (D11).
    """
    with Session(self.engine) as session:
        entry = session.get(ProjectFutureEntry, item_id)
        if entry is None:
            return None
        if project_id is not None and entry.project_id != project_id:
            return None                                  # A17 :1355 scoping preserved
        removed = entry.to_dict()
        session.delete(entry)
        session.commit()
        return removed
```

Concurrency note (D2): two concurrent adds can compute the same `seq` → `IntegrityError` at commit → the whole transaction rolls back (evictions AND insert) → table stays consistent at cap; caller retries. Accepted v1 behavior, fail-closed.

Acceptance: AC10–AC19. No count-helper method added (inline idiom per A17).

### W4 — Tool module `daemon/tools/project_future.py` (NEW)

Modeled exactly on `project_history.py` anatomy (A1–A7). ~200 lines. NO Blueprint sidecar (D10).

Structure:
- Imports: `from ..repositories.project.models import ProjectFutureEntry` (only if needed; the tool layer needs none of the model), `from ._tool_registry import register_tool_category, tool`. NOTE the tool layer does NOT need constants here.
- `CATEGORY_NAME = "project_future"` (:19 idiom).
- `CATEGORY_DOC = """Manage project future entries — forward-looking improvement ideas and wishlist items for a project. Use this for "we should add/try/improve X later" suggestions; use project_history for what HAS happened and critical notes for lessons and traps. Each project keeps at most 32 items; when full, the OLDEST idea is evicted automatically and the add response names what was removed."""` (:20-22 idiom).
- `_MAX_ENTRIES = 32` — THE single source of truth for the cap, module-level, test-imported (AC8; house idiom A29). Passed INTO the repo as `max_entries=_MAX_ENTRIES` on every add call (repo's own default is defensive only).
- `_MAX_SUMMARY_LEN = 300`, `_MAX_DETAILS_LEN = 5000` (:24-25 idiom).
- `_FULL_DOCS` dict (:29-91 idiom) with one entry per tool (content requirements in the Prompt/Doc section below).
- Factory: `def create_project_future_tools(store, current_instance_id: str = "", agent_id: str = "", manager=None) -> list:` (:106-109 idiom). Docstring states: `manager` accepted for call-site parity with the history lane and **deliberately UNUSED in v1** — no Blueprint pending-queue sidecar (D10); the Wishlist is not a structural event stream. (If lint flags the unused arg, a targeted `# noqa` on the parameter line is acceptable.)
- Three tools, each `@register_tool_category(CATEGORY_NAME)` outermost over `@tool` innermost (A4), one-line docstring ending "Use tool_help() for details.", `_full_doc_ = _FULL_DOCS["<name>"]` attached after the def (A2).

**`project_future_add`** — args: `project_id: str, summary: str, details: str | None = None`. Pipeline mirrors A5:
1. project exists: `store.get(project_id)` → falsy ⇒ `{"error": f"Project '{project_id}' not found"}` (:139-141).
2. `summary = summary.strip()`; empty ⇒ `{"error": "Summary cannot be empty"}` (:144-145).
3. truncate `summary = summary[:_MAX_SUMMARY_LEN]`; `details = details[:_MAX_DETAILS_LEN] if details else None` (:148-150).
4. stamp provenance from factory closure: `source_agent=agent_id`, `source_instance_id=current_instance_id` (A29 :192-193 wiring).
5. `result = store.add_future_entry(project_id, summary, details, agent_id, current_instance_id, max_entries=_MAX_ENTRIES)` wrapped in `try/except Exception as exc: return {"error": str(exc)}` — this is the seq-race fail-closed disclosure path (D2; keeps the errors-always-`{"error": str}` contract).
6. `return result` — flat entry dict **including `"evicted"`** (verbatim pass-through, like :218).

**`project_future_list`** — args: `project_id: str, limit: int = 20, offset: int = 0`. Project check; `limit = max(1, min(limit, 100))` (:236); `offset = max(0, offset)` (:237); return `store.list_future_entries(project_id, limit, offset)` verbatim (:252).

**`project_future_remove`** — args: `project_id: str, item_id: str` (arg naming follows delete's `(project_id, entry_id)` shape, A7; "item" because the surface speaks wishlist). Project check (:292-294 mirror). `removed = store.remove_future_entry(item_id, project_id)`; `None` ⇒ `{"error": f"Entry '{item_id}' not found in project '{project_id}'"}` (:300 wording); else `{"success": True, "removed": removed}` (richer than :302 — D11 requires the removed item be identified).

- Factory returns `[project_future_add, project_future_list, project_future_remove]` (:305 idiom).

Acceptance: AC1–AC9.

### W5 — Registration (FIVE steps; order matters)

1. **Module + decorator stack** — done by W4 (decorators A4).
2. **`_full_doc_` per tool** — done by W4 (`_FULL_DOCS` :29-91 idiom; harvested by `scan_tools_for_full_docs`, _tool_registry.py:1115-1129).
3. **CATEGORY_MODULES entry** — `daemon/tools/_tool_registry.py`, insert `"project_future": "daemon.tools.project_future",` directly below the project_history entry at :1189. Value MUST be a `daemon.tools.*` path (:1207-1211) or the AST scanner never finds the module.
4. **KNOWN_TOOL_NAMES entries** — add `"project_future_add"`, `"project_future_list"`, `"project_future_remove"` in the frozenset (:1565; project_* names live :1757-1786, project_history at :1772-1775). Alphabetical sort: `project_future_*` sorts BEFORE `project_history_*` (`f` < `h`) — place accordingly, or regenerate the set with the documented command (`uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"`, :1550-1553) and paste the sorted output. The drift pin `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` (:1559-1560) enforces bidirectional exactness.
5. **Construct + extend, in the right place** — `daemon/tools/instance.py`: add import `from .project_future import create_project_future_tools` adjacent to :281; insert immediately after the history block (:6655), keeping the same call shape:

```python
    future_tools = create_project_future_tools(
        manager.project_store, current_instance_id, agent_id,
        manager=manager,
    )
    tools.extend(future_tools)
```

   **HARD ORDERING LAW (A12):** this extend MUST precede `create_help_tool` (:7620), `scan_tools_for_full_docs` (:7625), `_apply_tool_filter` (:7628). A late extend is **SILENTLY INVISIBLE** (in-repo warning :6665-6668/:6677-6680/:6698-6701) — no existing test catches it; that is why PO7 exists.

**Do-NOT-copy decisions (explicit):**
- Blueprint pending-queue sidecar (:163-216) is NOT replicated — no `_PENDING_QUEUE_ENTRY_TYPES`, no metadata-key probe, no enqueue. Future ideas are not structural history events (D10).
- Category stays NON-privileged — do NOT add `project_future` to `PRIVILEGED_TOOL_CATEGORIES` (registry set ends :823; instance.py:7633-7668 strip + five-pin exact-set assertions :7646-7653 stay untouched). Consequence stated honestly: empty-allow agents (e.g. watcher) inherit the three tools by default, exactly as they inherit project_history today (A15).

Acceptance: AC21–AC24; PO5 drift pin green; PO7 assembly probe shows the three tools reachable past help/scan/filter.

### W6 — Prompt/doc edits (exact strings; BOTH reviewer twins)

**W6a — leader Decision Table row.** `agents/leader/rule.md` — insert ONE new row after :137 (after the Self-reflection row), matching the table's bold style (A24; there is NO leader[v2] — exactly one file):

```
| **Future improvement idea** — something you wish existed later (NOT a lesson, NOT an event) | `project_future_add()` | "Add a streaming retry path for long LLM calls" |
```

**W6b — reviewer Suggestion routing.** `agents/reviewer/workflow.md:286` AND `agents/reviewer[v2]/workflow.md:286` — the twins are BYTE-IDENTICAL today (same sha 24e40e96…, A25); apply the IDENTICAL edit to both and re-verify byte-identity after (PO6). Old line (both files):

```
| Suggestion | 🟢 | Consider for future improvements |
```

New line (both files, exactly):

```
| Suggestion | 🟢 | Consider for future improvements — record the idea with `project_future_add()` (32-item FIFO wishlist) |
```

Out of scope (follow-up): the same severity tier also appears in 7 reviewer `skills-template/*.md` files and `agents/reviewer/rule.md:22,:94` — NOT edited in v1 (prompt-docs #7).

**W6c — docs/agents.md category row.** Insert after :347 (the Project History row), matching header `| Category | Internal Key | Description | Key Tools |` (:336-337):

```
| **Project Future** | `project_future` | Forward-looking improvement wishlist (max 32 per project, FIFO eviction with disclosure) | `project_future_add`, `project_future_list`, `project_future_remove` |
```

docs/ is OUTSIDE agents/ → NOT baseline-hashed (A26).

**W6d — `_FULL_DOCS` plain-language content (part of W4, listed here because it IS the discoverability fix).** Each `_full_doc_` must state, in plain language: (i) future improvement ideas go HERE — not critical notes (lessons/traps, capped 200, REFUSES when full) and not project history (what happened); (ii) the cap is exactly 32 per project; (iii) when full, the OLDEST idea is evicted AUTOMATICALLY and the add response carries `"evicted"` naming each removed item's id + summary — eviction is disclosed, never silent (the deliberate contrast with critical_notes' REFUSE-on-full doctrine, A14); (iv) list is newest-first; (v) remove needs no ownership — retiring an obsolete idea is a normal operation (D11); (vi) caps 300/5000 are truncation, not rejection; (vii) no duplicate detection — the cap + FIFO is the discipline (D5).

Acceptance: PO6 (twin sha equality pre/post); PO4 baseline check; grep shows the new rows in all four files (leader rule, two reviewer twins, docs/agents.md).

### W7 — Baseline re-stamp

From the WORKTREE root:
1. `python3 scripts/protection/generate_prompt_artifact_baseline.py` (regenerates `agents/.prompt-artifacts.sha256`).
2. `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` → exit 0.
3. Verify: row count STILL 181 / 47 dirs; exactly 3 rows changed hash — `agents/leader/rule.md` (row 119), `agents/reviewer/workflow.md` (row 143), `agents/reviewer[v2]/workflow.md` (row 147). meta.json edits (W8) do NOT touch the baseline (A28).
4. Commit the baseline WITH the prompt edits on `feature/project-future`.

Acceptance: PO4.

### W8 — Allow grants (14 meta.json files, category-key style)

Add `"project_future"` to `tools.allow` (array append, category-key style matching leader's existing keys, A27) in EXACTLY these 14 files:

| # | File | Note |
|---|------|------|
| 1 | `agents/leader/meta.json` | allow array already holds `"critical_notes", "project_history"` at :15 — append alongside |
| 2 | `agents/developer/meta.json` | :16 allow array; keep `tools.deny: ["edit_file","write_file"]` untouched |
| 3 | `agents/developer[v2]/meta.json` | :16 |
| 4 | `agents/reviewer/meta.json` | |
| 5 | `agents/reviewer[v2]/meta.json` | |
| 6 | `agents/tidier/meta.json` | |
| 7 | `agents/tidier[v2]/meta.json` | |
| 8 | `agents/approver/meta.json` | |
| 9 | `agents/approver[v2]/meta.json` | |
| 10 | `agents/planner/meta.json` | |
| 11 | `agents/planner[v2]/meta.json` | |
| 12 | `agents/tester/meta.json` | non-v2 only (no tester[v2] exists) |
| 13 | `agents/architect/meta.json` | non-v2 only |
| 14 | `agents/devops/meta.json` | non-v2 only |

Justification (D8): all are leader-team dev-spine members (team_members :17) who do the work and generate improvement ideas; the surface is cheap, non-pinned, 32-capped, eviction-disclosed. EXCLUDED in v1: project-manager (verb-style idiom, not dev-team — easy follow-up), and the support roster (giter, explorer, wanderer, kb-writer, doc-writer, maintenancer, designer, gaia, aegis, coder, worker) — widening is a one-file-per-agent follow-up. Boot-time validation of unknown allow entries is warn-only (A13) — that is why AC24 must assert positively.

Acceptance: AC24 both-directions filter test; no boot WARNING for `project_future` on granted agents.

### W9 — Tests (new files; house idioms per A29)

| File | Idiom | ACs |
|------|-------|-----|
| `tests/unit/test_project_future_tools.py` | MagicMock store + side_effect handlers, direct dict asserts, cap constants imported module-level; factory called WITHOUT manager (defaults None) | AC1–AC9 |
| `tests/test_project_future.py` | tests/ ROOT; `sqlite:///:memory:` + `SQLModel.metadata.create_all` + real `SQLModelProjectRepository`; FK behavioral tests need `PRAGMA foreign_keys=ON` listener (SQLite default is OFF — without it AC16's cascade half silently passes-vacuous) | AC10–AC19 |
| `tests/unit/test_project_future_migration.py` | read the .sql as text; marker greps (pattern: test_project_metadata_table.py:583-611) | AC20 |
| `tests/unit/test_project_future_registration.py` | registry imports; `resolve_tool_filter` level asserts | AC21–AC24 |

**Full AC list:**

| AC | Assertion |
|----|-----------|
| AC1 | add happy path → flat dict with `seq`, `evicted == []`, `source_agent`/`source_instance_id` stamped from factory closure args, no `"error"` key |
| AC2 | add empty summary (whitespace-only) → `{"error": "Summary cannot be empty"}` |
| AC3 | add truncates: summary 400→300 chars; details 6000→5000 (truncate-not-reject, tool layer) |
| AC4 | unknown project on add/list/remove → `{"error": "Project '<pid>' not found"}` (three tools) |
| AC5 | eviction disclosure passes through the tool layer: mock store returns payload with `evicted=[{id, summary, seq, created_at}]` → result carries it verbatim |
| AC6 | list clamps `limit=max(1,min(limit,100))` (e.g. 500→100, 0→1), floors negative offset→0, returns store envelope verbatim |
| AC7 | remove success → `{"success": true, "removed": {...}}`; repo `None` → `{"error": "Entry '<id>' not found in project '<pid>'"}` |
| AC8 | `from daemon.tools.project_future import _MAX_ENTRIES; _MAX_ENTRIES == 32` |
| AC9 | factory returns exactly the 3 tools; each carries `_tool_category == "project_future"` and `_full_doc_` attr |
| AC10 | 32-cap pin: 33rd insert → response `evicted == [item#1 dict incl. id, summary, seq=1, created_at]`; table count == 32 exactly |
| AC11 | FIFO order pin: 35 inserts → evictions are items #1, #2, #3 in order; survivors == #4..#35; `min(seq) == 4` |
| AC12 | same-timestamp tie pin: two entries with IDENTICAL `created_at` → eviction strictly by `seq ASC` (oldest seq goes, timestamps don't decide) |
| AC13 | list newest-first: `entries[0].seq == max(seq)`; ordering seq DESC; envelope `{entries, total, limit, offset}`; repo-level limit clamp |
| AC14 | per-project independence: projects A and B each reach 32; adding A#33 evicts A#1 only; B untouched; B's first add gets seq 1 |
| AC15 | remove: existing → removed dict + count decremented; unknown id → None; wrong project_id → None (scoping preserved) |
| AC16 | FK cascade: behavioral (PRAGMA FK ON; delete project row → future rows gone) + introspection pin `fk.ondelete == "CASCADE"` (pattern test_snapshot_search_service.py:1039) |
| AC17 | repo truncate bounds 300/5000 (repository layer duplicates tool layer, A17) |
| AC18 | drift self-heal: inflate table to 34 rows via direct session inserts → next add evicts 3 → count == 32 |
| AC19 | `UNIQUE(project_id, seq)` enforced: duplicate-seq insert raises `IntegrityError` |
| AC20 | migration file matches `^\d{8}_\d{6}_create_project_future\.sql$`; contains `CREATE TABLE IF NOT EXISTS project_future`, `-- UP`, `-- DOWN`, `ON DELETE CASCADE`, `CREATE UNIQUE INDEX IF NOT EXISTS uq_project_future_project_seq`, `CREATE INDEX IF NOT EXISTS ix_project_future_project_id`, `DROP TABLE IF EXISTS project_future`; DOWN drops exactly the two indexes + table; zero `VARCHAR` tokens (TEXT-never-VARCHAR grep) |
| AC21 | `CATEGORY_MODULES["project_future"] == "daemon.tools.project_future"` |
| AC22 | `KNOWN_TOOL_NAMES ⊇ {project_future_add, project_future_list, project_future_remove}` |
| AC23 | existing drift pin green: `tests/unit/tools/test_frozen_tool_name_discovery.py` passes unchanged |
| AC24 | allow-filter resolves: with `allow=["project_future"]` all three tools survive the filter; with the entry absent all three are dropped (both directions — positive assertions, since boot validation is warn-only) |

NO network, NO live DB, NO migration-runner execution in tests (create_all fixtures sidestep the 20260714_000001 fresh-SQLite breaker, A29).

### W10 — Proof obligations (tester-executable; zero contact with live daemon/install dirs)

- **PO1 — 33-insert demonstration** via the tool lane against a real store (`SQLModelProjectRepository` over in-memory SQLite create_all, tools from the factory): (i) inserts #1..#32 each return `evicted == []` (assert every response); (ii) insert #33 returns `evicted == [item#1 with id + summary + seq=1]` and `project_future_list` total == 32; (iii) #34 evicts #2, #35 evicts #3 (monotone sequence); (iv) final list shows newest-first, newest present, item#1 absent, total == 32.
- **PO2 — per-project independence probe**: two projects each filled to 32 independently; A's eviction list never names a B item.
- **PO3 — cascade probe**: delete the project row (PRAGMA FK ON); its future rows disappear.
- **PO4 — baseline probe**: bare regen + `--check --tree .` exit 0; row count 181; exactly 3 changed hash rows (W7 list).
- **PO5 — drift-pin run**: `tests/unit/tools/test_frozen_tool_name_discovery.py` green.
- **PO6 — reviewer-twin integrity**: `sha256sum agents/reviewer/workflow.md 'agents/reviewer[v2]/workflow.md'` equal BEFORE and AFTER the edit (quote the bracketed dirname).
- **PO7 — assembly-visibility probe** (catches the SILENTLY-INVISIBLE class that no test catches, A12/A13): in-process harness mimicking the instance.py order — build the factory tools, extend a plain list at the W5 position, then run `create_help_tool` + `scan_tools_for_full_docs` + `_apply_tool_filter` (allow=["project_future"]) and assert all three names survive into help's tool_index and the filtered list. (Full live-instance smoke is the reviewer lane's own process; this lane makes zero daemon contact.)

### W11 — Version-slot note (D15)

NOTE ONLY, nothing staged: next free slot is **v0.29.23** (v0.29.21/.22 staged; live v0.29.20 as of 2026-11-12). This lane stages nothing and promotes nothing. ZERO-CONTACT restated: no daemon HTTP, no `/home/nea/agents-ensemble`, no canonical `/home/nea/ensemble-src` (detached HEAD + dirty — never commit there), no push, no merge, no promote.

---

## Data Model Summary (D2/D12)

| Field | SQLModel | DDL | Notes |
|-------|----------|-----|-------|
| `id` | str, uuid4 default_factory, PK | `id TEXT PRIMARY KEY` | sibling :483 |
| `project_id` | str, sa_column FK→projects.project_id, ondelete CASCADE, nullable=False, index=True | `project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE` + `ix_project_future_project_id` | sibling :484-486; FK cascade behavioral pin needs PRAGMA (AC16) |
| `summary` | str, max_length 300 | `summary TEXT NOT NULL` | truncate-not-reject at BOTH layers |
| `details` | str \| None, default None, max_length 5000 | `details TEXT` | nullable |
| `source_agent` | str, default "" | `source_agent TEXT NOT NULL` | NOT NULL per DDL; factory stamps closure `agent_id` (D2) |
| `source_instance_id` | str, default "" | `source_instance_id TEXT NOT NULL` | factory stamps `current_instance_id` (D2) |
| `created_at` | datetime, default_factory `datetime.now(timezone.utc)` | `created_at TIMESTAMP NOT NULL` | history-sibling idiom :496; to_dict isoformats (:509); NOT the file-wide ISO-string idiom — deliberate (D2/R3) |
| `seq` | int, nullable=False | `seq INTEGER NOT NULL` + `UNIQUE INDEX uq_project_future_project_seq(project_id, seq)` | app-assigned `COALESCE(MAX(seq),0)+1` inside the insert transaction (D2/D3) |

No `entry_metadata` JSONB column — deliberate omission (wishlist surface stays minimal; D1). No `entry_type` enum — every row IS a future idea.

---

## FIFO Algorithm — transaction ordering (D3)

Prune-before-insert, ONE session/transaction; the table NEVER holds 33 rows:

```
add_future_entry(project_id, summary, details, source_agent, source_instance_id, max_entries=32):
  1. truncate summary[:300], details[:5000]            # repo-layer house idiom (:1309-1310)
  2. BEGIN (with Session(self.engine))
  3. count   = COUNT(rows WHERE project_id = pid)      # inline func.count/subquery idiom (:1388)
  4. overflow = max(0, count - max_entries + 1)        # make room for the incoming row
  5. if overflow > 0:
       victims = SELECT * WHERE project_id = pid
                 ORDER BY seq ASC LIMIT overflow        # INSERTION order — same-timestamp-safe
       evicted  = [{id, summary, seq, created_at} for victims]
       DELETE victims                                  # in-transaction
  6. seq = (SELECT MAX(seq) WHERE project_id = pid) + 1
  7. INSERT new row(seq=seq)                           # UNIQUE(project_id, seq) guards the race
  8. COMMIT                                            # deletes + insert land atomically
  9. return to_dict() + {"evicted": evicted}           # disclosure, never silent
```

Properties: cap exact at every observation point; drifted tables (count > 32) self-heal (overflow 2+ evicts down, insert lands at exactly 32 — AC18); concurrent-add race loses nothing (IntegrityError rolls back the WHOLE transaction — evictions restored — caller retries, D2); eviction order is exactly insertion order (seq ASC survives same-microsecond timestamps, AC12).

---

## Exact Response Shapes (JSON)

**`project_future_add` success (no eviction):**
```json
{
  "id": "0f1e2d3c-…", "project_id": "<pid>",
  "summary": "≤300 chars", "details": "≤5000 chars or null",
  "source_agent": "<agent_id>", "source_instance_id": "<instance_id>",
  "created_at": "2026-11-12T16:25:08.696867+00:00", "seq": 17,
  "evicted": []
}
```
**add success WITH eviction:** same shape, `"evicted": [{"id": "…", "summary": "…", "seq": 1, "created_at": "…"}]` — the evicted item's id + summary are MANDATORY disclosure (user law). Multi-eviction (drift self-heal) lists victims oldest-first.

**add errors:** `{"error": "Project '<pid>' not found"}` · `{"error": "Summary cannot be empty"}` · `{"error": "<IntegrityError str>"}` (seq race, fail-closed, D2).

**`project_future_list` success:**
```json
{"entries": [ { …entry dicts, seq DESC… } ], "total": 27, "limit": 20, "offset": 0}
```
List errors: `{"error": "Project '<pid>' not found"}`.

**`project_future_remove` success:**
```json
{"success": true, "removed": {"id": "…", "project_id": "<pid>", "summary": "…", "details": null,
  "source_agent": "…", "source_instance_id": "…", "created_at": "…", "seq": 9}}
```
**remove error:** `{"error": "Entry '<item_id>' not found in project '<pid>'"}` (:300 wording).

Errors are ALWAYS a single `{"error": "<string>"}` dict — no codes, no structured fields (A7).

---

## Risk Table

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | **Registration-step omission** — extend lands after :7628 ⇒ SILENTLY INVISIBLE (no test catches it); missing KNOWN entry ⇒ frozen-binary false "unknown tool"; names pasted without CATEGORY_MODULES ⇒ drift-pin CI failure | Medium | High | W5 five-step checklist with exact anchors; AC21–AC23; PO5 drift pin; PO7 assembly-visibility probe (the only guard for the invisible class) |
| 2 | **seq race** — two concurrent adds read MAX(seq)=k, both insert k+1 ⇒ unique violation | Low | Medium | Accepted v1, fail-closed: whole transaction rolls back (evictions restored), error surfaced as `{"error": str}` via the add tool's except-wrap; caller retries; documented in `_full_doc_` + decisions D2 |
| 3 | **datetime-vs-ISO drift** — `created_at` datetime (sibling :496) vs file-wide ISO-string idiom (Project :418, CriticalNoteModel :183) | Medium | Low | Deliberate choice documented (D2); to_dict isoformats (mirror :509); FIFO never load-bears on created_at (seq ASC/DESC decides); AC12 pins seq-decides; DDL TIMESTAMP matches SQLAlchemy DateTime |
| 4 | **prompt-twin divergence** — reviewer/workflow.md vs reviewer[v2]/workflow.md must stay byte-identical | Medium | High | Identical edit string specified (W6b); PO6 sha equality asserted BEFORE and AFTER; baseline rows 143/147 both change (W7) |
| 5 | **migration naming collision** — duplicate `YYYYMMDD_HHMMSS` prefix ⇒ `DuplicateMigrationVersionError` (runner :420) | Low | Medium | Developer stamps real current UTC at file creation; AC20 filename regex; newest existing prefix 20261112_124200 leaves the namespace clear (A22) |
| 6 | **privilege misclassification** — category accidentally joins PRIVILEGED_TOOL_CATEGORIES (strips empty-allow agents; breaks five-pin exact-set assertions :7646-7653); or grants forgotten (boot only WARNS, A13) | Low | High | Category stays non-privileged — zero edits to the privileged set; grants are explicit W8 file list; AC24 asserts BOTH directions positively |
| 7 | **FIFO-vs-REFUSE acceptance friction** — deviation from the critical_notes "no silent eviction" house doctrine reads as data loss | Medium | Medium | D7 deviation statement; disclosure mandatory in the add response (id+summary); `_full_doc_` states the contract verbatim; cap exact at every observation point; AC10/AC5 pin the disclosure |
| 8 | **commit-scope contamination** — committing outside the planning dir, or in canonical /home/nea/ensemble-src (detached HEAD + dirty) | Low | High | All git via `git -C /home/nea/ensemble-src-wt-project-future`; add ONLY the five planning paths; branch + status verified before/after; zero-contact restated (W11) |
| 9 | **cascade test vacuousness** — SQLite does NOT enforce FKs by default ⇒ AC16's behavioral half silently passes | Medium | Low | PRAGMA foreign_keys=ON engine listener specified in W9; structural introspection pin kept as the second leg |

---

## Proof Obligations

See W10 (PO1–PO7). All are tester-executable in-process: no network, no live DB, no daemon HTTP, no install-dir contact. PO1 is the user-law demonstration (33-insert lifecycle with monotone disclosed eviction); PO4–PO6 are the cheap gates after W6/W7/W8; PO7 is the only defense against the silently-invisible registration class.

---

## Out of Scope + Follow-ups

1. **project-manager grant** — verb-style allow idiom (`project_future_add/list/remove` next to its existing `project_history_*`/`project_cn_*` verbs, denies preserved); one-file follow-up.
2. **Reviewer severity-tier doctrine sweep** — the same tier appears in 7 `agents/reviewer/skills-template/*.md` files (code-review.md:54, pr-review.md:57, plan-review.md:53, security-review.md:55, business-logic-review.md:56, architecture-review.md:53, review-strategy.md:231) and `agents/reviewer/rule.md:22,:94` — separate pass.
3. **REST parity** — additive later if asked (D6).
4. **Provenance enrichment** — freeform metadata/provenance beyond source_agent/source_instance_id (e.g. originating job/thread id); additive column later.
5. **Search tool** — `project_history_search` exists; a 32-item list needs no search; add only if usage shows the need.
6. **Support-roster grants** — giter/explorer/wanderer/kb-writer/doc-writer/maintenancer/designer/gaia/aegis/coder/worker if they should file wishes; one meta.json each.
7. **Per-plugin-style update policy N/A** — no external dependency involved.

---

## Research Insights (shaping decisions — see decisions.md for the full D1–D15 rationale)

- The construction seam lives in **instance.py:6651-6655**, not `_tool_registry.py` (research CORRECTION, confirmed by spot-check) — the brief's original location was wrong.
- `created_at` in the sibling is a **datetime**, the ONLY such column in models.py — a fork point this plan resolves deliberately toward the history-sibling idiom (D2/R3).
- critical_notes is the capped CONTRAST (200, REFUSE-on-full, "No silent eviction" verbatim) — our FIFO is a conscious, documented deviation (D7).
- `prune_excess` (blueprint pending_repository:421-477) is the only in-repo FIFO-cap template; it orders by created_at alone (non-deterministic under ties) — our `seq` column hardens exactly that weakness (D2/D3).
- Enforcement of registration is DISTRIBUTED and warn-only at boot; the drift pin is the only hard failure and does NOT catch a missing extend — hence PO7.
- NO dev-spine agent currently holds ANY recording category — project_future is the first persistent-recording channel for the dev team (census A27); discoverability edits (W6) are therefore as load-bearing as the tool itself (D9).
- leader[v2] does not exist; reviewer twins are byte-identical on workflow.md; docs/ is not baseline-hashed — all three facts shape W6/W7 precisely.

## Open Questions

None blocking. Two plan-writer judgment calls are documented for reviewer attention (both within dispatcher steer): (a) `source_agent`/`source_instance_id` modeled NOT NULL with `""` default per the D12 DDL, deviating from the sibling's Optional; (b) a narrow `try/except` wrap on the add tool's store call to keep the seq-race failure inside the `{"error": str}` contract. Everything else follows the dispatcher's D1–D15 steer as written.
