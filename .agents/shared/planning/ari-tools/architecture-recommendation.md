# Architecture Recommendation — commission `ari-tools`

Date: 2026-10-19
Author: **Architect** (controller) — adjudication + enrichment of the planner's 8-document design set
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`)
Planning commit analysed: `976cf0942`
Status: **ADJUDICATED — amendments required before reviewer**

Instance IDs (evidence base):
- `e18789c3-22bb-4ed1-b52b-a6919b103d96` — `security-design` — C1 privilege model + timing
- `e68fb980-61ee-4d91-a015-78b5198f2d91` — `data-flow-design` — schema / persistence / query paths
- `03ce3f9d-af1d-4ac2-b5d2-ba257b29bee7` — `system-decomposition` — registration + integration contracts
- `3a343ed9-fe18-4708-bbb4-0361600d3df0` — `resilience-design` — Phase 2 failure semantics
- `fd2bc882-d2ee-4dac-8ddc-dc9b82808711` — `structural-design` — Phase 3 route/gate/render structure

> **What this document is.** An adjudication of every decision the planner escalated (§C), plus
> correctness amendments derived from the actual codebase. Where this document *rules*, it supersedes
> the phase plan. Where it *amends*, the phase plan is correct in intent and needs the stated edit.
> Binding user decisions **U1–U5** and leader pre-rulings **L1–L6** are untouched — nothing here re-opens
> them. No item below required escalation to the user.

---

## 0. Verdict summary

| # | Question | Verdict |
|---|---|---|
| **R1** | C1 — privileged vs non-privileged `board`, and *when* | 🟢 **RULED: privileged from Phase 1's FIRST commit.** Leader's lean upheld; planner's "flip in Phase 3" superseded. Marginal cost is 5 file-touches and **zero** extra lines. |
| **R1b** | `self-alarm` privilege (not escalated by the planner) | 🔴 **OVERTURNED: must also be privileged.** Non-privileged hands it to `watcher`, which can then inject text into a future Ari turn. |
| **R2** | Schema soundness | 🟡 **SOUND with 6 required corrections** — one is a live correctness bug (`due_before` normalization). |
| **R3** | Tool/service wiring | 🟡 **SOUND with 4 required corrections** — the "three steps" is a lower bound (**six** surfaces). |
| **R4** | Lateness-cap policy | 🟢 **RULED: default `None`, no cap in v1.** Evidence is stronger than the plan's: the capped path is a verified 5-second re-evaluation loop with **no** self-heal and **no** user-visible signal. |
| **R4b** | Chat bridge for fired alarms | 🟢 **RULED: no new infra in v1.** No structural path exists. v1 contract must **name the board** as the user-visible surface. |
| **R5** | Phase 3 flagship | 🔴 **TWO BLOCKING DEFECTS** — a relative `reports` path silently kills `view_write`, and a template-engine choice 500s every board GET. |
| **R6** | §C5 ledger | **13 RESOLVED / 2 DEFERRED-to-v2 / 0 ESCALATED** (15 themes, 18 source questions). |
| **R7** | Risk table + 20-file map | 🟡 **3 rows materially wrong**, **15 missing risks**, and the file map **undercounts shared edits**. |

---

## 1. R1 — C1: the `board` category privilege model and its timing

### 1.1 The mechanism is confirmed, and the planner's premise was wrong twice

**Confirmed:** a non-privileged category **cannot** enforce a narrow audience (L1 as it then read — "ari + leader"; **⚠️ SUPERSEDED 2026-10-20 by [decisions.md](decisions.md) §A-RETRACTED **RET-1** — the board categories are ari-ONLY**).

| Behaviour | Evidence |
|---|---|
| Empty allow + empty deny ⇒ everything | `daemon/tools/instance.py:401-406` — `if allow_empty and deny_empty: return None` |
| `None` ⇒ all-minus-privileged | `instance.py:6337-6340` |
| The strip removes **only** privileged | `instance.py:6243-6271` — `getattr(t, "_tool_category", None) not in PRIVILEGED_TOOL_CATEGORIES` |
| No `tools` key at all ⇒ same default-open path | `instance.py:6300-6304` |
| A **literal tool name** in allow bypasses the category gate | `instance.py:471-478` |

**Correction 1 — the blast radius is not what the plan says.** `worker` (21 allow entries), `tester` (22), `developer` (13), `designer` (17) all carry **non-empty** allow lists ⇒ a non-privileged `board` would have reached **none** of them (`instance.py:471-478`). Phase-1 R2/Q1 is factually wrong at this HEAD.

**Correction 2 — the real exposure is worse in kind, narrower in count.** Independently re-verified by the architect: **exactly one** agent carries an empty allow list —

```
EMPTY-ALLOW   agents/watcher/meta.json
```

`agents/watcher/meta.json` self-describes as *"Security-focused tool call evaluator — intercepts every tool call from a watched instance before execution"*. It is the agent whose context is **attacker-influenced**, and under a non-privileged category it would hold **write authority over the global board**. The second population is unbounded: `agent_create` writes a `meta.json` with **no `tools` key** (`daemon/tools/agent_mother.py:282-295` ⇒ `instance.py:6300-6304`), and custom agents with an empty allow/deny triple normalise to `tools = None` (`daemon/repositories/custom_agent_configs/repository.py:193-198`).

**Correction 3 — the "five-pin ceremony" is not a meaningful cost.** All five pins are **exact-set equality**, and there is **no `len()` assertion anywhere** (grep: zero hits):

| Pin | Line | Shape |
|---|---|---|
| `tests/unit/tools/test_upgrade_registration.py` | `:142-166` | exact-set |
| `tests/unit/tools/test_attestation_registration.py` | `:186-208` | exact-set |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | `:324-341` | exact-set |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` | `:823-858` | exact-set |
| `tests/unit/tools/test_secrets_registration.py` | `:218-232` | exact-set |

Each pin costs **one inserted literal line**. No count pin, no byte-hash pin, no frozen-discovery coupling (that pin is over tool **names**, `test_frozen_tool_name_discovery.py:223-242` — privilege-independent). The MCP privilege stamp is inert without a matching prefix (`daemon/mcp/tool_adapter.py:189-196`).

### 1.2 Five-axis comparison

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **A — privileged from Phase 1's first commit** | **Low.** 5 one-line insertions in 5 test files; no new module, no new file. | **Good.** Opt-in stays correct as the agent population grows; mother-created and UI-created agents inherit nothing. | **Good.** Code and stated policy agree from commit 1; each commit carries its own pin update ⇒ no red window between phases. | **Low.** Residual = forgetting one of the five in a commit — identical in both options. | **THREE pin passes** (see the 2026-10-19 erratum at §7.1): 5 file-touch events + 5 lines in P1 (`board`), + 5 in P2 (`self-alarm`), + 10 in P3 (`view-write`, `board-link`) = **15 file-touch events / 20 inserted lines across the commission**, each pass in its own phase's commit. | ✅ **RECOMMENDED** |
| **B — non-privileged in P1, flip in P3** | Low (one edit, later) | **Poor during the window.** Every new agent with no `tools` key inherits board write authority; `watcher` inherits it. | **Poor.** A policy/mechanism divergence is carried across a whole phase and must be remembered by every reviewer. | **Medium–High.** The window contains the untrusted-input evaluator. P1-R6's "Low" was assessed on a false premise. | Saves 5 file-touch events. | ❌ Superseded |
| **A′ — non-privileged + body gate only** | Lowest | Same as B for visibility; the tool still appears in the model's schema and `tool_help`. | Medium — relies on per-body discipline at 4 call sites; a later 5th tool without the gate is a hole. | Medium — not structural. | Zero pins. | ❌ Rejected (not structural) |

### 1.3 Ruling

