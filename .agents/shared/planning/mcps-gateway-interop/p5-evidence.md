# Phase D1 / P5 — evidence log (conformance kit + docs riders)

**Branch**: `feature/mcps-gateway-interop` · base HEAD `f62aa2e40`
**Date**: 2026-10-22
**Scope**: plan §2/D4 (conformance kit) + §2/D5 (docs) + the §10 pointer text
(as a standalone deliverable — the `mcps_client` repo stays READ-ONLY).

---

## 1. Kit layout + how to point it at another center

```
scripts/conformance/run.sh                  # operator entry (outer timeout, TERM/INT/EXIT trap)
scripts/conformance/README.md               # how to point it at any center
tests/conformance/__init__.py
tests/conformance/conftest.py               # pointing resolution (args+env), self-host center fixture
tests/conformance/conformance_peer.py        # THE protocol module — machine peer + checks + in-kit centers
tests/conformance/test_handshake.py
tests/conformance/test_session_lifecycle.py
tests/conformance/test_transport.py
tests/conformance/test_mcp_fidelity.py
tests/conformance/test_errors.py
tests/conformance/test_auth.py
tests/conformance/test_peer_selftest.py     # FINDING vs CLIENT-BUG + the mutated-center controls
tests/conformance/COVERAGE.md               # §-by-§ coverage + NOT-exercised + independence attestation
```

`pyproject.toml` rides the kit commit: the `conformance` marker is registered
AND the default `addopts` gains `and not conformance` (deselect proof in §5).
`run.sh` invokes exactly:

```
uv run python -m pytest tests/conformance -p no:cacheprovider \
  --confcutdir=tests/conformance -m conformance
```

`--confcutdir` is LOAD-BEARING: the repo-root conftest's mcp-SDK
`sys.modules` mock + autouse `State` patch must not leak into the kit.

**Independence**: ZERO `daemon.*` imports in any kit file (grep-verified —
only a docstring mentions the string). Dependencies: stdlib + `websockets`
(the repo's declared set). The sanctioned-importer list is UNCHANGED:
`tls_harness.py`, `p0_gate.py`, `harness.py` (and `go_client.py` imports none).

### The pointing surface — exactly EIGHT keys (5 + 2 + 1)

| Kind | Key | Contract |
|---|---|---|
| pointing | `CONFORMANCE_CENTER_URL` | the FULL WSS URL including the gateway path |
| pointing | `CONFORMANCE_TOKEN` | the enrollment bearer |
| pointing | `CONFORMANCE_CA_PEM` | PEM to trust (unset ⇒ system store) |
| pointing | `CONFORMANCE_MACHINE_ID` | the `machine_id` announced in hello |
| pointing | `CONFORMANCE_EXPECTED_AGENT` | the agent id the center's sessions belong to |
| begin hook | `CONFORMANCE_BEGIN_CMD` | shell command; exit 0 = triggered; stdout line 1 = session id; remaining lines = optional JSON ECHO (`agent_catalog`, `agent_audit`) |
| begin hook | `CONFORMANCE_BEGIN_URL` | HTTP endpoint with the same document contract |
| strict | `CONFORMANCE_STRICT_CLOSE_CODE` | `1` ⇒ the close-code-1000 pin is a hard assert |

Pinned by `test_peer_selftest.py`: the pointing surface is EXACTLY five; the
TOTAL enumerated set is EXACTLY eight. Every key also has a pytest option
mirror (`--conformance-center-url`, …; option beats env).

**Mode discipline**: no `CONFORMANCE_CENTER_URL` ⇒ SELF-HOST mode (the kit
boots its own in-kit reference center and tests ITSELF — a
self-consistency run, stated loudly in the banner and COVERAGE.md). No
begin hook in pointed mode ⇒ the session areas FAIL FAST with
`SESSION-BEGIN-GAP` (B8a), never a silent skip.

---

## 2. Mutated-center control — the FINDING labels produced (B8e)

`test_peer_selftest.py` boots deliberately mutated in-kit centers and
asserts each check REDS with the CORRECT label + a transcript excerpt:

| Mutation | Label produced (asserted) |
|---|---|
| hello answers `protocol_version: "0.2"` | `FINDING handshake/hello-protocol-version — SPEC §5.2 …` |
| hello result omits `heartbeat_timeout_ms` | `FINDING handshake/hello-heartbeat-params — SPEC §3.3 …` |
| upgrade refused with **403** (not 401) | `FINDING auth/upgrade-401-never-403 — SPEC §4.1/§6.5 …` |
| center closes with **1008** (strict knob on) | `FINDING transport/close-code-1000 — §6.8 (no close code named) …` |
| center sends an oversized inbound REQUEST | **positive** proof of the client-side MUST: the peer answers `PAYLOAD_TOO_LARGE` and the connection survives (`transport.go:63-90`); no red — the row targets the CLIENT-side MUST |

Also pinned in the selftest: the peer+checks pass a CORRECT center (the
green baseline that makes a field red a FINDING rather than a CLIENT-BUG);
the begin-hook CMD/URL contracts (success, echo, failing ⇒ HOOK-ERROR);
`SessionBeginGap` on no hook; `data`-optional tolerance; the args-hash
canonicalisation (order-insensitive, 64-hex, different args differ); the
close-code default-warn vs strict-assert split.

---

## 3. The §10 pointer text — VERBATIM (for the mcps_client owner;

`mcps_client` is READ-ONLY — the user applies this by hand)

