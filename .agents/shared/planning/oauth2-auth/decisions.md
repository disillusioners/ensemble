# Decisions: OAuth2 Authentication Support (v1)

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes).** Adds
> **D19** (module auth lanes: webhooks + MCP-KB sit beyond/separate from
> OAuth2; OAuth2 governs only their token-management CRUD) and **D20**
> (lane isolation model: separate stores + token namespaces + middleware
> classification + negative test matrix — HARD requirement). **D4 gains a
> v1.4 note** (module-lane pass-through in BOTH auth states; supersedes
> the v1.1 `AUTH_DEFAULT_PUBLIC_PATHS` placement of `/api/mcp/kb*` with
> the semantically-distinct module-lane class). **S-20 semantics
> SUPERSEDED** (mounts are no longer "future OAuth2 protection" — the
> seam becomes "module-lane classification + own-key enforcement";
> [REQ] §10 row annotated; **NFR-11 seam census explicitly UNCHANGED at
> 20/20 seams / 21 dict entries** — semantics re-labeled, existence
> retained). Every v1.4 edit is greppably marked `AMEND v1.4 /
> module-lanes`. `architecture-recommendation.md` remains FROZEN;
> technical-analysis.md is not edited in this pass.

Decision register for the auth plan set. Status legend: **DECIDED** =
settled by the sibling artifacts (source cited; not re-litigated here);
**OPEN** = requires user/operator sign-off — each carries a recommended
default and cross-references the Open Decisions Ledger (OD-N) in
`plan-overview.md §7`. Sources: **[TA]** = `technical-analysis.md`,
**[REQ]** = `requirements.md` (§ + line where load-bearing).

> **Amendment pass v1.2, 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep, final pre-approver).** The reviewer verdict was APPROVED-WITH-NOTES (1 CRITICAL + 8 MAJOR + 12 MINOR). This brief is the sole v1.2 authority (no reviewer file on disk). **C1 (CRITICAL) — CSRF predicate rewrite** — see D8 below and `phase3-plan.md` T3.4: CSRF enforcement triggers ONLY when the request carries Origin or Referer headers (browser lane — presence-based branching, mirroring `daemon/routers/maintenance_origin_guard.py` rule-1); for such browser-lane state-changing requests, validate same-origin Origin/Referer (and token-echo when a CSRF header is present). Headerless machine/bearer requests are EXEMPT (no ambient cookie authority). NFR-18 (zero `frontend/src/**` changes) stays INTACT. SameSite=Lax + the Origin-guard predicate covers the residual cross-site risk. New **AC-39** asserts the SPA mutation from the unchanged SPA (browser sends Origin, no explicit CSRF header) succeeds — INV-3/SPA-lockout closed BY CONSTRUCTION. The previous v1.1 double-submit-only text in D8 is SUPERSEDED where it conflicts. Other amendments (A1–A8 + 12 MINORs) are stamped in their respective files; `architecture-recommendation.md` remains FROZEN.

> **Amendment pass v1.1, 2026-10-10 (architect CONDITIONAL PASS).** The
> 15 OD rulings from `architecture-recommendation.md §2` have been folded
> into DECIDED status (plain for OD-1/4/5/7/8/10/11/13/14/15; with
> folded conditions for OD-2/3/6/9/12 per the recommendation). The
> line-drift register (architecture-recommendation.md §0) was applied to
> all 15 OD-related citations and the D7/D10 line-drift anchors
> (`launcher.sh:52-54`→`launcher.sh:52` repo ROOT; `jobs_crud.py:275-278`→`:595`;
> `messages.py:391`→`:690`). New decisions D17 (SQLite inert tables) and
> D18 (route-metadata public-set derivation) added. The OPEN items table
> at the bottom of this file is retired — every entry is now DECIDED.
> `plan-overview.md §7` reflects the same closure.

---

## D1. Flow choice: Authorization Code + PKCE + server-side session cookie (browser lane) — DECIDED

**Decision:** OAuth2 Authorization Code grant with PKCE executed
server-side; the daemon holds the confidential `client_secret`; login
mints a PG-backed session returned to the browser as `ensemble_sid`.
**Rationale:** `EventSource` cannot send `Authorization` headers, so a
cookie session is the only browser-compatible scheme for REST + the 5 SSE
families; PKCE defends code interception; confidential-client keeps the
secret server-side. **Rejected:** Implicit (deprecated, OAuth 2.1),
Hybrid (deprecated), Device grant (wrong shape), mTLS-only (no browser
story), `X-API-Key`-only (SPA cannot set headers without CORS preflight).
**Status:** DECIDED. **Source:** [TA] §Trade-offs + §Recommendation
(:447-481); [REQ] §1.1 Lane A.

## D2. PKCE + state binding — DECIDED

