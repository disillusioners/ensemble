# Phase 2: Settings API

## Objective

Operators (and the FE) can read effective daemon settings with per-key source-tier reporting, set a DB override, and clear it — via three endpoints on the existing settings router, auth-scoped per the router's own precedent.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **FREEZE THE CONTRACT** — define response models in `daemon/routers/schemas.py`: `DaemonSettingEntry {key, value, source ("db"\|"env"\|"yaml"\|"default"), default, env_value?, yaml_value?, db_override?, requires_restart, sensitive, description}` and `DaemonSettingsResponse {settings: [DaemonSettingEntry], restart_required: bool}`; `DaemonSettingUpdate {value: int}` (typed per managed key at v1). **PUT/DELETE response shape — frozen HERE (adopted note 4): the bare refreshed `DaemonSettingEntry`** — NOT wrapped in `DaemonSettingsResponse`, no top-level `restart_required` on writes; the entry's own `requires_restart` + `db_override` fields carry the restart semantics (consistent with O3's per-key rule). **Masking rule (architect security addendum 1):** for `sensitive: true` keys the GET/PUT response OMITS `env_value`/`yaml_value`/`db_override` entirely (omitted, not nulled — the fields never enter the response model). Top-level `restart_required` is DERIVED (true when any key carries a live `db_override`) — see t3 for who consumes it. **GET `value` carriage (iteration-003):** the entry's `value` = `snapshot.applied_value` (the boot-applied running truth) — `applied_value` is NOT a resolver tier; the `resolve(key, snapshot)` ladder stays live DB > live env > snapshot yaml > default, untouched. This JSON shape is the Phase 3 contract — changes after this task require updating both phases | P1 t3-t4 | **Schema mirror** of `resolve(key, snapshot)`'s output shape (field names/types incl. `sensitive`) — value semantics deliberately diverge from `resolve()` (GET `value` is the boot-applied truth, not a fresh resolve; see t3); FE types will be copied verbatim |
| 2 | **Consume/verify the P1-t6-created router DI (iteration-002 Shape A — NO creation, NO edit in this task):** confirm `_settings_service` + `set_settings_service()` + the 503-raising getter and the resolution-snapshot `set_settings_resolution_snapshot()`/getter exist in `daemon/routers/settings.py` exactly as P1 t6 created them (both getters 503 when unwired) | **P1 t6** | 503 with clear detail when lifespan hasn't wired it; both symbol pairs importable from the router module; this task owns zero edits to `daemon/routers/settings.py` or `daemon/api.py` |
| 3 | `GET /settings/daemon` — gated `dependencies=[scope_required("settings:read")]` (architect Ruling 4; the infrastructure precedent class). Semantics (resolver SSOT, blind spot b): each entry's `value` = `snapshot.applied_value` — the boot-applied running truth (what the semaphore was constructed with; carriage from the P1-t5 snapshot, NOT a re-resolve); each entry's `source` = LIVE re-resolve via the one shared `resolve(key, snapshot)` over (live DB row > live env > snapshot yaml > default); divergence between `value` and `source` is honest restart-required reporting. Top-level `restart_required` = derived (any `db_override` present); **the FE keys off the per-key `requires_restart` field, NOT the top-level field** (adopted ruling O3 — the top-level field remains in the contract for API consumers/tooling and is documented as such in the schema docstring) | P1 t4, t2 | Gated GET (403 without `settings:read` under auth-on); payload matches the frozen contract; `value`/`source` divergence matches the snapshot semantics; masking honored for sensitive keys |
| 4 | `PUT /settings/daemon/{key}` (body `DaemonSettingUpdate`) and `DELETE /settings/daemon/{key}` — validate key ∈ registry (404 unknown), validate/coerce value against registry validator (422 on failure: out-of-range, non-int), then `set_override` / `clear_override`; response = the bare refreshed `DaemonSettingEntry` (the t1-frozen write shape — no wrapper, no top-level `restart_required`). **Actor capture (security addendum 2, adopted note 5):** REPLICATE the `actor_from_request` pattern (prompt_version/repository.py:593-637) with getattr-based extraction — do NOT import that helper verbatim, it RAISES on auth-off/internal callers; here auth-off → `updated_by = None`; NEVER body-asserted; NEVER a `request.client.host` fallback. **Audit (adopted ruling O2):** one INFO log line per PUT/DELETE carrying subject + key ("auth-off" for NULL rows) | P1 t3, t2 | 404 / 422 paths tested; PUT persists a row visible to the bootstrap reader; DELETE removes it and GET falls back down the ladder; INFO audit line emitted on both verbs; `updated_by` NULL (not a raised error) under the auth-off fixture |
| 5 | Auth gates ONLY (no wiring — the api.py:2451-2453 wiring is P1-t6-owned; this phase edits zero api.py code): `dependencies=[scope_required("settings:read")]` on GET; `scope_required("settings:write")` on PUT + DELETE (the snapshot-create pair settings.py:661/:675 is the in-router write precedent — GET/PUT citations, not POSTs); **register BOTH scopes in `SCOPE_TAXONOMY` (`daemon/auth/scopes.py:53-83`)** — closed taxonomy (normalize_scope_list rejects unknown names, scopes.py:120-122) makes registration mandatory | t2-t4 | Auth-on install: GET without `settings:read` → 403; PUT/DELETE without `settings:write` → 403; both scopes resolvable post-registration; **verify the P1-t6-owned lifespan wiring is present and green** (read-only reference check — `grep -c "set_settings_service" daemon/api.py` unchanged by this phase) |

## Tests

- `tests/test_daemon_settings_api.py` (authored Phase 4, pattern = `tests/test_scheduler_api.py:22-143` mock-manager/app/client fixtures, but wiring the settings service DI with a temp-SQLite-backed `SettingsService` instead of a manager):
  - GET 200 shape + tier correctness (seed each tier), incl. `value` (boot-applied) vs `source` (live re-resolve) divergence case.
  - **GET scope gate (adopted P6a): 403 without `settings:read` / 200 with it, under the auth-on fixture.**
  - **`sensitive` masking test (adopted P6b): a `sensitive=True` registry entry yields a GET payload omitting `env_value`/`yaml_value`/`db_override`; plus a pin that v1's `limits.llm_concurrency` entry is `sensitive=False`.**
  - PUT happy path → row persisted → GET `source="db"`; PUT unknown key → 404; PUT `value=0` → 422 (ge=1); PUT non-int → 422.
  - DELETE → override gone, GET shows lower tier.
  - Unwired service → 503.
  - Scope gates: GET 403 without `settings:read`; PUT/DELETE 403 without `settings:write` (auth-on fixture).

## Coupling

- **Tight with Phase 1** — consumes `SettingsService` and registry; cannot start before P1 t3-t4 (service) and P1 t6 (DI symbols + wiring) exist.
- **Tight with Phase 3** — the task-1 contract IS the FE's input; freeze it here.

## Risks

- Contract drift after FE starts (Phase 3) — mitigated by task-1 freeze + coupling map note.
- `restart_required` semantics ambiguity (applied-but-pending detection) — v1 keeps it dumb (`any db_override present`); live-apply detection is explicitly future work.
- ~~Scope-name bikeshed~~ — RESOLVED by architect Ruling 4: both `settings:read` and `settings:write` registered in `SCOPE_TAXONOMY`; no reuse debate remains (the previously-considered reuse candidate does not exist — `maintenance` is a read-only family, scopes.py:63).

## Exit Criterion

All three endpoints live behind the router's DI idiom with auth + validation + frozen contract (write shape = bare `DaemonSettingEntry`); api.py wiring is P1-t6-owned and merely verified here — this phase edits zero `daemon/api.py` code; API test file green; GET reports the exact tier ladder the resolver computes.