> **Pointer (2026-10-22).** The client half of a harness exists in this repo: `internal/fakecenter` is the server side of this protocol (independently transcribed, TLS on loopback) and `internal/remote/conformance_test.go` drives the real client against it over a real WSS connection. The reference center's interop run and the divergences it found are recorded outside this repo, in `agents-ensemble/.agents/shared/planning/mcps-gateway-interop/research-spec-go.md`. What §10 still does not have is the *runner*: a kit that points a client at an arbitrary center with nothing but a URL and a machine.
>
> **The runner now exists** (added 2026-10-22 by the center implementation): `agents-ensemble/scripts/conformance/run.sh` + `agents-ensemble/tests/conformance/` — an independent, center-agnostic pack mapped 1:1 onto this section's six areas (Handshake / Session lifecycle / Transport / MCP fidelity / Errors / Auth). It imports NOTHING from the center's `daemon.*`; every frame is transcribed from this SPEC. Point it at any center with five keys — `CONFORMANCE_CENTER_URL` (the full WSS path), `CONFORMANCE_TOKEN`, `CONFORMANCE_CA_PEM`, `CONFORMANCE_MACHINE_ID`, `CONFORMANCE_EXPECTED_AGENT` — plus the begin-hook contract (`CONFORMANCE_BEGIN_CMD` or `CONFORMANCE_BEGIN_URL`: how a foreign center is told to open a session) and `CONFORMANCE_STRICT_CLOSE_CODE`. Run: `./scripts/conformance/run.sh`. It proves the six areas' wire behaviour incl. a mutated-center negative control (a kit that cannot go RED proves nothing) and pins the known tensions (G1: `HEARTBEAT_TIMEOUT` carries no `id` vs §6.1; the §5.3 "required" column read offering-side; §10 "byte-identically" vs the client's catalog re-encoding — the kit asserts SEMANTIC equality). Its coverage + independence attestation: `tests/conformance/COVERAGE.md`.
>
> **Recommended amendment (§8.4).** §8.4 says "args hashed" and names neither an algorithm nor a canonicalisation. The two implementations diverge today on non-ASCII, `<>&`, and float arguments (Python `ensure_ascii=True` + `default=str` vs Go `json.Marshal` HTML-escaping + shortest-roundtrip float64), so the two audit ledgers are **not joinable** for those arguments, and Go's float64 normalisation collapses `{"n":1}` and `{"n":1.0}` to one hash. Recommendation: normatively pin **JCS (RFC 8785)**, or minimally *sorted keys + `ensure_ascii=False` + shortest-roundtrip numbers*, so an args hash is comparable across implementations.

