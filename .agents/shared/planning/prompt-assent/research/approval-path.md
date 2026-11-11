# prompt-assent — Approval-Path Research (current-state anchors)

Worktree: `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, base `0efdf106f`).
All `file:line` anchors below are from THIS worktree, read directly. Nothing was modified; no commits made.

---

## Q1 — daemon/routers/prompts.py: approve/reject map + human-lane mechanism

**Endpoints** (router prefix `/prompts`, mounted at `daemon/api.py:5898` → `/api/prompts`):

| # | Path | Method | Scope | Lines |
|---|------|--------|-------|-------|
| 4 | `/versions/{version_id}/approve` | POST | `prompts:approve` | prompts.py:616-660 |
| 5 | `/versions/{version_id}/reject` | POST | `prompts:approve` | prompts.py:663-697 |
| 6 | `/versions/{version_id}/submit` | POST | `prompts:submit` (machine OK) | prompts.py:700-732 |
| 7 | `/versions/{version_id}/retract` | POST | `prompts:submit` | prompts.py:735-790 (svc.retract call at :777) |
| 15 | `/pending-count` | GET | `prompts:read` | prompts.py:1078-1100 |

**What makes a session "human"** — a three-layer chain:

1. `daemon/auth/context.py:31-32` — `AUTH_METHOD_SESSION = "session"` (the **cookie lane**); `AUTH_METHOD_BEARER = "bearer"` (static-token machine lane). `AuthMiddleware` attaches the `AuthContext` (subject, scopes, auth_method, expires_at, meta) at `request.state.auth_context` (`daemon/auth/context.py:5-7, 35-60`). No session-store object is read by prompts.py — "human" is purely `auth_context.auth_method == "session"`.
2. Router gates per endpoint: `_require_auth_on(request)` → 503 `AUTH_NOT_CONFIGURED` when `app.state.auth_config` absent (prompts.py:187-198; D3 fail-closed, reads included); then `@require_scope(_SCOPE_APPROVE)` (decorators at prompts.py:618/:665; the decorator is inert when auth OFF — `daemon/auth/decorators.py:81-91, 108-114, 117-131`).
3. Actor minting: `actor_from_request(request)` (`daemon/repositories/prompt_version/repository.py:593-642`) reads ONLY `request.state.auth_context`; body-asserted actor fields are IGNORED (R3 anti-forgery, :602-604); it REFUSES `auth_method == "internal"` as a forgery attempt (:624-629) and refuses unknown methods (:630-633). Empty subject → ValueError (:634-638).
4. The 403 itself: the SERVICE raises `HumanLaneRequiredError` (`daemon/services/prompt_version_service.py:677-717`, check at :712-717: `auth_method != "session"` → raise). Router catch sites: **approve 403 at prompts.py:639-640** (`raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Approve requires session lane (D2)")`, constant at :137), **reject 403 at prompts.py:680-681**. The router resolves the class lazily via `_resolve_exception` (prompts.py:1126-1163; aliases documented :1166-1186, `HumanLaneRequiredError(ValueError)` at services/prompt_version_service.py:166... wait — verified def at :184).

   **Correction**: `HumanLaneRequiredError` class def is at **prompt_version_service.py:184** (verified in the service def-map grep); the :166 figure seen in a docstring comment is stale prose, not the def.

**svc.retract routing** (F2 gate): prompts.py:735-790 — handler MUST route through `svc.retract` (:777, direct `await`, no extra `to_thread` — call-form note :774-776); a direct `svc.atomic_transition` bypass was the F2 finding (:747-753). Authority matrix lives service-side (session = any row; internal = own-row only; bearer = refused `machine_lane_refused`) — service.py:506-670, matrix table :584-597, bearer refusal :607-617, own-row check :619-641.