**Decision:** state = 32-byte URL-safe base64, TTL 300 s, persisted in
`auth_states`; `code_verifier` 43-128 chars via `secrets.token_urlsafe(64)`
(never leaves the server); `code_challenge = S256(verifier)` (never
`plain`); callback consumption is a single-CAS `pending → consumed` —
the exactly-once arbitration point; CAS loser → `400 STATE_REPLAYED`;
expired → `400 STATE_EXPIRED`. **Rationale:** RFC 7636 conformance +
replay defeat at one atomic point. **Rejected:** client-side PKCE
(public-client shape); stateless state (no replay arbitration).
**Status:** DECIDED. **Source:** [TA] §Flow Choice Steps 1/3 (:488-508);
[REQ] FR-4/5/6/7, AC-3…AC-7.

## D3. OIDC discovery + manual-endpoint fallback — DECIDED

**Decision:** boot-time discovery fetch at
`{issuer}/.well-known/openid-configuration` (5 s timeout, backoff
1s→4s→16s, TTL cache ≤ 24 h / default 3600 s); operators may pin
`authorization_endpoint` / `token_endpoint` / `jwks_uri` /
`end_session_endpoint` overrides with discovery disabled; JWKS fetched
lazily at first callback, cached for process life, kid-miss → one forced
refresh then `401 JWKS_UNAVAILABLE`. **Rationale:** modern IdPs all
publish discovery; the fallback keeps air-gapped/self-hosted IdPs
workable. **Status:** DECIDED. **Source:** [TA] §Integration 3-5
(:351-359); [REQ] FR-29/30/31, §6.1 discovery/jwks rows.

## D4. Middleware placement: exact LIFO order — DECIDED

**Decision:** registration order in `create_app()` = CORSMiddleware →
SelectiveAccessLog → HostCapture → **AuthMiddleware (new, conditional)**;
execution (outermost-first) = Auth → HostCapture → AccessLog → CORS.
Binding rules: public fast-path is zero-I/O and first; HostCapture runs
before any redirect_uri construction (recorder is last-write-wins,
`api.py:1054-1055`); a non-preflighted cross-origin call must 401 at Auth
before CORS mutates headers; OPTIONS preflight short-circuits in
Starlette; WebSocket scopes pass through untouched. **AMENDED v1.1 (D1,
architect must-fix):** `/api/mcp/kb` and `/api/mcp/kb/sse` are
**INSIDE** the parent middleware stack on Starlette 0.52.1 / FastAPI
0.133.1 (`build_middleware_stack` wraps `self.router`,
`starlette/applications.py:75-93`; `app.mount()` registers Mounts inside
that router, `routing.py:373+`); the previous "Mounts run OUTSIDE
parent middleware ⇒ KB stays unauthenticated" reading was wrong.
Therefore both paths are in `AUTH_DEFAULT_PUBLIC_PATHS` v1 to preserve
today's loopback posture verbatim (the middleware never touches them);
the loopback trust rationale is documented in the runbook; a
KB-MCP-auth seam is RESERVED for a future commission. Plan pins
Starlette 0.52.1 / FastAPI 0.133.1; phase 3 carries a one-line test
asserting Mounts traverse the auth middleware (D1 regression guard).
**Rationale:** [TA] §Registered Middleware Order (binding rules 1-7).
**Rejected:** auth inside CORS; always-registered no-op (see D5).
**Status:** DECIDED. **Source:** [TA] :226-287, :652-740; [REQ]
FR-19/20/21, AC-19/20/21.

**AMEND v1.4 / module-lanes — module-lane pass-through (user ruling).**
The middleware now derives THREE path classes at construction
(FR-22a v1.4): public / **module-lane** / oauth2-protected. Module-lane
paths (`/api/webhooks/*` ingest, `/api/mcp/kb`, `/api/mcp/kb/sse`) pass
through the middleware UNTOUCHED — in BOTH `auth.enabled = true` and
`auth.enabled = false` states — to their own per-module auth (webhook:
`X-Webhook-Secret` at webhooks.py:63-76; MCP-KB: own-key mechanism,
confirm-at-implementation). This SUPERSEDES the v1.1
`AUTH_DEFAULT_PUBLIC_PATHS` placement of `/api/mcp/kb*` recorded above:
the v1.1 Mount-traversal FACT stands (Mounts DO traverse parent
middleware on Starlette 0.52.1 — which is exactly why the classifier must
explicitly TAG them module-lane rather than rely on placement), but the
class is no longer PUBLIC — it is the semantically-distinct MODULE-LANE
(beyond/separate from OAuth2 per the user ruling; D19). Behavioral
consequence: none for the request path (both classes pass through);
semantic consequence: module endpoints are excluded from the OAuth2
public/protected taxonomy and from its future evolution. The webhooks
ingest path, previously planned as "PROTECTED + X-Webhook-Secret"
(v1.3 §5.4), is likewise MODULE-LANE — the OAuth2 layer never gates it
and an OAuth2 credential is NOT accepted as a webhook credential.

## D5. Default-OFF mechanism: NOT registered vs passthrough — DECIDED (not-registered)