(The same text is carried verbatim in `docs/mcps-gateway.md` §10.4 with the
note that SPEC §10 remains a stub in `mcps_client` and the user applies it
by hand.)

---

## 4. Docs diff summary — `docs/mcps-gateway.md` (section by section)

| Section | Change |
|---|---|
| header | status/footnote reworded: the doc now pins the activation blocker too |
| **§1.7 (NEW)** The audit projection | one helper `audit_row_to_wire` + `AUDIT_WIRE_KEYS`; TWO read sites (memory sink + router); the write path EXCLUDED by design (persists `id`+`detail`); the `id`-absence and empty-vs-synthetic-stamp divergences; idempotence |
| §2 (activate step 2) | **rider**: the knob-re-read claim corrected — the running service holds a BOOT-TIME snapshot; `load_gateway_config()` reads env only when CALLED (fresh service in tests) |
| §2.2 (intro) | **rider**: same correction at the second site (`:184-186` in the old numbering) |
| §2.2 knob table | **+`MCPS_GATEWAY_READ_TIMEOUT_MS` (5000 ms)** row; heartbeat-interval row gains the `protocol.go:266-278` validation cite (timeout/dead-detect ≥ 3× interval) |
| **§4.4 (NEW)** The audit ledger's limits | two audit heads NOT JOINABLE (non-ASCII / `<>&` / floats); Go float64 COLLIDES 1 ≡ 1.0; center-side collisions (`None` ≡ `{}`, `default=str` folding); the SPEC amendment recommendation (JCS / sorted+`ensure_ascii=false`+shortest-roundtrip) |
| §5 conformance table | §10 row: the kit now EXISTS in this repo (Phase D1) |
| §5.1 The test suites | third lane documented (the kit, the pinned invocation incl. `--confcutdir`, marker gating, self-host vs pointed) |
| §6 known gaps | §10 kit row → SHIPPED (cross-center proof still deferred); **Phase D1 row** → shipped; **TLS-terminator row** (blocking, owner TBD/open); **F-D2 row** (doubled `create_app()`, reported, out of scope) |
| §8.4 | **D3b ordering wording**: `created_at DESC, id DESC` buys deterministic/stable pagination; `id` is uuid4 ⇒ NOT chronological within a same-stamp tie; memory-sink tie behaviour noted |
| **§8.5 (NEW)** The degraded contract | `GatewayReadEnvelope` shape; `degraded` ≠ `gateway_enabled`; only `/audit` can actually set `degraded:true` (machines/sessions are IN-MEMORY lanes — truthful symmetry, not a gap); why 200 not 503; the `MCPS_GATEWAY_READ_TIMEOUT_MS` bound + hung-reader shape; write-only sink ≠ outage; the FE rendering |
| **§10 (NEW)** The interop harness & the conformance kit | 10.1 the real-pair gate (TLS recipe incl. the IP-SAN openssl command, uvicorn kwargs, port=0 read, process isolation, the DB fence, the full client YAML template with the PRIMARY stub child + the FALLBACK, `-check-config` preflight, LOG-based failure detection, the compressed profile, the checklists pointer, the run command); 10.2 why harness TLS does NOT prove production; 10.3 the kit (eight keys, begin-hook contract, mutated-center control, G1/S2 pins, honest scope); 10.4 the §10 pointer note + the pointer text VERBATIM |
| **§11 (NEW)** What remains for activation | what shipped in B/C/D1; the blocking item **verbatim: "activation is BLOCKED on a TLS terminator until one exists"** (owner: TBD — user decision; date: open); the terminator MUST proxy WebSocket upgrades (a plain-HTTP terminator stripping `Upgrade` yields a 404/400 masquerading as a route bug); the harness's own TLS does NOT validate the terminator path; the enable/token/restart checklist; the F-D2 disposition note |
| footer | now also says the pin suite guards the activation blocker |

