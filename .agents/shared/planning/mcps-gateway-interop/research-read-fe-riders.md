# Phase D1 — read surface + FE riders: code truth

Branch `latest` @ 9632badd4. Read-only research; no source modified.
Paths are repo-relative.

---

## 1. The three READ endpoints

### 1.1 `GET /api/mcps-gateway/machines`
- Handler: `daemon/routers/mcps_gateway_read.py:142-178` (`list_machines`); `@router.get("/machines", response_model=GatewayMachinesResponse)` at `:142`; `@require_scope("mcp:read")` at `:143`.
- Data source: **in-memory only** — `service.store.roster()` (`:157`) → `InMemoryMachineStore.roster` (`daemon/services/mcps_gateway/store.py:156-170`) → per-record `record.presence(now, timeout_ms)` (`store.py:54-65`); `_epoch_to_iso(record.last_seen)` (`read.py:127-139`). No DB, no repository.
- Response model: `GatewayMachinesResponse{machines, count, gateway_enabled}` (`daemon/routers/mcps_gateway_schemas.py:74-79`); item `GatewayMachine{machine_id, presence, online, connected, last_seen_at, session_id, agent_id, busy}` (`:43-71`).
- Pagination: **none**.
- Scope: `mcp:read`.
- try/except around the data read: **none**. The only failure path is `_service_of()` (`read.py:101-124`) raising `503 {"detail":{"error":"gateway_unavailable","message":"...not initialized","state":"not_initialized"}}` when `app.state.mcps_gateway_service` is `None` (`:110-123`). A DB outage cannot reach this handler.

### 1.2 `GET /api/mcps-gateway/sessions`
- Handler: `daemon/routers/mcps_gateway_read.py:181-218` (`list_sessions`); decorator `:181`, scope `:182`.
- Data source: **in-memory only** — `service.sessions` (`:191`) → `sessions.all()` (`:193`) + `sessions.for_machine(session.machine_id)` (`:194`) over `SessionManager` dicts (`daemon/services/mcps_gateway/sessions.py:89-90`). No DB.
- Response model: `GatewaySessionsResponse{sessions, count, gateway_enabled}` (`mcps_gateway_schemas.py:120-125`); item `GatewaySession{session_id, machine_id, agent_id, instance_id, state, machine_locked, started_at, last_activity_at, expires_at, tools}` (`:82-117`).
- Field filtering: `tools` keeps only dict entries with a truthy `name` (`read.py:207-211`) — non-conforming catalog entries are silently dropped.
- Pagination: **none**. Scope: `mcp:read`. try/except: none (same `_service_of` 503 only).

### 1.3 `GET /api/mcps-gateway/audit`
- Handler: `daemon/routers/mcps_gateway_read.py:221-244` (`list_audit`); decorator `:221`, scope `:222`.
- Pagination: `limit: int = Query(default=50, ge=1, le=200)` (`:225`; constants `AUDIT_DEFAULT_LIMIT=50` `:97`, `AUDIT_MAX_LIMIT=200` `:98`), `offset: int = Query(default=0, ge=0)` (`:226`). Out-of-range → 422 before the handler (`docs/mcps-gateway.md:766-767`).
- Data source: `rows = await asyncio.to_thread(service.audit_tail, limit, offset)` (`:236`) → `McpGatewayService.audit_tail` (`daemon/services/mcps_gateway/service.py:758-776`) → duck-typed `sink.list_recent(limit=, offset=)` (`:769-773`) → `McpGatewayAuditRepository.list_recent` (`daemon/repositories/mcps_gateway/repository.py:73-97`). `SELECT ... ORDER BY created_at DESC LIMIT/OFFSET` (`:87-92`).
- Response model: `GatewayAuditResponse{audit, count, limit, offset, gateway_enabled}` (`mcps_gateway_schemas.py:156-167`); item `GatewayAuditEntry{timestamp, agent_id, instance_id, machine_id, tool, outcome, session_id, error_code, args_hash}` (`:145-153`). `count` = PAGE LENGTH, not ledger total (`:159-160`, `docs/mcps-gateway.md:758-760`).
- Scope: `mcp:read`.

