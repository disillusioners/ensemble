# Phase 1: The Workbook (cross-instance persistent work board)

Date: 2026-10-19
Author: worker via plan-creation (commission `ari-tools`, dispatched by planner)
Status: Ready for Review

Commission: **ari-tools** (user-approved 2026-10-19) — P1 of 3.
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`).
Research inputs: `research-db-agents.md`, `research-scheduler-tools.md` (same directory). All anchors below are re-verified against THIS checkout.

---

## 1. Objective

Ship a **DB-backed, globally-namespaced work board** for the Ari agent, exposed as **four agent tools** in a NEW non-privileged tool category `"board"`:

`board_create` · `board_update` · `board_list` · `board_get`

Items carry: stable id `wi-<uuid8>`, title, body_md, status enum (`todo|in_progress|blocked|done`), tags[], nullable project_id (FK), nullable tz-aware due_at, linked job_ids[], created_by_instance, created_at, updated_at, optional priority.
**Notes are append-only** `{ts, author_instance, text}` — no edits, no deletes, ever.
Channel/source and project are **TAGS on items**, never separate boards.

Phase 2 (`self_alarm`) and Phase 3 (live board view) hang off THIS schema. Build order 1→2→3 is binding.

## 2. Scope

**In (phase 1):** 3 new tables (items / notes / item-tag junction), a `BoardService`, 4 agent tools, registry + construction + allow-list wiring, tests, one SQLite migration file.

**Out (phase 1):** REST router, frontend, `self_alarm`, `view_write`, live board view, job-lifecycle auto-update (documented only, §9), any edit to `agents/ari/rule.md` / `agents/ari/workflow.md` / `agents/leader/rule.md` (SEAL-pinned — `tests/test_chart_image_delivery_e2e.py:1350-1398`).

**Not a replacement for `todo`:** the `todo` family is an in-memory, per-instance DAG (`daemon/services/todo_manager.py:186-214`); the board is durable and global. Both coexist; the distinction is documented (T10).

---

## 3. File map + edit-site checklist

### 3.1 New files

| # | Path | Contents |
|---|------|----------|
| N1 | `daemon/models/board.py` | SQLModel tables `BoardItem`, `BoardNote`, `BoardItemTag`; status constants; `_now_iso()` (fixed-width, §6.1) |
| N2 | `daemon/services/board_service.py` | `BoardService` + error hierarchy (`BoardError`, `BoardItemNotFound`, `BoardValidationError`) |
| N3 | `daemon/tools/board_tools.py` | Category module: `CATEGORY_NAME` / `CATEGORY_DOC` / 4 `@tool` fns + `*Input` BaseModels + `_full_doc_` |
| N4 | `daemon/migrations/versions/20261019_190000_create_board_tables.sql` | SQLite DDL for the 3 tables + indexes (grammar `daemon/migrations/README.md:38-49`; parser `daemon/migrations/runner.py:87-95`) |
| N5 | `tests/unit/test_board_service.py` | Service behaviour (SQLite in-memory) |
| N6 | `tests/unit/tools/test_board_tools.py` | Tool layer (mocked service) |
| N7 | `tests/migration/test_board_index_parity.py` | 3-site index lockstep |
| N8 | `tests/unit/test_board_registration.py` | Registration + allow-list proof |

> **N4 prefix safety (do this FIRST, before writing the file):** the runner enforces one ledger row per version prefix (`schema_migrations.version TEXT PRIMARY KEY`, `daemon/migrations/runner.py:211-220`); a duplicate prefix breaks a fresh SQLite boot (`:543` apply txn + `:735-751` loop) and the documented symptom is `UNIQUE constraint failed: schema_migrations.version` (`daemon/migrations/README.md:462-468`). The newest existing migration is `20261019_120822_alter_resource_monitor_booleans.sql`; `20261019_190000` is fresh and strictly greater. **Verify at write time:** `ls daemon/migrations/versions/ | grep '^20261019_19'` must be empty — if not, bump the seconds (e.g. `20261019_190500`) and re-verify. Do **not** copy the pre-existing `20261018_000001_*` duplicate-prefix defect.

### 3.2 Edit sites (existing files)

| # | File | Exact site | Change |
|---|------|-----------|--------|
| E1 | `daemon/manager.py` | late-import block, immediately **before** `SQLModel.metadata.create_all(self._engine)` (`:730-748` block, `create_all` at `:750`) | `from .models.board import BoardItem, BoardNote, BoardItemTag  # noqa: F401` — the resource_monitor P1 registration precedent |
| E2 | `daemon/manager.py` | right after `self._secret_service = SecretService(engine=self._engine)` (`:1381`) | `self._board_service = BoardService(engine=self._engine)` (+ import at `:133`-style top import block) |
| E3 | `daemon/manager.py` | next to the `scheduling_service` property (`:13534-13555`) | new read-only `board_service` property returning `self._board_service` |
| E4 | `daemon/tools/_tool_registry.py` | `DYNAMIC_TOOL_NAMES` (`:23-179`; `shared_meta_kv` at `:58`) | + `board_create`, `board_update`, `board_list`, `board_get` |
| E5 | `daemon/tools/_tool_registry.py` | `CATEGORY_MODULES` (`:690`+; `shared_meta_kv` at `:736`) | + `"board": "daemon.tools.board_tools"` |
| E6 | `daemon/tools/_tool_registry.py` | `KNOWN_TOOL_NAMES` (`:909`+; `shared_meta_kv` at `:1087`) | + the same 4 names (frozen-binary fallback; bidirectional drift pin `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) |
| E7 | `daemon/tools/instance.py` | top import block (`:259-260` pattern) | `from .board_tools import create_board_tools` |
| E8 | `daemon/tools/instance.py` | new `create_board_tools_if_available(manager, current_instance_id, agent_id, agent_tag)` (mirror `create_scheduling_tools_if_available`, `:2271-2300`) | returns `[]` when `manager.board_service` is absent (partial-init safe) |
| E9 | `daemon/tools/instance.py` | extend inside `create_instance_tools` (`:2450`; sibling extends at `:5768-5775`, `:5995`, `:6002-6003`) | `tools.extend(create_board_tools_if_available(manager, current_instance_id, agent_id, agent_tag=version_tag))` — the **third** step of the three-step registration seam (decorator + registry + construction) |
| E10 | `agents/ari/meta.json` | `tools.allow` array | + `"board"` |
| E11 | `agents/leader/meta.json` | `tools.allow` array | + `"board"` |

**No `daemon/tools/_auth.py` entry** — the board needs no backing agent spawn (`TOOL_REQUIRED_AGENTS` is only for categories that must spawn: `daemon/tools/_auth.py:35-51`).
**No `PRIVILEGED_TOOL_CATEGORIES` change** — the category is non-privileged, so the SAME-PR five-pin ceremony (`daemon/tools/_tool_registry.py:226-238`) does not apply. (Consequence disclosed as Q1.)

### 3.3 Exact meta.json diffs

`agents/ari/meta.json` (NOT seal-pinned):
```diff
       "secrets",
-      "settings"
+      "settings",
+      "board"
     ],
     "deny": ["edit_file", "write_file", "watch_job", "watch_jobs"]
