# Snapshot-UIUX — Backend Plan (be-plan.md)

> **⚠️ SUPERSESSION (amendment pass, 2026-10-05):** Where this body conflicts with `sequencing.md` §1, **§1 is binding.**
>
> **Scope.** This file is the **backend-only** plan for the `snapshot-uiux`
> feature: the new `Snapshots` page needs read-only HTTP access to the
> `snapshots` table (list + detail + monitoring metrics) and a stable
> contract for the FE to call. Writes remain tool-only (the
> `snapshot_create` agent tool is the only mutator). The matching FE
> plan lives alongside this file in `.agents/shared/planning/snapshot-uiux/`.
>
> **Worktree.** `/home/nea/ensemble-src-wt-snapshot-uiux`, branch
> `feature/snapshot-uiux` @ `ac399874` (base = `latest`).
> **This is PLAN-ONLY — no source files are modified, no commits made.**

---

## 1. Objective

Expose a read-only HTTP surface for the existing `snapshots` +
`snapshot_usage_counters` tables so the new `Snapshots` FE page can
list, filter, drill in, and surface R16 monitoring counters without
resorting to tool calls. Three endpoints ship in v1: `GET /api/snapshots`
(filterable list), `GET /api/snapshots/{id}` (single row with optional
digest), and `GET /api/snapshots/metrics` (R16 monitoring). Writes,
deletions, archive, and supersede are explicitly out of scope and
remain tool-only.

## 2. Scope

**In scope (v1, this plan):**

- New `daemon/routers/snapshots.py` with three endpoints
  (list / detail / metrics).
- One new repo method `list_with_filters(...)` on
  `SnapshotRepository`; `list_active_by_project` becomes a 3-line
  compat wrapper to keep the `snapshot_search_service.py:328` call
  byte-compatible.
- Two Pydantic response models in `daemon/routers/schemas.py`
  (`SnapshotListResponse`, `SnapshotResponse`).
- API router include + module import in `daemon/api.py`.
- Metric endpoint relocation: new canonical home
  `GET /api/snapshots/metrics`; old `GET /api/settings/snapshot-usage-metrics`
  is preserved as a thin re-export for one release window.
- Test plan: new `tests/unit/routers/test_snapshots.py` (router, via
  `TestClient`); extended `tests/unit/test_snapshot_repository.py`
  (generic-list + PG/SQLite tag parity); regression check on the
  settings router toggle endpoints.

**Out of scope v1 (deferred to a follow-up; see §10):**

- HTTP write operations (delete, archive, supersede) — these stay
  tool-only and behind a future feature commission.
- `size_bytes` / digest-size column — Phase-2 dimension (the
  `[given]` baseline is migration-free v1).
- `last_used_at` column on `snapshots` — Phase-2 (the
  `snapshot_usage_counters.spawn:snapshot:*` rows already expose
  warm-counts via the metrics endpoint).
- Per-agent `snapshot_enabled` gating — the
  `daemon/registry.py:298-301` change ships in the
  `feature/unify-spawn-tools` branch; v1 here ships a stub-free
  contract (no placeholder column, no UI param, no schema drift).
- Auth gating — inherits the project's open posture; no
  per-instance/per-user auth exists anywhere in the daemon
  ([given] §2 "Auth: NO auth gating exists anywhere in daemon —
  inherit open posture").

## 3. Decisions + Rationale

| ID | Decision | Rationale | Rejected alternative |
|----|----------|-----------|------------------------|
| D1 | **Global `GET /api/snapshots` with optional `project_id` filter** (NOT a per-project prefix) | The FE page is global (filter chip on top, not a project-scoped tab) per dispatch §1 lean. Rows are project-scoped at the SQL layer; mirroring the project in the URL adds no security, only URL-noise. Mirrors the existing `GET /api/skills` pattern (`daemon/routers/skills.py:522-587`) where `project_id` is a query param, not a path segment. | `GET /api/projects/{project_id}/snapshots` — adds a hard project ownership claim that contradicts the global page; harder to extend to "show me all snapshots across all projects" later; the FE would have to omit project_id to get the global view, breaking the URL-as-filter contract. |
| D2 | **Repo method: add `list_with_filters(...)`; keep `list_active_by_project` as a 3-line compat wrapper.** | The single production caller (`daemon/services/snapshot_search_service.py:328`) and the duck-typed test fake (`tests/unit/test_snapshot_search_service.py:141`) both already pass `(project_id, limit)`. A 3-line wrapper that calls the new method with `statuses=(SNAPSHOT_STATUS_ACTIVE,)` keeps the search service byte-compatible — zero risk to the spawn-hot WARM path. | Rename and update callers — costs one PR merge race against `feature/unify-spawn-tools` (both branches touch the snapshot repo surface) and risks breaking the search service's duck-typed contract. |
| D3 | **Tag filter is `repeat-param` (`?tags=a&tags=b`), NOT comma-joined.** | FastAPI's idiomatic `tags: list[str] = Query()` is the project standard (`daemon/routers/blueprints.py:60-63` triggers, `daemon/routers/skill_bank.py` similar). Comma-joined encoding is a footgun: an R8 tag may contain a colon (`dim:value`) but never a comma today; the moment a tag adds a comma (Phase-2), the comma-joined encoding silently truncates. The FE contract is explicit: **one `tags` param per value**. | `?tags=a,b` (comma-joined) — encoding-collision risk; requires escaping; no precedent. |
| D4 | **`agent` filter is exact match on `created_by_agent_id`, NOT substring.** | Agent ids are stable, short, fixed-set strings (`"coder"`, `"tester"`, `"explorer"`, `"governor"`, … — see `agents/`). The list page will have an agent-id filter chip backed by the registry's known-agent list, so substring search would mislead users into expecting agent-name search (which the schema doesn't carry). | Substring `LIKE '%{agent}%'` — misleading; opens a footgun where a typo'd value silently returns nothing. |
| D5 | **`status` filter is a multi-value; default = ALL 5 statuses (no implicit active-only filter).** | The page surfaces `running` / `failed` / `interrupted` rows too (ops visibility) — the FE draws chips for each. A query-param-only filter means the FE picks. Mirrors the project-scope pattern on `GET /api/skills` (no implicit scoping — caller decides). | Default `active` only — hides operational states; contradicts the page's "see all snapshots" framing; would require the FE to send `?status=active&status=running&…` to opt out. |
| D6 | **`created_before` / `created_after` accept ISO-8601 strings and are validated server-side via `datetime.fromisoformat`.** | The `created_at` column is already ISO-8601 TEXT (`daemon/repositories/snapshot/models.py:228`); the lexicographic ordering of zero-padded ISO-8601 strings equals chronological order (verified: `_now_iso()` returns `datetime.now(timezone.utc).isoformat()` which is always zero-padded). Locale-parsing (e.g. `dateutil`) is rejected because it accepts a much wider grammar than the schema actually stores. | Pass-through without validation — would let `?created_after=banana` succeed and return an empty page silently. |
| D7 | **Detail endpoint: full `to_dict()` plus `?include=digest` opt-in to keep list payloads lean.** | `digest` is unbounded JSONB (`daemon/repositories/snapshot/models.py:224-227`); a 50-row list page would carry tens of MB if the digest were inline. The list endpoint strips `digest` from `to_dict()`; the detail endpoint returns the full row but treats `digest` as lazy (default off; opt-in via `?include=digest`). | Always return full `to_dict()` — measurably bigger list payloads (typical digest is 8–25k tokens per the D6 ceiling context); no FE benefit (the list page never needs the digest). |
| D8 | **Metrics endpoint: RELOCATE to `GET /api/snapshots/metrics`; keep the old `GET /api/settings/snapshot-usage-metrics` as a 1-line re-export for one release.** | The only caller of the old path is the FE (`frontend/src/app/services/settings.service.ts:175-181`); the FE updates in lockstep in the same feature. A re-export preserves any non-FE caller (CLI / scripts / curl dashboards) for one release window. After the FE has shipped and a `CHANGELOG.md` deprecation note lands, the old path can be removed in a follow-up. | Drop the old path immediately — breaks the FE; reverses the dispatch's "minimal-breakage" framing. |
| D9 | **No per-agent `snapshot_enabled` gate in v1.** | That field lands in the `feature/unify-spawn-tools` branch (their `daemon/registry.py:298-301` change). The BE side of this plan is disjoint from that branch's BE touchpoints (`daemon/tools/snapshot_tools.py:917+`, `daemon/tools/instance.py:2092`, `_tool_registry.py`, `daemon/registry.py`). v1 ships a stub-free surface; a follow-up couples the two once unify-spawn-tools lands. | Add a stub `agent_filter` param now — placeholder; placeholder drift; per the dispatch's coupling rule. |
| D10 | **Engine retrieval for the metrics endpoint: read `manager._snapshot_metrics_service` directly (do NOT use `_project_repo.engine` like the settings router does).** | The settings router's `getattr(_project_repo, "engine", None)` pattern is a workaround for the historical fact that the settings router predates the snapshot service (`daemon/routers/settings.py:727-732`). The manager has both `self._snapshot_repo` (`daemon/manager.py:1816`) and `self._snapshot_metrics_service` (`daemon/manager.py:1861-1864`) wired at construction. The new router should depend-inject the manager (FastAPI `request.app.state.manager`) and grab `manager._snapshot_metrics_service` directly — clearer ownership; no global module-level singleton. | Reuse the settings-router pattern — couples the new router to the settings module's private global; creates an import-order coupling. |

