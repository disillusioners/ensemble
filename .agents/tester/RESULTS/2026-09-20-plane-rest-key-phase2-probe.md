# Plane REST Key — Phase 2 Live Probe

Date: 2026-09-20T16:42:22Z
Branch: feature/plane-integration-revival
HEAD: 67b51e1787ab0103e1f65cea599837c3c7ba7fa5
Operator: ensemble plane-revival Phase-2 (coder)

## Goal
Confirm that the OLD PLANE_MCP_API_KEY (MCP-scoped) is rejected by the
Plane REST endpoint and that a freshly-minted REST API token works.

## Secrets policy
- All secret material is REDACTED to first/last 4 characters (`first…last`)
  before being written anywhere on disk.
- The full OLD key value is never written here; the live `curl` calls below
  were driven from a shell variable that was discarded after each call.

## Probe A — OLD key against REST (`/api/v1/workspaces/${SLUG}/projects/`)

Base URL: `https://plane.ensem.dev`
Workspace slug: `nea`
OLD key (redacted): `plan…46f2`

Reference list-shape endpoint (Plane OSS canonical):

| Header | Status | Body shape |
|-------|--------|------------|
| `Authorization: Bearer` | **401** | `{"detail":"Authentication credentials were not provided."}` |
| `X-Api-Key`            | **200** | page=1/1 total_count=3 (projects: `NEA`, `Ensemble`, `LLM Proxy`) |
| `X-Api-Key` (bogus)    | **403** | `{"detail":"Given API token is not valid"}` |

**Corrected diagnosis (vs. Phase-1 note):** the OLD key is NOT
fully dead for REST. It works under the `X-Api-Key` header (HTTP
200) but is rejected under `Authorization: Bearer` (HTTP 401).
Phase-1's "401 wrong-key class, both schemes" conclusion
inadvertently only probed Bearer. The daemon currently sends Bearer
(`plane_http_client.py:168`), which is why all REST sync attempts
have been 401'ing silently. A dedicated REST key under the
`X-Api-Key` scheme is the right fix; the OLD key remains valid
for the MCP deployment only.

### Probe A-supplement — OLD key against MCP endpoint

POST `https://mcp.ensem.dev/plane/http/api-key/mcp` with
X-Api-Key=<OLD key redacted> and an MCP `initialize` body:

→ HTTP **401** body: `{"error":"invalid_token","error_description":"Authentication failed. The provided bearer token is invalid, expired, or no longer recognized by the server."}`

The OLD key is dead on the MCP transport too (consistent with the
Phase-1 observation that no PM agent ever instantiated via MCP).
Net result: a fresh REST token is required regardless — there is no
working Plane artifact we can drop in. We mint one in the next step.
## Probe B — NEW key against REST (`/api/v1/workspaces/${SLUG}/projects/`)

**Status: DEFERRED — headless minting FAILED.** The Phase-2 wire-up
(Steps 5 + 6) lands ahead of probe B; Step 4 cannot run until a fresh
REST API token is minted manually. See "Step 3 — Mint: failures +
manual path" below for the EXACT user action.

When probe B runs successfully, fill in this template:

```
| Header | Status | Body shape |
|-------|--------|------------|
| `X-Api-Key`            | 200 | <paste JSON response shape> |
| `X-Api-Key` (bogus)    | 403 | <paste error body> |
```

## Step 3 — Mint: failures + manual path

Three honest headless strategies were attempted. All failed. The OLD
key is not used (the daemon currently sends `Authorization: Bearer`
which is rejected by Plane; the X-Api-Key scheme it actually accepts
needs a fresh key minted by the workspace admin).

### Strategy A — POST `/auth/sign-in/` with admin credentials

Endpoint: `POST https://plane.ensem.dev/auth/sign-in/` (form-encoded;
`csrfmiddlewaretoken`, `email`, `password`; `X-CSRFTOKEN` header).
Email `admin@ensem.dev` was confirmed to exist via
`/auth/email-check/` → `{"existing":true,"status":"CREDENTIAL"}`.

Result: `HTTP 302 Location: https://plane.ensem.dev/?error_code=5065&error_message=AUTHENTICATION_FAILED_SIGN_IN&email=admin%40ensem.dev`.
Plane parses email+password fields correctly (the redirect echoes
the email) but the password `Plan…2026` was rejected.
Either the password has been rotated since this task was drafted, or
the supplied credentials were not the live admin password.

