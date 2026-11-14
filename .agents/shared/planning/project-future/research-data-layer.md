# Project Future — Data-Layer & Test Anchor Research (READ-ONLY verification)

Worktree: `/home/nea/ensemble-src-wt-project-future` (branch `feature/project-future`, base `ac9849bf7`).
All line numbers verified against THIS worktree on 2026-11-12. Method: direct file reads; no DB, no daemon, no git mutations.

---

## Q1 — Model: `daemon/repositories/project/models.py`

### ProjectHistoryEntry — VERIFIED at :479 (claim exact)

```python
479: class ProjectHistoryEntry(SQLModel, table=True):
480:     """SQLModel ProjectHistoryEntry table - tracks project history entries."""
481:     __tablename__ = "project_history"
482:
483:     id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
484:     project_id: str = Field(
485:         sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
486:     )
487:     entry_type: str = Field()
488:     summary: str = Field(max_length=300)
489:     details: str | None = Field(default=None, max_length=5000)
490:     source_agent: str | None = Field(default=None)
491:     source_instance_id: str | None = Field(default=None)
492:     entry_metadata: dict | None = Field(
493:         default=None,
494:         sa_column=Column(JSONBType)
495:     )
496:     created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
497:
498:     def to_dict(self) -> dict[str, Any]:
499:         """Convert to dictionary for serialization."""
500:         return {
501:             "id": self.id,
...
510:         }
```

Key idioms:
- **FK ondelete**: `sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)` (:484-486) — plain `Field(foreign_key=...)` is NOT used for the cascading FK; the `sa_column=` escape hatch is.
- **PK**: `default_factory=lambda: str(uuid.uuid4())` (:483).
- **created_at default idiom**: ⚠️ **`datetime` object, NOT ISO string** — `created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))` (:496). This DIFFERS from the other project tables (`Project` :418, `CriticalNoteModel` :183, `ProjectMetadataRecord` :369 all store ISO-8601 strings via `_now_iso()`/inline lambda). `to_dict()` isoformat()s it (:509). A `ProjectFutureEntry` sibling must choose deliberately: `datetime` (history precedent) or ISO string (everything else in this file).
- **JSONB dict column**: `entry_metadata` via `sa_column=Column(JSONBType)` (:492-495) — import at :19 `from daemon.repositories.infra.types import JSONBType`.
- No `__table_args__` on ProjectHistoryEntry (no indexes/constraints beyond the column-level `index=True`).

### Full class inventory of the file (510 lines)

| Line | Name | Kind |
|---|---|---|
| :25 | `_now_iso()` | module helper (single source of truth for created_at/updated_at/minted_at ISO defaults) |
| :37 | `CriticalNotesCategory` | str enum |
| :44 | `CriticalNotesPriority` | str enum |
| :49 | `CriticalNotes` | **pydantic BaseModel** (wire shape, NOT a table) |
| :104 | `ProjectStatus` | str enum |
| :116 | `ProjectType` | str enum |
| :135 | `HistoryEntryType` | str enum (milestone/commit/phase/bugfix/deployment/note/config_change/feature/other) |
| :147 | `ProjectTagLink` | table `project_tags` (composite PK junction) |
| :162 | `ProjectShortnameLink` | table `project_shortnames` |
| :175 | `CriticalNoteModel` | table `critical_notes` |
| :293 | `CriticalNoteEmbeddingModel` | table `critical_note_embeddings` |
| :359 | `ProjectMetadataRecord` | table `project_metadata_records` |
| :377 | `Project` | table `projects` |
| :479 | `ProjectHistoryEntry` | table `project_history` (LAST class in file) |

**LOUD FLAG — the model file is SHARED across domains**: critical notes, note embeddings, metadata, tags, shortnames all live here. "Modeled on the project_history data lane" does NOT mean a dedicated file; the idiomatic seat for `ProjectFutureEntry` is **appended after `ProjectHistoryEntry` at end of file (:512+)** — it is the last class, nothing follows it.

---

## Q2 — Repository: `daemon/repositories/project/repository.py` (2450 lines)

Class `SQLModelProjectRepository` (:56). **Constructor takes an `Engine`** (:59-61): `def __init__(self, engine: Engine): self.engine = engine`. Sessions are obtained **per-call** via `with Session(self.engine) as session:` blocks — no session-factory injection.