**Decision:** when `settings.auth.enabled is false`, the
`app.add_middleware(AuthMiddleware, …)` call is skipped entirely; no auth
service/sweeper/client objects are constructed. **Rationale:** zero
per-request branching and a byte-identical ASGI stack by construction;
"always registered + flag check" costs a branch on every request in the
default product state and mixes concerns. **Rejected:** pass-through
middleware consulting `scope["state"]["auth_disabled"]`.
**Status:** DECIDED. **Source:** [TA] §Middleware Placement rule 7
(:724-740); [REQ] FR-2, AC-2, NFR-1/2.

## D6. redirect_uri reuses BaseURLResolver; None ⇒ 503 LOGIN_NOT_CONFIGURED — DECIDED

**Decision:** `AuthService` resolves the callback base via the chain
`auth.external_redirect_base` (override) → live-view
`external_base_url` (reuse) → `BaseURLResolver.resolve()`
(Host-capture → bind) → **None ⇒ 503 LOGIN_NOT_CONFIGURED** — never a
path-relative mint. Inherits the documented host-poisoning trust model
(`docs/runbooks/live-views.md §2.4/§6`); the operator override is the
cure. **AMENDED v1.1 (D2, OD-6, architect must-fix):** `AuthConfig` carries
a one-shot lifespan copy of `config.live_views.external_base_url` (a plain
string) into the new `live_view_external_base_url_fallback: str | None`
field; `AuthService` passes that string as `BaseURLResolver`'s tier-1
argument. Same one-shot-copy pattern as `bind_host`/`bind_port` today.
DI-clean: `AuthService` never imports `live_views`. The host-poisoning
impact is **login-DoS, not session-fixation** — `code_verifier` never
leaves the server; the `state` CAS rejects replays; conformant IdPs
reject unregistered `redirect_uri` values. **Rationale:** RFC 6749
§3.1.2 requires absolute redirect URIs; a relative mint either bounces
badly at the IdP or risks stale-registration acceptance.
**Rejected:** auth-specific resolver re-implementation; auto-minting
relative URLs. **Status:** DECIDED (chain shape; the live-view
tier-1 wiring is D2; field name `live_view_external_base_url_fallback`
ratified). **Source:** [TA] §Redirect URI Resolution (:742-806);
[REQ] FR-28, AC-28; [TA] Debt #3 (DI seam — no liveviews import).

## D7. PG-backed sessions + restart survival — DECIDED

**Decision:** sessions/states/tokens live in PostgreSQL
(`auth_sessions`, `auth_states`, `auth_bearer_tokens`); in-memory stores
are categorically wrong because promote restarts are routine full process
death (SIGKILL at 70 s, `daemon/__main__.py:332-352`,
`launcher.sh:52` at repo ROOT — line-drift corrected v1.1). Sweeper: 5 min loop, batches of 1000,
`FOR UPDATE SKIP LOCKED`; 60 s state loop. **SQLite posture (AMENDED v1.1 / D17):** auth tables are
**inert on SQLite** — the migration runner (`daemon/migrations/runner.py:107-113`) auto-applies
every non-`MANUAL: TRUE` `.sql` at boot, so tables exist on BOTH backends; **no MANUAL marker,
single code path, no SQLite boot-refusal, no `require_sqlite_rejection` env var**. The
NFR-8 wording "PG-only / auth-ON refuses boot on SQLite" is the FEATURE GATE, not the SCHEMA
fact. **Rationale:** NFR-4 (≥99.9 % survival) + blueprint PG-primary posture. **Rejected:**
in-memory session map; SQLite auth tables with `MANUAL: TRUE` markers. **Status:** DECIDED.
**Source:** [TA] §Session Model (:881-898) + R4; [REQ] NFR-4/8; AC-S4.

## D8. Cookie flags + CSRF strategy — DECIDED (AMEND v1.2 — C1 CRITICAL)

**Decision (v1.2):** `ensemble_sid` = `HttpOnly` always; `Secure` default ON
(`allow_insecure_cookie=true` is the explicit LAN opt-out);
`SameSite=Lax` default (Strict allowed + boot warning — breaks the
top-level callback); `Path=/`; `Max-Age=ttl` (8 h default, sliding
renewal OFF). **CSRF (v1.2 — Origin-guard predicate, presence-based —
supersedes v1.1 double-submit-only text where it conflicts):**
- **Predicate.** CSRF enforcement triggers ONLY when the request carries
  an `Origin` or `Referer` header (browser lane — presence-based
  branching, mirroring the rule-1 precedent at
  `daemon/routers/maintenance_origin_guard.py:24-43`: "No Origin header
  → allow"). For such browser-lane state-changing requests, validate
  same-origin Origin/Referer (and token-echo when a CSRF header is
  present).
- **Exemption.** Headerless machine/bearer requests are EXEMPT — they
  have no ambient cookie authority, so the CSRF surface does not exist.
  This matches the reality that the dispatch's mandated NFR-18 (zero
  `frontend/src/**` changes) cannot be reconciled with mandatory
  `X-CSRF-Token` on every state-changing route (the unchanged SPA has no
  CSRF interceptor; mandatory `X-CSRF-Token` would 403 every SPA
  mutation day 1).