**What a DB exception does TODAY — swallowed twice, 200 + empty list:**
1. `repository.py:85-97` — `try: … except Exception as exc: logger.warning("the remote-command audit could not be read: %s", exc); return []` (`:95-97`).
2. `service.py:772-776` — the same swallow one layer up: `except Exception as exc: … return []` (`:775-776`).
3. Additionally, a sink without a `list_recent` attribute returns `[]` (`service.py:769-771`; `NullAuditSink` exists at `audit.py:128-132`).

⇒ Wire output on DB outage is **byte-identical** to an empty ledger: `{"audit": [], "count": 0, "limit": 50, "offset": 0, "gateway_enabled": <flag>}`. Three causes collapse into one shape: empty ledger / DB outage / sink with no reader. Nothing propagates to a 500; the router has no try/except at all (`read.py:235-244`).

---

## 2. DEGRADED SIGNAL — minimal touch points, two options

### 2.1 Minimal touch points (option-independent)
1. **Stop swallowing, or report the swallow.** Either delete `repository.py:95-97` (let the repository raise) and classify in `service.audit_tail`, or change that method to return `(rows, degraded, reason)`. One of the two layers must stop converting `Exception → []`; keeping both swallows means any new signal must be threaded through two frames.
2. **Response model.** The three envelopes share NO base class — `GatewayMachinesResponse` (`schemas:74-79`), `GatewaySessionsResponse` (`:120-125`), `GatewayAuditResponse` (`:156-167`) each re-declare `count` + `gateway_enabled`. Adding `degraded`/`error` needs **3 edits**, unless a `GatewayReadEnvelope(BaseModel)` is introduced and the three inherit (1 new class + 3 one-line base swaps). Nothing else in the file is shared.
3. **Router.** Machines/sessions are in-memory, so today only `list_audit` can be degraded. Adding the field to all three is contract symmetry, not coverage.
4. **Semantics.** `gateway_enabled` already answers "why is this empty when the transport is off" (`read.py:27-41`; `docs/mcps-gateway.md:662-674`). `degraded` must be orthogonal: disabled ≠ degraded (pinned today at `tests/unit/routers/test_mcps_gateway_read.py:348-381`).

### 2.2 Option (a) — 503 with a typed body
- **FE blast radius (breaks):** `decodeError` maps `status === 503` → `gateway_unavailable` (`mcps-gateway.service.ts:348-355`); the fallback message is hardcoded to "The remote-hands gateway service is not initialized." ⇒ a DB outage would be **labelled a boot-order defect**. The code survives only if the body carries `detail.error` (`:337`, then `:350`).
- The lane's `tap` never runs on an error (`service.ts:266-273`) ⇒ the `audit` signal keeps the **previous page** (stale rows stay on screen), `auditCount` is not updated and `canNextAuditPage` (`:146`) goes stale, while an error banner appears.
- FE tests that would need updating: `mcps-gateway.service.spec.ts:310-332` (503 → `gateway_unavailable`), `:366-376` (lane recovery after 503); `mcps-gateway.component.spec.ts:274-289` (503 banner).
- **Precedent:** 503 in this repo means "this surface is not available": un-wired service (`read.py:116-123`, `missions.py:266-269`, test `test_mcps_gateway_read.py:383-389`) and "writes paused for DB migration" (`daemon/routers/instances.py:316`, `dlq.py:329`, ~20 more). There is **no precedent of a read endpoint turning a DB read error into 503**.

### 2.3 Option (b) — 200 + `degraded: true` (+ error field)
- **FE blast radius (does not break):** the FE copies only known keys — `res.machines ?? []` (`:232`), `res.sessions ?? []` (`:246`), `res.audit ?? []` + `res.count ?? length` (`:267-268`). Extra fields are ignored, so no existing FE test changes; but nothing renders the signal either, so it fixes nothing until the FE adds a branch (model field + template banner + one signed `degraded()`).
- **Precedent (exact):** missions §8.2 — "NO 500 anywhere in the projection path": transient DB error on the count/page leg ⇒ 200 with empty page, `total=null`, `has_more=null`, `degraded=true` (`daemon/routers/missions.py:247-253`, construction `:288-295`). Field + doctrine at `daemon/routers/schemas.py:1618-1635` (`degraded: bool = False`, "operator signal: count unavailable, not zero"). Second precedent: `daemon/routers/resources.py:932-982` — 200 + `X-Resources-Traceback-Degraded` header + `_degraded` key stripped before the body (`:978-982`). **200-and-flag is the house convention for degraded reads; 503 is reserved for "not wired".**