### add_history_entry — VERIFIED at :1285 (claim exact)

```python
1285:     def add_history_entry(
1286:         self,
1287:         project_id: str,
1288:         entry_type: str,
1289:         summary: str,
1290:         details: str | None = None,
1291:         source_agent: str | None = None,
1292:         source_instance_id: str | None = None,
1293:         entry_metadata: dict | None = None,
1294:     ) -> dict:
...
1309:         summary = summary[:300]
1310:         details = details[:5000] if details else None
1311:
1312:         with Session(self.engine) as session:
1313:             entry = ProjectHistoryEntry(
...
1322:             session.add(entry)
1323:             session.commit()
1324:             session.refresh(entry)
1325:             return entry.to_dict()
```

Session/commit idiom: add → commit → refresh → `to_dict()`. Truncation happens at the REPOSITORY layer (:1309-1310), not just the tool layer.

### delete_history_entry — VERIFIED at :1340 (claim exact)

```python
1340:     def delete_history_entry(self, entry_id: str, project_id: str | None = None) -> bool:
...
1350:         with Session(self.engine) as session:
1351:             entry = session.get(ProjectHistoryEntry, entry_id)
1352:             if entry is None:
1353:                 return False
1355:             if project_id is not None and entry.project_id != project_id:
1356:                 return False
1358:             session.delete(entry)
1359:             session.commit()
1360:             return True
```

`session.get` PK lookup + optional ownership check → bool.

### list_history_entries — VERIFIED at :1362 (claim exact)

```python
1362:     def list_history_entries(
1363:         self,
1364:         project_id: str,
1365:         entry_type: str | None = None,
1366:         limit: int = 20,
1367:         offset: int = 0,
1368:     ) -> dict:
...
1380:         with Session(self.engine) as session:
1381:             stmt = select(ProjectHistoryEntry).where(
1382:                 ProjectHistoryEntry.project_id == project_id,
1383:             )
1384:             if entry_type:
1385:                 stmt = stmt.where(ProjectHistoryEntry.entry_type == entry_type)
1387:             # Get total count
1388:             count_stmt = select(func.count()).select_from(stmt.subquery())
1389:             total = session.exec(count_stmt).one()
1391:             # Apply ordering and pagination
1392:             stmt = stmt.order_by(ProjectHistoryEntry.created_at.desc()).offset(offset).limit(limit)
1393:             entries = list(session.exec(stmt))
1395:             return {
1396:                 "entries": [e.to_dict() for e in entries],
1397:                 "total": total,
1398:                 "limit": limit,
1399:                 "offset": offset,
1400:             }
```

**Ordering**: `order_by(created_at.desc())` — NEWEST-FIRST (:1392; same in search :1440 and get_recent_history :1465). **Count**: NOT a standalone helper — inline `select(func.count()).select_from(stmt.subquery())` (:1388, repeated :1436). The only standalone count in the repository is `count_pinned_critical_notes` (:2023-2045), which is `len(list(session.exec(...)))`-style.

### count helper?

No generic `count(...)`. Inline subquery-count idiom above; `count_pinned_critical_notes` (:2023) is the only named count method (critical-notes-specific).

### Multi-row delete-in-transaction idiom for FIFO prune — TWO precedents in-repo

1. **⭐ `blueprint` pending queue `prune_excess`** — `daemon/repositories/blueprint/pending_repository.py:421-477` — the canonical **"FIFO by oldest" cap eviction** (:448 comment says exactly that):
   ```python
   421:     def prune_excess(self, project_id: str, max_records: int = 100) -> int:
   ...
   434:         with Session(self.engine) as session:
   435:             count_stmt = (select(BlueprintPendingUpdate)... )
   442:             existing = len(list(session.exec(count_stmt)))
   443:             if existing <= max_records:
   444:                 return 0
   446:             overflow = existing - max_records
   451:             overflow_stmt = (select(...id)... .order_by(col(...created_at).asc()).limit(overflow))
   460:             ids_to_delete = [row for row in session.exec(overflow_stmt).all()]
   463:             from sqlalchemy import bindparam
   465:             delete_stmt = text("DELETE FROM project_blueprint_pending_updates WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
   469:             result = session.execute(delete_stmt, {"ids": ids_to_delete})
   470:             session.commit()
   471:             deleted = int(result.rowcount or 0)
   ```
   Count → overflow N → SELECT oldest ids `created_at ASC LIMIT N` → DELETE by expanding-bindparam IN → commit → rowcount. Docstring (:426-433): "FIFO — deletes the oldest **unprocessed** rows over the cap."
