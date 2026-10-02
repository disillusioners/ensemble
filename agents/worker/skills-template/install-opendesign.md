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
`mcp_set_env` + `kms_request` + `kms_attach`. No user prompt for
credentials — the keys are already there (per user directive
2026-10-02: "BYOK values come from the system .env; no new key
material").

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
(`ens_env_read`) — the agent's `tools.allow` MUST include
`ens-env`; the canonical `agents/worker/meta.json` already lists
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
| `BYOK_API_KEY` | `OPENAI_API_KEY`          | `OPENAI_API_KEY` ✓          | YES (`OPENAI_API_KEY`)  | `kms_request` → `kms_attach` (MARKER) — see §"Known gap" |
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

### Known gap — `BYOK_API_KEY` does NOT carry `OPENAI_API_KEY` plaintext

**This is the most important paragraph in this skill.** The user
directive says "BYOK_API_KEY ← OPENAI_API_KEY (via KMS marker,
never plaintext)" — but the **current `kms_request(service, reason)`
implementation mints a fresh random token internally**
(`daemon/services/kms_lite.py:219`: `plaintext =
secrets.token_urlsafe(32)`). The tool's signature does NOT accept
an external plaintext parameter; the caller never sees the
plaintext. Consequently, **a `kms_request` → `kms_attach` flow for
`BYOK_API_KEY` produces a marker that resolves to a KMS-minted
opaque token — NOT to the live install's `OPENAI_API_KEY` value.**

**Therefore `Stage 5` `od_generate_design` smoke is expected to fail
with HTTP 401/403 (`invalid API key`) from the BYOK upstream** —
this is the v1.3.0 contract; the failure is the correct signal that
the bridging commission has not yet landed. The skill does NOT
fall back to plaintext; it emits the standard BLOCKED-ON-CREDENTIALS
envelope and names the follow-up commission owed.

**Follow-up owed (separate commission — NOT in scope of this skill
type):** either (a) extend `kms_request` to accept an optional
external plaintext (`kms_request(service, reason, plaintext=None)`
— mint-and-store or ingest-and-store), OR (b) add an env-passthrough
mode where a sentinel handle (`__KMS_REF__OPENAI_API_KEY__`) is
recognised by the resolver as "look up OPENAI_API_KEY in
`os.environ` at spawn time", OR (c) add a `mcp_set_env_secret` lane
(parallel to `mcp_set_env` but for secrets) that bypasses the
secret-shape rejection. The skill author here picks (a) as the
smallest-diff option; the project owner chooses.

The KMS-Lite marker mechanism, the resolver seam, the audit
discipline, and the redaction filter (PB-F1) all remain intact —
only the ingest path for an external plaintext is missing. The
BYOK_BASE_URL + BYOK_MODEL halves of the mapping work end-to-end
today (this is the part the v1.3.0 skill verifies successfully).

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

### Fast-path KMS-Lite restart-drain check (v1.3.0 NEW)

KMS-Lite is **IN-MEMORY**: every daemon restart drains every
handle. The verify-only fast path MUST detect a dead marker and
self-heal rather than fail:

1. After `capability_check("opendesign")` returns `present`, look
   at the seam row's `instance_metadata.bound_handles` (the row
   is read via the same `/api/mcp-servers/<id>` GET the v1.2.0
   Stage 4 verification uses; the marker handle is recorded there).
2. For each `bound_handles` entry, call
   `kms_lookup_handle(handle=<handle>)`. A
   `{"fingerprint": "..."}` response = marker is alive;
   `ERROR: HANDLE_NOT_FOUND` = marker is dead (post-restart).
3. **For any dead marker, fall through to the install path** even if
   `capability_check` says `present`. The install path runs
   `kms_request` (mints a NEW handle) and `kms_attach` (binds the
   new handle, removing the dead binding in the collapse path) —
   the marker is restored. The fast path is fast ONLY when both the
   row AND the KMS-Lite marker are alive.