## 4. API Contract

All paths are prefixed with `/api/`. All payloads are JSON. All UUID path
params are validated server-side; an invalid UUID yields `400`. All
write/read endpoints inherit the project's open posture
([given] §2 "Auth: NO auth gating exists anywhere in daemon"); no auth
gating in v1.

### 4.1 `GET /api/snapshots` — list

**Query parameters** (all optional except where noted):

| Param | Type | Default | Validation | Notes |
|-------|------|---------|------------|-------|
| `project_id` | `UUID4` string | `None` (all projects) | `uuid.UUID()` parse → 400 on fail | When set, restricts to one project. Mirrors `daemon/routers/blueprints.py:137-155` (`_validate_project_id`). |
| `agent` | string | `None` (all agents) | exact match against `created_by_agent_id` | **D4 — exact match, not substring.** |
| `tags` | list of strings (repeat-param) | `[]` | non-empty after strip | `?tags=kind:implementation&tags=subsystem:upgrade-pipeline` |
| `tag_mode` | enum | `"all"` | `Query(pattern="^(all|any)$")` → 422 on invalid | Combined with `tags` only (no-op when `tags=[]`). |
| `status` | list of strings (repeat-param) | all 5 statuses | each value must be in `SNAPSHOT_STATUSES` (`daemon/repositories/snapshot/models.py:93-101`) → 422 on invalid; reject duplicates | `?status=active&status=running` |
| `created_after` | ISO-8601 string | `None` | `datetime.fromisoformat(...)` → 400 on fail | Inclusive lower bound (lexicographic ≥). |
| `created_before` | ISO-8601 string | `None` | `datetime.fromisoformat(...)` → 400 on fail | Inclusive upper bound (lexicographic ≤). |
| `sort` | enum | `"created_at_desc"` | `Query(pattern=...)` allow-list (see below) → 422 on invalid | |
| `limit` | int | `50` | `Query(default=50, ge=1, le=200)` (FastAPI rejects out-of-range with **422**); double-clamp idiom (`effective_limit = min(int(limit), 200)` at `daemon/routers/skills.py:1235-1240`) remains in code as a **defense-in-depth belt for direct repo callers**, NOT an HTTP 200-success path (case 21 pins 422) | D-6 `Query(ge=1, le=200)` is the contract; the runtime clamp is belt-and-suspenders. |
| `offset` | int | `0` | `Query(default=0, ge=0)` (FastAPI rejects negatives with **422**); double-clamp idiom (`effective_offset = max(int(offset), 0)`) remains in code as a defense-in-depth belt for direct repo callers | D-6 `Query(ge=0)` is the contract; case 21 pins 422. |

**Allowed `sort` values** (pattern-validated, exhaustive list):

| Value | SQL mapping |
|-------|-------------|
| `created_at_desc` (default) | `order_by(col(Snapshot.created_at).desc())` |
| `created_at_asc` | `order_by(col(Snapshot.created_at).asc())` |
| `title_asc` | `order_by(col(Snapshot.title).asc())` |
| `title_desc` | `order_by(col(Snapshot.title).desc())` |
| `status_asc` | `order_by(col(Snapshot.status).asc())` |
| `status_desc` | `order_by(col(Snapshot.status).desc())` |
| `project_id_asc` | `order_by(col(Snapshot.project_id).asc())` |
| `project_id_desc` | `order_by(col(Snapshot.project_id).desc())` |

Any other value is rejected with 422 (Pydantic + FastAPI handles this
automatically when `pattern=...` is set).

**Response (200) — `{items, total}` envelope** (mirrors
`BlueprintListResponse` at `daemon/routers/blueprints.py:122-126`):

```json
{
  "items": [
    {
      "id": "uuid",
      "project_id": "uuid",
      "created_by_agent_id": "coder",
      "target_instance_id": "uuid-soft-ref",
      "title": "version-pump-taskpack-after-v0.13.9",
      "domain_tags": ["kind:implementation", "subsystem:upgrade-pipeline"],
      "status": "active",
      "supersedes_snapshot_id": null,
      "repo_path": "/home/nea/ensemble-src",
      "vcs_type": "git",
      "git_sha": "abcdef…",
      "git_branch": "main",
      "git_dirty": false,
      "runtime_version": "0.17.0",
      "effective_model": "gpt-4o-mini",
      "created_at": "2026-10-05T18:58:44.618852+00:00"
    }
  ],
  "total": 42
}
```

