---
version: 1.3.0
category: execution
auto_load: false
---

capability_check("opendesign")

# install-opendesign — provision the OpenDesign MCP server (§7 bootstrap)

You are executing the FIRST user of the designer-agent bootstrap
pattern: drive the full OpenDesign MCP install through the daemon
HTTP API. **No generic `bash install`** — the only install surface is
the daemon's `configure-builtin` route over an `OpenDesignMCP` builtin
definition (arch §7.4 invariant).

v1.3.0 REPLACES v1.2.0's two-step ceremony (observe the marker, ask
the user, follow up) with **fully self-provisioning agent tools**:
the skill reads the daemon's live LLM connection values via
`ens_env_read` and writes the OpenDesign MCP BYOK fields via
`mcp_set_env` + `kms_attach(env_source=...)`. No user prompt for
credentials — the keys are already there (per user directive
2026-10-02: "BYOK values come from the system .env; no new key
material"). The `kms_request` + `kms_attach(handle=...)` flow
remains the right tool for self-minted opaque credentials (e.g.
`OD_API_TOKEN` for non-loopback OD installs); the v1.3.0-bridge
completion replaces ONLY the `BYOK_API_KEY` lane with the env-ref
mode.

The end-to-end verification contract (daemon health → seam → tool
surface → credentials readiness → `od_generate_design` smoke) is
preserved from v1.2.0. The fast-path discipline (verify-only on a
fully-working host, no mutation) is preserved from v1.2.0. The
v1.1.0 self-install procedure for the OpenDesign daemon itself is
preserved verbatim (no Docker, nvm user-space only). All fences
intact.

`requires:` for this skill is `{tools: [bash, infra], env: []}` —
deliberately NO `mcp:` key (PR4 resolution): requiring the
not-yet-installed `opendesign` capability in front-matter would
deadlock the loader (the capability only becomes true BY this skill).
`infra` covers `mcp_set_env`, `kms_request`, `kms_attach`, and
`kms_lookup_handle`. The skill ALSO needs the `ens-env` category
(`ens_env_read`) — `ens-env` is required DIRECTLY (listed in
`agents/worker/skill-set.yaml` requires.tools alongside `bash` and
`infra`); the canonical `agents/worker/meta.json` already lists
both `infra` and `ens-env`, so a worker spawn on a load no-override
inherits both. The pre-flight below is the bootstrap loop: it runs
before install and again as the body-final self-check.

## BYOK mapping (v1.3.0 NEW — verified against the live install)

The skill reuses the ensemble daemon's existing LLM connection
values rather than asking the user for new keys. The mapping table
below is the single source of truth; ALL other sections reference
back to it. Key names only are listed — **values are never echoed
to logs, tool-results, checkpoints, or reports** (PB-F1 family; the
audit trail logs `keys_requested=N result_size=N missing=N` only).

| BYOK field     | Source env key            | Live install key name (live) | In repo dev .env? | Mechanism                          |
|----------------|---------------------------|-----------------------------|-------------------|------------------------------------|
| `BYOK_BASE_URL`| `OPENAI_BASE_URL`         | `OPENAI_BASE_URL` ✓         | YES (`OPENAI_BASE_URL`) | `mcp_set_env` (plaintext)     |
| `BYOK_MODEL`   | `OPENAI_MODEL_VISION` if concrete, else literal `"vision"` | `OPENAI_MODEL_VISION` ✓     | (not present)     | `mcp_set_env` (plaintext)          |
| `BYOK_API_KEY` | `OPENAI_API_KEY`          | `OPENAI_API_KEY` ✓          | YES (`OPENAI_API_KEY`)  | `kms_attach(env_source=...)` (ENV-REF) — see §"BYOK_API_KEY — env-ref attach" |
| `BYOK_PROVIDER`| derived (`"openai"` if BYOK_BASE_URL contains `/v1`) | n/a                  | n/a               | `mcp_set_env` (plaintext, optional) |
| `OD_DAEMON_URL`| default `http://127.0.0.1:7456` | n/a                    | n/a               | `configure-builtin` default (no write needed) |

Precedence rule for `BYOK_MODEL`: **concrete value of
`OPENAI_MODEL_VISION` wins; else literal `"vision"`** per user
directive ("BYOK_MODEL = vision per user directive (always available
on the user's llm-supervisor-proxy)"). On the live install
`OPENAI_MODEL_VISION=vision`; on a dev host where the var is absent
or empty, fall back to literal `"vision"`. Documented explicitly
because "vision" is the canonical model name on the
`llm-supervisor-proxy` the user runs; substituting `OPENAI_MODEL`
("agentic", "coding", etc.) would route generation to the wrong
model lane.

**PB-F1 discipline (hard rule):** values from `ens_env_read` flow
into the LLM's working context and into the checkpoint graph only
transiently. The skill never echoes a value into a `Result:` line,
the install report, the journal, or the user-visible message.
`mcp_set_env`'s success result echoes key NAMES only (`env_keys_set:
["BYOK_BASE_URL", "BYOK_MODEL"]`) — verify via key NAMES in the
report. The single legitimate plaintext surface for the resolver is
the MCP subprocess env at spawn time (`kms_resolver.resolve_env`).

### BYOK_API_KEY — env-ref attach (v1.3.0 bridge, RESOLVED)

The v1.3.0 SKILL ships the env-ref bridge for `BYOK_API_KEY`: the
minted-handle lane (`kms_request` → `kms_attach(handle=...)`) is
PRESERVED for its original purpose (per-credential minting with
random opaque tokens — e.g. `OD_API_TOKEN` for non-loopback OD
installs), but `BYOK_API_KEY` no longer needs a fresh random token.
The skill uses the env-ref mode of `kms_attach` instead:

```
kms_attach(server_id=<id from 3c>, env_key="BYOK_API_KEY",
            env_source="OPENAI_API_KEY")
# → {"server_id": ..., "env_key": "BYOK_API_KEY",
#    "env_source": "OPENAI_API_KEY",
#    "marker": "__KMS_ENV__OPENAI_API_KEY__"}
```

The marker `__KMS_ENV__OPENAI_API_KEY__` is written into
`config.env["BYOK_API_KEY"]`. The binding entry is appended to
`instance_metadata.env_refs` (new list, mirroring `bound_handles`'s
audit-substrate semantics at `infra.py:998-1050`). The plaintext
`OPENAI_API_KEY` value is NEVER read or returned by the tool — the
caller passes only the var NAME. `kms_attach` performs an eager
presence check on the named var (`os.environ` membership) and refuses
with `ERROR: ENV_VAR_NOT_FOUND` if the var is absent (so the
caller learns the misconfiguration at attach time, not at next
spawn). The resolver at `daemon/services/kms_resolver.py`
substitutes the marker to `os.environ["OPENAI_API_KEY"]` at spawn
time (mirroring the LANE-1 `__KMS_REF__<HANDLE>__` substitution).
Plaintext lives in the subprocess env only — in-RAM, never stored.

**Why env-ref instead of mint-and-store:** the user directive
(2026-10-02) explicitly says "BYOK values come from the system
.env; no new key material" — and `kms_request` cannot ingest an
external plaintext today (it mints a fresh `secrets.token_urlsafe(32)`
internally). Env-ref reuses the existing daemon env without
modifying the input.

**Why env-ref instead of `mcp_set_env` for the plaintext:** the
leader policy keeps `mcp_set_env` rejecting secret-shaped KEY NAMES
(any value containing KEY/TOKEN/SECRET/PASSWORD — see the
`_env_key_is_secret_shaped` gate at `infra.py:1146-1158`). That
rejection is the load-bearing fence against plaintext credentials
crossing the agent tool boundary in tool-results and LangGraph
checkpoints (PB-F1 family). Env-ref bypasses that gate in a
CONTROLLED way — the marker carries the var NAME, not the value, so
the plaintext only materialises at the spawn seam (the same seam the
LANE-1 marker path uses).

**Restart durability:** env-ref markers are restart-DURABLE. The
binding is the var name (`__KMS_ENV__OPENAI_API_KEY__`); the
resolver reads `os.environ["OPENAI_API_KEY"]` fresh on every spawn.
A daemon restart does NOT drain the binding — as long as the env
var is still set in the daemon's process env, the next spawn
resolves it to the (current) plaintext. **This is the key
durability difference vs. the minted-handle lane** — see §"Fast-path
KMS-Lite restart-drain check" below for the (still preserved)
restart-drain semantics on the minted-handle lane.

**Marker-clobber trap (still applies):** a later `configure-builtin`
run regenerates the row's `config` from the builtin schema payload
and can drop env keys — including env-ref markers, exactly like
LANE-1 KMS markers. The install path runs `configure-builtin` FIRST
and `mcp_set_env` / `kms_attach` SECOND so a subsequent reconfigure
triggers a re-apply. The agent-lane re-apply discipline is
identical for both lanes.

The KMS-Lite marker mechanism, the LANE-1 secret-shape rejection,
the resolver seam, the audit discipline, and the redaction filter
(PB-F1) all remain intact. The single addition is the env-ref lane
(parallel to LANE-1, not replacing it) and the corresponding
`__KMS_ENV__<VAR>__` marker shape in the resolver.

## Idempotency contract (v1.2.0 preserved)

Re-running this skill on a host where the capability is already
`present` MUST NOT mutate state. The fast path:

1. `capability_check("opendesign")` → `present`
2. Run the five-stage end-to-end verification below
3. If all five PASS → emit ONE `Result:` summary line
   (`Result: opendesign capability present — 5/5 verification stages
   PASS`) and exit. No install call. No KMS call. No row write.
4. If any stage FAILS → fall through to the install path. Re-running
   the install path is safe (`configure-builtin` is idempotent;
   `mcp_set_env` is merge-not-replace; `kms_attach` collapses
   same-handle bindings).

The fast path exists because every mutation here writes to a row
the host may already have configured — re-writing on every probe
would race with concurrent installers and could clobber a marker.

### Fast-path restart-drain check (v1.3.0 env-ref durable, LANE-1 drained)

v1.3.0 ships TWO marker lanes with DIFFERENT restart durability:

* **LANE-2 env-ref markers (`__KMS_ENV__<VAR>__`)** — restart-DURABLE.
  The binding is the var name; the resolver reads
  `os.environ[<VAR>]` fresh on every spawn. A daemon restart does
  NOT drain the binding — as long as the env var is still set in
  the daemon process env, the next spawn resolves it to the current
  plaintext. The fast path DOES NOT need to self-heal env-ref
  markers.

* **LANE-1 KMS-Lite handles (`__KMS_REF__<HANDLE>__`)** — KMS-Lite is
  **IN-MEMORY**: every daemon restart drains every handle. The
  verify-only fast path MUST detect a dead LANE-1 marker and
  self-heal (unchanged from v1.2.0):

  1. **Slot-presence check** for `BYOK_BASE_URL`, `BYOK_MODEL`, and
     `BYOK_API_KEY` via the row GET (`/api/mcp-servers/<id>` — same
     call as `capability_check`). Assert the three keys are present
     in `config.env`. Values read back `[REDACTED]` for the
     KEY/BASE-shaped slots (`BYOK_BASE_URL`, `BYOK_API_KEY` —
     presentation-layer redaction via `redact_secrets` at
     `daemon/routers/mcp_servers.py`); presence is the signal. The
     marker shape at rest (`__KMS_ENV__` or `__KMS_REF__`) is NOT
     observable through this read lane — see Stage 4's
     `Read-back redaction reminder` for why.
  2. **Cheap OD round-trip** as the functional proof: call
     `od_list_projects` (the lightest tool endpoint). A 200 response
     with a JSON list = the seam is alive end-to-end (LANE-2 env-ref
     substitution, upstream auth, and the OD daemon all worked).
  3. On slot-presence miss OR auth/connection failure → **fall
     through to the full re-provision path** (self-heal — including
     a fresh `kms_request` → `kms_attach` cycle that mints and binds
     a new LANE-1 handle if one was previously bound and is now
     drained). The fast path is fast ONLY when both checks above
     pass.

The `odendesign` install uses ONLY LANE-2 env-ref markers for
BYOK_API_KEY today (the v1.3.0 contract), so the self-heal step
in (3) rarely fires for the BYOK seam. It is preserved here
verbatim because the LANE-1 lane still exists and other installs
(e.g. non-loopback OD with `OD_API_TOKEN`) use it.

## End-to-end verification — five stages (v1.2.0 preserved, adapted)

The success criterion for this skill, in order. Each stage produces
an EVIDENCE line; collect all five into the final `Result:` summary.
Any FAIL honest-stops with a `Result:` envelope (see Step 6).

> **Stage 4 expectations changed in v1.3.0:** the BYOK contract is
> now satisfied by `mcp_set_env` + `kms_attach` writes done by the
> skill itself (Stage 4 = "credentials readiness" reads them back).
> The marker-clobber trap (a later `configure-builtin` run drops
> env keys including markers) is documented as an explicit caveat
> below.

### Stage 1 — Daemon health 200

The OD daemon must be alive on its bound loopback port (default
`127.0.0.1:7456`). Two pieces of evidence are required:

```bash
# 1a. HTTP health from the daemon
curl -fsS --max-time 5 "${OD_DAEMON_URL:-http://127.0.0.1:7456}/api/health"
# expect {"ok":true,"version":"<semver>","packaged":false,...}
#
# 1b. systemd user unit alive (the durable supervision lane)
systemctl --user is-active opendesign-daemon.service
# expect: active
```

Failure modes:
- daemon unreachable → FAIL. The user must install the daemon first
  (source-build procedure in §"Daemon source-build install" below).
- daemon reachable but unit not active → FAIL. Report
  `journalctl --user -u opendesign-daemon.service -n 50`.
- daemon reachable AND unit active but `/api/health` returns non-200 →
  FAIL. Report the response body.

### Stage 2 — Seam registered

The opendesign MCP server row must exist in the daemon's MCP server
table, reachable via the daemon's HTTP API:

```bash
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-${PORT:-8079}}"
curl -fsS "$BASE/api/mcp-servers" | python3 -c \
  "import json,sys; rows=json.load(sys.stdin)['mcp_servers']; \
   row = next((r for r in rows if r['name']=='opendesign'), None); \
   assert row is not None, 'opendesign row not registered'; \
   assert row['is_active'], 'opendesign row is inactive'; \
   print(json.dumps({'id': row['id'], 'is_builtin': row['is_builtin'], \
                     'config_env_keys': sorted((row['config'].get('env') or {}).keys())}))"
```

Failure modes:
- HTTP unreachable → FAIL. This is the install path, not the daemon;
  if the daemon API is down, the seam cannot be set up.
- row missing → FAIL. Caller must run the install path (Step 1–4).
- `is_active=False` → FAIL. The seam row was deactivated (e.g. a
  `kms_attach` failure left it inactive). The install path's
  idempotent replay branch (per `daemon/routers/mcp_servers.py:567-580`)
  restores `is_active=True`.

### Stage 3 — Tool surface verified

The MCP server must publish its 10 `od_*` tools AND those tools must
be bound and callable from the ensemble side (not merely documented
in the system prompt).

**Verification — leader-bound lane (default on this host, verified
2026-10-02):** if the running agent has the `mcp_opendesign_*` tools
in its tool surface, call them directly:

```bash
# Probe 1 — list tool surface via JSON-RPC vs the proxy binary (most
# reliable lane while the runtime binding gap is open).
OD_DAEMON_URL="${OD_DAEMON_URL:-http://127.0.0.1:7456}" \
  open-design-mcp --stdio 2>/dev/null <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install-opendesign-v1.3.0","version":"1.3.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
EOF
# expect: serverInfo.open-design-mcp ≥ 0.16.1; tools/list → exactly 10 od_* tools
```

**Fallback lane (mcp_opendesign_* tool surface unbound):** a parallel
commission is fixing the binding defect where `mcp_opendesign_*` tools
appear in the system prompt inventory but are not bound on fresh
non-leader instances. **Do NOT hard-code a workaround for this
defect.** Until the binding fix lands, verify via the JSON-RPC lane
above (the proxy binary drives the real daemon seam). When the binding
fix lands, the verification shortens to:

```bash
# Probe 2 — call the seam directly (post-binding-fix).
# This call must succeed and return the SAME tool count as Probe 1.
# If it fails with capability_missing / tool_not_found, defer to
# Probe 1 — record the lane used in the Result: line.
```

The lane is recorded in the report so the operator can trace which
verification path the skill actually used.

### Stage 4 — Credentials readiness

The seam row's `config.env` must carry `BYOK_BASE_URL`, `BYOK_API_KEY`
(only as a marker — LANE-2 `__KMS_ENV__<VAR>__` env-ref (v1.3.0), or
legacy LANE-1 `__KMS_REF__<handle>__` on older installs — NEVER
plaintext),
`BYOK_MODEL`, and `OD_DAEMON_URL`. Read the row:

```bash
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-${PORT:-8079}}"
ROW_ID=$(curl -fsS "$BASE/api/mcp-servers" | python3 -c \
  "import json,sys; print(next(r['id'] for r in json.load(sys.stdin)['mcp_servers'] if r['name']=='opendesign'))")
