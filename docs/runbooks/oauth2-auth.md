# OAuth2 Authentication — Operator Runbook (DRAFT: §Token mint)

> **Status:** Phase 4 (T4.6) draft. This section is the operator path
> for machine (bearer) tokens. The full runbook (enable, IdP wire-up,
> cookie posture, module lanes) lands in P6 (T6.1) — the scope tables
> and patterns below are the P4 deliverable per phase4-plan §3 T4.6.

## §Token mint

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

Scope names are the closed v1 taxonomy (`daemon/auth/scopes.py`):
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
    http://127.0.0.1:8079/api/mcp-servers
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
surface (readable by every process in the unit's env, and a
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
`tests/unit/test_router_scope_manifest.py`): `mcp-servers` →
`mcp:read`; instances/messages/sources/jobs read+write splits;
maintenance writes → `maintenance:read` (+ the pre-existing Origin
guard); DB switch + recovery → `auth:admin`; settings snapshot-admin →
`snapshot:write`; workspace reads → `workspace:read`. Undeclared
families remain authenticated-but-any-scope (the D9 default-`*`
posture, declared in the manifest). Insufficient scope is a `403
SCOPE_INSUFFICIENT` (never a 404). Browser sessions carry the operator
grant family automatically — the SPA needs no token.