### 2.4 Net
- (b) is 1 helper change + 1 shared base (or 3 field appends) + 3 FE branches, zero breaking wire changes, and matches `missions.py`.
- (a) is 1 change + 3 `raise` sites, but rewrites the documented failure contract (`docs/mcps-gateway.md:768` "offset beyond the end ⇒ 200 empty"; `:771` "503 = service not wired only") and makes the FE show a wrong-label banner with stale rows.

---

## 3. AUDIT ORDERING

- Order by clause: `daemon/repositories/mcps_gateway/repository.py:89` — `.order_by(McpGatewayAudit.created_at.desc())`. Single key. `.limit(max(1,int(limit)))` `:90`, `.offset(max(0,int(offset)))` `:91`.
- `created_at` is **TEXT** (`models.py:84-87`; migration `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql:80`), holding `datetime.now(timezone.utc).isoformat()` — microseconds + explicit `+00:00` (`daemon/services/timestamps.py:63-73`; mirror `models.py:39-40`). Lexicographic = chronological while the format is fixed.
- **Same-stamp ties are unstable.** A single ORDER BY key leaves equal stamps unordered in both PG and SQLite; with `limit`/`offset` paging a tied pair can be repeated on one page and skipped on the next. Ties are reachable: any producer writing a second-resolution stamp, backfills, or two rows in the same microsecond. No tie test exists — the fixture uses five distinct stamps (`tests/unit/repositories/test_mcps_gateway_audit_repository.py:97-100`, assertions `:209`, `:230-240`).
- `id` column: `id TEXT PRIMARY KEY`, `default_factory=_new_uuid` = **`uuid.uuid4()`** (`models.py:35-36`, `:80-83`; migration `:79`). Therefore `id DESC` is **deterministic but NOT chronological** — it makes paging stable, it does not make order meaningful within a tie.
- Where the tiebreaker goes: `repository.py:87-92` (the `select(...)` chain), as `.order_by(McpGatewayAudit.created_at.desc(), McpGatewayAudit.id.desc())`. Note there is no plain `created_at` index — the three indexes are `(agent_id|machine_id|tool, created_at)` (`models.py:62-78`; migration `:92-99`), so this ORDER BY is a sort either way.
- In-memory sink: `daemon/services/mcps_gateway/audit.py:154-157` — `sorted(self.records, key=lambda r: r.created_at, reverse=True)` then slice `:157`. Python's `sorted` is **stable** ⇒ ties keep INSERTION order (oldest first), i.e. the opposite of "newest first" and different from the DB's arbitrary/PK order. To keep projection parity it needs the equivalent key — but `AuditRecord` has **no `id` field** (`audit.py:68-96`), so parity requires either a monotonic sequence on the record or accepting that only the DB path can be fully ordered.

---

## 4. THE 3 DUPLICATED PROJECTION SITES

### 4.1 The sites
| # | site | transform | `created_at` |
|---|---|---|---|
| 1 | `daemon/repositories/mcps_gateway/repository.py:51-66` | `AuditRecord` → `McpGatewayAudit` ORM row (write) | **REAL**: `created_at=record.created_at or now_utc_iso()` (`:56`) — synthetic "now" stamped at write time when the producer omitted it |
| 2 | `daemon/services/mcps_gateway/audit.py:147-172` | `AuditRecord` → dict (memory sink read) | **VERBATIM**: `record.created_at` (`:160`), no fallback ⇒ `""` when unset (`AuditRecord.created_at: str = ""`, `:95`) |
| 3 | `daemon/routers/mcps_gateway_read.py:247-265` | dict → `GatewayAuditEntry` (wire) | **VERBATIM**: `timestamp=row.get("created_at")` (`:256`) ⇒ `""` passes through (schema allows `str|None`, `schemas:145`) |
| +4 | `daemon/repositories/mcps_gateway/repository.py:94` | `row.model_dump()` (db read dict) | verbatim TEXT; **superset**: includes `id` and `detail` |

