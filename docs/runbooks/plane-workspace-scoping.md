# Plane workspace scoping — operator runbook

Covers the env-scoped Plane workspace enforcement (lane `plane-pm-issue-tools`,
2026-11-02): workspace **`nea` is production/live ONLY**; **dev, demo and
sandbox MUST target the `e2e` test workspace for ALL Plane operations** — REST
*and* MCP. Enforcement is **STRUCTURAL** (daemon code — the resolver plus the
REST/MCP chokepoints), never convention.

## 1. The directive (verbatim)

> Plane workspace separation is ENVIRONMENT-SCOPED and must be structural:
> workspace `nea` is for the production/live environment ONLY; dev and demo
> environments MUST target the `e2e` test workspace for ALL Plane operations
> (issue tools, `plane_sync_project` lanes, PM dispatches). A non-production
> environment writing into the production workspace is the exact pollution
> class to prevent.

*(User directive via Discord, 2026-11-02.)* A non-live environment writing
into `nea` is the exact pollution class this guard prevents.

## 2. Per-environment contract

The daemon reads `PLANE_MCP_WORKSPACE_SLUG` for **both** lanes (the REST URL
path and the MCP `x-workspace-slug` header). The workspace slug is **never**
a tool parameter — it is resolved from the environment.

| Env | Classification | Required slug | Who sets it |
|---|---|---|---|
| **live** (`~/agents-ensemble`) | live (explicit `ENSEMBLE_SELF_ENV=live`, or no marker + own `POSTGRES_DB=ensemble_prod`) | `nea` typical; **any configured slug permitted** (ADJ-1 live-exempt); unset ⇒ account-default (unchanged behaviour) | live install `.env` (operator) |
| **demo** (`~/agents-ensemble-demo`) | non-live | **`e2e`** | `scripts/deploy.sh` sed-pins `e2e` + grep-asserts before `mv` |
| **dev** (main checkout + worktrees) | non-live | **`e2e`** | manual `.env` — mode `0600` |
| **sandbox** (staged-binary drills) | non-live | **`e2e`** | drill harness |

Rules that follow:

1. **Dev/demo/sandbox MUST set `PLANE_MCP_WORKSPACE_SLUG=e2e` explicitly.**
   Unset ⇒ the resolver refuses (fail-closed) ⇒ no `mcp_servers` row, no
   `plane_*` tools. It never silently falls back to the account-default
   workspace.
2. **Live may leave the slug unset** — that preserves the account-default
   behaviour exactly. Under ADJ-1 live-exempt, live permits any *configured*
   slug (`nea`, `e2e`, or a third).
3. Key liveness is not a config property — the daemon's availability gate is
   presence-only; liveness is surfaced by the observability layer (see §5)
   and fixed by the operator's credential re-mint (see §8).

## 3. The resolver — behaviour and typed refusals

`daemon/plane_workspace.py::resolve_plane_workspace()` is the single
resolution point; the REST client, the MCP server registration, the sync
service and the `test-connection` report all consult it.

- **Fail-closed, typed refusal on non-live** when the slug is wrong **or**
  unset. A refusal means: no `plane` row materialises (or a stale row is
  deactivated), no `plane_*` tools reach any agent, and the boot log carries
  a WARNING with the typed reason.
- **No kill-switch for the guard.** `PLANE_MCP_ENABLED=false` remains a
  *deliberate* off for the whole builtin Plane server (reported by the
  observability layer as deliberately disabled, never as an unexpected
  skip) — it is not a workspace-policy switch.

The typed refusal reasons (interpolated verbatim into the boot WARNING):

| Reason string | Meaning |
|---|---|
| `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'` | non-live + unset slug — set `PLANE_MCP_WORKSPACE_SLUG=e2e` |
| `workspace 'nea' is production-only; this environment resolves to {label}` | non-live + `nea` — the prod workspace is live-only |
| `workspace '{slug}' is not permitted in a non-production environment; expected 'e2e'` | non-live + any third slug (e.g. a copied `your-workspace-slug` placeholder) |
| `environment could not be classified as production; refusing to target the 'nea' workspace` | unclassified env (fail-closed) — fix `ENSEMBLE_SELF_ENV` or the own-DB name |
| `environment marker '{marker}' is not a recognized environment (expected one of dev, demo, live, sandbox); refusing to classify as production` | garbage marker — fix `ENSEMBLE_SELF_ENV` |

## 4. Why live detection is lane-local (D-1)

Prod access requires an **explicit** `ENSEMBLE_SELF_ENV=live` marker OR the
daemon's **own** `POSTGRES_DB=ensemble_prod`. An **auto-derived** `live` is
not trusted: the shared env oracle (`daemon/tools/upgrade_tools.py::_auto_derive_env`)
reads the **sibling** live install's `.env` on a multi-install host and
returns `live` for a dev checkout **before** the dev-shape signal runs —
fail-open in exactly the guard's dangerous direction. The lane-local
predicate contains this for Plane only; **the shared oracle remains wrong for
its other consumers** (routed follow-up O3 — not fixed by this lane).

