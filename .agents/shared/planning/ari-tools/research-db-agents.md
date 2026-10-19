# Research — DB migration/repository/tag precedents + ari/leader agent surfaces

Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`).
All anchors below are from THIS checkout.

---

## 1. Migrations discipline (`daemon/migrations/versions/`)

### 1(a) Filename grammar

Grammar (README): `{YYYYMMDD}_{HHMMSS}_{description}.sql` — `daemon/migrations/README.md:38-49`.
Parser regex (authoritative): `^(\d{8}_\d{6})` for the version, and `^\d{8}_\d{6}_(.+)$` for the name; `_` in the name is rendered as spaces — `daemon/migrations/runner.py:87-95`.

Last 10 filenames in sorted order (from directory listing):

```
20261014_040000_create_custom_agent_configs.sql
20261017_000001_create_secrets_tables.sql
20261017_193816_create_live_view_passcode_tables.sql
20261018_000001_create_app_settings.sql
20261018_000001_secrets_audit_add_context.sql
20261018_090001_create_resource_daemon_bucket.sql
20261018_090002_create_resource_instance_sample.sql
20261018_090003_create_resource_pid_map.sql
20261018_090004_create_resource_limit_policy.sql
20261019_120822_alter_resource_monitor_booleans.sql
```

(Note the two `20261018_000001_*` files — duplicate version prefix; see 1(b).)

### 1(b) Ordering / application / duplicate-version failure

- Discovery: `self.migrations_dir.glob("*.sql")` in `sorted(...)`, parse each, then final `sorted(migrations, key=lambda m: m.version)` — `daemon/migrations/runner.py:285-304`.
- Pending = discovered minus applied ledger versions — `runner.py:306-314`.
- Apply loop — `runner.py:735-751`; each migration is applied in `with self.engine.begin()` — `runner.py:543`.
- The runner is a **SQLite-only no-op** for non-SQLite engines — `runner.py:699-726` (`if "sqlite" not in str(self.engine.url): ... return []`).
- Uniqueness: `schema_migrations.version TEXT PRIMARY KEY` — `runner.py:211-220`; the ledger row is inserted in `_add_ledger_row` — `runner.py:453-474`.
- Documented symptom: `UNIQUE constraint failed: schema_migrations.version` / "Use a unique timestamp for new migrations" — `daemon/migrations/README.md:462-468`.

Why a duplicate prefix breaks a fresh SQLite boot: both files are discovered as pending (the applied set is computed once), the first applies and inserts its version into the PRIMARY KEY ledger row, then the second applies and its ledger INSERT violates the PK, raising `MigrationError` and aborting boot.

Concrete collision in this tree:
- `20261018_000001_create_app_settings.sql`
- `20261018_000001_secrets_audit_add_context.sql`

Both are auto-apply (no `MANUAL: TRUE`). This is the open pre-existing defect recorded in project critical notes.

Counter-example (safe duplicate): the `20260628_000002` pair
- `20260628_000002_drop_admission_legacy.sql:4` — `-- MANUAL: TRUE`
- `20260628_000002_drop_job_queue_legacy_columns.sql:4` — `-- MANUAL: TRUE`

Manual-only migrations are skipped by the auto-apply loop (`runner.py:737-746`), so their shared prefix never hits the ledger on boot.

Separate pre-existing fresh-SQLite blocker: `20260714_000001_widen_job_queue_type_constraint.sql` uses `DROP CONSTRAINT IF EXISTS`, invalid on SQLite; the secrets migration header documents this defect — `20261017_000001_create_secrets_tables.sql:134-137`.

### 1(c) "Every new SQLModel table requires its own .sql file"

Where the requirement comes from:
- The `.sql` files are the canonical **SQLite** DDL; PostgreSQL is created from SQLModel via `create_all` — `daemon/models/resource_monitor.py:7-17`, `daemon/migrations/versions/20260925_194500_create_snapshot_tables.sql:9-14`, `daemon/repositories/secrets/models.py:47-51`, `20261017_000001_create_secrets_tables.sql:120-126`.
- The runner cannot stand in on PG (`runner.py:699-726`), so the file is the persistent SQLite + audit record.
- Per-table discipline is test-enforced per family. Example: auth migrations map one file per table name — `tests/migration/test_auth_tables_g2_2.py:215-219` (`table_names_in_files == set(AUTH_TABLE_NAMES)`), and "own per-table migration, index-name parity" — `tests/migration/test_auth_tables_g2_2.py:222-225`.
- Index-name parity tests exist per family, e.g. `tests/migration/test_prompt_versions_index_parity.py`, `test_secrets_index_parity.py`, `test_drift_events_index_parity.py`, `test_stored_watches_index_parity.py`.
- No single global assertion over *all* SQLModel tables was found; enforcement is by family-specific parity tests + review convention.

### 1(d) PostgreSQL column additions — `_ensure_postgres_columns`

- Method and rationale — `daemon/manager.py:6597-6605`: `create_all` does not add columns to existing tables; the migration runner skips non-SQLite; so PG gets idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements.
- Execution: statement list executed under one `engine.begin()` — `daemon/manager.py:7962-7965`.
- Recent precedent — `secrets_audit.context`:
  - comment + obligation — `daemon/manager.py:7935-7957`
  - statement — `daemon/manager.py:7958-7961`:
    ```python
    (
        "ALTER TABLE secrets_audit ADD COLUMN IF NOT EXISTS "
        "context TEXT"
    ),
    ```
  - SQLite companion (plain portable ADD COLUMN) — `daemon/migrations/versions/20261018_000001_secrets_audit_add_context.sql:66`.
- Invoked at boot only for PostgreSQL — `daemon/manager.py:772-773`.

### 1(e) Full recent new-table migration body — `20261018_090004_create_resource_limit_policy.sql`

```sql
-- Migration: create resource_limit_policy
--           (resource-cost-control Phase 1 — control seam)
-- Created: 2026-10-18
-- Phase: resource-cost-control P1 (plan:
--   .agents/shared/planning/resource-cost-control/phase1-plan.md).
--   Backing store for T4 ``resource_limit_policy``: the control seam
--   for v2 enforcement. EMPTY in v1 (observe-only); the chromium
--   concurrency cap (O2) lands later as a
--   ``scope='child_kind', target='chart_render'`` policy consumed at
--   the chart spawn seam.
--
-- What lives here:
--   * ``enabled`` defaults FALSE — every row is observe-only until
--     an operator enables it.
--   * ``user_gated`` is NOT NULL DEFAULT TRUE — every policy must be
--     gated by default; the un-gated form only lands via the
--     user-ceremony nonce gate (architecture §2.4 T4).
--   * v1 ships the table EMPTY (the FE control surface is P6).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`ResourceLimitPolicy` model
--     (``daemon/models/resource_monitor.py``) via the late-import
--     block in ``daemon/manager.py:738-748``.
--   - 3-site index lockstep (D7): ``idx_resource_limit_policy_scope_target_metric``
--     is byte-identical to ``__table_args__`` in
--     ``daemon/models/resource_monitor.py:ResourceLimitPolicy`` and
--     to the storage test pack (AC3).
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner; inline
--   comments that contain a ``;`` character are NOT stripped. This
--   file uses no inline semicolons.