```

`agents/leader/meta.json` (NOT seal-pinned; single-line allow array):
```diff
-    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings"]
+    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings", "board"]
```

Appending at the END of both arrays is deliberate: minimal, symmetric, and least likely to collide with a concurrent edit. **Do NOT touch** `agents/ari/rule.md`, `agents/ari/workflow.md`, `agents/leader/rule.md` (seal-pinned).

---

## 4. Schema

### 4.1 `board_items`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `"wi-" + uuid4().hex[:8]` (the `s-`/`n-` shape precedent, `daemon/services/todo_manager.py:1282-1304`) |
| `title` | TEXT NOT NULL | ≤512 |
| `body_md` | TEXT NOT NULL DEFAULT `''` | markdown body |
| `status` | TEXT NOT NULL DEFAULT `'todo'` | `todo\|in_progress\|blocked\|done`; service-validated (no DB CHECK — see D10) |
| `project_id` | TEXT NULL | **FK → `projects.project_id`**, `ON DELETE SET NULL` (D14) |
| `due_at` | TEXT NULL | UTC ISO-8601, **fixed-width microseconds** (§6.1) |
| `priority` | INTEGER NULL | 1–5 (1 lowest, 5 highest); NULL = unset; service-validated |
| `linked_job_ids` | JSONB NOT NULL DEFAULT `'[]'` | `Column("linked_job_ids", JSONBType)` — dialect-aware JSONB/JSON (`daemon/repositories/infra/types.py:35-71`) |
| `created_by_instance` | TEXT NOT NULL | stamped by the service from the tool closure (agent cannot spoof) |
| `created_at` | TEXT NOT NULL | fixed-width UTC ISO |
| `updated_at` | TEXT NOT NULL | fixed-width UTC ISO; bumped on any successful mutation |

Indexes (3-site byte-identical lockstep, D7 of the resource_monitor precedent):
`idx_board_items_status(status)` · `idx_board_items_project_id(project_id)` · `idx_board_items_due_at(due_at)` · `idx_board_items_created_at(created_at)`

### 4.2 `board_notes` (APPEND-ONLY child table)

`id INTEGER PK AUTOINCREMENT` · `item_id TEXT NOT NULL` (FK → `board_items.id`, **no CASCADE** — RESTRICT semantics) · `ts TEXT NOT NULL` · `author_instance TEXT NOT NULL` · `text TEXT NOT NULL`
Index: `idx_board_notes_item_id_ts(item_id, ts)`

Mirrors the repo's append-only audit precedent: `SecretAuditEvent` (model `daemon/repositories/secrets/models.py:156+`; DDL `daemon/migrations/versions/20261017_000001_create_secrets_tables.sql:156-165`; index `(secret_id, at)` → ours `(item_id, ts)`).

### 4.3 `board_item_tags` (junction table)

`item_id TEXT NOT NULL` (FK → `board_items.id`) · `tag TEXT NOT NULL` · **composite PK `(item_id, tag)`** (dedupe for free — the `project_tags` precedent, `daemon/repositories/project/models.py:146-158`) · index `idx_board_item_tags_tag_item(tag, item_id)` for the reverse tag filter.

### 4.4 Storage decisions and their justification

- **tags[] → JUNCTION TABLE, not embedded JSON.** `board_list` filters by tag ⇒ a *relational* filter ⇒ the repo's established filterable pattern is the junction table (`project_tags`, `daemon/repositories/project/models.py:146-158`); the embedded `JSONBType` list is the established pattern for lists that are **not** relationally filtered (`snapshots.domain_tags` `daemon/repositories/snapshot/models.py:194-197`; `blueprint.tags`; `projects.related_directories`). A JSON list filter would need PG-native `?`/`@>` vs SQLite `json_extract` — two dialects, two query shapes, no index parity; the junction table gives one identical SQL on both engines plus an index. **No PG-native `TEXT[]`** anywhere in the ORM (repo rule).
- **notes → CHILD TABLE, not a JSON column.** Appending to a JSON array rewrites the whole array — that *is* an edit of history and it loses updates between concurrent appenders. A child table gives immutable rows, an indexed timeline read, and a shape with nowhere to record a mutation.
- **linked_job_ids → embedded JSONBType list, no junction table.** v1 has **no** job filter on `board_list` ⇒ no relational need; job ids are opaque external handles (`job_queue_items.job_id`, `daemon/repositories/job_queue/models.py:272-318`) whose rows are **soft-deleted** (`deleted_at`) — a hard FK would either block job deletion or cascade-delete a board item. Append = read-modify-write; safe under the service write lock (§5.2).
- **due_at / created_at / updated_at → ISO-8601 TEXT** (repo convention — never DB `now()`), **always tz-aware UTC, always microseconds** (§6.1). `priority` → nullable INTEGER 1–5, validated at the service boundary (the "forward-compatible enum tag, frozenset-validated at the service boundary, NOT at the model" precedent, `daemon/repositories/secrets/models.py:44-52`).

---

## 5. Service API surface + manager wiring

### 5.1 Pick: **CLASS** (`BoardService`), not module functions

Justification:
1. The service has exactly **one** dependency (the `Engine`) — it is engine-bound, like `SecretService` (`daemon/services/secret_service.py:1129-1171`, wired at `daemon/manager.py:1381`).
2. `scheduling_service`'s module singleton exists because it needs a lazily-configured **dependency set** (source repo + registry) resolved from the manager (`daemon/services/scheduling_service.py:233-284`, `daemon/manager.py:13534-13555`). The board needs none of that — a class avoids the module-global-mutation footgun.
3. The service owns a write lock (§5.2); a lock on a module singleton is awkward, a lock on an instance is natural.
4. Unit-testability: the tool factory takes the service by injection → tests inject a mock (the `tests/unit/tools/test_scheduling_tools.py` precedent).

### 5.2 API surface (`daemon/services/board_service.py`)

```python
class BoardError(RuntimeError): ...
class BoardItemNotFound(BoardError): ...
class BoardValidationError(BoardError): ...