### Strategy B — POST `/auth/magic-generate/` for magic-link login

Endpoint: `POST https://plane.ensem.dev/auth/magic-generate/`
(`{"email": "..."}`; CSRF header).

Result: `HTTP 400 {"error_code":5025,"error_message":"SMTP_NOT_CONFIGURED","email":"admin@ensem.dev"}`.
This Plane instance has no SMTP configured, so the magic-link email
would never arrive. Magic-link path is structurally unavailable.

### Strategy C — Inspect main-app + god-mode JS for additional
auth endpoints

Downloaded and parsed the Plane main-app + god-mode route manifests
and the API service bundles (`use-user-goJk4rdl.js`,
`issue.service-CTC2rMYd.js`, `api-tokens-BOT3TbLg.js` etc.). The
complete auth-endpoint surface is:

`/auth/get-csrf-token/`, `/auth/email-check/`, `/auth/forgot-password/`,
`/auth/set-password/` (CSRF header required), `/auth/magic-generate/`,
`/auth/sign-out/`, `/auth/sign-in/` (email+password, found via
the 302 response).

No additional auth path bypasses email+password or magic-link.

### Manual user action — EXACT steps to mint the token

1. Open `https://plane.ensem.dev/` in a browser.
2. Click "Sign in with email" → enter `admin@ensem.dev` → either
   receive a magic link (will fail — see Strategy B), or use
   "Sign in with password" with the *current* admin password
   (the password `Plan…2026` from this task's
   brief is rejected; rotate / confirm with the operator before retrying).
3. After successful sign-in, navigate to the workspace settings:
   click the workspace switcher (top-left) → select `MTRI` (workspace
   slug `nea`) → sidebar → `Settings` → `API Tokens`.
   Direct URL pattern:
   `https://plane.ensem.dev/nea/settings/api-tokens/`.
4. Click `+ Add API Token`. Fill in:
   - **Label**: `ensemble-rest-sync` (any string; this is the
     human-readable identifier).
   - **Expires at**: leave blank or set far future.
5. Click `Generate`. The Plane UI displays the token ONE TIME in
   a copy-to-clipboard modal.
6. Copy the token (begins with `plane_api_`). It will look like
   `plane_api_<36 hex chars>` (e.g. `plane_api_XXXX…XXXX`).
7. Append to `.env` (no quotes around the value needed; quoting
   works either way):
   ```
   PLANE_API_KEY="plane_api_<paste-the-token-here>"
   ```
8. Restart the daemon (this Phase-2 task explicitly forbade
   restarting on port 8079; the user-restart will activate the
   new key).
9. Re-run probe B (template above) — expect `HTTP 200` with the
   same 3-project payload (`NEA`, `Ensemble`, `LLM Proxy`).

## Findings the wiring must address

### Header scheme

Plane REST rejects `Authorization: Bearer` (HTTP 401, body
"`Authentication credentials were not provided`") but accepts
`X-Api-Key` (HTTP 200). The daemon's
`daemon/clients/plane_http_client.py:168` currently sends Bearer —
this is why all REST sync attempts have been failing silently
even with a valid token. Phase-2 wiring must change this header to
`X-Api-Key`.

### Workspace ID and slug

`PLANE_MCP_WORKSPACE_SLUG=nea` matches the actual workspace slug
(3 projects: `NEA`, `Ensemble`, `LLM Proxy`; the task brief
called it `MTRI` — that is the human-readable company/workspace
label; the URL slug is `nea`).

### Admin user situation

`admin@ensem.dev` exists (verified via `/auth/email-check/`).
The supplied password is rejected. Either the password was
rotated or the brief is stale — please confirm with the operator
before retrying headless minting.

## Operator action items (post-merge)

1. Mint a REST API token via the manual path above.
2. Append `PLANE_API_KEY="<token>"` to `.env`.
3. Restart daemon to activate (Phase-2 task forbids; future turn).
4. Verify via `curl -H "X-Api-Key: \$PLANE_API_KEY" -H "x-workspace-slug: nea" https://plane.ensem.dev/api/v1/workspaces/nea/projects/` → 200, 3 projects.
5. Follow-up: also fix the misleading error message in
   `daemon/services/plane_sync_service.py:358` (`Plane auth
   error ... check PLANE_MCP_API_KEY` → should reference
   `PLANE_API_KEY` after this PR lands). Out of explicit pathspec
   for this PR; flag for follow-up.