- **Token.** When a CSRF header IS present (defense-in-depth for the
  SPA-with-explicit-token future), token-echo validates
  `X-CSRF-Token` against the readable `ensemble_csrf` cookie
  (~10 LoC, OD-15 timing preserved).
- **Residual coverage.** SameSite=Lax blocks most cross-site POSTs;
  the Origin-guard predicate covers the rest. NFR-18 (zero
  `frontend/src/**` source changes) is INTACT.

**Rationale:** [TA] §Session Model; NFR-6; NFR-18; the
SPA-unchanged constraint forces the presence-based predicate (a blanket
mandate cannot coexist with zero SPA changes). **Rejected:** SameSite=None;
mandatory `X-CSRF-Token` on all state-changing routes (would 403 every
SPA mutation — INV-3/SPA-lockout). **Status:** DECIDED (v1.2 — supersedes
v1.1 double-submit-only text where it conflicts). **Source:** [TA]
:810-837, OQ-7; [REQ] FR-11/14, §6.1 cookie rows, AC-11, **AC-39 (NEW
v1.2)**; rule-1 precedent `daemon/routers/maintenance_origin_guard.py:24-43`.

## D9. Static-bearer machine lane + scopes — DECIDED

**Decision:** Lane B = operator-minted static bearer tokens (CLI v1,
`sha256(token)`-only storage, plaintext printed once), presented as
`Authorization: Bearer`; middleware verifies hash, attaches
`AuthContext(auth_method="bearer")`; insufficient scope →
`403 SCOPE_INSUFFICIENT`; per-route policy via `@require_scope(...)`
decorator (default `*` for undecorated routes); fine-grained taxonomy
(7+ scopes) shipped day 1 with conservative mint defaults.
**AMENDED v1.1 (OD-3 condition, C1 — router-mount scope-declaration
cage):** because undecorated routes default to `*`
(allow-any-authenticated), every future router mounted without an
explicit scope declaration would silently ship weakest policy. v1
ships a **regression-cage test that fails when `include_router()` mounts
a router without an explicit scope declaration** (per-family manifest
where each router family is either decorated with `@require_scope(...)`
or explicitly wildcarded in the manifest). The default-`*` posture
itself stays for v1 — default-deny would force decorating all 28
router families in the first commission, which is the wrong blast
radius per architect C1. **Rationale:** 12 loopback consumers are
operator-trusted; bearer keeps them at zero code change; least-privilege
without OAuth-client complexity for cron-loop callers. **Rejected:**
OAuth client_credentials grant for machines in v1 (S-11 seam reserved);
blanket loopback trust (D10). **Status:** DECIDED (C1 cage adopted;
OD-3 default (c) hybrid endorsed). **Source:** [TA] §Machine-Client
Lane (:977-1056); [REQ] FR-16/17/18, §7.3, OQ-REQ-7/8; architect
§1.3 C1.

## D10. Loopback blanket exemption REJECTED — DECIDED