Field-for-field differences:
- `id`: generated uuid4 in (1), present in (4), **absent** in (2) and (3).
- `detail`: `None` when empty in (1) `:65` and (2) `:169`; **dropped** by (3); present in (4).
- `instance_id` / `session_id` / `error_code`: `"" → None` in (1) `:58,63,64` and (2) `:162,167,168`; `None` pass-through in (3) `:258,262,263`; `None` (column) in (4).
- `agent_id` / `machine_id` / `tool` / `outcome` / `args_hash`: coerced `or ""` in (3) `:257,259,260,261,264`; already non-null in (1).
- **Format divergence when the stamp is real:** `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` — ISO-8601, **microseconds**, explicit `+00:00` offset (never epoch-ms, never naive, never millisecond-truncated). Corroborated by `timestamps.py:63-73` (byte-compatible with `datetime.now(timezone.utc).isoformat()`) and pinned in tests as `"2026-10-22T07:02:00.000000+00:00"` (`tests/unit/routers/test_mcps_gateway_read.py:274-277`, `:501`). The FE must trim 6→3 fraction digits before `new Date()` (`mcps-gateway.component.ts:120-129`).
- **The fake/real divergence in one sentence:** the DB path *hides* a missing stamp by substituting `now_utc_iso()` at write time (`repository.py:56`), the memory path *exposes* it as `""` (`audit.py:160`), and the wire path *passes it through* (`read.py:256`) — so the same logical record renders differently depending on which sink answered.

### 4.2 Proposed single helper (signature + contract only — no code edited)
```python
# canonical home: daemon/services/mcps_gateway/audit.py, next to AuditRecord
AUDIT_WIRE_KEYS: tuple[str, ...] = (
    "timestamp", "agent_id", "instance_id", "machine_id", "tool",
    "outcome", "session_id", "error_code", "args_hash",
)

def audit_row_to_wire(row: AuditRecord | Mapping[str, Any]) -> dict[str, Any]:
    """Canonical audit WIRE projection. Returns EXACTLY AUDIT_WIRE_KEYS."""
```
Contract:
- accepts **either** an `AuditRecord` (site 2) **or** a repo dict (site 3) — the union is what makes one helper serve both;
- `timestamp: str | None` — from `created_at`; `""` → `None`; a `str` passes through verbatim (TEXT ISO already carries the offset); **never synthesizes "now" on the read path** (that stays a write-side concern, site 1);
- `agent_id` / `machine_id` / `tool` / `outcome` / `args_hash`: `str`, `None → ""`;
- `instance_id` / `session_id` / `error_code`: `str | None`, `"" → None`;
- never emits `id` or `detail` (not wire fields — `schemas:145-153`);
- ordering, pagination, `count`, `gateway_enabled` and any degraded flag stay **out** of the helper (orthogonal concerns).
- Call-site shape afterwards: `GatewayAuditEntry(**audit_row_to_wire(row))` (`read.py:255-265`) and `[audit_row_to_wire(r) for r in window]` (`audit.py:158-172`). Sites 1 and 4 stay unchanged (write-side; superset dict pinned by a test).

### 4.3 Tests that pin each site's current output (update list)
- **Site 1 (write / real `created_at`):** `tests/unit/repositories/test_mcps_gateway_audit_repository.py:294-308` (`test_the_values_round_trip`; `created_at` equality `:300`, `error_code is None` from `""` `:308`). Seeding helper ~`:150-190`.
- **Site 2 (memory sink dict):** `tests/unit/routers/test_mcps_gateway_read.py:495-504` — `rows[0]["created_at"] == "2026-10-22T07:02:00.000000+00:00"` (`:501`), `rows[0]["tool"]` ordering (`:502`), empty tail (`:503`). Additional sink constructions (assertions UNVERIFIED line-by-line): `tests/unit/test_mcps_gateway_service.py:571, 602, 905, 937`.
- **Site 3 (wire projection):** `tests/unit/routers/test_mcps_gateway_read.py:244-269` (exact item key set `:259-269`), `:271-282` (newest-first + `timestamp` list `:274-278`); `tests/unit/repositories/test_mcps_gateway_audit_repository.py:310-330` (`_audit_entry` fed by a REAL row; `:322-330`).
- **Site 4 (repo dict = model superset):** `tests/unit/repositories/test_mcps_gateway_audit_repository.py:280-292` — `assert set(row) == set(McpGatewayAudit.model_fields)` (`:292`). ⚠️ A helper that returns only wire keys must **not** be applied here, or this pin breaks.