class BoardService:
    def __init__(self, engine: Engine) -> None: ...
    def create_item(self, *, title: str, body_md: str = "", project_id: str | None = None,
                    tags: list[str] | None = None, due_at: str | None = None,
                    priority: int | None = None, created_by_instance: str) -> dict: ...
    def update_item(self, *, item_id: str, status: str | None = None, note: str | None = None,
                    add_job_id: str | None = None, body_md: str | None = None,
                    due_at: str | None = None, author_instance: str) -> dict: ...
    def list_items(self, *, status: str | None = None, project_id: str | None = None,
                   tag: str | None = None, due_before: str | None = None,
                   limit: int = 50) -> dict: ...
    def get_item(self, *, item_id: str) -> dict: ...
    # internal, called only by update_item — the ONLY notes mutation in the codebase:
    def _append_note(self, session, *, item_id: str, author_instance: str, text: str) -> dict: ...
```

- **Write serialization:** a `threading.RLock` held across the whole write transaction (create/update). Mirrors the shared_meta_kv repository lock discipline (`daemon/repositories/shared_meta_kv/repository.py:42-78`): under a SQLite `StaticPool` concurrent writers corrupt the prepared-statement cache. It also makes the `linked_job_ids` read-modify-write atomic. The daemon is single-process today, so a process-level lock is sufficient.
- **Transaction shape:** one `Session(self.engine)` block per write; the note INSERT is stamped **inside the same transaction** as the item UPDATE (the prompt_versions / secrets_audit "audit-event stamped inside the same transaction" contract, `daemon/repositories/secrets/models.py:156+`).
- **Item projection** (`_row_to_item`): `id, title, body_md, status, tags[], project_id, due_at, priority, linked_job_ids[], created_by_instance, created_at, updated_at`. `board_get` adds `notes[]` (oldest-first).

### 5.3 Manager wiring

- E1: late-import of the models before `SQLModel.metadata.create_all(self._engine)` (`daemon/manager.py:738-750`) — the resource_monitor P1 registration precedent ("register the 4 observability tables BEFORE create_all so the PG boot path lands them on existing PG databases").
- E2: `self._board_service = BoardService(engine=self._engine)` next to `SecretService` (`daemon/manager.py:1381`).
- E3: `board_service` property next to `scheduling_service` (`daemon/manager.py:13534-13555`).
- **No `_ensure_postgres_columns` entry** — these are brand-new tables; `create_all` creates them. Explicit precedent: "Both are brand-new tables … No `_ensure_postgres_columns` mirror needed" (`daemon/api.py:893-907`). The `create_all` route does not ALTER existing tables (`daemon/manager.py:6597-6605`) — irrelevant now, but it means the schema is **frozen at first boot**: any later column ADD in phase 2/3 needs both an `_ensure_postgres_columns` entry and a `.sql` ALTER.

### 5.4 Restart-survival argument

1. All state lives in the DB (PG primary; SQLite dev). The service holds **no** in-memory state beyond the engine handle and the lock — there is no cache to lose.
2. On boot the manager materialises the tables: PG via `SQLModel.metadata.create_all` (`daemon/manager.py:750`, models registered by E1); SQLite via the `.sql` file through the migration runner (`daemon/manager.py:753-757`, `daemon/migrations/runner.py:699-726` is SQLite-only, PG is a no-op there).
3. Therefore a restart re-reads the same rows: items, notes, and tags created before the restart are visible after it, from any instance, on any chat source.
4. **Test proof (N5):** create rows with service A → drop the tables → re-run `SQLModel.metadata.create_all(engine)` (the boot path) → assert the tables exist again **and** the rows are still present. Assert `create_all` is idempotent (running it twice raises nothing).

---

## 6. Global namespace + timestamp discipline

### 6.1 Global namespace (why the board is tree-agnostic)

The board schema contains **no partitioning column at all** — no `context_key`, no `tree_root_id`, no `source`, no `project`-scoped table. Every `board_*` tool reads and writes the same three tables. This is the deliberate opposite of `shared_meta_kv`, which partitions by `context_key = tree-root instance id` (resolved lazily inside the tool body — `daemon/tools/shared_meta_kv_tools.py:109-122`; the column itself — `daemon/repositories/shared_meta_kv/models.py:30-55`). Sibling Ari instances on different chat sources, and the leader, therefore see **one** board. Project and channel are expressed as *attributes of an item* (`project_id` nullable FK; channel/source as a plain tag string, e.g. `telegram`), never as separate boards.

**Timestamp discipline (D7 — deliberate deviation):** all board timestamps are **fixed-width** UTC ISO-8601 with microseconds always present, i.e. `datetime.now(timezone.utc).isoformat(timespec="microseconds")` → `2026-10-20T01:55:40.123456+00:00`. Reason: the `due_before` filter compares **TEXT lexicographically**, which is only chronologically correct under a fixed-width format. The house `_now_iso()` (`daemon/repositories/project/models.py:21-30`) omits microseconds when zero → variable width → wrong ordering at second boundaries. The board uses a **board-local** helper; the house helper is not changed (no blast radius).

---

## 7. Tool contracts

Category: **`board`** (non-privileged). Module: `daemon/tools/board_tools.py`.
Factory: `create_board_tools(board_service, *, current_instance_id: str, agent_id: str, agent_tag: str | None = None) -> list` — the `create_scheduling_tools` signature shape (`daemon/tools/scheduling.py:62-91`). The wrapper `create_board_tools_if_available` (E8) resolves the service from the manager.

**Three-step registration (all three required — decorator-only is SILENTLY INVISIBLE):** `@register_tool_category("board")` **above** `@tool(args_schema=...)` + a one-line docstring + a `_full_doc_` attribute (`:342-367` registry; `daemon/tools/scheduling.py:56-63, 267-294`) → `DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` entries (E4/E5) → the `tools.extend(...)` inside `create_instance_tools` (E9).

### 7.1 Input models (Pydantic `BaseModel` + `Annotated[T, Field(description=…)]`, `daemon/tools/scheduling.py:95-108`)

```python
class BoardCreateInput(BaseModel):
    title: Annotated[str, Field(description="Short human title (1-512 chars)")]
    body: Annotated[str, Field(default="", description="Markdown body. Stored as body_md.")]
    project_id: Annotated[str | None, Field(default=None, description="Optional project id (nullable FK)")]
    tags: Annotated[list[str] | None, Field(default=None, description="Tags, e.g. ['telegram','deploy']")]
    due_at: Annotated[str | None, Field(default=None, description="tz-aware ISO-8601 deadline, e.g. '2026-10-21T09:00:00+07:00'")]
    priority: Annotated[int | None, Field(default=None, description="1 (lowest) .. 5 (highest); omit for unset")]