4. Document the restart-drain behaviour in the final report so
   operators understand why a "fast" fast-path exit did not occur on
   a post-restart probe.

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
(only as a `__KMS_REF__<handle>__` marker — NEVER plaintext),
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
                     'BYOK_API_KEY': env.get('BYOK_API_KEY'), \
                     'bound_handles_count': len((r.get('instance_metadata') or {}).get('bound_handles') or [])}))"
```

Per-field checks:

| Field        | Required | Form                                                  |
|--------------|----------|-------------------------------------------------------|
| `OD_DAEMON_URL` | yes  | plaintext URL                                          |
| `BYOK_BASE_URL` | yes  | plaintext URL                                          |
| `BYOK_MODEL`    | yes  | plaintext model identifier                             |
| `BYOK_API_KEY`  | yes  | `__KMS_REF__KMS_HANDLE_<uuid>__` marker ONLY — never plaintext |

If ANY field is missing or malformed, the skill must COLLECT/GUIDE — not
fail silently. The skill reports EXACTLY which fields are missing and
the EXACT provisioning command (from the install path's Step 3 below).
`BYOK_API_KEY` must be provisioned via KMS attach (the
`__KMS_REF__<handle>__` marker) — plaintext is FORBIDDEN anywhere
in the stored config, in logs, in tool-results, in checkpoints
(P3-WP10 redaction covers KMS-registered plaintexts only — see §Fences).

**Marker-clobber trap (documented, v1.3.0 explicit caveat):** a later
`configure-builtin` run regenerates the row's `config` from the
builtin schema payload and can drop env keys — including KMS
markers. Agent-lane writers must re-apply env (and re-attach
handles) after any reconfigure. The HTTP lane's install-audit and
idempotency rails protect the HTTP lane; the agent lane owns its
own re-apply.

**Read-back redaction reminder:** `BYOK_BASE_URL` reads back
`[REDACTED]` through the HTTP API (presentation-layer redaction —
`KEY/TOKEN/SECRET/PASSWORD/BASE/HEADERS` are all redacted by the
`redact_secrets` helper at `daemon/routers/mcp_servers.py`). This
is EXPECTED — verify the key IS present (its slot is non-empty);
verify the value via the Stage 5 round-trip, not by reading the
field. `BYOK_API_KEY` reads back as a marker (`__KMS_REF__…__`)
which is the correct evidence that the KMS attach landed.

### Stage 5 — `od_generate_design` end-to-end smoke

A real generation call against the daemon with the BYOK seam
populated. With the v1.3.0 self-provisioning flow, BYOK_BASE_URL and
BYOK_MODEL are bridged directly; for `BYOK_API_KEY` the
bridging-gape behaviour is **expected and documented** (see §"Known
gap" above). Stage 5 PASS criterion differs by which path the
flow took:

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
  **EXPECTED** when the live install's `OPENAI_API_KEY` is not
  bridged into the KMS marker (see §"Known gap"). Emit
  `BLOCKED-ON-CREDENTIALS` with
  `detection_evidence="byok_api_key_marker_resolves_to_opaque_token_not_openai_api_key"`
  and `resume_hint="commission_kms_register_or_oidc_passthrough"`.
  This is NOT a skill failure — the mechanism completed; the
  bridging is owed to a separate commission.
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

If Stages 1-4 PASS and Stage 5 returns the expected `invalid API
key` from the upstream byok (the KMS marker-bridging limitation),
emit the standard `BLOCKED-ON-CREDENTIALS` envelope (Step 6) with
`detection_evidence` naming the follow-up commission owed.

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

Call `ens_env_read(keys=None)` (default-curated BYOK set). The
result is the canonical mapping-table payload — see §"BYOK
mapping" above.

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

Check `_missing`: any required key in `["OPENAI_BASE_URL",
"OPENAI_API_KEY"]` listed in `_missing` is a real blocker
(BYOK_BASE_URL/BYOK_API_KEY cannot be derived from empty
values). Emit
`BLOCKED-ON-CREDENTIALS` with
`detection_evidence="missing_required_env_keys:<keys>"`.

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
  replaces. `__KMS_REF__` markers written by `kms_attach`
  are preserved.
- The tool REJECTS secret-shaped KEY NAMES (any key whose name
  has KEY/TOKEN/SECRET/PASSWORD as a substring). It will return
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

### 3d — Mint + attach `BYOK_KEY` via `kms_request` → `kms_attach`

This is the BYOK_API_KEY lane. **Critical (documented §"Known
gap" above):** `kms_request` mints a fresh random plaintext
internally — the marker produced resolves to an opaque token,
NOT to the live install's `OPENAI_API_KEY`. The v1.3.0 contract
runs this anyway so the plumbing is verifiable; Stage 5 is expected
to `invalid API key` from the upstream byok for that reason.
The skill DOES NOT FALL BACK TO PLAINTEXT.

Call sequence:

```
kms_request(service="opendesign", reason="BYOK_API_KEY for opendesign MCP self-provisioning (v1.3.0)")
# → {"handle": "KMS_HANDLE_<uuid>", "fingerprint": "<sha256[:16]>"}
```

The tool returns the handle + fingerprint; the plaintext never
crosses the tool boundary. Audit lane logs the key name only.

```
kms_attach(server_id=<id from 3c>, handle=<handle>,
            env_key="BYOK_API_KEY")
