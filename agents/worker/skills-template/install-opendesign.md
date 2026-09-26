---
version: 1.0.0
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

KNOWN HOST LIMITATION (P2-WP4 §6, flagged leftover): the OD DAEMON
itself is not installed on this host (no Docker). The MCP server
starts and lists tools WITHOUT the daemon; actual design operations
fail until ops brings the daemon up. This is NOT an install failure —
report success for the MCP seam and note the daemon gap in the report
body.

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
