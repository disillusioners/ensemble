# Technical Analysis: OAuth2 Authentication Support for the Ensemble Daemon

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

> **AMEND v1.3 — applied amendments in this file:**
> - **B4 (FR-30 / NFR-15 readiness sub-field enum alignment)** — `:989` reworded from `auth_dependency_status=fail` to the FR-30 enum (`"ok" | "degraded" | "unconfigured"`); verdict NEVER flips to 503 due to auth state per OD-9 / AC-31.
> - **F8 (lifespan-site anchor correction)** — `:977` lifespan-site anchor `daemon/manager.py:315-345` corrected to `daemon/api.py:1544-1555` (the readiness-refresher task pattern; the same correction phase2 T2.4 / Minor #2 already received).
> - **F7 (TA Debt #7 superseded)** — `:1344` (the maintenance-canary 401 risk row) marked SUPERSEDED by FR-22/23 + G5.3 — the canary path is PUBLIC per the route-metadata derivation (D18 / D1); the canary runs unchanged against auth-ON daemon per AMEND v1.1 / D1.

> **AMEND v1.2 — applied amendments in this file:**
> - **Minor #10 (CORS-preflight rationale precision):** rule 3 (binding rule 3) amended — OPTIONS preflight MUST pass through unauthenticated regardless of `auth.enabled` because preflight requests never carry cookies or `Authorization` headers per the Fetch spec; a 401 on preflight would break every cross-origin browser caller.

Date: 2026-10-10
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive
Status: Draft — architect CONDITIONAL PASS applied (see `architecture-recommendation.md §5`)

> **Amendment log (v1.1, 2026-10-10).** The architect's CONDITIONAL PASS
> applied the following plan-text amendments to this file:
> - **D1 (Mount fix, MUST-FIX)** — Rule 6 amended: Starlette 0.52.1 Mounts
>   traverse the parent middleware (false claim removed); `/api/mcp/kb` and
>   `/api/mcp/kb/sse` moved to `AUTH_DEFAULT_PUBLIC_PATHS` v1; KB-MCP-auth
>   reserved seam documented. `[SUPERSEDED by AMEND v1.4 / module-lanes — D19: /api/mcp/kb* reclassified MODULE-LANE (three-class derivation; OAuth2 middleware passes module-lane untouched in both auth states)]`
> - **D2 (redirect chain wiring, MUST-FIX)** — Pseudocode `:749-754` amended
>   to pass `live_view_external_base_url_fallback` to `BaseURLResolver` as
>   tier-1; field name ratified in D6.
> - **C3 (route-metadata derivation)** — `public_path_set` construction
>   walks `app.routes` and logs the derived set; hand-listed frozenset
>   replaced.
> - **C4 (boot preflight, exit-78)** — Static misconfig validation moved
>   to boot preflight (exit-75 precedent at `__main__.py:319-324`).
> - **Line-drift** — `jobs_crud.py:275-278`→`:595`; `messages.py:391`→`:690`;
>   `launcher.sh:52-54`→`launcher.sh:52` (repo ROOT); 27→28 routers incl.
>   `tmp_images_router` at `api.py:3142`.
> - **SC-4/AC-S3 publicness precision** — Invariant reworded to PUBLICNESS
>   (never 401, no auth dependency) NOT "200 in every scenario."
> - **C1 (router-mount scope-declaration cage)** — Referenced in §Component
>   Architecture; cage test lives in [REQ] FR-38.
> - **C2 (bootstrap-token exfiltration surface)** — Three hardening
>   conditions (deny-list + one-shot + runbook) referenced in §Trade-offs;
>   full FR lives in [REQ] FR-16a.

## Question

How should the Ensemble daemon add OAuth2-based authentication to the
HTTP surface (browsers, SSE streams, REST clients, and 12 inventoried
loopback/same-host machine consumers) — **without** breaking the
default-zero-auth invariant, preserving the structural
public-health-probe guarantee, integrating with the existing
URL-resolution and cookie-less / secret-free design, and
architecturally accommodating the future /views share tokens + password-
gated views + anonymous /views scope extensions — so that operator
deployments behind reverse proxies are safe by default, machine
consumers keep working with zero behavioral change in the off-state,
and the build is testable in-process (no Docker)?

## Context Summary

The daemon is a FastAPI app built with `create_app()` at
`daemon/api.py:2909-3319`. Today there are exactly **three registered
middlewares** (CORSMiddleware, a factory-local SelectiveAccessLogMiddleware,
and `HostCaptureMiddleware`); **zero inbound authentication** (the grep
audit is clean — all credential references are outbound clients, not
inbound gates). The only existing inbound gating surfaces are the
maintenance `Origin` header guard
(`daemon/routers/maintenance_origin_guard.py:24-43`) and the optional
per-source webhook `X-Webhook-Secret` (`daemon/routers/webhooks.py:63-74`).

The plan context (sibling-worker producer) builds `requirements.md`
covering an endpoint × consumer compatibility matrix and per-consumer
permission semantics. This document supplies the **architecture
input**: the OAuth2 flow choice, the middleware / service /
repository split, the exact registration order, the redirect_uri
re-use strategy on top of `BaseURLResolver`, the session-store
contract, the failure-mode cascades, the machine-client lane, and
the testability story. The plan-creation worker will consume BOTH
artifacts (`requirements.md` and this `technical-analysis.md`) to
produce `plan-overview.md` + `phaseN-plan.md` + `decisions.md`.

The decisive structural facts:

1. **LIFO middleware execution** (`daemon/api.py:3014-3032`) — the
   LAST-registered runs OUTERMOST. Health probes (`/livez`,
   `/readyz`) are app-route functions registered AFTER the
   middlewares (`:3172`, `:3188`), so middleware decisions about
   "is this public?" must be made BEFORE the route dispatch fires.

2. **Promote restarts are routine full process death**
   (`daemon/__main__.py:332-352` SIGKILL at 70s,
   `launcher.sh:52` at repo ROOT — line-drift corrected v1.1).
   Anything that must survive
   restarts must be PG-backed; in-memory sessions are categorically
   wrong here.

3. **Live-view URL chain (`daemon/services/live_views.py:524-703`)** is
   already engineered for trust-aware re-resolution; OAuth
   `redirect_uri` should reuse `BaseURLResolver.resolve()` rather
   than build its own.

4. **CORS today is wildcard + credentials** (`daemon/api.py:3015-3021`).
   This is already a known risk under `allow_credentials=True`; the
   auth build MUST either keep CORS permissive for the off-state
   and tighten it for the on-state, or coexist with a new
   config-driven allowed-origins list that defaults to permissive.

5. **12 inventoried machine consumers** (promote/rollback/restart/status
   scripts, `adopt-unit.sh`, `deploy.sh`, `watchdog-watcher.sh`,
   `upgrade_tools.py`, `test_builtin_mcp_servers.sh`,
   `e2e_pause_resume_test.py`, `tests/e2e/mock_source_server.py`,
   `frontend/scripts/boot-e2e-snapshots-daemon.sh`,
   `frontend/scripts/boot-e2e-maintenance-daemon.sh`, the Angular
   SPA, and the pytest suite) **all** call loopback/same-host
   today and **none** send auth headers. The default-OFF contract
   is non-negotiable.

6. The base URL-resolver has a documented "operator override is the
   cure behind proxies" model (`docs/runbooks/live-views.md §2.4,
   §6 trust model`); OAuth `redirect_uri` must plug into that model
   without re-inventing trust.

## Architecture

### Current Patterns

- **Layered middleware composition (decorator-style ASGI stack)** —
  `daemon/api.py:3014-3032`. Each middleware is a pure ASGI class
  with `__init__(self, app)` + `async __call__(self, scope, receive, send)`.
  Pattern precedent: `daemon/middleware/host_capture.py:27-58` —
  thin pass-through, no failure path, looks up collaborators via
  `scope["app"]` (Starlette 0.30+).
- **Pure pass-through, fail-closed by capture drop, never an error** —
  `host_capture.py:42-52` decodes headers defensively, ignores
  malformed pairs, and never raises. This is the safety budget
  the new auth middleware needs to inherit.
- **`BaseSettings` config section per feature** —
  `config.py:3360-3391` aggregates sections via
  `Field(default_factory=...)`. Cleanest template:
  `LiveViewsConfig` at `config.py:2554-2628`
  (`env_prefix="ENSEMBLE_LIVE_VIEWS_"`). Env interpolation
  `${VAR:-default}` via `substitute_env_vars` at `config.py:43-60`;
  CSV-list parsing via `Annotated[list[str], NoDecode]` +
  `_parse_csv_or_json_list` at `:63-96`.
- **Repository = SQLModel + factory + Postgres-compat + new
  per-table migration SQL file** —
  pattern: `daemon/repositories/<domain>/` (factory.py + models),
  every new SQLModel table requires
  `daemon/migrations/versions/YYYYMMDD_HHMMSS_description.sql`
  (SHA-256 checksummed, `-- UP` / `-- DOWN`), template:
  `daemon/migrations/versions/20261007_000001_create_drift_events.sql`.
- **Service owns orchestration, repo owns persistence, router owns
  HTTP** — `daemon/services/live_views.py` is a service class
  exposing `build_url()` (`:1138-1178`); router is
  `daemon/routers/live_views.py:274-391`.
- **App-level gates registered OUTSIDE `/api`** — `/livez` at
  `daemon/api.py:3172-3186`, `/readyz` at `:3188-3238`. Publicness
  is achieved by route placement, not by middleware exemption
  lists. **We will mirror this pattern for `/api/auth/login`,
  `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`.**

### Module Boundaries (target)

```
daemon/middleware/auth.py                 # Pure ASGI; public-path gate; cookie/CSRF enforcement
        │
        ▼
daemon/services/auth_service.py           # login URL build, code exchange, callback verification,
        │                                   session issue/validate/invalidate
        ▼                                   (uses BaseURLResolver for redirect_uri)
daemon/repositories/auth_session/
        ├── models.py                     # AuthSession, AuthState (PKCE + nonce store), AuthProviderConfig
        └── repository.py                 # session CRUD, sweep, idempotent state use
        │
        ▼
daemon/services/oauth_client.py           # OIDC discovery + manual-endpoint fallback; token exchange
        │                                   (httpx.AsyncClient, same lane as DB external-conn)
        ▼
daemon/routers/auth.py                    # /api/auth/* routes
        │
        ▼
daemon/config.py AuthConfig section       # ENSEMBLE_AUTH_* env; LiveViewsConfig analog
```

Module-boundary dependencies (one-way, no cycles):

| Consumer | Producer | Why |
|---|---|---|
| `auth` middleware | `auth_service` | session validation + cookie issue |
| `auth_service` | `oauth_client` | code → token exchange |
| `auth_service` | `BaseURLResolver` | `redirect_uri` reuse |
| `auth_service` | `auth_session repo` | session + state persistence |
| `auth router` | `auth_service` | HTTP handlers |
| `create_app()` | `auth` middleware | registration site |
| `create_app()` | `LiveViewsService` | unchanged — base URL is reused |

### Architecture Diagram (text)

Browser login (full data flow):

```
                                            ┌────────────────────────────────┐
                                            │ daemon/middleware/auth.py       │
                                            │ (public-path gate, LIFO outer)  │
                                            └─────────┬──────────────────────┘
                                                      │ HTTP request
                            ┌──── /livez,/readyz ────┼── /api/auth/login ── /api/* ──┐
                            │                       │                  │               │
                            ▼                       ▼                  ▼               ▼
                  pass-through           302 to provider        provider redirect  protected
                  (always)               (login_url)            "code=…&state=…"  middleware
                                                                 │            │    enforces
                                                                 ▼            ▼    cookie
                                                       daemon/routers/auth.py     ▼
                                                       ┌──────────────────┐  daemon/services/
                                                       │ /api/auth/callback│  auth_service.py
                                                       └─────┬────────────┘  (validate→issue)
                                                             │
                                              ┌──────────────┴────────────┐
                                              ▼                           ▼
                                  daemon/services/oauth_client.py   daemon/repositories/
                                  (token endpoint POST)              auth_session/repository.py
                                              │                           │
                                              ▼                           ▼
                                  IdP token endpoint (HTTPS)        auth_session table
                                                                          │
                                                                          ▼
                                                          Set-Cookie: ensemble_sid=…;
                                                          HttpOnly; Secure (configurable);
                                                          SameSite=Lax
                                                                          │
                                              ┌───────────────────────────┘
                                              ▼
                                  Subsequent request → middleware sees cookie → calls
                                  auth_service.validate_session(sid) → if valid, attach
                                  identity to scope["state"]["auth_identity"] (or
                                  request.state.auth_identity in HTTP path)
```

Browser logout:

```
POST /api/auth/logout (cookie required)
        │
        ▼
auth_service.invalidate_session(sid)  → DELETE FROM auth_sessions WHERE id=… (single-CAS)
        │
        ▼
204 No Content + Set-Cookie: ensemble_sid=; Max-Age=0
```

Machine request (with static bearer tokens — see §10):

```
POST /api/instances (Authorization: Bearer <sha256(token)>)
        │
        ▼
auth middleware → token lookup (constant-time) → row hit → attach identity
        │
        ▼
router (no behavioral change in off-state: when auth disabled, middleware is pass-through)
```

### Registered Middleware Order — Final

Because Starlette `add_middleware` is LIFO, the LAST-registered runs
OUTERMOST. New order at `daemon/api.py` ~ line 3032 (after the
existing three are registered):

| Registration order | Middleware | Execution order (outermost first) | Notes |
|---|---|---|---|
| 1st registered | `CORSMiddleware` (`api.py:3015`) | 4th (innermost) | Wildcard+credentials today; on-state tightens origins |
| 2nd registered | `SelectiveAccessLogMiddleware` (`:3024`) | 3rd | Factory-local — unchanged |
| 3rd registered | `HostCaptureMiddleware` (`:3032`) | 2nd | MUST run BEFORE auth constructs `redirect_uri` |
| **4th registered (NEW)** | `AuthMiddleware` | **1st (outermost)** | Evaluates public-path fast path FIRST (zero I/O) |

Why this exact order:

1. **`HostCaptureMiddleware` MUST run before `AuthMiddleware`
   constructs `redirect_uri`** — the resolver at
   `daemon/services/live_views.py:617-703` reads from
   `app.state.host_recorder` which is last-write-wins; a host captured
   by a non-public request is the host that the OAuth callback will
   land on. We accept that this surface inherits the documented
   Host-poisoning risk noted in `docs/runbooks/live-views.md §2.4` —
   the operator override `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` is
   the published cure, and OAuth's `redirect_uri` MUST NOT be the
   first request to mint a host. **Action for the planner**: the
   first OAuth login attempt should pre-warm the host recorder by
   the `/api/auth/login` route issuing a backend-internal request
   to itself with `app.state` capture OR — preferred — `AuthService`
   should construct `redirect_uri` from `resolve()` only AFTER the
   Host-capture tier has been populated by at least one prior
   request on this daemon instance. If not, the operator MUST set
   `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` (see §6 + §9 for the
   escalation path).

2. **`AuthMiddleware` outermost** — runs before CORS so the public-path
   decision happens before any preflight logic; preflight `OPTIONS`
   is handled by starlette directly and short-circuits before reaching
   the middleware anyway, but the order matters for the **fallback
   path** (a non-preflighted cross-origin browser call must hit the
   auth first, get a 401 with `WWW-Authenticate`, and never reach
   the inner CORS handling).

3. **SelectiveAccessLog unchanged** — pass-through; auth failures
   MUST still log so the access log shows the denied traffic.
   Decision (not yet finalised — see Open Technical Questions): 401
   on a protected route should log at INFO with the same `client_addr`
   shape; successful auth-protected calls log at INFO with the user
   identity added (e.g., `user=…`); unauthenticated denials should
   log at WARNING to make brute-force visible.

4. **No new public-path exemption list in the middleware** — public
   paths (`/livez`, `/readyz`, `/api/auth/login`, `/api/auth/callback`,
   the SPA catch-all `GET /{path:path}` at `:3285-3310`, `/docs`,
   `/redoc`, `/openapi.json`, `/api/mcp/kb` mount surface, `/vscode`
   WebSocket at `vscode_proxy.py:1092`) are determined by a
   singleton tuple baked at middleware construction time. The
   tuple is derived from the **same router registration list** at
   `daemon/api.py:3097-3319`; we MUST NOT hardcode path strings —
   the router handlers are the source of truth, and any new
   public path added by a sibling feature must update the tuple
   manually **for now**. (Long term: a "public registry" decorator
   is out of scope here but mentioned in §11 reserved seams.)

### SEAMS / Reserved Extensions (Design For, Not Build)

These architectural seams are deliberately **named** but not built
in this plan. They MUST be accommodated so the Phase-N+1 features do
not require replumbing.

1. **Per-view share tokens** — `LiveViewsRouter.share_*` endpoints
   under `daemon/routers/live_views.py:274-391`. The auth build
   reserves an `AuthContext` object carried on `request.state` (HTTP
   path) or `scope["state"]` (ASGI). The context has at minimum:
   `subject` (opaque user id), `scopes` (set[str]), `auth_method`
   (`"session"` | `"bearer"` | `"share_token"` — last populated by
   the later feature), `expires_at` (epoch). `/views` handlers in
   future look up `scope["state"]["auth_context"]` and apply a
   per-root authorization policy hook. **No code for share tokens
   here** — the seam is the context object shape.

2. **Password-gated views** — same `AuthContext` shape; the future
   feature would populate `auth_method="password_view"` and inject a
   one-shot capability.

3. **Anonymous scopes on specific /views roots** — the future feature
   tags certain view paths as `public=true` in the routing table;
   `AuthMiddleware` consults this tag at request time and skips
   cookie enforcement for that path. Again — **no build here**;
   the tag resolution mechanism and tag storage location (route
   metadata vs a config map) is an architectural decision the
   Phase-N+1 planner must ratify.

4. **Grants table shape (future)** — predicted columns:
   `id`, `subject`, `scope`, `target` (view-uuid or share-token-id
   or route-name), `created_at`, `expires_at`. PG primary; SQLite
   compat via TEXT-id (UUID-emit fallback). Migration file
   `daemon/migrations/versions/<ts>_create_grants.sql` when this
   lands. Not built now.

## Integration Points

| # | Integration | Type | Contract | Auth today | Failure mode | File:Line |
|---|---|---|---|---|---|---|
| 1 | Browser → Daemon `/api/*` | sync HTTP | REST/JSON | None → 200 | Auth on → 401 + WWW-Authenticate, Auth off → 200 unchanged | `daemon/routers/*` |
| 2 | Browser → Daemon SSE `/api/*/events` | async HTTP | EventSource | None → SSE | EventSource cannot send Authorization → MUST use cookie session | `daemon/routers/messages.py:914`, `jobs_streaming.py:307`, `notifications.py:29`, `migration.py:223`, `workspace.py:165` |
| 3 | IdP /authorize | sync HTTPS (302) | OIDC / OAuth2 | n/a | Provider down → 401 `temporarily_unavailable` + `Retry-After`; disabled → return 503 with explicit message; state-store miss → 400 | `daemon/services/oauth_client.py` (new) |
| 4 | IdP /token | sync HTTPS POST | OIDC / OAuth2 (form) | n/a | Non-2xx → log+401, do NOT leak provider error body | `daemon/services/oauth_client.py` |
| 5 | OIDC `/.well-known/openid-configuration` | sync HTTPS GET | OIDC discovery doc | n/a | Boot-time fetch with manual-endpoint fallback + TTL cache (≤ 24h) | `daemon/services/oauth_client.py` |
| 6 | PostgreSQL `auth_session` table | sync SQL | CRUD + sweep | n/a | Table unavailable → fail-closed for auth-protected routes; /livez + /readyz unaffected (structural fast path) | `daemon/repositories/auth_session/` (new) |
| 7 | `BaseURLResolver.resolve()` | sync (in-proc) | returns `str | None` | n/a | None → documented "operator error" path; see §6 | `daemon/services/live_views.py:617-703` |
| 8 | Machine clients (12 rows) | sync HTTP | REST/JSON + curl | None → 200 | Static bearer tokens (env/config, hashed at rest); see §10 | per consumer file |
| 9 | Pytest in-process (`ASGITransport`) | sync in-proc | httpx | None → 200 | New fixture `auth_disabled_app` (existing) + `auth_enabled_app` (new) | `tests/test_api.py:196-227` |
| 10 | Fake OIDC/OAuth2 provider (NEW) | sync ASGI sub-app | AppRunner pattern | n/a | In-process; no Docker | `tests/fakes/fake_oidc_provider.py` (new; pattern after `tests/e2e/mock_source_server.py`) |
| 11 | `HostRecorder` | sync (in-proc) | last-write-wins recorder | n/a | Already documented; auth inherits | `daemon/services/live_views.py:524-549`, wired `api.py:1054-1055` |
| 12 | Config loader | sync at boot | `BaseSettings` | n/a | If `ENSEMBLE_AUTH_ENABLED=true` and `client_id`/`secret`/`issuer` missing → fail-fast at boot (not at first request) | `daemon/config.py` AuthConfig (new) |

### Integration Details

**Integration 1 + 2: browser → daemon (HTTP + SSE)** — cookie-session
is the only browser-compatible scheme for both REST and EventSource
(`new EventSource(url)` ignores `Authorization` headers). Cookie
flags are detailed in §7. CORS preflight `OPTIONS` short-circuits
in starlette before reaching our middleware stack; no special
handling required.

**Integration 3, 4, 5: OIDC provider** — boot-time discovery fetch
with 5 s timeout, manual endpoint fallback (operators can pin
`authorization_endpoint` / `token_endpoint` / `jwks_uri` / `end_session_endpoint`
directly when discovery is unreachable, with `discover=false` config).
Discovery response is cached with TTL ≤ 24 h. JWKS for ID-token
validation is fetched lazily on first callback (one-shot HTTP
GET) and cached for the duration of the process; rotation is
accommodated by a kid-miss path that re-fetches once and only
once.

**Integration 6: `auth_session` table** — see §7 for the schema. The
session store MUST survive routine promote restarts
(`daemon/__main__.py:332-352` SIGKILL at 70s) — that requires PG
persistence, not in-memory. Idempotent state use: state parameters
and PKCE verifiers are stored with a single-CAS
`pending → consumed` at the callback handler; the loser of a CAS
race gets a deterministic `400 STATE_REPLAYED`. Sweep deletes rows
with `expires_at < now()` (background task in the lifespan, started
alongside `DependencyBus` if enabled).

**Integration 7: `BaseURLResolver` reuse** — `AuthService` constructs
`BaseURLResolver(external_base_url=None, bind_host=None, bind_port=None,
host_recorder=...)` ONLY for the daemon's own bind+port (read from
`config.daemon.host` / `port`) and the operator-configured
`auth.external_redirect_base` (when set) or the liveview
`external_base_url` (re-use config); otherwise it relies on the
recorder having been warmed by a prior request. Decision tree:

- `auth.external_redirect_base` set → use that (overrides everything,
  same semantics as `live_views.external_base_url`).
- else `host_recorder.latest()` non-empty → use that, with the
  trust-model caveat.
- else bind + reverse-bind-wildcard → use that.
- else **None → reject the login attempt with a 503
  `LOGIN_NOT_CONFIGURED`** (operator error, never a mint).

**Integration 8: machine clients** — see §10.

**Integration 9: pytest** — add a parallel fixture to
`tests/test_health_probes.py:41-89` style: `auth_enabled_app`
yields a module-level app with `ENSEMBLE_AUTH_ENABLED=true` and
points `OAUTH_ISSUER` at the in-process fake OIDC. Both fixtures
must produce IDENTICAL response bodies on `/livez` and `/readyz`
(or prove equivalence with snapshot tests) — see §5 structural
guarantee.

**Integration 10: fake OIDC provider** — pure ASGI sub-app with
five routes: `/.well-known/openid-configuration`,
`/authorize` (handler that issues a 302 to the registered
`redirect_uri` with `code` + `state` + PKCE enforcement), `/token`
(validates `code` + `client_secret` + `code_verifier`, returns a
fake JWT), `/jwks`, `/logout` (introspect the `id_token_hint`
constant). NO Docker, NO external port — runs in-process under
the same event loop.

## Trade-offs

### Alternatives Considered

1. **Option A: OAuth2 Authorization Code + PKCE + session cookie
   (recommended)** — browser redirects to IdP, daemon holds
   confidential client secret, exchanges `code` for tokens server-
   side, sets an HttpOnly Secure cookie bound to a server-side
   session. Machine clients use a parallel static-bearer lane.
2. **Option B: Implicit flow (deprecated by OAuth 2.1, the
   successor RFC)** — tokens returned in the URL fragment. Insecure,
   no client authentication, not browser-compatible with
   `EventSource`. Not considered viable.
3. **Option C: Hybrid (deprecated by OAuth 2.1)** — `code` in
   query string, `id_token` in fragment. Adds complexity, no
   benefit over Authorization Code alone.
4. **Option D: Device Authorization Grant** — user pairs device
   with a code. Wrong shape: the ensemble is server-mode, not
   input-constrained.
5. **Option E: mTLS only** — does not scale to browser clients;
   out of scope for the user-facing auth surface.
6. **Option F: API key in `X-API-Key` header** — viable for the
   machine-client lane but not for browsers (browser cannot set
   custom headers per-origin without CORS preflight, and the SPA
   has zero auth UI today).

### Comparison

| Criterion | A (Authz Code + PKCE + cookie) | B (Implicit, deprecated) | D (Device flow) | F (API key header) |
|---|---|---|---|---|
| Browser-compatible | ✅ EventSource + REST | ❌ deprecated | ❌ wrong shape | ❌ SPA no UI |
| Machine-client lane | ✅ + parallel bearer | n/a | n/a | ✅ direct |
| Confidentiality of client secret | ✅ daemon holds it | ❌ impossible | n/a | ✅ env/config |
| CSRF surface | ⚠ mitigated by SameSite=Lax + double-submit token | ⚠ mitigated (or stateless) | ✅ minimal | ✅ header only |
| Restart-survival | ✅ PG session store | ✅ stateless | ✅ stateless | ✅ stateless |
| Testability in-process | ✅ fake OIDC ASGI sub-app | n/a | ⚠ needs user-prompt simulation | ✅ trivial |
| OIDC discovery | ✅ standard | n/a | n/a | n/a |
| Complexity to build & maintain | medium | n/a (deprecated) | n/a (wrong shape) | low |
| Reversibility to swap flow | medium (one middleware, well-seamed) | n/a | n/a | high |
| Compliance with current best practice (OAuth 2.1 draft, 2026) | ✅ | ❌ | ⚠ niche use | ✅ |

### Recommendation

**Pick: Option A (Authorization Code + PKCE) for the browser lane
+ bearer-token lane (Option F semantics, OAuth-shaped) for machine
clients.**

**Reasoning:** Browsers cannot supply `Authorization` headers on
EventSource SSE (`new EventSource(url)` only honors `withCredentials`),
so a cookie session is the only browser-compatible scheme. PKCE
provides defense-in-depth against authorization-code interception.
Confidential-client design keeps the `client_secret` server-side
(never exposed to JS), aligning with the project's "no front-end
secret leaking" invariant (see project `critical-notes` convention).
The machine-client lane is parallel, not nested: static bearer tokens
(env/config, hashed at rest via Fernet or argon2 in PG) avoid
introducing OAuth complexity for cron-loop callers that already exist.

**Assumptions:**

- The IdP supports OIDC discovery (anything modern — Auth0, Keycloak,
  Google, GitHub, Okta).
- The operator can register the daemon as an OIDC client with a
  known `client_id` + `client_secret` + `redirect_uri`.
- The deployment is either HTTPS-terminated (proxy or daemon-side
  TLS) or operating on a trusted LAN with operator-acknowledged
  Secure-cookie override (see §11 risk #2).
- Machine consumers are operator-controlled; the bearer-token lane
  is for trusted loopback callers, not third-party clients.

**Reversibility:** High. The AuthService interface is one-directional
(`issue_session`, `validate_session`, `invalidate_session`); swapping
the underlying flow is a contained change inside
`daemon/services/auth_service.py` + `daemon/middleware/auth.py`.
Swapping the bearer-token lane to client_credentials grant later
requires one new endpoint and a config flag — out of scope here.

## Flow Choice (Detailed)

The Authorization Code flow with PKCE, executed as follows (per-
step, citing the auth_service / middleware seams):

**Step 1 — Login initiation.** Browser `GET /api/auth/login` →
`AuthMiddleware` sees this is in the public-path tuple → pass-
through → `AuthRouter` calls `AuthService.build_login_url(state,
redirect_uri)`. `AuthService` mints: `state` (32-byte URL-safe base64,
TTL 5 min), `code_verifier` (43–128 char URL-safe base64), derives
`code_challenge = S256(code_verifier)` (never `plain`, per current
best practice). Persists both rows in the `auth_states` table with
`expires_at = now() + 300s`. Constructs the IdP `/authorize` URL:
`{issuer}/authorize?response_type=code&client_id=...&redirect_uri=...&scope=openid+profile+email&state=...&code_challenge=...&code_challenge_method=S256`.
Returns 302 to that URL.

**Step 2 — User authenticates at IdP.** Out of process.

**Step 3 — IdP redirects to `redirect_uri`.** Browser
`GET /api/auth/callback?code=...&state=...` →
`AuthMiddleware` public-path pass-through →
`AuthRouter.callback` reads `state`, looks up the
`auth_states` row by `state` (PK lookup), CAS
`pending → consumed` (single CAS, exactly-once). If CAS loses
→ `400 STATE_REPLAYED` (caller raced a replay). If `expires_at
< now()` → `400 STATE_EXPIRED`.

**Step 4 — Code exchange.** `AuthService` POSTs to the IdP
`/token` endpoint with `grant_type=authorization_code`, `code`,
`redirect_uri`, `client_id`, `client_secret` (Basic auth or
form body — both common; form body is the de facto default and
is the implementation choice here), `code_verifier`. Non-2xx →
log at WARNING with the `(body_snippet_truncated_to_256)` for
debugging (no full leakage), return `401 IDP_REJECTED` to the
user (with a generic message).

**Step 5 — ID-token validation.** Decode the JWT (or JWE), verify
`iss` matches `external_issuer` (constant-time string compare),
verify `aud` matches `client_id`, verify `exp` / `nbf` against
local clock (allow ±60 s skew via config), verify signature against
JWKS (cached). Extract `sub` (subject), `email` (if scope granted),
`name` (if scope granted), and any custom claims.

**Step 6 — Session issue.** `AuthService.issue_session(subject,
auth_method="session", scopes=set(openid, profile, email),
user_agent=request.headers.get("user-agent"))` inserts an
`auth_sessions` row with `id = secrets.token_urlsafe(32)`,
`created_at = now()`, `expires_at = now() + ttl_seconds` (default
8 h; sliding renewal on validate is `OFF` by default to keep TTLs
audit-clean), `last_used_at = now()`, `ip_subnet = /24 of the
client IP` (sanity-check fingerprint for hijack detection).
Returns the session id; `AuthRouter` writes
`Set-Cookie: ensemble_sid=<sid>; HttpOnly; Secure; SameSite=Lax;
Path=/; Max-Age=<ttl>`. (See §7 for the cookie flag debate.)

**Step 7 — Session validation.** Every protected request:
`AuthMiddleware.__call__` parses the cookie, looks up
`auth_sessions` by id, checks `expires_at`, optionally
checks `ip_subnet` against current request (configurable; default
OFF to avoid carrier-grade-NAT false positives). On hit, attaches
`request.state.auth_context = AuthContext(...)` (HTTP path) or
populates `scope["state"]["auth_context"]` (ASGI path). On miss
or expiry → 401 + `WWW-Authenticate: Bearer realm="ensemble"`.

## Component Architecture (Detailed)

```
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/middleware/auth.py — class AuthMiddleware                        │
│   - public_path_set: frozenset[str]   # built once at construction;    │
│       (DERIVED from route metadata per C3 v1.1 — routes carry a         │
│        `public-probe` tag/attribute; construction walks app.routes,     │
│        builds this frozenset, LOGS the derived set at startup;          │
│        prefix rules for `/api/auth/*` + SPA PUBLIC-ASSETS family;       │
│        v1 additions NOT expressible as route tags: "/api/mcp/kb",       │
│        "/api/mcp/kb/sse" — D1 loopback posture preserved verbatim)      │
│       + regex for SPA catch-all GET /{path:path}                        │
│       + "/api/mcp/kb" sub-mount pass                                    │
│       + "/vscode" websocket scope pass                                  │
│   - construction: pulls AuthConfig via settings (settings.auth) →      │
│       if not settings.auth.enabled: self.app = app; return              │
│         (effectively unregistered; or registered as pass-through)      │
│   - per request:                                                         │
│       if scope["type"] == "websocket": pass through                     │
│       if scope["path"] in public_path_set: pass through                │
│       if any(scope["path"].startswith(p) for p in                       │
│              config.anonymous_view_paths): pass through                 │
│         (S-19 v1.1 — consulted BEFORE session validation; v1 empty)     │
│       if scope["method"] == "OPTIONS": pass through (CORS preflight)    │
│       elif cookie present: lookup → attach context                     │
│       elif Authorization: Bearer <token>: verify → attach context      │
│       else:                                                              │
│         if SPA HTML path (Accept: text/html): 302 → /api/auth/login?   │
│           return_to=<path> (sanitised)                                   │
│         else: 401 + WWW-Authenticate                                     │
│   - all paths: never raise into the app; on internal auth failure,     │
│       log + 401/302                                                     │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/services/auth_service.py — class AuthService                     │
│   deps: oauth_client, auth_session_repo, base_url_resolver, clock       │
│   public surface:                                                        │
│     async build_login_url(*, request) -> str                            │
│     async handle_callback(*, code, state, request) -> AuthSession      │
│     async validate_session(sid) -> AuthContext | None                   │
│     async invalidate_session(sid) -> None                               │
│     async issue_static_token(subject) -> str (admin lane)               │
│     async verify_static_token(token) -> AuthContext | None              │
│     async lookup_bearer(token) -> AuthContext | None (constant-time)    │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/services/oauth_client.py — class OAuthClient                     │
│   deps: httpx.AsyncClient (long-lived, DNS-cached),                     │
│         config.auth.{issuer, client_id, client_secret, scopes, …}      │
│   public surface:                                                        │
│     async discovery() -> DiscoveryDoc (cached TTL ≤ 24h)                │
│     async exchange_code(*, code, redirect_uri, code_verifier)           │
│       -> TokenResponse                                                   │
│     async fetch_jwks(force=False) -> JWKS                                │
│     async validate_id_token(jwt_str) -> claims (uses JWKS)              │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/repositories/auth_session/                                        │
│   models.py — SQLModel classes:                                          │
│     AuthSession(id, subject, scopes, created_at, expires_at,            │
│                 last_used_at, user_agent, ip_subnet, auth_method)       │
│     AuthState(id, code_verifier, code_challenge,                        │
│                challenge_method, redirect_to, expires_at,               │
│                consumed_at)                                              │
│     BearerToken(id_hash, subject, scopes, created_at, expires_at,      │
│                 last_used_at, label) — id_hash is SHA-256(token)       │
│   repository.py — async CRUD + sweep                                    │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/routers/auth.py — auth-router                                    │
│   GET  /api/auth/login → 302 → IdP /authorize                            │
│   GET  /api/auth/callback → handle_callback → Set-Cookie + 302 → "/"   │
│   POST /api/auth/logout → invalidate_session → 204 + clear cookie       │
│   GET  /api/auth/me → returns auth_context (debug-friendly)             │
│     (NOT a /api/auth/session, to avoid colliding with session-router)   │
│   POST /api/auth/admin/tokens (admin lane, future — out of scope)       │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────────┐
│ daemon/config.py AuthConfig (new section)                                │
│   - enabled: bool = False                                                │
│   - issuer: str | None = None                                            │
│   - client_id: str | None = None                                         │
│   - client_secret_secret_ref: str | None = None  (KMS handle or env-ref)│
│   - discovery_url_override: str | None = None                           │
│   - authorization_endpoint_override: str | None = None                  │
│   - token_endpoint_override: str | None = None                          │
│   - jwks_uri_override: str | None = None                                │
│   - scopes: list[str] = ["openid", "profile", "email"]                  │
│   - session_ttl_seconds: int = 8*3600                                    │
│   - session_sliding_renewal: bool = False                                │
│   - clock_skew_seconds: int = 60                                         │
│   - bearer_tokens_secret_ref: str | None = None                         │
│   - public_paths: tuple[str, ...] = AUTH_DEFAULT_PUBLIC_PATHS           │
│       (AMENDED v1.1 / C3+D1: the hand-listed tuple is superseded —       │
│       the O(1) frozenset is DERIVED from route metadata at middleware    │
│       construction: routes carry a `public-probe` tag/attribute;         │
│       prefix rules allowed for `/api/auth/*` + the SPA PUBLIC-ASSETS     │
│       family; construction walks app.routes, builds the frozenset, and   │
│       LOGS the derived set at startup. The constant retains only the     │
│       v1 additions that are NOT app.routes-own: `/api/mcp/kb` +          │
│       `/api/mcp/kb/sse` Mount surfaces, tagged via the same mechanism)   │
│   - anonymous_view_paths: tuple[str, ...] = ()   (S-19, v1 empty)       │
│   - live_view_external_base_url_fallback: str | None = None             │
│       (D2 — one-shot lifespan copy of config.live_views.external_base_url│
│       so the resolver's tier-1 operator-override tier is actually wired; │
│       NOT an env var — derived, see phase1 T1.3)                        │
│   - external_redirect_base: str | None = None (overrides liveview's)    │
│   - allow_insecure_cookie: bool = False                                  │
│   - allowed_origins: list[str] | None = None  (when set, replaces       │
│       CORSMiddleware wildcard for THIS deployment)                      │
└────────────────────────────────────────────────────────────────────────┘
```

`[v1.4: KB-MCP entries moved out — module-lane class; see decisions.md D19 + D18 three-class derivation]` (AMEND v1.4 / module-lanes — the `public_paths` line above retains its v1.1/C3+D1 text for traceability; its "constant retains the `/api/mcp/kb` + `/api/mcp/kb/sse` Mount additions" clause is superseded: those entries now live in the derived `module_lane_path_set`, not the public set).

## Middleware Placement (Detailed Reasoning)

Registration (Starlette LIFO ⇒ outermost-first execution):

```
registration order in create_app()       execution order at runtime
─────────────────────────────────────    ──────────────────────
  4th: add_middleware(AuthMiddleware)       1st (outermost)  ← runs first
  3rd: add_middleware(HostCapture)          2nd              ← captures Host
  2nd: add_middleware(SelectiveAccessLog)   3rd              ← logs denials
  1st: add_middleware(CORSMiddleware)       4th (innermost) ← CORS last
```

Rationale (each rule is binding):

1. **Public-path fast-path is zero-network-I/O and runs
   BEFORE any other middleware interacts with the request.** The
   `public_path_set` is built at middleware construction time
   (closed-over tuple lookup, O(1)). `/livez` + `/readyz` pass
   through without invoking `AuthService`, `OAuthClient`, the
   recorder, the cookie parser, or the session store. This is
   the structural guarantee that health probes stay GREEN even
   when the IdP is down, the session table is unreachable, or
   the daemon is mid-session at restart.

2. **`HostCaptureMiddleware` MUST run before `AuthMiddleware` reads
   or constructs `redirect_uri`.** The recorder is last-write-
   wins on `app.state.host_recorder` (wired `api.py:1054-1055`),
   so the OAuth callback's Host IS the host the recorder saw on
   the request that started the login flow. We accept the
   documented Host-poisoning surface in exchange for not
   re-engineering `BaseURLResolver`; the operator override
   `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` (or the new auth-
   specific `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`) is the
   documented cure. See §6 for the None-from-resolver handling.

3. **CORS preflight (`OPTIONS`) short-circuits in Starlette BEFORE
   reaching user middleware.** This is Starlette/FastAPI
   behaviour for any handler that has `CORSMiddleware` registered;
   no special handling is required for preflight. **However**:
   when auth is enabled, the access-controlled response (a
   non-preflighted cross-origin POST 401) MUST come from
   `AuthMiddleware` BEFORE CORSMiddleware can mutate the
   response headers. This is exactly what the LIFO ordering
   gives us. **AMEND v1.2 / Minor #10 (CORS-preflight rationale precision):**
   preflight `OPTIONS` requests NEVER carry cookies or `Authorization`
   headers (the browser's preflight semantics preclude them — see
   Fetch spec §2.2 "CORS protocol"; the browser MUST NOT attach
   credentials to a preflight); therefore preflight MUST pass through
   unauthenticated regardless of `auth.enabled` — a 401 on preflight
   would break every cross-origin browser caller (the browser would
   interpret the 401 as "the server rejected the preflight, so the
   actual request is not safe to send"). AuthMiddleware's OPTIONS
   pass-through is therefore not just an optimization; it is a
   correctness requirement for cross-origin browser traffic.

4. **`/vscode` WebSocket** (`vscode_proxy.py:1092`) — `AuthMiddleware`
   passes through `scope["type"] == "websocket"` UNTOUCHED. The
   current vscode auth model is **out of scope** here; today's
   vscode mount has no auth (it shares the loopback trust model
   with the machine consumers). The future-vscode auth is a
   separate plan that reuses this middleware via a `scope["type"]
   == "websocket"` branch with cookie validation. This document
   reserves the seam.

5. **SSE endpoints** (`messages.py:914`, `jobs_streaming.py:307`,
   `notifications.py:29`, `migration.py:223`, `workspace.py:165`) —
   `EventSource` cannot send `Authorization`, so SSE auth MUST
   be cookie. `AuthMiddleware` validates the cookie on the
   initial `GET` and attaches context; from there the existing
   SSE handler proceeds unchanged. This means an SSE client
   without a valid cookie sees 401 instead of an `event: error`
   stream — acceptable.

6. **`/api/mcp/kb` mounts** (`api.py:3150-3151`) — **AMENDED v1.1
   (D1, architect must-fix; the original claim here was FALSE).**
   Starlette `Mount` applications run INSIDE the parent middleware
   stack on the pinned versions: `build_middleware_stack` wraps
   `self.router` (`starlette/applications.py:75-93`, Starlette
   0.52.1) and `app.mount()` registers Mounts inside that same
   router (`routing.py:373+`, FastAPI 0.133.1). Therefore
   `add_middleware(AuthMiddleware, ...)` IS applied to
   `/api/mcp/kb/*` — the original "Mounts run OUTSIDE parent
   middleware ⇒ KB stays unauthenticated" reading was wrong, and
   unamended it would 401 every cross-process KB-MCP consumer
   (local agents) on day 1 of auth-ON. **Fix adopted:** `/api/mcp/kb`
   and `/api/mcp/kb/sse` are in `AUTH_DEFAULT_PUBLIC_PATHS` for v1
   (preserves today's loopback posture verbatim — the middleware
   never touches them); the loopback trust rationale is documented
   in the runbook (P6); a KB-MCP-auth seam is RESERVED for a future
   commission (recommendation §3) instead of the false claim.
   `[v1.4: KB-MCP entries moved out — module-lane class; see decisions.md D19 + D18 three-class derivation]` (AMEND v1.4 / module-lanes — the "Fix adopted" placement above is superseded: the mounts are MODULE-LANE, passed through untouched by the OAuth2 middleware in both auth states; the Mount-traversal fact and the 401-avoidance goal stand, the class changes).
   **Framework pin + regression guard:** plan pins Starlette 0.52.1
   / FastAPI 0.133.1, and P3 carries a one-line test asserting
   Mounts traverse the auth middleware (fails loudly if a framework
   upgrade changes Mount/middleware composition — the D1 regression
   guard).

7. **Default-OFF behaviour.** When `settings.auth.enabled is False`,
   the AuthMiddleware constructor early-returns and replaces
   itself with identity (`self.app = app`; no per-request branching).
   This is **not** "registered as a no-op" but "functionally
   equivalent to not being registered at all" — proven by the
   construction order: `app.add_middleware(AuthMiddleware)` is
   conditional on `settings.auth.enabled`; the wiring sits in
   `create_app()` at the same call site as the other three,
   gated by an `if settings.auth.enabled:` block. **This is the
   zero-disruption proof**, not "middleware always registered and
   inspects a flag every request" (which has measurable per-
   request overhead).

   Alternative (less recommended): register as pass-through and
   check `scope["state"]["auth_disabled"]` per request. Rejected:
   extra per-request branch for a default-OFF product; mixes the
   concerns.

## Redirect URI Resolution (Detailed)

```
build_redirect_uri(request) -> str:
    config = request.app.state.auth_config   # AuthConfig section
    if config.external_redirect_base:
        return f"{config.external_redirect_base.rstrip('/')}/api/auth/callback"
    # AMENDED v1.1 (D2, architect must-fix): the resolver MUST receive the
    # live-view operator override as its tier-1 argument — an operator who
    # set only ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL must not be silently
    # skipped. AuthConfig carries a one-shot copy of the live-view value
    # (populated at lifespan from config.live_views.external_base_url —
    # a plain string; same pattern as bind_host/bind_port today). DI-clean:
    # AuthService never imports live_views.
    resolver = BaseURLResolver(
        external_base_url=config.live_view_external_base_url_fallback,
        bind_host=config.bind_host,
        bind_port=config.bind_port,
        host_recorder=request.app.state.host_recorder,
    )
    base = resolver.resolve()
    if base is None:
        # Operator error: the IdP will reject the callback anyway
        # (bad `redirect_uri` registration); refuse the login now
        # rather than minting a relative-URL callback that no IdP
        # will accept.
        raise AuthConfigurationError(
            "external_redirect_base (or ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL) "
            "is required when no Host has been captured yet. Set one and restart."
        )
    return f"{base}/api/auth/callback"
```

Decision (when `resolve() is None` and auth is enabled):

- **Reject the login attempt** with `503 LOGIN_NOT_CONFIGURED`.
  Rationale: a path-relative `redirect_uri` like `/api/auth/callback`
  is NOT a valid `redirect_uri` per OAuth2 (RFC 6749 §3.1.2 — must
  be absolute). Issuing one would let the IdP reject the user
  bounce at the callback step (worse UX) or — worse — let an IdP
  accept it against a stale absolute registration (security
  regression).
- For backend-to-backend IdPs that accept path-relative URIs
  (none in practice), the alternative is to mint relative and
  pre-flight the IdP registration; rejected as non-portable.

**Operator-facing wire-up** (documented in the runbook the plan-
creation worker produces):

1. Operator registers `https://daemon.example.com/api/auth/callback`
   (or `http://host:port/api/auth/callback` for LAN-only).
2. Operator sets `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` OR
   `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` to the same scheme+host
   (+optional port).
3. Operator optionally pre-warms the Host recorder by sending one
   request through the daemon before the first user logs in
   (the SPA hit on `/` does this naturally if same-origin).
4. Daemon validates `redirect_uri` registration by HTTP GET against
   the IdP's `/authorize` endpoint at first login (the IdP itself
   rejects mismatches with `redirect_uri_mismatch`).

**Host-poisoning trust model (from `docs/runbooks/live-views.md §6`):**

> Trust model: the captured Host header is **trusted** for the
> purpose of minting same-origin URLs, but **NOT trusted** for
> authenticating the caller. OAuth share-link URLs minted from
> poisoned hosts ARE STILL CONSIDERED POISONED — operators must
> set `external_base_url` in any deployment where the Host header
> is set by an untrusted intermediary.

Applied to OAuth: a `redirect_uri` minted from a poisoned Host is
a poisoned callback. Mitigation is identical: operator override.

## Session Model (Detailed)

**Cookie** (set at issue, cleared at logout):

```
Name:       ensemble_sid
Value:      URL-safe random 32 bytes (collision-resistant, ≈ 256-bit entropy)
Flags:      HttpOnly (always)
            Secure  (default ON; configurable for plain-HTTP LAN via
                     `allow_insecure_cookie: true`)
            SameSite=Lax  (default; needed for OAuth redirect-back on
                     top-level navigation from IdP; Strict would break it)
                     (configurable to Strict via `cookie_samesite="Strict"`)
Path:       /
Max-Age:    session_ttl_seconds (default 8 h)
```

**CSRF** (cookie-session + state-changing methods):

- For `POST /api/auth/logout` and any other state-changing route
  (SPA POST/PUT/PATCH/DELETE under cookie session): require
  `X-CSRF-Token` header. The token is generated at session issue,
  stored alongside the session row, and set as a
  non-HttpOnly readable cookie named `ensemble_csrf` (separate
  cookie, so the SPA can read it and echo it back in a header).
- This is the **double-submit cookie** pattern, the well-known
  mitigation that survives the absence of `SameSite=Strict`.
- `SameSite=Lax` blocks cross-site POST and `Content-Type: text/plain`
  requests, so the `X-CSRF-Token` is a belt-and-suspenders.
- For SSE / GET routes: no CSRF token required (idempotent reads).

**Session row schema (PG + SQLite compat):**

```sql
CREATE TABLE auth_sessions (
    id              TEXT PRIMARY KEY,            -- URL-safe 32 bytes
    subject         TEXT NOT NULL,               -- OIDC "sub"
    email           TEXT,                        -- OIDC "email" (optional)
    scopes          TEXT NOT NULL,               -- CSV of granted scopes
    auth_method     TEXT NOT NULL,               -- 'session' | 'bearer'
    user_agent      TEXT,
    ip_subnet       TEXT,                        -- /24 IPv4 or /64 IPv6
    created_at      TIMESTAMP NOT NULL,
    last_used_at    TIMESTAMP,
    expires_at      TIMESTAMP NOT NULL
);
CREATE INDEX idx_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE TABLE auth_states (
    id                   TEXT PRIMARY KEY,       -- the 'state' param
    code_verifier        TEXT NOT NULL,
    code_challenge       TEXT NOT NULL,
    challenge_method     TEXT NOT NULL,           -- 'S256' (never 'plain')
    redirect_to          TEXT,                    -- SPA path to return to post-login
    created_at           TIMESTAMP NOT NULL,
    expires_at           TIMESTAMP NOT NULL,
    consumed_at          TIMESTAMP                -- single-CAS marker
);

CREATE TABLE auth_bearer_tokens (
    id_hash         TEXT PRIMARY KEY,            -- SHA-256(token)
    subject         TEXT NOT NULL,
    scopes          TEXT NOT NULL,
    label           TEXT,                        -- operator-friendly name
    created_at      TIMESTAMP NOT NULL,
    expires_at      TIMESTAMP NOT NULL,
    last_used_at    TIMESTAMP,
    revoked_at      TIMESTAMP                    -- soft revocation
);
CREATE INDEX idx_auth_bearer_tokens_expires_at
    ON auth_bearer_tokens (expires_at);
```

- **Survival requirement.** Routinely-promote restart wipes in-
  memory state; PG-backed sessions MUST survive. The migration
  file `daemon/migrations/versions/<ts>_create_auth_tables.sql`
  follows the template at
  `daemon/migrations/versions/20261007_000001_create_drift_events.sql`.

- **TTL + rotation.** Default 8 h. Sliding renewal OFF by default
  (operator configurable; OFF keeps audit trails stable —
  `instance_metadata.events[]` keyed to `expires_at`). Last-used
  update is `1m < last_used_at`-batched to avoid a write per
  request.

- **Sweeper.** Background `asyncio.create_task(...)` started in
  the lifespan (alongside `DependencyBus`) at
  `daemon/api.py:1544-1555` (lifespan site — the readiness-refresher
  task pattern; NOT `daemon/manager.py:315-345` which is the
  manager's fire-and-forget variant). **AMEND v1.3 / F8** (line-drift
  correction — phase2 T2.4 / Minor #2 already received the same fix).
  Sweep interval 5 min. Deletes rows in batches of 1000 with
  `expires_at < now()` from `auth_sessions` and `auth_bearer_tokens`.
  `auth_states` has a separate 60-second-loop sweeper (states are
  short-lived).

- **Graceful degradation.** If the `auth_sessions` table is
  unreachable (PG outage) and auth is enabled:
  - All public paths (`/livez`, `/readyz`, `/api/auth/login`,
    `/api/auth/callback`) remain GREEN. The callback path will
    FAIL with `503 IDP_REJECTED` + operator-visible error.
  - All protected paths return `503 AUTH_TEMPORARILY_UNAVAILABLE`
    with `Retry-After: 30`.
  - `/readyz` reports `auth_dependency_status` as a body sub-field (FR-30 enum: `"ok" | "degraded" | "unconfigured"` — `degraded` when the IdP is unreachable but the validate path is still PG-only; `unconfigured` when `auth.enabled = false` OR `auth.enabled = true` but `LoginNotConfigured` per FR-28) but does NOT change the composite verdict (the readiness composite for the daemon's external DB already fails closed — adding auth doesn't change the failure mode). **AMEND v1.3 / B4:** the v1.2 text "auth_dependency_status=fail" is superseded — the FR-30 enum values `"ok" | "degraded" | "unconfigured"` are the canonical values (no `"fail"` member); verdict NEVER flips to 503 due to auth state per OD-9 / AC-31. **Decision needed**: see Open Technical Questions §OQ-1.

- **Logout.**
  - `POST /api/auth/logout` requires a valid session.
  - Single-CAS `DELETE FROM auth_sessions WHERE id=?`. Cookie
    cleared via `Set-Cookie: ensemble_sid=; Max-Age=0`.
  - Returns `204 No Content`.
  - **`logout` is NOT a redirect to the IdP** in v1 — the
    out-of-band `end_session_endpoint` redirect is documented for
    future v2 (operator opt-in via `auth.idp_logout_enabled=true`).

## Failure Modes (Detailed)

| Scenario | Public paths | Protected paths | Health probes |
|---|---|---|---|
| Auth **off** (default) | unchanged | unchanged | unchanged |
| Auth on, IdP healthy | unchanged | unchanged | unchanged |
| Auth on, IdP down | login redirects 503 LOGIN_NOT_CONFIGURED; callback 401 IDP_REJECTED | cookie sessions still validate (offline-tolerant) | unchanged |
| Auth on, `auth_sessions` table down | login/callback 503 AUTH_TEMPORARILY_UNAVAILABLE | 503 + `Retry-After: 30` | /livez GREEN; /readyz: degraded — see OQ-1 |
| Boot misconfig (enabled + missing required field) | fail-fast at **boot preflight** (AMENDED v1.1 / C4 — exit 78 BEFORE `uvicorn.run`; the original lifespan site was unreachable-as-specified: a lifespan RuntimeError exits via uvicorn's generic path, exit 1, post-port-bind) | n/a | n/a |
| Restart mid-session | sessions survive (PG) | sessions validate post-restart | GREEN |
| Clock skew (IdP clock ahead/behind local) | protected paths keep validating JWT until `exp`/`nbf` collide with `±skew_seconds` | ± 60 s grace via `auth.clock_skew_seconds` | n/a |
| Replay (state reused by attacker or duplicate-tab race) | callback 400 STATE_REPLAYED | n/a | unchanged |
| Authorization-code replay (single-use enforcement at IdP) | callback 400 CODE_REPLAYED on IdP-side rejection | n/a | unchanged |
| CSRF (cross-origin POST on cookie session) | n/a (login is GET) | 403 CSRF_TOKEN_INVALID | unchanged |
| Bearer-token replay by leak | n/a | token is rotated on suspicion; the bearer lane has its own revoke endpoint (future) | unchanged |
| `resolve() == None` at first login (no Host yet) | login 503 LOGIN_NOT_CONFIGURED; operator must set `external_redirect_base` (or liveview `external_base_url`) and restart, OR pre-warm Host via SPA hit | n/a | unchanged |

**Structural guarantee for health probes.** `AuthMiddleware` evaluates
`scope["path"] in self._public_path_set` BEFORE any other logic.
The set is constructed at middleware instantiation
(`__init__`), so no async work, no DB call, no IdP call, no clock
check. **AMENDED v1.1 (C3):** the set is *derived* at construction
from route metadata — routes carry a `public-probe` tag/attribute
(prefix rules allowed for `/api/auth/*` + the SPA PUBLIC-ASSETS
family); construction walks `app.routes`, builds the same O(1)
frozenset, and **logs the derived set at startup** (reviewable in
promote transcripts). Request-time behavior is byte-identical to the
original hand-listed design; the change kills the frozenset-drift
class (the drift hazard was already scheduled: the reserved
`draining` field at `readiness.py:238-239` + `api.py:3195-3197`
means a future `/drainz`-style probe is planned — hand-listed, it
would 401 at the promote gate; derived, it cannot).
`/livez` answers within the same event-loop tick as
`daemon/api.py:3172-3186` does today — pure CPU. `/readyz` reads
`app.state.readiness_composite` (`:3188-3238`), an in-memory cache
populated by `_periodic_readiness_refresh_loop`; this loop is
unaffected by auth (auth_failure is recorded into the composite as
an additional field but does not flip the verdict — see OQ-1).
Health probes are public in EVERY scenario above by construction.
**Publicness precision (v1.1):** the invariant is PUBLICNESS —
never 401, no auth dependency — NOT "200 in every scenario."
During a PG outage `/readyz` legitimately 503s via the pre-existing
`database` component; that is the readiness contract working, not an
auth regression. ACs assert never-401 + reachability, not 200.

**Token / state replay handling.**

- `state` parameter: stored once in `auth_states`, single-CAS
  `pending → consumed` at the callback. Loser gets `400 STATE_REPLAYED`.
- `code` parameter: not stored locally — the IdP's `/token` endpoint
  enforces single-use; IdP-side rejection surfaces as `401 IDP_REJECTED`.
- PKCE `code_verifier`: stored once alongside `state`. Replay attempt
  against a consumed state fails at the state CAS first (before
  the IdP exchange).

**Clock skew.** `nbf` and `exp` claim comparisons allow
`± auth.clock_skew_seconds` (default 60 s). Configurable per
deployment. JWKS rotation: a `kid` not found in the cached JWKS
triggers a one-shot forced refresh; second miss returns
`401 JWKS_UNAVAILABLE`.

**Boot fail-fast misconfig. AMENDED v1.1 (C4):** static misconfig
validation runs at the **boot preflight**, NOT at lifespan. The
exact precedent sits one screen away: `_boot_db_preflight` runs
before `uvicorn.run` and exits 75 (`daemon/__main__.py:319-324`).
A lifespan `RuntimeError` exits via uvicorn's generic failure path
(exit 1 + traceback, AFTER the port bind) — it could never deliver
NFR-7's "exit 78 + ERROR + missing-field list" contract. So: in the
boot preflight (beside the DB preflight), if `auth.enabled is True`:

- Missing any of `issuer`, `client_id`, `client_secret` →
  ERROR naming each missing env var + **exit 78** (`EX_CONFIG`);
  the daemon never binds a port, never partial-boots.
- Config-level combination validators ([REQ] §6.2/§6.3) remain the
  import-time fast-fail (also pre-bind); the preflight is the
  authoritative exit-78 surface with the operator-actionable
  missing-field enumeration.
- Discovery fetch non-2xx AND no override endpoints configured →
  **stays at lifespan** as a WARN (network I/O does not belong in
  config-time validators / the preflight); remember failure for
  runtime (callback returns `503 IDP_NOT_CONFIGURED`).

## Machine-Client Lane (Detailed)

**Architectural decision: introduce two parallel lanes, both
gated by the same `AuthMiddleware`.** The decision is non-obvious
because the 12 inventoried consumers are loopback operators who
already work; the question is whether to expose any new auth
surface for them at all in v1.

**Lane A: Cookie session (default for browsers).** All web traffic
from the SPA falls here.

**Lane B: Static bearer tokens (machine consumers).** Operators
mint a token via the lifespan-time CLI command
`python -m daemon.auth.cli issue-token --label <name> --ttl 30d`
(out of scope here — design only). The CLI prints the token ONCE
and stores only `sha256(token)` in the `auth_bearer_tokens` table.
Consumers store the token in env (`ENSEMBLE_BEARER_TOKEN=...`) or
config. Each call sends `Authorization: Bearer <token>`.

**Middleware dispatch:**

```python
# pseudocode for AuthMiddleware.__call__ (non-public path)
if scope["type"] == "websocket":
    return await self.app(scope, receive, send)
cookie = parse_cookie(scope, "ensemble_sid")
if cookie:
    ctx = await self.validate_session(cookie)
    if ctx is not None:
        return await self._dispatch(scope, receive, send, ctx)
auth_header = parse_authorization(scope)
if auth_header and auth_header.scheme.lower() == "bearer":
    ctx = await self.lookup_bearer(auth_header.credentials)
    if ctx is not None:
        return await self._dispatch(scope, receive, send, ctx)
if is_browser_html_request(scope):
    redirect_back = sanitize_return_to(scope.query.get("return_to", "/"))
    return await self._respond_302(f"/api/auth/login?return_to={redirect_back}")
return await self._respond_401(
    'Bearer realm="ensemble"'
)
```

**Loopback exemption debate.** Loopback calls (127.0.0.1,
::1, the operator-configured bind host) could be exempted from
auth — but the KB-flagged risk (`daemon/routers/jobs_crud.py:595`
— client-supplied `source=body.source`; `messages.py:690` —
hard-coded `source="api"`; line numbers corrected v1.1) is that
**unauthenticated loopback already
forges `source=api` on POST /jobs, /messages**. A blanket
loopback exemption would preserve this known risk AND silently
grant full create access to any process that can bind 127.0.0.1.
**R3 clarifier (v1.1):** auth does NOT close these forge lanes —
a stolen `jobs:write` bearer still forges `source`. Durable closure
= derive `source` from authenticated `AuthContext.subject` when
auth is ON; that is FILED as a separate follow-up ticket ("R3:
derive source from AuthContext"), out of this plan's scope.

**Recommendation: NO blanket loopback exemption.** The 12 inventoried
loopback callers are operator-trusted; they get the bearer-token
lane (Lane B). Operators mint one token per caller family
(`promote-rotate`, `promote-soak`, `frontend-spa`, `boot-e2e-…`,
`opendesign-via-opendesign-mcp`, etc.), each with the minimum
required scopes:

```
Scopes:
  system:read          # /readyz, /livez, /api/health, /api/info
  system:probe         # version-verify endpoint during promote
  instances:read       # GET /api/instances/*, GET /api/instances/*/events
  instances:write      # POST /api/instances, send_message
  jobs:read            # GET /api/jobs/*, GET /api/jobs/*/events
  jobs:write           # POST /api/jobs, /api/jobs/*/cancel, ...
  maintenance:read     # /api/maintenance/checkpoint-cleanup/availability, etc.
  (specific scopes assigned per consumer — the requirements worker
   produces the consumer × scope matrix)
```

When a consumer does not have the right scope for a route, the
auth middleware returns `403 SCOPE_INSUFFICIENT` (the route is
in `/api`, so 403 not 404; this matches typical convention).

**Empty / unset env path.** When `settings.auth.enabled is False`,
the middleware is not registered (default OFF; zero behavioral
change). When it IS enabled but a consumer is misconfigured,
the consumer's call returns 401 (not a daemon crash) so the
operator sees the failure and rotates the token.

## 401 Contract (Detailed)

**Machine-client path** (Accept contains `application/json`, or no
Accept, or `*/*`):

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="ensemble", error="invalid_token",
    error_description="Session expired or invalid"
Content-Type: application/json

{"error": "AUTH_REQUIRED", "message": "Authentication required"}
```

**Browser path** (`Accept: text/html`):

```
HTTP/1.1 302 Found
Location: /api/auth/login?return_to=/api/instances%3Fgroup%3Dactive
```

The browser then completes the IdP flow and is bounced back to
`return_to`.

**Client-type discrimination mechanism.** Three signals, evaluated
in order:

1. **`Accept` header parse.** `text/html` ⇒ browser ⇒ 302.
   `application/json` (explicit) ⇒ machine ⇒ 401.
2. **Path-family heuristic** (fallback if `Accept: */*`):
   `/api/auth/*`, `/livez`, `/readyz`, `/api/health`, `/api/info`
   → 401. Everything else → 302.
3. **Explicit hint** (future): `?as=api` query param forces
   the 401 path regardless of Accept. Out of scope for v1.

This gives curl callers a 401 they can read; gives browsers a
redirect they can follow.

## Reserved Seams (Detailed)

Already described at the end of §Component Architecture; expanded
here as a checklist the planner can cite:

| Seam | Phase-N+1 feature | Where it plugs in |
|---|---|---|
| `AuthContext` object on `request.state` / `scope["state"]` | Per-view share tokens, password-gated views | `daemon/routers/live_views.py:274-391` reads the context |
| Per-root authorization policy hook | Anonymous `/views` roots | `daemon/routers/live_views.py` injects a per-route `dependencies=[]` |
| Grants table (PG) | RBAC for views | New migration `..._create_grants.sql` |
| `allow_insecure_cookie` config flag | Plain-HTTP LAN deployments | `daemon/config.py AuthConfig` |
| WebSocket auth branch in `AuthMiddleware` | `/vscode` auth | `vscode_proxy.py:1092` |
| Token revoke endpoint (admin) | Bearer leak response | New route, scope-gated |
| IdP `end_session_endpoint` redirect | Federated logout v2 | `auth.idp_logout_enabled` config flag |
| Multiple IdP support (Workforce + Customer) | Multi-tenant | Per-route `idp_alias` config |

## Testability Architecture (Detailed)

**In-process fake OIDC/OAuth2 provider.** New module
`tests/fakes/fake_oidc_provider.py` modelled on
`tests/e2e/mock_source_server.py`. Pure ASGI sub-app. Five routes:

```
GET  /.well-known/openid-configuration  -> static discovery doc
GET  /authorize                          -> 302 to registered redirect_uri
                                            with code + state + PKCE-validating
POST /token                              -> validates code_verifier + client_secret
                                            returns ID token (signed with test keypair)
GET  /jwks                               -> JWKS with test public key
GET  /logout                             -> no-op (constant introspection target)
```

The test ID token is signed with an RSA keypair generated at fixture
setup; the daemon is configured with `JWKS_URI_OVERRIDE` pointing at
the in-process server. The `httpx.AsyncClient` in pytest uses
`ASGITransport` against the `mount("/", app=app)` composition
(app + fake_oidc sub-app on `/_test_oidc/`).

**No Docker.** In-process. Same event loop. Same DB connection.

**Test seam for `app.state.host_recorder` pre-warming.** A fixture
sends one synthetic request to a discardable route to populate
the recorder, then runs the test that needs a non-None base URL.

**Test cases (recommended counts; requirements worker owns the matrix):**

- ~30 unit tests for `AuthService` (state CAS, TTL expiry, sweep,
  scope parsing, clock skew).
- ~15 tests for `OAuthClient` (discovery cache TTL, JWKS kid-miss
  path, error response handling).
- ~10 tests for `BearerToken` mint + verify + revoke + constant-time
  compare.
- ~20 integration tests under `tests/` using `ASGITransport` with
  both `auth_disabled_app` (default) and `auth_enabled_app` (new).
- 1 boot-e2e shell script `frontend/scripts/boot-e2e-auth-daemon.sh`
  matching the `boot-e2e-snapshots-daemon.sh` and
  `boot-e2e-maintenance-daemon.sh` pattern.

**Coverage guarantees:**

- `/livez` 200 in every scenario above (table-driven).
- `/readyz` behaviour parity with OFF-state when auth-enabled and
  healthly; degraded field added when auth is degraded (per OQ-1).
- The 12 inventoried consumers keep working (each gets a bearer
  token in the test fixture; tests verify that `Authorization:
  Bearer <token>` yields 200).

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | **CORS wildcard+credentials × cookie auth**: cross-site credentialed reads already possible; cookie auth compounds this | Medium | High | When auth is enabled, set `auth.allowed_origins`; refuse to start if wildcard and credentials both true. Document the dependency in the runbook. (Defers the broader CORS hardening — the 2026-10-10 KNOWN RISK note in the project CN — to a separate ticket; do not conflate.) |
| 2 | **Secure cookie + plain-HTTP LAN deployment** | High (LAN use) | Medium | `allow_insecure_cookie: bool = False` default. Operators MUST explicitly opt in; the runbook spells out that this trades confidentiality for LAN-only deployability. |
| 3 | **SameSite × OAuth cross-site redirect return** | Low | Medium | Default `SameSite=Lax` (Strict would break top-level POST redirects from IdP). Document the trade-off; provide `cookie_samesite` config knob. |
| 4 | **Session table as new DB dependency on the auth path** | Low (PG already primary) | Medium | (a) Sessions are PG-primary — **AMENDED v1.1 (D17, leader ruling): auth tables are created on BOTH backends (inert when unused; the SQLite migration runner auto-applies every non-`MANUAL: TRUE` `.sql` at boot, `daemon/migrations/runner.py:107-113`) and the store is a single code path — no backend-conditional schema, no SQLite boot-refusal;** (b) Readiness composite reports `auth_dependency_status` as a sub-field; (c) Sweep task is decoupled from the auth-request path (background coroutine) — table being slow does not block validate_session. |
| 5 | **Bot brute-force on `/api/auth/login`** | Low (same-origin only) | Medium | The `/authorize` redirect is to the IdP — bot traffic shows up as IdP traffic. The IdP fronting absorbs the volume. Local ban of IP is out of scope (IdP's job). |
| 6 | **Session fixation**: pre-set `ensemble_sid` cookie | Low | Medium | Sessions are server-side; an attacker-set cookie without server-side row yields a miss. The "rotate cookie on auth state change" guideline: when a session is issued (post-IdP exchange), the existing pre-set cookie (if any) is invalidated server-side AND the response sets a fresh cookie. The middleware enforces this at the callback step. |
| 7 | **JWKS poisoning** (attacker returns fake JWKS) | Very Low | High | JWKS is fetched over HTTPS with cert verification; cached; HTTP redirects refused. Documented in `OAuthClient.fetch_jwks`. |
| 8 | **Replay of static bearer tokens** | Low | High | Tokens are stored as `sha256(token)`; the plaintext exists only at mint and in the operator's secret store. Leak rotation: separate `POST /api/auth/admin/tokens/revoke` endpoint (future). Sliding validity by default OFF keeps forensic snapshots stable. |
| 9 | **`host_recorder` last-write-wins is global**, not per-request | Medium | Medium | Inherited documented behaviour; operator override is the cure; pre-warm via SPA hit on `/`. |
| 10 | **`/vscode` WebSocket auth surface deferred** | Medium | Medium | Documented; reserved as a future seam. Today's loopback posture stands. |
| 11 | **Default-OFF proof**: how do we PROVE identical behavior when off? | Medium (regression risk) | High | (a) The `AuthMiddleware` is conditionally registered (not "always registered as no-op"). (b) The integration test matrix proves byte-equivalent response on `/livez`, `/readyz`, `/api/health`, `/api/info`, and a sample of `/api/*` GETs when auth is off vs auth-on-with-bypass-none. (c) Promote-loop e2e continues to pass identically with auth=off. |

## Scalability

### Growth Assumptions

- Users × N: ~10² simultaneous authenticated browser sessions
  (operator-facing; small team).
- Sessions × M: bounded by active users × TTL/refresh; sweep
  bounds stored rows by TTL.
- Auth requests per second: ~10¹–10² (bounded by the operator's
  interactive load).
- Machine-consumer rate: ~10⁰–10¹ (cron scripts; trivial).
- Tokens in DB: ~10² (operator mints; long-lived).

### Current Bottlenecks

| # | Bottleneck | Threshold | File:Line | Impact |
|---|---|---|---|---|
| 1 | Session validate = 1 DB row lookup | At ~10² RPS, becomes per-request hot path | `daemon/repositories/auth_session/repository.py` (new) | Latency +1 ms; acceptable. Add a small in-memory LRU cache keyed by session id (~5 min TTL) when traffic justifies it (NOT in v1). |
| 2 | Discovery fetch at first callback in cold processes | Once per process | `daemon/services/oauth_client.py` (new) | 1 round trip; cache 24 h. Negligible. |
| 3 | Sweep task lock contention | Sweep batches 1000 rows per 5 min | `daemon/services/auth_session/sweeper.py` (new) | Requires `SELECT ... FOR UPDATE SKIP LOCKED` (PG) for safe parallel sweeps; one sweeper per daemon. |
| 4 | `BaseURLResolver.resolve()` called per login + per callback | Once per login + once per callback | `daemon/services/live_views.py:617-703` | Negligible; pure-CPU string normalisation. |

### Scaling Characteristics

- **Vertical vs horizontal:** Stateless for browser traffic
  (the cookie session points to a PG row); horizontal scaling is
  safe behind a load balancer — any replica can validate any
  session.
- **Stateless vs stateful:** SESSION-stateful via PG; the
  middleware itself is stateless. `id_hash` of bearer tokens
  is PG-backed; a leaked token works from any replica.
- **Sync vs async:** All paths async (asyncio). IdP calls go via
  the existing `httpx.AsyncClient` instance used by LLM
  failover (no new connection pool).
- **Scaling cliffs:** None in the expected range. Future concerns
  (millions of sessions, JWKS rotation storms) are out of scope.

## Technical Debt

### Items Affecting This Analysis

| # | Debt Item | Impact on Recommendation | Severity | File:Line |
|---|---|---|---|---|
| 1 | CORS wildcard+credentials — KNOWN RISK flagged 2026-10-10 | Forces the auth-on config to require an explicit `allowed_origins`; without this mitigation we compound the risk. Recommendation MUST be conditional on this. | High | `daemon/api.py:3015-3021` |
| 2 | CORSMiddleware runs INSIDE AuthMiddleware on the response phase | Does not affect the request path, but CORS headers on a 401 are still a CORS concern; mitigation is in the middleware ordering discussion. | Low | LIFO ordering analysis in §Middleware Placement |
| 3 | `BaseURLResolver` is a liveview-specific service instance; reusing it from AuthService means AuthService takes a liveview dep. | A subtle cross-domain dep. Mitigation: AuthService receives the resolver (or just `host_recorder` + bind config) by DI; no import-from-liveviews is required at the file level. | Low | `daemon/services/live_views.py:524-703` |
| 4 | No session/token table today — net-new PG table | Forces a new migration file; follow the existing pattern (`drift_events.sql` template). | Medium (routinely handled) | `daemon/migrations/versions/20261007_000001_create_drift_events.sql` |
| 5 | No existing cookie-session repository precedent | The `auth_session` repository is a fresh pattern. Follow the `daemon/repositories/<domain>/` convention. | Medium | `daemon/repositories/` |
| 6 | DB constraint: repository abstractions retain SQLite compatibility | The session store SQL above is portable, but the `ip_subnet` / `user_agent` columns work in both. PG is primary — repositories are tested against both per the `Core Architecture` blueprint. | Low | `daemon/constants.py:147` (CHECKPOINT_TTL_HOURS precedent) |
| 7 | Read-only /api/maintenance/checkpoint-cleanup/availability is the boot-e2e canary today | When auth is ON, this 401s. The boot-e2e maintenance script needs a bearer token to keep working. The plan MUST update that script. **AMEND v1.3 / F7 — SUPERSEDED by FR-22 + FR-23 + phase5 G5.3** (the canary path is PUBLIC per the route-metadata derivation (D18 / D1); the canary runs unchanged against an auth-ON daemon — `frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182` does NOT need bearer-token support; AC-22 + AC-32 prove it). The risk recorded here is structurally closed by D18 — `canary bearer token MUST` (originally proposed) is wrong; do NOT "fix" the canary script with a bearer-token path. | ~~High (blocking ship without fix)~~ Closed (was a planning pre-D18 assumption) | `frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182` |
| 8 | Read-only /api/health is unauthenticated today | Health-check scripts that consume JSON expect this to stay public. Plan must keep `/api/health` in the public-path set. | Medium | `daemon/api.py:3046` |
| 9 | HostCapture host recording is GLOBAL (not per-request) | First-login `redirect_uri` issue on a freshly-rebooted daemon, before any request has warmed the recorder. Mitigation: operator override + pre-warm; the first SPA hit on `/` fixes this in practice. | Medium | `daemon/middleware/host_capture.py:33-57` |

### Items NOT Affecting This Analysis

- The 2026-10-10 checkpoint-connection-pool fix (closed). Affects
  the daemon's runtime, not the auth surface.
- The `/api/notifications/stream` EventSource defect. Streaming
  notifications are unaffected by the auth scheme (cookie
  auth works).
- The open defect on error-lane premature completion
  (`daemon/services/error_reporting.py:222-296`). Not auth-related.
- The sketcher / critic pipeline. Not auth-related.

### Recommended Paydown

In priority order, ONLY items that affect this analysis:

1. ~~**MUST** update `frontend/scripts/boot-e2e-maintenance-daemon.sh`
   to send a bearer token (or read one from env) when auth is
   enabled. The plan fixes it.~~ **AMEND v1.3 / F7 — SUPERSEDED by
   FR-22 + FR-23 + phase5 G5.3** (same ruling as Debt #7 above): the
   canary route is PUBLIC per the route-metadata derivation (D18/D1)
   and the canaries run UNMODIFIED against an auth-ON daemon. Do NOT
   add bearer-token support to the maintenance canary — phase6 T6.5
   asserts the scripts stay at zero `git diff`.
2. **MUST** tighten CORS for auth-on deployments; document in the
   runbook that wildcard+credentials must NOT co-exist with auth-on.
3. **SHOULD** add an admin endpoint to rotate bearer tokens (future).
4. **MAY** add a JWKS-rotation automatic refresh in v2 (NOT in v1).

## Open Technical Questions

These items need explicit decisions BEFORE the plan-creation worker
drafts `phaseN-plan.md`. They are not blockers for the architecture
shape, but they affect the on-disk artifact.

**OQ-1. `/readyz` composite when auth is degraded.** Should an
auth dependency failure flip `/readyz` from 200 → 503, or only add
a sub-field to the response body (keep 200)? Two arguments:

- Auth being down doesn't take user-facing traffic down entirely
  (browsers can still log in via the IdP path), so keep /readyz 200.
- An IdP-down state should be surfaced as not-ready (the daemon can
  issue sessions but cannot validate them; pending state).

**Recommendation:** Add a sub-field `auth_dependency_status` to the
composite; do NOT flip the verdict. Argument: the readiness composite
is the aggregate of "can I serve user requests"; auth degradation
does not change that boundary (the public paths still work).

**OQ-2. Static-token mint surface.** Should the CLI command
`python -m daemon.auth.cli issue-token` exist, or should minting
happen via a one-shot HTTP endpoint requiring already-valid session
(authenticated admin)? Trade-off: CLI keeps the operator secret
out of any HTTP path; HTTP endpoint requires a privileged session
first (bootstrap problem).

**Recommendation:** CLI for v1; HTTP endpoint for v2 (requires admin
scope). Admin privilege bootstraps from a `daemon.auth.bootstrap_token`
env token (one-time mint path) — design clarification needed.

**OQ-3. Scopes granularity.** The matrix in §10 is illustrative;
the requirements worker produces the per-consumer authoritative
matrix. Architectural question: which routes need a scope check
and which are "session-valid is enough"? Recommendation: starts
broad (only `system:read` vs `everything-else`) and tightens
later as concrete pressure emerges.

**OQ-4. `/vscode` WebSocket auth.** Today's loopback posture stands.
Should the v1 auth layer also gate the WebSocket? Decision:
out of scope here; the seam is reserved.

**OQ-5. Multi-IdP support (workforce + customer).** Out of scope
for v1 (single issuer); the `auth.idp_alias` config hook is the
seam.

**OQ-6. Federated logout via IdP `end_session_endpoint`.** Out of
scope for v1 (operator opt-in via `auth.idp_logout_enabled` flag
in v2). Today's logout is local-cookie-clearing.

**OQ-7. CSRF strategy under SPA same-origin reality.** Today the
SPA is same-origin (browser same-origin policy says cross-origin
POST is blocked by default at the CORS layer). `SameSite=Lax` blocks
most cross-origin POSTs. Do we need the X-CSRF-Token double-submit
for v1, or wait until v2 when CSRF attack surface concretizes?

**Recommendation:** Yes, implement the double-submit cookie NOW.
Cost is ~10 LoC; protects against regressions (e.g., a future
CORS whitelist that includes a less-trusted origin).
**AMEND v1.3 / F9 — SUPERSEDED by `decisions.md D8 v1.2`:** the shipped
mechanism is the Origin-guard PREDICATE (fires ONLY when `Origin` /
`Referer` headers are present — browser lane; headerless machine/bearer
requests are EXEMPT per the rule-1 precedent at
`daemon/routers/maintenance_origin_guard.py:24-43`). Token-echo against
the `ensemble_csrf` cookie is reserved as v2 defense-in-depth. Do NOT
implement standalone double-submit as the v1 mechanism.

**OQ-8. Per-view authorization hook.** The `AuthContext` shape is
fixed; per-route policy hook is reserved but not built. Where is
the policy store — route metadata on the decorator, or a separate
config map? Recommendation: decorator on the router handler
(`@require_scope(...)`); minimal change, idiomatic FastAPI.

**OQ-9. Subject identification across IdP changes.** If the IdP
itself is rotated (one workforce IdP replaced by another), how does
the daemon re-bind `sub` to the same human? Recommendation: do not
solve in v1 — treat `subject` as opaque. Optional `subject_alias`
table in v2.

**OQ-10. CORS hardening entanglement.** The KNOWN RISK note
(`api.py:3015-3021`) is a separate ticket. Recommendation: do NOT
harden CORS in this plan (scoped per the dispatch); link the
ticket in `decisions.md` so the planner sees it.

## Assumptions (consolidated)

| # | Assumption | Affects |
|---|---|---|
| A1 | The IdP supports OIDC discovery (modern). | Flow choice. |
| A2 | Operator can register a `client_id`/`client_secret`/`redirect_uri` at the IdP. | Boot config. |
| A3 | HTTPS is available (proxy or daemon-side TLS) for production. | Cookie Secure flag. |
| A4 | DB is PostgreSQL (per blueprint). | Session store. |
| A5 | The 12 inventoried consumers are operator-controlled and can store a bearer token in env. | Machine lane. |
| A6 | The SPA will be updated to handle the cookie session (cookie automatically sent on same-origin GET/POST; the SPA needs no change for cookies; needs to handle the 302 redirect for first-page loads after session expiry). | Browser lane. |
| A7 | The IdP does not strip security-critical headers from the callback response (no-op for redirect-based flow; but documents the assumption that the IdP's GET /authorize preserves our query params). | Flow choice. |
| A8 | Operator will restart the daemon on config changes (existing pattern). | Boot validation. |
| A9 | The pyproject.toml `httpx` dependency is available; if not, OAuthClient is the reason it gets added (existing convention is to use httpx for LLM proxy calls). | OAuth client. |
| A10 | The `daemon/repositories/<domain>/` factory pattern is followed; a single SQLModel file per table; a single migration per table. | Schema. |

## References

- `daemon/api.py:2909-3319` — `create_app()` factory, 3 middlewares, health probes, SPA catch-all, MCP mounts.
- `daemon/middleware/host_capture.py:27-58` — pure-ASGI middleware pattern (thin pass-through, `scope["app"]` lookup).
- `daemon/services/live_views.py:524-703` — `BaseURLResolver` 4-tier chain.
- `daemon/services/live_views.py:1138-1178` — `LiveViewsService.build_url()` composition.
- `daemon/config.py:43-60` — env interpolation `${VAR:-default}`.
- `daemon/config.py:63-96` — `Annotated[list[str], NoDecode]` + `_parse_csv_or_json_list`.
- `daemon/config.py:2554-2628` — `LiveViewsConfig` template.
- `daemon/config.py:3360-3391` — root `Config` aggregation pattern.
- `daemon/__main__.py:332-352` — graceful task-drain SIGKILL at 70s.
- `launcher.sh:52` (repo ROOT — line-drift corrected v1.1) — SIGKILL semantics; `scripts/upgrade/lib.sh:70-79,2506-2541,4215-4256` — gate budgets and soak loop.
- `daemon/services/readiness.py:208-233` — composite refresh + O(1) read.
- `daemon/tools/upgrade_tools.py:864-886,1706,2127,1725,2130` — `_probe_self`.
- `daemon/migrations/versions/20261007_000001_create_drift_events.sql` — migration template.
- `daemon/routers/maintenance_origin_guard.py:24-43` — the only inbound gate today.
- `daemon/routers/webhooks.py:63-74` — per-source `X-Webhook-Secret`.
- `daemon/routers/messages.py:914`, `jobs_streaming.py:307`, `notifications.py:29`, `migration.py:223`, `workspace.py:165` — 5 SSE routes (EventSource constraint).
- `daemon/routers/live_views.py:274-391` — future per-view auth policy hook seam.
- `daemon/routers/jobs_crud.py:595`, `messages.py:690` — known forge-lane risk (loopback `source=api`; line numbers corrected v1.1). **R3 clarifier:** NOT closed by auth — follow-up ticket "R3: derive source from AuthContext" owns the durable closure.
- `docs/runbooks/live-views.md §2.4, §6` — `external_base_url` mandate; host-poisoning trust model.
- `tests/e2e/mock_source_server.py` — in-process ASGI sub-app pattern (AppRunner).
- `tests/test_health_probes.py:41-89`, `tests/test_api.py:196-227` — pytest in-process with `ASGITransport`.
- `frontend/scripts/boot-e2e-snapshots-daemon.sh:204-208`, `frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182` — e2e canaries.
- `daemon/constants.py:147` — `CHECKPOINT_TTL_HOURS` precedent.
- `daemon/llm_error_classifier.py:77` — precedent for HA failover reuse of an external-conn abstraction.
- `daemon/clients/plane_http_client.py`, `daemon/services/plane_sync_service.py` — precedent for "outside client wrapper + never-raises contract".

## Citations in the Project Critical Notes System (when this plan lands)

The following project CNs / KB entries should be updated when this
plan is finalized (the plan-creation worker owns the cross-link
in `decisions.md`):

- Add a `[decision] 🟡 AUTH DESIGN v1 — Authorization Code + PKCE + cookie + bearer lane; PG session store; default OFF` note.
- Cross-link the CORS hardening KNOWN RISK (`api.py:3015-3021` flagged
  2026-10-10) to the new `auth.allowed_origins` mitigation so the two
  tickets are linked.
- Add a `[risk]` note: static bearer tokens are operator-mint-only;
  no self-service token request endpoint in v1.