**What a NEW accepted approval path (Ari-relayed, with provenance) must satisfy in these routes** (current gates, verbatim):
- auth ON + `_require_auth_on` 503 gate (prompts.py:187-198, :633/:674).
- `@require_scope("prompts:approve")` (:618/:665).
- Actor from `actor_from_request` only (:635/:676) — subject comes from AuthContext, never the body (repository.py:602-604).
- Service lane: `auth_method == "session"` strictly (service.py:712-717) → **any bearer/internal relayed call 403s today**.
- Repo defense-in-depth: `approve`/`reject` refuse `actor.is_internal()` (repository.py:1560-1565, :1594-1599); `atomic_transition` requires non-empty `approver_subject` + `approver_auth_method` for `approved`/`rejected` and restricts `approver_auth_method ∈ {session, bearer}` (repository.py:1433-1444). The repo "refuses to invent approver identity" — a relay lane cannot pass a synthetic session actor without touching these three layers.

## Q2 — daemon/routers/custom_agents.py (v2 persona router)

- Authorization pairing: every endpoint carries `@require_scope` + an INLINE `_require_auth_on` 503 gate that fires BEFORE the decorator (header rationale :38-67; gate impl :202-219; scope reuse `prompts:read`/`prompts:write` :48-56). Endpoints: POST create :329-430 (gate at :368), GET list :433-440, GET single :447-452, DELETE (soft) — mounted `/api/custom-agents` (`daemon/api.py:5810`).
- **There is NO approve/reject endpoint in custom_agents.py** — it never raises `HUMAN_LANE_REQUIRED` anywhere. R-A1 (header :9-12, :63): `create_custom_agent` writes v1 prompt_versions rows DIRECTLY `state='approved'` + `is_selected=1` with `approver_subject` = HTTP actor subject, via `PromptVersionService.create_initial_approved_v1` (**prompt_version_service.py:388**). R-A2: DB-born, no template files (:13-16).
- Where persona approval 403s today: only when a pending CORE row for a custom agent goes through the GENERIC `/api/prompts` approve/reject (Q1 sites). A chat-relay acceptance path for personas would plug in either (a) at `/api/prompts` (custom-agent prompt rows are ordinary prompt_versions CORE rows) or (b) as new endpoints in custom_agents.py mirroring the prompts-family pattern (`@require_scope` + `_require_auth_on` pairing per :42-46); the memoir router (`daemon/routers/memoir.py:143-208`, mounted api.py:5899) is the freshest in-tree exemplar of a persona approve/reject family with the same 403 shape (:160-161, :192-193).

## Q3 — Service + repository: state machine, approve/reject, audit, advisory lock, effectiveness

**VALID_TRANSITIONS** (`daemon/repositories/prompt_version/repository.py:217-223`, frozen A1):
`draft→pending_approval`, `pending_approval→approved`, `pending_approval→rejected`, `pending_approval→draft`, `draft→archived`. `approved`/`rejected` TERMINAL; resubmit-after-reject = NEW row at max(version)+1 (:212-216). Editable states: draft/pending only (:257). Canonical re-export: `daemon/services/prompt_state_machine.py`.

**Service methods** (`prompt_version_service.py`, async facade over sync repo, one `asyncio.to_thread` per method):
- `approve` :435-472 — lane guard `_require_human_lane` (:467) then `to_thread(repo.approve)`.
- `reject` :474-504 — mirror (:499).
- `retract` :506-670 — authority matrix (see Q1), transitions `pending_approval→draft` only.
- `create_and_submit` :363; `create_initial_approved_v1` :388; `count_pending` :1012-1023; `create_memory_version` :1030+.
- `_require_human_lane` :677-717 — single equality test `auth_method == "session"`; error carries version_id + offending lane (:701-706, :712-717).