curl -fsS "$BASE/api/mcp-servers/$ROW_ID" | python3 -c \
  "import json,sys; r=json.load(sys.stdin); env=r['config'].get('env') or {}; \
   print(json.dumps({'OD_DAEMON_URL': env.get('OD_DAEMON_URL'), \
                     'BYOK_BASE_URL': env.get('BYOK_BASE_URL'), \
                     'BYOK_MODEL': env.get('BYOK_MODEL'), \
                     'BYOK_API_KEY': env.get('BYOK_API_KEY')}))"
```

Per-field checks:

| Field        | Required | Form                                                  |
|--------------|----------|-------------------------------------------------------|
| `OD_DAEMON_URL` | yes  | plaintext URL                                          |
| `BYOK_BASE_URL` | yes  | plaintext URL (reads back `[REDACTED]`)                |
| `BYOK_MODEL`    | yes  | plaintext model identifier                             |
| `BYOK_API_KEY`  | yes  | marker at rest — LANE-2 `__KMS_ENV__<VAR>__` env-ref (v1.3.0) or legacy LANE-1 `__KMS_REF__<HANDLE>__` — never plaintext; reads back `[REDACTED]` (see the redaction reminder below) |

If ANY field is missing or malformed, the skill must COLLECT/GUIDE — not
fail silently. The skill reports EXACTLY which fields are missing and
the EXACT provisioning command (from the install path's Step 3 below).
`BYOK_API_KEY` must be provisioned via `kms_attach(env_source=...)`
(the `__KMS_ENV__<VAR>__` env-ref marker) — plaintext is FORBIDDEN
anywhere in the stored config, in logs, in tool-results, in checkpoints
(P3-WP10 redaction covers KMS-registered plaintexts only — see
§Fences; LANE-2 env-ref markers carry no secret material at rest, so
the redaction filter is unnecessary on this lane).

**Marker-clobber trap (documented, v1.3.0 explicit caveat):** a later
`configure-builtin` run regenerates the row's `config` from the
builtin schema payload and can drop env keys — including BOTH
`__KMS_REF__<HANDLE>__` (LANE-1) AND `__KMS_ENV__<VAR>__` (LANE-2)
markers. Agent-lane writers must re-apply env (and re-attach
handles / re-bind env-refs) after any reconfigure. The HTTP lane's
install-audit and idempotency rails protect the HTTP lane; the
agent lane owns its own re-apply.

**Read-back redaction reminder (C2, corrected v1.3.0 fix pass):**
BOTH `BYOK_BASE_URL` AND `BYOK_API_KEY` read back `[REDACTED]`
through the HTTP API (presentation-layer redaction — the
`redact_secrets` helper at `daemon/routers/mcp_servers.py`
`[REDACTED]`s ANY secret-shaped env name, `*KEY*` included). The
marker shape at rest is therefore NOT observable through this read
lane, and a `bound_handles` count over the API is not available as
evidence either (`McpServerInfo` carries no `instance_metadata` —
that read lane does not exist). Stage-4 verification evidence is
exactly: (1) the `BYOK_API_KEY` slot is NON-EMPTY in the read-back
(its value shows `[REDACTED]`), (2) the `kms_attach` call earlier in
this flow returned success (the tool result echoed the marker —
names only, never values), and (3) the Stage 5 smoke round-trip
passes. (Older installs may carry a LANE-1 `__KMS_REF__<HANDLE>__`
marker at rest in this slot — a valid back-compat shape; same
evidence applies.)

### Stage 5 — `od_generate_design` end-to-end smoke

A real generation call against the daemon with the BYOK seam
populated. The v1.3.0 env-ref bridge resolves `BYOK_API_KEY` from
`os.environ["OPENAI_API_KEY"]` at spawn time — the upstream
should authenticate and return a real HTML payload. Stage 5 PASS
criterion is the same regardless of which marker lane carried the
seam (LANE-2 env-ref for `BYOK_API_KEY`, LANE-1 KMS marker for
`OD_API_TOKEN` if the non-loopback case is exercised):

```bash
OD_DAEMON_URL="${OD_DAEMON_URL:-http://127.0.0.1:7456}" \
  open-design-mcp --stdio 2>/dev/null <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install-opendesign-v1.3.0-smoke","version":"1.3.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"od_generate_design","arguments":{"projectId":"install-opendesign-v1.3.0-smoke","prompt":"Render a small hero section with one primary button.","format":"html"}}}