2. **`_sync_tags_bulk`** — same project repository (:103-113): DELETE-all + bulk INSERT + `session.commit()` in ONE session — proves multi-statement transactions are the house norm here (no per-statement commit discipline, no forbid).

Session idiom therefore does NOT forbid multi-statement transactions; prune-before-insert in one `with Session(self.engine)` block is fully precedented.

### Sibling methods worth mirroring

- `get_history_entry(entry_id) -> dict | None` (:1327-1338) — `session.get` + `to_dict`.
- `search_history_entries` (:1402-1449) — escaped ILIKE over summary + coalesced details, count subquery, same ordering/pagination dict shape.
- `get_recent_history(project_id, limit=10) -> list[dict]` (:1451-1469).

---

## Q3 — Registration: TWO `__init__.py` sites

### Package-level — `daemon/repositories/project/__init__.py` (15 lines, VERIFIED full)

```python
3: from .repository import SQLModelProjectRepository
4: from .models import Project, ProjectTagLink, ProjectShortnameLink, ProjectStatus, ProjectType, ProjectHistoryEntry, HistoryEntryType
6: __all__ = [
...
13:     "ProjectHistoryEntry",
15: ]
```
A `ProjectFutureEntry` must be appended to BOTH the :4 import line and `__all__` here.

### Top-level — `daemon/repositories/__init__.py` (343 lines)

- :4-5 re-exports ONLY `SQLModelProjectRepository`, `Project`, `ProjectStatus`, `ProjectType`. **`ProjectHistoryEntry` is NOT re-exported at top level** — the sibling need not be either.
- :214-219 `__all__` "# Project" block mirrors exactly those four names.
- **The create_all registration comment idiom** (what a brand-new table follows — mission-record precedent at :95-107):
  ```python
  95: # Mission-record anchor tables (completion-subsystem Proposal B PR1).
  96: # Imported here so ``SQLModel.metadata.create_all()`` (called from
  97: # ``daemon/manager.py``) registers the ``mission_records`` /
  98: # ``mission_commissions`` tables. Brand-new tables — no
  99: # ``_ensure_postgres_columns`` entry is needed. The SQLite-side
  100: # canonical DDL ships in the migration runner
  101: # (``20261025_122631_create_mission_records.sql`` + ...
  107: from .mission_record.models import MissionCommission, MissionRecord
  ```
  Same pattern at :85-91 (report_injections), :109-114 (instance_ui_prefs), :118-128 (blueprint tables incl. `project_blueprint_pending_updates` :122-124), :130-136 (prompt_version), :165-174 (custom_agent_configs).
- Factory functions live in `daemon/repositories/factory.py`, re-exported at :185-212 (`create_project_repository` etc.) — a new repo would add `create_project_future_repository` there + to the import/`__all__` (not read in detail this pass).

### ⚠️ SECOND registration lane — `daemon/api.py` per-model create_all blocks

`daemon/api.py:1220-1383` carries ~15 per-model `X.metadata.create_all(manager.engine)` calls (StoredWatch :1233, PromptVersion :1250, CustomAgentConfig :1262, PromptAssentToken :1322, WedgeCapture :1334, AnomalyReport :1346-1347, PromptTemplateBaseline :1383, …). Recent tables use THIS lane (late-import + per-model create_all) INSTEAD of only the repositories-`__init__` import. Both are precedented; mission_records uses the repositories-`__init__` lane; prompt-assent/anomaly/wedge use the api.py lane.

---

## Q4 — Migrations

### Filename convention — VERIFIED: `YYYYMMDD_HHMMSS_desc.sql` in `daemon/migrations/versions/`

Newest files (timestamp prefixes): `20261112_124200_add_snapshot_agent_space_key.sql` (newest overall, additive-column), newest NEW-TABLE = `20261109_021154_create_memoir_v2_tables.sql`; also `20261110_221500_create_prompt_assent_tokens.sql`.

### `20261025_122631_create_mission_records.sql` — VERIFIED as the claimed canonical template (90 lines, full structure)