`tests/unit/test_mcps_gateway_docs.py` (same commit, house rule): the
`MCPS_GATEWAY_READ_TIMEOUT_MS → 5000` knob row added to `ENV_DEFAULTS`
(13 knobs), plus 11 new pin tests (activation verbatim, terminator-proxy,
harness-TLS caveat, degraded contract, projection, D3b wording, ledger
limits, kit location/invocation, pointer note, F-D2) and ONE new negative
arm (the doc must not claim knobs are re-read at call time).

**The "'add'→'extend' in §8.4" rider**: swept — no such wording was present
in the runbook (the §8.4 audit text already read "extend"-compatible);
nothing to change.

---

## 5. Suite results (commands, exit, wall)

| # | Command | Result |
|---|---|---|
| 1 | `timeout 300 uv run python -m pytest tests/unit/test_mcps_gateway_docs.py -q` | **55 passed** in 0.22 s (exit 0) |
| 2 | `./scripts/conformance/run.sh` (SELF-HOST / self-consistency) | **41 passed** in 12.15 s / 12.40 s (exit 0) — target recorded: the IN-KIT reference center |
| 3 | `CONFORMANCE_* ./scripts/conformance/run.sh` pointed at an EXTERNAL center process (`/tmp` driver: TLS center + `CONFORMANCE_BEGIN_URL` hook) | **37 passed, 4 skipped** in 8.54 s (exit 0) — the 4 skips are the NAMED pointed-mode gaps (session expiry; `tools/call`; args-hash; denied-outcome) — the begin-hook URL lane + pointed mode proven end-to-end |
| 4 | `timeout 300 uv run python -m pytest tests/integration/mcps_gateway/test_real_pair.py -m integration -q` | **3 passed** in 32.82 s (exit 0) |
| 5 | `timeout 240 uv run python -m pytest tests/integration/mcps_gateway/test_mcps_gateway_protocol.py tests/integration/mcps_gateway/test_fake_client_selftest.py -m integration -q` | **34 passed**, 28 warnings in 27.12 s (exit 0) |
| 6 | Deselect proof — `uv run python -m pytest tests/conformance -q` (default lane) | **41 deselected** in 0.17 s |
| 7 | Deselect proof — `uv run python -m pytest tests/conformance -m integration -q` | **41 deselected** in 0.10 s |

Whole-repo suite / whole FE suite: NOT run (forbidden by the phase contract).

**Teardown proof**: the kit is in-process (its self-host center dies with
pytest); the external pointed-mode driver (`/tmp/p5_external_center.py`,
NOT a repo file) was killed and `lsof -iTCP -sTCP:LISTEN` shows no surviving
kit listener. `run.sh` traps TERM/INT/EXIT and kills the pytest process
group.

---

## 6. New findings / notes

1. **Docs claim corrected (defect in the runbook, not the code)**: the
   doc said knobs "are re-read at call time by the running service" — the
   service holds a boot-time snapshot (`McpGatewayService.__init__`), so an
   operator editing an env var on a running daemon changes nothing until
   restart. Corrected at BOTH sites (§2 step 2 and §2.2 intro) with a pin
   that forbids the old wording returning.
2. **The kit's pointed-mode observability boundary** (named, not hidden):
   the begin-hook contract has a BEGIN verb only — `session.end`, the
   agent-driven `tools/call`, the ledger's `args_hash` and the `denied`
   outcome are reachable only through the hook's ECHO document or the
   self-host lane. COVERAGE.md lists each with the reason.
3. **Frame-size row semantics** (per the tasking): targets the CLIENT-side
   MUST (§6.7 + `transport.go:63-90`) — the daemon's §6.7 non-enforcement
   is RECORDED as an observation, never a center red. The peer's own MUST
   is pinned in the selftest (oversized inbound request ⇒
   `PAYLOAD_TOO_LARGE` + survival).