EOF
# expect: a JSON result whose content[].text carries a non-empty HTML payload.
```

**Smoke PASS criteria:**

- The `od_generate_design` call returns with no error.
- The response's content array carries at least one entry whose text
  contains `<html` (or a sensible HTML fragment opener — `<div`,
  `<section`, `<button` are acceptable evidence of a real generation).
- HTTP health did not degrade; daemon's `/api/health` still `ok:true`.

**Smoke FAIL — report the exact error verbatim:**

- HTTP 401/403 / `"invalid API key"` from the upstream byok →
  genuine configuration error. The most likely cause is that
  `OPENAI_API_KEY` is unset in the daemon's process env (an
  eager `ERROR: ENV_VAR_NOT_FOUND` would have surfaced at attach
  time too). Verify via a NON-ECHOING presence check on the
  daemon's `ps` env — COUNT ONLY, never the value (PB-F1: a
  value echoed into a bash tool-result lands in checkpoints):
  `tr '\0' '\n' < /proc/<pid>/environ | grep -c '^OPENAI_API_KEY='`
  → `1` means present (name-only evidence; a value is never
  printed), `0` means unset. If the var
  is set but still 401s, the issue is upstream-side (key revoked,
  wrong account, quota exhausted) — diagnose via the upstream
  portal, not via this skill.
- `"BYOK not configured: missing BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL"`
  → Stage 4 not met; honest-stop, NOT a skill failure.
- Tool timeout or daemon-side error → FAIL. Report the JSON-RPC error
  verbatim; do not retry blindly.

### Verification summary line

All five stages PASS → emit ONE summary:

```
Result: opendesign capability present — 5/5 verification stages PASS
  Stage 1 (daemon health):       PASS (version=0.23.1, unit=active)
  Stage 2 (seam registered):     PASS (row id=…, is_active=true)
  Stage 3 (tool surface):        PASS (10 od_* tools via <lane>)
  Stage 4 (credentials):         PASS (4/4 fields populated; BYOK_API_KEY marker)
  Stage 5 (generate smoke):      PASS (HTML payload produced, N bytes)