---

## 5. FE `SCOPE_INSUFFICIENT`

### 5.1 Where a 403 becomes `SCOPE_INSUFFICIENT`
`frontend/src/app/pages/mcps-gateway/mcps-gateway.service.ts:340-347`:
```ts
if (rawCode === 'SCOPE_INSUFFICIENT' || status === 403) {
  return { code: 'SCOPE_INSUFFICIENT', message: 'credential lacks mcp:read', status, source };
}
```
The `status === 403` arm fires **regardless of body** and **discards** `rawCode`, `rawMessage` and the server's `required` list.

### 5.2 The full mapping table (`decodeError`, `service.ts:332-370`)
| condition | emitted `{code, message}` | lines |
|---|---|---|
| `rawCode === 'SCOPE_INSUFFICIENT'` **OR** `status === 403` | `SCOPE_INSUFFICIENT` / `'credential lacks mcp:read'` | `:340-347` |
| `rawCode === 'gateway_unavailable'` **OR** `status === 503` | `rawCode ?? 'gateway_unavailable'` / `rawMessage ?? 'The remote-hands gateway service is not initialized.'` | `:348-355` |
| `status === 0` | `network_error` / `'The daemon is unreachable.'` | `:356-363` |
| anything else | `rawCode ?? 'http_<status>'` / `rawMessage ?? 'The read failed with HTTP <status>.'` | `:364-369` |
Body unwrap: `detail` object first, else the flat body (`:336-338`) — both shapes accepted. No branch for 401 and none for a redirect/HTML body.

### 5.3 What the page renders
One banner: `<strong>{{err.code}}</strong> <span>{{err.message}}</span> <span>({{err.source}})</span>`, `role="alert"`, `data-test="error-banner"` (`mcps-gateway.component.html:43-49`). Only the first non-null lane error is shown (`service.ts:126-128`).

### 5.4 The truthful label — check both paths
- **403 (auth ON):** credential lacks `mcp:read`. Decorator body `{"error":"SCOPE_INSUFFICIENT","message":"Token lacks required scope","required":["mcp:read"]}` — `daemon/auth/decorators.py:74-78`, raise at `:114`. Pinned `tests/unit/routers/test_mcps_gateway_read.py:465-478` (asserts `detail.error` and `detail.required == ["mcp:read"]`). Doc: `docs/mcps-gateway.md:770`.
- **OAuth-off path:** there is **no 403** — `_auth_enabled()` is False when `app.state.auth_config` is unset (`decorators.py:81-91`) and `_enforce` returns early (`:110-111`), so `require_scope` is inert and the endpoints answer 200. Confirmed by construction of the no-auth test app (`test_mcps_gateway_read.py:152-206`).
- **Other 403s exist** (so the label is not universally true): the auth middleware's CSRF guard emits `403 {"error":"CSRF_ORIGIN_MISMATCH"|"CSRF_TOKEN_MISMATCH","message":"Request rejected by CSRF guard"}` (`daemon/middleware/auth.py:471-491`, `:610-615`). Not reachable on GET (`csrf_applies` = state-changing methods only — `daemon/auth/cookie.py:220-229`), but any future non-GET, any reverse-proxy 403, or a body-less 403 lands on the scope label. And an unauthenticated read is **401 `AUTH_REQUIRED`** (`daemon/middleware/auth.py:596-608`; test `test_mcps_gateway_read.py:428-441`) or a **302 → `/api/auth/login?return_to=…`** (`middleware/auth.py:567-587`; test `:443-449`) — neither has a dedicated FE branch today (`http_401` / `http_200` via the redirect fallback).
- Truthful mapping: branch on `rawCode` first (keep `required` from the body in the message), use `status` only as a generic fallback ("forbidden"), and add `AUTH_REQUIRED` (401) + a redirect/HTML guard.