- **Header**: `-- Migration:` / `-- Created:` / `-- Author:` / `-- Description:` prose block (:1-44) including rationale, index trade-offs, and the dual-driver note (:32-38): *"On PostgreSQL the table is created by ``SQLModel.metadata.create_all()`` (the model is registered before create_all at manager boot — snapshot precedent for brand-new tables, **no _ensure_postgres_columns mirror**); the migration runner is a NO-OP there. Schema mirrors daemon/repositories/mission_record/models.py."*
- **`-- UP` marker** (:46), **`CREATE TABLE IF NOT EXISTS mission_records (...)`** (:57-73) — every column TEXT/INTEGER/BOOLEAN, `created_at TEXT NOT NULL`.
- **`CREATE INDEX IF NOT EXISTS`** (:76-77) + partial `CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE ...` (:82-84).
- **`-- DOWN` marker** (:86): `DROP INDEX IF EXISTS` ×2 then `DROP TABLE IF EXISTS` (:88-90) — DOWN drops only what UP created.

### Newer template with stricter doctrine — `20261109_021154_create_memoir_v2_tables.sql` (217 lines)

Dual-driver notes (:30-62): **"EVERY STRING COLUMN IS TEXT (D1.2 / AM-6) — never VARCHAR, on either driver"** (:42); 3-site index lockstep with model `__table_args__` (:50-58); **"WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY)"** (:64-73) — create_all runs FIRST on the boot path, so a bare CREATE would raise "already exists" and the runner would swallow it on its idempotent-skip branch; plain `ADD COLUMN` mandatory (SQLite has no `ADD COLUMN IF NOT EXISTS` — :75-80).

### `_ensure_postgres_columns` — brand-new tables need NO entry — VERIFIED (3+ distinct sites)