class BoardUpdateInput(BaseModel):
    id: Annotated[str, Field(description="Board item id, e.g. 'wi-1a2b3c4d'")]
    status: Annotated[str | None, Field(default=None, description="todo | in_progress | blocked | done")]
    note: Annotated[str | None, Field(default=None, description="Append a note to the timeline. Notes are append-only.")]
    add_job_id: Annotated[str | None, Field(default=None, description="Link a job id to this item")]
    body: Annotated[str | None, Field(default=None, description="Replace the markdown body")]
    due_at: Annotated[str | None, Field(default=None, description="Replace the deadline (tz-aware ISO-8601)")]

class BoardListInput(BaseModel):
    status: Annotated[str | None, Field(default=None, description="todo | in_progress | blocked | done | 'open' (= todo|in_progress|blocked) | omit for all")]
    project_id: Annotated[str | None, Field(default=None, description="Filter by project id")]
    tag: Annotated[str | None, Field(default=None, description="Filter by a single tag")]
    due_before: Annotated[str | None, Field(default=None, description="Only items due strictly before this tz-aware ISO-8601 instant")]
    limit: Annotated[int, Field(default=50, description="Max items to return (1-200)")]

class BoardGetInput(BaseModel):
    id: Annotated[str, Field(description="Board item id")]