**Repo methods** (`repository.py`):
- `approve` :1541-1573 — refuses internal actors (:1560-1565, "F4: agents cannot self-approve"), delegates to `atomic_transition(from_state="pending_approval", to_state="approved", approver_subject=actor.subject, approver_auth_method=actor.auth_method)` (:1566-1573).
- `reject` :1577-1607 — mirror.
- `atomic_transition` def ~:1356 (params :1360-1365) — guard order: (1) MEMORY-class probe `_refuse_if_memory` FIRST (:1421-1427; probe impl :795-836), (2) transition-shape validation against VALID_TRANSITIONS (:1429-1431; `_validate_transition` :770-777), (3) approver-identity requirement for terminal states (:1433-1444), (4) CAS UPDATE `WHERE version_id = ? AND state = from_state` inside `engine.begin()` (:1457-1472), rowcount 0 → re-read + `LookupError` or `InvalidTransitionError` (:1473-1491).
- **Audit row SAME TRANSACTION**: payload `{"from_state", "to_state"}` + optional `approver_subject`/`approver_auth_method` (:1493-1501) → `_append_audit_in_conn` (:1502-1511; impl :3036+). Event map: approved→`APPROVED`, rejected→`REJECTED`, submit→`SUBMITTED`, recall→`RETRACTED`, archive→`ARCHIVED` (:1446-1452).

**pg_advisory_xact_lock pattern**:
- `_lock_key(agent_id, version_tag, artifact)` :161-204 — sha256 over LENGTH-PREFIXED canonical string `"prompt-version\x1f{len}:{id}\x1f{len}:{tag}\x1f{len}:{artifact}"`, first 8 bytes masked to 63 bits (positive signed bigint); namespaced so no cross-feature collision (:176-179).
- `_acquire_key_lock` :839-882 — dialect-gated (`engine.dialect.name != "postgresql"` → no-op :877-878); `SELECT pg_advisory_xact_lock(:key)` (:879-882); MUST be the FIRST statement in the caller's `engine.begin()`; transaction-scoped, covers the ABSENT-ROW gap that `SELECT … FOR UPDATE` cannot (:852-862). Taken by `create_draft`/`create_and_submit`/`set_selection`/`clear_selection` (usage example :966-970, :1652-1659). **NOT taken by `atomic_transition`** — its single-statement CAS is the serialization (:871-875).

**PENDING exclusion from get_effective**:
- `get_effective` :1900-1945 — class-aware: CORE = `selected.content > latest_approved.content > None` (:1941-1944); MEMORY = `selected > latest-row-any-state > None` (:1933-1940). A `pending_approval` row is neither selected nor approved → never effective (also F6 prose at migration header, 20261013_…sql:27-28).
- `get_effective_rows` :1949+ — ONE-SELECT for the (agent_id, version_tag) pair; `EffectiveRow` dataclass :650+; loader fingerprint folds per-class.
- `get_latest_approved` :1877-1896; `list_versions` :1843-1866 (key-scoped, NO state filter — router post-filters, prompts.py:526-527).

**Prior state required by approve/reject**: row must be in `pending_approval` (CAS predicate repository.py:1462-1464) and must be CORE-class (:1421-1427).

## Q4 — prompt_audit_events: model, migration, exact payload, extension touch points

**Model** `PromptAuditEvent` (`daemon/repositories/prompt_version/models.py:439-537`): table `prompt_audit_events` (:477); columns event_id PK UUID4-text (:502-506), agent_id (:507), version_tag (:508), artifact (:509), version_id (:510 — NO FK, audit survives row cleanup :464-466), event_type (:511-514), actor_subject (:515-523), actor_auth_method (:524-531), **payload `dict` via `Column(JSONBType)`** (:532-536), created_at (:537). Indexes: `(agent_id,version_tag,artifact,created_at)`, `(version_id)`, `(event_type,created_at)` (:478-500).

**Event-type vocabulary** `PROMPT_AUDIT_EVENT_TYPES` (models.py:134-147): CREATED, SUBMITTED, APPROVED, REJECTED, RETRACTED, ARCHIVED, SELECTED, CLEARED, AGENT_CREATE_TEMPLATE_COPIED, UPDATED, DELETED, TEMPLATE_RESET. Validated at `_validate_event_type` (repository.py:762-768). **Code-level frozenset only — adding a type is additive, NO schema change** (models.py:120-133 prose confirms the precedent).