The `digest` field is **stripped** at the router layer; clients opt in
via the detail endpoint's `?include=digest` (D7). `task_summary` is
likewise **stripped from list items** (binding addendum A-2 in
sequencing.md §1.3 — amendment finding #3) and remains available on
the detail endpoint's full `to_dict()`.

**Error responses:**

| Status | When | Body shape |
|--------|------|------------|
| 400 | `project_id` not a valid UUID | `{"detail": "project_id must be a valid UUID"}` |
| 400 | `created_after` / `created_before` not parseable as ISO-8601 | `{"detail": {"error": "<message>"}}` (Pydantic-style) |
| 422 | `tag_mode` not `all`/`any`; `status` not in `SNAPSHOT_STATUSES`; `sort` not in allow-list; `limit`/`offset` out of range | FastAPI default Pydantic body |
| 500 | Repo / DB failure | `{"detail": {"error": "<message>", "endpoint": "list_snapshots"}}` (mirrors `_to_http_500` pattern in `daemon/routers/skills.py:587`) |

### 4.2 `GET /api/snapshots/{snapshot_id}` — detail

**Path parameters:**

| Param | Type | Validation |
|-------|------|------------|
| `snapshot_id` | string (UUID4) | presence check + UUID parse → 400 on blank/invalid |

**Query parameters:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `include` | enum | `""` | Reserved for future expansions; v1 supports exactly one value: `digest`. Anything else is ignored (forward-compatible). |

**Response (200) — full `to_dict()` of the row**:

```json
{
  "id": "uuid",
  "project_id": "uuid",
  "created_by_agent_id": "coder",
  "target_instance_id": "uuid-soft-ref",
  "title": "...",
  "task_summary": "...",
  "domain_tags": [...],
  "status": "active",
  "supersedes_snapshot_id": null,
  "repo_path": "...",
  "vcs_type": "git",
  "git_sha": "...",
  "git_branch": "main",
  "git_dirty": false,
  "runtime_version": "0.17.0",
  "effective_model": "gpt-4o-mini",
  "digest": { /* unbounded JSONB; included only when ?include=digest */ },
  "created_at": "2026-10-05T..."
}
```

When `?include=digest` is **omitted**, the `digest` key is present
with value `{}` (not absent) so the FE can branch on presence without
`undefined`-guards. When `?include=digest` is set, the full `digest`
dict is returned.

**Error responses:**

| Status | When | Body shape |
|--------|------|------------|
| 400 | `snapshot_id` blank or not UUID-shaped | `{"detail": "snapshot_id must be a valid UUID"}` (mirrors `_validate_project_id`) |
| 404 | No row matches | `{"detail": {"error": "Snapshot not found", "snapshot_id": "..."}}` (mirrors the 404 shape at `daemon/routers/skills.py:799-810`) |
| 500 | Repo / DB failure | `{"detail": {"error": "...", "endpoint": "get_snapshot"}}` |

### 4.3 `GET /api/snapshots/metrics` — R16 monitoring (relocated)

**Query parameters:** none.

**Response (200) — exact `SnapshotUsageMetricsResponse` body** (already
defined at `daemon/routers/schemas.py:1781-1818`):

```json
{
  "capture_counts": {
    "coder": {"created": 17},
    "tester": {"created": 9}
  },
  "spawn_counts_per_snapshot": [
    {"snapshot_id": "uuid-1", "count": 3}
  ]
}
```

**Behaviour:** mirrors `daemon/routers/settings.py:702-738` exactly.
The handler depends-injects the manager via `request.app.state.manager`
(D10) and returns `manager._snapshot_metrics_service.surface()` —
this is the SAME service object the settings router uses; no
duplication, no fork.

**404 / 503 semantics:** engine-not-wired → return the empty
`SnapshotUsageMetricsResponse` (degraded shape), NOT a 503 — the
`getattr(_project_repo, "engine", None)` pattern at
`daemon/routers/settings.py:727-732` exists for a reason (the
service can be queried before wiring is complete during boot
sweeps); preserve.

**Old endpoint behaviour (`GET /api/settings/snapshot-usage-metrics`):**
stays operational for one release window; handler body becomes:

```python
@router.get("/snapshot-usage-metrics", response_model=SnapshotUsageMetricsResponse,
            deprecated=True)
async def get_snapshot_usage_metrics_deprecated(request: Request, response: Response):
    """DEPRECATED — use ``GET /api/snapshots/metrics`` instead.

    Re-export to preserve any non-FE caller (CLI, scripts, curl
    dashboards) for one release. Removal planned once the FE has
    shipped and a CHANGELOG deprecation note is in place.
    """
    # Deprecation signal — surfaces to API clients that this endpoint is
    # superseded by /api/snapshots/metrics. RFC 8594 Deprecation + RFC 8288 Link.
    # NB: FastAPI's ``deprecated=True`` flag ONLY marks the route in OpenAPI
    # docs — it does NOT emit the actual response headers. We set them
    # manually here, copied from the project precedent at
    # ``daemon/routers/blueprints.py:551-557`` (the deprecated /initialize
    # handler), with the successor link pointing at the relocated
    # /api/snapshots/metrics endpoint.
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sun, 31 Dec 2026 23:59:59 GMT"
    response.headers["Link"] = '</api/snapshots/metrics>; rel="successor-version"'
    return await _proxy_to_snapshots_metrics(request)
```

> **Pass 4 amendment, blocker #6 (LEADER RULING) — placement of
> `_proxy_to_snapshots_metrics`.** The helper is DEFINED in
> `daemon/routers/snapshots.py` (the canonical home for the snapshot
> router) and IMPORTED by `daemon/routers/settings.py`. The
> `daemon/routers/settings.py` handler above calls
> `await _proxy_to_snapshots_metrics(request)` — the symbol resolves
> via the import added in §5.2.
>
> **The canonical definition is in §7 router skeleton** (the
> `_proxy_to_snapshots_metrics` async helper between `get_snapshot`
> and the closing fence at be-plan §7) — that is the code a
> developer copies; this §4.3 narrative is **placement RULE + import
> contract ONLY** (no duplicate definition here). The helper is a
> 3-line private function in `snapshots.py`:
>
> Rationale (LEADER RULING): the proxy is a one-line delegation to
> the canonical `get_snapshot_metrics` handler; keeping it in the
> snapshots router avoids a circular import
> (settings.py → snapshots.py is fine; the reverse would force the
> snapshots router to depend on the settings router for the legacy
> endpoint, which is the wrong coupling direction — the snapshots
> router should be standalone). The provenance comment in the
> handler above is updated to cite the LEADER-RULING placement
> (was: the helper "lives" near the settings handler; now: it is
> defined in `snapshots.py` and imported by `settings.py`).

`FastAPI`'s `deprecated=True` flag ONLY marks the route as deprecated
in the OpenAPI schema (it does NOT emit HTTP response headers — that
would have to be a middleware). The `Deprecation: true`, `Sunset:`, and
`Link:` headers are set **manually** on the `Response` object in the
handler body, copying the project precedent at
`daemon/routers/blueprints.py:551-557` (the deprecated `/initialize`
handler — verified by read). The handler delegates to the same service
call as the new endpoint via a small private helper that **lives in
`daemon/routers/snapshots.py`** (pass 4 amendment blocker #6) and is
imported by `settings.py`; no logic duplication. The `Link` header
points at the successor `/api/snapshots/metrics` route per RFC 8288.

### 4.4 `GET /api/settings/snapshot-create` & `PUT /api/settings/snapshot-create` — **BE scope: API endpoints unchanged**

Stays in `daemon/routers/settings.py:657-691` — **BE scope: these
endpoints are unchanged.** The toggle **UI control relocates** from
the Settings page to the new `/snapshots` page (fe-plan §4.2; sequencing
§4.3 step 7 — the Settings toggle block `html:242-321` is deleted in the
relocation commit). The new page calls `GET` at mount time and
`PUT /api/settings/snapshot-create` on Apply. No router change here;
a regression test pin is added in §8.6.

## 5. File-by-File Change List

> All paths are relative to `/home/nea/ensemble-src-wt-snapshot-uiux/`.

### 5.1 New files

| Path | Purpose | Approx. lines |
|------|---------|----------------|
| `daemon/routers/snapshots.py` | New router (list + detail + metrics) + the `_proxy_to_snapshots_metrics` helper (LEADER RULING, pass 4 amendment blocker #6 — the helper lives here, not in `settings.py`; `settings.py` imports it from this module) | ~225 |
| `daemon/routers/snapshot_schemas.py` | Pydantic response models (`SnapshotListResponse`, `SnapshotResponse`) **PLUS a re-export/alias of `SnapshotUsageMetricsResponse` from `daemon.routers.schemas`** (pass 4 amendment, blocker #4) — the model already lives in the 2136-line catch-all `daemon/routers/schemas.py:1781-1818`; keep it there, just re-export it from this sibling so the new router has a single import surface for the three response models it uses. **`SnapshotListResponse` and `SnapshotResponse` are NEW definitions** in this file; only `SnapshotUsageMetricsResponse` is a re-export. | ~65 |
| `tests/unit/routers/test_snapshots.py` | Router tests via `TestClient` | ~280 |
| `tests/unit/test_snapshot_list_with_filters.py` | Repo-layer generic-list tests (PG + SQLite tag parity) | ~180 |

(Schema is split into a sibling file `snapshot_schemas.py` because
`daemon/routers/schemas.py` is already 2136 lines and is the project's
catch-all for response models; new domain schemas go in their own
sibling — mirrors `daemon/routers/skill_schemas.py`.)

### 5.2 Edited files

| Path | Change | Anchor |
|------|--------|--------|
| `daemon/routers/__init__.py` | **Add one re-export row** — `from .snapshots import router as snapshots_router` (the existing convention at `__init__.py:3-27` exports every router as `*_router`; append the new row in the same alphabetical group). This is the SINGLE import surface for `daemon/api.py` and for `tests/unit/routers/test_snapshots.py` (the test file imports `from daemon.routers import snapshots_router, settings_router`, resolving through this file). | Append after line 27 (the `tmp_images` row); match the `from .X import router as X_router` convention verbatim. |
| `daemon/repositories/snapshot/repository.py` | Add `list_with_filters(...)` method (signature in §6); turn `list_active_by_project` into a 3-line compat wrapper. | `:423-449` (current `list_active_by_project`); add `list_with_filters` right after `:449`. |
| `daemon/api.py` | Add `from daemon.routers import snapshots_router` in the import block (resolves via the `__init__.py` re-export — NOT a direct `.snapshots` import); add `api_router.include_router(snapshots_router)` line in the router-registration block. | Import block: `:108-134`; include block: insert right after `:2991` (settings_router line). |
| `daemon/routers/settings.py` | (a) Add `deprecated=True` + manual Deprecation/Sunset/Link response headers + 1-line proxy handler for `GET /snapshot-usage-metrics` (manual header pattern copied from `daemon/routers/blueprints.py:551-557`). The handler delegates to `_proxy_to_snapshots_metrics`, which is **defined in `daemon/routers/snapshots.py` and IMPORTED here** (LEADER RULING, pass 4 amendment blocker #6 — placement in be-plan §4.3, this row, and §11 T4). (b) **Extend the `fastapi` import with `Response`** (pass 4 amendment, blocker #5 — the deprecated-proxy handler sets `response.headers[...]`, which needs `Response` in the import; precedent: `daemon/routers/blueprints.py:25`). (c) Add regression test pin note in the toggle endpoints' docstring. | Toggle endpoints: `:657-691`; metrics: `:702-738`. |
| `CHANGELOG.md` | Add an entry under the next version's "Unreleased" section noting the new endpoints, the metric relocation, and the deprecation. | Top of file. |

### 5.3 Files explicitly NOT touched

| Path | Why |
|------|-----|
| `daemon/repositories/snapshot/models.py` | Zero schema work (D9, [given] §2). The 18-col model is sufficient; `to_dict()` at `:230-251` is reusable as-is. |
| `daemon/services/snapshot_metrics_service.py` | No logic change. The new router consumes the existing service; the existing settings router still consumes it. |
| `daemon/tools/snapshot_tools.py` | Out of scope — write path is tool-only. No per-agent gating (D9). |
| `daemon/registry.py` | Per-agent `snapshot_enabled` lands in `feature/unify-spawn-tools`, not here. |
| `daemon/services/snapshot_search_service.py:328` | Compat wrapper preserves the call site byte-for-byte. |
| `daemon/routers/blueprints.py` | List-endpoint canon is read-only. The new router borrows the `BlueprintListResponse` shape but does not import it (the new `SnapshotListResponse` is its own model to keep coupling at the shape level, not the symbol level). |
| `frontend/` | BE-only plan. The FE plan lives in a sibling file in `.agents/shared/planning/snapshot-uiux/`. |
| `daemon/migrations/` | No migration — D9 + [given] §2 "ZERO schema work needed". |

## 6. Repository Method Spec

`list_with_filters` is the v1 generic-list method. It returns
`(items, total)` so the router can build the `{items, total}` envelope
without a separate count query. The pipeline is **single-query + Python
slice** — see §6.2 for the pinned order (SQL WHERE+ORDER BY **unpaginated**
→ `filter_by_tags` post-pass → `total = len(filtered)` → Python slice
`[offset:offset+limit]`).

### 6.1 Signature

```python
def list_with_filters(
    self,
    *,
    project_id: str | None = None,
    agent_id: str | None = None,
    statuses: Sequence[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    tags: Sequence[str] | None = None,
    tag_mode: Literal["all", "any"] = "all",
    sort: Literal[
        "created_at_desc", "created_at_asc",
        "title_asc", "title_desc",
        "status_asc", "status_desc",
        "project_id_asc", "project_id_desc",
    ] = "created_at_desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Snapshot], int]:
    """Generic filterable list for the HTTP /api/snapshots surface.

    Returns (items, total). Both PG and SQLite are supported; the
    tag filter is applied via the existing ``filter_by_tags`` post-
    pass to keep PG/SQLite containment parity (see §6.2 pipeline step 2).

    Each param mirrors a single filter dimension on the list
    endpoint. None / empty-list / empty-string means "no filter".

    The total is the post-filter count (the row count after every
    filter is applied, per §6.2 pipeline step 3) so the FE can
    render pagination correctly. The pagination itself happens via
    Python slice in §6.2 pipeline step 4 — NOT via SQL .limit() /
    .offset() — so the tag post-pass can still narrow the candidate
    set.
    """
```

### 6.2 Param → pipeline mapping (single pinned story)

**Pinned pipeline order** (every other implementation MUST match):

```
1. SQL:   SELECT … FROM snapshots
          WHERE <project_id?> AND <agent_id?> AND <statuses?>
                AND <created_after?> AND <created_before?>
          ORDER BY <sort>
          -- (no SQL .limit() / no SQL .offset() — UNPAGINATED)
2. POST:  filter_by_tags(candidates, tags, tag_mode)
          (Python-side; PG path inside filter_by_tags uses JSONB
           contains; SQLite path uses a Python scan — both dialects
           produce the same filtered list)
3. COUNT: total = len(filtered)   # post-filter list length, not SQL COUNT(*)
4. SLICE: items = filtered[offset : offset + limit]
5. RETURN: (items, total)
```

**Rationale.** Post-pass tag filtering (PG/SQLite parity via the
existing `filter_by_tags`, `daemon/repositories/snapshot/repository.py:493-608`)
means the SQL query cannot page — pagination MUST happen on the
filtered result. `total` is the length of that filtered list, not a
SQL `COUNT(*)`, so the FE pagination math is consistent with what
the next slice will yield.

**Param → effect table:**

| Param | Effect in pipeline step |
|-------|--------------------------|
| `project_id` | Step 1 — `WHERE col(Snapshot.project_id) == :project_id` (omit when `None`) |
| `agent_id` | Step 1 — `WHERE col(Snapshot.created_by_agent_id) == :agent_id` (omit when `None`) |
| `statuses` | Step 1 — `WHERE col(Snapshot.status).in_(list(statuses))` (omit when `None` or empty) |
| `created_after` | Step 1 — `WHERE col(Snapshot.created_at) >= :created_after` (omit when `None`; ISO-TEXT lexicographic) |
| `created_before` | Step 1 — `WHERE col(Snapshot.created_at) <= :created_before` (omit when `None`) |
| `tags` | Step 2 — passed to `filter_by_tags(candidates, tags, tag_mode)`; ignored when `[]` (passthrough) |
| `tag_mode` | Step 2 — passed through to `filter_by_tags` (no-op when `tags` empty) |
| `sort` | Step 1 — `ORDER BY` per the allow-list table in §4.1 |
| `limit` | **Step 4 — Python slice end (`offset + limit`)**. NOT SQL `.limit()`. |
| `offset` | **Step 4 — Python slice start.** NOT SQL `.offset()`. |

**Tag filter (PG vs SQLite parity):** the existing
`filter_by_tags` (`daemon/repositories/snapshot/repository.py:493-608`)
already handles both dialects:

* **PG path** (`:533-559`): `cast(col(Snapshot.domain_tags), JSONB).contains([...])`
  for `all`, OR-of-single-tag for `any`. Drifts-pinned by
  `TestTagFilterPGDriftPin` in `tests/unit/test_snapshot_repository.py`.
* **SQLite path** (`:561-568`): Python-side scan over the JSON arrays.
  Acceptable at v1 volumes (rider (f)).

The new method calls `filter_by_tags` AFTER the SQL-level filters
narrow the candidate set, so the Python scan is bounded.

**Total count story (PINNED):** `total = len(filtered)` — the
post-filter list length, computed from the in-memory list returned by
`filter_by_tags`. **NOT** a SQL `COUNT(*)`, **NOT** a window-function
`COUNT(*) OVER ()` (window-function approach is rejected because
tag filtering happens after SQL and a window-function total cannot
account for that post-pass). The `total` is identical for every row
of the result because it is a single integer computed once from the
filtered list — the slice in step 4 does not affect `total`.

**Out-of-range `limit`/`offset`:** FastAPI rejects with **422** at
the router layer via `Query(ge=1, le=200)` / `Query(ge=0)` (D-6; case
21). The repo method's Python slice silently clamps defensive
overflow (`filtered[offset:]` for `offset > len(filtered)` returns
`[]`, and a too-large `limit` returns whatever is left), but the
repo never sees the out-of-range value on the HTTP path — the router
rejects first.

### 6.3 Compat wrapper

```python
def list_active_by_project(
    self,
    project_id: str,
    limit: int = 50,
) -> list[Snapshot]:
    """Compat wrapper — delegates to list_with_filters.

    The spawn-hot WARM path (snapshot_search_service.py:328) and
    the unit-test fakes (tests/unit/test_snapshot_search_service.py:141)
    still call this signature. Keep byte-compatible.
    """
    items, _total = self.list_with_filters(
        project_id=project_id,
        statuses=(SNAPSHOT_STATUS_ACTIVE,),
        limit=limit,
    )
    return items
```

Three lines, one behaviour delta from the old impl: the new method
returns `(items, total)` and the wrapper discards `total`. The
search service only ever read `items`; the fakes only ever asserted
on items. Zero observable change at every call site.

### 6.4 Tag filter edge cases

* `tags=None` or `tags=[]` — pass-through (no filter), total
  reflects the SQL-only-filter count.
* `tag_mode="all"` and one tag — equivalent to `tag_mode="any"` (the
  `all` path is just `contains([t])`). The PG path collapses
  correctly; SQLite iterates the same.
* `tag_mode` outside `{"all", "any"}` — `filter_by_tags` raises
  `ValueError` (`:525-528`); the router catches it and returns
  400. The router's `Query(pattern=...)` should normally catch
  this first (422); the `ValueError` is a belt for direct
  repo callers.

## 7. Router Skeleton

> **Implementation-ready code shape** — a developer copies this and
> fills in. The docstrings and error maps are the contract.

```python
# daemon/routers/snapshots.py
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlmodel import col

from daemon.repositories.snapshot.models import SNAPSHOT_STATUSES, Snapshot
from daemon.routers.schemas import SnapshotUsageMetricsResponse
from .snapshot_schemas import SnapshotListResponse, SnapshotResponse

# ── Import chain (per amendment pass 3, fix #7) ─────────────────────
# This module is imported by `daemon/routers/__init__.py` via the row
# `from .snapshots import router as snapshots_router` (the convention
# at __init__.py:3-27; every router is re-exported as `*_router`).
# The single import surface for downstream consumers is
# `from daemon.routers import snapshots_router` — used by:
#   • daemon/api.py — registration: `api_router.include_router(snapshots_router)`
#   • tests/unit/routers/test_snapshots.py — harness import in §8.1
# Do NOT add a direct `from .snapshots import router` in api.py; the
# package re-export is the single source of truth.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

_ALLOWED_SORT_KEYS = {
    "created_at_desc": col(Snapshot.created_at).desc(),
    "created_at_asc":  col(Snapshot.created_at).asc(),
    "title_asc":       col(Snapshot.title).asc(),
    "title_desc":      col(Snapshot.title).desc(),
    "status_asc":      col(Snapshot.status).asc(),
    "status_desc":     col(Snapshot.status).desc(),
    "project_id_asc":  col(Snapshot.project_id).asc(),
    "project_id_desc": col(Snapshot.project_id).desc(),
}
_SORT_PATTERN = "^(" + "|".join(sorted(_ALLOWED_SORT_KEYS.keys())) + ")$"


def _validate_uuid(value: str, *, field: str) -> None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400, detail=f"{field} must be a valid UUID",
        )


def _to_http_500(exc: Exception, endpoint: str) -> HTTPException:
    logger.exception("[SnapshotsRouter] %s failed: %s", endpoint, exc)
    return HTTPException(
        status_code=500,
        detail={"error": str(exc), "endpoint": endpoint},
    )


@router.get("", response_model=SnapshotListResponse)
async def list_snapshots(
    request: Request,
    project_id: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    tags: list[str] = Query(default_factory=list),
    tag_mode: str = Query(default="all", pattern="^(all|any)$"),
    status: list[str] = Query(default_factory=list),  # D5: default = all 5
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    sort: str = Query(default="created_at_desc", pattern=_SORT_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SnapshotListResponse:
    """List snapshots, filterable. See be-plan.md §4.1."""
    if project_id is not None:
        _validate_uuid(project_id, field="project_id")
    if not status:
        status = list(SNAPSHOT_STATUSES)
    else:
        unknown = [s for s in status if s not in SNAPSHOT_STATUSES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"error": f"unknown status values: {unknown}",
                        "allowed": sorted(SNAPSHOT_STATUSES)},
            )
    for label, value in (("created_after", created_after),
                         ("created_before", created_before)):
        if value is not None:
            try:
                from datetime import datetime
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"{label} must be ISO-8601 ({exc})"},
                )
    # Double-clamp idiom (daemon/routers/skills.py:1235-1240)
    effective_limit = min(int(limit), 200)
    effective_offset = max(int(offset), 0)

    manager = request.app.state.manager
    repo = manager._snapshot_repo
    try:
        items, total = await asyncio.to_thread(
            repo.list_with_filters,
            project_id=project_id,
            agent_id=agent,
            statuses=status,
            created_after=created_after,
            created_before=created_before,
            tags=tags,
            tag_mode=tag_mode,
            sort=sort,
            limit=effective_limit,
            offset=effective_offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except Exception as exc:
        raise _to_http_500(exc, "list_snapshots")

    return SnapshotListResponse(
        items=[_to_list_item(s) for s in items],
        total=int(total),
    )


def _to_list_item(s: Any) -> dict[str, Any]:
    """to_dict() minus digest (D7) and minus task_summary (A-2) — list payloads stay lean."""
    d = s.to_dict() if hasattr(s, "to_dict") else s
    d.pop("digest", None)
    d.pop("task_summary", None)  # A-2 (sequencing §1.3, binding) — detail endpoint still carries it
    return d


@router.get("/metrics", response_model=SnapshotUsageMetricsResponse)
async def get_snapshot_metrics(request: Request) -> SnapshotUsageMetricsResponse:
    """R16 monitoring counters — see be-plan.md §4.3.

    Replaces ``GET /api/settings/snapshot-usage-metrics``; the old
    endpoint is kept as a deprecated re-export for one release.
    """
    manager = request.app.state.manager
    service = manager._snapshot_metrics_service  # may be None pre-init
    if service is None:
        return SnapshotUsageMetricsResponse(
            capture_counts={}, spawn_counts_per_snapshot=[],
        )
    try:
        data = await service.surface()
    except Exception as exc:  # fail-soft per settings.py:737
        logger.warning("[SnapshotsRouter] metrics degraded: %s", exc)
        return SnapshotUsageMetricsResponse(
            capture_counts={}, spawn_counts_per_snapshot=[],
        )
    return SnapshotUsageMetricsResponse(
        capture_counts=data.get("capture_counts") or {},
        spawn_counts_per_snapshot=data.get("spawn_counts_per_snapshot") or [],
    )


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot(
    request: Request,
    snapshot_id: str,
    include: str = Query(default=""),
) -> SnapshotResponse:
    """Single snapshot by id — see be-plan.md §4.2.

    ``?include=digest`` opts in to the unbounded JSONB digest.
    """
    if not snapshot_id or not snapshot_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "snapshot_id is required"},
        )
    _validate_uuid(snapshot_id, field="snapshot_id")
    manager = request.app.state.manager
    repo = manager._snapshot_repo
    try:
        row = await asyncio.to_thread(repo.get, snapshot_id)
    except Exception as exc:
        raise _to_http_500(exc, "get_snapshot")
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Snapshot not found",
                    "snapshot_id": snapshot_id},
        )
    d = row.to_dict() if hasattr(row, "to_dict") else row
    if "digest" not in (include or "").split(","):
        d["digest"] = {}  # D7: present-but-empty, not absent
    return SnapshotResponse(**d)


# ── Helper for the deprecated metrics proxy (LEADER RULING, pass 4
#    amendment blocker #6 — placement rule see §4.3; this is the
#    CANONICAL DEFINITION — `settings.py` IMPORTS this symbol) ───
async def _proxy_to_snapshots_metrics(request: Request) -> SnapshotUsageMetricsResponse:
    """Deprecated-path proxy — delegates to the canonical
    `get_snapshot_metrics` handler in this module. Imported by
    `daemon/routers/settings.py` for the legacy endpoint.

    Placement rule (LEADER RULING, pass 4 amendment blocker #6):
    this helper lives in `daemon/routers/snapshots.py`, not in
    `settings.py`, to avoid the circular import that would result
    if `settings.py → snapshots.py` were reversed. The deprecated
    proxy handler at `daemon/routers/settings.py:702-738` calls
    `await _proxy_to_snapshots_metrics(request)` after setting the
    `Deprecation` / `Sunset` / `Link` response headers — the symbol
    resolves via the import added at the top of `settings.py` next
    to the existing snapshots-router import:

        from daemon.routers.snapshots import _proxy_to_snapshots_metrics

    The settings.py import surface (added in §5.2 row) is the
    single source of truth for the deprecated-proxy wiring.
    """
    return await get_snapshot_metrics(request)
```

> **Note on `manager._snapshot_repo` access:** the manager exposes
> `_snapshot_repo` as a "private" attribute (single underscore). The
> project precedent at `daemon/routers/blueprints.py:336` does the
> same: `manager._blueprint_repo`. This is the project-house pattern
> for read endpoints; the leading underscore is convention, not
> enforcement. A follow-up could promote these to a public
> `get_snapshot_repo()` accessor — not in v1 scope.

## 8. Test Plan

### 8.1 `tests/unit/routers/test_snapshots.py` (new) — `TestClient` tests

> **Harness rebase (amendment pass 3, fix #6).** Three corrections
> to the harness shape, derived from the verified anchors the
> dispatcher cited:
> 1. **No `PRAGMA foreign_keys=ON`** — `tests/unit/test_snapshot_repository.py:60-76`
>    deliberately omits it (StaticPool + `SQLModel.metadata.create_all`,
>    no pragma). The snapshots tables have no FK constraints that need
>    enabling for v1; copying the missions-router pragma would force
>    the snapshots test surface to satisfy FKs that other repo tests
>    ignore. Drop the line.
> 2. **Mount BOTH `snapshots_router` AND `settings_router`** in the
>    client fixture. Cases 22 (`test_deprecated_old_metrics_path`) and
>    23 (`test_settings_toggle_regression`) hit `/api/settings/*` paths
>    that live on `settings_router`; without that mount the fixture
>    returns 404 for every settings path.
> 3. **Case 23 (`test_settings_toggle_regression`)** hits
>    `GET /api/settings/snapshot-create` and `PUT /api/settings/snapshot-create`.
>    Both call `get_project_repository()` (`daemon/routers/settings.py:93-96`)
>    which raises 503 when `_project_repo is None`. The harness MUST
>    seed a system-default project row AND wire
>    `set_project_repository(repo)` (the setter at
>    `daemon/routers/settings.py:99-101`) before the test fires, else
>    the request 503s. The seeding pattern is `ProjectRepository(engine)`
>    + `repo.create_default_system_project()` (or equivalent — verify
>    exact method name against the live source at implementation time;
>    the contract is: one row in `projects` with `project_id = '<system-default-uuid>'`
>    before the test request fires).
>
> Implementation-ready harness (corrected shape):

```python
@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    NO ``PRAGMA foreign_keys=ON`` — the existing repo test
    ``tests/unit/test_snapshot_repository.py:60-76`` deliberately
    omits it (StaticPool + create_all, no pragma). The snapshots
    tables have no FK constraints that require enforcement for v1;
    copying the missions-router pragma would force the snapshots
    test surface to satisfy FKs other repo tests ignore.
    """
    db_path = tmp_path / "snapshots-api-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        # NOTE: NO foreign_keys pragma — matches the existing
        # test_snapshot_repository.py:60-76 engine fixture. Verified.
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def project_repo(engine: Engine):
    """Project repository wired to the in-memory engine.

    Used by case 23 (settings toggle regression) which hits
    GET/PUT /api/settings/snapshot-create — that handler calls
    ``get_project_repository()`` (settings.py:93-96), which raises
    503 when ``_project_repo is None``. The fixture seeds the
    system-default project row AND calls
    ``set_project_repository(repo)`` (settings.py:99-101) so the
    dependency resolves. The seed is one row in ``projects`` with
    ``project_id = '<system-default-uuid>'`` — exact method name
    (``create_default_system_project`` or equivalent) is verified at
    implementation time against the live source; the contract is
    "one project row exists before the test fires".
    """
    from daemon.repositories.project.repository import ProjectRepository
    from daemon.routers.settings import set_project_repository

    repo = ProjectRepository(engine)
    repo.create_default_system_project()  # exact method name verified at impl time
    set_project_repository(repo)
    try:
        yield repo
    finally:
        set_project_repository(None)  # teardown — avoid global state leak


@pytest.fixture
def client(snapshot_repo, snapshot_metrics_service, project_repo) -> TestClient:
    """TestClient with BOTH routers mounted under /api.

    The fixture mounts ``snapshots_router`` (for the new endpoints)
    AND ``settings_router`` (for cases 22-23, which hit
    ``/api/settings/*``). Mirrors the api.py registration
    (``/api`` parent prefix) and the manager wiring
    (``app.state.manager._snapshot_repo``,
    ``app.state.manager._snapshot_metrics_service``).

    The ``project_repo`` fixture (above) MUST run before this fixture
    so that ``_project_repo`` is wired globally before any
    ``get_project_repository()`` call inside the settings router
    fires — pytest resolves fixtures in dependency order.
    """
    app = FastAPI()
    # Stub manager — exposes the two attributes the router depends on
    # (verified at be-plan §7 line 567 + 609).
    class _StubManager:
        _snapshot_repo = snapshot_repo
        _snapshot_metrics_service = snapshot_metrics_service
    app.state.manager = _StubManager()
    app.include_router(snapshots_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")  # cases 22-23 hit /api/settings/*
    return TestClient(app)
```

**Key shape (corrected):**

1. A real file-backed SQLite engine via `create_engine(..., poolclass=NullPool)`
   + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` (NO `foreign_keys`
   pragma — the existing `tests/unit/test_snapshot_repository.py:60-76`
   deliberately omits it; verified).
2. `SQLModel.metadata.create_all(eng)` to create the schema (the
   snapshot tables register on import; one `import daemon.repositories.snapshot.models`
   line in the test module is required).
3. A `project_repo` fixture (cases 22-23) seeds the system-default
   project row + calls `set_project_repository(repo)` so
   `get_project_repository()` (settings.py:93-96) does NOT 503.
4. A `client` fixture that mounts **BOTH** `snapshots_router` AND
   `settings_router` into a `fastapi.FastAPI()` test app with
   `prefix="/api"` (mirrors `daemon.api` registration at the same
   prefix) and exposes `app.state.manager` as a tiny stub carrying
   `_snapshot_repo` and `_snapshot_metrics_service` (the two
   attributes the router reads at `be-plan §7 line 567 + 609`).
5. Tests call `TestClient(app).get("/api/snapshots...")` and
   `TestClient(app).get("/api/settings/snapshot-create")` etc.
   directly. No `daemon._engine_for_tests` symbol exists (verified by
   `grep -r "_engine_for_tests" daemon/` — zero hits); the engine is
   owned by the test fixture, not by the daemon.

**Case list:**

| # | Name | What it pins |
|---|------|--------------|
| 1 | `test_list_envelope_default` | Empty repo → `{"items": [], "total": 0}` (200). |
| 2 | `test_list_pagination` | 5 rows seeded; `?limit=2&offset=2` → 2 items, `total=5`. |
| 3 | `test_list_default_sort_created_at_desc` | 3 rows inserted in known order → reverse-order response. |
| 4 | `test_list_sort_allowlist_validation` | `?sort=garbage` → 422. All 8 allowed values accepted. |
| 5 | `test_list_filter_project_id` | Two projects seeded → `?project_id=p1` returns only p1 rows. |
| 6 | `test_list_filter_invalid_project_id` | `?project_id=not-a-uuid` → 400, body `{"detail": "project_id must be a valid UUID"}`. |
| 7 | `test_list_filter_agent_exact_match` | `?agent=coder` returns only coder rows. `?agent=code` returns 0 (D4 — exact match). |
| 8 | `test_list_filter_status_multi` | `?status=active&status=running` returns both. `?status=garbage` → 422. |
| 9 | `test_list_filter_status_default_all` | Omit `status` → all 5 statuses surface (D5). |
| 10 | `test_list_filter_tags_all_mode` | `?tags=kind:impl&tag_mode=all` → row has BOTH. |
| 11 | `test_list_filter_tags_any_mode` | `?tags=kind:impl&tags=subsystem:foo&tag_mode=any` → row has at least one. |
| 12 | `test_list_filter_tags_repeat_param` | FastAPI's `?tags=a&tags=b` encoding accepted; both passed through. |
| 13 | `test_list_filter_created_window` | `?created_after=...&created_before=...` narrows; invalid ISO → 400. |
| 14 | `test_list_digest_excluded` | List payload has NO `digest` key (or `digest={}`). Item count of payload bytes is bounded. |
| 15 | `test_detail_default_no_digest` | `GET /api/snapshots/{id}` returns `digest={}`. |
| 16 | `test_detail_with_include_digest` | `GET /api/snapshots/{id}?include=digest` returns the full digest. |
| 17 | `test_detail_404_shape` | Unknown id → 404 with `{"detail": {"error": "Snapshot not found", "snapshot_id": "..."}}`. |
| 18 | `test_detail_invalid_uuid` | Blank / non-UUID path → 400. |
| 19 | `test_metrics_endpoint` | Seed 1 capture counter + 1 spawn counter; `GET /metrics` returns both. |
| 20 | `test_metrics_degraded_engine_none` | `manager._snapshot_metrics_service = None` → 200 empty shape, NOT 503. |
| 21 | `test_limit_offset_422_validation` | **FastAPI validation rejects out-of-range values with 422** (per D-6 `Query(ge=1, le=200)` and `Query(ge=0)`): `?limit=10000` → **422**; `?offset=-5` → **422**. The double-clamp idiom (`effective_limit = min(int(limit), 200)`, `effective_offset = max(int(offset), 0)`) remains in code as a **defense-in-depth belt for direct repo callers** — NOT a 200-success path. Validates: Pydantic + FastAPI `Query(ge=…, le=…)` fires FIRST (422), the runtime clamp never runs on an HTTP path that already rejected. |
| 22 | `test_deprecated_old_metrics_path` | `GET /api/settings/snapshot-usage-metrics` → 200 + `Deprecation: true` + `Sunset:` + `Link:` response headers (set manually in the handler body per `daemon/routers/blueprints.py:551-557`). |
| 23 | `test_settings_toggle_regression` | `GET /api/settings/snapshot-create` + `PUT /api/settings/snapshot-create` still work (regression pin). |
| 24 | `test_list_excludes_task_summary` | List payload has NO `task_summary` key (addendum A-2); the detail endpoint still carries it. |
| 25 | `test_metrics_degraded_surface_raises` | `manager._snapshot_metrics_service` is a service whose `.surface()` RAISES → endpoint returns 200 with the empty shape (`capture_counts={}`, `spawn_counts_per_snapshot=[]`), NOT 500 (fail-soft, mirrors the `settings.py:737` precedent; complements case 20's None-service branch). |
| 26 | `test_list_sort_created_at_desc_1s_apart` | Two rows seeded 1 second apart (ISO-8601 lexicographic strings) → `created_at_desc` response is chronological; pins R4's zero-pad/timezone-invariant claim (amendment finding #8). |

### 8.2 `tests/unit/test_snapshot_list_with_filters.py` (new) — repo-layer tests

Follows the convention of `tests/unit/test_snapshot_repository.py`:
SQLite in-memory, `_snapshot(...)` factory, `SnapshotRepository(engine)`.

| # | Name | What it pins |
|---|------|--------------|
| 1 | `test_filters_compose` | project_id + agent + statuses + age window all apply; total reflects post-filter count. |
| 2 | `test_default_sort_created_at_desc` | No `sort` arg → newest first. |
| 3 | `test_each_sort_key` | All 8 sort keys produce the expected ordering. |
| 4 | `test_pagination_offset` | `limit=2, offset=2` returns rows 3–4 of 5. |
| 5 | `test_tags_all_mode_sqlite` | SQLite path: `tag_mode='all'` intersects correctly. |
| 6 | `test_tags_any_mode_sqlite` | SQLite path: `tag_mode='any'` unions correctly. |
| 7 | `test_tags_empty_passthrough` | `tags=[]` → all candidates. |
| 8 | `test_pg_drift_pin_for_filter_with_tags` | Build the candidate query the way the new method would, assert the rendered SQL contains `@>` (NOT `LIKE`). Mirrors `TestTagFilterPGDriftPin` at `tests/unit/test_snapshot_repository.py`. |
| 9 | `test_compat_wrapper_list_active_by_project` | The old `list_active_by_project` returns the same rows it did before; `snapshot_search_service` call site is byte-compatible. |
| 10 | `test_value_error_on_bad_tag_mode` | `tag_mode='xor'` → `ValueError`; the router's pattern check fires first when the call comes from HTTP, but the repo's own belt is non-negotiable. |

### 8.3 `tests/unit/test_snapshot_repository.py` (extend)

Add a `TestListWithFilters` class at the end (after the existing
`TestReads` and tag-filter classes). Reuse `_snapshot(...)` and
`repo` fixtures. Cases 1–7 from §8.2 above.

### 8.4 `tests/unit/test_snapshot_search_service.py` (extend — minimal)

One test: a regression pin that the search service's existing fake
(`FakeSnapshotRepo.list_active_by_project` at
`tests/unit/test_snapshot_search_service.py:141`) still works
unchanged. If the compat-wrapper is correct, no change is needed;
this test exists to fail loud if someone breaks the duck-typed
contract.

### 8.5 `tests/integration/test_skill_bank_router.py`-style harness (re-use)

For the router tests in §8.1, the project's standard is
`fastapi.testclient.TestClient` against the actual `daemon.api.create_app`
app, with a fixture that overrides `app.state.manager` with a
test-double manager holding a SQLite-backed `_snapshot_repo` and
`_snapshot_metrics_service`. The existing missions-router test
(`tests/unit/routers/test_missions_api.py`) is the closest
precedent — same structure, same fixture pattern.

### 8.6 Regression check — **case 23 of §8.1, NOT a new case**

The settings-toggle regression pin lives **inline as router case 23** in
§8.1 (`test_settings_toggle_regression`). This is intentional and pins
the double-count identity for the test-rollup math:

- §8.1 has 26 router cases (1–26); case 23 IS the settings toggle regression.
- §8.6 is NOT a separate case — it documents where case 23 lives and what it pins.

**Identity note (mandatory in any test-count rollup):**
**26 router cases (case 23 = settings toggle regression pin) + 10
new-repo + 7 repo-ext + 1 search-ext = 44 unique BE cases.** Any
earlier figure of "33", "36", "45", or "double-count" was the bug
being fixed in amendment pass 3; the single package-wide number is
**44 unique**.

The harness requirements that make case 23 work are documented in
the §8.1 harness rebase (amendment pass 3, fix #6): the `client`
fixture must mount BOTH `snapshots_router` AND `settings_router`,
and the `project_repo` fixture must seed a system-default project
row + call `set_project_repository(repo)` (settings.py:93-96, :99-101)
so `get_project_repository()` does NOT 503.

## 9. Risks + Mitigations

| ID | Risk | Impact | Likelihood | Mitigation |
|----|------|--------|------------|------------|
| R1 | `digest` payload weight on list endpoint | High (page slowness / browser memory) | High (default would be 25k tokens/row) | D7 — strip `digest` at the router layer; detail endpoint opt-in via `?include=digest`. |
| R2 | Multi-value tag encoding mismatch between FE and BE | High (silent filter empty) | Medium | D3 — declare `repeat-param` as the FE contract in the plan; pin via test case 12. |
| R3 | PG vs SQLite JSONB tag-parity drift | High (PG `LIKE` regression: tested 2026-08 incident per `filter_by_tags` docstring) | Low (existing `TestTagFilterPGDriftPin` covers the SQL build) | Reuse the `_build_filter_by_tags_pg_stmt` static method (`:570-608`); add a NEW drift-pin in the repo tests that asserts the post-filter SQL contains `@>`, not `LIKE`. |
| R4 | `created_at` ISO-TEXT sorting pitfall (locale / zero-pad) | Medium (sort order wrong on malformed rows) | Low (the schema is fixed; only inserts from `_now_iso` exist) | Document in the plan that `_now_iso()` is the only writer; lexicographic ISO-8601 with timezone = chronological. Add a test that asserts ordering of rows seeded 1s apart. |
| R5 | Index adequacy for new filter combinations (agent / age / tag) | Medium (full scans at scale) | Low (v1 volumes are small; pilot scale) | Existing `(project_id, status)` index covers the dominant case (D8-permanent project-scoped search); agent / age filters are bounded by the `limit`. Document as a Phase-2 backlog item (no index in v1). |
| R6 | Compat-wrapper drift on `list_active_by_project` breaks `snapshot_search_service` | High (spawn-hot WARM path) | Low (wrapper is 3 lines) | §6.3 wrapper is byte-compatible; tests/unit/test_snapshot_repository.py:367 already pins the old behaviour; add §8.2 case 9. |
| R7 | Deprecation of `/api/settings/snapshot-usage-metrics` breaks external callers | Medium (CLI / curl dashboards) | Low (only 1 in-repo caller — FE — moves in lockstep) | Keep old endpoint as 1-line deprecated re-export; `Deprecation: true` + `Sunset:` + `Link:` headers set **manually** in the handler body (FastAPI's `deprecated=True` only marks the OpenAPI schema — verified by read of `daemon/routers/blueprints.py:551-557`); CHANGELOG entry notes removal in a follow-up. |
| R8 | `manager._snapshot_repo` private-attribute access | Low (project convention) | Low (matches blueprints.py:336) | Documented as project-house pattern; follow-up could promote to public accessor. |
| R9 | UUID-validation pattern diverges from `_validate_project_id` | Low (inconsistent error bodies) | Low (the dispatcher asked to follow the existing 404 body shape) | Reuse the `uuid.UUID(...)` try/except idiom; copy the 400 detail string verbatim. |
| R10 | `created_after` / `created_before` lexicographic compare vs timezone offset | Medium (rows near TZ boundaries sort wrong) | Low (all timestamps are `+00:00` UTC per `_now_iso`) | Plan documents the `+00:00` invariant; a test seeds two rows 1s apart and asserts the order. |

## 10. Out of Scope v1 / Follow-ups

These are explicitly **not** in this plan; they ride a follow-up
commission. Each line is sized for a separate plan file.

- **HTTP write ops on snapshots** — DELETE, archive (logical soft
  delete), and supersede. The R12 `create-mints-successor` semantics
  (`daemon/repositories/snapshot/repository.py:112-203`) suggest a
  `POST /api/snapshots/{id}/archive` and `POST /api/snapshots/{id}/supersede`
  pair; out of v1.
- **`size_bytes` dimension** — new column on `snapshots` + an
  index. The Digest size is bounded by the ~25k-token injection
  ceiling (D6 context) but currently unmeasured. Add a migration
  + a sort/filter dimension in a Phase-2 plan.
- **`last_used_at` on `snapshots`** — currently the warm-count is
  the only signal. A `last_used_at` denormalized column would let
  the FE show "last used 3 days ago" without a join. Phase-2.
- **Auth gating** — no per-user auth today. When the project adds
  per-project auth, the list endpoint needs an `actor`-scoped
  read filter.
- **Per-agent `snapshot_enabled` gating** — lands in
  `feature/unify-spawn-tools`. Coupling point:
  `daemon/registry.py:298-301`. Once that branch lands, add a
  filter param `?enabled_only=true` (or similar) here.
- **GIN index on `domain_tags` (PG)** — rider (f) deferred
  (`daemon/repositories/snapshot/models.py:126-140`). Add a PG-only
  migration in a follow-up; SQLite keeps the Python scan.
- **Project-wide snapshot count badge** — the FE page may want a
  total-by-status badge. Compute via the existing
  `count_by_project` (`daemon/repositories/snapshot/repository.py:481-489`)
  + a new group-by-status method; out of v1.

## 11. Task Breakdown w/ Estimates

> Estimates are for one engineer, single-threaded, after reading
> this plan. BE+FE together fit one overnight implementation sitting
> per the dispatch's framing.

| # | Task | File(s) | Hours | Notes |
|---|------|---------|-------|-------|
| T1 | Add `list_with_filters(...)` + compat wrapper | `daemon/repositories/snapshot/repository.py` | 2.0 | Signature in §6.1; SQL mapping in §6.2; wrapper in §6.3. |
| T2 | Pydantic schemas | `daemon/routers/snapshot_schemas.py` (new) | 0.5 | `SnapshotListResponse`, `SnapshotResponse` (subset of `to_dict()` minus `digest` **and `task_summary`** for the list item — addendum A-2). |
| T3 | Router module | `daemon/routers/snapshots.py` (new) | 3.0 | Skeleton in §7; full error map. |
| T4 | Metrics relocation + deprecation | `daemon/routers/snapshots.py`, `daemon/routers/settings.py` | 1.0 | (a) The `_proxy_to_snapshots_metrics` helper is **defined in `daemon/routers/snapshots.py`** (LEADER RULING, pass 4 amendment blocker #6 — see §4.3 old-endpoint block, §5.2 settings.py edit row) and is a 3-line private function that delegates to the canonical `get_snapshot_metrics` handler in the same file. (b) `daemon/routers/settings.py` IMPORTS it: `from daemon.routers.snapshots import _proxy_to_snapshots_metrics` (added at the top of `settings.py` next to the existing `from .snapshots import router as snapshots_router` import if present, or alongside the other settings.py module imports). (c) The deprecated proxy handler in `settings.py` uses the new `Response` import (pass 4 amendment blocker #5 — `from fastapi import …, Response, …`, precedent: `daemon/routers/blueprints.py:25`) for the manual `Deprecation` / `Sunset` / `Link` headers. FastAPI's `deprecated=True` only marks the OpenAPI schema (verified by read of `daemon/routers/blueprints.py:551-557`). |
| T5 | API registration | `daemon/api.py` | 0.25 | Import + `include_router` after `:2991`. |
| T6 | Repo-layer tests | `tests/unit/test_snapshot_list_with_filters.py` (new) + `tests/unit/test_snapshot_repository.py` (extend) | 2.0 | 10 cases in §8.2. |
| T7 | Router tests | `tests/unit/routers/test_snapshots.py` (new) | 2.0 | 26 cases in §8.1 (23 original + amendment pins #24-26). |
| T8 | Settings-router regression pin | `tests/unit/routers/test_snapshots.py` (case 23) | 0.25 | One test. |
| T9 | CHANGELOG + ADR note | `CHANGELOG.md` | 0.25 | New entry under "Unreleased". |
| T10 | Manual smoke: run daemon, hit endpoints with curl, verify FE happy-path | local | 0.5 | 3 endpoints × 2 happy + 1 sad path. |
| **BE total** | | | **~12 hours** | |

**Combined with FE** (T-FE list is OUT of this plan; sized for the
dispatch's "one overnight" framing):

- FE service: `frontend/src/app/services/snapshots.service.ts` —
  ~2 hours.
- FE page: list view with filter chips + detail drawer with `?include=digest`
  toggle — ~6 hours.
- FE wiring (route, navigation entry, e2e) — ~2 hours.
- **FE total: ~10 hours.**

**Combined BE+FE: ~22 hours** = one engineer for one long sitting,
or a pair (BE serialises FE) for ~12 hours wall-clock. The dispatch's
"overnight" framing implies serialised single-engineer execution.

## 12. Coupling Notes (vs `feature/unify-spawn-tools`)

> The dispatch's coupling rules: this branch is disjoint from
> `feature/unify-spawn-tools` on the BE side. The expected
> touchpoint overlap is `frontend/src/app/.../settings.component.html`
> only.

**BE-side overlap: one shared file — `daemon/routers/settings.py`.**
*(Amended 2026-10-05, amendment finding #9: the original "zero / No
file or line is shared" claim below was disproven by a verified
`git -C .../ensemble-src-wt-unify-spawn-tools diff -U0` — their dirty
tree DOES modify `daemon/routers/settings.py` with two hunks:
`@@ -649` (docstring) and `@@ -713,3 → 715,4` adjacent to
`get_snapshot_usage_metrics` — the exact handler this plan deprecates.
Auto-merge is expected; re-verify pre-merge. All other BE files below
remain disjoint.)* Verified by inspection — this plan
touches `daemon/routers/snapshots.py` (new), `daemon/routers/settings.py`
(deprecated proxy only — pure additive, no semantics change),
`daemon/repositories/snapshot/repository.py` (one new method + 3-line
compat wrapper). The unify-spawn-tools BE side touches
`daemon/tools/snapshot_tools.py:917+`, `daemon/tools/instance.py:2092`,
`_tool_registry.py`, `daemon/registry.py:298-301`. Apart from the
`settings.py` hunks above, **no file or line is shared** — and
`daemon/repositories/snapshot/models.py`,
`daemon/services/snapshot_metrics_service.py`,
`daemon/services/snapshot_search_service.py` are in THEIR dirty tree
but NOT in this plan's touch set (disjoint; listed so the pre-merge
checker does not false-alarm).

**FE-side overlap: settings.component.html only** (per dispatch).

**Conflict points to watch when merging:**

1. **Both branches extend the snapshot repo** — unify-spawn-tools
   may add a per-agent-enabled check inside the repo; this plan
   adds a new method without touching the existing check. Merge
   should be conflict-free on the repo file.
2. **Both branches touch the FE settings page** — unify-spawn-tools
   adds the per-agent `snapshot_enabled` chip; this plan does not
   touch the settings page (D9). FE merger coordinates.
3. **Both branches extend the metrics surface** — unify-spawn-tools
   may add a per-project grouping to the metrics payload; this
   plan only relocates the endpoint. Merge should be additive.

If the merging engineer discovers a conflict at any of the above
points, **stop and re-plan** — neither branch anticipated the
overlap; a third pair-of-eyes is cheaper than a 2am revert.

## 13. Citations Index

| Claim | Source |
|-------|--------|
| Snapshot model 18 columns | `daemon/repositories/snapshot/models.py:104-228` (verified by read) |
| `to_dict()` shape | `daemon/repositories/snapshot/models.py:230-251` (verified by read) |
| `SNAPSHOT_STATUSES` 5-value set | `daemon/repositories/snapshot/models.py:83-101` (verified) |
| Indexes on `snapshots` | `daemon/repositories/snapshot/models.py:164-172` (verified) |
| `list_active_by_project` signature + body | `daemon/repositories/snapshot/repository.py:423-449` (verified) |
| `filter_by_tags` PG/SQLite parity | `daemon/repositories/snapshot/repository.py:493-608` (verified) |
| `_build_filter_by_tags_pg_stmt` drift-pin | `daemon/repositories/snapshot/repository.py:570-608` (verified) |
| `count_by_project` exists | `daemon/repositories/snapshot/repository.py:481-489` (verified) |
| Settings router toggle endpoints | `daemon/routers/settings.py:657-691` (verified) |
| Settings router metrics endpoint | `daemon/routers/settings.py:702-738` (verified) |
| Schema models for snapshot preference + metrics | `daemon/routers/schemas.py:1743`, `:1766`, `:1781-1818` (verified) |
| `_snapshot_repo` wired in manager | `daemon/manager.py:1816` (verified) |
| `_snapshot_metrics_service` wired in manager | `daemon/manager.py:1861-1864` (verified) |
| `BlueprintListResponse` shape canon | `daemon/routers/blueprints.py:122-126` (verified) |
| `_validate_project_id` UUID 400 shape | `daemon/routers/blueprints.py:137-155` (verified) |
| Double-clamp idiom | `daemon/routers/skills.py:1235-1240` (verified) |
| 404 detail shape | `daemon/routers/skills.py:799-810` (verified) |
| Sort pattern with `Query(pattern=...)` | `daemon/routers/instances.py:399-410` (verified) |
| Router include site | `daemon/api.py:2991` (verified by `grep include_router`) |
| Single FE caller of old metrics path | `frontend/src/app/services/settings.service.ts:175-181` (verified by `grep`) |
| Spawn-hot WARM uses `list_active_by_project` | `daemon/services/snapshot_search_service.py:241, 328` (verified) |
| Duck-typed test fake | `tests/unit/test_snapshot_search_service.py:128-175` (verified) |
| Existing repo test class | `tests/unit/test_snapshot_repository.py:366-398` (verified) |
| `MonitoringOnlyPinTest` pins metrics-vs-search isolation | `tests/unit/tools/test_snapshot_v3.py::TestMonitoringOnlyPin` (referenced in models.py:60) |
| Router test harness precedent | `tests/unit/routers/test_missions_api.py` (verified) |
| Project open auth posture | [given] §2 "Auth: NO auth gating exists anywhere in daemon" |
| Zero schema work in v1 | [given] §2 "ZERO schema work needed for project/agent/tag/status/age filters" |
| `size` dimension deferred | [given] §2 "size dimension MISSING — v1 DEFERS it (migration-free)" |
| FE page is global with project filter | [given] §1 caller lean |
| Coupling rules (unify-spawn-tools disjoint) | [given] Coupling Rules block |
| Worktree / branch / base SHA | `/home/nea/ensemble-src-wt-snapshot-uiux`, `feature/snapshot-uiux`, `ac399874` (verified by `git log -1 --oneline` + `git status -sb`) |

---

**End of be-plan.md.** Plan-only; no source file modified. Hand off
to the developer commission (T1–T10 in §11) and to the parallel FE
plan in the sibling file.