```

Any FAIL → emit the `installed_but_unconfigured` envelope (Step 6)
with the failed stage's `detection_evidence`.

## Step 0 — Pre-flight (mandatory first instruction, already above)

`capability_check("opendesign")` — tri-state:

- `present` → already installed AND configured. Run the five-stage
  end-to-end verification (§"End-to-end verification — five stages")
  — fast path on a fully-working host, no mutation. PASS →
  summary line; FAIL → install path from Step 1.
- `unconfigured` → installed but a secret/env binding is missing.
  Jump to Step 3 (KMS bind) — and check Stage 4's credentials
  contract.
- `missing` → not installed. Continue with Step 1.

## Step 1 — Resolve the daemon API base

The daemon HTTP API lives on loopback at the port the daemon booted
with (default 8079; live 9797; demo 7979):

```bash
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-${PORT:-8079}}"
curl -fsS "$BASE/api/mcp-servers" >/dev/null && echo "api reachable"
```

NOTE (route reality, verified 2026-09-26): the routes are
`/api/mcp-servers/...` — hyphenated, NO `/v1` segment. Earlier plan
text citing `/api/v1/mcp_servers/...` is stale; use the hyphenated
form. If the API is unreachable, STOP and escalate (Step 6) with
`detection_evidence="daemon API unreachable at $BASE"`.

NOTE (env-key precedence for the daemon port, verified 2026-10-02):
the launcher (`launcher.sh::load_env_file`) loads the daemon
sees whatever env vars the live install's `.env` exports — on the
live install `PORT=9797` (not `ENSEMBLE_PORT`). The fallback
expression `${ENSEMBLE_PORT:-${PORT:-8079}}` resolves correctly on
both the dev lane (8079 default) and the live install (PORT=9797).

## Step 2 — Configure the builtin server (create or update)

`open-design-mcp` is a stdio MCP server (10 `od_*` tools). The
builtin definition supplies day-1 loopback defaults
(`OD_DAEMON_URL` default; `OD_API_TOKEN` empty for loopback); the
v1.3.0 self-provisioning flow does NOT pass secrets through this
call — BYOK values come from `mcp_set_env` + `kms_attach` in
Step 3, never from `configure-builtin`. The minimal install is
still the empty-values call:

```bash
curl -fsS -X POST "$BASE/api/mcp-servers/configure-builtin" \
  -H 'Content-Type: application/json' \
  -d '{"template_name": "opendesign", "values": {}}'
```

The route is idempotent: re-running with the same values returns
the same row (an `install_idempotency_key` in the row's
`instance_metadata` guards against duplicate inserts). A re-run
with DIFFERENT values updates the row and the daemon snapshots the
previous config (`prev_config_snapshot`, 7-day TTL).

If the daemon refuses with a schema-version mismatch (HTTP 409),
DO NOT retry blindly — the row's config schema drifted from the
definition. Escalate (Step 6) with the refusal body as
`detection_evidence`.

**CAUTION (v1.3.0 explicit, marker-clobber trap):** a later
`configure-builtin` run regenerates `config` from the builtin
schema payload and can DROP env keys written by `mcp_set_env`
(including KMS markers). After every `configure-builtin` call,
the skill MUST re-apply `mcp_set_env` (Step 3) and re-attach
handles (`kms_attach`) — the markers are not idempotent across
reconfigure. This is why the v1.3.0 install path runs
`configure-builtin` FIRST and the marker writes SECOND: a
subsequent `configure-builtin` (e.g. by a different installer
or by an admin) drops the markers and the skill must re-run.

## Step 3 — BYOK self-provisioning via agent tools (v1.3.0 NEW)

This step is the v1.3.0 self-provisioning flow. It is the
replacement of v1.2's "BYOK values are populated by a separate
self-provisioning follow-up commission" placeholder — that
follow-up landed on this branch (`bb465174` for `ens_env_read`,
`d4af89b3` for `mcp_set_env`). The flow uses ONLY agent tools
(no curl, no shell-out, no plaintext at rest):

### 3a — Capability probe (mandatory first; honest-stop on absence)

Before ANY provisioning call, verify the four tools the flow uses
are present in the running agent's tool surface. The agent may
not have all four opt-in categories loaded (`infra` and `ens-env`
must be in the agent's `tools.allow`; F1a/b semantics may
suppress empty allowlists).

Probe mechanism (per-call inspection, no daemon probe — the
probe IS a tool call and the response IS the evidence):

- Call `ens_env_read(keys=["OPENAI_BASE_URL"])` with the
  explicit-keys form. The function exists → return is a JSON
  payload (`{"OPENAI_BASE_URL": "...", ...}`). The function is
  absent or refused → the LLM surfaces a clear "tool not
  available" error which the skill catches and reports.
- Same pattern for `mcp_set_env`, `kms_request`, and
  `kms_lookup_handle` (each is a real `tool`-decorated function;
  absence surfaces as a tool-call error).

If ANY of the four is absent → **HONEST-STOP** with:

```
Result: {"kind": "capability_missing", "capability": "opendesign",
          "installer_skill": "install-opendesign",
          "detection_evidence": "self_provisioning_tools_absent:
            ens_env_read/mcp_set_env/kms_request/kms_lookup_handle
            not in agent tool surface; daemon version must include
            Stage-1 of <feature/od-self-provisioning>; run the
            ensemble promote ceremony to ship the latest",
          "blocker_scope": "this_task", "resume_hint": null,
          "policy_denied_reason": null, "ts": "<ISO-8601>"}