**Migration**: `daemon/migrations/versions/20261013_214000_create_prompt_versions.sql` — ONE grouped file creates BOTH tables (lines 85-102 prompt_versions; **113-131 prompt_audit_events** with `payload JSON NOT NULL DEFAULT '{}'` at :122; indexes :126-131; DOWN :133-143). PG truth = `SQLModel.metadata.create_all` (dual-driver notes :55-68); index-name parity test `tests/migration/test_prompt_versions_index_parity.py` (:69-73). Alter precedent for this family: `20261014_013702_alter_prompt_versions_add_artifact_class.sql`.

**Exact payload written today for an APPROVED event** (repository.py:1494-1501):
```json
{"from_state": "pending_approval", "to_state": "approved",
 "approver_subject": "<AuthContext.subject>", "approver_auth_method": "session"}
```
Written by whom/when: `PromptVersionRepository.atomic_transition` (called from service.approve/reject ← HTTP router Q1) inside the SAME `engine.begin()` as the state UPDATE (:1457-1511). Other writers: `set_selection` (CLEARED+SELECTED, :1724-1746), `clear_selection`, `create_draft`/`create_and_submit` (CREATED/SUBMITTED), PATCH/DELETE (UPDATED/DELETED), standalone `append_audit_event` :2298-2338 (rare, outside-mutation callers). Audit READ surface: `list_audit_events` :2342-2417+, HTTP `GET /api/prompts/audit` prompts.py:916-987 (filters incl. `actor_subject`).

**Extension touch points for relay provenance (approving user + source e.g. my-discord-bot + message id + relay agent ari + timestamp)**:

*Option A — JSON-payload-extend (no schema change)*:
1. `atomic_transition` payload assembly (repository.py:1494-1501) — add optional kwargs (e.g. `relay_provenance: Optional[dict]`) merged into payload; thread through `repo.approve`/`repo.reject` (:1541-1607) → `svc.approve`/`svc.reject` (service.py:435-504) → router (prompts.py:616-697; needs a body model — today `_SubmitBody` is intentionally EMPTY, prompts.py:318-322).
2. EITHER keep event_type `APPROVED` (payload-only differentiation) OR mint a new type e.g. `APPROVED_RELAYED` in `PROMPT_AUDIT_EVENT_TYPES` (models.py:134-147) + event map (repository.py:1446-1452).
3. Guard note: body-asserted ACTOR identity stays forbidden (repository.py:602-604) — relay provenance is metadata ABOUT the relay, distinct from `actor_subject`; the architect must draw that line explicitly.
Cost: ~4 files, zero migration. Filterable only via JSON containment (no dedicated index).

*Option B — new columns*:
1. New migration `20261111_HHMMSS_alter_prompt_audit_events_add_relay_provenance.sql` (UP: `ALTER TABLE … ADD COLUMN` xN idempotent; DOWN drops; format per Q5).
2. SQLModel fields on `PromptAuditEvent` (models.py:502-537 block) — PG fresh-DB gets them via `create_all`; EXISTING PG DBs need a `_ensure_postgres_columns` entry (manager.py, Q5) — the artifact_class precedent (20261014_013702 migration + ensure entry).
3. Populate at the same payload assembly site (repository.py:1494-1501 region).
4. Index-name parity test extends if new indexes added.
Cost: migration + model + ensure entry + write-site; queryable/filterable natively; heavier ceremony.

## Q5 — Migration conventions + PG evolution semantics

**3 most recent migration files** (`daemon/migrations/versions/`):
1. `20261109_021154_create_memoir_v2_tables.sql`
2. `20261107_122648_create_source_gc_archive_tables.sql`
3. `20261107_110003_add_pok_source_tier.sql`