**`"board"` is PRIVILEGED, added in Phase 1's first commit.** The planner's arithmetic ("Phase 3 already pays the pin cost ⇒ zero marginal cost there") is arithmetically true but answers the wrong question: the pins are cheap in absolute terms, and deferring buys a 5-file saving in exchange for shipping a category whose stated audience the mechanism does not enforce. A silent widening is the failure mode this commission is most exposed to, because the board is global by design.

**Defence in depth is mandatory, not optional.** Because a **literal tool-name** allow entry bypasses the category gate (`instance.py:471-478`), the privileged category alone is insufficient. All four `board_*` bodies gate on `agent_id ∈ {"ari","leader"}` through **one** module-level helper — there is **no** existing two-agent-set precedent in `daemon/tools/` (the `blueprint` precedent is single-agent, `daemon/tools/blueprint.py:71-73`), so a shared `_is_board_authorized()` is required rather than four copies.

### 1.4 R1b — OVERTURN: `self-alarm` must also be privileged

Not escalated by the planner, but the same mechanism applies and the consequence is worse.

`phase2-plan.md:23` registers the category as **non-privileged**, relying on the allow-list plus a runtime `agent_id != "ari"` gate. Under a non-privileged category:

1. `watcher` (empty allow, re-verified above) **resolves the tool** — it appears in its schema and `tool_help`;
2. `watcher` can therefore **arm an alarm**;
3. an alarm spawns a **fresh Ari instance** (`instance_mode = "new_instance"`, P2-D6) whose message body carries the caller's action text **verbatim** (P2-D7);
4. that Ari instance holds Ari's full allow list (`bash`, `system_upgrade`, `secrets`, `cu`, `settings`, `view-views`, …).

⇒ **watcher → `self_alarm` → a future Ari turn executing attacker-influenced text.** That is a privilege-escalation chain, not merely a visibility leak. L2 ("ari only") therefore requires the same structural enforcement as L1.

**Ruling: `"self-alarm"` is PRIVILEGED from Phase 2's first commit.** The runtime gate stays (defence in depth, and it closes the literal-tool-name bypass). Required pin: one inserted line × 5 files. The privilege set trajectory is therefore **8 → 9 (P1: `board`) → 10 (P2: `self-alarm`) → 12 (P3: `view-write`, `board-link`)**.

The registration test must prove the property directly: **`resolve_tool_filter` yields no `self_alarm` for a non-ari agent with an EMPTY allow list** — not merely that only one `meta.json` names the category.

### 1.5 Consequence for Phase 3's D13 justification

P3-D13 keeps `board-link` in its own category for three reasons; the second ("phase 1's `board` is non-privileged ⇒ default-granted") **dissolves** under this ruling. The decision survives on the two enduring reasons: (a) `view_link` cannot mint the reserved name; (b) **audience separation** — ~~ari+leader may *mint the link*, while the data tools are what the page reads~~ (**⚠️ SUPERSEDED 2026-10-20 by [decisions.md](decisions.md) §A-RETRACTED **RET-1** — the minting audience is ari-ONLY; the category split survives structurally**). Keeping the split also preserves the phase-merge hygiene that D13 cites. **Ruling: keep `board-link` separate; amend the stated rationale.**

---

## 2. R2 — Schema soundness

### 2.1 Confirmed

| Item | Verdict | Evidence |
|---|---|---|
| Junction table for tags (not PG `TEXT[]`) | ✅ **CONFIRMED** | `project_tags` precedent: `daemon/repositories/project/models.py:146-158` (composite PK, no reverse index); filter query `repository.py:366-390`; upsert `:543-575`. **Zero** `TEXT[]`/`ARRAY` usage in the ORM. `@>`/`&&`/`= ANY` have no SQLite equivalent ⇒ two dialect branches. Junction gives one SQL on both drivers. |
| `linked_job_ids` as embedded `JSONBType` | ✅ **CONFIRMED** | `daemon/repositories/infra/types.py:35-71` (`JSONB` on PG, `JSON` elsewhere); exact precedent `daemon/repositories/snapshot/models.py:194-197` + DDL `20260925_194500_create_snapshot_tables.sql:38`. |
| One migration file for three tables | ✅ **CONFIRMED** | Multi-table files are established (19 of 105): `20261017_000001_create_secrets_tables.sql` (2 tables), `20261017_193816_create_live_view_passcode_tables.sql` (3), `20260710_000001_create_skill_tables.sql` (6). One ledger row (`version` = filename prefix, `runner.py:88-91`). |
| `wi-<uuid8>` collision fails loudly | ✅ **CONFIRMED** | Duplicate PK raises `IntegrityError`; no silent overwrite. Generator shape matches `todo_manager.py:1279-1290`. |
| Additive / `rollback_safe` | ✅ **CONFIRMED** | 3 new tables + indexes, no ALTER, no data change; the old binary ignores them — same shape as `app_settings` / `resource_limit_policy`. |
| D13 (no `_ensure_postgres_columns`) | ✅ **CONFIRMED** for the initial creation | `create_all` creates the missing tables *and their model-declared indexes* on the first boot (`manager.py:750`). |

### 2.2 Required corrections

| # | Finding | Sev | Required action |
|---|---|---|---|
| **S-1** | **`due_before` is never normalized.** `due_at` is stored fixed-width UTC (P1-D8), but the filter parameter accepts any tz-aware ISO. Probe: `'2026-10-20T08:00:00+07:00' < '2026-10-20T01:55:40.123456+00:00'` evaluates **False** although 01:00Z < 01:55Z ⇒ **the filter silently inverts.** `phase1-plan.md:227` states no normalization rule while the `due_at` docstring advertises `+07:00`. | 🔴 | One `_to_utc_iso()` used by **both** the `due_at` write path and the `due_before` filter. A raw agent string must never reach SQL. |
| **S-2** | **"The agent retries with a fresh id" is impossible** — the agent never supplies the id (`phase1-plan.md:315`, `plan-overview.md:180`). | 🟡 | Service catches `IntegrityError`, confirms the **id PK** (not a bare `except`), retries **once**; second failure ⇒ generic `ERROR: board operation failed`. The agent never sees a collision. |
| **S-3** | **"No down-migration" violates repo convention.** `rollback_migration` hard-requires a DOWN section (`runner.py:645-646`); every existing file has `-- UP`/`-- DOWN`. | 🟡 | Include `-- DOWN` (DROP INDEX, then child tables before parent). Never auto-executed. No inline `;` in comments (`runner.py:548-557`). |
| **S-4** | **`Field(foreign_key=…)` cannot express `ON DELETE SET NULL`** (emits no `ondelete`) ⇒ D14 is unimplementable as written. Also bare `str` emits **VARCHAR** on PG, not TEXT. | 🟡 | `project_id`: `sa_column=Column(Text, ForeignKey("projects.project_id", ondelete="SET NULL"), nullable=True)` — indexed by `idx_board_items_project_id` per the binding six-index set (§2.3), NOT a bare `index=True`. Every string column: `sa_column=Column(Text, …)` (`daemon/models/app_settings.py:66-72` precedent). |
| **S-5** | **`create_all` creates indexes only for tables it creates** (SQLAlchemy 2.0.48, `sqlalchemy/sql/ddl.py:962-966` + `:1020-1022`; probe: a dropped index is **not** recreated). ⇒ an index declared **only** in the `.sql` is created on SQLite by the runner but **never on PG** (the PG runner is a no-op). | 🟡 | Registry parity test must compare **names AND column lists**; add a migration-smoke test applying the `.sql` verbatim to a temp SQLite DB. Corollary: **any later index ADD** needs a new `.sql` **and** an `_ensure_postgres_columns` entry. |
| **S-6** | **The migration-runner direction is stated backwards** in the plan's §5.4 point 2 ("SQLite via the .sql file"). The runner is **SQLite-only** (`runner.py:721-726`); `create_all` runs **first on both** drivers. ⇒ the **model is the source of truth on both**; the `.sql` is the persistent record + SQLite-only index fallback. | 🟡 | Rewrite §5.4 point 2. |