**Decision:** NO loopback/127.0.0.1/::1 auth exemption. All loopback
callers ride Lane B (bearer) or touch only PUBLIC probes.
**Rationale:** unauthenticated loopback already forges `source=api` on
POST /jobs, /messages (`daemon/routers/jobs_crud.py:595`,
`messages.py:690` — KB-flagged; line-drift corrected v1.1), a blanket exemption would preserve that
known risk AND grant full create access to any process that can bind
127.0.0.1. **R3 clarifier (AMENDED v1.1):** auth does NOT close the
forge-source lane — a stolen `jobs:write` bearer still forges
`source="api"` and `messages.py:690` still hard-codes `source="api"`. The
durable closure is a SEPARATE follow-up ticket ("R3: derive source from
`AuthContext.subject` when auth is ON — ignore/merge `body.source`"),
cross-linked from the project critical note "OAuth2 planning follow-ups
(R3): source-forgery lanes NOT closed by auth plan". **Rejected:**
loopback exemption in any form. **Status:** DECIDED. **Source:** [TA]
§Machine-Client Lane "Loopback exemption debate" (:1020-1056); [REQ] §7
(all consumers pass without it); `daemon/routers/jobs_crud.py:595`,
`daemon/routers/messages.py:690`.

## D11. Health-probe public invariant: structural zero-I/O fast path — DECIDED

**Decision:** `public_path_set` (AMENDED v1.1 / D18: derived from route
metadata at construction — routes carry a `public-probe` tag/attribute;
construction walks `app.routes`, builds the O(1) frozenset, and LOGS the
derived set at startup; prefix rules for `/api/auth/*` + the SPA
PUBLIC-ASSETS family). Contents per [REQ] FR-22 incl. the maintenance
canary, `/views/livez`, and (AMENDED v1.1 / D1) `/api/mcp/kb` +
`/api/mcp/kb/sse`. The set is evaluated BEFORE any other logic in the
middleware — no DB, no IdP, no cookie parse. Publicness in every
scenario (OFF/ON/IdP-down/store-down/restart/replay) holds by
construction, verified by AC-22/AC-S3 and the canary scripts. **`/readyz`
degradation surfaces as an `auth_dependency_status` body sub-field
(AMENDED v1.1 / OD-9); verdict NEVER flipped.** **AMENDED v1.1 / SC-4
wording precision:** the invariant is **PUBLICNESS** (never 401, no
auth dependency) NOT "200 in every scenario." During a PG outage
`/readyz` legitimately 503s via the pre-existing `database` component;
that is the readiness contract working, not an auth regression. ACs
assert never-401 + reachability, not 200. **Rationale:**
restart/upgrade machinery (promote/rollback/restart/deploy/watchdog/
`_probe_self`) depends on unauthenticated probes. **Rejected:**
behavioral exemption lists; readyz verdict flip. **Status:** DECIDED
(D18 route-metadata derivation adopted; OD-9 body sub-field default (b)
endorsed with the `draining` field consistent body shape; AC-S3 wording
carried in §9 of `plan-overview.md`). **Source:** [TA] §Failure Modes
structural guarantee (:939-949); [REQ] FR-22/22a/23, NFR-3/15, AC-22/31,
AC-S3; architect §1.4 C3.

## D12. 401-vs-302 discrimination — DECIDED

**Decision:** Accept-header parse first (`text/html` ⇒ 302
`/api/auth/login?return_to=<sanitized>`; explicit `application/json` ⇒
401 + `WWW-Authenticate: Bearer realm="ensemble"` + JSON
`{"error":"AUTH_REQUIRED",…}`); path-family heuristic as the `*/*`
fallback. **AMENDED v1.1 (OD-12 condition):** `return_to` sanitization
MUST normalize `\` → `/` BEFORE the same-origin path-prefix check
(legacy browsers treat `\\`/`//` interchangeably in `Location:`, so
`return_to=\\evil.com` and `return_to=//evil.com` are equivalent
open-redirect vectors if normalization is skipped). The full sanitization
contract: ≤1024 chars, normalizes `\` → `/` first, then same-origin
path-prefixed only (must start `/`, must not start `//`, `/\`, no scheme),
invalid → `/`. **AC-25 keeps the backslash cases** as table-driven
inputs. **Rationale:** browsers follow 302s; curl reads 401s; no client
config needed. **Rejected:** always-401 (breaks SPA UX); always-302
(breaks machine callers). **Status:** DECIDED. **Source:** [TA] §401
Contract (:1058-1094); [REQ] FR-24/25, AC-24/25.

## D13. Fake OIDC in-process testing — DECIDED

**Decision:** `tests/fakes/fake_oidc_provider.py` — pure ASGI sub-app,
5 routes, test RSA keypair, mounted `/_test_oidc/`, exercised through
`httpx.AsyncClient + ASGITransport`; no Docker; two-fixture estate
(`auth_disabled_app` / `auth_enabled_app` + `mint_test_token`).
**Rationale:** determinism + speed + no sandbox boundary; mirrors
`tests/e2e/mock_source_server.py`; NFR-10 runnability. **Rejected:**
Dockerized Keycloak; HTTP-layer mocks only (don't exercise redirects).
**Status:** DECIDED. **Source:** [TA] §Testability (:1112-1161); [REQ]
FR-33/34, §7.5, AC-34.

## D14. Migration convention: own `.sql` per table (template
`20261007_000001_create_drift_events.sql`) — DECIDED