```

Do NOT attempt the install path with raw HTTP — the whole point
of the agent-tool flow is that the LIVE install's `.env` keys
never cross the tool boundary in plaintext (PB-F1).

### 3b — Read live env via `ens_env_read`

Call `ens_env_read(keys=["OPENAI_BASE_URL", "OPENAI_MODEL_VISION"])`
(explicit-keys form; W3 fix pass 2026-10-02). The env-ref lane needs
only the var NAME of the API key (passed as
`env_source="OPENAI_API_KEY"` to `kms_attach` in Step 3d) — the
provisioning flow must NEVER read the API key VALUE at all, so the
key is deliberately absent from the request set. The result is the
canonical mapping-table payload for the two requested keys — see
§"BYOK mapping" above.

Audited-only response handling: the result JSON carries the
values; the skill extracts the value into a working variable
WITHOUT echoing it to logs, the report, the user, or any
checkpoint payload. The only audit signal is key NAMES.

If the response carries `{"error": "ens_env_read failed:
<ExceptionClassName>"}` → the tool's environment read failed
(no class-name leak to logs). **HONEST-STOP** with
`detection_evidence="ens_env_read_failed:<class>"` — the
daemon may have lost access to its `.env`, which is a daemon-side
concern.

Check `_missing`: `OPENAI_BASE_URL` listed in `_missing` is a real
blocker (BYOK_BASE_URL cannot be derived from an empty value). Emit
`BLOCKED-ON-CREDENTIALS` with
`detection_evidence="missing_required_env_keys:<keys>"`.
(`OPENAI_API_KEY` absence is a blocker too, but it is detected at
Step 3d attach time as the tool's eager
`ERROR: ENV_VAR_NOT_FOUND` — the flow treats that as the same
`BLOCKED-ON-CREDENTIALS` outcome; it is never read via this tool.)

For `OPENAI_MODEL_VISION`: if absent/empty, fall back to
literal `"vision"` per the precedence rule in §"BYOK mapping".
The meaning of the fallback is documented in §"BYOK mapping";
do not substitute `OPENAI_MODEL` (which is the chat model,
not the vision model).

### 3c — Write `BYOK_BASE_URL` + `BYOK_MODEL` via `mcp_set_env`

Call `mcp_set_env(server="opendesign", env={"BYOK_BASE_URL":
<value>, "BYOK_MODEL": <value>})`.

Important behavioral notes:

- The tool accepts server NAME (resolves first) or server ID
  (id fallback). The first call uses the name `"opendesign"`.
- The tool MERGES env into existing `config.env` — never
  replaces. `__KMS_REF__` and `__KMS_ENV__` markers written by
  `kms_attach` are preserved.
- The tool REJECTS secret-shaped KEY NAMES (any key whose name
  has KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/PRIVATE/PWD/AUTH as a
  substring — the shared policy at `daemon/services/
  env_key_policy.py`), non-ASCII key names
  (`ERROR: INVALID_ENV_KEY`), and marker-shaped VALUES
  (`ERROR: MARKER_VALUE_FORBIDDEN` — markers go through
  `kms_attach` only). It returns
  `ERROR: SECRET_SHAPED_KEY` for `BYOK_API_KEY` — that is the
  EXPECTED response if you accidentally try it; `BYOK_API_KEY`
  goes through `kms_attach`, NOT this write.
- The success result is `{"ok": True, "server_id": "<uuid>",
  "server_name": "opendesign", "env_keys_set":
  ["BYOK_BASE_URL", "BYOK_MODEL"], "note": "..."}`. Echo the
  `server_id` (NOT the values) into the working variable for
  the chained `kms_attach` call. The note mentions reconnect
  semantics — open MCP sessions keep the OLD env until they
  reconnect (close_server or fresh instance spawn); this is
  expected for a fresh install where no session exists yet.
- Invalidates the MCP schema cache so the next preload rediscovers
  config — fresh instance loads, sees the new env.

For `BYOK_PROVIDER` (optional): derive from `BYOK_BASE_URL`
(`"openai"` if the URL contains `/v1` — the canonical
OpenAI-compatible proxy shape the live install uses); default
to `"openai"` for the user's `llm-supervisor-proxy`. Write
via the SAME `mcp_set_env` call (multi-key merge is one
round-trip). If the schema refuses `BYOK_PROVIDER`, drop it
silently (the field is optional; `od_generate_design` works
without it).

### 3d — Attach `BYOK_API_KEY` via `kms_attach(env_source=...)` (v1.3.0 env-ref lane)

The BYOK_API_KEY env-ref lane. The minted-handle lane
(`kms_request` → `kms_attach(handle=...)`) is documented in
§"BYOK_API_KEY — env-ref attach" above and remains the right tool
for self-minted opaque credentials (e.g. `OD_API_TOKEN` on a
non-loopback OD install). For `BYOK_API_KEY` the env-ref mode
reuses the existing daemon env without minting new key material
(per user directive 2026-10-02).

Call sequence:

```
kms_attach(server_id=<id from 3c>, env_key="BYOK_API_KEY",
            env_source="OPENAI_API_KEY")
# → {"server_id": ..., "env_key": "BYOK_API_KEY",
#    "env_source": "OPENAI_API_KEY",
#    "marker": "__KMS_ENV__OPENAI_API_KEY__"}
```

The tool accepts exactly one of `handle` / `env_source` (passing
both or neither returns `ERROR: INVALID_ARGUMENT` — read the
kms_attach full doc at `infra.py` for the contract). For
env-ref mode:

- The marker `__KMS_ENV__OPENAI_API_KEY__` is written into
  `config.env["BYOK_API_KEY"]`. Existing keys + any prior
  `__KMS_REF__` markers (LANE-1, from a prior install run) are
  preserved by the read-fresh→merge→write pattern (R1 invariant,
  same as `mcp_set_env`). ONE deliberate exception (M5 note): a
  LANE-2 attach targeting a slot that already holds a v1.2-era
  LANE-1 `__KMS_REF__` marker SILENTLY OVERWRITES that slot's
  marker (same env_key → the new marker replaces the old) — an
  intentional upgrade path (the restart-durable env-ref binding
  supersedes the restart-drained KMS-Lite handle); the stale
  `bound_handles` entry remains until re-attach/cleanup and is
  harmless.
- The binding entry `{env_key: "BYOK_API_KEY", env_source:
  "OPENAI_API_KEY", actor: <current_instance_id>}` is appended
  to `instance_metadata.env_refs` (new list, mirroring
  `bound_handles`'s audit-substrate semantics). Idempotent:
  same `(env_source, env_key)` tuple collapses — the existing
  binding is removed and re-appended with the fresh actor
  (the binding collapse path mirrors `kms_attach` LANE-1 at
  `infra.py:1033`).
- The success result echoes `server_id`, `env_key`,
  `env_source`, and `marker` only — NEVER a value. The
  plaintext `OPENAI_API_KEY` is not read by the tool (presence
  check only, via `os.environ` membership); the resolver at
  `daemon/services/kms_resolver.py` substitutes it at spawn
  time.

Failure modes (typed errors, no value echo):

- `ERROR: ENV_VAR_NOT_FOUND` — the named var is absent from the
  daemon's `os.environ`. The var NAME is in the error; the
  value (if it had been present) is never read. Verify the
  daemon's `ps` env or `.env`, set the var, and retry.
- `ERROR: INVALID_ARGUMENT` — `env_source` is not a valid env-var
  name (the validator at `kms_lite.build_env_marker` — must match
  `[A-Za-z_][A-Za-z0-9_]*`). Common typos: hyphens, leading
  digits.
- `ERROR: INVALID_ARGUMENT` — both `handle` AND `env_source`
  passed (or neither). The tool does NOT route ambiguity to the
  wrong lane silently.
- `ERROR: SERVER_NOT_FOUND` — `server_id` does not resolve.

**Do NOT fall back to plaintext** if env-ref fails. The
fall-back would cross the same PB-F1 boundary the leader
policy exists to prevent. Honest-stop with the typed error and
let the operator diagnose.

### 3e — Verify the seam

The install path's Step 4 below runs the integrated §"End-to-end
verification — five stages" — what was historically a single
endpoint ping is now the full contract.

## Step 4 — Verify the install

Run the five-stage end-to-end verification from §"End-to-end
verification — five stages" (this is the integrated check — what
was historically a single endpoint ping is now the full contract).

DAEMON AVAILABILITY (updated v1.1.0, proven 2026-10-02 on the ensemble
host): the OD daemon is installable natively on Linux WITHOUT Docker via
the source-build procedure in §"Daemon source-build install" below. If
the daemon is not yet installed on this host, run that procedure FIRST
(or verify it is running: `curl -fsS http://127.0.0.1:7456/api/health`),
then return here. The MCP server starts and lists tools even without
the daemon; actual design operations require the daemon at
`OD_DAEMON_URL` — report the seam state honestly either way.

## Step 5 — Post-install self-check (body-final; closes the bootstrap loop)

`capability_check("opendesign")` — MUST now return `present`. If it
does not, treat the install as failed per the tri-state you got:

- `unconfigured` → a binding is missing (Step 3 skipped or failed) →
  envelope `kind="installed_but_unconfigured"`.
- `missing` → the configure call did not land → envelope
  `kind="capability_missing"` with the HTTP evidence.

Run the five-stage end-to-end verification one more time. Stages
1-4 PASS → the BYOK seam is wired correctly. Stage 5 should PASS
on a healthy install — the env-ref bridge carries the live
`OPENAI_API_KEY` value to the upstream byok, and
`od_generate_design` returns a real HTML payload. Any FAIL → emit
the envelope with the failed stage's `detection_evidence` (see
§"End-to-end verification — five stages" for the per-stage
evidence shape).

