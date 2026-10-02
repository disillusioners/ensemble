---
version: 1.1.0
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

v1.1.0 adds §"Daemon source-build install (Linux, user-space)" — the
self-install procedure for the OD daemon itself (previously
MCP-seam-only; the daemon-gap disclaimer in Step 4 now points there).

`requires:` for this skill is `{tools: [bash, instance], env: []}` —
deliberately NO `mcp:` key (PR4 resolution): requiring the
not-yet-installed `opendesign` capability in front-matter would
deadlock the loader (the capability only becomes true BY this skill).
The pre-flight below is the bootstrap loop: it runs before install and
again as the body-final self-check.

## Step 0 — Pre-flight (mandatory first instruction, already above)

`capability_check("opendesign")` — tri-state:

- `present` → the server is already installed and configured. SKIP to
  Step 5 (self-check) and report success. Do not re-install.
- `unconfigured` → installed but a secret/env binding is missing. Jump
  to Step 3 (KMS bind).
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

Rules:

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
  is genuinely required (non-loopback install), mint it via KMS-Lite
  in Step 3 and attach the handle — the marker value is the only
  secret-shaped thing that may appear in the stored config.

## Step 3 — KMS bind (only when a secret is actually required)

Mint a handle and attach it to the server row. The stored config then
carries ONLY a `__KMS_REF__<handle>__` marker; plaintext never leaves
the KMS store and never appears in any message, log, or checkpoint:

```
kms_request(service="opendesign", reason="OD_API_TOKEN for opendesign MCP")
# → {"handle": "KMS_HANDLE_…", "fingerprint": "…"}
kms_attach(server_id=<id from Step 2>, handle=<handle>, env_key="OD_API_TOKEN")
```

If `kms_request` returns `ERROR: KMS_UNAVAILABLE` the store is
fail-closed (no `SYSTEM_ENCRYPTION_KEY`). DO NOT fall back to
plaintext. Report the install as complete-but-unbound: re-run
`capability_check("opendesign")` — it will return `unconfigured` — and
emit the envelope in Step 6 with
`kind="installed_but_unconfigured"`,
`detection_evidence="kms_key_absent"`,
`resume_hint="step_after_kms_bind"`.

## Step 4 — Verify the install

```bash
curl -fsS "$BASE/api/mcp-servers" | python3 -c \
  "import json,sys; rows=json.load(sys.stdin)['mcp_servers']; \
   print(json.dumps(next(r for r in rows if r['name']=='opendesign')['config']))"
```

POST that `config` to `/api/mcp-servers/test-connection`:

```bash
CONFIG='{"transport": "stdio", "command": "open-design-mcp", "args": [], "env": {"OD_DAEMON_URL": "http://127.0.0.1:7456"}}'
curl -fsS -X POST "$BASE/api/mcp-servers/test-connection" \
  -H 'Content-Type: application/json' -d "{\"config\": $CONFIG}"
```

A successful start reports the tool count (expect ~10 `od_*` tools).
A marker-bearing `OD_API_TOKEN` does NOT block the start — the MCP
server process launches fine; only token-authenticated daemon calls
would need the resolved secret (resolved in-RAM at MCP spawn by the
KMS resolver, never via this API).

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
be emitted (no policy layer day-1). On FULL success report a plain
`Result:` summary (kind not required) — the designer reads the
capability state, not just the envelope.

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
