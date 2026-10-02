---
version: 1.2.0
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

v1.2.0 adds the **End-to-end verification contract** — five ordered
stages (daemon health → seam → tool surface → credentials readiness →
`od_generate_design` smoke). The contract is the success criterion;
"daemon built" alone is NOT success. Re-runnable: on a fully-working
host the contract short-circuits to a no-mutation verify-only fast
path. The v1.1.0 self-install procedure is preserved verbatim below
(only Step 3's KMS bind is generalised to cover `BYOK_API_KEY`).

`requires:` for this skill is `{tools: [bash, instance], env: []}` —
deliberately NO `mcp:` key (PR4 resolution): requiring the
not-yet-installed `opendesign` capability in front-matter would
deadlock the loader (the capability only becomes true BY this skill).
The pre-flight below is the bootstrap loop: it runs before install and
again as the body-final self-check.

## Idempotency contract (v1.2.0 NEW)

Re-running this skill on a host where the capability is already
`present` MUST NOT mutate state. The fast path:

1. `capability_check("opendesign")` → `present`
2. Run the five-stage end-to-end verification below
3. If all five PASS → emit ONE `Result:` summary line
   (`Result: opendesign capability present — 5/5 verification stages
   PASS`) and exit. No install call. No KMS call. No row write.
4. If any stage FAILS → fall through to the install path. Re-running
   the install path is safe (`configure-builtin` is idempotent;
   `kms_attach` collapses same-handle bindings).

The fast path exists because `configure-builtin` writes to a row the
host may already have configured — re-writing on every probe would
race with concurrent installers and could clobber a marker.

## End-to-end verification — five stages (v1.2.0 NEW)

The success criterion for this skill, in order. Each stage produces
an EVIDENCE line; collect all five into the final `Result:` summary.
Any FAIL honest-stops with a `Result:` envelope (see Step 6).

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
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-8079}"
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
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install-opendesign-v1.2.0","version":"1.2.0"}}}
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
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-8079}"
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

Note on follow-up commissioning: BYOK values are system-generated in
a separate self-provisioning follow-up (KMS key-generation feature +
agent tools to read ensemble LLM provider config + write MCP config).
The mechanism below is ready; values will be populated by tooling in
that follow-up.

### Stage 5 — `od_generate_design` end-to-end smoke

A real generation call against the daemon with the BYOK seam
populated. Without the seam populated, this stage reports
**BLOCKED-ON-CREDENTIALS** (honest-stop, not failure).

```bash
OD_DAEMON_URL="${OD_DAEMON_URL:-http://127.0.0.1:7456}" \
  open-design-mcp --stdio 2>/dev/null <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install-opendesign-v1.2.0-smoke","version":"1.2.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"od_generate_design","arguments":{"projectId":"install-opendesign-v1.2.0-smoke","prompt":"Render a small hero section with one primary button.","format":"html"}}}
EOF
# expect: a JSON result whose content[].text carries a non-empty HTML payload.
```

Smoke PASS criteria:

- The `od_generate_design` call returns with no error.
- The response's content array carries at least one entry whose text
  contains `<html` (or a sensible HTML fragment opener — `<div`,
  `<section`, `<button` are acceptable evidence of a real generation).
- HTTP health did not degrade; daemon's `/api/health` still `ok:true`.

Smoke FAIL — report the exact error verbatim:

- `"BYOK not configured: missing BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL"`
  → Stage 4 not met; honest-stop, NOT a skill failure.
- `"invalid API key"` or upstream 401/403 from the BYOK provider →
  KMS attach completed but the key is invalid; re-mint via
  `kms_request` + re-attach (see Step 3). The mechanism completed;
  the value is wrong.
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

Any FAIL → emit the `installed_but_unconfigured` envelope
(Step 6) with the failed stage's `detection_evidence`.

## Step 0 — Pre-flight (mandatory first instruction, already above)

`capability_check("opendesign")` — tri-state:

- `present` → already installed AND configured. Run the five-stage
  end-to-end verification (§"End-to-end verification — five stages")
  — fast path on a fully-working host, no mutation. PASS →
  summary line; FAIL → install path from Step 1.
- `unconfigured` → installed but a secret/env binding is missing. Jump
  to Step 3 (KMS bind) — and check Stage 4's credentials contract.
- `missing` → not installed. Continue with Step 1.

## Step 1 — Resolve the daemon API base

The daemon HTTP API lives on loopback at the port the daemon booted
with (default 8079):

```bash
BASE="http://127.0.0.1:${ENSEMBLE_PORT:-8079}"
curl -fsS "$BASE/api/mcp-servers" >/dev/null && echo "api reachable"
```

NOTE (route reality, verified 2026-09-26): the routes are
`/api/mcp-servers/...` — hyphenated, NO `/v1` segment. Earlier plan
text citing `/api/v1/mcp_servers/...` is stale; use the hyphenated
form. If the API is unreachable, STOP and escalate (Step 6) with
`detection_evidence="daemon API unreachable at $BASE"`.

## Step 2 — Configure the builtin server (create or update)

`open-design-mcp` is a stdio MCP server (10 `od_*` tools). Day-1
loopback posture is ZERO-CREDENTIAL: `OD_DAEMON_URL` carries the
default endpoint, `OD_API_TOKEN` is EMPTY (loopback is
unauthenticated). The builtin definition supplies both defaults —
so the minimal install passes empty values and NO secret exists:

```bash
curl -fsS -X POST "$BASE/api/mcp-servers/configure-builtin" \
  -H 'Content-Type: application/json' \
  -d '{"template_name": "opendesign", "values": {}}'
```