- `daemon/manager.py:1025-1029` (comment immediately above the pok late-import + create_all): *"(SQLModel.metadata.create_all is the PG source-of-truth per the app_settings / resource_monitor / board / bash_spawn / PSL / memoir precedent). **Brand-new tables ⇒ NO ``_ensure_postgres_columns`` mirror**; SQLite path is the Phase-1 .sql files"* — followed by the live precedent itself: `from .repositories.pok.models import (...)` # noqa F401 "register before create_all" (:1031-1034) then `SQLModel.metadata.create_all(self._engine)` (:1036), then MigrationRunner (:1039-1041).
- `daemon/manager.py:1045-1049` (call-site comment): *"create_all() only creates tables that don't exist — it does NOT add columns to existing tables, and the migration runner skips non-SQLite engines."*
- `daemon/manager.py:8421-8429` (the method's own docstring — its charter is ADD-ONLY columns to EXISTING tables; :8461-8462 notes the single DROP exception).
- Bonus documentation: repositories/__init__.py :85-91, :95-107, :109-114, :118-121, :130-136, :165-174 all repeat "Brand-new table(s) — no _ensure_postgres_columns needed".

⚠️ Fresh-SQLite-boot caution (matches the live critical note): prefix `20260714_000001_widen_job_queue_type_constraint.sql` breaks EVERY fresh-SQLite boot (documented in the project's critical notes; the file exists at that name in this worktree). New-table migrations with unique prefixes are unaffected — but test-lane fresh-SQLite DB construction should be aware.

---

## Q5 — Tests

Three test files: `tests/unit/test_project_history_tools.py` (tool layer, MagicMock store, 743 lines), `tests/test_project_history.py` (repository layer, real in-memory SQLite, 500 lines — NOTE: at tests/ ROOT, not tests/unit/), `tests/test_project_history_api.py` (API layer, not read in detail).

### Tool-layer house idiom — `tests/unit/test_project_history_tools.py`

**No DB at all** — store is a `MagicMock` with handler `side_effect`s (:18-109):

```python
18: def make_mock_store(initial_entries: list = None):
27:     store = MagicMock()
30:     project = MagicMock()
31:     project.project_id = "test-project-id"
33:     store.get.return_value = project
36:     entries = initial_entries if initial_entries is not None else []
104:     store.add_history_entry.side_effect = add_history_entry_handler
105:     store.delete_history_entry.side_effect = delete_history_entry_handler
106:     store.list_history_entries.side_effect = list_history_entries_handler
107:     store.search_history_entries.side_effect = search_history_entries_handler
```

Fixtures (:117-164): `mock_store` → `history_tools = create_project_history_tools(mock_store, current_instance_id="test-instance", agent_id="test-agent")` (:126-128; **`manager` is NOT passed — defaults None**) → per-tool lookup by `tool.name` (`add_tool`/`list_tool`/`search_tool`/`delete_tool`).

**Assertion idiom: DIRECT DICT, no json.loads round-trip** — `.invoke({...})` returns the dict the tool body returned:

```python
175:     def test_add_valid_entry_with_all_fields(self, add_tool, mock_store):
177:         result = add_tool.invoke(
178:             {"project_id": "test-project-id", "entry_type": "deployment", "summary": "...", "details": "...", "entry_metadata": {...}}
186:         )
187:         assert "error" not in result
188:         assert result["project_id"] == "test-project-id"
...
192:         assert result["source_agent"] == "test-agent"
193:         assert result["source_instance_id"] == "test-instance"
```

The tool layer stamps `source_agent`/`source_instance_id` from the factory's closure args (agent_id/current_instance_id) — :192-193 confirm the expected wiring. File also imports cap constants from the tool module: `from daemon.tools.project_history import create_project_history_tools, _MAX_SUMMARY_LEN, _MAX_DETAILS_LEN` (:9) — the house idiom for a 32-item cap constant is a module-level `_MAX_...` imported by tests.

### Repository-layer idiom — `tests/test_project_history.py` (tests/ ROOT)

```python
10: @pytest.fixture
11: def engine():
13:     engine = create_engine("sqlite:///:memory:", echo=False)
14:     SQLModel.metadata.create_all(engine)
15:     yield engine
16:     engine.dispose()
19: @pytest.fixture
20: def store(engine):
22:     return ProjectStore(engine)   # = SQLModelProjectRepository
25: @pytest.fixture
26: def project(store):
28:     return store.create(name="Test Project")
```

In-memory SQLite + full `SQLModel.metadata.create_all` + real repository — no fixtures file, no PG, no fakes. Representative bodies: `test_add_history_entry` (:34-51, dict-key asserts incl. `"created_at" in entry`), `test_add_history_entry_truncates_summary` (:72-81, `len == 300`), `test_get_history_entry_not_found` (:114+).

### Migration-structure test precedent — `tests/unit/test_project_metadata_table.py:583-611`

Reads the .sql file as text and asserts markers: `assert "CREATE TABLE" in content`, `"DROP TABLE" in content`, `"-- UP" in content`, `"-- DOWN" in content` (:587-590), plus constraint/cascade greps (`assert "ON DELETE CASCADE" in content`, :603-611). A `project_future` migration test can copy this verbatim.

### FK-cascade-on-project-delete tests

- No dedicated project_history-cascade test found. Closest imitable precedents:
  - `tests/unit/test_board_service.py:574-594` — introspects SQLAlchemy FK objects: `fk.ondelete is None` / `fk.ondelete == "SET NULL"` on table metadata.
  - `tests/unit/test_snapshot_search_service.py:1039` — `assert fk.ondelete == "CASCADE"` (same introspection idiom).
  - `tests/repositories/infra/test_edge_case_verification.py:736` `TestHistoryOnDelete` + `tests/repositories/infra/test_infra_repository.py:606` — behavioral delete-then-check-history tests (infra-scoped, ON DELETE SET NULL semantics there).

---

## Q6 — Wiring: what `store` is

**`store` = `manager.project_store`** — and `manager.py:2151`: **`self.project_store = self._project_repository`** (comment :2150 "Keep backward compatible name for tools") — i.e. the store IS the `SQLModelProjectRepository` singleton bound to the shared engine (constructed via `create_project_repository(engine=...)` earlier in manager init; `project_store` alias at :2151).

Call site — `daemon/tools/instance.py:6651-6655`:

```python
6651:     history_tools = create_project_history_tools(
6652:         manager.project_store, current_instance_id, agent_id,
6653:         manager=manager,
6654:     )
6655:     tools.extend(history_tools)
```

Factory signature — `daemon/tools/project_history.py:106-109`: `def create_project_history_tools(store, current_instance_id: str = "", agent_id: str = "", manager=None) -> list:`. Inside, the tools use `store.get(project_id)` for project validation (:139) and `store.add_history_entry(...)`-style calls; `manager` is threaded ONLY for the Blueprint pending-queue sidecar (C8: `feature`/`milestone` entry types feed `project_blueprint_pending_updates`, `_PENDING_QUEUE_ENTRY_TYPES` frozenset at :103) — comment at instance.py:6647-6650: factory still works with manager=None.

**Attribute path a new tool factory would mirror**: `manager.project_store.<method>` if methods are added to `SQLModelProjectRepository` (closest mirror — history lane has no separate repo class), OR construct a sibling repo near manager.py:2151 (e.g. `self._project_future_repository = create_project_future_repository(engine=self._engine)` + alias) and pass THAT to `create_project_future_tools(<repo>, current_instance_id, agent_id)`. Note `manager.project_store` is also passed to other factories (`create_plane_sync_tools(manager.project_store)` at instance.py:6555; critical notes at :6641-6643 pass it plus instance/agent ids) — the `(store, current_instance_id, agent_id, manager=...)` 4-arg shape is the established pattern.

---

## Corrections & surprises (plan-affecting)

1. ✅ All four claimed anchors VERIFIED EXACT: model :479, add :1285, delete :1340, list :1362. No stale anchors.
2. ⚠️ **`ProjectHistoryEntry.created_at` is a `datetime`, not ISO string** — the ONLY project-tree table with that choice (:496 vs `_now_iso()` string idiom used by Project :418, CriticalNoteModel :183, ProjectMetadataRecord :369). FIFO ordering by `created_at ASC` works for both, but the sibling must pick deliberately; string-ISO keeps file-internal consistency, datetime keeps history-lane consistency. Tie-break note: `prune_excess` orders by `created_at` alone — same-microsecond rows have no deterministic tie-break (id secondary order would harden this).
3. ⚠️ **models.py is a shared multi-domain file** (critical notes + embeddings + metadata + tags live here); `ProjectHistoryEntry` is the LAST class — `ProjectFutureEntry` appends at :512+.
4. ⚠️ **Top-level `daemon/repositories/__init__.py` does NOT re-export `ProjectHistoryEntry`** — only Project/Status/Type (:4-5, :214-219). Registration contract for a new table = (a) model import somewhere before `create_all` (repositories-`__init__` mission-record style, OR the newer `daemon/api.py:1220-1383` per-model `X.metadata.create_all(manager.engine)` lane — both live), (b) package `__init__` export if tool code imports from the package.
5. ⚠️ **api.py second create_all lane exists** (~15 per-model calls) — pick ONE lane consciously; mixing both is harmless for create_all (idempotent) but the house comments cite exactly one.
6. ✅ Multi-statement transactions are the NORM (`_sync_tags_bulk` delete+insert+commit in one session, repository.py:103-113; `prune_excess` full count→select→delete→commit, pending_repository.py:434-470). Nothing forbids prune-before-insert in a single session.
7. ⚠️ The best FIFO-cap template is NOT in the project repository — it's `BlueprintPendingRepository.prune_excess` (blueprint/pending_repository.py:421-477), docstring literally "FIFO by oldest". Mirror it (or better: count + `order_by(created_at).asc().limit(overflow)` + expanding-bindparam IN delete).
8. ⚠️ Repository-layer truncation duplication: caps are enforced BOTH in tool (`_MAX_SUMMARY_LEN`/`_MAX_DETAILS_LEN`, imported by tests :9) and repository (:1309-1310). A 32-item cap constant should follow the module-level `_MAX_...` idiom in the tool module.
9. ✅ Migration template claim CONFIRMED (mission_records 20261025_122631 full structure above) but NEWER new-table migrations exist with stricter written doctrine (memoir v2 20261109_021154: TEXT-never-VARCHAR D1.2/AM-6, IF-NOT-EXISTS-mandatory rationale, 3-site index lockstep) — follow the newer doctrine notes even if mimicking mission_records' shape.
10. ⚠️ Fresh-SQLite boot on this tree is blocked by pre-existing prefix `20260714_000001` (live critical note, file present here) — any new test lane that builds a fresh SQLite DB via the MIGRATION RUNNER will hit it; in-memory `create_all`-style fixtures (test_project_history.py idiom) sidestep migrations entirely and are the safe default for unit tests.
11. ℹ️ Blueprint pending-queue sidecar: `project_history_add` feeds the C8 queue for feature/milestone entries only when `manager=` is passed (project_history.py:103, instance.py:6647-6654). Decide explicitly whether `project_future_add` should hook it (probably not — it's a forward-improvement list, not a structural history event).