## Step 6 — Report via the child-report lane

Emit ONE `Result:`-prefixed escalation envelope (schema in
`daemon/services/capability_resolver.py`, `EscalationEnvelope`):

```
Result: {"kind": "...", "capability": "opendesign",
          "installer_skill": "install-opendesign",
          "detection_evidence": "...", "blocker_scope": "this_task",
          "resume_hint": "...", "policy_denied_reason": null,
          "ts": "<ISO-8601>"}
```

Day-1 kinds you may emit: `capability_missing` (install did not land;
escalate to spawn/retry the installer), `installed_but_unconfigured`
(installed; a binding is missing — re-attach env-ref or mint+attach
and resume). `policy_denied` is schema-only and MUST NOT be emitted
(no policy layer day-1). `blocked_on_credentials` is preserved in the
envelope schema for back-compat (older skill versions emitted it for
the v1.3.0 bridging gap); the v1.3.0-bridge install does NOT emit it
because the bridge now lands. On FULL success report the five-stage
verification summary line — the designer reads the capability state
plus the verification trail, not just the envelope.
capability state plus the verification trail, not just the envelope.

The daemon writes the §7.4 audit line
(`install-audit.jsonl`: `ts/event/name/actor/parent/secret_ref/idempotency_key/trace_id`)
server-side per configure-builtin mutation; your envelope is the
mission-side record. `secret_ref` in the audit lane is the KMS HANDLE
only — never a secret value.

## Fences (preserved verbatim from v1.1.0 / v1.2.0)

- **NO Docker** on the install host (no Docker, no Docker
  Compose, no Docker socket). The OD daemon source-build procedure
  is user-space only.
- **NO apt / system packages**. No `sudo apt-get install`. No
  system Node touch. Every native dep resolves via prebuilt
  binaries (verified for `better-sqlite3` and the daemon's other
  deps) — if a future dep forces source compile without `make`,
  STOP and report; do NOT apt-install.
- **No plaintext secrets anywhere** — files, reports, command
  echoes, tool-results, checkpoints. The KMS-Lite marker is the
  ONLY secret-shaped thing that may appear in the stored config.
- **No env-var value in any report** — the audit trail logs key
  NAMES only. The skill body, the `Result:` envelope, the
  install report, the journal entries, and the user-visible
  message all carry key names; the values stay in the tool
  surface and the (in-memory) KMS-Lite store.
- **HONEST-STOP on hard blockers** — if a precondition is missing
  (daemon source-build prerequisites, dev lane daemon unreachable,
  the four self-provisioning tools absent), the skill emits the
  envelope and STOPS. No flailing, no retries, no fallback to
  plaintext.
- **Idempotent** — re-running on a fully-working host is a
  verify-only fast path; no install call, no KMS call, no row
  write.

## Daemon source-build install (Linux, user-space, no Docker)

Proven procedure (executed 2026-10-02, ensemble host, Ubuntu 24.04;
daemon 0.23.1 @ HEAD 53231d40; lineage d813a652→50b89fdb→9eee753d→
003ab835→this skill). FENCES: no Docker, no apt/system packages, no
system Node touch, nothing destructive — user-space only.

### Preconditions (check FIRST — a miss here is persistent-failure → STOP)

```bash
python3 --version        # needed only by node-gyp fallbacks
gcc --version | head -1  # INFORMATIONAL ONLY — the compile fallback cannot run on this host class (make is absent AND fenced); recorded for triage
# `make` is NOT required on this path: every native dep resolves via
# prebuilt binaries (verified — see notes below). If a future dep
# forces source compile without make, STOP and report; do NOT apt-install.
curl -sI https://github.com | head -1   # network (clone + prebuilds)
```

Native-dep facts (verify per-release, they drift):
- `better-sqlite3` (allowlisted in upstream `pnpm-workspace.yaml`
  `onlyBuiltDependencies`) downloads a prebuilt binding — needs a
  GitHub-release asset matching the Node ABI (Node 24 = v137,
  e.g. `better-sqlite3-v12.10.0-node-v137-linux-x64.tar.gz`).
- `node-pty` is NOT allowlisted → pnpm 10 SKIPS its build script; the
  daemon models this (`loadNodePty()` → `NodePtyUnavailableError`).
  DEGRADED FEATURE: interactive terminals. Everything else works.
  (node-pty ships no linux-x64 prebuilds in any 1.x — 1.2.0-beta
  included; compile needs make.)

### Steps

```bash
# 1. User-local Node 24 via nvm (system Node stays untouched)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install 24            # ~v24.21.0, ABI 137
NVM_NODE="$(nvm which 24)"  # absolute node path — fresh hosts resolve a DIFFERENT v24.x; use for ExecStart below
# 2. pnpm via corepack — MUST follow nvm sourcing (ordering is load-bearing):
#    host /usr/bin/corepack and /usr/bin/pnpm are not user-writable (EACCES),
#    so 'corepack enable' only succeeds INSIDE the nvm-owned bin dir (shims
#    land there, user-owned).
corepack enable && corepack prepare pnpm@10.33.2 --activate   # upstream pins this in packageManager
# 3. Clone (user-space; pick your dir)
git clone --depth 1 https://github.com/nexu-io/open-design.git ~/opt/open-design
cd ~/opt/open-design
# 4. Install daemon dependency closure.
#    EXPECT the root postinstall to FAIL late at packages/dsh-runtime:
#    its esbuild devDep is absent outside the daemon closure. Observed
#    2026-10-02: the root postinstall runs EVEN under this filtered
#    form (root '. postinstall:' output in transcript) — a plain
#    unfiltered 'pnpm install' triggers the same late failure plus a
#    much larger download. Harmless for the daemon either way; do not
#    chase it. State check: node_modules present +
#    apps/daemon/node_modules/better-sqlite3/build/Release/*.node exists.
pnpm install --filter "@open-design/daemon..."
# 5. Build: workspace deps first (upstream pretest selector), then daemon
pnpm --filter "@open-design/daemon^..." --workspace-concurrency=4 --if-present run build
pnpm --filter "@open-design/daemon" run build   # → apps/daemon/dist/cli.js
```

### Upgrade (tree is pinned — e.g. 0.23.1 @ 53231d40; upstream already tags v0.24.1)

```bash
cd ~/opt/open-design
git fetch --depth 1 origin tag <target-tag>    # shallow clone: fetch only the tag you want
git checkout <target-tag>
# repeat Steps 4–5 verbatim (install --filter + both build commands;
# same expected dsh-runtime postinstall noise; re-check the
# better-sqlite3 prebuild note for the new version/ABI)
systemctl --user restart opendesign-daemon.service
curl -fsS http://127.0.0.1:7456/api/health     # expect {"ok":true,"version":"<target>"}
```


### Durable run (systemd USER unit + linger)

The daemon binds 127.0.0.1:7456 (`OD_PORT`/`OD_BIND_HOST` env or
`--port/--host`) and stores embedded sqlite at `./.od/` RELATIVE TO
CWD — WorkingDirectory is load-bearing. Unit
`~/.config/systemd/user/opendesign-daemon.service`:

```ini
[Unit]
Description=OpenDesign daemon (source build)
After=network.target
[Service]
Type=simple
WorkingDirectory=/home/<user>/opt/open-design
# <nvm-node> = absolute path resolved at install time via: nvm which 24
# (e.g. /home/<user>/.nvm/versions/node/v24.21.0/bin/node — differs per
# host and per Node upgrade; NEVER hardcode a specific v24.x here)
ExecStart=<nvm-node> apps/daemon/dist/cli.js --no-open
Environment=OD_PORT=7456
Environment=OD_BIND_HOST=127.0.0.1
Environment=HOME=/home/<user>
Environment=PATH=<dir-of-nvm-node>:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
```