### 2.3 Index set (replaces `phase1-plan.md:112`)

| Index | Columns | Serves |
|---|---|---|
| `idx_board_items_status_updated` | `(status, updated_at)` | status filter **and** the ordering when status is pinned — subsumes a single-column status index |
| `idx_board_items_updated_at` | `(updated_at)` | default `ORDER BY updated_at DESC` + LIMIT (**missing from the plan**) |
| `idx_board_items_project_id` | `(project_id)` | project filter |
| `idx_board_items_due_at` | `(due_at)` | `due_before` range (NULLs excluded by the predicate) |
| `idx_board_item_tags_tag_item` | `(tag, item_id)` | **reverse** tag filter — the composite PK `(item_id, tag)` cannot serve `WHERE tag = ?` (the `project_tags` precedent full-scans; do not copy that) |
| `idx_board_notes_item_id_ts` | `(item_id, ts)` | per-item timeline |
| ~~`idx_board_items_created_at`~~ | — | **DROP** — no v1 query path; pure write overhead |

**Pin `list_items` `ORDER BY updated_at DESC`** (mirror `daemon/repositories/project/repository.py:362`) — the plan never states it, and the index choice depends on it.

### 2.4 Notes hydration — RESOLVES §C5 theme 12

**Ruling: batch in Phase 1.** Add `list_items(..., with_notes: bool = False)` + internal batch loaders for notes **and tags** (`WHERE item_id IN (…)` then group in Python; ≈15–20 lines). The **tool's item shape is unchanged** (no `notes[]` in `board_list`), so the payload and truncation behaviour are untouched.