# → {"server_id": ..., "handle": ..., "env_key":
#  "BYOK_API_KEY", "fingerprint": ..., "marker":
#  "__KMS_REF__KMS_HANDLE_<uuid>__"}
```

The marker is written into `config.env["BYOK_API_KEY"]`. The
binding entry is appended to `instance_metadata.bound_handles`.
Idempotency: same `(handle, env_key)` tuple collapses — the
existing binding is removed and re-appended with the fresh
actor + timestamp (the binding collapse path,
`kms_attach:1033`). This is the right behaviour for a
post-restart re-apply (see §"Fast-path KMS-Lite restart-drain
check" above).

If `kms_request` returns `ERROR: KMS_UNAVAILABLE` the store is
fail-closed (no `SYSTEM_ENCRYPTION_KEY`). DO NOT fall back to
plaintext. Report the install as complete-but-unbound: re-run
`capability_check("opendesign")` — it will return `unconfigured`
— and emit the envelope in Step 6 with
`kind="installed_but_unconfigured"`,
`detection_evidence="kms_key_absent"`,
`resume_hint="step_after_kms_bind"`.

If `kms_attach` returns `ERROR: HANDLE_NOT_FOUND` the stored
handle is unknown to the KMS store (e.g. daemon restarted and
the day-1 in-memory store was drained — see §"Fast-path
KMS-Lite restart-drain check"). Re-mint via `kms_request` and
re-attach. Do NOT fall back to plaintext.

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
1-4 PASS → the BYOK seam is wired correctly. Stage 5 — if
"invalid API key" from the upstream byok, that is the EXPECTED
v1.3.0 signal (the bridging gap; see §"Known gap"); emit
`BLOCKED-ON-CREDENTIALS` with the follow-up commission named.
Any OTHER Stage 5 failure → emit the envelope with the failed
stage's `detection_evidence` (see §"End-to-end verification — five
stages" for the per-stage evidence shape).

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
escalate to spawn/retry the installer),
`installed_but_unconfigured` (installed; a minted key is not yet
attached — mint + resume), `blocked_on_credentials` (BYOK_API_KEY
bridging gap; see §"Known gap" — the bridging commission is owed
to a separate ticket). `policy_denied` is schema-only and MUST NOT
be emitted (no policy layer day-1). On FULL success report the
five-stage verification summary line — the designer reads the
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
  transitively via the agent's `tools.allow` (the canonical
  `agents/worker/meta.json` lists both `infra` and `ens-env`).
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