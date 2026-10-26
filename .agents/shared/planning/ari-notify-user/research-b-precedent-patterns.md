# Research B — Precedent Patterns for a Privileged ari-only `notify_user` Tool

Worktree: `/home/nea/ensemble-src-wt-notify-user` @ `feature/ari-notify-user` (d4ecdbd3d). Read-only investigation; all paths relative to the worktree root. Line numbers verified against this tree on 2026-10-24.

---

## B1. `self_alarm` — the closest precedent

### Module layout

`daemon/tools/self_alarm.py` (1488 lines) — the LLM-facing closure layer:

| Element | Location |
|---|---|
| Module docstring: three-layer access posture, wake-the-caller doctrine | `daemon/tools/self_alarm.py:1-55` (posture at :19-29, wake semantics at :37-54) |
| `CATEGORY_NAME = "Self-Alarm"` | `self_alarm.py:90` |
| `CATEGORY_DOC` (category-level help text) | `self_alarm.py:91-112` |
| `SELF_ALARM_AUTHORIZED_AGENT = "ari"` (layer-3 runtime gate constant) | `self_alarm.py:114-116` |
| `SELF_ALARM_TARGET_AGENT = "ari"` (engine invoked-agent) | `self_alarm.py:118-122` |
| `SELF_ALARM_INSTANCE_MODE = "reuse_instance"` | `self_alarm.py:124-134` |
| `_error(msg)` envelope helper ("ERROR: {msg}", never raise) | `self_alarm.py:137` |
| Pure helpers: `_read_stored_row` / `_stored_weekday` / `_compatibility_gate` | `self_alarm.py:171` / `:190` / `:209` |
| `_resolve_project_id` (D11 resolve-first) | `self_alarm.py:306` |
| `_resolve_wrapper_timezone` | `self_alarm.py:332` |
| `_lateness_cap_seconds` (reads `SchedulingConfig()`) | `self_alarm.py:827` (reads at `:832`) |
| `_create_payload` | `self_alarm.py:839` |
| **Wake-the-caller binding helper** `_bind_schedule_to_caller` | `self_alarm.py:874-977` — mapper `force_new=False` rationale `:892`; stranger-spawn refusal rationale `:907-917`; `raise BoundCallerRefusal` `:931`; `source_repo.create_instance_mapping` `:942`; refusal on missing mapping `:965` |
| `_disarm_refused_arm` (safe rollback of a refused arm) | `self_alarm.py:990` |
| `_deferral_visibility` | `self_alarm.py:1106` |
| `_create_arm` / `_reuse_alarm` (both catch `BoundCallerRefusal`) | `self_alarm.py:1140` (catch `:1223`) / `:1296` (catch `:1359`) |

Pure service module split (P2-D2): `daemon/services/self_alarm_service.py` — `LABEL_NAMESPACE = "self-alarm:"` (:67), `MAX_LABEL_INPUT = 115` (:73), `MIN_HORIZON_SECONDS = 1800` (:77), `BOARD_ITEM_RE` (:80), families `FAMILY_ONCE/DAILY/WEEKLY/CRON` (:83-86), `SelfAlarmInputError(ValueError)` (:107), `ParsedWhen` (:122), `FirePlan` (:160), `enforce_min_horizon` (:550, floor check `:566`). `__all__` at :41.

### Tool factory + registration

- `create_self_alarm_tools(service, *, source_repo=None, instance_repository=None, project_repository=None, current_instance_id="", agent_id="", agent_tag=None) -> list` — `self_alarm.py:409-418`; docstring binds each param to a manager attribute (:421-437).
- `class SelfAlarmInput(BaseModel)` pydantic args schema, `Annotated[str, Field(description=...)]` per arg — `self_alarm.py:445-489` (label :448, when_or_every :457, message_to_self :469, link_board_item :479).
- Decorator order is load-bearing: `@register_tool_category("self-alarm")` **above** `@tool(args_schema=SelfAlarmInput)` — `self_alarm.py:491-492`; pinned by `tests/unit/test_self_alarm_registration.py:174`.
- `async def self_alarm(...)` with a duplicated `Annotated` signature and one-line docstring pointing at `tool_help` — `self_alarm.py:493-537`.
- Error contract: `ValueError` surfaced, everything else logged server-side + generic `"ERROR: ..."` — `self_alarm.py:551-557` (house rule documented in the module docstring :9-17).
- `_full_doc_` assignment (the tool_help surface) — `self_alarm.py:559-655`; returns `[self_alarm]` at `:657`.
- Runtime gate inside `_arm_self_alarm`: `if agent_id != SELF_ALARM_AUTHORIZED_AGENT: return _error(...)` — `self_alarm.py:680-685`.