Cost: ≤3 queries per board render, versus 1+50 = 51 (worst case 201 at the tool's own ceiling). Phase 3 reads the **one** `list_items(..., with_notes=True)` call — a row with a MISSING `notes` key means **ZERO notes**; there is **no** per-row `get_item` fallback and **no** "notes unavailable" state (deviation **P3-DEV-2**). Do **not** copy the `project` repository's per-row tag hydration (`repository.py:87-89`).

### 2.5 Append-only notes — strengthen the proof

"No update/delete method" is necessary but **not sufficient**: a fetched `BoardNote` in a session that later commits lets SQLAlchemy dirty-tracking emit an UPDATE.

- **(a)** Keep the schema guard (no `updated_at`/`deleted_at`/`revision`) — the schema cannot express an edit. ✅
- **(b)** Add a **SQL-capture guard** test (`before_cursor_execute` listener) asserting the only statement touching `board_notes` across create/update cycles is `INSERT`/`SELECT`. Primary. The static source scan stays as a cheap secondary.
- **(c)** The service must never load a `BoardNote` into a session it later commits against.

**FK `ON DELETE` ruling:** both child FKs are **NO ACTION** (no `ondelete=`). This makes the database **refuse** a hard item delete, enforcing the soft-delete-only position structurally (the `SecretAuditEvent.secret_id` precedent, `daemon/repositories/secrets/models.py:289-291`). It does not conflict with soft delete (an UPDATE, no row removed).

**Correction to R19:** the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on every connection (`daemon/repositories/factory.py:170-177`; engine built through that factory, `daemon/manager.py:656-660`) ⇒ FK behaviour **does** hold on both drivers at runtime. It is inert only on a bare `create_engine("sqlite://")` — any unit-test FK assertion must enable the pragma or be marked PG-only.

### 2.6 Migration product hygiene

- Use a **distinct day prefix** (e.g. `20261020_060000_create_board_tables.sql`) — `20261019_19` is free *now*, but a same-hour concurrent commission would collide and abort a fresh SQLite boot (`runner.py:465-474`, no `IntegrityError` catch). Re-check at **write time and merge time**.
- Two duplicate prefixes already exist (`20260628_000002`, `20261018_000001`) — do not copy the defect.
- The ledger `checksum` is **write-only, never verified** (grep: no mismatch check; `README.md:472` documents a warning the code does not implement). **Do not claim drift detection.**
- Stage-time: a new migration makes the delta **non-empty** vs v0.25.4 ⇒ the stage gate will demand an explicit Option-Y adjudication with the semantic reasoning recorded verbatim. Prepare that text now.

---

## 3. R3 — Tool / service wiring

### 3.1 The "three steps" is a lower bound — there are **six** surfaces

| # | Surface | Miss-consequence | Evidence |
|---|---|---|---|
| 1 | `@register_tool_category("cat")` **ABOVE** `@tool` | falls into a `name.split('_')[0]` bucket; **privileged category never default-denied** | `_tool_registry.py:342-367`, `:655-657`; `instance.py:6270` |
| 2 | `CATEGORY_MODULES["cat"]` | boot warns "unknown category"; tool is **not AST-discovered** ⇒ frozen-universe drift | `_tool_registry.py:690`, `:488-490`; `registry.py:1368` |
| 3 | `KNOWN_TOOL_NAMES` | name vanishes in the PyInstaller binary (2026-08-20 prod incident, 32 warnings) | `_tool_registry.py:909`, `:589-616` |
| 4 | `DYNAMIC_TOOL_NAMES` | allow/deny token warns at boot | `_tool_registry.py:23`; `registry.py:1360` |
| 5 | `PRIVILEGED_TOOL_CATEGORIES` **+ its five exact-set pins** | category is default-granted to the empty-allow universe | `:239-338`; the 5 pin files |
| 6 | `tools.extend(factory(...))` in `create_instance_tools` | **decorator-only is silently invisible** — no warning, tool never appears | `instance.py:5698-6240` |

**Two constraints the plan does not state:**

- **The `CATEGORY_MODULES` value MUST be a `daemon.tools.*` path.** The AST scanner resolves it as `daemon/tools/<parts[2:]>.py` (`_tool_registry.py:504-506`); a `daemon.services.*` target is never discovered and the exact-equality drift pin fails. Phase 2's D2 split is legal **only** because the category points at `daemon.tools.self_alarm` while the pure helpers live in `daemon.services.self_alarm_service`.
- **The extend must precede** `create_help_tool` (`instance.py:6230`), `scan_tools_for_full_docs` (`:6235`) and `_apply_tool_filter` (`:6238`). A later extend is either **silently invisible** to the allow list or **bypasses the privileged strip**.

### 3.2 `self_alarm` dependency surface — five assumed-vs-actual mismatches

| # | Plan assumes | Reality | Impact |
|---|---|---|---|
| a | `one_shot_max_lateness_seconds` is a per-alarm knob | **Not a payload field.** Daemon-global config only (`daemon/config.py:2617-2623`), read by the adapter at fire time (`sources/adapters/scheduler.py:705-728`) | the wrapper cannot set it; the ≥30-min floor has **no engine backstop** |
| b | `project_id` is required by the payload | `ScheduleCreatePayload.project_id` is optional (`:161`); the **route** requires it (`scheduler.py:998`) | D11 is correct as wrapper policy; reframe the justification |
| c | reuse via a uniform `{when, message}` update | `update_schedule` branches on the **stored** recurrence: `once` re-anchors to the row's **stored** tz (`:941-972`); `daily`/`weekly` edit cron in place (`:973-981`); **`cron` raises** (`:982-986`) | one generic "recurrence cannot be changed" text is wrong; translate per family |
| d | the label namespace is the scheduler's | `get_source_config_by_name` filters **only** on `name` — **no `source_type` filter** (`daemon/repositories/source/repository.py:212-216`), while `list_schedules` **does** filter (`:877-878`) | a label can collide with an MCP-server/Slack source name ⇒ namespace labels (`self-alarm:`) or verify `source_type` |
| e | call the engine module | the module needs `configure()`; `_require_repo()` raises `RuntimeError` until `manager.scheduling_service` has run | **must** go through `manager.scheduling_service` at call time |

### 3.3 `view_write` / `board_link` seams — confirmed

| Seam | Verdict |
|---|---|
| Lifespan root injection | ✅ `daemon/api.py:1685-1700`; mutate `config.live_views.roots["reports"]` before construction. No `config.py`/`config.yaml` edit. `app.state.data_dir` stashed at `:493`. |
| `MAX_SERVED_BYTES` | ✅ public constant `daemon/services/live_views.py:166`; applied at resolve **and** read, public lane included. |
| `mint` / `refresh` | ✅ `view_passcode_service.py:1132` / `:1384`; **synchronous** ⇒ `await asyncio.to_thread(...)` mandatory in an async tool (`daemon/tools/scheduling.py:216-218`). |
| `is_well_formed_rel_path` | ✅ lives in the **service** (`live_views.py:251`); the existing tool already imports it from there ⇒ no router import. |
| `root_path` / `public_base` | 🟡 must be added (D17). `root_path` **must return `None` when `not self._config.enabled`** (mirror `resolve_for_instance:897-899`) or `view_write` publishes pages that can never be served. |
| Optional: `daemon/loader.py` warm-scan | 🟡 `:490-586` omits `scheduling`/`secrets`/`settings`/`live_views`; a cold boot can pin an empty category doc into the no-TTL prompt cache. **Recommended**, not required. |

### 3.4 Freeze the cross-phase interface (binding)

`board_service.list_items(status=…, project_id=…, tag=…, limit=…, with_notes=…)` and `get_item(item_id=…)` are the **only** tight seam in the commission. They must be frozen by name **and keyword name** in `board_service.py`'s docstring at the end of Phase 1. Today the adapter's call shape is an open question (P3-OQ-1), which leaves the one tight coupling unversioned.

---

## 4. R4 — Phase 2 semantics

### 4.1 RULING: lateness cap → default `None`, no cap in v1

The plan's characterisation is **correct**, and the code is worse than the plan says.

| Fact | Evidence |
|---|---|
| Default is `None` | `daemon/config.py:2617-2623` |
| Cap unset ⇒ fires late, immediately | `sources/adapters/scheduler.py:731` — `return now` |
| Cap set ⇒ `skipped` history row, **schedule untouched** | `:737-796` — status stays `running`, `enabled` stays True, `config['run_at']` unchanged; there is no `next_run_at` column to advance |
| **No self-heal** — re-evaluates the same past instant every iteration | `:559` — `await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)` where `SCHEDULER_ERROR_RETRY_S = 5.0` (`daemon/constants.py:376`), then `continue` |
| **No user-visible signal** | the only trace is a `skipped` row; the schedule still reads `running`, with `next_run_at = None` (a past-due one-shot, `:463-464`) |

**Ruling.** A cap converts a *late but real* delivery into a **silent, permanent, non-self-healing loss** — the exact failure class the tool exists to prevent. Keep `None`. The late path is self-announcing (the composed message carries the scheduled instant, P2-D7); the skip path announces nothing until a human inspects the list, and its only remedy is a manual `task_schedule_update`.

The honest counter-direction (a days-late alarm can trigger a wrong action) is handled by the **prompt contract**, not by a cap: the message states the scheduled instant and instructs the (fresh or, since 2026-10-20 **RET-2**, the CALLING) instance to compare it against its `time` tool and **re-verify before acting if late**. Make the threshold explicit rather than implicit.

Because the cap is **daemon-wide**, the wrapper cannot override it. It **must** read and echo it: `"lateness_cap_seconds": <int|null>` — so the agent knows whether a missed alarm would be delivered late or skipped forever.

### 4.2 RULING: chat bridge → no new infra in v1

`SchedulerAdapter.send` only logs and returns `True` (`sources/adapters/scheduler.py:435-457`), and the receiving instance is scheduler-owned with `external_user_id = source_id` and `force_new=True` (`:1063-1069`) ⇒ **no chat identity, no originating surface**. Paths to a chat surface, enumerated: the instance's own reply (dead-ends, logged only); a job dispatched to a chat-facing agent (the report climbs to **this** scheduler root and dead-ends); `job_inject` into a **RUNNING** chat-rooted job (the only push path — but the fired instance has no public instance→job resolver, so it cannot discover the id); or `bash` + curl (out-of-band, not a contract). In the common case — the requesting chat turn already completed — **there is no path at all**.

**Ruling: v1 adds no infrastructure. The board is the user-visible surface, and the contract must say so.**

- **Promises:** the alarm is delivered into a fresh Ari instance at the scheduled instant; that instance reads the board item, performs the action, and leaves evidence **on the board** (status/notes) and in any job it dispatches; the live board view shows the change.
- **Must NOT promise:** a ping in chat/Discord/Slack; a reminder in the conversation; an interruption.

The plan currently names **no** surface — that omission is precisely what invites an agent (and the user) to expect a ping.

### 4.3 Two HIGH-severity defects the plan does not have

**D-1 — reusing a `stopped` label silently disarms the alarm.** `update_schedule` preserves the previous `enabled` flag when `paused` is unset (`scheduling_service.py:1019-1023`); a fired row is `enabled=False` (`sources/registry.py:523`) ⇒ the rebuild is skipped (`:1046-1047`) ⇒ the row stays `enabled=False`, `status='stopped'`, and the boot filter skips it (`registry.py:291-310`). **The plan's D4 specifies exactly this call.** Reuse MUST pass `paused=False`, and the wrapper MUST assert `paused == false` / `status != "stopped"` in the response, falling back to a fresh label if the resume did not take.

**D-2 — a message-only re-arm of a fired alarm is swallowed.** Key = `scheduler:{source_id}:{run_at}` (`scheduler.py:1090-1094`, ONE_TIME only); a matching **non-terminal** job within 24 h collapses (`job_queue_service.py:1424-1481`). Re-arming a fired label with the **same** `when` rebuilds the adapter (fires again) but enqueues under the same key ⇒ collapse ⇒ **silently never delivered**. Under the ≥30-min floor an identical-instant re-arm is already impossible (the instant is past), so the plan's stated mitigation is a **no-op**; the reachable hole is the message-only edit. **Ruling: a fired/`stopped` label MUST be re-armed with a strictly later instant; a message-only re-arm is REFUSED.**

### 4.4 Other corrections

| # | Finding | Action |
|---|---|---|
| P2-1 | **No unique constraint on label** — two racing arms both survive and **both fire** (`scheduling_service.py:34-39`) | collision-check the `-2` candidate; after create, re-read and **report** duplicates; **never cancel the loser** (cancel burns the name and the lookup returns the first match) |
| P2-2 | **Default list is not status-filtered** (`list_schedules(status=None)` ⇒ running **and** stopped) and is capped at 100 ordered `created_at DESC` | tool doc must instruct `status='running'` to see armed alarms; note that armed rows eventually hide behind stopped ones |
| P2-3 | **`stopped` means *dispatched*, not *acted upon*** — the callback fires right after the enqueue (`scheduler.py:599-609`) | state it; the action can still be lost or doubled |
| P2-4 | **`tz_warning` erasure** — passing an explicit `timezone` short-circuits the engine's chain (`tz.py:323-327`) ⇒ the engine's UTC-fallback warning is suppressed | echo the **wrapper's own** warning |
| P2-5 | **Create-response `status` is racing** (`'stopped'` → `'starting'` → `'running'`, `repository.py:82` / `registry.py:611` / `:785`) | echo the actual status; treat `'starting'` as armed |
| P2-6 | **Rejection text is family-blind** — the bash-sleep hint is wrong advice for a recurring alarm | family-aware text; always print the computed next fire |
| P2-7 | **Idempotency cross-schedule worry is impossible** — the key contains `source_id` | remove the concern; keep the message-only hole (D-2) |
| P2-8 | A past `run_at` is accepted by the engine (`create_schedule` has no floor) and fires immediately | the ≥30-min floor exists **only** in the wrapper — say so |
| P2-9 | DST is safe by construction (shared `compute_next_cron_fire` / `anchor_local_to_utc`) | but the echoed first-fire is a **snapshot**; recurring fires are wall-clock-stable, offset-variable |
| P2-10 | A user-tz change between arm and fire does not move a one-shot, but **does** change a recurring alarm's effective offset (stored IANA zone is authoritative) | document |

### 4.5 `project_id` — confirmed, and it must run first

`project_id` is the **only** thing that selects the job-queue route (`scheduler.py:998`); without it the fire takes `_execute_immediate` — still delivered, but with **no JobItem, no idempotency key, no retry, no visibility**. It is always resolvable for a live instance (spawn-time normalisation to the system-default project, `daemon/tools/job_queue.py:1103-1108`). Resolve it and **error before any label lookup or create** (so no row and no burned name), and do **not** swallow the exception the way `blueprint.py:67-68` does.

---

## 5. R5 — Phase 3 flagship

### 5.1 Route order — confirmed, but the hazard is mis-described

The insertion point is correct (**between `routers/live_views.py:1742` and the catch-all decorator at `:1744`**), and first-match-wins is confirmed against the installed Starlette 0.52.1 / FastAPI 0.133.1 — including that **FastAPI adds no implicit HEAD** (so HEAD needs its own route, else it falls to the public HEAD catch-all and returns a JSON body on a HEAD request).

**But the plan's failure description is wrong.** A mis-ordered board GET would **not** "resolve `board` as a file" — `_public_resolve` rejects it at the **reserved-name mirror** first (`routers/live_views.py:1313-1316`; service mirror `services/live_views.py:911-913`). Same 404, different mechanism. Fix the loud comment or a future reader debugs the wrong thing.

**The route-order pin must be a route-table index assertion** (`app.routes` index of the board route < index of the public catch-all GET) **plus** the behavioural half (minted row ⇒ HTML interstitial, not JSON). A response-only pin is ambiguous: with no minted row the mis-ordered route also returns the uniform 404.

No fragility found: single `include_router` (`daemon/api.py:4437`), no prefix, no double mount, no `scope["path"]` rewrite; `_tag_routes_by_path_public` (`api.py:4458`) auto-tags the static `/views/public/board/index.html` template public-probe ⇒ daemon-side anonymous in both auth states; the SPA catch-all explicitly rejects `views` (`api.py:4495-4504`).

### 5.2 Reserved root name — confirmed posture

`RESERVED_ROOT_NAMES = frozenset({"public"})` (`services/live_views.py:141`); the validator is a **boot-time** `field_validator` (`config.py:3099-3100`) whose message names the root and suggests a rename (`:3142-3149`) — keep **hard refusal**, matching the `public` precedent. `view_link` already returns the reserved envelope (`tools/live_views.py:263-270`). The direct lane `/views/board/index.html` **404s by an existing guard** (`routers/live_views.py:1165-1167`) — **no new guard needed**.

**Amendment:** the message text *"the public passcode lane occupies this prefix"* becomes inaccurate once `board` is reserved ⇒ make it generic.

### 5.3 Gate reuse — the load-bearing accident is real, and now pinned

Confirmed: `_public_post` (`:1464-1741`, 278 lines) contains **no** `is_reserved_root_name`, **no** `has_root`, **no** `resolve_for_instance` — only passcode-service calls. That is what lets a reserved, unregistered root verify.

**The coupling risk is real:** two plausible future "hardenings" — adding `is_reserved_root_name` to `_public_post`, or requiring the POST to serve **registered** roots only (the GET already does) — would silently break the board. **Minimum pin set:** (i) the board POST verifies although the root is reserved **and** unregistered; (ii) a wrong code still strikes and locks on the 10th; (iii) a **source-scan pin** asserting `is_reserved_root_name` is absent from `_public_post`; (iv) the 303 `Location` and `Set-Cookie Path` are exactly `/views/public/board/index.html` (query-bearing Location, **queryless Path**).

### 5.4 Failure envelopes — no new oracle, with one rule

`_uniform_404()` (`:474-487`) is the same function for unknown root, known-root-wrong-file, **reserved root**, and no-minted-row ⇒ byte-identical. The **locked** case returns `200 + HTML` — exactly the asymmetry the file lane already has, therefore **not a new oracle**.

**Rule:** the board's "no row" case must return `_uniform_404()` **verbatim**. Any board-specific "unavailable" HTML would disclose both that the URL is special and that a row exists. Add a **byte-equality** assertion (body + headers) against a miss on a random path. Do **not** add a board-specific interstitial (OQ-3 (a) is right).

### 5.5 CSP — already expressible, use it alone

`page_headers(csp_nonce=…)` (`daemon/services/view_passcode_pages.py:68-89`) returns **exactly** the strict set the plan wants (`default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` + `nosniff` + `no-store` + `Referrer-Policy: no-referrer`). The nonce is per-request (`:60-63`, `routers/live_views.py:490-501`) and injected into both header and `<style nonce>` → fully compatible with server-render-per-GET.

Today's raw-file lane sends only `nosniff` + `private, max-age=60` (`:122-125`) with **no CSP** — the plan's split posture is correct, and no proxy conflict exists.

🔴 **Merge-order trap:** the board response must build headers from `page_headers(...)` **alone**. Merging `{**_HARDENING_HEADERS, **page_headers(...)}` with the wrong precedence lets `private, max-age=60` win over `no-store` — the tree already has this exact trap at `:1857`. **Pin `response.headers["cache-control"] == "no-store"`.**

### 5.6 🔴 Two blocking defects

**G1 — the injected `reports` root can be RELATIVE ⇒ `view_write` is silently dead.** `app.state.data_dir = Path(ENSEMBLE_DATA_DIR or DATA_DIR or "./data")` (`daemon/api.py:483-488`) — the **default is relative**. The plan's injection uses `str(Path(_data_dir) / "reports")` ⇒ `"data/reports"` ⇒ the filesystem resolver **refuses a non-absolute path** (`services/live_views.py:952-958`) ⇒ every published page 404s and `root_path("reports")` returns `None` ⇒ the tool reports the misleading *"reports storage is not configured"*. **Fix:** `path=str((Path(_data_dir) / "reports").resolve())`, plus a test with a relative `ENSEMBLE_DATA_DIR`.

**G2 — `str.format` on a template containing CSS braces ⇒ 500 on every board GET.** The board page needs a `<style>` block, and CSS contains literal `{`/`}`. Use **plain concatenation** (the `view_passcode_pages.py:117-141` precedent) or `string.Template`.

### 5.7 Write atomicity — the served artefact IS the file

There is no sidecar gate on `view_write` pages, so a plain open/write/close exposes a **truncated** page to a concurrent reader and leaves a **permanently servable corrupt file** after a crash. The plan's `mkstemp(dir=parent)` + `os.replace` is **correct**; the same-directory requirement is load-bearing (a `/tmp` temp ⇒ `EXDEV`).

Four additions:

1. **`flush()` + `os.fsync(fd)` BEFORE `os.replace`** — `os.replace` is atomic w.r.t. the namespace, **not** durability; without fsync a crash can leave a zero-length file.
2. **Unlink the temp file in a `finally`** on any failure.
3. **Containment must resolve both sides** — compare against `Path(entry.path).resolve()`, not the unresolved `root_path()` value (mirror `_resolve_under_root:1157-1170`).
4. **Directories mode `0o700`**; file `0o600`. The default `0o755` makes the reports tree world-readable on a multi-user host.

The 32 MiB cap must be checked **before** the write (the plan does this) — never rely on the serve-path `stat()` check, which is a 404 ("the page I just published is dead").

### 5.8 Render ceiling — there is no byte bound

With batched hydration the render is **≤3 queries**. Without it, **51** (worst case 201 at the tool's own ceiling). `MAX_SERVED_BYTES` applies **only** to files served from disk — a server-rendered response is unbounded, while `body_md` and `board_notes.text` are unconstrained TEXT.

**Ruling:** 50 rendered rows is an acceptable *display* ceiling, but `list_items` has **no pagination cursor** and returns `count` ⇒ render **"showing N of M"** whenever `count > limit`, and a notice when the filter-option set caps at 500. Add a **2 MiB rendered-body ceiling** plus per-field caps (notes per item ≤ 20, note text ≤ 4 KiB, `body_md` ≤ 64 KiB), enforced **during assembly** (a post-hoc slice cuts mid-tag), truncating **with a visible notice** — never a 500, never a silent cut.

**Concurrency:** wrap the **whole render** (adapter fetch + assembly) in **one** `asyncio.to_thread`, not one per service call. SQLite is safe under `to_thread` because the engine sets `check_same_thread: False` (`daemon/repositories/factory.py:164`) — **pin that flag**; without it the board 500s on dev. Two concurrent GETs share no mutable state; a concurrent `board_link(refresh=True)` revokes outstanding grants by design (readers bounce to the interstitial) — state it.

### 5.9 `board_link`

- **Key shape confirmed:** the row key is the exact `(root_name, rel_path)` pair and the query string never participates (`view_passcode_service.py:637-650`; `_request_public_url_path:811-830`) ⇒ one row `(board, "index.html")`, filters are query params, a holder sees the whole board. Stated plainly in D11 — correct.
- **Overwrite does not bump the generation** is implementable: `mint` is idempotent (`:1166-1180`); `refresh` (`:1384-1447`) is the **only** bump and also clears the lock, so `refresh=True` remains the sole lockout recovery. ✅
- 🟡 **Missing `enabled` check:** with live-views disabled the board GET is a **permanent 404** (`routers/live_views.py:1320-1323`) even though the row exists ⇒ `board_link` must return the `view_link` disabled envelope (`tools/live_views.py:243-244`), not a dead URL.
- 🟡 **`board_link` must compose both URL lines itself** — `build_public_url` returns `None` for a reserved **and** for an unregistered root (`services/live_views.py:1275-1279`) ⇒ use `public_base()` + `PUBLIC_NAMESPACE` / `+ "/views/"`. Note in the docstring that the base may be the Host-captured value; set `live_views.external_base_url` for a stable pasted link.

### 5.10 Code split + escaping

The split is right **provided** `board_view_pages.py` consumes only the phase-3 `BoardItemView` dataclass and **never** imports `board_service`. One module-level `_esc(v) = html.escape(str(v), quote=True)` (with `None → ""`) at **every** interpolation site.

Escaping contract — every site below is escaped; **URL-context values are `quote()`d FIRST, then `_esc()`d**:

| Site | Context | Note |
|---|---|---|
| item title / `body_md` / note text / note author | text node (`<pre>` for body) | `_esc` — escapes `</pre>` |
| note/store timestamps, status, priority, project id, counts, generated_at | text node | `_esc` (coerce ints first) |
| tag / project id | text **and** `<option value>` | `_esc`; `quote()` in a URL context |
| item id | text (+ any `href`/`id`) | `_esc` **and** validate `^wi-[0-9a-f]{8}$`, drop non-conforming |
| **active-filter echo** | `<input value>` / `<option selected>` | **`quote()` then `_esc`** — HTML-escaping alone yields a *different* value than the user typed (`&lt;` ≠ `<`) ⇒ the filter never matches; an unescaped `&` splits the query |

---

## 6. R6 — §C5 ledger adjudication

**15 themes / 18 source questions → 13 RESOLVED, 2 DEFERRED-to-v2, 0 ESCALATED.**

| # | Theme | Source Qs | Status | Ruling |
|---|---|---|---|---|
| 1 | Privilege semantics of `board` | P1-Q1 + P3-OQ-2 | ✅ **RESOLVED** | Privileged from Phase 1 (§1.3); `board-link` stays separate on audience grounds (§1.5) |
| 2 | Author provenance | P1-Q5 | ✅ **RESOLVED** | Service-stamped from the tool closure; an agent cannot spoof it. Keep. |
| 3 | List / render ceiling + pagination | P1-Q3 + P3-OQ-5 | ✅ **RESOLVED** | `limit` + truncation metadata in v1; **render "showing N of M" now** (one template line, not a deferral); cursor → v2 (§5.8) |
| 4 | Item mutation surface — clear-flags, deletion | P1-Q2 + P1-Q4 | ⏸ **DEFERRED-to-v2** | Keep the bound `board_update` shape; `clear_fields` is a v1.1 amendment; any delete must be **soft** — and the child FKs now enforce that at the schema level (§2.5) |
| 5 | REST twin in Phase 1 | P1-Q6 | ✅ **RESOLVED** | No. Tools-only; Phase 3 calls the service directly. |
| 6 | Chat bridge for the alarm's reply | P2-OQ-1 | ✅ **RESOLVED** | No new infra; the board is the user-visible surface; contract promises/not-promises stated (§4.2) |
| 7 | Recurring arm-now-imminent | P2-OQ-2 | ✅ **RESOLVED** | Confirm reject; **family-aware** error text with the computed next fire (§4.4 P2-6) |
| 8 | Interval recurrence / engine change | P2-OQ-3 | ⏸ **DEFERRED-to-v2** | Reject with the `cron */60 * * * *` hint; a real engine change is a separate commission |
| 9 | Lateness-cap deployment policy | P2-OQ-4 | ✅ **RESOLVED** | Default `None`; no cap in v1; document both behaviours + the skip state's signature; echo `lateness_cap_seconds` (§4.1) |
| 10 | Auto-arm v2 confirmation | P2-OQ-5 | ✅ **RESOLVED** | Confirm the deferral (five blockers stand); v1 = prompt convention |
| 11 | Alarm scope semantics | P2-OQ-6 | ✅ **RESOLVED** | Ari-for-ari only. "Arm for others" is a different tool. |
| 12 | Notes hydration N+1 / list projection | P3-OQ-1 | ✅ **RESOLVED** | Batch in Phase 1 (`with_notes=True`); item shape unchanged; Phase 3 reads the ONE `with_notes=True` call — the per-row `get_item` fallback is GONE (deviation **P3-DEV-2**; §2.4) |
| 13 | Interstitial copy | P3-OQ-3 | ✅ **RESOLVED** | Accept the shared interstitial; **no** board-specific template (gate-contract risk for zero gain) |
| 14 | Board layout | P3-OQ-4 | ✅ **RESOLVED** | Flat filtered table with `<details>` per row; rendering-only, no schema/service impact |
| 15 | Headline link — public vs direct lane | P3-OQ-6 | ✅ **RESOLVED** | Public URL is the headline; the direct lane 404s **by design** and is labelled informational |

---

## 7. R7 — Cross-phase coupling and risk-table sanity

### 7.1 Coupling map — three corrections

1. **"Phase 2 depends on Phase 1 only through the id format" — still true**, and now reinforced: Phase 2 never imports board code (P2-D12), and Phase 3's batched-hydration option does not create a new coupling because the tool shape is unchanged.
2. **Phase 1 ↔ Phase 3 gains one binding artefact**: the frozen `list_items`/`get_item` signature (§3.4). This is a *schedule* coupling, not a code one: Phase 3 must not start until Phase 1 freezes it.
3. **The privileged set is now a cross-phase coupling** — P1 adds `board`, P2 adds `self-alarm`, P3 adds `view-write` + `board-link`, each with its own 5-pin pass. This is **four passes over the same five files**, not one. Each pass is 1 line per file, and each commit is self-consistent, so there is **no red window** — but the plan's statement that "phase 3 pays the pin cost once" is no longer true and must be amended.

> **ERRATUM — 2026-10-19 (reviewer re-gate):** the "**four passes**" above counted **categories**, not **pass-events** — the binding count is **THREE pin passes** (P1, P2, P3), with the privileged set moving **8 → 9 → 10 → 12**; phase 3's pass inserts **two** literals because it adds **two** categories. The **phase docs govern** the operative form ([plan-overview.md §3/§4/§6](plan-overview.md), [phase1-plan.md](phase1-plan.md) §3.2.1, [phase2-plan.md](phase2-plan.md), [phase3-plan.md](phase3-plan.md) §2.5); this architect paragraph is retained as the original adjudication and its "four" is superseded.

### 7.2 Existing risk rows that are materially wrong

| Row | Problem | Correction |
|---|---|---|
| **R2** | Describes an unresolved choice | **RESOLVED** — privileged from Phase 1. Keep the row as a decision record, drop it from the live risk set. |
| **R3** | Understates the capped path | A capped miss is a **5-second re-evaluation loop with no self-heal and no user-visible signal** (`scheduler.py:559`; `SCHEDULER_ERROR_RETRY_S = 5.0`). |
| **R18** | "the agent retries with a fresh id" | The agent never supplies the id — the **service** retries once (§2.2 S-2). |
| **R19** | "SQLite ignores FKs" | The daemon sets `PRAGMA foreign_keys=ON` on every connection (`factory.py:170-177`) ⇒ FK behaviour holds on **both** drivers at runtime; inert only on a bare unit-test engine. |
| **R11 / R12** | Mixed severity model | R12's mitigation ("allow-list **and** runtime gate") is now backed by a **privileged category** — three layers, not two. |
| **R15** | "three privileged categories, one pin pass" | Now **four categories across three phases ⇒ three pin passes**. |
| **R28** | N+1 render | Resolved by batched hydration — the per-row fallback path is **GONE** (deviation **P3-DEV-2**), so no fallback-path cost remains. |

### 7.3 Missing risks (add to the consolidated table)

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R33 | **`stopped`-label reuse without `paused=False` silently disarms the alarm** (§4.3 D-1) | 🔴 | Pass `paused=False`; assert the response; fall back to a fresh label |
| R34 | **Message-only re-arm collapses** into the prior job under `scheduler:{source_id}:{run_at}` (§4.3 D-2) | 🔴 | Refuse a re-arm that is not strictly later; state it in the doc |
| R35 | **`self-alarm` non-privileged ⇒ `watcher` arms alarms ⇒ Ari executes attacker-influenced text** (§1.4) | 🔴 | Privileged category + runtime gate |
| R36 | **Injected `reports` path can be relative ⇒ `view_write` silently dead** (§5.6 G1) | 🔴 | `.resolve()` + a relative-`ENSEMBLE_DATA_DIR` test |
| R37 | **`str.format` + CSS braces ⇒ 500 on every board GET** (§5.6 G2) | 🔴 | Concatenation or `string.Template` |
| R38 | **`Cache-Control` merge-order trap ⇒ `no-store` lost** (§5.5) | 🟡 | `page_headers` alone; pin the header |
| R39 | **Render output has no byte cap**; `body_md`/note text unbounded (§5.8) | 🟡 | 2 MiB body cap + per-field caps during assembly |
| R40 | **`board_link` hands out a URL when live-views is disabled** (§5.9) | 🟡 | Disabled envelope |
| R41 | **No `fsync` before `os.replace`** ⇒ zero-length page after a crash (§5.7) | 🟡 | flush + fsync + finally-unlink |
| R42 | **A future `has_root` check in `_public_post` silently breaks the board** (§5.3) | 🟡 | Four-pin regression set incl. a source scan |
| R43 | **Default schedule list hides armed alarms** — not status-filtered, 100-row cap (§4.4 P2-2) | 🟡 | Doc: `status='running'` |
| R44 | **Label namespace shared with non-scheduler sources** — no `source_type` filter (§3.2 d) | 🟡 | Namespace labels or verify `source_type` |
| R45 | **Create-response `status` is racing** (`'starting'`) (§4.4 P2-5) | 🟢 | Echo actual status; treat `'starting'` as armed |
| R46 | **`tz_warning` erasure** when passing an explicit zone (§4.4 P2-4) | 🟡 | Echo the wrapper's own warning |
| R47 | **Truncation is invisible** — >50 rows / >500 filter values (§5.8) | 🟡 | "showing N of M" + cap notice |

### 7.4 The 20-file map

**New files: 20 — correct.** (P1 8 · P2 5 · P3 7.)

**Shared edit files: the summary row undercounts.** `plan-overview.md:80` says "3 shared edit files"; the per-phase rows themselves list more. The true set is:

`daemon/manager.py` · `daemon/tools/_tool_registry.py` · `daemon/tools/instance.py` · `agents/ari/meta.json` · `agents/leader/meta.json` · **the 5 privileged-set pin files** · `daemon/api.py` (P3 lifespan) · `daemon/services/live_views.py` + `daemon/routers/live_views.py` (P3) · `config.yaml` (doc-only) · optional `daemon/loader.py` (warm-scan).

**Plus one file the map omits entirely:** `agents/ari/tools_note.md` (P2's prompt surface) — it appears in the phase-2 table but not in the overview summary.

**Stage-time note:** the commission produces a **non-empty migration delta** vs v0.25.4 ⇒ the release gate will require an explicit rollback-safety adjudication with the reasoning recorded verbatim (the standing rule from the v0.25.4 commission). That is expected and non-blocking, but the planner should draft the text rather than discover it at stage time.

---

## 8. Build order and sequencing — confirmed

1 → 2 → 3 stays. Three refinements:

- **Phase 1 must ship the privileged flip and the frozen `list_items`/`get_item` signature in its final commit**, since both are consumed by later phases.
- **Phase 2 owns no schema** — correct, and it must stay that way (D8's deferral is sound: auto-arming would leak label/burn semantics into the board service).
- **Phase 3 must not start before Phase 1's interface freeze** (§3.4) and must **reconcile against the landed phase-1 code** before merging (R9 stands).

---

## 9. Consolidated amendments for the planner

Grouped by owning document. **P0 = correctness (must land before reviewer). P1 = clarity/consistency.**

### `decisions.md`
- **A-01 (P0)** §C.1 → mark **RESOLVED**; replace the "ARCHITECT-TO-RULE" status with R1's ruling and the four corrections in §1.1.
- **A-02 (P0)** §C.5 theme 1 → RESOLVED; themes 4 and 8 → DEFERRED-to-v2; the remaining 12 → RESOLVED per §6.
- **A-03 (P1)** §C.2 → accept the local fixed-width helper **and** add the missing half: one `_to_utc_iso()` on **both** the `due_at` write path and the `due_before` filter (§2.2 S-1).
- **A-04 (P1)** §C.4 → amend the `board-link` rationale: the "phase-1 `board` is non-privileged" premise is gone; audience separation + reserved-name impossibility are the enduring reasons.
- **A-05 (P0)** Add §A2 row **L2a**: `self-alarm` is **privileged** (R1b).

### `phase1-plan.md`
- **A-06 (P0)** D6 → privileged (entry #9 of `PRIVILEGED_TOOL_CATEGORIES`), 5 pin insertions in the same commit, plus the `agent_id ∈ {"ari","leader"}` body gate via one shared helper.
- **A-07 (P0)** Q1 → RESOLVED, with the two evidence corrections (non-empty allow lists on worker/tester/developer/designer; the real inheritors are `watcher` and `tools`-less agents).
- **A-08 (P0)** §5.4 point 2 → rewrite: the runner is **SQLite-only**; `create_all` runs first on both drivers; the **model is the source of truth**; the `.sql` is the record + SQLite index fallback (§2.2 S-6).
- **A-09 (P0)** §4.1 model shapes → `Text` columns, explicit `ForeignKey(..., ondelete="SET NULL")`, explicit `JSONBType`, `board_notes.id` autoincrement (§2.2 S-4).
- **A-10 (P0)** index set → §2.3 verbatim; drop `created_at`, add `updated_at`, composite `(status, updated_at)`; pin `ORDER BY updated_at DESC`.
- **A-11 (P0)** §7.1 `due_before` → normalized by the same `_to_utc_iso()` (§2.2 S-1).
- **A-12 (P0)** R1/R18 → service-side retry-once contract (§2.2 S-2).
- **A-13 (P0)** §7.3 + R3 → child FKs `NO ACTION`; add the SQL-capture append-only guard; correct R19 (pragma is ON) (§2.5).
- **A-14 (P0)** migration → distinct day prefix, re-check at merge, `-- DOWN` section, no inline `;` in comments (§2.6).
- **A-15 (P0)** N7 → index parity compares **columns**, plus a migration-smoke test applying the `.sql` verbatim (§2.2 S-5).
- **A-16 (P1)** add the `create_all`-index corollary: a **later** index ADD needs both a `.sql` and an `_ensure_postgres_columns` entry.
- **A-17 (P1)** §5.2 → add `list_items(..., with_notes=False)` + batched notes/tags loaders; freeze `list_items`/`get_item` as the binding Phase-3 interface (§2.4, §3.4).

### `phase2-plan.md`
- **A-18 (P0)** line 23 + D10 → `self-alarm` is **privileged**; add `PRIVILEGED_TOOL_CATEGORIES` + the 5 pins to the file map; add the empty-allow-agent test (§1.4).
- **A-19 (P0)** D4 → reuse MUST pass `paused=False`; assert the response; fall back to a fresh label; refuse a message-only re-arm of a fired label (§4.3).
- **A-20 (P0)** D4 → collision-check the `-2` candidate; report duplicates after create; never cancel the loser (§4.4 P2-1).
- **A-21 (P0)** D5 → the default list is not status-filtered and caps at 100; document `status='running'`; `stopped` = *dispatched* (§4.4 P2-2/P2-3).
- **A-22 (P0)** D9 → family-aware rejection text with the computed next fire (§4.4 P2-6).
- **A-23 (P0)** tool doc → lateness semantics (both cap states + the skip signature) + `lateness_cap_seconds` echo (§4.1).
- **A-24 (P0)** tool doc → the chat-surface contract: promise the board, **never** promise a ping (§4.2).
- **A-25 (P0)** D11 → resolve `project_id` and error **before** any label lookup; do not swallow the exception (§4.5).
- **A-26 (P1)** echo the **wrapper's own** tz warning; echo the actual `status`, treating `'starting'` as armed (§4.4 P2-4/P2-5).
- **A-27 (P1)** add the label-namespace rule (`self-alarm:` prefix or `source_type` verification) (§3.2 d).
- **A-28 (P1)** registry contract → the `CATEGORY_MODULES` value must be a `daemon.tools.*` path (§3.1).
- **A-29 (P1)** risks → add R33, R34, R35, R43, R44 (and R46).

### `phase3-plan.md`
- **A-30 (P0)** §2.2 → `path=str((Path(_data_dir) / "reports").resolve())` + a relative-`ENSEMBLE_DATA_DIR` test (§5.6 G1).
- **A-31 (P0)** §3.2 step 7 → same-directory temp, flush + fsync before `os.replace`, finally-unlink, resolved-base containment, 0o700 dirs (§5.7).
- **A-32 (P0)** §4.3 → no `str.format` on a CSS-bearing template; one `_esc()`; `quote()` before `_esc()` in URL contexts (§5.10).
- **A-33 (P0)** §4.2 → headers from `page_headers()` **alone**; pin `cache-control: no-store` (§5.5).
- **A-34 (P0)** §4.2 → 2 MiB rendered-body cap + per-field caps, enforced during assembly, visible notice (§5.8).
- **A-35 (P0)** §7 tests → route-table **index** assertion + behavioural half; four-pin T-B7 set; uniform-404 byte equality (§5.1, §5.3, §5.4).
- **A-36 (P0)** §4.6 → `board_link` disabled envelope; compose both URL lines itself; note the Host-captured base (§5.9).
- **A-37 (P0)** §4.2 → one `to_thread` around the whole render; pin `check_same_thread: False` (§5.8).
- **A-38 (P1)** §2.3 → `root_path` returns `None` when disabled (§3.3).
- **A-39 (P1)** §2.3/config → generic reserved-root message (§5.2).
- **A-40 (P1)** §4.2/§7 → HEAD gets its own route (§5.1).
- **A-41 (P1)** → add "showing N of M" disclosure (§5.8).
- **A-42 (P1)** risks → add R36–R42, R45, R47.

### `plan-overview.md`
- **A-43 (P0)** §6 R2 → mark RESOLVED; correct R3, R18, R19, R15.
- **A-44 (P0)** §6 → add R33–R47 with severities.
- **A-45 (P1)** §3 → correct "3 shared edit files" (§7.4); add `tools_note.md` and `daemon/loader.py` (optional).
- **A-46 (P1)** §4/§5 → the privileged set is a cross-phase coupling with **three** pin passes, not one (§7.1).
- **A-47 (P1)** §9 → pre-draft the stage-time rollback-safety adjudication (§7.4).

---

## 10. Decisions pending

Nothing escalated to the user — every item was delegated and is decided above.

Two items the **planner** must decide (both LOW impact, neither blocking):

1. **Migration day-prefix choice** — `20261020_060000` is recommended (§2.6); any free prefix satisfies the constraint.
2. **Whether to adopt the optional `daemon/loader.py` warm-scan edit** (§3.3) — recommended for cold-boot doc fidelity, not required for correctness.

## 11. Open questions

1. **Live `custom_agent_configs` population** — unverified (no DB read was performed). It only affects the *size* of the default-grant population under Option B, which the ruling removes. No action.
2. **`enqueue_message_job` behaviour against a deleted project** — untraced; low likelihood (a project deleted between arm and fire) and the alarm still fires, only the job routing degrades. Flagged for Phase 2 tests if cheap.
3. **Whether a public instance→job resolver exists under a name other than `get_work_by_instance*`** — if one exists, the chat-bridge ruling could move from "no path" to "a narrow push path". It would not change the v1 ruling (the board is still the guaranteed surface), but it would be worth a v2 note.

## 12. Evidence base

Five independent skill-equipped analyses, each read-only against `feature/ari-tools @ 976cf0942`, each instruction-scoped to cite `file:line` and to mark `UNVERIFIED` rather than guess. The two highest-consequence findings — the empty-allow population (`watcher`) and the five-pin exactness — were **independently re-verified by the architect** against the worktree.

**Confidence: HIGH** on §1 (privilege mechanism, timing, and the `self-alarm` extension), §2.2 S-1/S-2/S-3/S-6, §3.1–§3.3, §4.1–§4.4, §5.1–§5.6, §5.9. **MEDIUM** on the numeric ceilings in §5.8 (2 MiB / 50 / 500 — design judgement, not measurement) and on §4.3 D-1's runtime impact (mechanism verified; not exercised against a live daemon). **UNVERIFIED** items are named inline.

**What would flip the C1 ruling:** a count or byte-hash pin over the privileged set outside the five files (none exists), or a policy forbidding test edits in a phase that adds no tests (none exists). **What would flip the lateness ruling:** evidence that the deployment intends to set the cap — which would make the skip-loop a launch blocker rather than a documented trade-off.