4. **F-D2 disposition** (carried in docs §11): `daemon/api.py` has a
   doubled module-level `app = create_app()` — reported, out of scope.
5. **Carry-forwards NOT touched** (still open, unchanged):
   `p0_gate.py:635`'s inverted DETAIL string one-liner and the
   `read_client_audit_log` skipped-count surfacing — neither file was
   touched this phase.

## 7. Open questions for the dispatcher

None blocking. The one decision that remains is the OWNER + DATE for the
TLS terminator (§11) — a user decision, tracked open in the doc.


---

## Independent review round (post-121997b43)

An independent review of the P5 kit + docs riders landed after `121997b43`.

**Verdict**: 7/7 focus areas **PASS**. The reviewer's own re-runs:

| # | Command | Result |
|---|---|---|
| 1 | `timeout 300 ./scripts/conformance/run.sh` | **41 passed** in 12.31 s (exit 0) |
| 2 | `timeout 300 uv run python -m pytest tests/unit/test_mcps_gateway_docs.py -q` | **55 passed** in 0.24 s (exit 0) |
| 3 | Deselect proof — `uv run python -m pytest tests/conformance -q` | **41 deselected** (exit 0) |

The re-runs were **dir-scoped**: a whole-suite collect is blocked by the
PRE-EXISTING `tests/packs/g7_unique_index_smoke_test.py:121` `SystemExit`
(raised at collection time). That file is untouched by this phase — out of
scope, **reported upward** to the dispatcher, NOT fixed here.

**Findings**: 0 🔴 / 3 🟡 / 4 🟢.

**This round's dispositions** (F1–F5):

* **F1 🟡 — the README under-documented the 9th runner key.**
  `scripts/conformance/run.sh:26` reads `CONFORMANCE_RUN_TIMEOUT`
  (default 600) at the shell layer, while the README claimed "exactly
  EIGHT keys" (:28-65) and "an undocumented knob cannot appear" (:65).
  **FIXED**: the README now scopes the invariant explicitly to the kit's
  **pytest layer** (the `conformance_peer.py` pointing surface that
  `test_peer_selftest.py` pins) and documents `CONFORMANCE_RUN_TIMEOUT`
  in a new "Runner controls" section — operator tooling, NOT a pointing
  key. The truthfulness sentence now reads: the pytest surface is exactly
  eight; the runner adds one documented control.
* **F2 🟡 — the G1 FINDING label gave an operator no way to read the
  tension.** The **HARD RED STAYS** (deliberate: the kit pins the current
  reference behaviour per the phase tasking — it was NOT converted to a
  warn). **FIXED (TEXT ONLY)**: the detail of
  `FINDING transport/g1-heartbeat-timeout-no-id`
  (`tests/conformance/conformance_peer.py:1057-1083`) now appends the
  G1 OPEN-SPEC pointer — the reference centre omits `id` today, so a
  centre that includes `id` per SPEC §6.1 hits the KNOWN SPEC tension;
  flip the pin when the SPEC decision lands. The existing marker comment
  is intact and the label string is unchanged (no test pins it).
* **F4 🟢 — double negative.** `tests/conformance/test_transport.py:135`
  read `not peer.transcript.close_code is not None` → rewritten
  `peer.transcript.close_code is None` (same semantics).
* **F5 🟢 — the pytest header read `os.environ`** instead of the resolved
  env, so a `--conformance-begin-cmd` CLI mirror was invisible in the
  header. **FIXED**: `tests/conformance/conftest.py:93-97` now reads
  `_effective_env(config)`, the same resolution the kit uses.
* **F3 🟢 — REPORTED, NOT FIXED** (tester territory; out of this round's
  scope).

**This round's verification** (one run, per the tasking):

| # | Command | Result |
|---|---|---|
| 1 | `timeout 180 ./scripts/conformance/run.sh` | **41 passed** in 11.99 s (exit 0) — the F2 detail-text change breaks no selftest assertion (no test pins the G1 detail string, and the red is unchanged by design) |