**Decision:** three files — `<ts>_create_auth_sessions.sql`,
`<ts>_create_auth_states.sql`, `<ts>_create_auth_bearer_tokens.sql` —
each `-- UP`/`-- DOWN`, SHA-256-checksummed, following
`daemon/migrations/versions/20261007_000001_create_drift_events.sql`.
**Rationale:** the blueprint rule ("every new SQLModel table requires its
own `*.sql`"; 79-file history, zero exceptions) and [REQ] A10 both say
per-table; [TA]'s single `create_auth_tables.sql` mention (:883-885) is a
naming slip — blueprint wins. Reserved-seam `.sql` stubs
(grants/subject_alias/view_shares) are EMPTY placeholders labeled
"reserved — no schema" so the cage test has honest artifacts.
**Status:** DECIDED (conflict resolution recorded here). **Source:**
Project blueprint §Core Architecture (migrations); [REQ] A10; [TA]
§Session Model :881-885 (superseded on this naming point).

## D15. CORS tightening when auth ON — DECIDED (guard in scope; hardening OOS)

**Decision:** when `auth.enabled=true` and CORS is wildcard +
`allow_credentials=true` with empty `auth.allowed_origins`, the daemon
refuses to boot (exit 78). When `auth.allowed_origins` is non-empty it
replaces the wildcard for that deployment. Off-state CORS posture is
preserved verbatim. Broad CORS hardening stays OUT (OOS-5, separate
ticket; the 2026-10-10 KNOWN RISK). **Rationale:** wildcard+credentials+cookies
is the canonical XSRF recipe; guard is cheap; hardening is a separate
blast radius. **Status:** DECIDED (ledger OD-4). **Source:** [TA] R1 +
OQ-10 + Debt #1; [REQ] FR-27, OOS-5, AC-27.

## D16. Runbook v-pattern — DECIDED

**Decision:** `docs/runbooks/oauth2-auth.md` follows the live-views
v2.0 pattern (single-edge-rule → scope → operator notes →
troubleshooting → security notes → cross-references → annexes) with the
7 FR-35 sections mapped onto it; merge lands auth-OFF; enabling is a
post-merge operator act (config + restart + first-token mint).
**Rationale:** in-repo precedent for "non-trivial feature ships with
runbook"; operator muscle memory. **Status:** DECIDED. **Source:** [REQ]
FR-35, AC-35; `docs/runbooks/live-views.md`; [TA] Debt paydown #1-2.

## D17. SQLite posture: INERT AUTH TABLES (leader ruling) — DECIDED

**Decision:** the v1 build creates the auth tables on BOTH backends
(PostgreSQL + SQLite) via the standard migration runner. There is NO
`MANUAL: TRUE` marker, NO `require_sqlite_rejection` env var, and NO
SQLite boot-refusal path. The migration runner
(`daemon/migrations/runner.py:107-113`) auto-applies every non-`MANUAL:
TRUE` `.sql` at boot, so tables exist on both backends by construction.
**A single code path serves both backends.** The SQLite-auth fixtures
keep working. **NFR-8's wording becomes "boot refuses (`enabled=true` ∧
SQLite backend)" → "tables exist on both backends, inert when not in
use; the feature-gate is the absence of `auth.enabled`"** — i.e. the
SQLite compat is preserved at the schema layer; the feature gate is
operator config, not a runtime refusal. **Rationale:** the literal
reading of "auth tables rejected" fails its own acceptance (the runner
auto-applies them); the inert-tables posture matches the migration
runner invariant, keeps the SQLite test fixtures (auth_enabled_app etc.)
working, and avoids a `MANUAL: TRUE` marker that would re-introduce a
hidden second code path. **Rejected:** `require_sqlite_rejection=true`
default → boot-refusal (contradicted the runner, broke fixtures);
`MANUAL: TRUE` markers on auth tables (hidden second code path).
**Status:** DECIDED (leader ruling; `require_sqlite_rejection` env var
REMOVED from [REQ] §6.1; NFR-8 row reworded; the §6.3 row about
SQLite+auth refusal is retracted). **Source:** [REQ] NFR-8 (reworded),
§6.1 (env-var row removed); architect §1.2 NFR-8 wording condition +
§1.5 SQLite compat note; `daemon/migrations/runner.py:107-113`.

## D18. Public-path set derived from route metadata (C3) — DECIDED

**Decision:** the `public_path_set` consumed by `AuthMiddleware` is
**derived from route metadata at middleware construction**, not
maintained as a hand-listed frozenset. Routes carry a `public-probe`
tag/attribute (prefix rules allowed for `/api/auth/*` and the SPA
PUBLIC-ASSETS family); the construction walks `app.routes`, builds the
same O(1) frozenset, and **logs the derived set at startup**
(reviewable in promote transcripts). Request-time behavior is
byte-identical to the hand-listed design. **Rationale:** the drift
hazard is already scheduled — the reserved `draining` field at
`readiness.py:238-239` + `api.py:3195-3197` means a future
`/drainz`-style probe is planned; a hand-listed frozenset that forgot
to join would 401 at the promote gate (a resilience regression, not a
security hole, but a burned promote ceremony). The route-metadata
derivation gives a single source of truth + zero-I/O + log-visibility.
**AC-22's curl canary stays as the runtime backstop** and asserts the
FULL known probe inventory (defense in depth). **Rejected:** hand-
listed frozenset (drift hazard); per-request router introspection (cost
on the hot path); explicit-no-allow (default-deny would force decorating
all 28 router families in the first commission). **Status:** DECIDED
(leader ruling; replaces the hand-listed frozenset with route-metadata
derivation; FR-22a added to [REQ] §3.22; phase 3 amended; the
`AUTH_DEFAULT_PUBLIC_PATHS` constant still exists as the EXPLICIT
list-of-prefixes fallback for routes that cannot be tagged — `/api/auth/*`
prefix and the SPA PUBLIC-ASSETS family). **Source:** [REQ] FR-22a
(new in v1.1), NFR-3; architect §1.4 C3; [TA] §Structural guarantee.

## D19. Module auth lanes: webhooks + MCP-KB beyond OAuth2; OAuth2 governs only their token-management CRUD — DECIDED (AMEND v1.4 / module-lanes — user ruling)