### Config knobs

**There are NO `SELF_ALARM_*` keys in `daemon/config.py`** (grep over `config.py` for `SELF_ALARM|self_alarm|self-alarm` returns only `SchedulingConfig`). The feature's knobs live in `SchedulingConfig` (`daemon/config.py:2716`), `SettingsConfigDict(env_prefix="ENSEMBLE_SCHEDULING_")` at `:2730`:

| Field | Default | Env var | Cite |
|---|---|---|---|
| `default_timezone` | `None` | `ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE` | `config.py:2732-2737` |
| `one_shot_max_lateness_seconds` | `None` (= unlimited) | `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS` | `config.py:2738-2744` |
| `tz_warning_echo_to_tool_output` | `True` | `ENSEMBLE_SCHEDULING_TZ_WARNING_ECHO_TO_TOOL_OUTPUT` | `config.py:2745-2749` |
| `host_local_tz_cache_seconds` | `300` | `ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS` | `config.py:2750-2754` |
| `negative_cache_seconds` | `60` | `ENSEMBLE_SCHEDULING_NEGATIVE_CACHE_SECONDS` | `config.py:2755-2760` |
| `defer_max_count` (deferred-delivery bounds) | `16` | `ENSEMBLE_SCHEDULING_DEFER_MAX_COUNT` | `config.py:2766-2769` |

