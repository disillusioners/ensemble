# Phase 1: The Workbook (cross-instance persistent work board)

Date: 2026-10-19
Author: worker via plan-creation (commission `ari-tools`, dispatched by planner)
Status: **APPROVED** (reviewer re-gate — 0 critical findings) · **approver iteration 002 amendment in progress** — the A2 / A3 / A4 / A6 items from `.agents/approver/ari-tools-tracking.md` are folded into the body below by this pass (see the *Approver iteration 002* amendment log at the end of this document).

Commission: **ari-tools** (user-approved 2026-10-19) — P1 of 3.
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`).
Research inputs: `research-db-agents.md`, `research-scheduler-tools.md` (same directory). All anchors below are re-verified against THIS checkout.

---

## 1. Objective

Ship a **DB-backed, globally-namespaced work board** for the Ari agent, exposed as **four agent tools** in a NEW **privileged** tool category `"board"` — entry #9 of `PRIVILEGED_TOOL_CATEGORIES` (§13 D6):

`board_create` · `board_update` · `board_list` · `board_get`

**Access posture — two layers, BOTH required.** The category is privileged ⇒ the board is **opt-in-only** and never joins the default-grant universe: an empty allow list expands to every category *except* the privileged ones (`daemon/tools/instance.py:460-470`), and the default-allow strip removes only privileged categories (`daemon/tools/instance.py:6243-6271`; the no-`tools`-key path at `:6300-6304`; the `None` ⇒ all-minus-privileged path at `:6340`). A privileged category alone is **not** sufficient, because a **literal tool name** in an allow list bypasses the category gate entirely (`daemon/tools/instance.py:475-478` — an allow entry that is not a category key is added verbatim). So all four tool bodies additionally gate on `agent_id ∈ {"ari","leader"}` through **one** shared module-level helper (`daemon/tools/board_tools.py::_is_board_authorized`; there is no two-agent-set precedent in `daemon/tools/` — the `blueprint` gate is single-agent, `daemon/tools/blueprint.py:71-73`).

Items carry: stable id `wi-<uuid8>`, title, body_md, status enum (`todo|in_progress|blocked|done`), tags[], nullable project_id (FK), nullable tz-aware due_at, linked job_ids[], created_by_instance, created_at, updated_at, optional priority.
**Notes are append-only** `{ts, author_instance, text}` — no edits, no deletes, ever.
Channel/source and project are **TAGS on items**, never separate boards.

Phase 2 (`self_alarm`) and Phase 3 (live board view) hang off THIS schema. Build order 1→2→3 is binding.

## 2. Scope

**In (phase 1):** 3 new tables (items / notes / item-tag junction), a `BoardService`, 4 agent tools, registry + construction + allow-list wiring, **the privileged-category registration (entry #9 of `PRIVILEGED_TOOL_CATEGORIES`) + the SAME-PR five-pin pass**, tests, one SQLite migration file.

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
| N4 | `daemon/migrations/versions/20261020_060000_create_board_tables.sql` | SQLite DDL for the 3 tables + indexes, with **both** an `-- UP` and a `-- DOWN` section (the `-- DOWN` section is mandatory — `rollback_migration` hard-requires it: `daemon/migrations/runner.py:645-646`; grammar `daemon/migrations/README.md:38-49`; parser `daemon/migrations/runner.py:85-95`) **and `IF NOT EXISTS` on EVERY `CREATE TABLE` / `CREATE INDEX` statement** (mandatory — the boot path applies `create_all` FIRST, so the objects already exist when the `.sql` runs; see the *`IF NOT EXISTS` + create_all-first* note below and §8 N7) |
| N5 | `tests/unit/test_board_service.py` | Service behaviour (SQLite in-memory) |
| N6 | `tests/unit/tools/test_board_tools.py` | Tool layer (mocked service) |
| N7 | `tests/migration/test_board_index_parity.py` | 3-site index lockstep — names **and column lists** — + a migration-smoke test applying the `.sql` verbatim to a temp SQLite DB |
| N8 | `tests/unit/test_board_registration.py` | Registration + allow-list proof **+ the privileged-default-deny proof** (an empty-allow agent resolves ZERO board tools) |

> **N4 prefix safety (do this FIRST, before writing the file):** the runner enforces one ledger row per version prefix (`schema_migrations.version TEXT PRIMARY KEY`, `daemon/migrations/runner.py:211-222`); a duplicate prefix breaks a fresh SQLite boot (apply txn + the pending loop at `daemon/migrations/runner.py:735-752`) and the documented symptom is `UNIQUE constraint failed: schema_migrations.version` (`daemon/migrations/README.md:463-468`). The runner does **not** catch `IntegrityError` on that insert (grep: zero hits in `daemon/migrations/runner.py`) ⇒ the failure is an unhandled abort, not a skip.
>
> **Use a distinct DAY prefix: `20261020_060000`.** A same-hour concurrent commission on the same day collides; the newest existing migration is `20261019_120822_alter_resource_monitor_booleans.sql` and the whole `20261020_*` namespace is free. **Re-verify at write time AND at merge time** — `ls daemon/migrations/versions/ | grep '^20261020_'` must return exactly the one file, and the prefix must still be unique against whatever landed in between. Two duplicate prefixes already exist in the tree (`20260628_000002`, `20261018_000001` — verified by `ls daemon/migrations/versions/ | sed 's/\.sql$//' | cut -d_ -f1-2 | sort | uniq -d`); do **not** copy the defect.
>
> **Comment hygiene:** the runner strips **full-line** `--` comments but leaves inline (trailing) comments alone — a `;` inside an inline comment corrupts the statement split (`daemon/migrations/runner.py:544-556`). So: **no inline `;` in any comment.** Header block + `-- UP` / `-- DOWN` sections, mirroring `daemon/migrations/versions/20261018_090004_create_resource_limit_policy.sql` (its `-- DOWN` at `:57`). The ledger `checksum` column is **write-only, never verified** — do not claim drift detection.
>
> **Every `CREATE` carries `IF NOT EXISTS` — MANDATORY, because `create_all` runs FIRST.** On the boot path `SQLModel.metadata.create_all(self._engine)` runs at `daemon/manager.py:751`, and the migration runner applies the pending `.sql` files **immediately after it** (`daemon/manager.py:754-757` — `MigrationRunner(self._engine)` at `:755`, `run_pending_migrations()` at `:756`). ⇒ **By the time the board `.sql` is applied, `create_all` has already created the three tables AND the six model-declared indexes (§4.1)** — the `.sql` is applied to a schema that already contains every object it declares. Consequences, all verified in THIS checkout:
> - A bare `CREATE TABLE board_items (…)` / `CREATE INDEX idx_board_items_status_updated …` therefore raises *"table/index already exists"*. The runner **swallows** that error on its idempotent-skip branch (`daemon/migrations/runner.py:587-591` — *"object already exists, skipping (idempotent)"*) ⇒ the failure is **silent**: the `.sql` degrades into a no-op on every boot and stops being an executable record of the schema change.
> - Any statement the swallow does **not** cover aborts the migration instead — the runner re-raises as `MigrationError` (`daemon/migrations/runner.py:606-610`) ⇒ a boot-time abort, not a skip.
> - `IF NOT EXISTS` removes both failure modes and makes the file applicable in the **real** boot order. **House precedent** — the two newest table migrations are idempotent for exactly this reason: `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` in `daemon/migrations/versions/20261018_090004_create_resource_limit_policy.sql:40,:54` and `daemon/migrations/versions/20261017_000001_create_secrets_tables.sql:141,:152,:156,:164` (their header comments say so verbatim: *"idempotent CREATE TABLE IF NOT EXISTS"*).
> - The `-- DOWN` section is the mirror image: `DROP TABLE IF EXISTS` for the two child tables **before** the parent, so the rollback path is equally order-safe.
> - **N7 pins both halves of this** (§8): the `.sql` is applied **verbatim** to a temp SQLite DB **after** `create_all` has already run on that same DB (the real boot order), and every `CREATE` in the UP section is statically asserted to carry `IF NOT EXISTS`.

### 3.2 Edit sites (existing files)

| # | File | Exact site | Change |
|---|------|-----------|--------|
| E1 | `daemon/manager.py` | late-import block, immediately **before** `SQLModel.metadata.create_all(self._engine)` (`:738-749` block, `create_all` at `:751`) | `from .models.board import BoardItem, BoardNote, BoardItemTag  # noqa: F401` — the resource_monitor P1 registration precedent |
| E2 | `daemon/manager.py` | right after `self._secret_service = SecretService(engine=self._engine)` (`:1381`) | `self._board_service = BoardService(engine=self._engine)` (+ import at `:133`-style top import block) |
| E3 | `daemon/manager.py` | next to the `scheduling_service` property (`:13534-13555`) | new read-only `board_service` property returning `self._board_service` |
| E4 | `daemon/tools/_tool_registry.py` | `DYNAMIC_TOOL_NAMES` (`:23-179`; `shared_meta_kv` at `:58`) | + `board_create`, `board_update`, `board_list`, `board_get` |
| E5 | `daemon/tools/_tool_registry.py` | `CATEGORY_MODULES` (`:690`+; `shared_meta_kv` at `:736`) | + `"board": "daemon.tools.board_tools"` |
| E6 | `daemon/tools/_tool_registry.py` | `KNOWN_TOOL_NAMES` (`:909`+; `shared_meta_kv` at `:1087`) | + the same 4 names (frozen-binary fallback; bidirectional drift pin `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) |
| E7 | `daemon/tools/instance.py` | top import block (`:259-260` pattern) | `from .board_tools import create_board_tools` |
| E8 | `daemon/tools/instance.py` | new `create_board_tools_if_available(manager, current_instance_id, agent_id, agent_tag)` (mirror `create_scheduling_tools_if_available`, `:2271-2300`) | returns `[]` when `manager.board_service` is absent (partial-init safe) |
| E9 | `daemon/tools/instance.py` | extend inside `create_instance_tools` (`:2450`; sibling extends at `:5768-5775`, `:5995`, `:6002-6003`) | `tools.extend(create_board_tools_if_available(manager, current_instance_id, agent_id, agent_tag=version_tag))` — the **third** step of the three-step registration seam (decorator + registry + construction). **The extend MUST precede** `scan_tools_for_full_docs` (`:6235`) and `_apply_tool_filter` (`:6238`) — a later extend is either silently invisible to the allow list or **bypasses the privileged strip** |
| E10 | `agents/ari/meta.json` | `tools.allow` array | + `"board"` |
| E11 | `agents/leader/meta.json` | `tools.allow` array | + `"board"` |
| E12 | `daemon/tools/_tool_registry.py` | `PRIVILEGED_TOOL_CATEGORIES` (`:239-339`) | + `"board"` as **entry #9** (the set today holds exactly eight: `system_upgrade`, `system-log`, `ens-db`, `ens-env`, `view-views`, `cu`, `secrets`, `settings`), with the same explanatory comment block as the `secrets` / `settings` entries. **Same commit as the five pins (E13).** |
| E13 | the five SAME-PR pin files (see §3.2.1) | the `PRIVILEGED_TOOL_CATEGORIES == frozenset({...})` exact-set assertion in each | + `"board"` in **all five**, in the **same commit** as E12 — the SAME-PR rule (D18/A14) is documented at `daemon/tools/_tool_registry.py:226-238` |

**No `daemon/tools/_auth.py` entry** — the board needs no backing agent spawn (`TOOL_REQUIRED_AGENTS` is only for categories that must spawn: `daemon/tools/_auth.py:35-51`).
**`PRIVILEGED_TOOL_CATEGORIES` — the category IS privileged.** `"board"` joins the set as **entry #9** (E12) in **Phase 1's first commit**, and the SAME-PR five-pin ceremony (`daemon/tools/_tool_registry.py:226-238`) applies in full: all five exact-set pins are updated **in the same commit** (E13). Deferring the flip to a later phase was considered and rejected — the deferral window would contain the empty-allow universe (the real inheritors are `watcher` and tools-less custom agents — §12 Q1), and a silent widening is the failure mode this commission is most exposed to, because the board is global by design.

**Binding trajectory — the privileged set grows across ALL THREE phases, not one.** The set goes **8 → 9 (P1: `board`) → 10 (P2: `self-alarm`) → 12 (P3: `view-write` + `board-link`)** ⇒ **three pin passes** over the same five files: this phase makes the **8 → 9** pass, phase 2 the **9 → 10** pass, and phase 3 the **10 → 12** pass (two categories added in one commit/pass). Each pass ships **in the same commit** as its category entry ⇒ every phase-commit is self-consistent and there is **no red window** — but no phase may claim the pins were "already paid": the cost is re-paid in every phase that adds a category.

### 3.2.1 The five SAME-PR pin files (E13)

| # | File | Exact-set assertion |
|---|------|---------------------|
| 1 | `tests/unit/tools/test_upgrade_registration.py` | `:142-166` |
| 2 | `tests/unit/tools/test_attestation_registration.py` | `:186-208` |
| 3 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` | `:324-341` |
| 4 | `tests/integration/test_service_tool_flag_off_byte_identical.py` | `:823-858` |
| 5 | `tests/unit/tools/test_secrets_registration.py` | `:218-232` |

Each is an **exact-set equality** assertion (`PRIVILEGED_TOOL_CATEGORIES == frozenset({...})`) and there is **no `len()` assertion anywhere** — so the marginal cost of the ninth entry is one inserted literal line per file. **All five updates ship in the same commit as E12**: a partial update trips the untouched pins, and a stale set trips all five.

*Doc-hygiene rider (same commit, docstring only — the assertions are what gate):* the surrounding docstrings/comments say "exactly eight entries" — `tests/unit/tools/test_upgrade_registration.py:103`, `tests/unit/tools/test_attestation_registration.py:143`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:288`, `tests/unit/tools/test_secrets_registration.py:26,202,212`. Update the count to nine so the next reader is not misled.

### 3.3 Exact meta.json diffs

`agents/ari/meta.json` (**not** covered by EITHER seal baseline — the 161-entry committed manifest
`agents/.prompt-artifacts.sha256` has **zero** `meta.json` rows, and the 35-entry `SEALED_SHA_BASELINES`
test tripwire lists `agents/charter/meta.json` but neither ari nor leader `meta.json`) ⇒ **this phase's
allow-list edit carries NO re-stamp obligation**:
```diff
       "secrets",
-      "settings"
+      "settings",
+      "board"
     ],
     "deny": ["edit_file", "write_file", "watch_job", "watch_jobs"]