-- UP

CREATE TABLE IF NOT EXISTS resource_limit_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT,
    target TEXT,
    metric TEXT,
    op TEXT,
    threshold REAL,
    window_s INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    user_gated INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_resource_limit_policy_scope_target_metric
    ON resource_limit_policy (scope, target, metric);

-- DOWN

DROP INDEX IF EXISTS idx_resource_limit_policy_scope_target_metric;
DROP TABLE IF EXISTS resource_limit_policy;
```

---

## 2. Repository / service precedent for the newest table family

### Resource-limit / resource-monitor family
- Model home: `daemon/models/resource_monitor.py` — four tables; T4 at `:321-378`.
- No repository class and no persistence service for T4; T4 is an empty control seam (`resource_monitor.py:41-43`, `:321-328`).
- Observability service: `daemon/services/resource_monitor/facade.py` (`class ResourceMonitor` at `:35`), built by `build_resource_monitor(...)` at `daemon/api.py:2069-2098`; sampler/flusher threads live in the package (`daemon/services/resource_monitor/flusher.py`).

### Secrets family (better full-stack precedent)
- Model home: `daemon/repositories/secrets/models.py` — `Secret` at `:99`, `SecretAuditEvent` (append-only audit) starting `:156` in the DDL; module docstring `:1-72`.
- Repository: there is **no** `*Repository` class — `daemon/repositories/secrets/` contains only `__init__.py` and `models.py`. The service is engine-bound.
- Service: `class SecretService` — `daemon/services/secret_service.py:1129-1171`; docstring states "the service is engine-bound (a single SecretService instance per process; today the manager is the wiring site)".
- Wiring: `self._secret_service = SecretService(engine=self._engine)` — `daemon/manager.py:1381`; model `create_all` calls live in the API lifespan — `daemon/api.py:893-907`.
- Append-only audit table precedent: `secrets_audit` DDL — `20261017_000001_create_secrets_tables.sql:156-165`; index `(secret_id, at)` at `:164-165`.

### PG-primary vs SQLite-compat techniques observed
- Dialect-aware JSON: `JSONBType(TypeDecorator)` maps to PG `JSONB` else generic `JSON` — `daemon/repositories/infra/types.py:35-71`.
- Booleans: explicit `sa_column=Column("enabled", Boolean, nullable=False)` — `daemon/models/resource_monitor.py:361-376`; SQLite stores via integer affinity; PG `BOOLEAN` (see recent alter migration `20261019_120822_alter_resource_monitor_booleans.sql`).
- IDs: UUID4 strings as TEXT for cross-driver portability — `daemon/repositories/secrets/models.py:41-46`.
- Timestamps: ISO-8601 TEXT, not DB `now()`:
  - aware-UTC house style — `daemon/repositories/project/models.py:21-30` (`datetime.now(timezone.utc).isoformat()`);
  - resource-monitor deliberately naive-UTC — `daemon/models/resource_monitor.py:21-25`.
- Blobs: `LargeBinary` (BLOB/BYTEA) — `daemon/repositories/secrets/models.py:47-51`.
- Counters: `BigInteger` via `sa_column` — `daemon/models/resource_monitor.py:133-150`.
- No native enums/ARRAY in ORM models.

---

## 3. Tag-like columns — how this repo stores arrays/tags

### Verdict
No ORM model uses PostgreSQL-native `TEXT[]`/`ARRAY`. Grep for `ARRAY(` / `postgresql.ARRAY` / `TEXT[]` across `daemon/` finds only raw SQL in `daemon/checkpoint_adapter.py:1142-1398` and a comment in `20261011_102502_set_checkpoint_trio_autovacuum_reloptions.sql:71`. The two sanctioned patterns are:

1. **Junction table** (filterable many-to-many) — projects:
   - `class ProjectTagLink(SQLModel, table=True)` with `__tablename__ = "project_tags"`, composite PK `(project_id, tag)` — `daemon/repositories/project/models.py:146-158`.
   - Load: `select(ProjectTagLink).where(...)` → `[link.tag ...]` — `daemon/repositories/project/repository.py:60-63`.
   - Sync: delete-all + bulk insert — `daemon/repositories/project/repository.py:94-104`.
   - On the ORM object, tags are runtime-only `_tags` with a property — `daemon/repositories/project/models.py:389-399`.
2. **JSONBType list column** (portable JSON, not PG array):
   - `snapshots.domain_tags: list[str]` via `Column("domain_tags", JSONBType)` — `daemon/repositories/snapshot/models.py:194-197`; SQLite DDL `domain_tags TEXT NOT NULL DEFAULT '[]'` — `20260925_194500_create_snapshot_tables.sql:38`.
   - Blueprints: `tags: list[dict]`, `file_refs: list[str]`, `trigger_queries: list[str]` all `JSONBType` — `daemon/repositories/blueprint/models.py:65-76`.
   - Projects `related_directories: list[str]` via `JSONBType` — `daemon/repositories/project/models.py:347-350`.
   - Skills: `condition_json` and embedding vectors via `JSONBType` — `daemon/repositories/skill/models.py:543`, `:563-590`.
3. **Scalar tag column** (single version tag, not a set):
   - `Instance.agent_tag: str | None` — `daemon/models/instance.py:73`.
   - `JobItem.agent_tag` with PG `ADD COLUMN IF NOT EXISTS agent_tag VARCHAR` — `daemon/manager.py:7370-7377`; SQLite migration `20260729_000001_add_agent_tag_to_job_queue_items.sql:36`.

**Recommendation implied by precedent:** for a board `tags[]` used for filtering/joins, the project-tags junction-table pattern is the established filterable choice; for a simple embedded list without relational filtering, `JSONBType` (`list[str]`) is the established portable choice (`snapshots.domain_tags` / `blueprints.tags`). Do not use PG-native `TEXT[]`.

---

## 4. Agent surfaces

### 4(a) `agents/ari/` — all files

```
meta.json     41 lines
rule.md      238 lines
soul.md      258 lines
tools_note.md 625 lines
user.md      172 lines
workflow.md  426 lines
```

No `skill-set.yaml` (answer to 4c: **no**). No `skills-template/`, no `memory.md`.

`agents/ari/meta.json` VERBATIM:

```json
{
  "id": "ari",
  "name": "Ari",
  "description": "Your smart general-purpose virtual assistant — the front door to ensemble. Does quick tasks directly and delegates complex work to specialist agents via the job system.",
  "icon": "🌟",
  "color": "accent-amber",
  "version": "1.2.0",
  "system": false,
  "innate_skills": ["job-orchestration", "chart", "todo"],
  "tools": {
    "allow": [
      "job",
      "bash",
      "filesystem",
      "time",
      "self",
      "help",
      "image",
      "image-gen",
      "context",
      "shared_meta_kv",
      "project",
      "job_messages",
      "job_tree",
      "job_progress",
      "job_inject",
      "pause_instance",
      "resume_instance",
      "system_upgrade",
      "mission",
      "service",
      "scheduling",
      "view-views",
      "cu",
      "secrets",
      "settings"
    ],
    "deny": ["edit_file", "write_file", "watch_job", "watch_jobs"]
  },
  "default_queue": "system_parallel_queue"
}
```

Characterization:
- `soul.md` (258 lines): identity + three modes of operation; first person ("I'm Ari — your virtual assistant and the front door…") — `soul.md:1-22`.
- `rule.md` (238 lines): hard constraints; starts with Chart Delivery on Chat Sources, then "TRUEAUTO MODE (DEFAULT)" non-negotiable autonomy — `rule.md:1-22`.
- `workflow.md` (426 lines): three mode workflows; cardinal rule that all project work routes to Leader and Ari never reads project files itself — `workflow.md:1-26`.
- `tools_note.md` (625 lines): tool-by-tool reference; begins with Job Visibility Tools (`job_messages`/`job_tree`/`job_progress`/`job_inject`) — `tools_note.md:1-18`.
- `user.md` (172 lines): user-facing "Meet Ari" documentation of capabilities — `user.md:1-16`.
- `meta.json` has no `model`/lane override key and no `team_members`; it declares `default_queue: "system_parallel_queue"` and `system: false`.

### 4(b) `agents/leader/meta.json` tools section VERBATIM (`:13-16`)

```json
  "tools": {
    "allow": ["instance", "subtree_messages",
    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings"]
  },
```

Leader has no `deny` list; it also declares `no_force_explore: true` (`:9`), `context_injection.heuristic_match_shared_md_files: true` (`:10-12`), and `team_members` at `:17`.

### 4(c) `agents/ari/skill-set.yaml`
Not present. Directory contains only `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `user.md`, `workflow.md`.

---

## 5. Seal-pinning exposure

`SEALED_SHA_BASELINES` is defined in **`tests/test_chart_image_delivery_e2e.py:1350-1398`** (35 entries). `tests/test_chart_image_delivery_audit.py` references its provenance block at `:167` but does not define a separate dict.

Pinned `agents/ari/*` and `agents/leader/*` paths:

| Path | Baseline location |
|---|---|
| `agents/ari/workflow.md` | `tests/test_chart_image_delivery_e2e.py:1359` |
| `agents/ari/rule.md` | `tests/test_chart_image_delivery_e2e.py:1381` |
| `agents/leader/rule.md` | `tests/test_chart_image_delivery_e2e.py:1388` |

NOT pinned: `agents/ari/meta.json`, `agents/ari/soul.md`, `agents/ari/tools_note.md`, `agents/ari/user.md`, `agents/leader/meta.json`.

Mechanism (`tests/test_chart_image_delivery_e2e.py:1418-1450`):
```python
for rel_path, baseline_sha in SEALED_SHA_BASELINES.items():
    abs_path = worktree_root / rel_path
    if not abs_path.exists(): missing.append(rel_path); continue
    current_sha = hashlib.sha256(abs_path.read_bytes()).hexdigest()
    if current_sha != baseline_sha:
        mismatches.append((rel_path, baseline_sha, current_sha))
...
if missing:   pytest.fail("Sealed artifacts missing ...")
if mismatches: pytest.fail("Phase D modified sealed Phase A/B/C artifacts: ...")
```

Re-pin procedure structurally: any commission that intentionally edits a sealed file must (1) attribute the mismatch via git-log last-touch, (2) update the baseline constant to the new sha with a provenance comment, (3) run the seal test green, (4) commit the re-pin as an explicit, provenance-labeled step. Recent in-tree precedent is the re-pin comments at `tests/test_chart_image_delivery_e2e.py:1362-1367` and `:1343-1349`.

---

## 6. `docs/agent-prompt-writing-guide.md` — top-level rules (≤15 lines)

1. Write as the agent, first person; never describe daemon/loader internals (`meta.json`, `tools.allow`, `daemon/` paths, seeding, `agent_id=` plumbing) — `:9-49`.
2. If the agent cannot act on a fact, delete it — `:49`.
3. One concern per file; composition order `soul → rule → skills → tools_note → workflow → memory` — `:53-64`.
4. Canonical-home rule: each repeated artifact (tier table, dispatch snippet, template, contract) has exactly one home; other files link by section name — `:66-78`.
5. `rule.md`: ≤7 Cardinal rules at top, everything else Guidelines; delete duplicates — `:82-89`.
6. Cross-reference convention v2: section name (+ owning agent) only; no filename/path tokens in prompt text — `:91-122`.
7. Tool boundaries stated operationally, not as system reasoning; allow-list table in rule/tools_note, machine deny in `meta.json` without describing the mechanism in prose — `:126-140`.
8. Tone directive in `soul.md`: caller voice, dispatch-prompt voice, per-severity framing — `:144-152`.
9. Distinguish own auto-loaded `*-strategy` skills from dispatched execution skills; keep skill versions consistent (frontmatter is source of truth) — `:156-168`.
10. Async dispatch: END TURN after dispatch; define a fan-in escape valve with a max re-dispatch cap — `:172-199`.
11. Any dispatching agent must have report-scrutiny guidance conditioned on `[REPORT SANITY: …]` — `:201-213`.
12. Skill-bank fallbacks stay within `team_members`; no peer spawning outside the org chart; escalation is the caller's job — `:217-230`.
13. v1→v2 steady state: base and `[v2]` dirs move together unless divergence is explicitly documented — `:234-242`.
14. Pre-commit checklist: no system internals, canonical homes, no false "stated once", ≤7 cardinals, refs resolve, tone present, escape valve, version consistency, tests pass — `:246-260`.
15. File quick reference / size guidance (`soul` ~2k chars, `rule` ≤7 cardinals + guidelines, `workflow`/`tools_note` optional) — `:330-344`.