```

### 7.2 Signatures, returns, errors

| Tool | Signature | Returns |
|---|---|---|
| `board_create` | `(title, body="", project_id=None, tags=None, due_at=None, priority=None)` | **dict** = the created item (`id` at the top level, `notes: []` absent — a fresh item has no notes) |
| `board_update` | `(id, status=None, note=None, add_job_id=None, body=None, due_at=None)` | **dict** = the updated item (no notes timeline) + `"note_appended": bool` |
| `board_list` | `(status=None, project_id=None, tag=None, due_before=None, limit=50)` | **dict** = `{"items": [...], "count": N}`; each item without notes; then `truncate_dict_result(result, "items", limit=…)` adds truncation metadata (`daemon/tools/_truncate.py:113`) |
| `board_get` | `(id)` | **dict** = the full item + `"notes": [{"ts":…, "author_instance":…, "text":…}, …]` oldest-first |

- **Item shape (canonical, all four tools):** `{id, title, body_md, status, tags[], project_id, due_at, priority, linked_job_ids[], created_by_instance, created_at, updated_at}`. The **input** param is `body` (bound shape); the **stored/returned** field is `body_md` (bound item-field name) — the docstring states the mapping explicitly.
- **Error envelope — tools NEVER raise** (`daemon/tools/scheduling.py:57-59`): every failure returns the string `"ERROR: {msg}"`.
  - `BoardItemNotFound` → `"ERROR: board item not found: wi-xxxx"`.
  - `BoardValidationError` → `"ERROR: <authored safe message>"` (e.g. `"ERROR: due_at must be a timezone-aware ISO-8601 string"`).
  - Unexpected exception → log server-side (`logger.exception`) and return `"ERROR: board operation failed"` — **no** `str(exc)`/traceback in the payload (the PB-F1 leak class; `daemon/tools/secrets_tools.py:47-58`).
- **Status transitions — UNRESTRICTED (any → any, including reopening a `done` item).** Rationale: the board is a human-facing work surface, not a workflow engine; a rigid state machine would force a fake "cancel" path to undo a mistaken close. The audit trail is the **notes timeline**, not the status field. The only validation is membership in the 4-value enum (service boundary).
- **updated_at maintenance:** bumped on any successful `board_update` that changed something (status, body, due_at, linked job ids, **or** an appended note). A pure read never bumps it. A `board_update` with only `id` and no mutable field is a **no-op** (returns the item unchanged, no bump, no note).
- **Open-items convention helper:** `board_list(status="open")` ⇒ `status IN ('todo','in_progress','blocked')` — `done` is the only terminal status. Documented in the tool docstring **and** in `CATEGORY_DOC`. No other magic values; `status=None` means no filter.

### 7.3 Append-only notes — structural enforcement

1. **Absence of mutation APIs.** The service exposes exactly one notes mutation: `_append_note`, which issues a single `INSERT`. There is **no** `update_note` / `delete_note` / `edit_note` anywhere — the update path is *structurally incapable* of editing or deleting history because no such code path exists.
2. **Shape has nowhere to hide a mutation.** `board_notes` has no `updated_at`, no `deleted_at`, no `revision` column — the schema itself cannot express "this note was edited".
3. **No cascade.** `board_notes.item_id` FK has **no** `ON DELETE CASCADE`; and no `board_delete` tool exists in v1, so notes can never be orphaned by a delete.
4. **Same-transaction insert.** The note row is stamped inside the same transaction as the item mutation (audit-event contract).
5. **Guard test (N5/N6).** A static source assertion that `daemon/services/board_service.py` contains no `UPDATE`/`DELETE` against `BoardNote`, plus a runtime assertion that the note count only ever grows across a create/update cycle. (Static-audit style precedent: `tests/unit/test_log_secret_static_audit.py`.)

---

## 8. Tests

| File | Covers | Exemplar |
|---|---|---|
| N5 `tests/unit/test_board_service.py` | create/get/update/list; status transitions incl. reopening `done`; **notes append-only** (count grows; no update/delete API; static source scan); tag filter; project filter; `status="open"` helper; `due_before` filter; update of a missing id → `BoardItemNotFound`; validation errors (naive `due_at`, priority out of range, unknown status); **restart survival**: drop tables → `SQLModel.metadata.create_all(engine)` → tables re-created **and** rows still present; `create_all` idempotency; write-lock concurrency (two threads appending notes → both rows present) | `tests/unit/test_shared_meta_kv_repo.py`, `tests/unit/test_shared_meta_kv_concurrency.py` |
| N6 `tests/unit/tools/test_board_tools.py` | factory returns exactly 4 tools; each Input schema (required/optional, defaults); **never raise** (a service that raises → the tool returns an `"ERROR: …"` string); error envelopes for not-found / validation; `board_list(status="open")` passes through; returned item shape; `board_update` note appends and bumps `updated_at`; `board_create` stamps `created_by_instance` from the closure (not agent-supplied) | `tests/unit/tools/test_scheduling_tools.py` (mocked service), `tests/unit/test_shared_meta_kv_tool.py` (mocked manager) |
| N7 `tests/migration/test_board_index_parity.py` | 3-site index lockstep: model `__table_args__` index names == `.sql` `CREATE INDEX` names, **byte-identical**; the migration declares every column of every table; the composite PK `(item_id, tag)` exists in both | `tests/migration/test_secrets_index_parity.py`, `tests/migration/test_prompt_versions_index_parity.py` |
| N8 `tests/unit/test_board_registration.py` | `"board"` ∈ `agents/ari/meta.json` and `agents/leader/meta.json` allow lists; the 4 names ∈ `DYNAMIC_TOOL_NAMES` **and** `KNOWN_TOOL_NAMES` **and** the source-discovered universe; `CATEGORY_MODULES["board"]` resolves; every meta.json allow entry is a known tool or category | `tests/unit/test_scheduling_registration.py` |
| N9 (no new file) | `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` **must stay green** — E4+E5+E6 together satisfy the bidirectional pin (`:223-242`) | existing |

**Allow-list gating (N8, mandatory):** assert an agent whose allow list contains `"board"` resolves to the 4 tools, and an agent whose allow list is **non-empty and lacks** `"board"` resolves to none. Use a non-empty allow list for the negative case — an **empty** allow list means "everything allowed" (`daemon/tools/instance.py:373-384`). This is exactly the default-grant consequence disclosed in Q1.

---

## 9. v2 — job-lifecycle auto-update (DOCUMENT ONLY, no build steps)

**Trigger events:** the job lifecycle — `job_queue_items.admission_state` transitions (`QUEUED → ACTIVE → DONE | DEAD`, `daemon/services/job_state_machine.py`) and the job's `terminal_reason`.

**Proposed updates (v2, not built here):** when a job referenced by a board item reaches `DONE`, auto-append a system-authored note (`"job <id> completed"`) and, if the item is `todo|in_progress`, flip it to `done`; when the job reaches `DEAD`, append a note and flip the item to `blocked`. A new `author_instance` convention for system-authored notes (e.g. `system:job-queue`) is required.

**Why deferred:** (a) there is no lifecycle-event seam for an arbitrary table — the job-completion path lives inside `daemon/services/task_processor.py` / `daemon/services/child_reports.py`, the same area as the RDRS six-lane report-delivery sweep (`daemon/services/report_delivery_recovery.py`); touching it risks the report-delivery path. (b) Auto-writing notes changes the **authorship model** of an append-only audit trail (system author vs. instance author) and needs a documented convention before any row is written. (c) It requires the job-completion path to reach the `BoardService`, a manager-level dependency the service does not currently hold. Deferring until phase-1 traffic exists keeps the phase-1 diff small and the audit trail's provenance unambiguous.

---

## 10. Task checklist (dependency-ordered)

- [ ] **T1** — `daemon/models/board.py`: 3 SQLModel tables + status constants + `_now_iso` (fixed-width). Import-compile check.
- [ ] **T2** — `daemon/migrations/versions/20261019_190000_create_board_tables.sql`. **First** verify prefix uniqueness (`ls daemon/migrations/versions/ | grep '^20261019_19'` → empty); bump seconds if needed. Header comment block + `-- UP` / `-- DOWN` sections (the `20261018_090004_create_resource_limit_policy.sql` template); no inline `;` in comments (the runner's comment stripper does not strip inline comments containing `;`).
- [ ] **T3** — `daemon/services/board_service.py`: `BoardService` + errors + lock + transactions + projections.
- [ ] **T4** — manager wiring **E1** (late import) + **E2** (construction) + **E3** (property).
- [ ] **T5** — `daemon/tools/board_tools.py`: 4 tools, 4 Input models, `CATEGORY_NAME`/`CATEGORY_DOC`, `_full_doc_` per tool.
- [ ] **T6** — registry **E4** + **E5** + **E6**.
- [ ] **T7** — construction **E7** + **E8** + **E9**.
- [ ] **T8** — allow-lists **E10** + **E11** (exact diffs in §3.3).
- [ ] **T9** — tests **N5**, **N6**, **N7**, **N8** (+ re-run N9 green).
- [ ] **T10** (optional, deferrable) — `agents/ari/tools_note.md`: a `## Board` section (NOT seal-pinned; obey `docs/agent-prompt-writing-guide.md` §1 — no `meta.json` / `tools.allow` / `daemon/` tokens in agent-facing prose). Not required for phase-1 acceptance; `tool_help` + `_full_doc_` already cover discovery.
- [ ] **T11** — verification sweep: `tests/unit/tools/test_frozen_tool_name_discovery.py`, `tests/unit/test_scheduling_registration.py`, the seal test `tests/test_chart_image_delivery_e2e.py` (must be green — no sealed file touched), and a boot smoke (dev port 8079, `POSTGRES_DB != ensemble_prod`).