**Naming/format**: `YYYYMMDD_HHMMSS_<snake_description>.sql`; UP section first, DOWN section last (20261013_…sql:83 "UP", :133 "DOWN"); every statement idempotent (`IF NOT EXISTS` / `IF EXISTS`); grouped tables in one file allowed (skill-tables precedent cited at :5-6); index/constraint names MUST stay byte-identical to the SQLModel `__table_args__` (:69-73). The .sql runner is **SQLite-only**; PostgreSQL truth is `SQLModel.metadata.create_all` + `_ensure_postgres_columns` (20261013_…sql:55-68).

**`_ensure_postgres_columns`** (`daemon/manager.py:8396` def; docstring :8397-8484+ explains the create_all-gap rationale):
- Entry shape: plain SQL strings accumulated into a `statements` list — idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`, `CREATE INDEX IF NOT EXISTS …`, backfill UPDATEs (example entry, the most recent: POK `source_tier` ALTER at manager.py:10030-10034).
- Execution: **ONE transaction for the whole statement list** — `with self._engine.begin() as conn: for stmt in statements: conn.execute(text(stmt))` (**manager.py:10036-10038, verified**). NO per-statement try/except inside this block → any single entry's failure raises out of the method and aborts the ensure (the entire DDL list rolls back). **The ONE-TRANSACTION-ANY-FAILURE-ABORTS semantics are code-confirmed at :10036-10038**; the method itself has no internal catch around that block (contrast below).
- Deliberate CONTRAST in the same method: the legacy-`status` backfill UPDATEs run with PER-STATEMENT transactions (`with self._engine.begin()` per loop iteration + typed `except ProgrammingError/InternalError` skip logic) — manager.py:10049-10105 — proving per-statement isolation is possible but applied only to that specific tolerant block. "Order is not containment" holds for the main list.
- Caveat (honest scope): I verified the single-transaction framing and the absence of internal exception handling; I did NOT trace the method's CALLER to confirm the exception ultimately aborts boot (no caller-side try/except inspected).

## Q6 — List surface for "pending items awaiting assent" (ari-facing)

Current state: **no global pending-enumeration exists** for prompt_versions.
- `GET /api/prompts/versions?state=pending_approval` (prompts.py:444-541) — REQUIRES a full `(agent_id, version_tag, artifact)` key; if any part is None the router returns an EMPTY list (:500-507); `state` is a ROUTER-side post-filter (:526-527); repo `list_versions` has no state filter (repository.py:1843-1866).
- `GET /api/prompts/pending-count` (prompts.py:1078-1100) → `svc.count_pending` (service.py:1012-1023) → `repo.count_pending` (repository.py:2999-3029, CORE-only predicate `state='pending_approval' AND artifact_class='CORE'`, rides `idx_prompt_versions_state_updated`) — a count, not items.
- Closest in-tree exemplar of a pending LIST: memoir persona lane — `GET` route calling `repo.list_pending(agent_id="ari")` at `daemon/routers/memoir.py:130` (repo method def line not separately located; call site verified).

**Options**:
1. **Reuse + extend the prompts REST family (recommended shape)**: add `repo.list_pending()` (clone of `count_pending`'s WHERE clause returning rows, ordered by created_at; ~20 lines at repository.py:2999 neighborhood) + `svc.list_pending()` wrapper (service.py:1012 pattern) + `GET /api/prompts/pending` route (clone of pending-count route prompts.py:1078-1100, scope `prompts:read`, `_require_auth_on` included). Plumbing: 3 small touch points, all precedented (memoir.py:130 + count_pending). Ari consumes via bearer/internal HTTP with `prompts:read` — reads are lane-agnostic today (no human-lane check on GETs).
2. **New Ari read tool** (`daemon/tools/`): mirror the inner_soul plumbing — `_get_prompt_version_service(manager)` (inner_soul.py:1273-1283) + `_run_coro_sync` (inner_soul.py:1250) around `svc.list_pending()`; register the tool + add to `agents/ari/meta.json` allowlist. Plumbing: tool module + registry + meta.json; no router/DB change; works even when Ari acts in-process (no HTTP hop).
3. **shared_meta_kv snapshot**: write a pending-items JSON under a meta key at submit time (inner_soul `_submit_prompt_version` already handles the submit loop, inner_soul.py:1286-1364+) and clear/update at approve/reject — but approve/reject run through the HTTP service (Q1), so the KV write would need hooks in svc.approve/svc.reject (service.py:435-504) or the router. Plumbing: 3 write sites + boot rehydration; carries DB↔KV drift risk (stale list if any lane misses a hook). Cheapest to READ, most expensive to keep TRUE.

---

## Implications for prompt-assent (max 15 bullets)

1. The ONLY thing that makes an approval "human" today is `AuthContext.auth_method == "session"` (cookie lane) — checked at service.py:712-717, enforced twice more (router 403 prompts.py:639-640/:680-681; repo internal-refusal repository.py:1560-1565/:1594-1599; auth-method vocabulary clamp repository.py:1440-1444).
2. A chat-relayed acceptance CANNOT reuse `svc.approve`/`svc.reject` as-is: every layer refuses non-session actors; the repo explicitly "refuses to invent approver identity" (repository.py:1433-1444).
3. Candidate designs the current code implies: a NEW lane value (extend `_AUTH_METHODS` repository.py:507-511 + `_require_human_lane` service.py:677-717 + repo clamp :1440-1444 + audit payload), or a new service entrypoint that takes verified relay provenance and transitions via `atomic_transition` with an explicit provenance-bearing actor — architect's call; the three-layer refusal list above is the full gate inventory.
4. `atomic_transition`'s CAS (`WHERE state = from_state`) gives exactly-once semantics for free — a relayed double-approve is a benign `409 ALREADY_APPROVED` (prompts.py:647-655 mapping).
5. The 3-factor echo token has NO existing home: `_SubmitBody` is deliberately empty (prompts.py:318-322) — a relay endpoint needs either a body model (body-actor fields still forbidden) or headers/query params.
6. Relay provenance fits `payload` JSONB with ZERO schema change (Option A) — the vocabulary itself was extended additively twice before (UPDATED/DELETED, TEMPLATE_RESET; models.py:120-133).
7. New-columns (Option B) requires BOTH the .sql migration (SQLite runner) AND a `_ensure_postgres_columns` entry for existing PG DBs — the artifact_class pair (20261014_013702 + manager entry) is the template.
8. `_ensure_postgres_columns` is one-transaction for the whole statement list (manager.py:10036-10038) — a new entry's failure aborts boot; add entries idempotently (`IF NOT EXISTS`) and put them last for auditability only, NOT safety.
9. Migration file convention for any schema change: `YYYYMMDD_HHMMSS_<desc>.sql`, UP/DOWN, idempotent, index names byte-identical to `__table_args__` (parity test exists).
10. A "pending items" view does NOT exist today — `GET /versions` needs a full key and post-filters state (prompts.py:500-527); `pending-count` is a scalar (repository.py:2999-3029).
11. Cheapest pending-list path: repo `list_pending()` cloned from `count_pending`'s predicate + service wrapper + one GET route (memoir `list_pending` at memoir.py:130 is the precedent); reads need NO human lane — `prompts:read` suffices for Ari via bearer.
12. Retract routing discipline (F2) is a warning: any new relay acceptance route MUST go through the service gate, never raw `svc.atomic_transition` (prompts.py:747-753).
13. MEMORY-class rows never enter pending (born-approved, models.py:158-166) — the relay lane only ever sees CORE rows; `count_pending` already encodes that predicate.
14. The memoir persona router (memoir.py:143-208, api.py:5899) is the second live implementation of the identical human-lane 403 pattern — a relay lane must decide whether it covers BOTH surfaces or only `/api/prompts`.
15. Advisory-lock note: approve/reject need NO lock work — `atomic_transition` intentionally skips `pg_advisory_xact_lock` (repository.py:871-875); the CAS is the serializer.
