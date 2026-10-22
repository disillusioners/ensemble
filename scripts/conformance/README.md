# The conformance kit — how to point it at ANY center

An independent, center-agnostic test pack for the remote-hands protocol
(`mcps_client/SPEC.md` v0.1, §10). It lives HERE (not in the protocol
repo) and drives a center over a real WSS connection with nothing but a
URL and a machine — the thing SPEC §10 says does not exist.

## Run it

```bash
./scripts/conformance/run.sh                # SELF-CONSISTENCY (in-kit center — see below)
```

Pointed at YOUR center:

```bash
CONFORMANCE_CENTER_URL=wss://center.example:8443/api/mcps-gateway/ws \
CONFORMANCE_TOKEN=<enrollment-bearer> \
CONFORMANCE_CA_PEM=/path/to/center-ca.pem \
CONFORMANCE_MACHINE_ID=conf-machine-1 \
CONFORMANCE_EXPECTED_AGENT=conformance-agent \
  ./scripts/conformance/run.sh
```

Every key also has a pytest mirror (`--conformance-center-url`, ...);
an explicit CLI option beats the environment.

## The pointing surface — exactly EIGHT keys (the kit's pytest layer)

**The five pointing keys** (nothing else points the kit):

| Key | Meaning |
|---|---|
| `CONFORMANCE_CENTER_URL` | the FULL WSS URL **including the gateway path** (the shipped client appends its own default only when the URL has none) |
| `CONFORMANCE_TOKEN` | the enrollment bearer presented on the upgrade |
| `CONFORMANCE_CA_PEM` | a PEM the kit trusts (the center's CA / self-signed cert); unset ⇒ the system trust store |
| `CONFORMANCE_MACHINE_ID` | the `machine_id` the kit's peer announces in `hello` |
| `CONFORMANCE_EXPECTED_AGENT` | the agent id the center's sessions are expected to belong to (informational; echoed to the hook lane) |

**The two begin-hook keys** (B8a — how a FOREIGN center is told to begin
a session; the SPEC has no agent-facing `session.begin` API):

| Key | Contract |
|---|---|
| `CONFORMANCE_BEGIN_CMD` | a shell command that asks the center to open a session. Exit 0 = triggered. **stdout line 1 = the session id**; any remaining lines, when they parse as ONE JSON object, are the ECHO document (optional members: `agent_catalog` — the agent-visible catalog; `agent_audit` — the center's audit rows, `args_hash` included). Non-zero exit = HOOK-ERROR (an operator error, never a center finding). |
| `CONFORMANCE_BEGIN_URL` | an HTTP endpoint with the same contract (2xx = triggered; the body is the hook document). |

Exactly ONE is used per run. Neither set ⇒ the session-dependent areas
**fail fast** with the message `SESSION-BEGIN-GAP: ...` — the kit never
skips them silently; the machine-facing subset (Handshake / Transport /
Errors-from-the-machine / Auth) is the scoped invocation:

```bash
./scripts/conformance/run.sh -- tests/conformance/test_handshake.py tests/conformance/test_transport.py \
  tests/conformance/test_errors.py tests/conformance/test_auth.py
```

**The one strict knob**:

| Key | Meaning |
|---|---|
| `CONFORMANCE_STRICT_CLOSE_CODE` | `1` turns the close-code-1000 pin into a hard assert. Default: a **recorded warning** — the SPEC names no close code (S2), so a 1002/1008 close is a finding to report, not a red by default. |

Total = 5 + 2 + 1 = **8** — pinned by `test_peer_selftest.py`
(an undocumented knob cannot appear; a documented one cannot vanish).
That invariant is scoped to the kit's **pytest layer** — the
`conformance_peer.py` pointing surface (the eight keys above plus their
`--conformance-*` CLI mirrors).

### Runner controls (operator tooling — outside the pytest layer)

`scripts/conformance/run.sh` is a thin shell wrapper around the pytest
lane. It reads ONE extra key, which is **not** a pointing key and is not
part of the eight:

| Key | Meaning |
|---|---|
| `CONFORMANCE_RUN_TIMEOUT` | the OUTER timeout (seconds) for the WHOLE kit run — the runner wraps the pytest call in `timeout "$RUN_TIMEOUT"`. Default: **600**. |

So: the pytest pointing surface is exactly eight keys; the shell runner
adds ONE documented control (`CONFORMANCE_RUN_TIMEOUT`).

## Two modes

* **pointed** — `CONFORMANCE_CENTER_URL` set: the kit talks to THAT
  center. This is the conformance verdict.
* **self-host** — no center URL: the kit boots its own in-kit REFERENCE
  center (TLS on 127.0.0.1, ephemeral port, compressed heartbeat knobs)
  and tests ITSELF. This is a **self-consistency** run: it proves the
  kit is right and that its checks can pass a correct peer; it says
  nothing about your center. The banner and `COVERAGE.md` say so.

## What a red means

Every failure is a labelled **`FINDING <area>/<check> — SPEC <§>:
<detail> — transcript: <frames>`** (or a `HOOK-ERROR`, which is an
operator problem). `test_peer_selftest.py` proves the peer + the check
helpers pass a correct center and that deliberately MUTATED centers
(`v: "0.2"`, a missing `heartbeat_timeout_ms`, a 403 upgrade refusal, a
1008 close) go RED with the CORRECT label. So: a field red is a
**FINDING** (center defect) unless the selftest also reds — that would
be a **CLIENT-BUG** (the kit's fault).

## What it does NOT prove

Cross-center behavioural proof awaits a SECOND real center.
`internal/fakecenter` in the protocol repo is Go-`internal/`,
library-only (no `cmd/` entrypoint), so it cannot be driven as a
process from Python. Today's evidence is: the mutated-center control +
this repo's real-pair interop gate (`tests/integration/mcps_gateway/`).
`tests/conformance/COVERAGE.md` states the §-by-§ coverage, the
NOT-exercised areas with reasons, and the independence attestation.

## Requirements

* `openssl` on PATH (the self-host / mutated centers mint a per-run
  self-signed cert with `IP:127.0.0.1` in the SAN — a DNS SAN would not
  match a dial to loopback).
* The repo's declared dependencies (`websockets>=13.0`); the kit imports
  NOTHING from `daemon.*`.