For the BYOK contract (Stage 4), the same endpoint accepts the
plaintext fields (`BYOK_BASE_URL`, `BYOK_MODEL`) — `BYOK_API_KEY` is
left OUT of this call because the secret-bearing field rides the
KMS-Lite marker seam (Step 3). Example with BYOK plaintext set:

```bash
curl -fsS -X POST "$BASE/api/mcp-servers/configure-builtin" \
  -H 'Content-Type: application/json' \
  -d '{"template_name": "opendesign", "values": {
        "byok_base_url": "<BYOK_BASE_URL plaintext>",
        "byok_model":    "<BYOK_MODEL plaintext>"
      }}'
# `byok_api_key` intentionally NOT in this payload. Step 3 binds the marker.
```

- The route is idempotent: re-running with the same values returns the
  same row (an `install_idempotency_key` in the row's
  `instance_metadata` guards against duplicate inserts). A re-run with
  DIFFERENT values updates the row and the daemon snapshots the
  previous config (`prev_config_snapshot`, 7-day TTL).
- If the daemon refuses with a schema-version mismatch (HTTP 409), DO
  NOT retry blindly — the row's config schema drifted from the
  definition. Escalate (Step 6) with the refusal body as
  `detection_evidence`.
- NEVER put a plaintext secret in `values`. If (and only if) a token
  is genuinely required (non-loopback install OR BYOK), mint it via
  KMS-Lite in Step 3 and attach the handle — the marker value is the
  only secret-shaped thing that may appear in the stored config.

## Step 3 — KMS bind (only when a secret is actually required)

Mint a handle and attach it to the server row. The stored config then
carries ONLY a `__KMS_REF__<handle>__` marker; plaintext never leaves
the KMS store and never appears in any message, log, or checkpoint:

```
kms_request(service="opendesign", reason="BYOK_API_KEY for opendesign MCP")
# → {"handle": "KMS_HANDLE_…", "fingerprint": "…"}
kms_attach(server_id=<id from Step 2>, handle=<handle>, env_key="BYOK_API_KEY")
# → {"server_id": …, "handle": …, "env_key": "BYOK_API_KEY", "fingerprint": …, "marker": "__KMS_REF__…__"}
```

The marker format is fixed: `__KMS_REF__` + handle + `__`. The handle
namespace is `KMS_HANDLE_<uuid>`. Both are defined in
`daemon/services/kms_lite.py`. The spawn-time resolver
(`daemon/services/kms_resolver.py`) substitutes plaintext→marker→plaintext
in-RAM immediately before the MCP subprocess env is built; plaintext
never rides the wire.

Idempotency: re-running `kms_attach` with the same `(handle, env_key)`
tuple collapses — the existing binding entry in
`instance_metadata.bound_handles` is removed and re-appended with the
fresh actor + timestamp (the binding collapse path, kms_attach:1033).

The full BYOK contract (`BYOK_BASE_URL` + `BYOK_MODEL` plaintext,
`BYOK_API_KEY` via KMS attach) is encoded in the config schema at
`daemon/mcp/builtin_servers/opendesign.py:146-176`.

If `kms_request` returns `ERROR: KMS_UNAVAILABLE` the store is
fail-closed (no `SYSTEM_ENCRYPTION_KEY`). DO NOT fall back to
plaintext. Report the install as complete-but-unbound: re-run
`capability_check("opendesign")` — it will return `unconfigured` — and
emit the envelope in Step 6 with
`kind="installed_but_unconfigured"`,
`detection_evidence="kms_key_absent"`,
`resume_hint="step_after_kms_bind"`.

If `kms_attach` returns `ERROR: HANDLE_NOT_FOUND: handle=<…>` the
stored handle is unknown to the KMS store (e.g. daemon restarted and
the day-1 in-memory store was drained — see arch §7.5). Re-mint via
`kms_request` and re-attach. Do NOT fall back to plaintext.

## Step 4 — Verify the install

Run the five-stage end-to-end verification from §"End-to-end
verification — five stages" (this is the integrated check — what was
historically a single endpoint ping is now the full contract).

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

Run the five-stage end-to-end verification one more time. All five
PASS → emit the verification summary line. Any FAIL → emit the
envelope with the failed stage's `detection_evidence` (see §"End-to-end
verification — five stages" for the per-stage evidence shape).

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
escalate to spawn/retry the installer) and
`installed_but_unconfigured` (installed; a minted key is not yet
attached — mint + resume). `policy_denied` is schema-only and MUST NOT
be emitted (no policy layer day-1). On FULL success report the
five-stage verification summary line — the designer reads the
capability state plus the verification trail, not just the envelope.

The daemon writes the §7.4 audit line
(`install-audit.jsonl`: `ts/event/name/actor/parent/secret_ref/idempotency_key/trace_id`)
server-side per configure-builtin mutation; your envelope is the
mission-side record. `secret_ref` in the audit lane is the KMS HANDLE
only — never a secret value.

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
  `installed_but_unconfigured`).
- `tools_now_available` — tool names the resumer can now use (e.g. a
  newly-granted `infra` category).
- `resume_from` — the step label to continue from
  (`step_after_preflight` | `step_after_kms_bind` | ...). NO state
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
  plaintext-fields example (`byok_base_url`, `byok_model` via
  `configure-builtin`; `byok_api_key` deliberately omitted; KMS
  marker attached separately). Original v1.1.0 minimal install
  example preserved verbatim.
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

BYOK values are populated by a separate self-provisioning follow-up
commission (KMS key-generation feature + agent tools to read ensemble
LLM provider config + write MCP config). The v1.2.0 mechanism is
ready and proven via dry-run (see acceptance criteria report): exact
provisioning commands + KMS attach dry-run + revert. The skill's
next version consumes the new self-provisioning agent tools; v1.2.0
implements the contract those tools will drive.

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