```bash
loginctl enable-linger <user>    # user manager starts at BOOT w/o login
export XDG_RUNTIME_DIR=/run/user/$(id -u)   # detached shells need this
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemctl --user daemon-reload && systemctl --user enable --now opendesign-daemon.service
curl -fsS http://127.0.0.1:7456/api/health   # {"ok":true,"version":"..."}
```

Boot-persistence: linger starts user@<uid>.service at boot; the
default.target.wants symlink starts the unit. Ensemble restarts never
touch it (separate supervision tree). Crash: Restart=on-failure.

### Verify (all five, evidence not claims)

1. `GET /api/health` → `{"ok":true,...}` + `systemctl --user is-active`.
   Respawn proof: `kill -9 $(systemctl --user show
   opendesign-daemon.service -p MainPID --value)` → ~10s later a NEW
   MainPID serves `ok:true` (Restart=on-failure). Failure branch:
   `journalctl --user -u opendesign-daemon.service -n 50` (detached
   shells need the XDG_RUNTIME_DIR export from above).
2. Spawn leg through ensemble: POST the row config to
   `<ensemble>/api/mcp-servers/test-connection` → `tools_count: 10`.
3. Proxy↔daemon leg: drive the SAME proxy binary over stdio JSON-RPC
   (initialize → tools/call `od_list_projects`) with
   `OD_DAEMON_URL=http://127.0.0.1:7456` → real daemon response.
   Full CRUD round-trip (create→list→get→delete→list-empty) is the
   strong form; pace requests (sleep between writes) — the proxy
   handles calls concurrently and responses may race.
4. `od_generate_design` without creds → expect
   `BYOK not configured: missing BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL`
   — that exact error is the CORRECT day-1 outcome. BYOK vars are read
   by the PROXY (`open-design-mcp/dist/src/config.js`), so they are
   provisioned in the MCP row's `config.env` (BYOK_API_KEY via KMS
   attach — never plaintext), separate user confirmation.
5. Run the packaged `opendesign-verify` skill → its checklist (pre-flight
   `present`, 10-tool surface report, `Result:` line) must PASS — it is
   the consumer half of this bootstrap loop and part of the real
   verification.

### Reversal

```bash
systemctl --user disable --now opendesign-daemon.service
rm ~/.config/systemd/user/opendesign-daemon.service && systemctl --user daemon-reload
loginctl disable-linger <user>          # if no other user units need it
rm -rf ~/opt/open-design                # includes .od/ data
rm -rf ~/.nvm                           # if nothing else uses nvm
corepack shims live inside ~/.nvm — removed with it
```

## [resume] convention — canonical spec (WP11)

This skill RATIFIES the `[resume]` convention for re-entering an
interrupted install via `job_continue(old_job_id, message)`. The
resume message carries a `[resume]`-tagged JSON block (mirrors the
`Result:` prefix convention):

```
[resume] {"capability_id": "opendesign", "status": "installed_but_unconfigured",
          "tools_now_available": ["kms_request", "kms_attach"],
          "resume_from": "step_after_kms_bind"}
```

Field contract (all four REQUIRED):

- `capability_id` — the capability being resumed (e.g. `opendesign`).
- `status` — the tri-state or envelope kind that triggered the resume
  (`missing` | `unconfigured` | `capability_missing` |
  `installed_but_unconfigured` | `blocked_on_credentials`).
- `tools_now_available` — tool names the resumer can now use (e.g. a
  newly-granted `infra` or `ens-env` category).
- `resume_from` — the step label to continue from
  (`step_after_preflight` | `step_after_kms_bind` | ...) NO state
  replay: re-run the pre-flight and jump straight to the labeled step.

Consumer contract (ANY worker skill consuming a resume):

1. Parse the `[resume]` block (structured parse lives in
   `daemon/services/capability_resolver.py::parse_resume_message`).
2. Re-run `capability_check(<capability_id>)` — never trust the stale
   status; the check is the single source of truth.
3. Continue at `resume_from`. Honoring `resume_from` is REQUIRED —
   re-running already-completed steps (e.g. minting a SECOND handle)
   is a contract violation.
4. A malformed resume block (unparseable JSON, missing fields) is an
   ESCALATION, not a crash: emit
   `Result: {"kind": "capability_missing", ...,
   "detection_evidence": "malformed [resume] block: <reason>"}` and
   stop. `job_continue` itself carries no resume logic — the
   convention lives entirely in skill bodies and this parser
   (documented-convention ratification; no code enforcement in
   `job_continue`).

## Changelog

### v1.3.0-bridge — env-ref attach for `BYOK_API_KEY` (2026-10-02, pre-release completion, no version bump)