**Do NOT:** commit a `PRIVILEGED_TOOL_CATEGORIES` change, edit any seal-pinned file, or promote/stage a release (promote is the user's nonce ceremony).

### 10.1 Coupling

- **Tight with Phase 2 (`self_alarm`)** — shares the **schema** this phase freezes: the alarm horizon reads `due_at` (tz-aware UTC, indexed) and `status`; the item/author provenance it will report comes from `created_by_instance` + the notes timeline. Any phase-2 column ADD after the first board boot is a migration event (R4). Phase 2 must not need a column that phase 1 does not create.
- **Tight with Phase 3 (live board view)** — shares the **service** (`BoardService.list_items` / `get_item`) and the global-namespace contract: the view must render the SAME single namespace the tools see, with no per-chat or per-project partition (D1). Phase 3 decides whether the view calls the service directly or via a REST router (Q6).
- **Loose with the `todo` tool family** — shares a domain (user work tracking) but no data structure: `todo` is per-instance and in-memory (`daemon/services/todo_manager.py:186-214`), the board is global and durable. Only the agent-facing wording couples them (R9 / T10).
- **Independent of** the job-queue lifecycle in v1 — the `linked_job_ids` column stores opaque job ids but v1 never reads them back; the auto-update path is deferred to v2 (§9) precisely to keep this phase decoupled from `daemon/services/task_processor.py` and the report-delivery path.

---

## 11. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | **`wi-<uuid8>` is 32 bits of entropy** — birthday collision: P≈1 at ~77k items, P≈0.0001 at 1k items | Low | Low | `id` is the **PRIMARY KEY** → a collision raises `IntegrityError` → the create fails loudly with an `ERROR:` envelope (no silent overwrite, no data loss) and the agent retries with a fresh id. The shape is fixed by the binding; flagged, not changed. Human-scale board ⇒ negligible in practice |
| R2 | **Non-privileged category ⇒ default-grant** — every empty-allow agent inherits the board tools (`daemon/tools/instance.py:373-384`), diluting "ari AND leader" | Medium | High (certain) | Disclosed as **Q1** with a recommendation (keep non-privileged in v1; the board is a benign work surface with no secrets and no authority). If the architect rules privileged, the cost is the SAME-PR five-pin update (`daemon/tools/_tool_registry.py:226-238`) — add to the task list, do not skip the pins |
| R3 | **FK behaviour differs across drivers** — `project_id REFERENCES projects(project_id) ON DELETE SET NULL` is enforced by PG; SQLite ignores FKs unless `PRAGMA foreign_keys=ON` (off by default), so a deleted project leaves a dangling `project_id` on SQLite | Low | Medium | Acceptable: the item survives either way and the value is informational. No code path depends on the FK's SET NULL behaviour (project filter is by value, not by join) |
| R4 | **Schema freeze** — `create_all` does not ALTER existing tables (`daemon/manager.py:6597-6605`); after first boot any phase-2/3 column ADD needs both an `_ensure_postgres_columns` entry **and** a `.sql` ALTER | Medium | Medium (certain, if phase 2 adds a column) | §7 success criterion 7 pins the phase-2/3 schema hooks NOW (`due_at`, `status`, `tags`, `linked_job_ids`) so no later ADD is required. Documented in §5.3 |
| R5 | **`linked_job_ids` read-modify-write** — safe only under the service write lock; a future multi-process writer needs row-level locking | Low | Low | The daemon is single-process today; the `threading.RLock` (§5.2) serialises all board writes. Flagged for any future multi-process deployment |
| R6 | **No per-item ACL** — any agent holding the category can write the global board | Low | Medium | Consistent with the repo trust model ("the only access boundary is the per-agent `tools.allow` category gate", `daemon/services/secret_service.py:1163-1171`); the board carries no secrets and grants no authority. Revisit only if the board ever holds sensitive content |
| R7 | **Unbounded growth** — no retention policy in v1; `board_notes` grows monotonically | Low | Medium | `board_list` is bounded by `limit`; archival is a v2 topic. Add a retention decision before the board exceeds a few thousand rows |
| R8 | **Migration prefix collision** with a concurrent commission (duplicate prefix ⇒ fresh-SQLite boot failure, `daemon/migrations/README.md:462-468`) | High | Low | T2 verifies prefix uniqueness **before** writing (`ls daemon/migrations/versions/ \| grep '^20261019_19'` must be empty) and bumps the seconds if needed. Never copy the pre-existing `20261018_000001_*` duplicate-prefix defect |
| R9 | **`todo` confusion** — agents have an existing per-instance `todo` family (`daemon/tools/todo_tools.py`); the board is durable + global | Medium | Medium | The distinction is documented (T10, `agents/ari/tools_note.md`) and stated in the tool docstrings + `CATEGORY_DOC`: todo = ephemeral per-turn planning, board = durable cross-instance user work |
| R10 | **Phase-1 schema under-specifies phase 2/3** — `self_alarm` (horizon ≥30 min) and the live view need fields not bound in phase 1 | Medium | Medium | §7 criterion 7 pins the four hooks phase 2/3 need (`due_at` indexed + tz-aware, `status` indexed, `tags` junction, `linked_job_ids` JSONB). Any further need is an explicit additive change in a later phase, not a phase-1 rework |

---

## 12. Open questions for the architect

- **Q1 — Should `"board"` be PRIVILEGED (opt-in) rather than non-privileged?** A non-privileged category is default-granted to every empty-allow agent (`daemon/tools/instance.py:373-384`), which dilutes "ari AND leader". Making it privileged costs the SAME-PR five-pin update (`tests/unit/tools/test_upgrade_registration.py`, `tests/unit/tools/test_attestation_registration.py`, `tests/integration/test_maintenancer_spawn_resolves_tools.py`, `tests/integration/test_service_tool_flag_off_byte_identical.py`, `tests/unit/tools/test_secrets_registration.py:218`). **Recommendation:** keep non-privileged in v1 (benign global work surface — no secrets, no authority) and document the default-grant consequence; revisit only if the board ever carries sensitive content.
- **Q2 — v1 cannot CLEAR `due_at` / `project_id` / `priority`** (the bound `board_update` shape has no clear-flag). Options: (a) keep the bound shape, document the limitation; (b) `""`/`"null"` sentinels; (c) a `clear_fields` param later. **Recommendation:** (a) now, (c) as a v1.1 amendment — the bound shape is not re-litigated in this plan.
- **Q3 — `board_list` has no pagination cursor.** A global board larger than `limit` cannot be paged. **Recommendation:** accept `limit` + truncation metadata in v1; add `offset`/`cursor` when the board exceeds a few hundred items.
- **Q4 — Should items be deletable?** v1 has no delete path (history preservation, the `schedule_cancel`-is-terminal precedent). If the user needs "remove this item", add a **soft** delete (an `archived`/soft-delete marker), never a hard delete — the notes audit trail must survive. **Recommendation:** soft delete in v1.1 on demand.
- **Q5 — Author provenance.** `created_by_instance` / note `author_instance` are stamped by the **service** from the tool closure, so an agent cannot spoof them. Confirm this is the intended contract (vs. an agent-supplied author).
- **Q6 — REST twin in phase 1?** The bound phase-1 shape is tools-only; phase 3 adds the live view. **Recommendation:** no REST router in phase 1 (the phase-3 view can call the service directly, or a router can be added then).

---

## 13. Design decisions made in this phase plan

| # | Decision | Rationale + anchor |
|---|----------|--------------------|
| D1 | **One global table set; no `context_key`/tree/project/source partitioning column** | The board must be cross-instance; per-tree partitioning is exactly what `shared_meta_kv` does (`daemon/tools/shared_meta_kv_tools.py:109-122`, `daemon/repositories/shared_meta_kv/models.py:30-55`) and what the board must NOT do |
| D2 | **`tags[]` = junction table `board_item_tags`** | `board_list` filters by tag ⇒ relational filter ⇒ the repo's filterable pattern (`project_tags`, `daemon/repositories/project/models.py:146-158`); the embedded-JSONB list is the pattern for non-filtered lists (`daemon/repositories/snapshot/models.py:194-197`); no PG `TEXT[]` in the ORM |
| D3 | **Notes = append-only child table `board_notes`** | Appending to JSON rewrites history (violates "no edits, ever") and races; mirrors the append-only audit precedent (`daemon/repositories/secrets/models.py:156+`, `20261017_000001_create_secrets_tables.sql:156-165`, index `(secret_id, at)` → `(item_id, ts)`) |
| D4 | **`linked_job_ids` = embedded JSONBType list, no FK** | No v1 job filter ⇒ no relational need; job rows are soft-deleted (`daemon/repositories/job_queue/models.py:272-318`) ⇒ a hard FK would block/ cascade job deletion |
| D5 | **Service = CLASS `BoardService`** | Engine-bound, one dependency, no lazily-configured dep set; mirrors `SecretService` (`daemon/services/secret_service.py:1129-1171`, wired `daemon/manager.py:1381`); the module-singleton pattern exists for `scheduling_service`'s registry/repo wiring, which the board lacks |
| D6 | **Category `"board"` is NON-privileged** | Avoids the SAME-PR five-pin ceremony (`daemon/tools/_tool_registry.py:226-238`) for a benign work surface; default-grant consequence disclosed as Q1 |
| D7 | **All timestamps fixed-width UTC ISO-8601, microseconds always on** (board-local `_now_iso`) | The `due_before` filter compares TEXT lexicographically — only correct under a fixed width. **DEVIATION** from the house `_now_iso()` (`daemon/repositories/project/models.py:21-30`), which drops microseconds when zero; the house helper is left untouched |
| D8 | **`due_at`: naive input rejected; tz-aware accepted; stored as UTC** | No tz-chain in phase 1 (the scheduling chain is heavy — `daemon/services/scheduling_service.py:342-359`); phase 2's `self_alarm` owns tz resolution |
| D9 | **Status transitions unrestricted (any→any; `done` reopenable); enum validated at the service boundary** | Human-facing work surface, not a workflow engine; the notes timeline is the audit trail; the "forward-compatible enum tag validated at the service boundary, NOT at the model" precedent (`daemon/repositories/secrets/models.py:44-52`) |
| D10 | **`priority` = nullable INTEGER 1–5, service-validated, no DB CHECK** | Small ordered scale for a human-visible work item; no native enums in the ORM; service-boundary validation per D9's precedent |
| D11 | **Registered in THREE registry surfaces** (`DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES`) | Factory-built tools must be in `DYNAMIC_TOOL_NAMES` or boot allow-list validation rejects the category; `CATEGORY_MODULES` makes the AST scanner see the module; `KNOWN_TOOL_NAMES` is the frozen-binary fallback and is under a bidirectional drift pin. The `shared_meta_kv` precedent appears in all three (`:58`, `:736`, `:1087`) |
| D12 | **Model registration = late-import in `daemon/manager.py` before `create_all`** (not the api.py lifespan) | The manager site is on every boot path (`daemon/manager.py:738-750`); the resource_monitor precedent. The api.py lifespan (`daemon/api.py:893-907`, secrets) is API-server-only |
| D13 | **No `_ensure_postgres_columns` entry** | Brand-new tables — `create_all` creates them; explicit precedent at `daemon/api.py:893-907` |
| D14 | **`project_id` is a real FK → `projects.project_id` with `ON DELETE SET NULL`** | The binding says "nullable FK"; `projects` is registered in `SQLModel.metadata` at manager import (`daemon/manager.py:44` → `daemon/repositories/project/repository.py:17`) so `create_all` can order it; SET NULL keeps the board item alive when a project is hard-deleted (`daemon/repositories/project/repository.py:1088+`) |
| D15 | **`"board"` appended at the END of both allow arrays** | Minimal, symmetric diffs; least likely to collide with a concurrent edit; both `meta.json` are NOT seal-pinned (the pinned set is `agents/ari/workflow.md`, `agents/ari/rule.md`, `agents/leader/rule.md` — `tests/test_chart_image_delivery_e2e.py:1350-1398`) — this phase touches none of them |
| D16 | **No REST surface in phase 1** | The bound phase-1 shape is tools-only; phase 3 adds the live view |
| D17 | **The board is NOT a replacement for `todo`** | `todo` is an in-memory per-instance DAG (`daemon/services/todo_manager.py:186-214`); the board is durable and global. The distinction must be documented (T10) |

---

## 14. Success criteria

1. `board_create` → `board_update` → `board_list` → `board_get` work from an Ari instance and from a Leader instance, and both see the **same** items (global namespace proven across two different `current_instance_id` values).
2. Items, notes, and tags survive a daemon restart (DB-backed; `create_all` + migration runner paths both green).
3. Notes are provably append-only (no update/delete code path; count grows monotonically; static source scan green).
4. `board_list` filters correctly by `status` (incl. `status="open"`), `project_id`, `tag`, and `due_before`.
5. The 4 tools are visible only to agents whose allow list includes `"board"` (allow-list gating test green) and the `KNOWN_TOOL_NAMES` drift pin stays green.
6. Zero seal violations: `tests/test_chart_image_delivery_e2e.py` green; `agents/ari/{rule,workflow}.md` and `agents/leader/rule.md` byte-identical.
7. The phase-2 (`self_alarm`) and phase-3 (live view) schema hooks are present: `due_at` (tz-aware, indexed), `status` (indexed), `tags` (junction), `linked_job_ids` (JSONB) — no phase-2/3 column added later.

---

## 15. Exit Criterion

Phase 1 is **done** when all of the following hold (this is the signal that Phase 2 may start):

1. `board_create` / `board_update` / `board_list` / `board_get` are bound for an Ari instance and a Leader instance, and both observe the **same** global namespace (verified by a cross-instance test with two different `current_instance_id` values — §14 criterion 1).
2. The three tables exist on both drivers: PG via `SQLModel.metadata.create_all` at the manager boot, SQLite via the `.sql` migration through the runner; the migration file's version prefix is unique (`ls daemon/migrations/versions/ | grep '^20261019_19'` → empty).
3. Items, notes, and tags written before a restart are readable after it (DB-backed; §5.4).
4. Notes are provably append-only: no update/delete code path exists, the count grows monotonically, the static source scan is green.
5. The 4 tool names are present in `DYNAMIC_TOOL_NAMES`, `CATEGORY_MODULES`, and `KNOWN_TOOL_NAMES`; the bidirectional drift pin (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) is green; allow-list gating is proven for a non-empty allow list.
6. No seal violation: `tests/test_chart_image_delivery_e2e.py` is green and `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` are byte-identical to `630cefebb`.
7. No release is staged or promoted by this phase — promote remains the user's nonce ceremony.

**Not a phase-1 exit condition (deliberately):** the v2 job-lifecycle auto-update (§9), the REST surface (Q6), the live board view (Phase 3), `self_alarm` (Phase 2), and the tools_note documentation (T10, deferrable).