### 5.5 Every spec/test pinning the current label
- `frontend/src/app/pages/mcps-gateway/mcps-gateway.service.spec.ts:334-348` — flat `{error:'SCOPE_INSUFFICIENT', message:'missing scope'}`; asserts message `'credential lacks mcp:read'` (`:346`, i.e. the server's message is intentionally discarded).
- `frontend/src/app/pages/mcps-gateway/mcps-gateway.service.spec.ts:350-364` — nested `{detail:{error:…}}` shape; code `:361`, message `:362`, source `:363`.
- `frontend/src/app/pages/mcps-gateway/mcps-gateway.component.spec.ts:291-303` — banner contains `SCOPE_INSUFFICIENT` and `credential lacks mcp:read`.
- Doc-comments that restate the label: `frontend/src/app/models/mcps-gateway.model.ts:170-182`, `frontend/src/app/pages/mcps-gateway/mcps-gateway.component.ts:29-36`.
- `mcps-gateway.pins.spec.ts`: no `SCOPE`/`403`/`decodeError` assertions matched (UNVERIFIED beyond that — the file was not read end-to-end).

---

## 6. FE PAYLOAD ASSUMPTIONS → BE↔FE parity assertion set

Sources: models `frontend/src/app/models/mcps-gateway.model.ts`, template `mcps-gateway.component.html`, service `mcps-gateway.service.ts`, component helpers `mcps-gateway.component.ts`.

### Machines (`model:42-66`; template `:89-127`; service `:229-241`)
| field | FE usage | nullability | FE breaks if BE omits? |
|---|---|---|---|
| `machine_id` | `track` (`:89`), cell (`:91`), `data-machine-id` | non-null (`schemas:64`) | yes (track key undefined → all rows collapse) |
| `presence` | rendered raw (`:94`) + `presenceBadgeClass` switch (`component.ts:141-152`) | non-null | no (unknown → neutral badge) |
| `online` | ternary (`:97`) | bool default `false` (`schemas:66`) | no (renders "no") |
| `connected` | ternary (`:98`) | bool default `false` (`:67`) | no |
| `busy` | `@if` (`:111`) | bool default `false` (`:71`) | no |
| `last_seen_at` | `formatUtc` (`:99`) | `str|null` (`:68`) | no (null-safe `component.ts:120-123`) |
| `session_id` | `@if` (`:101`) | `str|null` (`:69`) | no |
| `agent_id` | `@if` inside session block (`:103`) | `str|null` (`:70`) | no |
| `count` | **not read** (template uses `machines().length`, `:69`) | — | no |
| `gateway_enabled` | `!== false` (`service:233`) | bool (`schemas:79`) | no (defaults true) |

### Sessions (`model:71-95`; template `:159-197`; service `:243-255`)
| field | FE usage | nullability | FE breaks if BE omits? |
|---|---|---|---|
| `session_id` | `track` (`:159`), cell (`:161`) | non-null (`schemas:108`) | yes (track key) |
| `agent_id` | cell (`:162`) | non-null (`:109`) | no (blank) |
| `machine_id` | cell (`:163`) | non-null (`:110`) | no |
| `instance_id` | cell (`:164`) | non-null (`:111`) | no |
| `state` | badge text (`:166`) | non-null, default `live` (`:112`) | no |
| `machine_locked` | `@if` (`:169`) | bool default (`:113`) | no (renders "—") |
| `started_at` | `formatUtc` (`:178`) | `str|null` (`:114`) | no |
| `expires_at` | `formatUtc` (`:179`) | `str|null` (`:116`) | no |
| `last_activity_at` | **never rendered** (model `:90`) | `str|null` (`:115`) | no — wire-only parity |
| `tools` | `@for (tool of session.tools; track tool)` (`:181`) | **array, default `[]`** (`:117`) | **⚠️ YES** — `@for` over `undefined` fails; the only FE assumption the BE must never break. `res.sessions ?? []` (`service:246`) defaults the LIST, not each item. |
| `count` | not read (`:137` uses length) | — | no |
| `gateway_enabled` | `!== false` (`service:247`) | bool | no |

### Audit (`model:100-122`; template `:225-258`; service `:257-279`)
| field | FE usage | nullability | FE breaks if BE omits? |
|---|---|---|---|
| `timestamp` | `formatUtc` (`:227`) | `str|null` (`schemas:145`); `""` also renders "—" | no |
| `agent_id` | cell (`:228`) | `str` coerced `or ""` (`read.py:257`) | no |
| `machine_id` | cell (`:229`) | `str` coerced (`:259`) | no |
| `tool` | cell (`:230`) | `str` coerced (`:260`) | no |
| `outcome` | text + `outcomeBadgeClass` (`:232`, `component.ts:155-168`) | `str` coerced (`:261`) | no |
| `session_id` | `@if` (`:235`) | `str|null` | no |
| `error_code` | `@if` (`:242`) | `str|null` | no |
| `args_hash` | `truncateHash` (`:249`) + `title` | `str` coerced (`:264`); helper null-safe (`component.ts:171-176`) | no |
| `instance_id` | **never rendered** (model `:106`) | `str|null` | no — wire-only parity |
| `audit` (array) | `@for … track $index` (`:225`) | default `[]` | no (positional tracking) |
| `count` | drives `canNextAuditPage` (`count === limit`, `service:146`) | page length (`schemas:164`) | **semantic**: a ledger TOTAL here breaks the pager |
| `limit` / `offset` | echoed into state (`service:269-270`) | ints (`schemas:165-166`) | no |

Exact key sets are already pinned BE-side and can be reused verbatim as the interop assertions: `tests/unit/routers/test_mcps_gateway_read.py:156-168` (machines), `:213-227` (sessions), `:248-269` (audit).

---

## 7. What makes a live "hit the endpoints and compare" test awkward

- **Auth is mandatory on the OAuth lane.** With auth ON, no credentials ⇒ 401 `AUTH_REQUIRED` JSON (only for `Accept: application/json`) or a 302 to `/api/auth/login?return_to=…` (`daemon/middleware/auth.py:567-608`; tests `test_mcps_gateway_read.py:428-449`). The test must mint a bearer with `mcp:read` (harness `:395-410`) or run a daemon with auth OFF.
- **Auth OFF proves nothing about scope.** `require_scope` is inert when `auth_config` is absent (`decorators.py:110-111`), so a 200 on an auth-off daemon does not exercise the 403 lane.
- **Non-empty machines/sessions require the transport.** The roster is in-memory and empties on restart (`store.py:12-16`, `read.py:147-151`), so rows only exist while `MCPS_GATEWAY_ENABLED=1` + a token is configured **and** a real machine has dialed in (§2 of `docs/mcps-gateway.md`; route registration is boot-time, `docs/mcps-gateway.md:161-166`). There is no way to seed a machine/session row.
- **Non-empty audit requires a real forwarded command.** Rows are written only by `_record_audit` (`service.py:1110-1145`, called on the `tools/call` path) — no seed endpoint, no write route (405 on POST/PUT/DELETE, `test_mcps_gateway_read.py:336-342`). An empty audit ledger is a perfectly legal 200, so "expect non-empty" fails for environmental reasons.
- **Polling + overlap guard.** The page polls every 10 s (`service.ts:64`; `component.ts:71-81`) and drops a refresh while one is in flight (`service.ts:160-188`) ⇒ a Playwright "click Refresh then assert" can be a silent no-op; `lastRefreshedAt` is flaky to assert on.
- **Single-banner derivation.** Only the first non-null lane error renders (`service.ts:126-128`) ⇒ any test asserting "the banner" must fail exactly one lane.
- **Pager edge case.** `canNextAuditPage = count === limit` (`service.ts:146`) ⇒ a 50-row page enables Next even when the ledger ends there (Next then returns an empty page, `test_mcps_gateway_read.py:299-304`). Do not assert "Next disabled" on an exactly-full fixture.
- **Redirect/HTML trap (UNVERIFIED mechanism, follows from `HttpClient` defaults).** A 302 to the login page is followed by the browser/XHR, the HTML body fails JSON parsing, and the error surfaces with `status: 200` → `decodeError` yields `http_200` (`service.ts:364-369`) and the lane keeps stale data. A live test that forgets the `Authorization` header may see this instead of 401.
- **Same-origin requirement.** FE URLs are relative (`/api/...`, `service.ts:35-47`) — an interop run needs the dev proxy or a daemon on the same origin; a second-daemon harness (8099 pattern used by the maintenance e2e) must additionally set `MCPS_GATEWAY_ENABLED` **before boot**.
- **Positional DOM identity.** `@for (entry of audit(); track $index)` (`template:225`) keys rows by index ⇒ after a tie-order shift the rendered content mutates with no key change; only row-order assertions are meaningful.
- **No cleanup lane.** There is no delete/write route on this surface, so audit rows created by a live test stay in `mcp_gateway_audit` (append-only by construction, `models.py:44`, migration header).
