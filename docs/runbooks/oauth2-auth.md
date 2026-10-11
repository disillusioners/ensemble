# OAuth2 Authentication — Operator Runbook

> **Status:** full runbook (Phase 6, T6.1). Supersedes the Phase 4
> DRAFT (`§Token mint` only, T4.6) — the mint procedure below carries
> that draft's content forward verbatim where it was already correct.
> Pattern: live-views v2.0 (D16). Sections (a)–(g) are the seven
> FR-35-mandated sections; §7 Troubleshooting is additive.

OAuth2/OIDC authentication for the daemon: browser sessions via an
external IdP (Auth Code + PKCE), static bearer tokens for machine
clients, hash-only storage, and a default-OFF contract — the feature
is INERT unless an operator explicitly sets `ENSEMBLE_AUTH_ENABLED=true`.

## 0. The one gating rule

Exactly one gate exists: `AuthMiddleware`, registered **only** when
`auth.enabled=true` (conditional registration — not-registered is not
registered-no-op). It sorts every request into one of three path
classes, derived from route metadata at construction and logged at
startup (`[Auth] route classes tagged ...`):

1. **public** — `/livez`, `/readyz`, `/api/health`, `/api/info`, the
   SPA assets, `/api/maintenance/checkpoint-cleanup/availability`
   (the boot-e2e canary route), `/api/auth/*`, `/docs` trio unless
   `ENSEMBLE_AUTH_DOCS_ENABLED=false`.
2. **module-lane** — `/api/webhooks/*` ingest and the MCP-KB mounts
   (`/api/mcp/kb`, `/api/mcp/kb/sse`). These pass the middleware
   **UNTOUCHED in BOTH auth states** (FR-39/FR-42): they authenticate
   with their own per-module credentials (webhook secrets, MCP-KB
   keys), never with OAuth2. See Annex B.
3. **oauth2-protected** — everything else: a valid cookie session or
   `Authorization: Bearer` (with sufficient scope) is required.

With auth OFF, requests never reach the middleware: behavior is
byte-identical to the pre-auth build (§2).

## 1. Enable procedure

### 1a. Register an OAuth2/OIDC client at your IdP

Create a **confidential** client with Authorization-Code + PKCE. The
callback (redirect) URI is `https://<daemon-host>/api/auth/callback`
(derive the exact URI per §1c first, then register THAT value — the
IdP and the daemon must agree byte-for-byte). Issue a client secret.

### 1b. Set the daemon environment

Required (auth-ON refuses to boot without them — exit 78, see §7):

```bash
ENSEMBLE_AUTH_ENABLED=true
ENSEMBLE_AUTH_CLIENT_ID=<from IdP>
ENSEMBLE_AUTH_CLIENT_SECRET=<from IdP>          # SecretStr hygiene; never logged
ENSEMBLE_AUTH_DISCOVERY_URL=https://<idp>/.well-known/openid-configuration
ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://daemon.example.com   # REQUIRED
```

`ENSEMBLE_AUTH_ALLOWED_ORIGINS` is **not optional**: the daemon's CORS
posture is `allow_origins=['*'] + allow_credentials=True` by
construction, and cookie sessions under wildcard CORS is the canonical
XSRF recipe — an empty list with auth ON refuses to boot (exit 78,
`auth.allowed_origins must replace wildcard + credentials ...`). Name
every origin that serves the SPA or makes cross-origin calls.

Common optional knobs (all default sensibly; the full 23-var table is
[REQ] §6.1 / Annex C):

| Var | Default | Why touch it |
|---|---|---|
| `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` | derived | Tier-1 redirect-base override for proxy-fronted deployments (scheme+host[:port] only). Wins over the live-views var. |
| `ENSEMBLE_AUTH_LIVE_VIEW_EXTERNAL_BASE_URL` fallback | — | If you already set `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` for live views, auth inherits it automatically (tier-2). |
| `ENSEMBLE_AUTH_REDIRECT_URI` | derived | **Exact-URI** override only (reverse-proxy path rewrites). Mutually exclusive with `EXTERNAL_REDIRECT_BASE`. |
| `ENSEMBLE_AUTH_COOKIE_SECURE` / `ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE` | `true` / `false` | Plain-HTTP LAN deployments must explicitly set `ALLOW_INSECURE_COOKIE=true` (drops the `Secure` flag — know the trade). |
| `ENSEMBLE_AUTH_COOKIE_SAMESITE` | `Lax` | `Strict` breaks the top-level OAuth redirect-back (boot-warned). Keep `Lax`. |
| `ENSEMBLE_AUTH_SESSION_TTL_SECONDS` | `28800` (8h) | Server-side session lifetime; sliding renewal OFF by design. |
| `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` | unset | One-shot first-admin mint lane (§3). Feed via systemd `EnvironmentFile`, not shell history. |