```

`agents/leader/meta.json` (**not** covered by EITHER seal baseline — same verification as above; single-line
allow array) ⇒ **this phase's allow-list edit carries NO re-stamp obligation**:
```diff
-    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings"]
+    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings", "board"]
```

Appending at the END of both arrays is deliberate: minimal, symmetric, and least likely to collide with a concurrent edit. **Do NOT touch any `.md` prompt artefact in this phase** — `agents/ari/rule.md`, `agents/ari/soul.md`, `agents/ari/tools_note.md`, `agents/ari/workflow.md` and the leader equivalents (`growth.md`, `rule.md`, `soul.md`, `tools_note.md`, `workflow.md`) **ARE** manifest-covered (the 161-entry committed manifest `agents/.prompt-artifacts.sha256`), and `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` are **also** in the 35-entry `SEALED_SHA_BASELINES` test tripwire ⇒ an edit to any of them is a **seal event**. Under the **C1① consolidation ruling** every prompt-artefact edit is **deferred to the conventions step** ([plan-overview.md §8](plan-overview.md) — step 4 of this commission, before merge + stage) ⇒ **phase 1 carries no re-stamp task and touches no prompt artefact**.

**Write lane, for whoever does edit them later (binding).** The file tools **HARD-REFUSE** a write to a versioned prompt artefact — `tools_note.md` and the legacy `tools.md` are in `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:119-127`) and the fail-closed guards live in `daemon/tools/filesystem.py` (`write_file` **and** `edit_file`, via `_check_prompt_artifact_write`) — that is the posture our fencing prescribes; **do not fight it**. The **sanctioned lane** is the **bash/git escape hatch** with the **same-commit manifest re-stamp**. The **prompt-versions API is a runtime DB lane**, not the repo-commit path, and does **not** satisfy the committed manifest seal.

---

## 4. Schema

### 4.1 `board_items`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `"wi-" + uuid4().hex[:8]` (the `s-`/`n-` shape precedent, `daemon/services/todo_manager.py:1279-1304`) |
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

**Model declaration shape (binding — SQLModel cannot express this by inference).** Three things must be written *explicitly* in `daemon/models/board.py`; inferring them produces the wrong schema:

1. **Every string column is `Text`,** declared as `sa_column=Column("name", Text, …)` — a bare `str` emits **VARCHAR** on PG, not TEXT, and the PG and SQLite schemas must match type-for-type. Precedent: `daemon/models/app_settings.py:74-84` with its rationale note at `:66-72`. NOTE: when `sa_column` is supplied, SQLModel **ignores** Field-level column options — the `Column` itself carries `primary_key` / `nullable` (`daemon/models/app_settings.py:66-72`).
2. **`project_id` needs an explicit `ForeignKey(..., ondelete="SET NULL")` inside the `Column`:**
   `sa_column=Column("project_id", Text, ForeignKey("projects.project_id", ondelete="SET NULL"), nullable=True)`.
   (The **explicit named index** `idx_board_items_project_id` in the §4.1 index table below is the serving index —
   the Column carries **no index flag**, which would emit a duplicate 7th auto-named index
   `ix_board_items_project_id` and break the binding "exactly six" index set + the N7 parity assertions.)
   `Field(foreign_key=…)` **cannot** express `ON DELETE SET NULL` — it emits no `ondelete` clause, so D14 is unimplementable as written. Precedent for the `ondelete="SET NULL"` shape: `daemon/repositories/skill/models.py:691-697`, `daemon/repositories/infra/models.py:231-239`; the **FK target** — the `projects` table itself — is the `Project` SQLModel class at `daemon/repositories/project/models.py:329` (its `project_id` primary key at `:333`), and the `project_tags` junction at `daemon/repositories/project/models.py:157` is the in-repo **precedent** for a FK pointing at that target.
3. **`linked_job_ids` uses the explicit `JSONBType` column,** not a JSON-serialized string: `sa_column=Column("linked_job_ids", JSONBType, nullable=False, default=list)` — dialect-aware JSONB on PG / JSON elsewhere (`daemon/repositories/infra/types.py:35-71`; the exact precedent `daemon/repositories/snapshot/models.py:194-197`).

**Index set (binding — 3-site byte-identical lockstep, D7 of the resource_monitor precedent).** Declared in the model's `__table_args__` with the `Index("name", "col", …)` shape (`daemon/models/resource_monitor.py:174-180`), byte-identical in the `.sql`, and asserted by N7 (names **and** column lists):

| Index | Columns | Serves |
|---|---|---|
| `idx_board_items_status_updated` | `(status, updated_at)` | the status filter **and** the ordering when status is pinned — subsumes a single-column status index |
| `idx_board_items_updated_at` | `(updated_at)` | the default `ORDER BY updated_at DESC` + LIMIT |
| `idx_board_items_project_id` | `(project_id)` | the project filter |
| `idx_board_items_due_at` | `(due_at)` | the `due_before` range (NULLs excluded by the predicate) |
| `idx_board_item_tags_tag_item` | `(tag, item_id)` | the **reverse** tag filter — the composite PK `(item_id, tag)` cannot serve `WHERE tag = ?` (the `project_tags` precedent full-scans; do not copy that) |
| `idx_board_notes_item_id_ts` | `(item_id, ts)` | the per-item timeline |

`idx_board_items_created_at` is **dropped** — no v1 query path; pure write overhead. **Pinned ordering: `list_items` sorts `ORDER BY updated_at DESC`** (mirror `daemon/repositories/project/repository.py:362`) — the index choice depends on it, so the sort is a contract, not an implementation detail.

### 4.2 `board_notes` (APPEND-ONLY child table)

`id INTEGER PK AUTOINCREMENT` · `item_id TEXT NOT NULL` (FK → `board_items.id`, **NO ACTION**) · `ts TEXT NOT NULL` · `author_instance TEXT NOT NULL` · `text TEXT NOT NULL`
Index: `idx_board_notes_item_id_ts(item_id, ts)`

**Model shape (binding).** `id` is the autoincrement integer PK: `id: Optional[int] = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})` — the `SecretAuditEvent` precedent (`daemon/repositories/secrets/models.py:325-337`; SERIAL on PG / INTEGER PRIMARY KEY AUTOINCREMENT on SQLite) with the matching DDL shape `id INTEGER PRIMARY KEY AUTOINCREMENT` (`daemon/migrations/versions/20261017_000001_create_secrets_tables.sql:156-162`).

**The child FK is `NO ACTION` — no `ondelete=` clause at all.** `board_notes.item_id` is declared with a plain `ForeignKey("board_items.id")`: **not** `CASCADE`, **not** `SET NULL`. A hard delete of the parent item is therefore **refused by the database**, which enforces the soft-delete-only position structurally instead of by convention. The real-FK / no-`ondelete` precedent is the `project_tags` junction (`daemon/repositories/project/models.py:155-158`); the `ondelete`-carrying sibling (for contrast) is `daemon/repositories/project/models.py:179-180`. *(The `SecretAuditEvent.secret_id` column is a **doc-only** reference — no `ForeignKey` in the ORM and no `REFERENCES` in the DDL, `daemon/repositories/secrets/models.py:336-345` + `daemon/migrations/versions/20261017_000001_create_secrets_tables.sql:156-162` — so it is **not** the precedent for a NO-ACTION FK.)*

Mirrors the repo's append-only audit precedent: `SecretAuditEvent` (model `daemon/repositories/secrets/models.py:156+`; DDL `daemon/migrations/versions/20261017_000001_create_secrets_tables.sql:156-165`; index `(secret_id, at)` → ours `(item_id, ts)`).

### 4.3 `board_item_tags` (junction table)

`item_id TEXT NOT NULL` (FK → `board_items.id`, **NO ACTION**) · `tag TEXT NOT NULL` · **composite PK `(item_id, tag)`** (dedupe for free — the `project_tags` precedent, `daemon/repositories/project/models.py:146-158`) · index `idx_board_item_tags_tag_item(tag, item_id)` for the reverse tag filter.

**The child FK is `NO ACTION` — no `ondelete=` clause**, the same ruling as §4.2: a hard delete of the parent item is refused by the database rather than silently orphaning (or cascading away) the tag rows. The `project_tags` junction is the exact declaration precedent (`daemon/repositories/project/models.py:155-158`).

**Junction rows are written ONLY at create ⇒ `tags` are IMMUTABLE post-create.** `board_create` is the **single writer** of `board_item_tags`: no update path inserts or deletes a tag row, and `board_update` carries **no** tag parameter at all (§7.2). The A-17 batched tag loader (`_load_tags_bulk`, §5.2) only **reads** the junction — it is a hydration path, never a write path. ⇒ Adding or removing a tag on an existing item is **not expressible in v1**.

### 4.4 Storage decisions and their justification

- **tags[] → JUNCTION TABLE, not embedded JSON.** `board_list` filters by tag ⇒ a *relational* filter ⇒ the repo's established filterable pattern is the junction table (`project_tags`, `daemon/repositories/project/models.py:146-158`); the embedded `JSONBType` list is the established pattern for lists that are **not** relationally filtered (`snapshots.domain_tags` `daemon/repositories/snapshot/models.py:194-197`; `blueprint.tags`; `projects.related_directories`). A JSON list filter would need PG-native `?`/`@>` vs SQLite `json_extract` — two dialects, two query shapes, no index parity; the junction table gives one identical SQL on both engines plus an index. **No PG-native `TEXT[]`** anywhere in the ORM (repo rule).
- **notes → CHILD TABLE, not a JSON column.** Appending to a JSON array rewrites the whole array — that *is* an edit of history and it loses updates between concurrent appenders. A child table gives immutable rows, an indexed timeline read, and a shape with nowhere to record a mutation.
- **linked_job_ids → embedded JSONBType list, no junction table.** v1 has **no** job filter on `board_list` ⇒ no relational need; job ids are opaque external handles (`job_queue_items.job_id`, `daemon/repositories/job_queue/models.py:272-318`) whose rows are **soft-deleted** (`deleted_at`) — a hard FK would either block job deletion or cascade-delete a board item. Append = read-modify-write; safe under the service write lock (§5.2).
- **due_at / created_at / updated_at → ISO-8601 TEXT** (repo convention — never DB `now()`), **always tz-aware UTC, always microseconds** (§6.1). `priority` → nullable INTEGER 1–5, validated at the service boundary (the "forward-compatible enum tag, frozenset-validated at the service boundary, NOT at the model" precedent — `daemon/repositories/secrets/models.py:59-61` (the `action` enum bullet) and `:293-296` (`SecretAuditEvent.action`)).

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
    # NOTE: no `tags` — and no `title` / `project_id` / `priority` either: those are
    # CREATE-ONLY fields (§7.2). `tags` is IMMUTABLE post-create — the junction rows are
    # written only by `create_item` (§4.3); the batched tag loader only reads them.
    def list_items(self, *, status: str | None = None, project_id: str | None = None,
                   tag: str | None = None, due_before: str | None = None,
                   limit: int = 50, with_notes: bool = False) -> dict: ...
    def get_item(self, *, item_id: str) -> dict: ...
    # internal, called only by update_item — the ONLY notes mutation in the codebase:
    def _append_note(self, session, *, item_id: str, author_instance: str, text: str) -> dict: ...
    # internal batch loaders (N+1 avoidance — one query per collection, not one per row):
    def _load_notes_bulk(self, session, item_ids: list[str]) -> dict[str, list[dict]]: ...
    def _load_tags_bulk(self, session, item_ids: list[str]) -> dict[str, list[str]]: ...
```