**Decision:** the webhook module and the MCP-KB module are SEPARATE auth
lanes, OUTSIDE the OAuth2 surface. (1) They are NOT designed for
human-user access via OAuth2 and have their OWN UI/API-key management;
they sit beyond/separate from OAuth2. (2) Their endpoints —
`POST /api/webhooks/{source_id}` (webhooks.py:15, :24-25, mounted
api.py:3103; sole auth = optional `X-Webhook-Secret` vs plaintext
`source_configs.config["webhook_secret"]`, webhooks.py:63-76:
`compare_digest` :69, OPEN ingest when unset :67, 401 :70-76) and
`/api/mcp/kb` + `/api/mcp/kb/sse` (api.py:3150-3151, FastMCP
kb_server.py:214-223 — ZERO auth today, fully open) — are classified
**MODULE-LANE** in the protection matrix ([REQ] §5.0/§5.6) and pass the
OAuth2 middleware untouched in BOTH auth states. (3) OAuth2's role is
exactly: a logged-in human can LIST/READ/UPDATE/DELETE module tokens —
webhook-source tokens via the EXISTING generic `/api/sources*` CRUD
(stays PROTECTED; annotated as THE webhook token-management surface —
there is NO dedicated webhook-secret endpoint, and no SPA surface: grep
`api/sources` in frontend/src = zero hits); MCP-KB keys via a NEW SMALL
`/api/mcp/kb/keys` CRUD family under the OAuth2 human lane (routes +
authz one-liner; confirm-at-implementation; no UX invented at plan
level). The MCP-KB mount key-check mechanism itself is ALSO new —
module-lane mechanism, confirm-at-implementation; the current
fully-open KB state makes it a security improvement independent of
OAuth2. When auth is OFF everything is unchanged (as today).
**Supersessions recorded:** (a) the v1.3 §5.4 webhooks verdict
"PROTECTED + X-Webhook-Secret (both must succeed)" — SUPERSEDED: the
OAuth2 layer never gates webhook ingest and an OAuth2 credential is not
a webhook credential; (b) the v1.1/D1 PUBLIC placement of
`/api/mcp/kb*` — SUPERSEDED by the module-lane class (behavioral
pass-through identical; semantic class distinct — see D4 v1.4 note);
(c) the **S-20 KB-MCP-auth seam semantics** — SUPERSEDED: the seam is no
longer "future OAuth2 protection of the mounts" but "module-lane
classification + own-key enforcement" ([REQ] §10 row annotated; **NFR-11
seam census explicitly UNCHANGED: 20/20 seams, 21 §10.1 dict entries —
semantics re-labeled, existence and grep assertions retained**);
(d) the v1.2/A6 §7 row 17 "YES-WITH-CONFIG / set `webhook_secret` BEFORE
enabling auth" framing — SUPERSEDED: webhook senders are auth-INDEPENDENT
(verdict YES UNCHANGED — module-lane); `webhook_secret` remains existing
best practice for the pre-existing open-ingest posture, not auth
enablement config.
**Rationale:** the user's 3-point ruling (2026-10-10) is the sole
authority; fresh code grounding in [REQ] §5.0 incl. the two NOT-FOUND
facts (no MCP-KB key surface: no env/config/router/repository — grep
KB_MCP/MCP_KB/kb_mcp_key/KB_API_KEY = wiring only; KB absent from
`/api/mcp-servers` rows, which manage agent-side OUTBOUND client
connections, mcp_servers.py:46-47; no dedicated webhook-secret endpoint).
**Rejected:** keeping webhook ingest dual-gated (auth + secret) —
re-locks module senders behind OAuth2 against the ruling; keeping the KB
mounts PUBLIC — conflates "no auth anywhere" with "module owns its auth",
forecloses the own-key mechanism's classification, and misstates the
surface to operators; folding module keys into `auth_bearer_tokens` —
violates lane isolation (D20).
**Status:** DECIDED (user ruling). **Source:** [REQ] §5.0, §5.4-5.6,
§6.6, §7.1 rows 17-18, §7.7, FR-39/41/42; phase3 T3.10-T3.12; phase6
T6.1 module-lane runbook sections.

## D20. Lane isolation model: separate stores + token namespaces + middleware classification + negative test matrix — DECIDED (AMEND v1.4 / module-lanes — HARD requirement)