Webhook sources need **no auth prep**: webhook ingest is module-lane
(auth-INDEPENDENT, Annex B). Setting a per-source `webhook_secret`
remains good security practice for the pre-existing open-ingest
posture, but it is NOT an auth-enablement step and is not required
before enabling auth.

### 1c. Redirect-URI resolution & the pre-warm note

The daemon derives the redirect URI at request time:
`ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` > inherited live-views base >
Host-capture > bind. A **freshly booted daemon with a cold
Host-recorder** cannot derive a base — the first login attempt answers
`503 LOGIN_NOT_CONFIGURED` rather than minting a relative URL. In
practice the SPA's first hit on `/` pre-warms the recorder; for
headless/proxied deployments set `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`
explicitly so login works from the very first attempt.

### 1d. First admin token + restart + verify

Mint the first admin bearer via the one-shot bootstrap lane (§3),
then restart and verify:

```bash
systemctl --user restart ensemble.service          # or your unit's equivalent
tests/e2e/auth_health_probes.sh http://127.0.0.1:9797   # 5/5 probes NOT-401
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Accept: application/json' \
    http://127.0.0.1:9797/api/mcp-servers          # 401 AUTH_REQUIRED = gate live
```

Open `https://<daemon-host>/` in a browser, click through the IdP
login, and confirm the SPA loads normally (cookie `ensemble_sid` set,
`SameSite=Lax`, `Secure`).

## 2. Default-OFF verification

The build's core guarantee: with `ENSEMBLE_AUTH_ENABLED` unset/false,
nothing changes. Two complementary proofs:

### 2a. curl walkthrough (AC-S1, auth OFF)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9797/livez            # 200
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9797/api/mcp-servers  # 200 — no 401/302
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9797/api/auth/login   # 404 — auth routes not even registered
```

A 404 (not 401) on `/api/auth/*` is the load-bearing check: the auth
router is conditionally registered, so OFF-state has no auth surface
at all.

### 2b. In-worktree shape verification (G6.1, this phase)

Verified against the P5 in-process ASGI fixtures
(`tests/conftest_auth.py` — the two-fixture estate, no daemon boot):

```
OFF /livez -> 200                     OFF /api/auth/login -> 404 (not registered)
ON  /api/mcp-servers (Accept: application/json, no token) -> 401 AUTH_REQUIRED + WWW-Authenticate: Bearer
ON  /api/mcp-servers (no Accept)      -> 302 /api/auth/login?return_to=/api/mcp-servers
ON  /api/auth/me (no token)           -> 401 (machine-contract path family)
ON  /api/mcp/kb/keys (bearer mcpkb:admin) -> 200
ON  /livez /readyz /api/health /api/info (no token) -> NOT-401 (/readyz may be 503-degraded = readiness contract)
ON  /api/mcp/kb, POST /api/webhooks/* (no token) -> passes through (404/405 from routers, never 401/302)
```

### 2c. Suite evidence (P5 estate)

`tests/e2e/test_auth_lifecycle.py` 12/12 (the 4-scenario AC-S1…S4
walkthrough), P5 estate 110 passed + 1 skip, merge-gate riders 44+1 —
see `rollout-verification.md` in the planning set for the merge-PR
checklist.

## 3. Token mint

Every machine consumer gets a **static bearer token** minted by the
operator via the CLI, delivered to the consumer as an **environment
variable it already controls** (zero consumer code change — D4.5).
Tokens are stored hash-only (`sha256(token)`); the plaintext is
printed **exactly once** at mint time and never persisted server-side.

### Mint a token

```bash
python -m daemon.auth.cli issue-token \
    --label mcpservers-test \
    --scopes "system:read,mcp:read"
# → prints the token ONCE on stdout; status line on stderr.
```

`--ttl` is an **integer number of SECONDS** (default:
`ENSEMBLE_AUTH_BEARER_TOKEN_TTL_SECONDS`, 30d). Accepted range is
**60..31536000** seconds (60s floor, 1y ceiling); values ≤0 or
>31536000 are REFUSED with a clear error and non-zero exit (CLI
bounds agree with the `auth.bearer_token_ttl_seconds` §6.1 config
ceiling — negative TTLs previously minted an already-expired token
with exit 0, closed by the 2026-10-11 review riders). Scope
names are the closed v1 taxonomy (`daemon/auth/scopes.py`):
`system:read`, `system:probe`, `instances:read`, `instances:write`,
`jobs:read`, `jobs:write`, `messages:write`, `sources:write`,
`maintenance:read`, `mcp:read`, `mcpkb:admin`, `snapshot:write`,
`workspace:read`, `auth:admin`. Unknown names are refused — typos
never mint silently-wrong tokens.

### Per-consumer table (the in-repo YES-WITH-CONFIG consumers)

| Consumer (env var it reads) | Label | Scopes |
|---|---|---|
| `test_builtin_mcp_servers.sh` (`ENSEMBLE_BEARER_TOKEN`) | `mcpservers-test` | `system:read`, `mcp:read` |
| `e2e_pause_resume_test.py` (`ENSEMBLE_BEARER_TOKEN`) | `e2e-pause-resume-test` | `instances:read`, `instances:write`, `jobs:read`, `jobs:write`, `messages:write` |
| `tests/e2e/mock_source_server.py` (fixture-minted) | `e2e-mock-source` | `sources:write`, `instances:read` |

Generic consumer wiring (all row-7-style scripts):

```bash
export ENSEMBLE_BEARER_TOKEN='<the one-time printed token>'
curl -sS -H "Authorization: Bearer $ENSEMBLE_BEARER_TOKEN" \
    -H "Accept: application/json" \
    http://127.0.0.1:9797/api/mcp-servers
```

A missing/wrong token fails LOUDLY: the daemon answers `401
AUTH_REQUIRED` (machine Accept) or `302 → /api/auth/login` (browser
heuristic) and the consumer aborts — the daemon itself is unaffected
(§7.4 fail-loudly contract).

### Generic remote-VM pattern (§7.1 row 14)

One token per remote VM, minimum-scoped to that VM's actual use:

```bash
# On the daemon host:
python -m daemon.auth.cli issue-token \
    --label vm-edge-01 \
    --scopes "instances:read,jobs:read" \
    --ttl 7776000   # 90d; default is auth.bearer_token_ttl_seconds (30d)

# On the VM (the token travels ONCE, out of band — not in a repo):
export ENSEMBLE_BEARER_TOKEN='...'
```

Rotate by minting a replacement token and revoking the old label:

```bash
python -m daemon.auth.cli issue-token --label vm-edge-01 \
    --scopes "instances:read,jobs:read"
python -m daemon.auth.cli revoke --label vm-edge-01
```

`revoke` flips `revoked_at` on every row carrying the label (soft
revocation; idempotent — already-revoked rows keep their first
timestamp). Revoked/expired tokens verify exactly like unknown ones:
a uniform 401, no oracle.

### First admin token (bootstrap, ONE-SHOT)

The very first token (before any exists) mints via the bootstrap env
var. Set it in the daemon's environment (systemd `EnvironmentFile=`,
not an interactive shell), restart, then:

```bash
python -m daemon.auth.cli issue-token --label first-admin --bootstrap
```

* Default scopes for the bootstrap mint: **`auth:admin` only** (OD-14).
  The first token administers; re-mint with explicit scopes for more.
* **ONE-SHOT:** the first mint records a sentinel row
  (`sha256(env_value)`, `consumed_at`, `actor_label`). Any later
  `--bootstrap` with the SAME env value is REJECTED:
  `bootstrap token already consumed at <ts> for <label>; rotate the
  env var`. Rotating the env var re-arms the lane (per-value one-shot).

**Bootstrap env-var REMOVAL step (C2 (c) — REQUIRED):** after the
first successful mint, REMOVE `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` from the
daemon's environment and restart the unit. The sentinel row makes the
old value worthless, but the env var is still a live secret-class
surface (readable by every process in the unit's env, and an
exfil-target for any env-reading tool — the `ens_env_read` deny-list
refuses `ENSEMBLE_AUTH_*`, but the removal step is the durable
closure):

```bash
# systemd example:
systemctl --user edit ensemble.service   # drop the Environment line / EnvironmentFile entry
systemctl --user restart ensemble.service
```

Prefer feeding the env var via `read -s` into a systemd drop-in or a
root-owned `EnvironmentFile` — never an interactive-shell
`export` (shell history). The CLI prints a shell-history warning on
every `--bootstrap` use.

### Trust-class disclosure (C2 (d) — stated plainly)

The deny-list and the one-shot sentinel constrain TOOL and REPLAY
lanes, not BASH. **An agent (or any process) holding shell access AND
database access can invoke this CLI directly and mint a token** — same
trust class as every other DB-adjacent agent capability (the agent
could equally read the `auth_bearer_tokens` table or the DB
credential). Nothing in this runbook claims otherwise: the boundary
that matters is host access, and `bash + DB` IS host access. Treat
agent bash grants accordingly.

### Scope map (which token can touch what)

Route families carry explicit scope requirements (the D4.7 manifest,
pinned by `tests/unit/test_router_scope_manifest.py`): `mcp-servers` →
`mcp:read`; instances/messages/sources/jobs read+write splits;
maintenance writes → `maintenance:read` (+ the pre-existing Origin
guard); DB switch + recovery → `auth:admin`; settings snapshot-admin →
`snapshot:write`; workspace reads → `workspace:read`; the MCP-KB key
CRUD (`/api/mcp/kb/keys`) → `mcpkb:admin`. Undeclared families remain
authenticated-but-any-scope (the D9 default-`*` posture, declared in
the manifest). Insufficient scope is a `403 SCOPE_INSUFFICIENT` (never
a 404). Browser sessions carry the operator grant family
automatically — the SPA needs no token.

**Interim v1 scope-trust disclosure (2026-10-11 review riders — no
scope/code change in this build):**
(i) `mcp:read` is WRITE-CAPABLE in v1 (it guards all mutating MCP
routes); mint mcp tokens with minimal scopes; the `mcp:write` split
arrives in v2.
(ii) ~100 write routes are default-`*` (any-scope bearer) in v1 —
treat every bearer as full-trust; the v2 taxonomy pass is tracked.

## 4. Rotation & maintenance

**Client secret rotation:** replace `ENSEMBLE_AUTH_CLIENT_SECRET` in
the environment and restart the unit. In-flight browser sessions
survive (they are server-side `auth_sessions` rows, not IdP state);
only NEW logins exercise the new secret.

**JWKS / discovery caches:** both cache for 1h by default
(`ENSEMBLE_AUTH_JWKS_CACHE_TTL_SECONDS` /
`ENSEMBLE_AUTH_DISCOVERY_CACHE_TTL_SECONDS`). After an IdP key
rotation, an unknown `kid` triggers a one-shot forced JWKS refetch
(kid-miss path) — no operator action needed; worst case the cache
ages out within the TTL window.

**Session revocation:** `POST /api/auth/logout` (browsers) or
`DELETE /api/auth/session` (API clients) soft-deletes the caller's
`ensemble_sid` row. Expired/invalidated sessions are swept by the
background sweeper (session + state rows, TTL-driven) — the sweeper
is decoupled from the validate path, so a slow sweep never gates a
request.

**Sweeper dual-instance note (resilience):** two ensemble instances
on different ports pointing at the SAME database (the dev-checkout
scenario) are safe — `SKIP LOCKED` row claiming + idempotent deletes
make concurrent sweeps non-interfering. No guard is built; operators
do not need to coordinate sweeps.

**Bearer rotation:** mint-then-revoke (§3). Revoke is label-wide and
immediate (the verify path re-reads the store).

## 5. Upgrade & rollout

The rollout posture is **merge with auth OFF** (D6.2): the release
ships `auth.enabled=false` by default and enables nothing by itself —
enabling auth is a pure operator config+restart act performed AFTER
the upgrade (§1), never a side effect of the merge or a promote.

Before enabling on a deployment, run the §2 walkthrough on the NEW
binary (still OFF) to confirm the default-OFF contract on your
environment. The full pre-merge evidence checklist lives in
`.agents/shared/planning/oauth2-auth/rollout-verification.md`; the
boot-e2e canaries (`frontend/scripts/boot-e2e-maintenance-daemon.sh`,
`boot-e2e-snapshots-daemon.sh`) run UNCHANGED against an auth-ON
daemon (their probe route is public by derivation, G5.3) — never add
bearer-token support to them (that supersede ruling is binding).

Rollback note: auth tables are created by additive migrations on both
PG and SQLite (inert-tables posture); rolling the binary back leaves
them in place and harmless — the older binary ignores them.

## 6. CORS notes

When auth is **ON**, the daemon refuses to boot if its CORS posture
is wildcard origins + credentials and `ENSEMBLE_AUTH_ALLOWED_ORIGINS`
is empty (FR-27, exit 78 — see §7). Name explicit origins; HTTPS is
required for them in production.

When auth is **OFF**, the pre-existing CORS posture is preserved
verbatim — this build performs NO CORS hardening. The broader
wildcard+credentials hardening (a recorded KNOWN RISK,
`daemon/api.py` `allow_credentials=True` with wildcard origins) is
tracked as **OOS-5** (separate ticket), deliberately out of scope
here; the two are cross-linked in the planning decisions (D15) and
the project critical notes.

## 7. Troubleshooting

### Failure-mode table

| Symptom (exact code) | Meaning | Fix |
|---|---|---|
| `503 LOGIN_NOT_CONFIGURED` | The redirect-URI chain resolved no absolute base (cold Host-recorder; headless boot) | Set `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` (or open `/` once in a browser to pre-warm); body names the exact env vars |
| `503 IDP_UNREACHABLE` | Discovery or token endpoint unreachable (network, DNS, IdP down) | Check egress from the daemon host to the IdP; discovery/JWKS problems also WARN in the log at boot |
| `400 STATE_REPLAYED` | The `state` parameter was already consumed or unknown (double-submitted callback, stale bookmark, back-button on the callback) | Start a fresh login; if persistent, check for a proxy stripping/rewriting query params |
| `403 SCOPE_INSUFFICIENT` | Bearer authenticated but lacks the route family's scope | Re-mint with the needed scope (scope map, §3) — never a 404, the route exists |
| `401 AUTH_REQUIRED` | No/invalid/expired credential on a protected route (machine Accept lane) | Bearer: re-mint/rotate (§3). Browser: log in again (session expired — 8h default) |

### 401-vs-302: which one do I get?

The unauthenticated denial shape follows FR-24:

| Request shape | Answer |
|---|---|
| `Accept: text/html` (browser navigation) | `302` → `/api/auth/login?return_to=<path>` |
| Explicit non-HTML `Accept` (e.g. `application/json`) | `401` + `WWW-Authenticate: Bearer realm="ensemble"` (machine contract) |
| No/`*/*` Accept on data routes (`/api/mcp-servers`, …) | `302` (treated as browser — heuristic fallback) |
| No/`*/*` Accept on `/api/auth/*`, `/livez`, `/readyz`, `/api/health`, `/api/info` | `401` (machine-contract path families) |

Gotcha: `/api/auth/me` is the **session (cookie-lane) probe** — a
valid BEARER token on `/me` still answers 401 by design; probe the
bearer lane with a protected data route instead (e.g. `Accept:
application/json` on `/api/mcp/kb/keys` with an `mcpkb:admin` token).

### Boot refuses with exit 78 (auth config misconfig)

Auth-ON misconfigurations fail fast at boot preflight — BEFORE the
port binds — and each refusal line names the offending
`ENSEMBLE_AUTH_*` var. The common ones:

* `ENSEMBLE_AUTH_ALLOWED_ORIGINS` empty (tester finding **F2**):
  `auth.allowed_origins must replace wildcard + credentials ...` —
  set explicit origins (§1b). This is REQUIRED, not optional.
* `enabled=true` without `client_id` / `client_secret` / discovery
  or issuer; `openid` missing from `ENSEMBLE_AUTH_SCOPES`;
  `redirect_uri` non-HTTPS without the insecure-cookie opt-in;
  `SameSite=None` without `Secure=true` (the guard REJECTS that
  combination: `cookie_samesite=None` REQUIRES `cookie_secure=true`);
  session/JWKS/discovery TTL
  bounds violations; unknown `ENSEMBLE_AUTH_*` env vars (stale names
  are refused loudly rather than ignored).
* Auth-OFF conjunction (R3, 2026-10-11): `vscode_protected=true ∧
  auth.enabled=false` ALSO refuses boot with exit 78 — even though
  auth itself is disabled. `ENSEMBLE_AUTH_VSCODE_PROTECTED=true`
  requires `ENSEMBLE_AUTH_ENABLED=true`: with auth off the OAuth2
  middleware never constructs and the /vscode loopback peer gate
  would be disabled with nothing behind it (remote-open under a
  protective-sounding flag). The refusal names BOTH vars; either var
  alone with a consistent auth state never refuses.

Exit 78 is EX_CONFIG: the launcher must NOT retry — fix the env and
restart manually (restart cannot fix config).

### Fresh-SQLite boot trap (pre-existing, NOT auth — F1)

A fresh **SQLite** database fails to boot on migration
`20260714_000001` (PG-only `ALTER TABLE ... DROP CONSTRAINT IF
EXISTS` syntax that SQLite rejects; the migration runner is a NO-OP
on PG, so PG deployments never hit this). Pre-existing defect,
tracked as project critical note **CN 43852fbd** — DO NOT patch the
migration; the tested workaround for throwaway verification boots is
the ledger-pre-mark helper `tests/fakes/f1_premark_helper.py`
(records an authentic skip for that one migration). Fix lands in its
own commission.

### Daemon answers 503 AUTH_TEMPORARILY_UNAVAILABLE

The session store (PostgreSQL) is unreachable — protected paths fail
closed while public paths and module lanes stay unaffected. `/readyz`
reports `auth_dependency_status` (its verdict NEVER flips because of
auth). Restore the DB; no restart needed.

## 8. Cross-references

* Full config surface: `daemon/config.py` `AuthConfig` (23
  `ENSEMBLE_AUTH_*` vars) — planning source `[REQ] §6.1-6.3`.
* Planning set: `.agents/shared/planning/oauth2-auth/`
  (`plan-overview.md`, `decisions.md` — D1/D19/D20 module lanes,
  D15 CORS, OD annex — `rollout-verification.md`).
* Technical analysis: `.agents/shared/planning/oauth2-auth/technical-analysis.md`
  (threat model, failure modes, machine-client lane).
* Related runbook: `docs/runbooks/live-views.md` (the v-pattern this
  file follows; the redirect-base fallback interplay).
* Critical notes: R3 source-forgery follow-up ticket — **CN
  257488dc-c0b1-42d4-8581-f97cb4d55f72** (2026-10-11, filed-not-
  closed: auth does NOT close the forge-source lane — a stolen
  `jobs:write` bearer can still forge `source=api`; fix = derive
  source from `AuthContext.subject`); fresh-SQLite migration trap —
  **CN 43852fbd** (§7 above).
* CORS hardening follow-up: OOS-5 ticket (§6).

## Annex A — KB-MCP-auth posture (D1, re-labeled v1.4)

> **v1.4 supersession note:** this annex's original "PUBLIC in v1"
> framing is superseded by the module-lane classification (decisions
> D19) — the two KB mounts are module-lane, not public. The trust
> prose below remains valid as history and as the reason the lane
> stays open.

`/api/mcp/kb` and `/api/mcp/kb/sse` are the KB-MCP (knowledge-base
MCP) module mounts. They are consumed by local agents cross-process
today with ZERO auth (pre-existing posture — see Annex B). On
Starlette 0.52.1, Mounts traverse parent middleware, so OAuth2-gating
them would have 401'd every KB-MCP consumer on day 1 of auth-ON;
the v1.4 ruling instead classifies them MODULE-LANE — the middleware
passes them through untouched in BOTH auth states, which preserves
the loopback trust posture byte-identically. The per-surface
credential seam (S-20 / `kb_mcp_auth_enabled`) is reserved for the
module-key commission; it is NOT part of OAuth2.

## Annex B — Module-lane key management (D19/D20)

**Module keys are INDEPENDENT of OAuth2.** OAuth2 does NOT govern the
module endpoints: webhook ingest (`/api/webhooks/*`) and the MCP-KB
mounts authenticate with their own per-module credentials, and those
credentials are never accepted by the OAuth2 lane (and vice versa —
HARD lane isolation, D20: separate stores, distinct token namespaces,
three-class middleware routing, negative cross-lane test matrix).

Current posture, disclosed plainly:

* **MCP-KB mounts are fully open today** (`kb_server.py` zero auth;
  6 tools including a WRITE tool + context reads; no key surface
  exists yet). This is pre-existing, unchanged-by-design until the
  module key mechanism lands — a security improvement INDEPENDENT of
  OAuth2 (it will work with auth OFF too).
* **Administering the two credential families:**
  * MCP-KB keys → the NEW `/api/mcp/kb/keys` CRUD family
    (`daemon/routers/mcp_kb_keys.py`, OAuth2-governed management
    surface, scope `mcpkb:admin`).
  * Webhook secrets → the EXISTING `/api/sources` CRUD
    (OAuth2-governed management surface). A per-source
    `webhook_secret` is a pre-existing best practice for the
    open-ingest posture (`webhooks.py`), NOT auth configuration —
    webhook senders never present OAuth2 credentials.
* Secret read-back masking on the management surface (GET
  `/api/sources`) is SHIPPED: **mask-on-read + PUT-with-masked-no-clobber**
  (plan §12 item 4(c) option (a)) — RULED 2026-10-11 by the leader.
  GET redacts `webhook_secret` to the bare `********` mask sentinel;
  a PUT carrying the masked value back keeps the stored secret (the
  round-trip no-clobber guard); a PUT carrying new plaintext rotates.
  Exercised by the AC-42 round-trip test
  (`tests/integration/test_module_lane_isolation.py::TestWebhookSecretMaskingAC42`).
* **Pinned sentinel semantics (exactly as shipped — council-ratified
  ACCEPT-AS-PINNED, 2026-10-11):**
  * The bare `********` sentinel is the SOLE set-ness signal on
    read-backs — no `has_webhook_secret` boolean exists.
  * Sentinel-equality semantics (PUT path):
    * A stored-literal echo — a PUT carrying back the same masked
      value read from a GET — is a SELF-HEALING NO-OP: the stored
      secret is preserved (never clobbered by the mask).
    * A literal `********` CANNOT be set as a NEW secret while a
      secret exists.
    * BUT on a SECRET-LESS source, a sentinel PUT PERSISTS the mask
      literal as a real (weak, 8-char) secret — operator-class
      action, `sources:write` scope required.

## Annex C — Decisions & defaults (OD annex)

All Open Decisions (OD-1…OD-15) are DECIDED (see `decisions.md` §OD
Rulings for the full folded ledger). The operator-visible subset:

| Knob / behavior | Shipped default (DECIDED) |
|---|---|
| `/docs`, `/redoc`, `/openapi.json` | public; `ENSEMBLE_AUTH_DOCS_ENABLED=false` 404s the trio (OD-1) |
| First-admin bootstrap mint | CLI + one-shot `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN`, `auth:admin` only (OD-2, OD-14) |
| Scope granularity | 14-scope closed taxonomy + router-mount declaration cage (OD-3) |
| CORS when auth ON | boot-refusal guard only; hardening = OOS-5 (OD-4) |
| Cookie under plain HTTP | `Secure` ON; `ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE=true` explicit opt-in (OD-5) |
| Redirect base chain | `EXTERNAL_REDIRECT_BASE` > live-views fallback > Host-capture > bind (OD-6) |
| `/vscode` WebSocket | loopback posture unchanged; `ENSEMBLE_AUTH_VSCODE_PROTECTED=true` pre-wires protection (OD-7) |
| Multi-IdP | single issuer v1; `idp_alias` seam reserved (OD-8) |
| `/readyz` when auth degraded | `auth_dependency_status` sub-field; verdict never flips (OD-9) |
| Logout | `POST /api/auth/logout` + `DELETE /api/auth/session` alias (OD-10) |
| Cookie name | `ensemble_sid`, `SameSite=Lax`, `Secure` (OD-11) |
| SPA expiry UX | middleware 302 with sanitized `return_to` (OD-12) |
| SPA assets | implicit PUBLIC-ASSETS rule mirroring SPA carve-outs (OD-13) |
| CSRF | Origin-guard predicate (presence-based branching); SameSite=Lax covers the residual (OD-15, D8 v1.2) |

Settled without user input ([REQ] §9.11): default scope set for
minted tokens, `Authorization: Bearer` header, cookie `Path=/`, 8h
session TTL, 1h discovery/JWKS cache TTLs, `secrets`-module-only
crypto.