The bridging gap (v1.3.0's "Known gap — `BYOK_API_KEY` does NOT carry
`OPENAI_API_KEY` plaintext") is resolved in-place. The skill version
stays at `1.3.0` because this is pre-release completion of the
1.3.0 contract, not a new feature surface.

**Added (NEW lane documented):**

- `## BYOK_API_KEY — env-ref attach` — the bridge section replacing
  the "Known gap" section. Documents the new
  `kms_attach(env_source=...)` mode (LANE-2 env-ref) for the
  `BYOK_API_KEY` slot. The minted-handle lane (`kms_request` →
  `kms_attach(handle=...)`, LANE-1) is preserved for its original
  purpose (per-credential minting for `OD_API_TOKEN` etc.) but no
  longer used for `BYOK_API_KEY`. Eager `ERROR: ENV_VAR_NOT_FOUND`
  at attach time catches misconfiguration immediately. Restart-
  durable (the binding is the var name; the resolver reads
  `os.environ[<VAR>]` fresh on every spawn).

**Changed:**

- `## BYOK mapping` — `BYOK_API_KEY` row mechanism changed from
  "kms_request → kms_attach (MARKER)" to "kms_attach(env_source=...)
  (ENV-REF) — see §'BYOK_API_KEY — env-ref attach'".
- `## Fast-path KMS-Lite restart-drain check` — clarified to a
  "restart-drain check" covering BOTH lanes: LANE-2 env-ref markers
  are restart-DURABLE (no self-heal needed); LANE-1 KMS-Lite
  handles are restart-fragile and the existing self-heal still
  applies for non-BYOK_API_KEY slots.
- `## Step 3 — BYOK self-provisioning via agent tools` — Step 3d
  rewritten: `kms_attach(env_source=...)` replaces
  `kms_request` → `kms_attach(handle=...)` for `BYOK_API_KEY`. The
  `kms_request` + `kms_attach(handle=...)` flow is documented as
  the right tool for non-loopback `OD_API_TOKEN` minting.
- `## Stage 5 — od_generate_design smoke` — the
  "expected HTTP 401/403 invalid API key" split is REMOVED. v1.3.0
  is the bridged flow; a 401/403 is now a genuine configuration
  error (most often `OPENAI_API_KEY` not set in the daemon env).
  The follow-up commission (env-passthrough handle,
  `kms_register`, or `mcp_set_env_secret`) is RESOLVED.
- `## Step 5 — Post-install self-check` — the BLOCKED-ON-CREDENTIALS
  fallback for the bridging gap is removed; Stages 1-5 must all PASS
  for a healthy install.
- `## Verification summary line` — the "Stages 1-4 PASS / Stage 5
  expected to fail" split is removed.
- `## Step 6 — Report` — `blocked_on_credentials` is preserved in the
  envelope schema for back-compat but the v1.3.0-bridge install does
  not emit it.
- `## Step 3c` — marker-preservation note updated from
  "`__KMS_REF__` markers preserved" to "`__KMS_REF__` and
  `__KMS_ENV__` markers preserved" — both lanes are co-owned.

**Removed:**

- `## Known gap — BYOK_API_KEY does NOT carry OPENAI_API_KEY
  plaintext` — the bridging gap section. The bridging is RESOLVED.
  The follow-up commission (`kms_register` ingest, env-passthrough
  handle, or `mcp_set_env_secret`) is owed to this branch and is
  merged.

**Preserved (v1.3.0):**

- `## BYOK mapping` precedence rule (`OPENAI_MODEL_VISION` concrete
  wins else literal `"vision"`).
- PB-F1 discipline (no plaintext in any report, log, tool-result, or
  checkpoint; `ens_env_read` values stay in the LLM's working
  context only).
- The full `## End-to-end verification — five stages` framework —
  only Stage 5's PASS criteria changed.
- The full `## Idempotency contract` — the verify-only fast path is
  unchanged.
- The marker-clobber trap caveat (a later `configure-builtin` run
  drops env keys including markers — applies to BOTH lanes).
- All fences intact: no Docker, no apt/system packages, no plaintext
  secrets anywhere, no env-var values in any report.
- The full `## Daemon source-build install (Linux, user-space, no
  Docker)` section verbatim (no changes — the self-install
  procedure for the OD daemon itself).

### v1.3.0 — Self-provisioning via agent tools (2026-10-02)

**Added (NEW sections):**

- `## BYOK mapping` — the verified mapping table (BYOK_BASE_URL ←
  OPENAI_BASE_URL; BYOK_MODEL ← OPENAI_MODEL_VISION concrete else
  literal "vision"; BYOK_API_KEY ← KMS marker from OPENAI_API_KEY
  via the v1.3.0 tool chain). Precedence rule documented.
  PB-F1 discipline restated: values are never echoed to logs /
  tool-results / reports / checkpoints.
- `## Known gap — BYOK_API_KEY does NOT carry OPENAI_API_KEY
  plaintext` — the canonical failure mode for the v1.3.0 Stage 5
  smoke (upstream 401/403). Names the bridging commission owed
  (`kms_register` ingest, env-passthrough handle, or
  `mcp_set_env_secret` lane). Critical paragraph — do not omit.
- `## Fast-path KMS-Lite restart-drain check` — the verify-only
  fast path detects a dead marker via `kms_lookup_handle` and
  falls through to the install path to self-heal. Documents the
  restart-drain behaviour.
- `## Step 3 — BYOK self-provisioning via agent tools (v1.3.0 NEW)`
  — the four sub-steps (3a capability probe → 3b `ens_env_read`
  → 3c `mcp_set_env` for BYOK_BASE_URL + BYOK_MODEL →
  3d `kms_request` + `kms_attach` for BYOK_API_KEY marker → 3e
  seam verify). 3a's honest-stop on absent tools is the canonical
  "promote required" gate.

**Changed:**

- Front-matter version 1.2.0 → 1.3.0.
- `requires:` body: `{tools: [bash, instance], env: []}` → `{tools:
  [bash, infra], env: []}`. `mcp_set_env` is the `infra` category;
  `instance` was never actually needed. `ens-env` is required
  DIRECTLY (listed in `requires.tools` alongside `bash` and
  `infra`) — `ens_env_read` is the self-provisioning entry point.
- `## Step 2 — Configure the builtin server` — added the
  marker-clobber-trap caveat: a later `configure-builtin` run
  drops env keys (including KMS markers); the install path runs
  `configure-builtin` first and `mcp_set_env`/`kms_attach` second
  so a subsequent reconfigure triggers a re-apply.
- `## Step 3 — KMS bind` — replaced with the
  `## Step 3 — BYOK self-provisioning via agent tools` section
  above. The v1.2.0 KMS bind is preserved in spirit (mint →
  attach → marker), but the entry point is the agent tool chain
  (no raw curl).
- `## Stage 5 — od_generate_design smoke` — split into PASS
  criteria and FAIL criteria; the FAIL branch now distinguishes
  the "invalid API key" expected outcome (bridging gap) from
  other tool failures.
- `## Step 6 — Report` — added `blocked_on_credentials` to the
  day-1 kinds the skill may emit.

**Preserved (v1.2.0):**

- The full `## End-to-end verification — five stages` section is
  preserved with the Stage 4 / Stage 5 v1.3.0 adaptations.
- The full `## Idempotency contract` is preserved.
- The `[resume]` convention is preserved verbatim; the field
  contract now lists `blocked_on_credentials` as a kind.
- All fences intact: no Docker, no apt/system packages, no
  plaintext secrets anywhere, no env-var values in any report.

**Preserved (v1.1.0):**

- The full `## Daemon source-build install (Linux, user-space, no
  Docker)` section is preserved verbatim — the self-install
  procedure for the OD daemon itself (no Docker, no apt/system
  packages, nvm user-space only, source-build).

**Stage-3 relabel:** v1.2.0's "Mechanism readiness — supersedes
self-provisioning directive" paragraph is REMOVED. v1.3.0 IS the
self-provisioning flow that v1.2.0 was waiting for (the Stage 3
follow-up). The three-stage chain in the
`daemon/tools/ens_env_tools.py` docstring (Stage 1 ens_env_read →
Stage 2 system promote → Stage 3 this skill) is now resolved: this
skill IS Stage 3.

### v1.2.0 — End-to-end verification contract (2026-10-02)

**Added (NEW sections):**

- `## Idempotency contract` — re-run on a fully-working host =
  verify-only fast path; no mutation.
- `## End-to-end verification — five stages` — Stage 1 daemon
  health, Stage 2 seam registered, Stage 3 tool surface verified,
  Stage 4 credentials readiness, Stage 5 `od_generate_design` smoke.
  The success criterion per user directive: "must not JUST install, must
  make sure it WORKS WITH THE SYSTEM, including MCP, keys, etc."
- Per-stage evidence lines; verification summary line template.

**Changed:**

- `## Step 0 — Pre-flight` — added `present → verify-only fast path`
  branch (was: always re-run install; now: short-circuit to the
  five-stage verification on a fully-working host).
- `## Step 2 — Configure the builtin server` — added the BYOK
  plaintext-fields example (with `byok_api_key` deliberately
  omitted; KMS marker attached separately). Original v1.1.0 minimal
  install example preserved verbatim.
- `## Step 3 — KMS bind` — generalised from "non-loopback token only"
  to "any secret-bearing field, including `BYOK_API_KEY` for the
  design-workflow integration"; added `kms_attach` idempotency note
  (same-(handle, env_key) tuple collapses the existing binding).
- `## Step 4 — Verify the install` — now points to the integrated
  five-stage end-to-end verification section (was: single endpoint
  ping).
- `## Step 5 — Post-install self-check` — added the five-stage
  verification re-run before emitting the summary line.
- `## Step 6 — Report` — added the verification summary line
  template (five-stage summary on full success; envelope on any
  FAIL).

**Preserved (v1.1.0):**

- The full `## Daemon source-build install (Linux, user-space, no
  Docker)` section is preserved verbatim — the self-install
  procedure for the OD daemon itself (no Docker, no apt/system
  packages, nvm user-space only, source-build).
- The `[resume] convention` is preserved verbatim.
- All fences intact: no Docker, no apt/system packages, no plaintext
  secrets anywhere — files, reports, command echoes, tool-results,
  checkpoints.

**Mechanism readiness — supersedes self-provisioning directive:**
(REMOVED in v1.3.0 — the self-provisioning follow-up landed.)

### v1.1.0 — Linux user-space daemon source-build (2026-10-02)

- Added `## Daemon source-build install (Linux, user-space, no
  Docker)` — the self-install procedure for the OD daemon itself
  (previously MCP-seam-only; the daemon-gap disclaimer in Step 4
  now points there).
- Preconditions + native-dep facts verified on this host class.
- U64 not in scope; resolved in `init`.

### v1.0.0 — initial (§7 bootstrap)

- configure-builtin + KMS-Lite marker discipline + [resume]
  convention ratified.