The tool echoes the daemon-global lateness cap (`lateness_cap_seconds`) rather than setting it — `self_alarm.py:582-591` (`_full_doc_`), read at `:832`. `SchedulingConfig` is mounted as `config.scheduling` at `config.py:4740`. Note for the planner: a `notify_user` wanting its own caps/rate knobs would add a new pydantic-settings section (no DDL; env-prefix auto-tracked by tests' `_TRACKED_ENV_PREFIXES`, `config.py:2724-2727`).

### Test packs

- **`tests/unit/test_self_alarm_registration.py`** — registration pins:
  - `EXPECTED_PRIVILEGED_SET` frozenset `:74`; `TestStaticRegistration :114` — AST discovery `:115`, `CATEGORY_MODULES` entry is a `daemon.tools.*` path `:121`, `DYNAMIC_TOOL_NAMES` `:132`, `KNOWN_TOOL_NAMES` `:138`, construction extend present in source `:142`, **extend precedes the filter and the doc scan** `:153`, decorator order `:174`, category metadata present `:187`.
  - `TestPrivilegedCategory :197` — membership `:198`, **exact-set equality** `:201-202`, **count pin `len(PRIVILEGED_TOOL_CATEGORIES) == 16` at `:205`** (the test *names* still say "fifteen" — stale names, live assertions).
  - `TestAllowLists :211` — ari allows the category `:221`, **no other agent holds it** `:224`, every allow entry is a known tool or category `:240`.
  - `TestToolVisibility :313` — privileged **default-deny for empty-allow** `:346`, non-empty-allow without the category denies `:384`, no tools config denies `:415`.
  - `TestToolDocObligations :447` — the five tool-doc obligations `:448-493` + due-at convention `:493`.
- **`TestDocSurfaceWakeTheCaller`** — `tests/unit/tools/test_self_alarm_tools.py:1802-1903`. Pins the **assembled** agent-visible doc surface (`tool._full_doc_` + every `args_schema.model_fields[*].description`), not a loose source grep — `_doc_surface()` at `:1850-1864`. `RETRACTED` needles are verbatim pre-fix strings (`:1842-1848`); parametrized absence test `:1866-1874`; positive assertions that `link_board_item`'s description teaches "THIS session" `:1876-1884` and that `_full_doc_` carries "ALARM INTENT"/"THIS session"/"context intact" `:1886-1894`; source-level belt for contiguous literals `:1896+`. Mutation-verified (plant stale string → RED).
- **`tests/unit/test_self_alarm_deferred_visibility_pin.py`** — `_FakeAdapter :262`, `_FakeRegistry :271`, `TestDeferredVisibility :434`: `test_schedule_detail_exposes_deferred_count_and_last_execution_status :437`; `test_no_migration_file_and_deferred_status_is_text :610` (asserts **no new migration file** and TEXT status).
- Privilege/literal-allow pins (from the phase-2 corpus): `tests/unit/tools/test_self_alarm_privilege_pins.py`, `tests/unit/tools/test_self_alarm_literal_allow_side_effect_pins.py` (zero-side-effect invariant on the literal-tool-name bypass path, with a negative control).

---

## B2. Privileged tool category registration

### The set

`PRIVILEGED_TOOL_CATEGORIES: frozenset[str]` — `daemon/tools/_tool_registry.py:338-588`. **Current count = 16**, in order:

| # | Category | Line | ARI-ONLY? |
|---|---|---|---|
| 1 | `system_upgrade` | `:339` | — |
| 2 | `system-log` | `:340` | — |
| 3 | `ens-db` | `:341` | — |
| 4 | `ens-env` | `:348` | — |
| 5 | `view-views` | `:363` | ari+leader+designer |
| 6 | `cu` | `:380` | ari+leader |
| 7 | `secrets` | `:411` | worker/tester/leader/ari |
| 8 | `settings` | `:437` | ari+leader |
| 9 | `board` | `:460` | **ari exactly** |
| 10 | `self-alarm` | `:482` | **ari exactly** |
| 11 | `view-write` | `:504` | **ari exactly** |
| 12 | `board-link` | `:524` | **ari exactly** |
| 13 | `wedge-capture` | `:539` | ari+leader+maintenancer |
| 14 | `instance_read` | `:561` | maintenancer |
| 15 | `instance_ops` | `:562` | maintenancer |
| 16 | `mcp_rpc` | `:587` | ari+leader |

SAME-PR pin rule header comment (grep the tree, don't trust the prose): `_tool_registry.py:315-337` (the "count assertion (16 today)" at `:326`).

### The v0.26.2 ARI-ONLY allow-sets (exact)

| Constant | Value | Cite |
|---|---|---|
| `BOARD_AUTHORIZED_AGENTS` | `frozenset({"ari"})` | `daemon/tools/board_tools.py:104` (checker `:119-121`) |
| `VIEW_WRITE_AGENTS` | `frozenset({"ari"})` | `daemon/tools/views_auth.py:67` |
| `BOARD_LINK_AGENTS` | `frozenset({"ari"})` | `daemon/tools/views_auth.py:77` (shared checker `_is_views_authorized` `:80`) |
| `SELF_ALARM_AUTHORIZED_AGENT` | `"ari"` (scalar) | `daemon/tools/self_alarm.py:116` |
| `INSTANCE_READ_AUTHORIZED_AGENTS` | `frozenset({"maintenancer"})` | `daemon/tools/instance_read.py:115` |
| `INSTANCE_OPS_AUTHORIZED_AGENTS` | `frozenset({"maintenancer"})` | `daemon/tools/instance_ops.py:142` |
| `WEDGE_CAPTURE_AUTHORIZED_AGENTS` | ari+leader+maintenancer | `daemon/tools/wedge_capture.py:84` |
| `MCP_RPC_AUTHORIZED_AGENTS` | `frozenset({"ari", "leader"})` | `daemon/tools/mcp_rpc_tools.py:82` |

Independent e2e verification of the v0.26.2 ruling: `.agents/tester/RESULTS/2026-10-21-v0262-ruling-e2e-staged-verification.md:34` (all three frozensets + two scalars verified `EXACTLY {ari}`).

### How access works (two structural gates + one literal bypass)

1. **Category gate**: privileged categories are stripped from every agent that doesn't explicitly allow them — `_strip_privileged_category_tools` at `daemon/tools/instance.py:6693` (default-deny strip; the registry comment cites the older line `instance.py:6169`), and the `resolve_tool_filter` empty-allow branch at `instance.py:393` denies privileged categories to empty-allow agents.
2. **Body gate**: every privileged tool ALSO carries an in-body `agent_id` check because **a literal tool name in a `tools.allow` list bypasses the category gate** (documented at `_tool_registry.py:453-455`, `self_alarm.py:27-29`, `instance.py:2398-2400`).
3. `_apply_tool_filter` (`instance.py:6731`) scopes the built tool list per agent afterwards.

A category is declared by: (a) stamping `@register_tool_category("<key>")` on each tool (`_tool_registry.py:591`), (b) a `CATEGORY_MODULES["<key>"] = "daemon.tools.<module>"` row (`_tool_registry.py:939` dict; `"self-alarm"` row `:981`; `"wedge-capture"` `:1036`; value MUST be a `daemon.tools.*` path for the AST scanner), (c) optionally joining `PRIVILEGED_TOOL_CATEGORIES`, (d) `DYNAMIC_TOOL_NAMES` (`:23`; `self_alarm` entry `:82-90`) + `KNOWN_TOOL_NAMES` (`:1240`; drift pin `test_known_tool_names_matches_source_exactly_no_drift` cited `:1235`).

### Registration pins (grep-verified, current)

**Exact-set pins** (`PRIVILEGED_TOOL_CATEGORIES == frozenset({...})`):
- `tests/unit/tools/test_upgrade_registration.py:152`
- `tests/unit/tools/test_attestation_registration.py:189`
- `tests/unit/tools/test_secrets_registration.py:226`
- `tests/integration/test_maintenancer_spawn_resolves_tools.py:366`
- `tests/integration/test_service_tool_flag_off_byte_identical.py:832`

**Count pins** (`len(PRIVILEGED_TOOL_CATEGORIES) == 16`):
- `tests/unit/test_board_registration.py:205`
- `tests/unit/test_instance_ops_registration.py:191`
- `tests/unit/test_self_alarm_registration.py:205`
- `tests/unit/test_wedge_capture_registration.py:166`

**`tests/unit/test_instance_ops_registration.py` structure** (550 lines): `EXPECTED_PRIVILEGED_SET :81-112` (16 entries incl. `wedge-capture` at `:101`, `mcp_rpc` at `:111`); `TestStaticRegistration :118` (AST finds all ten at any nesting depth `:119`, CATEGORY_MODULES `:126`, DYNAMIC `:139`, KNOWN `:143`, extend-in-source `:147`, extend ordering `:160`); `TestPrivilegedCategory :182` (both categories privileged `:183`, exact-set `:187-188`, len==16 `:191` — names say "fifteen", assertions say 16); `TestAllowLists :194` (maintenancer holds both `:209`, **ari does NOT hold either `:214`, leader does NOT `:219`, no other agent gained `:224`**); `TestProvenanceWiring :240`; `TestNoGateKeysOnPclass :311`; `TestProvenanceVocabulary :450`; `TestKillSwitchSurface :517` (env kill-switch name/default/off-spellings `:518-544`).

### What adding a new `notify` category touches (derived from the precedents)

1. `daemon/tools/_tool_registry.py` — 4 edits: `PRIVILEGED_TOOL_CATEGORIES` entry #17 (`:338-588`), `DYNAMIC_TOOL_NAMES` (`:23+`), `KNOWN_TOOL_NAMES` (`:1240+`), `CATEGORY_MODULES["notify"] = "daemon.tools.notify_user"` (`:939+`).
2. **All 9 pins** — 5 exact-set + 4 count (16 → 17), in the SAME commit (SAME-PR rule, `_tool_registry.py:315-337`).
3. `daemon/tools/instance.py` — `create_notify_user_tools_if_available(manager, ...)` factory + one `tools.extend(...)` **before** `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` (precedent: self-alarm extend `instance.py:6044-6047`; "decorator-only registration is SILENTLY invisible" comments `:6093`, `:6139`).
4. `agents/ari/meta.json` — `"notify"` in `tools.allow` (ari only; the only agent file carrying ari-tools privileged entries — grep over `agents/*/meta.json` confirms).
5. Tool body gate constant (`NOTIFY_AUTHORIZED_AGENTS = frozenset({"ari"})` style).
6. New registration test file (mirror `test_self_alarm_registration.py` / `test_wedge_capture_registration.py`).
7. `agents/ari/tools_note.md` section (the convention-closure surface; phase 2 authored `## Self-Alarm` there, `tools_note.md:713`).

---

## B3. ARI agent wiring

### `agents/ari/meta.json` (49 lines)

- `tools.allow` at `:11-45` — includes the privileged ari-tools entries: `"board"` `:39`, `"self-alarm"` `:40`, `"view-write"` `:41`, `"board-link"` `:42`, `"mcp_rpc"` `:43`, `"wedge-capture"` `:44`; plus base categories (job/question/bash/filesystem/time/self/help/image/context/...) and literal names (`job_messages`...`system_upgrade`, `pause_instance`, `resume_instance`).
- `tools.deny` at `:46`: `["edit_file", "write_file", "watch_job", "watch_jobs"]`.
- `default_queue: "system_parallel_queue"` `:48`.

**A new tool is exposed to ARI ONLY** by (1) making its category privileged (never default-granted; empty-allow agents like `watcher` structurally get nothing — `test_self_alarm_registration.py:346`), (2) adding the category key to **ari's** `tools.allow` only, and (3) the in-body agent gate. **Worker/leader/tester/maintenancer are structurally excluded**: grep of `agents/*/meta.json` for `self-alarm|board-link|view-write` matches `agents/ari/meta.json` ONLY; `instance_read`/`instance_ops` pin the inverse case (`test_instance_ops_registration.py:214-224` asserts ari/leader hold neither).

### Prompt files (`agents/ari/`)

Files present: `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `user.md`, `workflow.md`.

- **`tools_note.md`** — the tool-doctrine surface. Relevant headings: `## Board — Durable Cross-Instance Work Items` `:638` (global-namespace doctrine `:640-651`, tags-immutable `:651-655`, append-only notes `:665`, `### board_create :668` / `### board_update :684` / `### board_list :693` / `### board_get :704`, board↔alarm cross-ref `:709`); `## Self-Alarm` `:713-762` (`self_alarm` = reminder to MYSELF `:715-721`, example call `:727-732`, response fields `:737-738`, **"The board is the user-visible surface — promise the board, never a ping" `:758-762`**). Earlier sections: `## Job Visibility Tools :3`, `## Scheduling :552` (task_schedule `:560`), `## System Upgrade / Restart Tools :377`, `## Pausing / Resuming :457`.
- **`rule.md`** — `## Must :3` (`### Chart Delivery on Chat Sources :5`, `### 🚨 TRUEAUTO :9`, `### TASK TRIAGE :51`, `### Always Watch Jobs :102`, `### Know Your Limits :169`, `### Default Delegation Targets :179`); `## Must Not :192` (`### ❌ Never Use \`instance\` Tools :216`); `## Core Principles :230`. No board/self-alarm/voice sections.
- **`soul.md`** — `# My Nature: Three Modes :11` (Mode 1 `:17`, Mode 2 `:53`, Mode 3 `:121`), `# My Autonomy: TrueAuto :147`, `## What Counts as "Very Important / Breaking" :161`, `# My Relationship with the User :191`, `# How I Communicate :218`. No board/self-alarm/voice sections.
- **`workflow.md`** — no self-alarm/board/voice mentions surfaced.
- No "voice" wording exists anywhere under `agents/ari/` today — a notify tool would be the first; the `## Self-Alarm` section (`tools_note.md:713`) and its "never a ping" doctrine (`:758-762`) are the doctrine a notify tool would partially supersede and must be co-authored with it.

---

## B4. Audit ledger precedents (three)

### (i) `instance_ops_audit` (nest-ops v0.27.1) — the two-tier exemplar

- **Model**: `daemon/repositories/instance_ops/models.py` — `class InstanceOpsAudit(SQLModel, table=True)` `:50`, `__tablename__ = "instance_ops_audit"` `:93`, three named indexes `:95-105`. Two-tier contract declared in the model docstring `:1-13`.
- **Migration**: `daemon/migrations/versions/20261021_120000_create_instance_ops_audit.sql` — columns `:94-113`: `id TEXT PK`, `created_at TEXT`, `created_by_instance_id`, `created_by_agent_id`, `action`, `target`, `reason DEFAULT ''`, `dry_run INTEGER DEFAULT 0`, `before_snapshot JSON`, `after_snapshot JSON`, `outcome`, `confirmed_target_id`, `provenance_class`, `provenance_depth INTEGER`, `root_source`, `root_message_id`, `error_message`, `preview_run_id`; indexes `:115-122`; `-- DOWN` drops `:124-129`. The two-tier declaration lives in the SQL header `:28-48`.
- **Two-tier write mechanism** (`daemon/repositories/instance_ops/repository.py`):
  - **Tier-1 (true same-transaction)**: `write_audit_and_act(session, ..., act=)` `:88-152` — caller OWNS the session; repo only `session.add(row)` + `session.flush()` (`:145-149`) so an INSERT failure surfaces inside the act's transaction and rollback covers both halves; `act(session)` runs `:150-152`. Used by `instance_terminate` via the additive `audit_sink` hook in the lifecycle service (header `:31-36` of the SQL; `daemon/manager.py:12101` `audit_sink=None` param, `:12125` pass-through) and by `instance_mark(protect|suspect)` via `repo.set_metadata(..., session=)`.
  - **Tier-2 (write-ahead)**: `write_audit_ahead(...)` `:157+` — INSERT first with `outcome='pending'` + full intent, run the act, then settle to `committed`/`error` (`settle` lookups `:220-235`); a crash mid-act leaves a durable `pending` row (SQL header `:38-44`).
- **Query/read surfaces**: budget counters `count_terminates_since` `:333-341`, protected-terminate counts `:359-367`, `count_pending` `:378-384`, last-committed timestamps `:394+` — all defensive, never raise.
- **PG registration**: late import in `daemon/api.py:991-998` (comment `:991`, import `:997`, `create_all` `:998`) BEFORE the lifespan `create_all`; also `daemon/repositories/factory.py:72-77`. "Brand-new tables need NO `_ensure_postgres_columns` mirror" — `20261021_120000...sql:59-61`.

### (ii) `mcp_gateway_audit` (mcps-gateway Phase C)

- **Model**: `daemon/repositories/mcps_gateway/models.py` — `class McpGatewayAudit` `:43`, `__tablename__` `:61`; repository `McpGatewayAuditRepository` `daemon/repositories/mcps_gateway/repository.py:34`.
- **Migration**: `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql` — columns `:78-90`: `id TEXT PK`, `created_at TEXT`, `agent_id`, `instance_id`, `machine_id`, `tool`, **`args_hash`** (SHA-256 of canonicalised args, hashed-not-stored ruling `:20-25`), `outcome DEFAULT 'ok'`, `session_id`, `error_code`, `detail`; indexes `:92-99`; DOWN `:101-106`.
- **Write site**: `daemon/services/mcps_gateway/audit.py` (263 lines) — docstring `:1-38`: hash spec `:8-12`; **BEST-EFFORT write** (audit failure never aborts the command — logged loudly + counted) `:23-37`, divergence from the secrets same-transaction pattern is deliberate and recorded; `OUTCOME_OK/ERROR/DENIED/TIMEOUT` `:53-56`; `AUDIT_WIRE_KEYS` FE contract `:64-74` (write path persists `id` + `detail` storage-only columns by design `:58-63`). Wired at `daemon/manager.py:1461` (`audit_sink=McpGatewayAuditRepository(...)`).

### (iii) `secrets_audit` (agent-secrets P1/P2)

- **Model**: `daemon/repositories/secrets/models.py` — `class SecretAuditEvent` `:270`, `__tablename__ = "secrets_audit"` `:310`.
- **Migration**: created by `20261017_000001_create_secrets_tables.sql`; the **additive column** came later via `20261018_000001_secrets_audit_add_context.sql` (74 lines): adds ONE nullable `context TEXT` `:8-15`; P1 rows stay NULL, no backfill `:17-20`; the ALTER pattern rules `:22-60`:
  - runner applies .sql on SQLite ONLY; PG schema evolution routes through `EnsembleManager._ensure_postgres_columns` (cited `:26-29`, per the runner docstring contract);
  - fresh PG gets the column from `SQLModel.metadata.create_all` via the model field `:30-34`;
  - existing PG gets an idempotent `ALTER TABLE secrets_audit ADD COLUMN IF NOT EXISTS context TEXT` entry in `_ensure_postgres_columns` `:35-47`;
  - the .sql itself uses the plain portable `ADD COLUMN` (NOT `IF NOT EXISTS`, which SQLite rejects with a parse error the runner does not catch — the `20260714_000001` breaker class) `:49-60`.
- **Write path**: `daemon/services/secret_service.py` — insert inside the caller's transaction `:1036-1100` (`SecretAuditEvent.__table__.insert().values(...)` at `:1091`) — the same-transaction Tier-1 shape.

### Best-fit pattern for a notification-send audit ledger

`mcp_gateway_audit` is the closest SHAPE for "send" semantics (actor + destination + hashed payload + outcome + error class + session, best-effort write on an external side effect), while `instance_ops_audit` is the closest GOVERNANCE shape (privileged category ledger with Tier-2 write-ahead `pending → committed/error`). Fields requested (instance, session root, source, content hash/preview, outcome, latency, error class) map cleanly; **no precedent carries a latency column** — it would be a new additive field (the secrets `context` ALTER is the additive precedent). Content-preview vs content-hash should follow the §8.4 hash-not-store ruling (`mcp_gateway_audit.sql:20-25`) for anything sensitive. A Tier-2 write-ahead (`pending` → `sent`/`error`) fits a send that crosses an external API; the durable N-per-window counter can be a COUNT over this ledger exactly like the terminate budget (`repository.py:333-341`).

---

## B5. Migration file conventions

- **Naming**: `{YYYYMMDD}_{HHMMSS}_{description}.sql` — `daemon/migrations/README.md:38-49`; parsed by regex `^(\d{8}_\d{6})` at `daemon/migrations/runner.py:107-115`. The timestamp prefix IS the version (`schema_migrations` ledger keys on it, `runner.py:32-34`).
- **Format**: header comments + mandatory `-- UP` + recommended `-- DOWN` — `README.md:51-73`. Live-example header (verbatim shape): `-- Migration:` / `-- Created:` / `-- Phase:` / `-- What lives here:` / `-- WHY ...` / `-- DUAL-DRIVER NOTES` / `-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY)` / `-- RUNTIME-SPLITTER NOTE` / `-- PRE-EXISTING BLOCKERS` / `-- UP` / `-- DOWN` — `20261021_120000_create_instance_ops_audit.sql:1-90`, `20261022_000001_create_mcp_gateway_audit.sql:1-74`.
- **`IF NOT EXISTS` is MANDATORY on every CREATE** because `SQLModel.metadata.create_all` runs FIRST on the boot path and the runner applies the .sql immediately after; a bare CREATE would hit the runner's idempotent "already exists" swallow and silently become a no-op — `20261021_120000...sql:66-73`, `20261022_000001...sql:61-68`.
- **Runner is SQLite-only**: it skips non-SQLite engines; PG is served by `create_all` (late model imports in `daemon/api.py:991-998` BEFORE `create_all`) plus `_ensure_postgres_columns` for existing-PG column adds (contract cited at `20261018_000001...sql:26-29` and `20261019_120822...sql:23-28`). Runner idempotent catches: "already exists" / "no such column" / "has no column" (`runner.py:703-719`); unexpected → `MigrationError` (`:722-726`); ledger row persisted after (`:731-734`).
- **`MANUAL: TRUE` example**: `20261019_120822_alter_resource_monitor_booleans.sql` — header `-- MANUAL: TRUE` at `:3`; the file is "the audit/operator document" while the runtime correction lives in `manager._ensure_postgres_columns` (`:23-28`). `MANUAL: TRUE` is parsed by the same header family as preconditions (`README.md:96`).
- **Duplicate-prefix defect class**: `DuplicateMigrationVersionError` — `runner.py:29-46`: raised in `discover_migrations` BEFORE any apply (`:38-39`, raise site `:420`); names BOTH offending files; precedent = two files under `20261018_000001` and two under `20260628_000002` deadlocking fresh-SQLite boots (`:41-44`); fix recorded at `811d3e21b` and cited in `20261021_120000...sql:81-84`.
- **Fresh-SQLite boot blocker `20260714_000001`**: documented at `20261021_120000...sql:85-90` (fresh-SQLite boot "dies before any new migration"; test plans therefore run against scratch PG), `20261022_000002_create_wedge_captures.sql:71`, `20261017_000001_create_secrets_tables.sql:135`, `20261018_000001...sql:57`, `daemon/manager.py:8070`.
- **"One .sql per migration event / every new SQLModel table needs its own .sql file"**: stated in each audit migration's DUAL-DRIVER NOTES — "One .sql per migration event — this file is the persistent record" (`20261021_120000...sql:51-53`; `20261022_000001...sql:47-49`); README overview ("Migrations are SQL files that auto-apply on startup") `README.md:3,16-18`.

---

## B6. Tool factory context (how privileged tools get services/DB)

**Factory signature pattern (3 examples):**

1. `create_self_alarm_tools(service, *, source_repo=None, instance_repository=None, project_repository=None, current_instance_id="", agent_id="", agent_tag=None)` — `self_alarm.py:409-418`.
2. `create_board_tools(board_service, ...)` — `daemon/tools/board_tools.py:193-205` (service injected from `InstanceManager.board_service`, docstring `:203-205`; all four tools delegate to `board_service.*` — `:276, :335, :392, :451`).
3. `create_instance_ops_tools(...)` — `daemon/tools/instance_ops.py:398`; the audit ledger is resolved lazily per-call via `_audit_repo_for(manager)` `:326-333`: `engine = getattr(manager, "engine", None)` → `InstanceOpsAuditRepository(engine)` (call sites `:511, :902, :1055, :1210, :1460, :1610`).

**Manager wiring seam**: every factory has an `*_if_available(manager, current_instance_id, agent_id, agent_tag)` wrapper in `daemon/tools/instance.py` that reads manager attributes with `getattr(..., None)` and returns `[]` when the service isn't mounted (test-double safety) — `create_self_alarm_tools_if_available` at `instance.py:2381-2418` (reads `manager.scheduling_service`, `_source_repository`, `_instance_repository`, `_project_repository`; three-step registration seam doctrine `:2402-2405`). The mcp_rpc gateway gets its audit sink wired on the manager itself: `manager.py:1461`.

**Construction order**: `create_instance_tools` (`instance.py:2659`) calls the `*_if_available` factories and `tools.extend(...)`s the results **before** `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` — self-alarm extend at `instance.py:6044-6047`; the ordering pins are `test_self_alarm_registration.py:153` and `test_instance_ops_registration.py:160`; "decorator-only registration is SILENTLY invisible" comments at `instance.py:6093`, `:6139`.

**DB session inside a tool**: tools NEVER open sessions; they call services/repositories that own them — BoardService methods per call; Tier-1 audit receives the CALLER's session (`repository.py:110-152`); Tier-2 opens its own engine session; `_audit_repo_for` builds the repo from `manager.engine` (`instance_ops.py:326-333`).

**Async declaration**: `async def` inside the factory, `@register_tool_category(...)` above `@tool(args_schema=...)` (`self_alarm.py:491-493`); error contract = dict on success / `"ERROR: ..."` string on failure, never raise (`self_alarm.py:9-17`, `:551-557`). A `notify_user` tool plugs into this exact seam: new `daemon/tools/notify_user.py` + (optional pure split) `daemon/services/notify_user_service.py` (the P2-D2 split precedent: `.agents/shared/planning/ari-tools/decisions.md:173`), one factory + one extend + the 6 registry surfaces + pins.

---

## B7. Rate limit / counter primitives

| Primitive | Kind | Persistence | Cite |
|---|---|---|---|
| `VerifyRateLimiter` | token bucket (capacity + refill_per_minute) | in-memory, per-app `app.state.verify_rate_limiter` → **resets on restart** | `daemon/services/view_passcode_service.py:1912-1950` (refill math `:1883-1903`); mounted `daemon/api.py:1909-1920`; config `daemon/config.py:3140-3163` (env `ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_CAPACITY` / `_REFILL_PER_MINUTE` at `:3140-3141`) |
| `BlueprintRateLimiter` | sliding-window counter + circuit breaker, LRU-bounded (`_MAX_STATE_ENTRIES = 1000`), atomic `reserve()` | **"In-process only — no persistence. State resets on daemon restart"** (its own docstring) | `daemon/services/blueprint_rate_limiter.py:1-22` (persistence `:6-8`, LRU cap `:52-54`, atomic reserve `:19-21`, `:89+`); constructed `daemon/manager.py:2077`, injected `:2596` |
| Dispatcher per-user send locks | ordering (not limiting): `OrderedDict[external_user_id → asyncio.Lock]`, `MAX_SEND_LOCKS = 10000` LRU | in-memory | `daemon/sources/dispatcher.py:430-452`, `_get_send_lock :810-834`, used `:628-629`, `:767-768` |
| Wedge manual-trigger cap | 2-per-minute in-memory check → `manual_rate_limited` | in-memory | `daemon/routers/wedge_capture.py:33`, `:310` |
| Source-type send caps | static tuples (e.g. Telegram 30/30) | constants (enforcement downstream) | `daemon/constants.py:147-149` |
| `snapshot_usage_counters` table | persisted usage counters | **DB-persisted** | migration `daemon/migrations/versions/20261025.../20260925_233000_create_snapshot_usage_counters.sql` |
| Terminate budget counter | N-per-window **derived from the audit ledger** (COUNT query) | **DB-persisted** | `daemon/repositories/instance_ops/repository.py:333-341` (cutoff query) |
| `instance_metadata` / `set_metadata` | per-instance KV on the instance row (Tier-1 `session=` capable) | DB-persisted | two-tier declaration `20261021_120000...sql:31-36` |

**Where N-per-turn / M-per-hour would live**: per-turn (N) is naturally call-scoped in-memory on a per-instance limiter object mounted on the manager (BlueprintRateLimiter pattern — LRU-bounded, lock-guarded, restart-reset documented as acceptable); M-per-hour durable counting has a working precedent: COUNT over the audit ledger within a time cutoff (`repository.py:333-341`), which for `notify_user` doubles as the audit requirement — no separate counter store needed. **What a daemon restart resets today**: everything in-memory — `BlueprintRateLimiter` (self-declared, `blueprint_rate_limiter.py:6-8`), `VerifyRateLimiter` (`app.state`, `api.py:1909-1920`), dispatcher send locks (`dispatcher.py:451`), wedge manual check (`wedge_capture.py:310`); DB tables (`snapshot_usage_counters`, audit ledgers, `instance_metadata`) survive restarts.

---

## Cross-cutting summary for the planner

- The single most complete ari-only privileged-tool blueprint is `self_alarm` (B1) + its registration test (`test_self_alarm_registration.py`); the newest precedent (wedge-capture, entry #16) is the one whose count pins already read `== 16` (B2).
- Registration cost of a new privileged category = 4 registry edits + 9 pin files + 1 factory/extend + 1 meta.json entry + 1 body-gate constant + 1 registration test + 1 tools_note.md section — all in ONE commit (SAME-PR rule).
- For the audit ledger, `mcp_gateway_audit` shape + Tier-2 write-ahead from `instance_ops_audit` + the secrets additive-ALTER pattern covers every requested field; latency is a new column (no precedent), and durable rate counting can ride the same ledger (B4/B7).
- Migration lane: timestamped `.sql` with UP/DOWN, `IF NOT EXISTS` everywhere, SQLite-runner-only, model = canonical for PG, brand-new tables skip `_ensure_postgres_columns` (B5).
- Doctrine collision to author carefully: `tools_note.md:758-762` currently promises "the board, never a ping" — a voice notification tool directly extends that promise surface (B3).