## 5. Operator caveats

- **Expired credential (R1/ADJ-4):** the live and demo `PLANE_MCP_API_KEY`
  are the same **expired token** (HTTP 401 `invalid_token`). The presence-only
  gate still materialises a `plane` row, so **every session fails 401** — a
  row's presence is *not* evidence Plane works. The re-mint is **user-owned**
  (§8); until it lands, the failure is **visible** (the boot WARNING taxonomy
  + the honest `test-connection` report) rather than silent.
- **`POST /api/mcp-servers/test-connection` historically bypassed the
  kill-switch (AD7/D7, pre-existing):** since the lane's P3-D it consults the
  kill-switch and the workspace resolver for PLANE-shaped configs and
  reports the typed refusal / deliberately-disabled state instead of the
  stale `success=true, 177 tools`. Treat any legacy-shaped success report
  with suspicion; the refusal strings in §3 are the truthful state.
- **`GET /api/mcp-servers` returns the bearer in `config.headers` unredacted
  (AD2/O1, routed follow-up):** `redact_secrets` scrubs `env` and `url`
  userinfo only, not `headers` — treat the response as secret-bearing; do
  not paste it anywhere.
- **The operator escape hatch (R22):** the workspace guard governs the
  **builtin** Plane lane (and the daemon-created rows). A **hand-created**
  MCP server (not the builtin) pointed at `nea` is **operator authority**: it
  is not name-matched to the builtin, the name-keyed preload gate returns
  "no opinion", and the workspace policy does not speak to it. The
  REST-create endpoint is an operator-authority path as well. The
  registry-vs-row cross-check DOES name an operator-created row in its census
  (it is a *registered* server) ⇒ it is **visible**, not *refused*. **An
  operator who creates such a server has, deliberately, left the guard's
  scope.**
- **The FE Plane indicator reflects ENV PRESENCE, not RESOLUTION (P4-B):**
  the frontend Plane surface is driven by `PLANE_BASE_URL`
  (`daemon/routers/settings.py` — the FE iframe gate). The daemon can refuse
  the workspace while the FE still shows a Plane indicator ⇒ the indicator
  means **"configured"**, never **"available"**. No FE code change in this
  lane — documentation only.

## 6. Shell-layer defence-in-depth (NR2)

Two shell fences predate the daemon guard and **stay**:

- `scripts/deploy.sh` sed-pins `PLANE_MCP_WORKSPACE_SLUG=e2e` into the
  demo `.env` it generates and **grep-asserts** the active line before `mv`.
- `scripts/run_tests_scrubbed.sh` unsets `PLANE_MCP_WORKSPACE_SLUG` +
  `PLANE_BASE_URL` per-variable in test children and **leak-gates** them
  (`exit 78` if either survives).

**The deploy pin is defence-in-depth and covers only a deploy-generated
`.env`** (P4-B): a hand-written `.env` (the dev checkout, an operator edit, a
copied install) bypasses the sed pin entirely. The daemon guard is the
**structural** enforcement and covers **every** `.env`. The daemon layer is
*additional*, never a replacement — do not trust one layer
(`boot_subprocess_sweep.py` precedent).

## 7. How to configure per environment

1. Mint per-environment values (see the `.env.example` Plane block — all
   sample lines are commented **deliberately**: an active placeholder would
   register a `plane` row against the real endpoint with a placeholder
   bearer, because the availability gate is presence-only).
2. In the target `.env` (mode `0600`): set `PLANE_MCP_URL`,
   `PLANE_MCP_API_KEY`, and `PLANE_MCP_WORKSPACE_SLUG` — `nea` on live,
   `e2e` on dev/demo/sandbox. For the REST sync lane additionally
   `PLANE_BASE_URL` + `PLANE_API_KEY` (absent ⇒ REST inert).
3. Restart the daemon. On success the boot log shows the plane row
   registration; on refusal it shows the WARNING with the §3 reason — fix
   the named variable, not the guard.
4. Stale rows self-heal: a schema/config heal re-materialises
   `headers['x-workspace-slug']` from the resolver on the next available
   boot, and a workspace refusal deactivates a pre-existing active row.

## 8. Live/demo credential state (ADJ-4 — user-owned)

The live and demo Plane MCP bearers are the **same expired token**. After
this lane's promote, live/demo carry a configured-but-unusable Plane lane
until the **user re-mints** the credential (workspace Settings → API
tokens). The re-mint is **not** a lane task and not a blocker for staging —
the lane's contribution is that the failure is now **visible** (typed
reasons in the boot WARNING + the honest `test-connection` report) instead
of silent. Dev is the only environment armed with a working credential
today.