- **Write serialization:** a `threading.RLock` held across the whole write transaction (create/update). Mirrors the shared_meta_kv repository lock discipline (`daemon/repositories/shared_meta_kv/repository.py:42-78`): under a SQLite `StaticPool` concurrent writers corrupt the prepared-statement cache. It also makes the `linked_job_ids` read-modify-write atomic. The daemon is single-process today, so a process-level lock is sufficient.
- **Transaction shape:** one `Session(self.engine)` block per write; the note INSERT is stamped **inside the same transaction** as the item UPDATE (the prompt_versions / secrets_audit "audit-event stamped inside the same transaction" contract, `daemon/repositories/secrets/models.py:156+`).
- **Item projection** (`_row_to_item`): `id, title, body_md, status, tags[], project_id, due_at, priority, linked_job_ids[], created_by_instance, created_at, updated_at`. `board_get` adds `notes[]` (oldest-first).
- **Batched hydration (binding).** The **tags** are always hydrated in **batch** — one `WHERE item_id IN (...)` query for the whole page, then group the rows in Python (≈15–20 lines) — because tags are part of the item shape; `with_notes=True` adds the **notes** as a second batch query on the same pattern. The **tool's item shape is unchanged** — `board_list` still returns no `notes[]` — so the payload and the truncation behaviour are untouched; only the service's own projection grows. Cost: ≤3 queries per board render versus 1+N (worst case 51 at the tool's own ceiling of 50, 201 at the 200 limit). **Do not** copy the `project` repository's per-row tag hydration (`daemon/repositories/project/repository.py:87-89`).
- **Tag input dedupe (service level).** The junction table's composite PK `(item_id, tag)` dedupes **DB rows** — a repeated tag cannot produce two stored rows. It does **NOT** dedupe the **input list**: `tags=["a","a"]` reaches the service as two entries, and a naive per-tag INSERT would attempt the same `(item_id, tag)` twice ⇒ an `IntegrityError` on the second insert (a create that looks like an id collision but is not one). The service therefore **de-duplicates the incoming tag list before writing** — order-preserving, first occurrence wins, case-sensitive (the tag column is plain TEXT; there is no case folding anywhere in v1) — so the write path is idempotent for a repeated tag and the read projection never emits a duplicate tag. The dedupe happens on the **input**, not on the read path.
- **ID collision — the SERVICE retries once; the agent never does.** The `wi-<uuid8>` id is generated **by the service** and is never supplied by the agent, so "the agent retries with a fresh id" is not a reachable behaviour. Contract: the service catches `IntegrityError` (never a bare `except`), confirms the failure is an **id PK** collision rather than any other constraint, regenerates the id and retries **exactly once**; a second failure ⇒ the generic `"ERROR: board operation failed"` envelope. The agent never sees a collision.
- **Interface freeze (binding for Phase 3).** `list_items(...)` and `get_item(...)` — names **and keyword names** — are the **only** tight seam in this commission, and they are frozen at the end of Phase 1: the `board_service.py` module docstring states them verbatim. Phase 3 consumes exactly this signature; changing it later is a breaking change to a frozen interface, not a refactor.

### 5.3 Manager wiring

- E1: late-import of the models before `SQLModel.metadata.create_all(self._engine)` (`daemon/manager.py:738-751`) — the resource_monitor P1 registration precedent ("register the 4 observability tables BEFORE create_all so the PG boot path lands them on existing PG databases").
- E2: `self._board_service = BoardService(engine=self._engine)` next to `SecretService` (`daemon/manager.py:1381`).
- E3: `board_service` property next to `scheduling_service` (`daemon/manager.py:13534-13555`).
- **No `_ensure_postgres_columns` entry** — these are brand-new tables; `create_all` creates them. Explicit precedent: "Both are brand-new tables … No `_ensure_postgres_columns` mirror needed" (`daemon/api.py:893-907`). The `create_all` call (`daemon/manager.py:751`) does not ALTER existing tables — the rule ("`SQLModel.metadata.create_all` only creates tables that don't exist; it does not add columns to existing tables") is stated in the `_ensure_postgres_columns` **docstring** (`daemon/manager.py:6597-6605`, sentence at `:6600-6601`) — irrelevant now, but it means the schema is **frozen at first boot**: any later column ADD in phase 2/3 needs both an `_ensure_postgres_columns` entry and a `.sql` ALTER.
- **Corollary — `create_all` creates indexes only for the tables it actually creates.** The table collection is filtered to "does not exist yet" (`sqlalchemy/sql/ddl.py:962-964`, SQLAlchemy 2.0.48) and the index objects are emitted **inside** `visit_table` (`sqlalchemy/sql/ddl.py:1023-1025`) — so an index that is declared only in the `.sql` is created on SQLite by the runner but **never on PG** (the PG runner is a no-op, `daemon/migrations/runner.py:721-726`), and a **dropped** index is not recreated. ⇒ **A LATER index ADD (phase 2/3) needs BOTH a new `.sql` file AND an `_ensure_postgres_columns` entry** (`CREATE INDEX IF NOT EXISTS …` — the precedent at `daemon/manager.py:6816`), because `create_all` will not add an index to a table that already exists. The six indexes in §4.1 are declared in the model's `__table_args__`, so they are created by `create_all` on both drivers from day one.

### 5.4 Restart-survival argument

1. All state lives in the DB (PG primary; SQLite dev). The service holds **no** in-memory state beyond the engine handle and the lock — there is no cache to lose.
2. On boot the manager materialises the tables. **The migration runner is SQLite-only** (`daemon/migrations/runner.py:721-726` — on any non-SQLite engine it logs "Skipping migrations for non-SQLite database" and returns; the PG equivalents live in `_ensure_postgres_columns`), and **`create_all` runs FIRST on both drivers** (`daemon/manager.py:751`, models registered by E1) with the `.sql` runner applied immediately after it (`daemon/manager.py:753-757`). ⇒ **The MODEL is the source of truth on both drivers**; the `.sql` file is the **persistent record** of the schema change plus the **SQLite-only index fallback** — it is *not* the PG path. (Same dual-driver posture as `app_settings` / `resource_limit_policy`: `daemon/models/app_settings.py:11-30`.)
3. Therefore a restart re-reads the same rows: items, notes, and tags created before the restart are visible after it, from any instance, on any chat source.
4. **Test proof (N5):** create rows with service A → drop the tables → re-run `SQLModel.metadata.create_all(engine)` (the boot path) → assert the tables exist again **and** the rows are still present, **and** the six indexes of §4.1 exist (they are created from the model's `__table_args__`). Assert `create_all` is idempotent (running it twice raises nothing).

---

## 6. Global namespace + timestamp discipline

### 6.1 Global namespace (why the board is tree-agnostic)

The board schema contains **no partitioning column at all** — no `context_key`, no `tree_root_id`, no `source`, no `project`-scoped table. Every `board_*` tool reads and writes the same three tables. This is the deliberate opposite of `shared_meta_kv`, which partitions by `context_key = tree-root instance id` (resolved lazily inside the tool body — `daemon/tools/shared_meta_kv_tools.py:109-122`; the column itself — `daemon/repositories/shared_meta_kv/models.py:30-55`). Sibling Ari instances on different chat sources, and the leader, therefore see **one** board. Project and channel are expressed as *attributes of an item* (`project_id` nullable FK; channel/source as a plain tag string, e.g. `telegram`), never as separate boards.

**Timestamp discipline (D7 — deliberate deviation):** all board timestamps are **fixed-width** UTC ISO-8601 with microseconds always present, i.e. `datetime.now(timezone.utc).isoformat(timespec="microseconds")` → `2026-10-20T01:55:40.123456+00:00`. Reason: the `due_before` filter compares **TEXT lexicographically**, which is only chronologically correct under a fixed-width format. The house `_now_iso()` (`daemon/repositories/project/models.py:21-30`) omits microseconds when zero → variable width → wrong ordering at second boundaries. The board uses a **board-local** helper; the house helper is not changed (no blast radius).

**MUST-FIX — one `_to_utc_iso()` on BOTH sides of the deadline path (blocking defect).** `due_at` is stored fixed-width UTC, but the `due_before` **filter parameter** accepts any tz-aware ISO string — and a raw agent string must never reach SQL. Left unhandled, the filter **silently inverts**: `'2026-10-20T08:00:00+07:00' < '2026-10-20T01:55:40.123456+00:00'` evaluates **False** although 01:00Z < 01:55Z — the filter then returns the *later* items and drops the *earlier* ones, with no error and no visible symptom. Contract:

- **One** board-local helper, `_to_utc_iso(value)`, used by **both** the `due_at` **write path** and the `due_before` **filter path** — no second implementation, no per-call-site fix-up.
- The input must be tz-aware; a naive input is rejected with `BoardValidationError` (the existing "naive `due_at`" validation, extended to `due_before`).
- The output is the fixed-width UTC form of D7 (microseconds always present), so the lexicographic SQL comparison is chronologically correct.
- The two paths must agree byte-for-byte: the round-trip `_to_utc_iso(_to_utc_iso(x)) == _to_utc_iso(x)` is pinned by a test (N5), together with the mixed-offset inversion probe above.

---

## 7. Tool contracts

Category: **`board`** — **PRIVILEGED** (entry #9 of `PRIVILEGED_TOOL_CATEGORIES`; §3.2.1, D6). Module: `daemon/tools/board_tools.py`.
Factory: `create_board_tools(board_service, *, current_instance_id: str, agent_id: str, agent_tag: str | None = None) -> list` — the `create_scheduling_tools` signature shape (`daemon/tools/scheduling.py:62-91`). The wrapper `create_board_tools_if_available` (E8) resolves the service from the manager.
**Body gate (defence in depth — binding).** All four tool bodies call **one** shared module-level helper, `_is_board_authorized(agent_id) -> bool`, which returns `agent_id in {"ari", "leader"}`. The privileged category does **not** close the literal-tool-name bypass (`daemon/tools/instance.py:475-478`), so the **category gate AND the body gate are both required** — one is not a substitute for the other. There is no two-agent-set precedent in `daemon/tools/` (the `blueprint` gate is single-agent, `daemon/tools/blueprint.py:71-73`) ⇒ one shared helper, not four copies. An unauthorized call returns the standard `"ERROR: …"` envelope; the tool never raises.

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
    # NO `tags` field — and no `title` / `project_id` / `priority` either: those four are
    # CREATE-ONLY (§7.2). `tags` is IMMUTABLE post-create (junction writes happen only at
    # create, §4.3); the update shape can neither add nor remove a tag.

class BoardListInput(BaseModel):
    status: Annotated[str | None, Field(default=None, description="todo | in_progress | blocked | done | 'open' (= todo|in_progress|blocked) | omit for all")]
    project_id: Annotated[str | None, Field(default=None, description="Filter by project id")]
    tag: Annotated[str | None, Field(default=None, description="Filter by a single tag")]
    due_before: Annotated[str | None, Field(default=None, description="Only items due strictly before this tz-aware ISO-8601 instant. Any UTC offset is accepted; the service normalises it to UTC before the comparison — a raw string never reaches the query.")]
    limit: Annotated[int, Field(default=50, description="Max items to return (1-200)")]

class BoardGetInput(BaseModel):
    id: Annotated[str, Field(description="Board item id")]
```

### 7.2 Signatures, returns, errors

| Tool | Signature | Returns |
|---|---|---|
| `board_create` | `(title, body="", project_id=None, tags=None, due_at=None, priority=None)` | **dict** = the created item (`id` at the top level, `notes: []` absent — a fresh item has no notes) |
| `board_update` | `(id, status=None, note=None, add_job_id=None, body=None, due_at=None)` — **no `tags` parameter**: the create-only field set is `{title, project_id, tags, priority}` and **`tags` is IMMUTABLE post-create** (see the bullet below) | **dict** = the updated item (no notes timeline) + `"note_appended": bool` |
| `board_list` | `(status=None, project_id=None, tag=None, due_before=None, limit=50)` | **dict** = `{"items": [...], "count": N}`; each item without notes; then `truncate_dict_result(result, "items", limit=…)` adds truncation metadata (`daemon/tools/_truncate.py:113`) |
| `board_get` | `(id)` | **dict** = the full item + `"notes": [{"ts":…, "author_instance":…, "text":…}, …]` oldest-first |

- **Item shape (canonical, all four tools):** `{id, title, body_md, status, tags[], project_id, due_at, priority, linked_job_ids[], created_by_instance, created_at, updated_at}`. The **input** param is `body` (bound shape); the **stored/returned** field is `body_md` (bound item-field name) — the docstring states the mapping explicitly. The shape is asserted by **N6** (§8), the house precedent being `tests/unit/tools/test_scheduling_tools.py`; it is projected **once**, in the service's `_row_to_item` (§5.2).
- **`tags` are IMMUTABLE post-create — `board_update` can neither add nor remove a tag.** Stated plainly, because the field set is easy to over-read: `board_update` has **no tag parameter at all**. The `board_item_tags` junction rows are written **only** by `board_create` (§4.3) — there is no tag-insert and no tag-delete anywhere in the write path — and the A-17 batched tag loader (`_load_tags_bulk`, §5.2) only **reads** the junction. The **create-only field set** is therefore `{title, project_id, tags, priority}`: all four are set at create and are **never settable** through `board_update` in v1. Of the remaining fields, `status`, `body` (`body_md`) and `due_at` are updatable, `due_at` is **replaceable but not clearable** (Q2), and `linked_job_ids` only ever grows (`add_job_id` — there is no unlink). ⇒ Re-tagging an existing item is **not expressible in v1**; it needs a new item (or a v1.1 `clear_fields`-style amendment, Q2).
- **Error envelope — tools NEVER raise** (`daemon/tools/scheduling.py:57-59`): every failure returns the string `"ERROR: {msg}"`.
  - `BoardItemNotFound` → `"ERROR: board item not found: wi-xxxx"`.
  - `BoardValidationError` → `"ERROR: <authored safe message>"` (e.g. `"ERROR: due_at must be a timezone-aware ISO-8601 string"`).
  - Unexpected exception → log server-side (`logger.exception`) and return `"ERROR: board operation failed"` — **no** `str(exc)`/traceback in the payload (the PB-F1 leak class; `daemon/tools/secrets_tools.py:52-63` — the *PB-F1 error envelope* section, which states that `str(exc)` / `traceback` / `repr(exc)` are **never** carried into the response payload; the adjacent in-repo mirror is `daemon/tools/ens_env_tools.py:377-395`, the class-name-only catch-all).
- **Status transitions — UNRESTRICTED (any → any, including reopening a `done` item).** Rationale: the board is a human-facing work surface, not a workflow engine; a rigid state machine would force a fake "cancel" path to undo a mistaken close. The audit trail is the **notes timeline**, not the status field. The only validation is membership in the 4-value enum (service boundary).
- **updated_at maintenance:** bumped on any successful `board_update` that changed something (status, body, due_at, linked job ids, **or** an appended note). A pure read never bumps it. A `board_update` with only `id` and no mutable field is a **no-op** (returns the item unchanged, no bump, no note).
- **Open-items convention helper:** `board_list(status="open")` ⇒ `status IN ('todo','in_progress','blocked')` — `done` is the only terminal status. Documented in the tool docstring **and** in `CATEGORY_DOC`. No other magic values; `status=None` means no filter.

### 7.3 Append-only notes — structural enforcement

1. **Absence of mutation APIs.** The service exposes exactly one notes mutation: `_append_note`, which issues a single `INSERT`. There is **no** `update_note` / `delete_note` / `edit_note` anywhere — the update path is *structurally incapable* of editing or deleting history because no such code path exists.
2. **Shape has nowhere to hide a mutation.** `board_notes` has no `updated_at`, no `deleted_at`, no `revision` column — the schema itself cannot express "this note was edited".
3. **The child FK is `NO ACTION`, and no `board_delete` exists.** `board_notes.item_id` carries **no `ondelete=` clause at all** — neither `CASCADE` nor `SET NULL` (§4.2) ⇒ a hard delete of the parent item is **refused by the database**, so the soft-delete-only position is enforced structurally rather than by convention. (This is also why the FK behaves the same on both drivers at runtime: the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on every connection, `daemon/repositories/factory.py:169-176`.) No `board_delete` tool exists in v1 either, so notes can never be orphaned.
4. **Same-transaction insert.** The note row is stamped inside the same transaction as the item mutation (audit-event contract).
5. **No dirty-tracking escape hatch.** "No mutation API" is necessary but **not sufficient**: a `BoardNote` fetched into a session that later commits lets SQLAlchemy's dirty-tracking emit an UPDATE. ⇒ the service must **never** keep a live `BoardNote` ORM instance across a commit boundary — notes are read into plain dicts (or an explicitly expired/detached identity) and the row is never re-flushed.
6. **Guard test (N5/N6) — the primary proof is a SQL capture.** A `before_cursor_execute` listener asserts that the **only** statements touching `board_notes` across a create/update cycle are `INSERT` and `SELECT` — never `UPDATE`, never `DELETE`. The static source scan (no `UPDATE`/`DELETE` against `BoardNote` in `daemon/services/board_service.py`) stays as a cheap **secondary** check, and the runtime count-monotonicity assertion stays. (Static-audit style precedent: `tests/unit/test_log_secret_static_audit.py`.)

---

## 8. Tests

| File | Covers | Exemplar |
|---|---|---|
| N5 `tests/unit/test_board_service.py` | create/get/update/list; status transitions incl. reopening `done`; **notes append-only** (count grows; no update/delete API; **SQL-capture guard** — the only `board_notes` statements are INSERT/SELECT; static source scan); tag filter; project filter; `status="open"` helper; `due_before` filter **incl. the mixed-offset inversion probe** (`+07:00` vs UTC) **and** the `_to_utc_iso` round-trip; **`ORDER BY updated_at DESC`**; **the id-collision retry-once contract** (the service retries once; the agent never supplies the id); **child FK `NO ACTION`** (a hard parent delete is refused — the unit-test engine must enable `PRAGMA foreign_keys=ON`, or the assertion is marked PG-only); **batched hydration** (`list_items(with_notes=True)` ⇒ ≤3 queries, notes and tags both batched); update of a missing id → `BoardItemNotFound`; validation errors (naive `due_at`/`due_before`, priority out of range, unknown status); **restart survival**: drop tables → `SQLModel.metadata.create_all(engine)` → tables re-created **and** rows still present **and** the six indexes of §4.1 exist; `create_all` idempotency; write-lock concurrency (two threads appending notes → both rows present) | `tests/unit/test_shared_meta_kv_repo.py`, `tests/unit/test_shared_meta_kv_concurrency.py` |
| N6 `tests/unit/tools/test_board_tools.py` | factory returns exactly 4 tools; each Input schema (required/optional, defaults); **never raise** (a service that raises → the tool returns an `"ERROR: …"` string); error envelopes for not-found / validation; **the body gate** — an `agent_id` outside `{"ari","leader"}` gets an `"ERROR: …"` envelope and never reaches the service; `board_list(status="open")` passes through; `board_list` still returns **no** `notes[]` (the tool shape is unchanged by the batched hydration); returned item shape; `board_update` note appends and bumps `updated_at`; **`board_update` has NO tag parameter** — asserted as a **schema** property of `BoardUpdateInput` (the Input model exposes no `tags` field, so the create-only field set `{title, project_id, tags, priority}` is not settable through the update shape, and `tags` is IMMUTABLE post-create — §7.2), not merely as a runtime outcome; `board_create` stamps `created_by_instance` from the closure (not agent-supplied) | `tests/unit/tools/test_scheduling_tools.py` (mocked service), `tests/unit/test_shared_meta_kv_tool.py` (mocked manager) |
| N7 `tests/migration/test_board_index_parity.py` | 3-site index lockstep: the model `__table_args__` index **names AND column lists** == the `.sql` `CREATE INDEX` names+columns, **byte-identical** (names alone are not enough — a same-named index with different columns is a silent mismatch); **a migration-smoke test that applies the `.sql` VERBATIM to a temp SQLite DB** and asserts the same six index names+columns exist there; **the create_all-FIRST ordering is ASSERTED, not assumed** — the smoke test models the **real boot order**: `SQLModel.metadata.create_all(engine)` FIRST (`daemon/manager.py:751`), the `.sql` applied **verbatim** AFTER it (`daemon/manager.py:754-757`), and the apply must **succeed without error** against the already-created schema (it only does if every `CREATE` carries `IF NOT EXISTS` — §3.1) with the six index names+columns still exactly the §4.1 set (no duplicate, no drift); **a static assertion over the `.sql` text** that every `CREATE TABLE` / `CREATE INDEX` statement in the UP section carries `IF NOT EXISTS` (a bare `CREATE` must be a RED test, not a silent no-op on the boot path — the runner swallows "already exists" at `daemon/migrations/runner.py:587-591`); **a source-order assertion on the boot path** — in `daemon/manager.py` the `create_all` call precedes the `run_pending_migrations()` call, because the ordering is a property of the boot path and must be pinned where it lives; the migration declares every column of every table; the composite PK `(item_id, tag)` exists in both; the `-- DOWN` section is present and drops the child tables before the parent | `tests/migration/test_secrets_index_parity.py`, `tests/migration/test_prompt_versions_index_parity.py` |
| N8 `tests/unit/test_board_registration.py` | `"board"` ∈ `agents/ari/meta.json` and `agents/leader/meta.json` allow lists; the 4 names ∈ `DYNAMIC_TOOL_NAMES` **and** `KNOWN_TOOL_NAMES` **and** the source-discovered universe; `CATEGORY_MODULES["board"]` resolves; **`"board"` ∈ `PRIVILEGED_TOOL_CATEGORIES` (the ninth entry)**; every meta.json allow entry is a known tool or category | `tests/unit/tools/test_scheduling_registration.py` |
| N9 (no new file) | `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` **must stay green** — E4+E5+E6 together satisfy the bidirectional pin (`:223-242`) | existing |

**Tool visibility (N8, mandatory — three assertions):**

1. **Positive:** an agent whose allow list contains `"board"` (`ari`, `leader`) resolves to the 4 tools.
2. **Privileged default-deny — the property that made the category privileged:** an agent with an **EMPTY** allow list resolves to **ZERO** board tools. `resolve_tool_filter` with an empty allow expands to every category **except** the privileged ones (`daemon/tools/instance.py:460-470`), and the default-allow strip removes only privileged categories (`:6243-6271`; the no-`tools`-key path at `:6300-6304`). The in-tree empty-allow agent is **`watcher`** (`agents/watcher/meta.json` — the security-focused tool-call evaluator whose context is attacker-influenced); the second population is unbounded — an `agent_create`-born custom agent has **no `tools` key** (`daemon/tools/agent_mother.py:283-294`) and a UI-created agent with an empty allow/deny triple normalises to `tools = None` (`daemon/repositories/custom_agent_configs/repository.py:193-198`). This assertion is the proof that the flip happened at all.
3. **Negative (non-empty allow without the category):** an agent whose allow list is **non-empty and lacks** `"board"` resolves to none.

**The five SAME-PR pins stay green** (§3.2.1) — they live in the five existing pin files (E13), not in a new file, and they are the structural proof that the ninth entry is exactly one.

---

## 9. v2 — job-lifecycle auto-update (DOCUMENT ONLY, no build steps)

**Trigger events:** the job lifecycle — `job_queue_items.admission_state` transitions (`QUEUED → ACTIVE → DONE | DEAD`, `daemon/services/job_state_machine.py`) and the job's `terminal_reason`.

**Proposed updates (v2, not built here):** when a job referenced by a board item reaches `DONE`, auto-append a system-authored note (`"job <id> completed"`) and, if the item is `todo|in_progress`, flip it to `done`; when the job reaches `DEAD`, append a note and flip the item to `blocked`. A new `author_instance` convention for system-authored notes (e.g. `system:job-queue`) is required.

**Why deferred:** (a) there is no lifecycle-event seam for an arbitrary table — the job-completion path lives inside `daemon/services/task_processor.py` / `daemon/services/child_reports.py`, the same area as the RDRS six-lane report-delivery sweep (`daemon/services/report_delivery_recovery.py`); touching it risks the report-delivery path. (b) Auto-writing notes changes the **authorship model** of an append-only audit trail (system author vs. instance author) and needs a documented convention before any row is written. (c) It requires the job-completion path to reach the `BoardService`, a manager-level dependency the service does not currently hold. Deferring until phase-1 traffic exists keeps the phase-1 diff small and the audit trail's provenance unambiguous.

---

## 10. Task checklist (dependency-ordered)

- [ ] **T1** — `daemon/models/board.py`: 3 SQLModel tables + status constants + `_now_iso` (fixed-width) + **`_to_utc_iso()`** — the single normalizer used by the `due_at` **write** path **and** the `due_before` **filter** path (§6.1). Model shapes per §4.1: every string column `sa_column=Column(…, Text, …)`; `project_id` with an explicit `ForeignKey("projects.project_id", ondelete="SET NULL")`; `linked_job_ids` with the explicit `JSONBType` column; `board_notes.id` autoincrement; both child FKs **NO ACTION** (no `ondelete=` clause); the six indexes of §4.1 in `__table_args__`. Import-compile check.
- [ ] **T2** — `daemon/migrations/versions/20261020_060000_create_board_tables.sql`. **First** verify the distinct-day prefix is free (`ls daemon/migrations/versions/ | grep '^20261020_'` → empty) and **re-verify at merge time** (§3.1). Header comment block + `-- UP` / `-- DOWN` sections (the `20261018_090004_create_resource_limit_policy.sql` template; the `-- DOWN` section is **mandatory** — `daemon/migrations/runner.py:645-646` hard-requires it, dropping the child tables before the parent); **no inline `;` in comments** (the runner strips full-line `--` comments only, so a `;` inside an inline comment corrupts the statement split — `daemon/migrations/runner.py:544-556`); the six index names **and column lists** byte-identical to the model (§4.1); **every `CREATE TABLE` / `CREATE INDEX` carries `IF NOT EXISTS`** (§3.1 — `create_all` runs FIRST on the boot path, so the objects already exist when this `.sql` is applied; without it the runner swallows every statement as "already exists" and the file becomes a silent no-op), and the `-- DOWN` section uses `DROP TABLE IF EXISTS` in child-before-parent order.
- [ ] **T3** — `daemon/services/board_service.py`: `BoardService` + errors + lock + transactions + projections + the **retry-once** contract on an id collision (§5.2) + the **batched** notes/tags loaders behind `list_items(..., with_notes=False)` (§5.2).
- [ ] **T4** — manager wiring **E1** (late import) + **E2** (construction) + **E3** (property).
- [ ] **T5** — `daemon/tools/board_tools.py`: 4 tools, 4 Input models, `CATEGORY_NAME`/`CATEGORY_DOC`, `_full_doc_` per tool + the **shared `_is_board_authorized()` body gate** used by all four bodies (§7).
- [ ] **T6** — registry **E4** + **E5** + **E6** **+ E12 + E13 — all in ONE commit**: `"board"` as **entry #9** of `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:239-339`) **and** the five SAME-PR exact-set pin insertions (§3.2.1). The SAME-PR rule is a **same-commit** obligation: a partial update trips the untouched pins, and the set must never be red between two commits of this phase.
- [ ] **T7** — construction **E7** + **E8** + **E9** (the extend must precede `scan_tools_for_full_docs` `:6235` / `_apply_tool_filter` `:6238`, or the privileged strip is bypassed).
- [ ] **T8** — allow-lists **E10** + **E11** (exact diffs in §3.3).
- [ ] **T9** — tests **N5**, **N6**, **N7**, **N8** (+ re-run N9 green).
- [ ] **T10** — **DEFERRED to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4 of this commission, before merge + stage). The `## Board` section of `agents/ari/tools_note.md` is authored **there**, not here: `tools_note.md` **IS** manifest-covered (161-entry committed manifest `agents/.prompt-artifacts.sha256`, row `:28`), so editing it in phase 1 would create a **second** re-stamp pass — which the C1① consolidation ruling forbids (one in-commit re-stamp pass for the whole commission). Phase 1 therefore ships **no prompt-artefact edit** and **no re-stamp task**. The section's content is specced in [plan-overview.md §8.3/§8.4](plan-overview.md) and must obey `docs/agent-prompt-writing-guide.md` §1 (agent POV — no `meta.json` / `tools.allow` / `daemon/` tokens) and §2 (one canonical home). `tool_help` + `_full_doc_` already cover discovery, so nothing is lost by the deferral. **Recorded as a deferral in [decisions.md](decisions.md) §A-ADJUDICATED (A-6).**
- [ ] **T11** — verification sweep: `tests/unit/tools/test_frozen_tool_name_discovery.py`, `tests/unit/tools/test_scheduling_registration.py`, **the five SAME-PR pin files (§3.2.1)**, the seal test `tests/test_chart_image_delivery_audit.py` / `tests/test_chart_image_delivery_e2e.py` (**no NEW stale pins beyond the measured baseline of 7 — the test is ALREADY RED at HEAD; the acceptance is mismatch-count-before == mismatch-count-after, NOT "green"**), and a boot smoke (dev port 8079, `POSTGRES_DB != ensemble_prod`).
- [ ] **T12** — **correct the `_strip_privileged_category_tools` docstring in `daemon/tools/instance.py` — the stale privileged-category enumeration.** This is a **numbered checklist item**, not a standing note: it cannot be skipped. Phase 1 edits that file (E8/E9), so the correction ships **in the same commit as the board tool registration**. The docstring at `daemon/tools/instance.py:6244-6267` (the function at `:6243`) enumerates the privileged categories as *"today: `system_upgrade`, `system-log`, `ens-db`, `ens-env`"* — a **stale 4-entry list** at `:6250-6251` — while the live set is **8** at HEAD (`system_upgrade`, `system-log`, `ens-db`, `ens-env`, `view-views`, `cu`, `secrets`, `settings`) and becomes **9 / 10 / 12** across this commission's three pin passes (§3.2). **Acceptance:** the docstring carries **no** stale enumeration — either it is replaced by a pointer to the registry constant (*"see `PRIVILEGED_TOOL_CATEGORIES` for the live set"*, so it **cannot** go stale again — the preferred fix), or it is updated to the post-phase-1 value (**9** entries, including `"board"`) **in this same commit**. The rule carries into phases 2 and 3, which edit `instance.py` too. (Advisory A-7; also recorded in [decisions.md](decisions.md) §C.)

**Do NOT:** split **E12** and **E13** across commits (the SAME-PR rule is same-commit), ship a partial set of the five pins, edit any prompt artefact (every prompt-artefact edit is deferred to the conventions step, [plan-overview.md §8](plan-overview.md)), or promote/stage a release (promote is the user's nonce ceremony).

**Standing note — the `_strip_privileged_category_tools` docstring correction is now a NUMBERED CHECKLIST ITEM: see T12.** It binds **every** phase that touches `daemon/tools/instance.py` — phases 1, 2 and 3 all edit that file, so each phase corrects the docstring in its own commit. (Advisory A-7; also recorded in [decisions.md](decisions.md) §C.)

### 10.1 Coupling

- **Tight with Phase 2 (`self_alarm`)** — shares the **schema** this phase freezes: the alarm horizon reads `due_at` (tz-aware UTC, indexed) and `status`; the item/author provenance it will report comes from `created_by_instance` + the notes timeline. Any phase-2 column ADD after the first board boot is a migration event (R4). Phase 2 must not need a column that phase 1 does not create.
- **Tight with Phase 3 (live board view)** — shares the **service** (`BoardService.list_items` / `get_item`) and the global-namespace contract: the view must render the SAME single namespace the tools see, with no per-chat or per-project partition (D1). Phase 3 decides whether the view calls the service directly or via a REST router (Q6).
- **Loose with the `todo` tool family** — shares a domain (user work tracking) but no data structure: `todo` is per-instance and in-memory (`daemon/services/todo_manager.py:186-214`), the board is global and durable. Only the agent-facing wording couples them (R9 / T10).
- **Independent of** the job-queue lifecycle in v1 — the `linked_job_ids` column stores opaque job ids but v1 never reads them back; the auto-update path is deferred to v2 (§9) precisely to keep this phase decoupled from `daemon/services/task_processor.py` and the report-delivery path.

---

## 11. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | **`wi-<uuid8>` is 32 bits of entropy** — birthday collision: P≈1 at ~77k items, P≈0.0001 at 1k items | Low | Low | `id` is the **PRIMARY KEY** → a collision raises `IntegrityError` → no silent overwrite, no data loss. **The agent never supplies the id and never retries**: the *service* catches the `IntegrityError`, confirms it is an **id-PK** collision, regenerates the id and retries **once**; a second failure ⇒ the generic `"ERROR: board operation failed"` envelope (§5.2). The shape is fixed by the binding; flagged, not changed. Human-scale board ⇒ negligible in practice |
| R2 | **RESOLVED — the category is privileged, so there is no default-grant.** (Was: "non-privileged ⇒ every empty-allow agent inherits the board tools".) | — | — | Closed by the architect's ruling (§12 Q1): `"board"` is **entry #9** of `PRIVILEGED_TOOL_CATEGORIES` from Phase 1's **first** commit (E12) **and** the five SAME-PR pins ship in the **same** commit (E13). Kept as a decision record, not a live risk. The residual is the *forgetting* risk — mitigated by the five exact-set pins and by the N8 empty-allow assertion (§8) |
| R3 | **FK behaviour differs across drivers** — the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on **every** connection (`daemon/repositories/factory.py:169-176`), so FK behaviour holds on **both** drivers at runtime; a bare `create_engine("sqlite://")` unit-test engine has the pragma **off** by default | Low | Low | The FK is inert **only** on a bare unit-test engine ⇒ **any unit-test FK assertion must enable the pragma or be marked PG-only** (N5). For `project_id` (the one `ON DELETE SET NULL`) the item survives either way and the value is informational — no code path depends on the SET NULL behaviour (the project filter is by value, not by join). The two **child** FKs are `NO ACTION` (§4.2/§4.3) ⇒ a hard parent delete is refused by the database on both drivers — that refusal is the point, not a side effect |
| R4 | **Schema freeze** — the `create_all` call (`daemon/manager.py:751`) does not ALTER existing tables (the rule is stated in the `_ensure_postgres_columns` docstring, `daemon/manager.py:6597-6605`); after first boot any phase-2/3 column ADD needs both an `_ensure_postgres_columns` entry **and** a `.sql` ALTER — and any later **index** ADD needs both a `.sql` file **and** an `_ensure_postgres_columns` entry, because `create_all` creates indexes only for the tables it actually creates (`sqlalchemy/sql/ddl.py:962-964` + `:1023-1025`; §5.3) | Medium | Medium (certain, if phase 2 adds a column) | §14 criterion 7 pins the phase-2/3 schema hooks NOW (`due_at`, `status`, `tags`, `linked_job_ids`) so no later ADD is required. The six indexes of §4.1 are declared in the model's `__table_args__`, so `create_all` creates them on both drivers from day one. Documented in §5.3 |
| R5 | **`linked_job_ids` read-modify-write** — safe only under the service write lock; a future multi-process writer needs row-level locking | Low | Low | The daemon is single-process today; the `threading.RLock` (§5.2) serialises all board writes. Flagged for any future multi-process deployment |
| R6 | **No per-item ACL** — any agent that clears the two gates can write the global board | Low | Low | Two structural gates now stand: the **privileged category** (opt-in-only, never default-granted) **and** the **body gate** `agent_id ∈ {"ari","leader"}` (§1, §7) — on top of the per-agent `tools.allow` entry. Within that pair the board is a shared, global, human-facing work surface that carries no secrets and grants no authority, consistent with the repo trust model ("the only access boundary is the per-agent `tools.allow` category gate", `daemon/services/secret_service.py:1163-1171`). Revisit only if the board ever holds sensitive content |
| R7 | **Unbounded growth** — no retention policy in v1; `board_notes` grows monotonically | Low | Medium | `board_list` is bounded by `limit`; archival is a v2 topic. Add a retention decision before the board exceeds a few thousand rows |
| R8 | **Migration prefix collision** with a concurrent commission (duplicate prefix ⇒ fresh-SQLite boot failure, `daemon/migrations/README.md:463-468`; the runner does **not** catch `IntegrityError` — grep: zero hits in `daemon/migrations/runner.py`) | High | Low | T2 uses a **distinct-day** prefix (`20261020_060000`), verifies it is free **before writing** (`ls daemon/migrations/versions/ \| grep '^20261020_'` must be empty) and **re-verifies at merge time**. Never copy the two pre-existing duplicate prefixes (`20260628_000002`, `20261018_000001`) |
| R9 | **`todo` confusion** — agents have an existing per-instance `todo` family (`daemon/tools/todo_tools.py`); the board is durable + global | Medium | Medium | The distinction is documented (T10, `agents/ari/tools_note.md`) and stated in the tool docstrings + `CATEGORY_DOC`: todo = ephemeral per-turn planning, board = durable cross-instance user work |
| R10 | **Phase-1 schema under-specifies phase 2/3** — `self_alarm` (horizon ≥30 min) and the live view need fields not bound in phase 1 | Medium | Medium | §14 criterion 7 pins the four hooks phase 2/3 need (`due_at` indexed + tz-aware, `status` indexed, `tags` junction, `linked_job_ids` JSONB). Any further need is an explicit additive change in a later phase, not a phase-1 rework |
| R11 | **🔴 The `due_before` filter can SILENTLY INVERT** — `due_at` is stored fixed-width UTC, but a tz-aware `due_before` parameter carrying another offset compares wrongly as TEXT: `'2026-10-20T08:00:00+07:00' < '2026-10-20T01:55:40.123456+00:00'` is **False** although 01:00Z < 01:55Z ⇒ the filter returns the *later* items and drops the *earlier* ones — no error, no visible symptom | High | Medium (a non-UTC offset is a normal agent input) | **MUST-FIX:** one `_to_utc_iso()` on **both** the write path and the filter path (§6.1); a naive input is rejected; the mixed-offset inversion probe and the round-trip are pinned by N5. This is a **blocking defect** of this phase, not a nice-to-have |
| R12 | **An index declared only in the `.sql` never reaches PG** — `create_all` creates indexes only for the tables it creates, and the PG migration runner is a no-op ⇒ the index exists on SQLite and is missing on PG | Medium | Low | All six indexes are declared in the model's `__table_args__` (§4.1), so `create_all` creates them on both drivers from day one; N7 compares index **names and column lists** across the three sites and applies the `.sql` verbatim to a temp SQLite DB (§8) |

---

## 12. Open questions for the architect

- **Q1 — RESOLVED: `"board"` is PRIVILEGED (opt-in), from Phase 1's FIRST commit.** A non-privileged category **cannot** enforce "ari + leader": it is default-granted to the empty-allow universe, and a **literal tool name** in an allow list bypasses the category gate in any case (`daemon/tools/instance.py:475-478`). The privileged category (entry #9) **and** the shared `agent_id ∈ {"ari","leader"}` body gate are **both** required, and the SAME-PR five-pin update lands in the **same commit** as the category entry (§3.2.1, E12/E13).
  - **Evidence correction 1 — the blast radius is not what this plan originally claimed.** `worker` (21 allow entries), `tester` (22), `developer` (13) and `designer` (17) all carry **non-empty** allow lists ⇒ a non-privileged board would have reached **none** of them (`daemon/tools/instance.py:475-478`).
  - **Evidence correction 2 — the real exposure is narrower in count and worse in kind.** Exactly **one** in-tree agent carries an empty allow list: **`watcher`** (`agents/watcher/meta.json` — "Security-focused tool call evaluator — intercepts every tool call from a watched instance before execution"), whose context is attacker-influenced; under a non-privileged category it would hold **write authority over the global board**. The second population is unbounded: an `agent_create`-born custom agent writes a `meta.json` with **no `tools` key** (`daemon/tools/agent_mother.py:283-294` ⇒ `daemon/tools/instance.py:6300-6304`), and a UI-created custom agent with an empty allow/deny triple normalises to `tools = None` (`daemon/repositories/custom_agent_configs/repository.py:193-198`).
  - **Cost check:** all five pins are exact-set equality and there is **no `len()` assertion anywhere** ⇒ one inserted literal line per file; the marginal cost of the ninth entry is 5 file-touches and 5 lines.
  - **Defence in depth is mandatory, not optional:** because a literal tool-name allow entry bypasses the category gate, the body gate is not redundant — dropping it re-opens the escalation path.
- **Q2 — v1 cannot CLEAR `due_at` / `project_id` / `priority`** (the bound `board_update` shape has no clear-flag). Options: (a) keep the bound shape, document the limitation; (b) `""`/`"null"` sentinels; (c) a `clear_fields` param later. **Recommendation:** (a) now, (c) as a v1.1 amendment — the bound shape is not re-litigated in this plan. **Precision on the create-only field set (A3):** of the three fields named above, only `due_at` is even **settable** post-create (replaceable, not clearable); `project_id` and `priority` are **create-only** — the bound `board_update` shape never carries them (§7.2) — so for those two "cannot clear" is a strict subset of "cannot touch". The full create-only set is `{title, project_id, tags, priority}`, and **`tags` is IMMUTABLE post-create**: `board_update` has **no tag parameter**, the `board_item_tags` junction rows are written **only** by `board_create` (§4.3), and the A-17 batched tag loader only **reads** them (§7.2). ⇒ **Re-tagging an existing item is not expressible in v1**; a v1.1 `clear_fields`-style amendment would have to cover the tag **junction**, not just item columns.
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
| D6 | **Category `"board"` is PRIVILEGED — entry #9 of `PRIVILEGED_TOOL_CATEGORIES`, from Phase 1's FIRST commit** | A non-privileged category cannot enforce "ari + leader": it is default-granted to the empty-allow universe, and a **literal tool-name** allow entry bypasses the category gate outright (`daemon/tools/instance.py:475-478`). The real inheritors are **`watcher`** (the only in-tree empty-allow agent) and tools-less custom agents (§12 Q1). The SAME-PR five-pin ceremony (`daemon/tools/_tool_registry.py:226-238`) therefore applies **in the same commit** (E12 + E13), and the tool bodies add the shared `agent_id ∈ {"ari","leader"}` gate as defence in depth (§7). **The binding privileged-set trajectory is 8 → 9 → 10 → 12 across THREE pin passes** (§3.2) |
| D7 | **All timestamps fixed-width UTC ISO-8601, microseconds always on** (board-local `_now_iso`), **plus ONE `_to_utc_iso()` normalizer serving both the `due_at` write path and the `due_before` filter path** | The `due_before` filter compares TEXT lexicographically — only correct under a fixed width. **DEVIATION** from the house `_now_iso()` (`daemon/repositories/project/models.py:21-30`), which drops microseconds when zero; the house helper is left untouched. The normalizer is **mandatory**, not cosmetic: without it the filter silently inverts on a non-UTC offset (§6.1, R11) |
| D8 | **`due_at`: naive input rejected; tz-aware accepted; stored as UTC — through the single `_to_utc_iso()` helper, which also normalises the `due_before` filter** | No tz-chain in phase 1 (the scheduling chain is heavy — `daemon/services/scheduling_service.py:342-359`); phase 2's `self_alarm` owns tz resolution. The **same** helper must serve the filter path, or the comparison inverts silently (§6.1, R11) |
| D9 | **Status transitions unrestricted (any→any; `done` reopenable); enum validated at the service boundary** | Human-facing work surface, not a workflow engine; the notes timeline is the audit trail; the "forward-compatible enum tag validated at the service boundary, NOT at the model" precedent (`daemon/repositories/secrets/models.py:59-61` + `:293-296`) |
| D10 | **`priority` = nullable INTEGER 1–5, service-validated, no DB CHECK** | Small ordered scale for a human-visible work item; no native enums in the ORM; service-boundary validation per D9's precedent |
| D11 | **Registered in THREE registry surfaces** (`DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES`) | Factory-built tools must be in `DYNAMIC_TOOL_NAMES` or boot allow-list validation rejects the category; `CATEGORY_MODULES` makes the AST scanner see the module; `KNOWN_TOOL_NAMES` is the frozen-binary fallback and is under a bidirectional drift pin. The `shared_meta_kv` precedent appears in all three (`:58`, `:736`, `:1087`) |
| D12 | **Model registration = late-import in `daemon/manager.py` before `create_all`** (not the api.py lifespan) | The manager site is on every boot path (`daemon/manager.py:738-751`); the resource_monitor precedent. The api.py lifespan (`daemon/api.py:893-907`, secrets) is API-server-only |
| D13 | **No `_ensure_postgres_columns` entry for the three tables** — **but a LATER index ADD needs BOTH a `.sql` and an `_ensure_postgres_columns` entry** | Brand-new tables — `create_all` creates them; explicit precedent at `daemon/api.py:893-907`. Corollary: `create_all` creates indexes only for the tables it actually creates (`sqlalchemy/sql/ddl.py:962-964` + `:1023-1025`), so an index added to an existing table later needs the `.sql` record **and** an `_ensure_postgres_columns` `CREATE INDEX IF NOT EXISTS` entry (`daemon/manager.py:6816`) — otherwise it exists on SQLite and is missing on PG (§5.3, R12) |
| D14 | **`project_id` is a real FK → `projects.project_id` with `ON DELETE SET NULL`** — declared as `sa_column=Column("project_id", Text, ForeignKey("projects.project_id", ondelete="SET NULL"), nullable=True)` (the explicit named index `idx_board_items_project_id` serves the filter — the Column carries **no index flag**, see §4.1) | The binding says "nullable FK"; `projects` is registered in `SQLModel.metadata` at manager import — the import chain is `daemon/manager.py:44` (`create_project_repository`) → `daemon/repositories/project/repository.py:17` (`from .models import (Project, …)`) → **the table class itself, `daemon/repositories/project/models.py:329`** (`class Project(SQLModel, table=True)`, `__tablename__ = "projects"`, `project_id` primary key at `:333`) ⇒ `create_all` can order the FK. SET NULL keeps the board item alive when a project is hard-deleted (`daemon/repositories/project/repository.py:1088` — the `delete` method, *"Delete a project with full cascade cleanup"*). The declaration MUST carry the explicit `ondelete=` clause — `Field(foreign_key=…)` emits none, so D14 is otherwise unimplementable (§4.1) |
| D15 | **`"board"` appended at the END of both allow arrays** | Minimal, symmetric diffs; least likely to collide with a concurrent edit. Both `meta.json` are covered by **NEITHER** seal baseline — the 161-entry committed manifest `agents/.prompt-artifacts.sha256` has **zero** `meta.json` rows, and the 35-entry `SEALED_SHA_BASELINES` test tripwire (`tests/test_chart_image_delivery_e2e.py:1350-1398`) lists `agents/charter/meta.json` but neither ari nor leader `meta.json` ⇒ **this phase's allow-list edits carry NO re-stamp obligation**. This phase touches **no** prompt artefact at all: the `.md` artefacts (ari `rule.md`/`soul.md`/`tools_note.md`/`workflow.md`; leader `growth.md`/`rule.md`/`soul.md`/`tools_note.md`/`workflow.md`) **ARE** manifest-covered, and `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` are **also** in the test tripwire ⇒ every prompt-artefact edit is **deferred to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4) and phase 1 carries **no** re-stamp task |
| D16 | **No REST surface in phase 1** | The bound phase-1 shape is tools-only; phase 3 adds the live view |
| D17 | **The board is NOT a replacement for `todo`** | `todo` is an in-memory per-instance DAG (`daemon/services/todo_manager.py:186-214`); the board is durable and global. The distinction must be documented (T10) |

---

## 14. Success criteria

1. `board_create` → `board_update` → `board_list` → `board_get` work from an Ari instance and from a Leader instance, and both see the **same** items (global namespace proven across two different `current_instance_id` values).
2. Items, notes, and tags survive a daemon restart (DB-backed): `create_all` creates the three tables **and** the six model-declared indexes on both drivers, and the SQLite `.sql` path is green too.
3. Notes are provably append-only: no update/delete code path; the **SQL-capture guard** shows only `INSERT`/`SELECT` against `board_notes`; the count grows monotonically; the static source scan is green; the two child FKs are `NO ACTION`.
4. `board_list` filters correctly by `status` (incl. `status="open"`), `project_id`, `tag`, and `due_before` — **including a `due_before` in a non-UTC offset** (the mixed-offset inversion probe is green, §6.1) — and sorts `ORDER BY updated_at DESC`.
5. The 4 tools are visible **only** to `ari` and `leader`: the **privileged** category is default-denied (an agent with an **empty** allow list resolves **zero** board tools), the `agent_id ∈ {"ari","leader"}` body gate rejects everyone else, the allow-list gating test is green, and the **five SAME-PR pins** (§3.2.1) plus the `KNOWN_TOOL_NAMES` drift pin stay green.
6. Zero seal violations **introduced by this phase**: the seal-test mismatch count **before == after** (the measured baseline at this HEAD is **7** — the test is **already red** on 7 pre-existing stale pins, which are **NOT ours to fix**; the acceptance is "no NEW stale pins beyond the baseline", NOT "green"); `agents/ari/{rule,workflow}.md` and `agents/leader/rule.md` are byte-identical — phase 1 edits **no** prompt artefact.
7. The phase-2 (`self_alarm`) and phase-3 (live view) schema hooks are present: `due_at` (tz-aware, indexed), `status` (indexed), `tags` (junction), `linked_job_ids` (JSONB) — no phase-2/3 column added later.
8. The two failure-path contracts are proven: the **id-collision retry-once** (the service retries once; the agent never supplies the id) and the **index parity** (names **and** column lists across the three sites, with the `.sql` applied verbatim to a temp SQLite DB).

---

## 15. Exit Criterion

Phase 1 is **done** when all of the following hold (this is the signal that Phase 2 may start):

1. `board_create` / `board_update` / `board_list` / `board_get` are bound for an Ari instance and a Leader instance, and both observe the **same** global namespace (verified by a cross-instance test with two different `current_instance_id` values — §14 criterion 1).
2. The three tables exist on both drivers: `SQLModel.metadata.create_all` at the manager boot creates them **and** their six model-declared indexes (the migration runner is **SQLite-only**, `daemon/migrations/runner.py:721-726` — the model is the source of truth on both drivers, the `.sql` is the record + the SQLite index fallback, §5.4); the `.sql` also applies cleanly on SQLite, and the migration file's version prefix is unique **and on a distinct day** (`ls daemon/migrations/versions/ | grep '^20261020_'` → exactly this one file).
3. Items, notes, and tags written before a restart are readable after it (DB-backed; §5.4).
4. Notes are provably append-only: no update/delete code path exists, the SQL-capture guard shows only `INSERT`/`SELECT` against `board_notes`, the count grows monotonically, the static source scan is green, and the two child FKs are `NO ACTION`.
5. The 4 tool names are present in `DYNAMIC_TOOL_NAMES`, `CATEGORY_MODULES`, and `KNOWN_TOOL_NAMES`; `"board"` is **entry #9** of `PRIVILEGED_TOOL_CATEGORIES`; the **five SAME-PR pins** (§3.2.1) are green; the bidirectional drift pin (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) is green; the **privileged default-deny** is proven for an empty-allow agent and the **body gate** for a non-ari/leader agent.
6. No seal violation **introduced by this phase**: the seal-test mismatch count **before == after** (baseline **7** at `630cefebb` — the test is already red on 7 pre-existing stale pins that are **NOT ours to fix**); `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` are byte-identical to `630cefebb` — phase 1 edits **no** prompt artefact and carries **no** re-stamp task.
7. No release is staged or promoted by this phase — promote remains the user's nonce ceremony.
8. `list_items` / `get_item` are **frozen** (names **and** keyword names) in the `board_service.py` docstring — the binding Phase-3 interface (§5.2) — and the deadline path is proven non-inverting (the `_to_utc_iso` round-trip + the mixed-offset probe, §6.1).

**Not a phase-1 exit condition (deliberately):** the v2 job-lifecycle auto-update (§9), the REST surface (Q6), the live board view (Phase 3), `self_alarm` (Phase 2), and the tools_note documentation (T10, deferrable).

---

## Amendment log (post-architect 2b297db7d)

The architect's adjudication (`.agents/shared/planning/ari-tools/architecture-recommendation.md`, 541 lines) landed on this branch at `2b297db7d`; the leader accepted every verdict as a **binding** ruling. The amendments below are **folded into the body above** — this document reads as-if-designed-so, not as a changelog; the log exists for traceability only.

| ID | Sev | Folded into | What changed |
|----|-----|-------------|--------------|
| **A-06** | P0 | §1, §2, §3.2 (+ new §3.2.1), §7, §8, §10 (T6 + the "Do NOT" rule), §11 (R2, R6), §13 D6, §14 (5), §15 (5) | `"board"` is **PRIVILEGED** — **entry #9** of `PRIVILEGED_TOOL_CATEGORIES` from Phase 1's **first** commit (E12) + the **five SAME-PR exact-set pin insertions in the same commit** (E13) + the `agent_id ∈ {"ari","leader"}` body gate through **one** shared helper (category gate **and** body gate are both required — a literal tool-name allow entry bypasses the category gate) |
| **A-07** | P0 | §12 Q1, §3.2, §8, §11 (R2) | Q1 **RESOLVED**, with the two evidence corrections: `worker`/`tester`/`developer`/`designer` have **non-empty** allow lists; the real inheritors are **`watcher`** (the only in-tree empty-allow agent) and tools-less custom agents |
| **A-08** | P0 | §5.4 point 2, §5.3 | The migration runner is **SQLite-only**; `create_all` runs **first on both** drivers; the **model** is the source of truth; the `.sql` is the persistent record + the SQLite index fallback |
| **A-09** | P0 | §4.1, §4.2, §13 D14 | Model shapes: every string column `Text`; explicit `ForeignKey(..., ondelete="SET NULL")`; explicit `JSONBType`; `board_notes.id` autoincrement |
| **A-10** | P0 | §4.1, §8 (N5) | Index set replaced with the architect's set: drop `created_at`, add `updated_at`, composite `(status, updated_at)`; `ORDER BY updated_at DESC` pinned |
| **A-11** | P0 | §6.1, §7.1, §8 (N5), §11 (R11), §13 D7/D8 | `due_before` normalised by the **same** `_to_utc_iso()` as the `due_at` write path — framed as a **MUST-FIX** implementation constraint (the due_at/due_before UTC inversion is one of the five blocking defects) |
| **A-12** | P0 | §5.2, §8 (N5), §11 (R1) | Service-side **retry-once** contract on an id collision — the agent never supplies the id and never retries |
| **A-13** | P0 | §4.2, §4.3, §7.3, §8 (N5/N6), §11 (R3) | Child FKs **NO ACTION** (no `ondelete=` clause); the **SQL-capture append-only guard** is the primary proof (the static scan stays secondary); R19 corrected — the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on every connection, so FK behaviour holds on **both** drivers at runtime |
| **A-14** | P0 | §3.1 (N4 + prefix note), §10 (T2), §11 (R8), §15 (2) | Migration → distinct-day prefix `20261020_060000` (prefix uniqueness re-verified at write time **and** merge time); a mandatory `-- DOWN` section; **no inline `;`** in comments |
| **A-15** | P0 | §8 (N7) | Index parity compares **columns** as well as names, **plus** a migration-smoke test that applies the `.sql` **verbatim** to a temp SQLite DB |
| **A-16** | P1 | §5.3, §13 D13, §11 (R4, R12) | `create_all`-index corollary: a **later** index ADD needs **both** a `.sql` file **and** an `_ensure_postgres_columns` entry |
| **A-17** | P1 | §5.2, §8 (N5/N6), §15 (8) | `list_items(..., with_notes=False)` + **batched** notes/tags loaders; `list_items` / `get_item` **frozen** (names + keyword names) as the binding **Phase-3** interface — the one cross-phase seam |

The **C1 ruling**'s consequences were propagated everywhere the old "non-privileged" choice appeared — §1, §2, §3.2 (+ §3.2.1), §7, §8, §10 (T6 + the "Do NOT" rule), §11 (R2/R6), §12 (Q1), §13 (D6), §14 (criterion 5), §15 (criterion 5): the ari-only allow-list assertion became an **ari + leader** assertion, and the registration test gained the **privileged five-pin** assertion plus the empty-allow default-deny proof.

### Approver iteration 002 amendments (2026-10-20)

The approver's iteration-001 notes (`/home/nea/ensemble-src/.agents/approver/ari-tools-tracking.md` — iteration 001 = REJECTED, carried four items against **this** phase-1 file) are folded into the body above, same convention as the architect log: this document reads as-if-designed-so.

| ID | Sev | Folded into | What changed |
|----|-----|-------------|--------------|
| **A2** | P1 | §3.1 (N4 + the new *`IF NOT EXISTS` + create_all-first* note), §8 (N7), §10 (T2) | Every `CREATE TABLE` / `CREATE INDEX` in the board `.sql` carries **`IF NOT EXISTS`** — mandatory, because the boot path runs `create_all` **first** (`daemon/manager.py:751`) and the runner applies the `.sql` immediately after (`:754-757`), so every object the `.sql` declares already exists; without it the runner swallows each statement as *"already exists"* (`daemon/migrations/runner.py:587-591`) and the file silently degrades into a no-op on every boot. **N7 now asserts the create_all-FIRST ordering** (apply `create_all`, then the `.sql` **verbatim** on the same temp SQLite DB — it must not error), **statically asserts** `IF NOT EXISTS` on every `CREATE` in the UP section, and **source-order asserts** `create_all` before `run_pending_migrations()` in `daemon/manager.py` |
| **A3** | P1 | §4.3, §5.2 (`update_item` signature), §7.1 (`BoardUpdateInput`), §7.2 (table row + new bullet), §8 (N6), §12 (Q2) | **`tags` are IMMUTABLE post-create** — `board_update` has **no tag parameter**, the `board_item_tags` junction rows are written **only** by `board_create`, and the A-17 batched tag loader only **reads** them. The **create-only field set** is now stated plainly as `{title, project_id, tags, priority}`; Q2's disposition reconciles *"cannot clear"* with *"create-only"* (only `due_at` is settable-but-not-clearable; `project_id` / `priority` are never settable post-create at all), and a future `clear_fields`-style amendment is flagged as having to cover the tag **junction**, not just item columns |
| **A4** | P1 | §4.1 (FK-target anchor), §13 (D14), §7.2 (PB-F1 anchor + item-shape anchor) | **Two stale anchor hops corrected against THIS worktree.** (i) D14's import chain ended at `daemon/repositories/project/repository.py:17` — an **import** statement, not the model — it now lands on the real declaration site, the `Project` SQLModel class at `daemon/repositories/project/models.py:329` (`project_id` PK at `:333`), with the chain (`daemon/manager.py:44` → `repository.py:17`) kept explicit; §4.1's FK-target clause carries the same correction. (ii) §7.2's PB-F1 citation `daemon/tools/secrets_tools.py:47-58` opened in the **wrong paragraph** (the tool-name *"documentation shape"* text); it now points at the actual **PB-F1 error-envelope** section `:52-63`, with the adjacent mirror `daemon/tools/ens_env_tools.py:377-395` named, and the canonical item shape gained a live anchor (`tests/unit/tools/test_scheduling_tools.py` + the service projection, §5.2). Re-verified and still accurate: `daemon/tools/_truncate.py:113`, `daemon/tools/scheduling.py:57-59`, `daemon/repositories/project/repository.py:1088` |
| **A6** | P1 | Header (Status), §10 (new **T12**), §10 standing note | The **Status** header no longer reads *"Ready for Review"* — it reads **APPROVED (reviewer re-gate — 0 critical findings) · approver iteration 002 amendment in progress**. The `_strip_privileged_category_tools` docstring correction is **no longer a standing note** — it is now the **explicit numbered checklist item T12**, carrying its acceptance criterion (pointer-to-the-registry-constant preferred, else the post-phase-1 9-entry list, in the same commit) so it cannot be skipped |