**Decision:** a token minted/owned by one lane NEVER authenticates
another lane. Four enforcement layers, all REQUIRED: (a) **separate
stores** — webhook secrets in `source_configs.config["webhook_secret"]`
(repositories/source/models.py:62-65); MCP-KB keys in their OWN
module-owned store (own table/storage, confirm-at-implementation);
OAuth2 sessions/bearers in `auth_sessions` / `auth_states` /
`auth_bearer_tokens`; no cross-store lookups anywhere in the auth code
paths; (b) **token namespace/format separation** — webhook: per-source
shared secret in `X-Webhook-Secret`; MCP-KB: module key; OAuth2:
`ensemble_sid` cookie / `Authorization: Bearer` — formats are distinct
and non-interchangeable by construction; (c) **middleware
classification** — the three-class route-metadata derivation (FR-22a
v1.4; D18 extended) routes every path to exactly one lane's checker;
module-lane membership is static across auth states (FR-42); (d)
**rejection semantics** — webhook token → MCP-KB mount or general API ⇒
401/403; MCP-KB key → webhook ingest or general API ⇒ 401/403; OAuth2
session/bearer → webhook ingest ⇒ NOT a webhook credential (module guard
decides; 401 as today); module tokens never valid outside their lane.
The negative cross-lane matrix is a REQUIRED test artifact (AC-40; phase5
T5.11): each token type × each wrong lane, plus module-lane ON/OFF
equivalence (AC-41).
**Rationale:** the user's ruling makes lane isolation a HARD requirement
("a webhook-source token must NOT work for MCP-KB, and neither works for
anything else"); store separation is already true for webhook-vs-OAuth2
and is the cheapest isolation layer; the negative matrix converts the
requirement into a provable property instead of an intention.
**Rejected:** shared token store with lane-tags (one leaked row
cross-authenticates); prefix-based soft separation without store
separation (grep-level hygiene, not enforcement); documenting isolation
without a negative test matrix (unprovable).
**Status:** DECIDED (user ruling). **Source:** [REQ] FR-40/FR-42, §5.0,
§7.7; AC-40/AC-41; phase5 T5.11.

---

## OD Rulings — FOLDED to DECIDED (architect §2)

All 15 Open Decisions from the original ledger have been folded into
DECIDED status in the relevant D-N above. Cross-reference for
traceability:

| OD | Topic | Status | Folded into | Condition / amendment |
|----|-------|--------|-------------|------------------------|
| OD-1 | Docs trio stance (`/docs`, `/redoc`, `/openapi.json`) | **DECIDED (ENDORSE)** | D15 cross-ref | public + `ENSEMBLE_AUTH_DOCS_ENABLED` kill switch (default true) |
| OD-2 | Bootstrap-token mint surface | **DECIDED (ENDORSE-WITH-CONDITION)** | D8, D9 | folded C2 conditions: `ens_env_read` deny-list `ENSEMBLE_AUTH_*` + one-shot consumption + runbook removal step |
| OD-3 | Scope granularity | **DECIDED (ENDORSE-WITH-CONDITION)** | D9 | folded C1: router-mount scope-declaration cage test |
| OD-4 | CORS tightening policy | **DECIDED (ENDORSE)** | D15 | boot-refusal guard (FR-27); hardening OOS-5 |
| OD-5 | Cookie `Secure` under plain-HTTP LAN | **DECIDED (ENDORSE)** | D8 | default ON; `allow_insecure_cookie` explicit opt-in |
| OD-6 | Auth base-URL chain | **DECIDED (ENDORSE-WITH-AMENDMENT)** | D6 | folded D2: `live_view_external_base_url_fallback` lifespan copy; D6 field name ratified |
| OD-7 | `/vscode` WS stance | **DECIDED (ENDORSE)** | D4 rule 4 | loopback posture; seam pre-wired |
| OD-8 | Multi-IdP | **DECIDED (ENDORSE)** | D14 + seam S-9 | single issuer v1; `idp_alias` seam |
| OD-9 | `/readyz` degraded signal | **DECIDED (ENDORSE)** | D11 | verdict-freeze test + auth sub-field added to /readyz no-composite fail-closed branch |
| OD-10 | Logout endpoint shape | **DECIDED (ENDORSE)** | D8 + phase 3 T3.6 | POST + DELETE alias, same soft-delete |
| OD-11 | Cookie name | **DECIDED (ENDORSE)** | D8 | `ensemble_sid` |
| OD-12 | SPA first-load UX | **DECIDED (ENDORSE-WITH-CONDITION)** | D12 | `return_to` normalizes `\` → `/` BEFORE same-origin prefix check; AC-25 keeps backslash cases |
| OD-13 | SPA asset publicness | **DECIDED (ENDORSE)** | D4, D11 | implicit PUBLIC-ASSETS rule mirroring SPA carve-outs |
| OD-14 | First-admin-token default scopes | **DECIDED (ENDORSE)** | D9, D4 (CLI) | `auth:admin` only; CLI `--scopes` per mint |
| OD-15 | CSRF double-submit timing | **DECIDED (ENDORSE)** | D8 | implement now (~10 LoC) |

**No OD items remain OPEN.** The user may override any of these in the
implementation commission, but no decision blocks the plan from being
handed to implementation.

---

**Settled without user input** ([REQ] §9.11): default scope set for
minted tokens, `Authorization: Bearer` header, cookie `Path=/`, 8 h
session TTL, 1 h discovery/JWKS cache TTLs, `secrets`-module-only crypto.
