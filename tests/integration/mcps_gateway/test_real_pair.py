"""Phase D1 / P1 + P2 — the REAL pair gate (the shipped Go client ↔ the shipped gateway).

WHAT THIS IS
------------
One runnable gate that drives the SHIPPED Go client (``mcps_client``,
built into ``/tmp`` from the READ-ONLY repository) against the SHIPPED
Python gateway (the real ``create_app()``, the real lifespan, uvicorn-native
TLS on an ephemeral port, the real PostgreSQL fence database) and asserts
the SPEC v0.1 contract on the WIRE.

* **P1 — the happy path**: assertions 1-6, 9-13, 20-22.
* **P2 — the negative / diagnostic paths**: assertions 7, 8, 14, 15, 16,
  24, 25, 26.

Assertions 17-19 (the audit-DB proof) are **P3's** gate: this file
OBSERVES them and never fails on them.

WHY IT RUNS IN ITS OWN PROCESS
------------------------------
``MCPS_GATEWAY_ENABLED`` gates the ROUTE REGISTRATION and the ensemble
config is resolved when ``daemon.api`` is IMPORTED — both at import time.
A pytest process that has already imported the daemon (the fake-client
suite next door is ``integration``-marked too) can no longer change
either.  The gate therefore runs in its OWN process: this file is BOTH
the runner (``python -m tests.integration.mcps_gateway.test_real_pair``)
and the pytest wrapper, and the wrapper SHELLS OUT to the runner.  The
ordering (patch the environment BEFORE the import) is therefore always the
shipped ordering — the harness asserts it too
(``tls_harness.boot_tls_gateway`` refuses to run when ``daemon.api`` is
already imported).

INDEPENDENCE
------------
This module imports NOTHING from ``daemon.*``.  Every daemon-facing fact
it needs is reached through ``tls_harness`` — the ONE module of the
interop lane that is allowed to import the daemon.  ``transcript.py``
(the frame log and the envelope violation recorder) is daemon-free too.

ASSERT FROM THE SPEC, NEVER FROM OBSERVED BEHAVIOUR
----------------------------------------------------
Where the SPEC is silent or ambiguous, the observation is RECORDED and
the assertion does NOT fail on it — that is the discovery channel.  The
SPEC-vs-code gaps this lane surfaced are listed in the evidence file.

Run it directly (the gate itself)::

    .venv/bin/python -m tests.integration.mcps_gateway.test_real_pair --phase p1

or through pytest (the wrapper)::

    .venv/bin/python -m pytest tests/integration/mcps_gateway/test_real_pair.py -m integration -q

File-size rationale (~3000 lines — over the pack band ceiling; header
now, split is BACKLOG):
    This file is deliberately ONE unit for now: it is BOTH the pytest
    wrapper AND the runner (the wrapper shells out to ``python -m
    tests.integration.mcps_gateway.test_real_pair`` — the two-process
    ordering invariants live in one place), and the P1/P2 assertions
    share the harness fixtures and the transcript cross-check helpers.
    Splitting runner from assertions is a filed BACKLOG item (the QA
    consolidation explicitly defers it), not something this lane does
    incidentally.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tests.integration.mcps_gateway import go_client, harness, tls_harness, transcript
else:  # pragma: no cover - module invocation
    from . import go_client, harness, tls_harness, transcript

# ── the identities of the lane ───────────────────────────────────────────

#: The machine id of the interop peer.
MACHINE_ID = "mach_interop"

#: A SECOND machine id, used by the raw upgrade probes.  The probes open
#: their own connection, and a connection for the SAME machine id would
#: SUPERSEDE the Go client's connection (the centre treats a reconnect as
#: a supersession) and break the rest of the flow.
PROBE_MACHINE_ID = "mach_probe"

#: The agent that drives the centre side.
AGENT_ID = "ari"

#: A SECOND agent, used by the §8.3 exclusivity probe.
OTHER_AGENT_ID = "ari2"

#: The tool the stub child serves successfully.
SUCCESS_TOOL = "secret_tool"

#: The tool the stub child always fails on (its error embeds the args).
FAILING_TOOL = "echo_tool"

#: The arguments of the ONE forwarded command — ASCII only, no floats and
#: no ``nil`` (the args hash must be recomputable byte for byte).
COMMAND_ARGS: dict[str, Any] = {"command": "uptime", "timeout_ms": 5000}

#: The arguments of the FAILING command.  The value is the probe secret of
#: the shipped stub child: it MUST NOT reach the ledger (§8.4).
FAILING_ARGS: dict[str, Any] = {"password": "S3CRET-VALUE-4711"}

#: The machine id of the P3 canary row — a SEPARATE id, so the canary is
#: never counted by the ``mach_interop``-scoped assertions (B11: every DB
#: assertion is machine-scoped, and the canary row is not the flow's).
CANARY_MACHINE_ID = "mach_canary_p3"

#: The arguments of the canary row (ASCII-only: the canary hash must be
#: recomputable byte for byte, like every fixture arg of the #18 shape).
CANARY_ARGS: dict[str, Any] = {"probe": "p3-canary"}

#: The args of the three EXPECTED-DIVERGENCE markers (assertion 19) —
#: each INDEPENDENT (never one bundled "they differ"):
#:   (i)   UTF-8 escaping — Python ``ensure_ascii=True`` vs Go raw UTF-8;
#:   (ii)  HTML escaping — Go escapes ``<`` ``>`` ``&``; Python does not;
#:   (iii) number canonicalisation — Go shortest-roundtrip float64
#:         (``1.0`` -> ``1``); Python keeps ``.0``.
#: These args are DELIBERATELY outside the R-2 safe set (ASCII/no-floats)
#: — that is their point.
DIVERGENCE_MARKERS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("utf8-escaping", {"text": "héllo"}),
    ("html-escaping", {"text": "<b>&"}),
    ("float-canonicalisation", {"n": 1.0}),
)

#: The wrapper key sets of the three read endpoints (assertion 23) —
#: EXACT, not superset: a key that appears here unannounced is a shape
#: change the FE model does not know about. D2 (P4) added the shared
#: ``degraded`` / ``error`` envelope keys — this set is the sanctioned
#: post-D2 shape (the FE models them as OPTIONAL, so an older daemon's
#: body without them still parses).
MACHINES_WRAPPER_KEYS = frozenset(
    {"machines", "count", "gateway_enabled", "degraded", "error"}
)
SESSIONS_WRAPPER_KEYS = frozenset(
    {"sessions", "count", "gateway_enabled", "degraded", "error"}
)
AUDIT_WRAPPER_KEYS = frozenset(
    {"audit", "count", "limit", "offset", "gateway_enabled", "degraded", "error"}
)

#: The §8.4 outcome vocabulary (SPEC: ``ok`` / ``error`` / ``denied`` /
#: ``timeout``).
OUTCOME_VOCABULARY = frozenset({"ok", "error", "denied", "timeout"})

# ── the compressed timing profile (the plan's §4 profile) ───────────────

#: THE CENTRE's compressed knobs.  They are NAMES, not literals buried in
#: the code: the evidence file has to name the EXACT values used.
CENTRE_PROFILE: dict[str, str] = {
    "MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS": "500",
    "MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS": "1500",
    "MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS": "1500",
    "MCPS_GATEWAY_COMMAND_TIMEOUT_MS": "1200",
    "MCPS_GATEWAY_SESSION_TTL_MS": "60000",
    "MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS": "30000",
    "MCPS_GATEWAY_PING_INTERVAL_MS": "200",
}

#: The CLIENT's configuration-side values.  They are the client's FALLBACKS
#: (``internal/config/config.go``): the client REFUSES a configuration
#: whose ``heartbeat_interval_ms`` is outside 5000-60000 (SPEC §3.3), so
#: the compressed values of the CENTRE cannot be written into the YAML —
#: the client ADOPTS the advertised ones at runtime instead (and says so
#: in its log).
CLIENT_PROFILE: dict[str, int] = {
    "heartbeat_interval_ms": 5000,
    "heartbeat_timeout_ms": 45000,
    "client_dead_detect_ms": 45000,
    "reconnect_initial_backoff_ms": 250,
    "reconnect_max_backoff_ms": 2000,
    # ABOVE the centre's command deadline (1200 ms) on purpose: the
    # CENTRE's arm must win the timeout race (§9.3 / assertion 26).
    "tool_call_timeout_ms": 5000,
}

#: The auth configuration of the read-parity lane.  The read surface is
#: guarded by ``@require_scope("mcp:read")``, and the decorator is INERT
#: when auth is off — an auth-off lane would vacate exactly the behaviour
#: the read surface has to prove (B12).
AUTH_PROFILE: dict[str, str] = {
    "ENSEMBLE_AUTH_ENABLED": "true",
    "ENSEMBLE_AUTH_CLIENT_ID": "interop-client",
    "ENSEMBLE_AUTH_CLIENT_SECRET": "interop-client-secret-0123456789",
    "ENSEMBLE_AUTH_ISSUER": "https://interop.invalid/",
    "ENSEMBLE_AUTH_ALLOWED_ORIGINS": "https://127.0.0.1",
    "ENSEMBLE_AUTH_SCOPES": "openid,profile,email,mcp:read",
}

#: The repository root (the daemon's ``config.yaml`` is read from here).
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where the evidence of the run is written.
#: The marker of the machine-readable result line of the runner.
RESULT_MARKER = "##RESULT##"

#: The capability map the centre MUST advertise (§5.3).
EXPECTED_SERVER_CAPABILITIES: dict[str, bool] = {
    "session": True,
    "tools": True,
    "notifications": False,
    "heartbeat": True,
    "screenshot_delta": False,
    "multi_session": False,
    "client_cert": False,
}

#: The fields the FE model names for the roster row (assertion 20).
MACHINE_FIELDS = (
    "machine_id",
    "presence",
    "online",
    "connected",
    "last_seen_at",
    "session_id",
    "agent_id",
    "busy",
)

#: The fields the FE model names for the session row (assertion 21).
SESSION_FIELDS = (
    "session_id",
    "agent_id",
    "machine_id",
    "instance_id",
    "state",
    "machine_locked",
    "started_at",
    "last_activity_at",
    "expires_at",
    "tools",
)

#: The fields the FE model names for the audit row (assertion 22).
AUDIT_FIELDS = (
    "timestamp",
    "agent_id",
    "instance_id",
    "machine_id",
    "tool",
    "outcome",
    "session_id",
    "error_code",
    "args_hash",
)


# ── the assertion ledger ────────────────────────────────────────────────


@dataclass
class Assertion:
    """One numbered assertion of the contract."""

    number: int
    phase: str
    name: str
    verdict: str  # "PASS" | "FAIL" | "OBSERVED"
    detail: str = ""
    evidence: str = ""

    def line(self) -> str:
        return (
            f"[{self.verdict:8}] #{self.number:<2} {self.name}"
            + (f" — {self.detail}" if self.detail else "")
        )


@dataclass
class PhaseResult:
    """The outcome of one phase."""

    phase: str
    assertions: list[Assertion] = field(default_factory=list)
    gates: list[Assertion] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    def add(
        self,
        number: int,
        name: str,
        ok: bool,
        detail: str = "",
        *,
        evidence: str = "",
        observed: bool = False,
    ) -> bool:
        """Record one assertion (a FAIL is also a mismatch)."""
        verdict = "OBSERVED" if observed else ("PASS" if ok else "FAIL")
        self.assertions.append(
            Assertion(
                number=number,
                phase=self.phase,
                name=name,
                verdict=verdict,
                detail=detail,
                evidence=evidence,
            )
        )
        if not ok and not observed:
            self.mismatches.append(f"#{number} {name}: {detail}")
        return bool(ok)

    def gate(self, name: str, ok: bool, detail: str = "") -> bool:
        """Record a GATE (the hygiene of the lane, not a SPEC assertion)."""
        self.gates.append(
            Assertion(
                number=0,
                phase=self.phase,
                name=name,
                verdict="PASS" if ok else "FAIL",
                detail=detail,
            )
        )
        if not ok:
            self.mismatches.append(f"gate {name}: {detail}")
        return bool(ok)

    def observe(self, text: str) -> None:
        """Record an observation (never a verdict)."""
        self.observations.append(text)

    @property
    def ok(self) -> bool:
        return not any(a.verdict == "FAIL" for a in self.assertions) and not any(
            g.verdict == "FAIL" for g in self.gates
        )

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"

    def numbers(self) -> list[int]:
        return sorted(a.number for a in self.assertions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "verdict": self.verdict,
            "assertions": [
                {
                    "number": a.number,
                    "name": a.name,
                    "verdict": a.verdict,
                    "detail": a.detail,
                }
                for a in self.assertions
            ],
            "gates": [
                {"name": g.name, "verdict": g.verdict, "detail": g.detail}
                for g in self.gates
            ],
            "observations": list(self.observations),
            "mismatches": list(self.mismatches),
            "timings": dict(self.timings),
            "violations": list(self.violations),
        }


class LogCapture(logging.Handler):
    """Capture the daemon's log records (the heartbeat-kill line is asserted)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    def contains(self, needle: str) -> bool:
        return any(needle in message for message in self.messages())

    def find(self, pattern: str) -> list[str]:
        regex = re.compile(pattern)
        return [m for m in self.messages() if regex.search(m)]


@dataclass
class ClientBox:
    """The carrier of the client a phase started.

    A phase may RAISE — that failure IS the deliverable — so the client
    handle travels back through this box: the teardown of the runner has
    to be able to stop the client (and its stub child) even when the
    phase died halfway through.
    """

    client: Any = None
    pgid: Any = None


# ── the daemon-free helpers ─────────────────────────────────────────────


def canonical_args_hash(arguments: Any) -> str:
    """The SHA-256 of the canonicalised arguments — computed INDEPENDENTLY.

    The shape is the contract of the daemon's ``canonical_json``
    (sorted keys, tight separators, ``default=str``) over the arguments
    ONLY.  The hash is recomputed HERE so the ledger row is checked
    against a value this file derived without the daemon.
    """
    payload = json.dumps(
        arguments if arguments is not None else {},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def go_canonical_hash_derived(arguments: Any) -> str:
    """The SHA-256 of the bytes Go's ``canonical()`` is DOCUMENTED to emit.

    DERIVED — a faithful transcription of ``internal/audit/audit.go``
    (``canonical()``: ``json.Unmarshal`` into ``any``, then
    ``json.Marshal``) combined with ``encoding/json``'s documented
    encodings:

    * map keys are sorted (``json.Marshal`` sorts map keys);
    * ``<``/``>``/``&`` are HTML-escaped (``SetEscapeHTML(true)`` is the
      default) — transcribe with ``ensure_ascii=False`` FIRST, then
      replace exactly ``&`` -> ``\\u0026``, ``<`` -> ``\\u003c``,
      ``>`` -> ``\\u003e`` (in that order: the escapings do not nest);
    * strings are written as RAW UTF-8 (Go never \\u-escapes printable
      non-ASCII);
    * floats take the shortest-roundtrip form (``1.0`` -> ``1``).

    The P3 gate ALSO reads the client's own audit jsonl (the client
    writes ``audit.HashArgs`` for every forwarded call when
    ``audit.enabled`` is on), so this derivation is CROSS-CHECKED against
    the ACTUAL Go hash — if they disagree, this transcription is wrong,
    not the pair.
    """
    payload = json.dumps(
        _go_number_forms(arguments) if arguments is not None else {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,  # Go writes raw UTF-8
        default=str,
    )
    # Go's SetEscapeHTML(true): & first, then < and > (no nesting).
    payload = (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _go_number_forms(value: Any) -> Any:
    """Normalise integral floats to ints — Go's shortest-roundtrip form.

    ``json.Marshal(float64(1.0))`` emits ``1``; Python's ``json.dumps``
    emits ``1.0``.  Replacing the VALUE (not the serialised text) keeps
    the transcription honest for nested shapes.
    """
    if isinstance(value, float) and value == int(value) and abs(value) < 1e21:
        return int(value)
    if isinstance(value, dict):
        return {key: _go_number_forms(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_go_number_forms(item) for item in value]
    return value


def shasum_sha256(payload: str) -> str:
    """The SHA-256 of ``payload`` computed by a NON-PYTHON tool (shasum).

    Assertion 18 is only as strong as its independence claim: hashing the
    same bytes twice in one interpreter proves nothing about the hash
    FUNCTION.  ``shasum -a 256`` (macOS / coreutils) is the second
    implementation — the payload goes in on stdin, exactly the bytes the
    Python side hashed.
    """
    proc = subprocess.run(
        ["shasum", "-a", "256"],
        input=payload.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=True,
    )
    return proc.stdout.decode("utf-8").split()[0]


def https_get(
    port: int,
    path: str,
    *,
    token: str | None = None,
    timeout: float = 10.0,
    accept: str = "application/json",
) -> dict[str, Any]:
    """One HTTPS GET against the booted daemon (the read surface).

    The certificate is self-signed, so the verification is switched off —
    this is a LOCAL probe, not a trust decision.
    """
    import http.client
    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPSConnection(
        "127.0.0.1", port, context=context, timeout=timeout
    )
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except Exception:  # noqa: BLE001 - a non-JSON body is still evidence
            body = None
        return {
            "status": response.status,
            "headers": dict(response.getheaders()),
            "body": body,
            "text": text,
        }
    finally:
        with contextlib.suppress(Exception):
            connection.close()


async def raw_ws_exchange(
    port: int,
    path: str,
    token: str | None,
    frames: list[str],
    *,
    timeout: float = 8.0,
    linger: float = 0.0,
    heartbeat_every: float = 0.0,
) -> dict[str, Any]:
    """Open a RAW WSS to the centre, send ``frames``, collect the answers.

    The raw probe is how the centre is spoken to OUTSIDE the shipped
    client: a refused upgrade (§3.1), a hand-written ``hello`` (§5.1,
    §5.2, §5.3) and the close that follows them are all centre-side
    behaviour that the shipped client cannot produce on demand.

    ``linger`` is how long the probe waits for answers before it closes
    the socket: ``0`` closes it right after the upgrade (the probe of the
    upgrade contract alone), a positive value keeps it open long enough
    to collect the centre's answer.
    """
    import ssl

    import websockets
    from websockets.exceptions import InvalidStatus

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"wss://127.0.0.1:{port}{path}"
    try:
        ws = await websockets.connect(
            url,
            additional_headers=headers,
            ssl=context,
            open_timeout=timeout,
        )
    except InvalidStatus as exc:
        response = getattr(exc, "response", None)
        return {
            "status": getattr(response, "status_code", None),
            "headers": dict(getattr(response, "headers", {}) or {}),
            "frames": [],
            "close_code": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    received: list[str] = []

    async def heartbeat_loop() -> None:
        """A conformant machine heartbeats; a probe that does not is a
        DEAD machine from the centre's point of view (§3.3) — it would
        be killed for silence and would put a stray HEARTBEAT_TIMEOUT
        frame into the transcript of the run."""
        while True:
            await asyncio.sleep(heartbeat_every)
            with contextlib.suppress(Exception):
                await ws.send(
                    json.dumps(
                        {
                            "v": "0.1",
                            "type": "notification",
                            "method": "heartbeat",
                            "params": {},
                        }
                    )
                )

    heartbeater: asyncio.Task | None = None
    if heartbeat_every > 0:
        heartbeater = asyncio.create_task(heartbeat_loop())
    try:
        for raw in frames:
            await ws.send(raw)
        deadline = time.monotonic() + max(0.0, linger)
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                break
            received.append(message)
    finally:
        if heartbeater is not None:
            heartbeater.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeater
        with contextlib.suppress(Exception):
            await ws.close()
        close_code = getattr(ws, "close_code", None)
    return {
        "status": 101,
        "headers": {},
        "frames": received,
        "close_code": close_code,
        "error": "",
    }


def client_hellos(lines: Any, machine_id: str = MACHINE_ID) -> list[Any]:
    """The ``hello`` frames of ONE machine.

    The raw probes send hellos of their own (for a DIFFERENT machine id),
    so a bare count of ``hello`` frames would count the probes too — and
    the "did the client reconnect?" question would be answered by the
    probes' traffic.
    """
    return [
        frame
        for frame in lines.find(direction="recv", method="hello")
        if (frame.parsed or {}).get("params", {}).get("machine_id") == machine_id
    ]


def parsed_frames(raw_frames: list[str]) -> list[dict[str, Any]]:
    """The parsed envelopes of a list of raw frames (the unparsable ones are skipped)."""
    out: list[dict[str, Any]] = []
    for raw in raw_frames:
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def hello_frame(
    *,
    request_id: str = "probe-0001",
    protocol_version: str = "0.1",
    machine_id: str = PROBE_MACHINE_ID,
    capabilities: dict[str, bool] | None = None,
) -> str:
    """One ``hello`` request frame (§5.1), with a chosen capability map."""
    if capabilities is None:
        capabilities = dict(EXPECTED_SERVER_CAPABILITIES)
    return json.dumps(
        {
            "v": "0.1",
            "type": "request",
            "id": request_id,
            "method": "hello",
            "params": {
                "protocol_version": protocol_version,
                "client": {"name": "interop-probe", "version": "0.1.0"},
                "machine_id": machine_id,
                "capabilities": capabilities,
            },
        }
    )


def child_catalog(record_path: Path) -> list[dict[str, Any]] | None:
    """The catalog the CHILD reported — its own ``tools/list`` answer.

    The stub child records every message that crossed its stdio pipe
    (``MCPS_ADVERSARIAL_CHILD_RECORD``), so the child's own catalog can
    be read back INDEPENDENTLY of the centre.  That is what makes the
    semantic-equality assertion of the catalogue meaningful.
    """
    if not record_path.exists():
        return None
    catalog: list[dict[str, Any]] | None = None
    for line in record_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:  # noqa: BLE001 - a partial line is skipped
            continue
        if record.get("dir") != "out":
            continue
        try:
            message = json.loads(record.get("raw") or "")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(message, dict):
            continue
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            catalog = result["tools"]
    return catalog


def catalog_semantics(tools: Any) -> dict[str, Any]:
    """A shape-agnostic projection of a catalogue — for SEMANTIC equality.

    Two catalogues are semantically equal when they carry the same tool
    names and the same ``inputSchema`` STRUCTURE.  The comparison is on
    the PARSED JSON (a JSON object is unordered), never on a re-serialised
    and re-sorted string: the plan forbids "normalising by sorting"
    because the client carries ``inputSchema`` as a raw message.
    """
    out: dict[str, Any] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str):
            continue
        out[name] = {
            "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema"),
        }
    return out


def _roster_session_id(service: Any, machine_id: str) -> Any:
    """The session id the ROSTER points at for ``machine_id`` (or None).

    The roster is the READ surface's view of the §8.3 lock — it is a
    different object from the session manager, and the two can disagree.
    That disagreement is exactly what assertion 16(c) is about.
    """
    for row in service.roster():
        if row.get("machine_id") == machine_id:
            return row.get("session_id")
    return None


def _process_group_alive(pgid: int | None) -> bool:
    """Is any process still a member of the process group ``pgid``?"""
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


async def _wait_until(predicate, *, timeout: float, poll: float = 0.05) -> bool:
    """Wait for ``predicate()`` to become true (never blocks the loop)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return bool(predicate())


# ── the shared phase scaffolding ────────────────────────────────────────


async def _write_client_yaml(
    work_dir: Path,
    artifacts: go_client.GoArtifacts,
    *,
    gw: Any,
    enrollment_token: str,
    listen_token: str,
    listen_addr: str,
    name: str = "client-config.yaml",
    audit_enabled: bool = False,
) -> Path:
    """Write the client configuration and run the ``-check-config`` preflight."""
    config_path = work_dir / name
    go_client.write_client_config(
        config_path,
        center_url=gw.ws_url,
        enrollment_token=enrollment_token,
        machine_id=MACHINE_ID,
        ca_pem=gw.cert_path,
        listen_addr=listen_addr,
        listen_token=listen_token,
        stub_child_bin=artifacts.stub_child_bin,
        audit_path=work_dir / "client-audit.jsonl",
        child_record_path=work_dir / "child.jsonl",
        audit_enabled=audit_enabled,
        **CLIENT_PROFILE,
    )
    return config_path


async def _start_client(
    result: PhaseResult,
    artifacts: go_client.GoArtifacts,
    config_path: Path,
    work_dir: Path,
    box: ClientBox,
    *,
    log_name: str = "client-stderr.log",
) -> go_client.GoClient:
    """Start the shipped client and wait for the handshake."""
    client = go_client.start_client(
        artifacts.client_bin, config_path, log_path=work_dir / log_name
    )
    box.client = client
    child_ready = await client.wait_for_message_async(go_client.CHILD_READY_LINE, timeout=60.0)
    result.gate(
        "client-child-started",
        child_ready,
        "the stub child reported 'child started'"
        if child_ready
        else "the stub child did not report 'child started' within 60s",
    )
    handshake = await client.wait_for_message_async(go_client.HANDSHAKE_LINE, timeout=60.0)
    result.gate(
        "client-handshake-accepted",
        handshake,
        "the client reported 'the center accepted the handshake'"
        if handshake
        else "the client did not report the handshake within 60s",
    )
    return client


# ── P1 — the happy path ─────────────────────────────────────────────────


async def _phase_p1(
    result: PhaseResult,
    *,
    gw: Any,
    artifacts: go_client.GoArtifacts,
    enrollment_token: str,
    listen_token: str,
    listen_addr: str,
    read_token: str,
    capture: LogCapture,
    box: ClientBox,
) -> go_client.GoClient:
    """Assertions 1-6, 9-13, 20-22."""
    service = gw.service
    lines = gw.transcript

    # ── #1 — the upgrade with a BAD bearer ──────────────────────────────
    frames_before = len(lines.frames)
    bad = await raw_ws_exchange(
        gw.port, gw.ws_path, "not-the-enrollment-token", [], linger=0.0
    )
    frames_after = len(lines.frames)
    challenge = bad["headers"].get("WWW-Authenticate") or bad["headers"].get(
        "www-authenticate"
    )
    result.add(
        1,
        "upgrade-with-a-bad-bearer-is-401-with-the-challenge",
        bad["status"] == 401
        and challenge == 'Bearer realm="ensemble-gateway"'
        and frames_after == frames_before,
        f"status={bad['status']} challenge={challenge!r} "
        f"protocol_frames_during_the_refusal={frames_after - frames_before}",
        evidence=f"raw probe: status={bad['status']} headers={bad['headers']}",
    )

    # the SHIPPED client's reaction to the same refusal: fatal, no retry
    bad_config = await _write_client_yaml(
        Path(gw.data_dir).parent,
        artifacts,
        gw=gw,
        enrollment_token="wrong-" + "0" * 40,
        listen_token=listen_token,
        listen_addr=listen_addr,
        name="client-bad-token.yaml",
    )
    code, out, err = await asyncio.to_thread(
        go_client.run_check_config, artifacts.client_bin, bad_config
    )
    result.gate(
        "client-bad-token-config-is-valid",
        code == 0,
        f"exit={code} stderr={err.strip()[:200]}",
    )
    bad_client = go_client.start_client(
        artifacts.client_bin, bad_config, log_path=Path(gw.data_dir).parent / "client-bad.log"
    )
    try:
        fatal = await bad_client.wait_for_message_async(
            "the remote hop stopped", timeout=30.0
        )
        messages = bad_client.messages()
        retried = [m for m in messages if "reconnecting to the center" in m]
        result.add(
            1,
            "the-client-treats-the-refused-credential-as-fatal-and-does-not-retry",
            fatal and not retried,
            f"fatal_logged={fatal} reconnect_lines={len(retried)}",
            evidence=" | ".join(messages[-4:]),
        )
    finally:
        await asyncio.to_thread(bad_client.stop)

    # ── #2 — the upgrade with the GOOD bearer ───────────────────────────
    good = await raw_ws_exchange(
        gw.port, gw.ws_path, enrollment_token, [], linger=0.0
    )
    result.add(
        2,
        "upgrade-with-the-good-bearer-is-101",
        good["status"] == 101,
        f"status={good['status']} close_code={good['close_code']}",
    )

    # ── the shipped client ──────────────────────────────────────────────
    config_path = await _write_client_yaml(
        Path(gw.data_dir).parent,
        artifacts,
        gw=gw,
        enrollment_token=enrollment_token,
        listen_token=listen_token,
        listen_addr=listen_addr,
    )
    code, out, err = await asyncio.to_thread(
        go_client.run_check_config, artifacts.client_bin, config_path
    )
    result.gate(
        "client-check-config-exit-0",
        code == 0,
        f"exit={code} stdout={out.strip()[:160]} stderr={err.strip()[:300]}",
    )
    t0 = time.monotonic()
    client = await _start_client(
        result, artifacts, config_path, Path(gw.data_dir).parent, box
    )
    result.timings["client_handshake_s"] = time.monotonic() - t0

    # ── #3, #4, #5, #6 — the hello exchange ────────────────────────────
    hello_frames = lines.find(direction="recv", method="hello")
    ack_frames = [
        frame
        for frame in lines.find(direction="send", type="response")
        if frame.result.get("protocol_version")
    ]
    result.add(
        3,
        "hello-response-echoes-the-id-verbatim",
        bool(hello_frames)
        and bool(ack_frames)
        and ack_frames[0].id == hello_frames[0].id,
        f"hello_id={hello_frames[0].id if hello_frames else None!r} "
        f"ack_id={ack_frames[0].id if ack_frames else None!r}",
        evidence=f"hello: {hello_frames[0].raw}" if hello_frames else "",
    )
    ack_result = ack_frames[0].result if ack_frames else {}
    result.add(
        4,
        "hello-negotiates-protocol-version-0.1",
        ack_result.get("protocol_version") == "0.1",
        f"protocol_version={ack_result.get('protocol_version')!r}",
    )
    timings = {
        key: ack_result.get(key)
        for key in (
            "heartbeat_interval_ms",
            "heartbeat_timeout_ms",
            "client_dead_detect_ms",
        )
    }
    result.add(
        5,
        "hello-advertises-the-three-heartbeat-parameters",
        all(isinstance(value, int) and value > 0 for value in timings.values()),
        json.dumps(timings),
        evidence=f"hello_ack: {ack_frames[0].raw}" if ack_frames else "",
    )
    # the client's ADOPTED values (the client adopts the server's — §3.3)
    adopted = [
        record
        for record in client.records()
        if "outside the permitted range" in str(record.get("msg", ""))
    ]
    adopted_ok = False
    adopted_detail = "the client logged no adoption warning"
    if adopted:
        record = adopted[-1]
        interval = record.get("heartbeat_interval_ms")
        dead_detect = record.get("client_dead_detect_ms")
        adopted_ok = (
            interval == timings.get("heartbeat_interval_ms")
            and dead_detect == timings.get("client_dead_detect_ms")
        )
        adopted_detail = (
            f"the client adopted interval={interval} dead_detect={dead_detect} "
            f"(the server advertised {timings.get('heartbeat_interval_ms')}/"
            f"{timings.get('client_dead_detect_ms')})"
        )
    result.add(
        5,
        "the-client-adopts-the-advertised-heartbeat-parameters",
        adopted_ok,
        adopted_detail,
        evidence=json.dumps(adopted[-1]) if adopted else "",
    )
    result.add(
        6,
        "the-server-capability-map-is-exactly-the-v0.1-map",
        ack_result.get("capabilities") == EXPECTED_SERVER_CAPABILITIES,
        f"capabilities={json.dumps(ack_result.get('capabilities'), sort_keys=True)}",
        evidence=f"expected={json.dumps(EXPECTED_SERVER_CAPABILITIES, sort_keys=True)}",
    )

    # ── #9 — session.begin ──────────────────────────────────────────────
    t0 = time.monotonic()
    session = await service.connect(
        machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="p1-gate"
    )
    result.timings["session_begin_s"] = time.monotonic() - t0
    begin_frame = _response_to(lines, "session.begin")
    begin_result = begin_frame.result if begin_frame is not None else {}
    result.add(
        9,
        "session-begin-answers-with-the-session-id-the-machine-and-the-catalog",
        bool(session.get("session_id"))
        and session.get("machine_id") == MACHINE_ID
        and isinstance(session.get("tools"), list)
        and bool(begin_result.get("session_id"))
        and begin_result.get("machine_id") == MACHINE_ID
        and isinstance(begin_result.get("tools"), list),
        f"session_id={session.get('session_id')!r} "
        f"tools={len(session.get('tools') or [])}",
        evidence=f"session.begin answer: {begin_frame.raw}" if begin_frame else "",
    )
    session_id = str(session.get("session_id") or "")

    # ── #10 — the catalogue is SEMANTICALLY equal ───────────────────────
    child_record = Path(gw.data_dir).parent / "child.jsonl"
    child_tools = await asyncio.to_thread(child_catalog, child_record)
    center_tools = begin_result.get("tools") if begin_result else None
    center_semantics = catalog_semantics(center_tools)
    child_semantics = catalog_semantics(child_tools)
    equal = bool(child_semantics) and center_semantics == child_semantics
    result.add(
        10,
        "the-centre-catalog-is-semantically-equal-to-the-child-catalog",
        equal,
        f"centre_tools={sorted(center_semantics)} child_tools={sorted(child_semantics)}",
        evidence=json.dumps({"centre": center_semantics, "child": child_semantics})[:600],
    )
    result.observe(
        "the catalogue is re-encoded by the client — SPEC §10 says "
        "'byte-identically', so the BYTE form differs while the SEMANTIC "
        "form is equal. The re-encode mechanisms OBSERVED on the wire: "
        "whitespace compaction (the client answers compact JSON, the "
        "centre emits spaced JSON), HTML-escaping, omitempty (the "
        "'isError' member disappears when it is false), unknown-member "
        "drop, AND the KEYS OF THE inputSchema OBJECT ARE SORTED by the "
        "client's marshaller (the child reported type-then-properties, the "
        "centre received properties-then-type). The tool object itself is "
        "a struct (field order preserved). "
        f"centre={json.dumps(center_tools)[:300]} child={json.dumps(child_tools)[:300]}"
    )

    # ── #11 — tools/call, the success path ──────────────────────────────
    call_result = await service.command(
        agent_id=AGENT_ID, tool=SUCCESS_TOOL, arguments=COMMAND_ARGS
    )
    call_frame = _response_to(lines, "tools/call")
    call_payload = call_frame.result if call_frame is not None else {}
    result.add(
        11,
        "tools-call-returns-the-mcp-result-as-is",
        isinstance(call_result.get("content"), list)
        and call_result.get("isError") is not True
        and isinstance(call_payload.get("content"), list)
        and call_payload.get("isError") is not True,
        f"result_keys={sorted(call_result)} "
        f"content={json.dumps(call_result.get('content'))[:160]}",
        evidence=f"tools/call answer: {call_frame.raw}" if call_frame else "",
    )
    result.observe(
        "the tools/call answer carries "
        f"{sorted(call_result)}: the SPEC §6.6 example shows 'isError: false' "
        "EXPLICITLY, but the shipped client OMITS the member when it is "
        "false (the MCP default is false) and ADDS two members of its own "
        "('_meta' and 'resultType'). The centre relays the result as-is — "
        "which is what §6.6 asks for — so the deviation is the PEER's "
        "re-encoding, not a centre defect."
    )
    result.observe(
        "S1 (non-object tool result): the shipped client answers a "
        "tools/call with the MCP result OBJECT — always, because the MCP "
        "result shape is an object — so the centre's re-wrap branch "
        "(a non-object result becomes {'result': …}) is UNREACHABLE "
        "against this peer. The SPEC §6.6 pass-through is therefore NOT "
        "settled end to end by the real pair; the branch stays a latent "
        "centre-side defect."
    )

    # ── #12 — tools/call, the failure path ──────────────────────────────
    failure_code = ""
    failure_message = ""
    try:
        await service.command(
            agent_id=AGENT_ID, tool=FAILING_TOOL, arguments=FAILING_ARGS
        )
        result.add(
            12,
            "tools-call-relays-the-peers-typed-error-verbatim",
            False,
            "the failing tool returned no error",
        )
    except Exception as exc:  # noqa: BLE001 — the refusal IS the evidence
        failure_code = tls_harness.gateway_error_code(exc)
        failure_message = str(getattr(exc, "message", "") or "")
        result.add(
            12,
            "tools-call-relays-the-peers-typed-error-verbatim",
            failure_code == "TOOL_ERROR"
            and "invalid argument" in failure_message
            and FAILING_ARGS["password"] in failure_message,
            f"code={failure_code!r} message={failure_message[:200]!r}",
        )

    # ── #20, #21, #22 — the read surface (AUTH ON) ─────────────────────
    machines_response = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/machines", token=read_token
    )
    machine_rows = [
        row
        for row in (machines_response["body"] or {}).get("machines", [])
        if row.get("machine_id") == MACHINE_ID
    ]
    machine_row = machine_rows[0] if machine_rows else {}
    result.add(
        20,
        "the-roster-read-carries-every-field-the-fe-model-names",
        machines_response["status"] == 200
        and len(machine_rows) == 1
        and all(field in machine_row for field in MACHINE_FIELDS),
        f"status={machines_response['status']} rows={len(machine_rows)} "
        f"row={json.dumps(machine_row, sort_keys=True)[:300]}",
        evidence=f"body={machines_response['text'][:400]}",
    )

    sessions_response = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/sessions", token=read_token
    )
    session_rows = [
        row
        for row in (sessions_response["body"] or {}).get("sessions", [])
        if row.get("session_id") == session_id
    ]
    session_row = session_rows[0] if session_rows else {}
    result.add(
        21,
        "the-session-read-carries-every-field-and-an-array-of-tools",
        sessions_response["status"] == 200
        and len(session_rows) == 1
        and all(field in session_row for field in SESSION_FIELDS)
        and isinstance(session_row.get("tools"), list),
        f"status={sessions_response['status']} rows={len(session_rows)} "
        f"tools={session_row.get('tools')!r}",
        evidence=f"body={sessions_response['text'][:500]}",
    )

    audit_response = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/audit?limit=1&offset=0", token=read_token
    )
    audit_body = audit_response["body"] or {}
    audit_rows = audit_body.get("audit", [])
    audit_row = audit_rows[0] if audit_rows else {}
    result.add(
        22,
        "the-audit-read-renames-created_at-to-timestamp-and-counts-the-page",
        audit_response["status"] == 200
        and bool(audit_rows)
        and all(field in audit_row for field in AUDIT_FIELDS)
        and audit_row.get("timestamp") is not None
        and audit_body.get("count") == len(audit_rows)
        and audit_body.get("limit") == 1
        and audit_body.get("offset") == 0
        and "gateway_enabled" in audit_body,
        f"status={audit_response['status']} count={audit_body.get('count')} "
        f"page={len(audit_rows)} row={json.dumps(audit_row, sort_keys=True)[:300]}",
        evidence=f"body={audit_response['text'][:500]}",
    )

    # ── the audit-DB proof is P3's gate: OBSERVED here, never failed ────
    await _observe_audit_rows(result, gw, session_id=session_id)

    # ── #13 — session.end ───────────────────────────────────────────────
    frames_before_end = len(lines.frames)
    end_result = await service.disconnect(agent_id=AGENT_ID)
    end_frame = _response_to(lines, "session.end")
    # the socket stays open: heartbeats keep arriving after the end
    await asyncio.sleep(1.5)
    heartbeats_after = [
        frame
        for frame in lines.frames[frames_before_end:]
        if frame.method == "heartbeat" and frame.direction == "recv"
    ]
    roster_after = {
        row.get("machine_id"): row for row in service.roster()
    }.get(MACHINE_ID, {})
    result.add(
        13,
        "session-end-is-acknowledged-and-the-socket-stays-open",
        bool(end_result.get("ended"))
        and end_frame is not None
        and bool(heartbeats_after)
        and roster_after.get("session_id") in (None, ""),
        f"ended={end_result.get('ended')} "
        f"heartbeats_after_the_end={len(heartbeats_after)} "
        f"roster_session_id={roster_after.get('session_id')!r}",
        evidence=f"session.end answer: {end_frame.raw}" if end_frame else "",
    )

    # ── the envelope deviations of the whole run (recorded, never raised) ──
    recorder = transcript.ViolationRecorder()
    recorder.observe_all(lines.frames)
    result.violations = [str(v) for v in recorder.violations]
    result.observe(f"envelope deviations recorded: {recorder.summary()}")
    for violation in recorder.violations:
        result.observe(f"deviation: {violation}")

    return client


def _response_to(lines: Any, method: str, direction: str = "recv") -> Any:
    """The frame that ANSWERS the last request of ``method``.

    THE DIRECTION MATTERS: the centre is the REQUESTOR of
    ``session.begin`` / ``session.end`` / ``tools/call``, so the answer
    travels MACHINE -> CENTRE and is recorded as a ``recv`` frame.  The
    only frame the centre ORIGINATES as an answer is the hello
    acknowledgement, which is a ``send`` frame.
    """
    requests = lines.find(direction="send", method=method, type="request")
    if not requests:
        return None
    request_id = requests[-1].id
    for frame in lines.find(direction=direction, type="response"):
        if frame.id == request_id:
            return frame
    return None


async def _observe_audit_rows(result: PhaseResult, gw: Any, *, session_id: str) -> None:
    """OBSERVE the audit rows (assertions 17-19 are P3's gate)."""
    rows = await asyncio.to_thread(
        tls_harness.fetch_rows,
        gw.engine,
        "SELECT * FROM mcp_gateway_audit WHERE machine_id = :m ORDER BY created_at",
        {"m": MACHINE_ID},
    )
    result.observe(f"audit rows for {MACHINE_ID}: {len(rows)}")
    for row in rows:
        result.observe(
            "audit row: "
            + json.dumps(
                {k: (str(v) if k == "created_at" else v) for k, v in row.items()},
                sort_keys=True,
                default=str,
            )
        )
    expected_hash = canonical_args_hash(COMMAND_ARGS)
    matching = [
        row
        for row in rows
        if row.get("session_id") == session_id
        and row.get("tool") == SUCCESS_TOOL
        and row.get("outcome") == "ok"
    ]
    result.observe(
        f"the success row recomputes to args_hash={expected_hash} "
        f"(stored={matching[0].get('args_hash') if matching else None!r})"
    )
    leaked = [
        row
        for row in rows
        if FAILING_ARGS["password"] in json.dumps(row, default=str)
    ]
    result.observe(
        f"rows carrying the raw failing argument: {len(leaked)} "
        "(the §8.4 guarantee — P3's gate)"
    )


# ── P2 — the negative / diagnostic paths ────────────────────────────────


async def _phase_p2(
    result: PhaseResult,
    *,
    gw: Any,
    artifacts: go_client.GoArtifacts,
    enrollment_token: str,
    listen_token: str,
    listen_addr: str,
    read_token: str,
    capture: LogCapture,
    box: ClientBox,
) -> go_client.GoClient:
    """Assertions 7, 8, 14, 15, 16, 24, 25, 26."""
    service = gw.service
    lines = gw.transcript
    #: The close codes the centre sent — collected by the raw probes.
    probe_close_codes: list[Any] = []

    # ── #7a — a hello WITHOUT the `tools` capability ────────────────────
    caps = dict(EXPECTED_SERVER_CAPABILITIES)
    caps["tools"] = False
    probe = await raw_ws_exchange(
        gw.port,
        gw.ws_path,
        enrollment_token,
        [hello_frame(request_id="probe-cap-1", capabilities=caps)],
        linger=4.0,
        heartbeat_every=0.4,
    )
    answers = parsed_frames(probe["frames"])
    errors = [a for a in answers if a.get("type") == "error"]
    error = errors[0] if errors else {}
    result.add(
        7,
        "a-hello-without-the-tools-capability-is-refused-with-CAPABILITY_MISSING",
        (error.get("error") or {}).get("code") == "CAPABILITY_MISSING"
        and probe["close_code"] is not None,
        f"error={json.dumps(error)[:220]} close_code={probe['close_code']}",
        evidence=" | ".join(probe["frames"])[:400],
    )
    probe_close_codes.append(probe["close_code"])

    # ── #7b — a hello with `notifications: false` is ACCEPTED ──────────
    probe = await raw_ws_exchange(
        gw.port,
        gw.ws_path,
        enrollment_token,
        [hello_frame(request_id="probe-cap-2", capabilities=dict(EXPECTED_SERVER_CAPABILITIES))],
        linger=4.0,
        heartbeat_every=0.4,
    )
    answers = parsed_frames(probe["frames"])
    accepted = [
        a
        for a in answers
        if a.get("type") == "response" and a.get("result", {}).get("protocol_version")
    ]
    result.add(
        7,
        "a-hello-with-notifications-false-is-accepted",
        bool(accepted),
        f"responses={len(accepted)} close_code={probe['close_code']}",
        evidence=" | ".join(probe["frames"])[:400],
    )
    probe_close_codes.append(probe["close_code"])

    # ── #8 — a hello with an unsupported version ────────────────────────
    probe = await raw_ws_exchange(
        gw.port,
        gw.ws_path,
        enrollment_token,
        [hello_frame(request_id="probe-ver-1", protocol_version="0.9")],
        linger=4.0,
        heartbeat_every=0.4,
    )
    answers = parsed_frames(probe["frames"])
    errors = [a for a in answers if a.get("type") == "error"]
    error = errors[0] if errors else {}
    data = error.get("error", {}).get("data", {}) or {}
    downgraded = [
        a
        for a in answers
        if a.get("type") == "response" and a.get("result", {}).get("protocol_version")
    ]
    result.add(
        8,
        "an-unsupported-version-is-refused-with-the-requested-and-supported-set",
        (error.get("error") or {}).get("code") == "PROTOCOL_VERSION_UNSUPPORTED"
        and data.get("requested") == "0.9"
        # The centre speaks "0.2" and "0.1" (SPEC v0.2) — the refusal
        # names the versions it DOES speak, newest first.
        and data.get("supported") == ["0.2", "0.1"]
        and probe["close_code"] is not None
        and not downgraded,
        f"error={json.dumps(error)[:260]} close_code={probe['close_code']} "
        f"silent_downgrade={bool(downgraded)}",
        evidence=" | ".join(probe["frames"])[:400],
    )

    # the probes are gone: the centre must have noticed their closes (a
    # lingering probe connection would be killed for heartbeat silence and
    # would add a SECOND HEARTBEAT_TIMEOUT frame to the transcript).
    await _wait_until(
        lambda: len(service._connections) <= 1,
        timeout=10.0,
    )

    # ── the shipped client ──────────────────────────────────────────────
    config_path = await _write_client_yaml(
        Path(gw.data_dir).parent,
        artifacts,
        gw=gw,
        enrollment_token=enrollment_token,
        listen_token=listen_token,
        listen_addr=listen_addr,
    )
    code, out, err = await asyncio.to_thread(
        go_client.run_check_config, artifacts.client_bin, config_path
    )
    result.gate(
        "client-check-config-exit-0",
        code == 0,
        f"exit={code} stdout={out.strip()[:160]} stderr={err.strip()[:300]}",
    )
    client = await _start_client(
        result, artifacts, config_path, Path(gw.data_dir).parent, box
    )

    # ── #14 — the §8.3 exclusivity ─────────────────────────────────────
    session = await service.connect(
        machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="p2-gate"
    )
    session_id = str(session.get("session_id") or "")
    begins_before = len(lines.find(direction="send", method="session.begin", type="request"))
    busy_code = ""
    try:
        await service.connect(
            machine_id=MACHINE_ID, agent_id=OTHER_AGENT_ID, reason="p2-gate-busy"
        )
        result.add(
            14,
            "a-second-connect-for-the-same-machine-is-refused-with-MACHINE_BUSY",
            False,
            "the second connect was accepted",
        )
    except Exception as exc:  # noqa: BLE001 — the refusal IS the evidence
        busy_code = tls_harness.gateway_error_code(exc)
        result.add(
            14,
            "a-second-connect-for-the-same-machine-is-refused-with-MACHINE_BUSY",
            busy_code == "MACHINE_BUSY",
            f"code={busy_code!r}",
        )
    begins_after = len(lines.find(direction="send", method="session.begin", type="request"))
    result.add(
        14,
        "the-refusal-happens-BEFORE-any-frame-reaches-the-machine",
        begins_after == begins_before == 1,
        f"session.begin frames before={begins_before} after={begins_after}",
        evidence="the transcript carries exactly ONE session.begin frame",
    )

    # ── #15 — the liveness evidence ────────────────────────────────────
    await asyncio.sleep(1.0)
    connection = service._by_machine.get(MACHINE_ID)
    pings_sent = -1
    if connection is not None:
        transport = service._connections.get(connection.connection_id)
        pings_sent = int(getattr(getattr(transport, "transport", None), "pings_sent", -1))
    result.add(
        15,
        "the-centre-sends-websocket-liveness-pings",
        pings_sent > 0,
        f"pings_sent={pings_sent}",
    )
    result.add(
        15,
        "the-client-survives-the-idle-window",
        not any(
            "the center went silent" in message for message in client.messages()
        ),
        "the client logged no 'the center went silent'",
    )

    # ── #25 — the centre never issues tools/list ───────────────────────
    sent_methods = lines.methods_sent()
    result.add(
        25,
        "the-centre-never-issues-a-tools-list",
        "tools/list" not in sent_methods,
        f"methods sent by the centre: {sent_methods}",
    )

    # ── #26 — the timeout arm ──────────────────────────────────────────
    t0 = time.monotonic()
    timeout_code = ""
    timeout_data: dict[str, Any] = {}
    os.kill(client.process.pid, signal.SIGSTOP)
    try:
        await service.command(
            agent_id=AGENT_ID, tool=SUCCESS_TOOL, arguments=COMMAND_ARGS
        )
        result.add(
            26,
            "the-centres-deadline-wins-the-timeout-race",
            False,
            "the command returned instead of timing out",
        )
    except Exception as exc:  # noqa: BLE001 — the refusal IS the evidence
        timeout_code = tls_harness.gateway_error_code(exc)
        timeout_data = tls_harness.gateway_error_data(exc)
        elapsed = time.monotonic() - t0
        result.add(
            26,
            "the-centres-deadline-wins-the-timeout-race",
            timeout_code == "TOOL_TIMEOUT"
            and timeout_data.get("method") == "tools/call"
            and timeout_data.get("timeout_ms")
            == int(CENTRE_PROFILE["MCPS_GATEWAY_COMMAND_TIMEOUT_MS"]),
            f"code={timeout_code!r} data={json.dumps(timeout_data)} "
            f"elapsed={elapsed:.3f}s "
            f"(the client's own timeout is {CLIENT_PROFILE['tool_call_timeout_ms']} ms)",
        )
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(client.process.pid, signal.SIGCONT)

    # the heartbeat cadence, MEASURED centre-side (the client's own ticker
    # is the only writer of these frames)
    heartbeats = [
        frame for frame in lines.find(direction="recv", method="heartbeat")
    ]
    gaps = [
        round(heartbeats[i].at - heartbeats[i - 1].at, 3)
        for i in range(1, len(heartbeats))
    ]
    result.observe(
        f"the heartbeat cadence observed centre-side: {len(heartbeats)} frames, "
        f"gaps={gaps} (the advertised interval is "
        f"{CENTRE_PROFILE['MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS']} ms)"
    )

    # ── #16 — the heartbeat kill ────────────────────────────────────────
    # THE MECHANISM (named): the client subprocess is SIGSTOPped, so it
    # stops heartbeating; the centre's monitor (the sweep granularity is
    # min(interval, timeout/10) = 150 ms) declares the machine dead after
    # the advertised tolerance; the client is SIGCONTed afterwards.
    old_session_id = session_id
    hb_before = len(lines.find(direction="send", type="error", error_code="HEARTBEAT_TIMEOUT"))
    os.kill(client.process.pid, signal.SIGSTOP)
    killed_at = time.monotonic()
    try:
        # The SESSION dies first: the centre releases the session manager
        # BEFORE it closes the socket.
        await _wait_until(
            lambda: service.sessions.get(old_session_id) is None,
            timeout=15.0,
            poll=0.02,
        )
    finally:
        # The client runs again: the WebSocket close handshake needs it,
        # and the centre's roster release happens AFTER that handshake
        # (the finding recorded below).
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(client.process.pid, signal.SIGCONT)
    killed_elapsed = time.monotonic() - killed_at
    # The roster's §8.3 pointer is released after the socket close — the
    # wait is for the END STATE, bounded.
    await _wait_until(
        lambda: _roster_session_id(service, MACHINE_ID) in (None, ""),
        timeout=15.0,
        poll=0.02,
    )

    hb_errors = lines.find(direction="send", type="error", error_code="HEARTBEAT_TIMEOUT")
    hb_for_client = [
        frame
        for frame in hb_errors
        if (frame.error.get("data") or {}).get("machine_id") == MACHINE_ID
    ]
    result.add(
        16,
        "the-centre-emits-exactly-one-HEARTBEAT_TIMEOUT-error-frame-without-an-id",
        len(hb_errors) == hb_before + 1
        and len(hb_for_client) == 1
        and hb_for_client[0].id is None,
        f"error_frames={len(hb_errors)} (before the kill: {hb_before}) "
        f"for {MACHINE_ID}: {len(hb_for_client)} "
        f"id={hb_for_client[0].id if hb_for_client else None!r}",
        evidence=hb_for_client[0].raw if hb_for_client else "",
    )
    silence_lines = capture.find(r"was silent for (\d+) ms")
    silence_ms = -1
    if silence_lines:
        match = re.search(r"was silent for (\d+) ms", silence_lines[-1])
        if match:
            silence_ms = int(match.group(1))
    timeout_ms = int(CENTRE_PROFILE["MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS"])
    result.add(
        16,
        "the-centre-logs-the-silence-that-killed-the-session",
        silence_ms >= timeout_ms and silence_ms <= timeout_ms * 2,
        f"silence={silence_ms} ms (the band is [{timeout_ms}, {timeout_ms * 2}])",
        evidence=silence_lines[-1] if silence_lines else "",
    )
    roster_row = {row.get("machine_id"): row for row in service.roster()}.get(
        MACHINE_ID, {}
    )
    result.add(
        16,
        "the-session-is-gone-and-the-roster-no-longer-points-at-it",
        service.sessions.get(old_session_id) is None
        # P3 hygiene: the row must EXIST and be cleared — a VANISHED row
        # answers ``None`` for session_id and would pass vacuously.
        and bool(roster_row)
        and roster_row.get("session_id") in (None, ""),
        f"sessions.get(old)={service.sessions.get(old_session_id)!r} "
        f"roster_row_present={bool(roster_row)} "
        f"roster.session_id={roster_row.get('session_id')!r} "
        f"roster.connected={roster_row.get('connected')!r} "
        f"kill_seen_after={killed_elapsed:.3f}s",
    )
    result.observe(
        "the centre releases the roster's §8.3 pointer (the read surface's "
        "'busy' flag) AFTER the awaited socket close (the roster release is "
        "the last statement of the connection teardown), so on a WEDGED peer "
        "the read surface keeps reporting the dead session as busy for the "
        "whole duration of the WebSocket close handshake. The session "
        "manager itself releases the slot first, so the §8.3 lock is never "
        "held twice."
    )
    # the re-begin is an EXPLICIT action: the centre does not auto-reopen
    hellos_before = len(client_hellos(lines))
    reconnected = await _wait_until(
        lambda: len(client_hellos(lines)) > hellos_before,
        timeout=30.0,
    )
    new_session: dict[str, Any] = {}
    new_session_error = ""
    if reconnected:
        try:
            new_session = await service.connect(
                machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="p2-rebegin"
            )
        except Exception as exc:  # noqa: BLE001 — the refusal IS the evidence
            new_session_error = f"{type(exc).__name__}: {exc}"
    result.add(
        16,
        "an-explicit-re-begin-opens-a-NEW-session",
        bool(new_session.get("session_id"))
        and str(new_session.get("session_id")) != old_session_id,
        f"old={old_session_id!r} new={new_session.get('session_id')!r} "
        f"reconnected={reconnected} error={new_session_error}",
    )
    result.observe(
        "the client DROPS the uncorrelated HEARTBEAT_TIMEOUT error (it has no "
        "pending request for it), so it never surfaces the code to the "
        "operator — while SPEC §6.1 requires an `id` on every error frame "
        "(SPEC-vs-code gap, reported)."
    )

    # ── #24 — the close code ───────────────────────────────────────────
    # the client reads the close frame only when it runs again — it was
    # SIGSTOPed during the kill — so the log is read after a short wait.
    await asyncio.sleep(1.0)
    close_records = [
        record
        for record in client.records()
        if "the center closed the connection" in str(record.get("msg", ""))
    ]
    close_codes = sorted({int(record.get("code", -1)) for record in close_records})
    observed_codes = sorted(
        {code for code in close_codes + list(probe_close_codes) if code is not None}
    )
    strict = os.environ.get("CONFORMANCE_STRICT_CLOSE_CODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    result.add(
        24,
        "every-server-initiated-close-is-1000",
        bool(observed_codes) and all(code == 1000 for code in observed_codes),
        f"close codes observed: client={close_codes} probes={probe_close_codes} "
        f"(strict={strict}) — the SPEC names NO close code anywhere",
        observed=not strict,
    )
    result.observe(
        f"the server-initiated close codes observed: {observed_codes} "
        f"(the client logged {close_codes}, the raw probes saw "
        f"{probe_close_codes}); the SPEC v0.1 names no close code at all, so "
        "the 1000 convention is an implementation choice (the gap is "
        "flagged, not failed)"
    )

    # ── the envelope deviations of the whole run (recorded, never raised) ──
    recorder = transcript.ViolationRecorder()
    recorder.observe_all(lines.frames)
    result.violations = [str(v) for v in recorder.violations]
    result.observe(f"envelope deviations recorded: {recorder.summary()}")
    for violation in recorder.violations:
        result.observe(f"deviation: {violation}")

    return client


# ── P3 — the audit DB proof + the read parity ────────────────────────────


def _pick_row(rows: Any, **criteria: Any) -> list[dict[str, Any]]:
    """The rows matching EVERY criterion by exact value."""
    return [
        row
        for row in (rows or [])
        if all(row.get(key) == value for key, value in criteria.items())
    ]


async def _phase_p3(
    result: PhaseResult,
    *,
    gw: Any,
    artifacts: go_client.GoArtifacts,
    enrollment_token: str,
    listen_token: str,
    listen_addr: str,
    read_token: str,
    fence_role: str,
    capture: LogCapture,
    box: ClientBox,
) -> go_client.GoClient:
    """Assertions 17, 18, 19, 23 — P3 is the SOLE GATE for 17-19.

    The audit proof is canary-guarded (B11, extended): the sink identity,
    the enabled flag, a canary row read back through a SECOND,
    independently constructed engine, and the flow's rows correlated by
    EXACT VALUE (machine/session/tool/outcome/args_hash) and reasoned
    from DELTAS — never absolutes.  The read parity (#23) runs on the
    SAME live daemon with AUTH ON.
    """
    service = gw.service
    lines = gw.transcript
    work_dir = Path(gw.data_dir).parent

    # ── #17 (canary set, extended) — BEFORE anything else ──────────────
    result.add(
        17,
        "the-audit-sink-writes-through-the-manager-engine",
        getattr(service._audit, "_engine", None) is gw.manager.engine,
        f"identical={getattr(service._audit, '_engine', None) is gw.manager.engine} "
        f"sink={type(service._audit).__name__}",
    )
    result.add(
        17,
        "the-audit-sink-is-enabled",
        getattr(gw.config, "audit_enabled", None) is True,
        f"audit_enabled={getattr(gw.config, 'audit_enabled', None)!r}",
    )
    canary_hash = canonical_args_hash(CANARY_ARGS)
    await asyncio.to_thread(
        tls_harness.record_audit_canary,
        service,
        agent_id="ari-canary",
        machine_id=CANARY_MACHINE_ID,
        tool="canary_tool",
        args_hash=canary_hash,
        session_id="sess_canary_p3",
        detail="p3 canary row",
    )
    independent = await asyncio.to_thread(
        tls_harness.independent_fence_engine, fence_role
    )
    try:
        canary_rows = await asyncio.to_thread(
            tls_harness.fetch_rows,
            independent,
            "SELECT * FROM mcp_gateway_audit WHERE machine_id = :m",
            {"m": CANARY_MACHINE_ID},
        )
    finally:
        await asyncio.to_thread(independent.dispose)
    result.add(
        17,
        "the-canary-row-round-trips-to-a-second-independent-engine",
        len(canary_rows) == 1
        and canary_rows[0].get("args_hash") == canary_hash,
        f"rows={len(canary_rows)} "
        f"stored={canary_rows[0].get('args_hash') if canary_rows else None!r} "
        f"recomputed={canary_hash}",
        evidence=(
            "canary row: "
            + json.dumps(canary_rows[0], sort_keys=True, default=str)
        )
        if canary_rows
        else "",
    )

    # ── the shipped client, audit lane ON (the Go hashes become ACTUAL) ──
    config_path = await _write_client_yaml(
        work_dir,
        artifacts,
        gw=gw,
        enrollment_token=enrollment_token,
        listen_token=listen_token,
        listen_addr=listen_addr,
        name="client-p3.yaml",
        audit_enabled=True,
    )
    code, out, err = await asyncio.to_thread(
        go_client.run_check_config, artifacts.client_bin, config_path
    )
    result.gate(
        "client-check-config-exit-0",
        code == 0,
        f"exit={code} stdout={out.strip()[:160]} stderr={err.strip()[:300]}",
    )
    client = await _start_client(result, artifacts, config_path, work_dir, box)

    # ── #23 — the positive control FIRST, then the Accept gotcha ───────
    # (the runner's own 401 gate has already fired; the SHAPE is pinned
    # here — the 401 body is FLAT: middleware/auth.py:596-608)
    control = await asyncio.to_thread(https_get, gw.port, "/api/mcps-gateway/audit")
    control_body = control["body"] or {}
    result.add(
        23,
        "the-401-body-is-FLAT",
        control["status"] == 401
        and control_body
        == {"error": "AUTH_REQUIRED", "message": "Authentication required"},
        f"status={control['status']} body={json.dumps(control_body)[:200]}",
    )
    # The Accept gotcha: with ``Accept: */*`` the auth middleware answers
    # the BROWSER 302 to the login page, not the machine 401 — the
    # gateway prefix is NOT in _401_HEURISTIC_PREFIXES (middleware/auth.py:84-91).
    wildcard = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/audit", accept="*/*"
    )
    location = (
        wildcard["headers"].get("Location")
        or wildcard["headers"].get("location")
        or ""
    )
    result.add(
        23,
        "no-token-with-accept-wildcard-is-a-302-to-the-login-page-not-401",
        wildcard["status"] == 302 and "/api/auth/login" in location,
        f"status={wildcard['status']} location={location!r}",
        evidence=f"body={wildcard['text'][:160]!r}",
    )

    # ── #23 — the 403 lane: a VALID token lacking mcp:read ─────────────
    # D3a consumes this pin: the 403 body is NESTED under ``detail``
    # (decorators.py:108-114) — a DIFFERENT shape from the flat 401.
    narrow_token = await asyncio.to_thread(
        tls_harness.issue_read_bearer,
        gw.engine,
        label="interop-narrow",
        scopes=("profile",),
        ttl=600,
    )
    result.gate("narrow-bearer-minted", bool(narrow_token), f"length={len(narrow_token)}")
    forbidden = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/audit", token=narrow_token
    )
    forbidden_body = forbidden["body"] or {}
    forbidden_detail = forbidden_body.get("detail")
    result.add(
        23,
        "the-403-body-is-NESTED-under-detail-with-SCOPE_INSUFFICIENT",
        forbidden["status"] == 403
        and set(forbidden_body.keys()) == {"detail"}
        and isinstance(forbidden_detail, dict)
        and forbidden_detail.get("error") == "SCOPE_INSUFFICIENT"
        and forbidden_detail.get("required") == ["mcp:read"],
        f"status={forbidden['status']} body={json.dumps(forbidden_body)[:300]}",
    )

    # ── snapshot 0 — enrolled but NOT busy (before the session) ────────
    m0 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/machines", token=read_token
    )
    m0_body = m0["body"] or {}
    m0_row = (
        _pick_row(m0_body.get("machines"), machine_id=MACHINE_ID) or [{}]
    )[0]
    s0 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/sessions", token=read_token
    )
    s0_body = s0["body"] or {}
    result.gate(
        "read-snapshot-pre-connect",
        m0["status"] == 200
        and bool(m0_row)
        and m0_row.get("busy") is False
        and m0_row.get("session_id") is None
        and isinstance(s0_body.get("sessions"), list),
        f"machines={m0['status']} busy={m0_row.get('busy')!r} "
        f"session_id={m0_row.get('session_id')!r} "
        f"sessions_is_list={isinstance(s0_body.get('sessions'), list)}",
    )

    # ── the live session ───────────────────────────────────────────────
    session = await service.connect(
        machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="p3-gate"
    )
    session_id = str(session.get("session_id") or "")
    result.gate("p3-session-open", bool(session_id), f"session_id={session_id!r}")

    m1 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/machines", token=read_token
    )
    m1_body = m1["body"] or {}
    m1_row = (
        _pick_row(m1_body.get("machines"), machine_id=MACHINE_ID) or [{}]
    )[0]
    s1 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/sessions", token=read_token
    )
    s1_body = s1["body"] or {}
    s1_row = (
        _pick_row(s1_body.get("sessions"), session_id=session_id) or [{}]
    )[0]
    child_tools = await asyncio.to_thread(child_catalog, work_dir / "child.jsonl")
    catalog_names = sorted(
        str(tool.get("name"))
        for tool in (child_tools or [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    )
    result.add(
        23,
        "the-live-machine-row-is-busy-and-names-the-session",
        m1["status"] == 200
        and m1_row.get("busy") is True
        and m1_row.get("session_id") == session_id
        and m1_row.get("agent_id") == AGENT_ID
        and m1_row.get("online") is True
        and m1_row.get("connected") is True,
        f"status={m1['status']} busy={m1_row.get('busy')!r} "
        f"session_id={m1_row.get('session_id')!r} "
        f"agent_id={m1_row.get('agent_id')!r} "
        f"online={m1_row.get('online')!r} connected={m1_row.get('connected')!r}",
        evidence=f"row={json.dumps(m1_row, sort_keys=True)[:300]}",
    )
    result.add(
        23,
        "the-live-session-row-is-live-locked-and-carries-the-REAL-catalog",
        s1["status"] == 200
        and bool(s1_row)
        and s1_row.get("state") == "live"
        and s1_row.get("machine_locked") is True
        and s1_row.get("machine_id") == MACHINE_ID
        and s1_row.get("agent_id") == AGENT_ID
        and bool(catalog_names)
        and sorted(s1_row.get("tools") or []) == catalog_names,
        f"status={s1['status']} state={s1_row.get('state')!r} "
        f"machine_locked={s1_row.get('machine_locked')!r} "
        f"tools={sorted(s1_row.get('tools') or [])} "
        f"child_catalog={catalog_names}",
    )

    # ── the flow: one ok, one error, three markers, one timeout ────────
    ok_result = await service.command(
        agent_id=AGENT_ID, tool=SUCCESS_TOOL, arguments=COMMAND_ARGS
    )
    result.gate(
        "p3-ok-command-returned",
        isinstance(ok_result, dict) and isinstance(ok_result.get("content"), list),
        f"result_keys={sorted(ok_result) if isinstance(ok_result, dict) else None}",
    )

    echo_error_code = ""
    try:
        await service.command(
            agent_id=AGENT_ID, tool=FAILING_TOOL, arguments=FAILING_ARGS
        )
        result.gate("p3-error-command-refused", False, "echo_tool returned instead of erroring")
    except Exception as exc:  # noqa: BLE001 — the refusal IS the evidence
        echo_error_code = tls_harness.gateway_error_code(exc)
        result.gate(
            "p3-error-command-refused", bool(echo_error_code), f"code={echo_error_code!r}"
        )

    for marker_name, marker_args in DIVERGENCE_MARKERS:
        try:
            await service.command(
                agent_id=AGENT_ID, tool=FAILING_TOOL, arguments=marker_args
            )
            result.gate(
                f"p3-marker-{marker_name}-refused",
                False,
                "echo_tool returned instead of erroring",
            )
        except Exception as exc:  # noqa: BLE001
            result.gate(
                f"p3-marker-{marker_name}-refused",
                bool(tls_harness.gateway_error_code(exc)),
                f"code={tls_harness.gateway_error_code(exc)!r}",
            )

    # the timeout row — cheap under the compressed profile (1200 ms)
    timeout_code = ""
    os.kill(client.process.pid, signal.SIGSTOP)
    try:
        await service.command(
            agent_id=AGENT_ID, tool=SUCCESS_TOOL, arguments=COMMAND_ARGS
        )
        result.gate(
            "p3-timeout-command-timed-out",
            False,
            "the command returned instead of timing out",
        )
    except Exception as exc:  # noqa: BLE001
        timeout_code = tls_harness.gateway_error_code(exc)
        result.gate(
            "p3-timeout-command-timed-out",
            timeout_code == "TOOL_TIMEOUT",
            f"code={timeout_code!r}",
        )
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(client.process.pid, signal.SIGCONT)
    # let the client drain the frozen frame and resume heartbeats
    await asyncio.sleep(1.5)
    session_survived = service.sessions.get(session_id) is not None
    result.gate(
        "p3-session-survived-the-request-timeout",
        session_survived,
        f"session_id={session_id!r} still_live={session_survived}",
    )

    # ── snapshot 2 — the audit rows are visible on the read surface ────
    a2 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/audit", token=read_token
    )
    a2_body = a2["body"] or {}
    a2_rows = a2_body.get("audit") or []

    # ── session.end, then the post-end snapshot ─────────────────────────
    end_result = await service.disconnect(agent_id=AGENT_ID)
    result.gate(
        "p3-session-closed", bool(end_result.get("ended")), f"ended={end_result.get('ended')!r}"
    )
    await _wait_until(
        lambda: _roster_session_id(service, MACHINE_ID) in (None, ""),
        timeout=10.0,
        poll=0.05,
    )
    m3 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/machines", token=read_token
    )
    m3_body = m3["body"] or {}
    m3_row = (
        _pick_row(m3_body.get("machines"), machine_id=MACHINE_ID) or [{}]
    )[0]
    s3 = await asyncio.to_thread(
        https_get, gw.port, "/api/mcps-gateway/sessions", token=read_token
    )
    s3_body = s3["body"] or {}
    result.add(
        23,
        "the-busy-flag-transitions-true-then-false-across-begin-end",
        m0_row.get("busy") is False
        and m1_row.get("busy") is True
        and m3_row.get("busy") is False
        and m3_row.get("session_id") is None,
        f"pre={m0_row.get('busy')!r} live={m1_row.get('busy')!r} "
        f"post={m3_row.get('busy')!r} post_session_id={m3_row.get('session_id')!r}",
    )
    result.add(
        23,
        "the-session-disappears-from-the-session-read-after-the-end",
        s3["status"] == 200
        and isinstance(s3_body.get("sessions"), list)
        and not _pick_row(s3_body.get("sessions"), session_id=session_id),
        f"sessions_after_end={len(s3_body.get('sessions') or [])} "
        f"matching={len(_pick_row(s3_body.get('sessions'), session_id=session_id))}",
    )

    # ── #23 — the wrapper shapes are EXACT (post-D2: + degraded/error) ─
    result.add(
        23,
        "the-machines-wrapper-is-exactly-machines-count-gateway_enabled-degraded-error",
        m3["status"] == 200
        and frozenset(m3_body.keys()) == MACHINES_WRAPPER_KEYS
        and m3_body.get("count") == len(m3_body.get("machines") or [])
        # D2: the VALUE is pinned, not the key's presence — an
        # in-memory lane must never report degraded.
        and m3_body.get("degraded") is False,
        f"keys={sorted(m3_body.keys())} count={m3_body.get('count')} "
        f"page={len(m3_body.get('machines') or [])} "
        f"degraded={m3_body.get('degraded')!r}",
    )
    result.add(
        23,
        "the-sessions-wrapper-is-exactly-sessions-count-gateway_enabled-degraded-error",
        s3["status"] == 200
        and frozenset(s3_body.keys()) == SESSIONS_WRAPPER_KEYS
        and s3_body.get("count") == len(s3_body.get("sessions") or [])
        and s3_body.get("degraded") is False,
        f"keys={sorted(s3_body.keys())} count={s3_body.get('count')} "
        f"page={len(s3_body.get('sessions') or [])} "
        f"degraded={s3_body.get('degraded')!r}",
    )
    result.add(
        23,
        "the-audit-wrapper-is-exactly-audit-count-limit-offset-gateway_enabled-degraded-error",
        a2["status"] == 200
        and frozenset(a2_body.keys()) == AUDIT_WRAPPER_KEYS
        and a2_body.get("count") == len(a2_rows)
        and a2_body.get("degraded") is False,
        f"keys={sorted(a2_body.keys())} count={a2_body.get('count')} "
        f"page={len(a2_rows)} degraded={a2_body.get('degraded')!r}",
    )

    # ── #23 — count is the PAGE LENGTH, not the ledger total ───────────
    page1 = await asyncio.to_thread(
        https_get,
        gw.port,
        "/api/mcps-gateway/audit?limit=1&offset=0",
        token=read_token,
    )
    page1_body = page1["body"] or {}
    result.add(
        23,
        "the-audit-count-is-the-PAGE-LENGTH-not-the-ledger-total",
        page1["status"] == 200
        and page1_body.get("count") == 1
        and len(page1_body.get("audit") or []) == 1
        and len(a2_rows) > 1,
        f"count(limit=1)={page1_body.get('count')} "
        f"page={len(page1_body.get('audit') or [])} "
        f"rows_on_the_default_page={len(a2_rows)}",
        evidence=(
            "the FE footgun: 'The length of THIS PAGE — NOT the ledger "
            "total' (mcps-gateway.model.ts); the BE honours it"
        ),
    )
    result.add(
        23,
        "the-audit-default-page-is-limit-50-offset-0",
        a2_body.get("limit") == 50 and a2_body.get("offset") == 0,
        f"limit={a2_body.get('limit')} offset={a2_body.get('offset')}",
    )
    page2 = await asyncio.to_thread(
        https_get,
        gw.port,
        "/api/mcps-gateway/audit?limit=1&offset=1",
        token=read_token,
    )
    page2_body = page2["body"] or {}
    result.add(
        23,
        "the-audit-echoes-the-applied-limit-and-offset",
        page2["status"] == 200
        and page2_body.get("limit") == 1
        and page2_body.get("offset") == 1,
        f"limit={page2_body.get('limit')} offset={page2_body.get('offset')}",
    )

    # ── #23 — the pagination bounds are 422 (1 <= limit <= 200, offset >= 0) ──
    bounds: dict[str, tuple[int, bool]] = {}
    for query in ("limit=0", "limit=201", "offset=-1"):
        response = await asyncio.to_thread(
            https_get, gw.port, f"/api/mcps-gateway/audit?{query}", token=read_token
        )
        body = response["body"] or {}
        bounds[query] = (
            response["status"],
            isinstance(body.get("detail"), list),
        )
    result.add(
        23,
        "the-audit-pagination-bounds-answer-422",
        all(status == 422 and detail_is_list for status, detail_is_list in bounds.values()),
        json.dumps({key: value[0] for key, value in bounds.items()}),
    )

    # ── #23 — machines/sessions take NO query params ───────────────────
    ignored: dict[str, int] = {}
    shape_ok = True
    for lane, keys in (("machines", MACHINES_WRAPPER_KEYS), ("sessions", SESSIONS_WRAPPER_KEYS)):
        response = await asyncio.to_thread(
            https_get,
            gw.port,
            f"/api/mcps-gateway/{lane}?limit=1&offset=1",
            token=read_token,
        )
        ignored[lane] = response["status"]
        if response["status"] != 200 or frozenset((response["body"] or {}).keys()) != keys:
            shape_ok = False
    result.add(
        23,
        "machines-and-sessions-take-no-query-params",
        shape_ok and all(status == 200 for status in ignored.values()),
        f"statuses={ignored} (extra params are IGNORED — the endpoints "
        "declare none, so no 422 and no shape change)",
    )

    # ── #23 — the 17-18 rows are visible in /audit, keys ⊇ FE fields ──
    ok_hash = canonical_args_hash(COMMAND_ARGS)
    wire_ok_rows = _pick_row(
        a2_rows,
        machine_id=MACHINE_ID,
        session_id=session_id,
        tool=SUCCESS_TOOL,
        outcome="ok",
        args_hash=ok_hash,
    )
    result.add(
        23,
        "the-17-18-audit-rows-are-visible-in-the-audit-read",
        a2["status"] == 200 and len(wire_ok_rows) >= 1,
        f"matching_ok_rows={len(wire_ok_rows)} of {len(a2_rows)} wire rows",
        evidence=(
            "wire ok row: "
            + json.dumps(wire_ok_rows[0], sort_keys=True)
            if wire_ok_rows
            else ""
        ),
    )
    machines_superset = bool(m1_row) and set(MACHINE_FIELDS) <= set(m1_row.keys())
    sessions_superset = bool(s1_row) and set(SESSION_FIELDS) <= set(s1_row.keys())
    audit_superset = bool(wire_ok_rows) and set(AUDIT_FIELDS) <= set(wire_ok_rows[0].keys())
    result.add(
        23,
        "be-keys-are-a-superset-of-the-fe-model-fields-on-all-three-shapes",
        machines_superset and sessions_superset and audit_superset,
        f"machines={machines_superset} sessions={sessions_superset} "
        f"audit={audit_superset}",
    )
    array_keys_always_present = all(
        isinstance(body.get(key), list)
        for body, key in (
            (m0_body, "machines"),
            (m1_body, "machines"),
            (m3_body, "machines"),
            (s0_body, "sessions"),
            (s1_body, "sessions"),
            (s3_body, "sessions"),
            (a2_body, "audit"),
            (page1_body, "audit"),
            (page2_body, "audit"),
        )
    )
    result.add(
        23,
        "the-array-keys-are-always-present-so-the-fe-defaults-are-never-load-bearing",
        array_keys_always_present,
        "machines/sessions/audit keys were lists in every response of "
        "the run (mcps-gateway.service.ts `?? []` never fires)",
    )

    # ── #17/#18 — the ledger in the REAL DB, read INDEPENDENTLY ────────
    independent = await asyncio.to_thread(
        tls_harness.independent_fence_engine, fence_role
    )
    try:
        rows = await asyncio.to_thread(
            tls_harness.fetch_rows,
            independent,
            "SELECT * FROM mcp_gateway_audit WHERE machine_id = :m "
            "ORDER BY created_at, id",
            {"m": MACHINE_ID},
        )
    finally:
        await asyncio.to_thread(independent.dispose)
    expected_rows = 2 + len(DIVERGENCE_MARKERS) + 1 + 1
    # ok + error + markers + timeout + the ``session.end`` row of the
    # explicit end. The ledger carries the SESSION-LIFECYCLE rows too —
    # the expiry hardening added them, because the ledger is where the
    # operator asks "why did this session not end?".
    result.add(
        17,
        "the-ledger-grew-by-exactly-the-six-commands-of-the-flow-plus-the-session-end-row",
        len(rows) == expected_rows,
        f"delta={len(rows)} (pre-flow 0 is gated by the runner's "
        f"audit-pre-flow-count-zero; expected={expected_rows} = six command "
        f"rows + the session.end row of the explicit end)",
    )

    # The SESSION-END surface wrote a row about THIS session. The
    # args_hash is the discriminator (it hashes {"session_id": ...}), and
    # the OUTCOME is deliberately NOT asserted: which end path ran
    # (the explicit end, or the late end over a tombstone when the
    # session died first) is the subject of the "p3-session-closed" gate
    # above — this row only proves the ledger carries the surface.
    session_end_rows = _pick_row(rows, tool="session.end")
    result.add(
        17,
        "the-session-end-row-is-in-the-ledger-with-the-hash-of-its-own-params",
        len(session_end_rows) == 1
        and session_end_rows[0].get("args_hash")
        == canonical_args_hash({"session_id": session_id})
        and session_end_rows[0].get("session_id") == session_id,
        f"rows={len(session_end_rows)} "
        f"row={json.dumps(session_end_rows[0], sort_keys=True, default=str)[:300] if session_end_rows else None} "
        f"recomputed={canonical_args_hash({'session_id': session_id})}",
    )

    failing_hash = canonical_args_hash(FAILING_ARGS)
    ok_rows = _pick_row(
        rows,
        session_id=session_id,
        tool=SUCCESS_TOOL,
        outcome="ok",
        args_hash=ok_hash,
    )
    result.add(
        17,
        "the-ok-row-correlates-by-EXACT-VALUE",
        len(ok_rows) == 1
        and ok_rows[0].get("machine_id") == MACHINE_ID
        and ok_rows[0].get("agent_id") == AGENT_ID,
        f"rows={len(ok_rows)} "
        f"args_hash={ok_rows[0].get('args_hash') if ok_rows else None!r} "
        f"recomputed={ok_hash}",
        evidence=(
            "ok row: " + json.dumps(ok_rows[0], sort_keys=True, default=str)
            if ok_rows
            else ""
        ),
    )
    error_rows = _pick_row(
        rows,
        session_id=session_id,
        tool=FAILING_TOOL,
        outcome="error",
        args_hash=failing_hash,
    )
    result.add(
        17,
        "the-error-row-correlates-by-EXACT-VALUE-and-carries-an-error-code",
        len(error_rows) == 1
        # P4 tightening (carry-forward): the code is pinned to the
        # TYPED code the centre actually answered on the raise — not
        # merely "is a non-empty string" (the P3 posture).
        and bool(echo_error_code)
        and error_rows[0].get("error_code") == echo_error_code,
        f"rows={len(error_rows)} "
        f"error_code={error_rows[0].get('error_code') if error_rows else None!r} "
        f"typed_client_code={echo_error_code!r} "
        f"args_hash={error_rows[0].get('args_hash') if error_rows else None!r} "
        f"recomputed={failing_hash}",
        evidence=(
            "error row: " + json.dumps(error_rows[0], sort_keys=True, default=str)
            if error_rows
            else ""
        ),
    )
    timeout_rows = _pick_row(
        rows,
        session_id=session_id,
        tool=SUCCESS_TOOL,
        outcome="timeout",
        error_code="TOOL_TIMEOUT",
    )
    result.add(
        17,
        "the-timeout-row-correlates-by-EXACT-VALUE",
        len(timeout_rows) == 1
        and timeout_rows[0].get("args_hash") == ok_hash
        # P4 tightening (carry-forward): the timeout row's code is
        # pinned by VALUE (the pick filters on it, but the assertion
        # never did).
        and timeout_rows[0].get("error_code") == "TOOL_TIMEOUT",
        f"rows={len(timeout_rows)} "
        f"error_code={timeout_rows[0].get('error_code') if timeout_rows else None!r} "
        f"args_hash={timeout_rows[0].get('args_hash') if timeout_rows else None!r}",
    )

    # §8.4 — the field presence per row (+ error_code where applicable)
    presence_fields = (
        "created_at",
        "session_id",
        "machine_id",
        "agent_id",
        "tool",
        "args_hash",
        "outcome",
    )
    missing_presence = [
        (index, field)
        for index, row in enumerate(rows)
        for field in presence_fields
        if row.get(field) in (None, "")
    ]
    result.add(
        17,
        "every-row-carries-the-8.4-field-set",
        not missing_presence,
        f"missing={missing_presence[:6]} rows={len(rows)}",
    )
    vocab_ok = {row.get("outcome") for row in rows} <= OUTCOME_VOCABULARY
    result.add(
        17,
        "the-outcomes-stay-within-the-8.4-vocabulary",
        vocab_ok,
        f"outcomes={sorted(str(row.get('outcome')) for row in rows)}",
    )
    row_dump = json.dumps(rows, sort_keys=True, default=str)
    leaked_password = FAILING_ARGS["password"] in row_dump
    leaked_args = json.dumps(COMMAND_ARGS, sort_keys=True) in row_dump
    result.add(
        17,
        "no-column-carries-the-raw-arguments",
        not leaked_password and not leaked_args,
        f"password_leaked={leaked_password} command_args_leaked={leaked_args} "
        "(§8.4: the arguments are hashed, never stored)",
    )

    # the plan's full column list, reconciled against the actual row
    if rows:
        observed_columns = sorted(rows[0].keys())
        result.observe(
            "the plan #17 column list reconciled: observed DB columns "
            f"{observed_columns}; the plan names agent_id, instance_id, "
            "machine_id, tool, args_hash, outcome, session_id, error_code, "
            "detail, created_at as the storage shape — every one present"
            + (
                "; instance_id/detail are NULL on these rows (the flow "
                "passes no instance_id; the centre writes a FIXED detail "
                "classification, empty-rendered as NULL)"
                if rows[0].get("instance_id") is None and rows[0].get("detail") is None
                else f"; instance_id={rows[0].get('instance_id')!r} detail={rows[0].get('detail')!r}"
            )
        )

    # ── #18 — the recomputation is INDEPENDENT (shasum cross-check) ────
    command_payload = json.dumps(
        COMMAND_ARGS, sort_keys=True, separators=(",", ":"), default=str
    )
    shasum_hash = await asyncio.to_thread(shasum_sha256, command_payload)
    result.add(
        18,
        "the-args-hash-matches-a-second-non-python-implementation",
        shasum_hash == ok_hash and len(ok_rows) == 1
        and ok_rows[0].get("args_hash") == ok_hash,
        f"python={ok_hash} shasum={shasum_hash} "
        f"stored={ok_rows[0].get('args_hash') if ok_rows else None!r}",
        evidence=f"payload={command_payload!r} | shasum -a 256",
    )
    failing_payload = json.dumps(
        FAILING_ARGS, sort_keys=True, separators=(",", ":"), default=str
    )
    failing_shasum = await asyncio.to_thread(shasum_sha256, failing_payload)
    result.add(
        18,
        "the-error-rows-hash-also-matches-the-recomputation",
        failing_shasum == failing_hash and len(error_rows) == 1
        and error_rows[0].get("args_hash") == failing_hash,
        f"python={failing_hash} shasum={failing_shasum} "
        f"stored={error_rows[0].get('args_hash') if error_rows else None!r}",
    )

    # ── #19 — the three EXPECTED-DIVERGENCE markers, each INDEPENDENT ──
    # The Go side is ACTUAL: the P3 client config turns the client's own
    # audit lane on, so the client's jsonl carries the Go-computed hash
    # of every forwarded call.  The documented derivation
    # (internal/audit/audit.go canonical()) is cross-checked against it.
    client_audit = await asyncio.to_thread(
        go_client.read_client_audit_log, work_dir / "client-audit.jsonl"
    )
    # the jsonl is APPEND-ONLY and survives runs: correlate by THIS run's
    # session id (the client stamps the remote-hands session on every
    # forwarded record — client.go:1244-1255).
    session_audit = [
        record
        for record in client_audit
        if record.get("session_id") == session_id
    ]
    go_echo_error_rows = [
        record
        for record in session_audit
        if record.get("tool") == FAILING_TOOL and record.get("outcome") == "error"
    ]
    result.gate(
        "client-audit-lane-captured-the-flow",
        len(go_echo_error_rows) == 1 + len(DIVERGENCE_MARKERS),
        f"go_records_total={len(client_audit)} "
        f"this_session={len(session_audit)} "
        f"echo_error_records={len(go_echo_error_rows)} "
        f"(the failing call + {len(DIVERGENCE_MARKERS)} markers)",
    )
    result.observe(
        "the client's audit jsonl is APPEND-ONLY across runs (observed: "
        f"{len(client_audit)} records accumulated over the P3 attempts, "
        "only the session filter isolates this run's six) — an operator "
        "note for the kit, not a defect."
    )
    result.observe(
        "the Go-side hashes of assertion 19 are ACTUAL: the P3 client "
        "config sets audit.enabled=true, and the shipped client writes "
        "one record per forwarded call with audit.HashArgs "
        "(internal/remote/client.go:1244-1255, internal/audit/audit.go). "
        "No derivation fallback was needed."
    )
    # the client records the calls in order: [ok, failing, m1, m2, m3, …]
    marker_go_rows = go_echo_error_rows[1 : 1 + len(DIVERGENCE_MARKERS)]
    for (marker_name, marker_args), go_row in zip(DIVERGENCE_MARKERS, marker_go_rows):
        python_hash = canonical_args_hash(marker_args)
        derived_go_hash = go_canonical_hash_derived(marker_args)
        actual_go_hash = str(go_row.get("args_hash") or "")
        result.add(
            19,
            f"marker-{marker_name}-the-python-and-go-args-hashes-DIFFER",
            python_hash != actual_go_hash,
            f"python={python_hash} go(actual)={actual_go_hash}",
            evidence=(
                "EXPECTED DIVERGENCE — if this fails, Go was aligned: "
                "update the SPEC note and drop the marker"
            ),
        )
        result.gate(
            f"go-derivation-faithful-{marker_name}",
            derived_go_hash == actual_go_hash,
            f"derived={derived_go_hash} actual={actual_go_hash} "
            "(the audit.go transcription is cross-checked against the "
            "client's own record)",
        )
        # P4 tightening (carry-forward): each marker's DB audit row is
        # correlated BY VALUE and pinned to the TYPED error code —
        # the 3 marker rows previously had no error_code pin at all.
        marker_db_rows = _pick_row(
            rows,
            session_id=session_id,
            tool=FAILING_TOOL,
            outcome="error",
            args_hash=python_hash,
        )
        result.add(
            19,
            f"marker-{marker_name}-the-db-row-carries-the-TYPED-error-code",
            len(marker_db_rows) == 1
            and bool(echo_error_code)
            and marker_db_rows[0].get("error_code") == echo_error_code,
            f"rows={len(marker_db_rows)} "
            f"error_code={marker_db_rows[0].get('error_code') if marker_db_rows else None!r} "
            f"typed_client_code={echo_error_code!r} "
            f"args_hash={python_hash}",
        )

    # ── the rulings this phase records (they bind later phases) ────────
    result.observe(
        "RULING G1 (OPEN-SPEC): the centre's HEARTBEAT_TIMEOUT error frame "
        "carries NO id (pinned by P2 #16a) while SPEC §6.1 requires an id "
        "on every error — the client DROPS uncorrelated errors, so the "
        "machine can never see why it was killed.  Owner: the mcps_client "
        "SPEC owner; options: amend §6.1 for unsolicited errors, or carry "
        "an id.  The conformance kit (P5) pins the CURRENT behaviour "
        "(error frame, no id) with a marker comment pointing at G1.  The "
        "daemon is NOT altered."
    )
    result.observe(
        "RULING (roster-release ordering, CONFIRMED for P4): the centre "
        "releases the roster's §8.3 pointer AFTER the awaited socket "
        "close (the P2 §7.1 finding) — on a wedged peer the read surface "
        "keeps reporting the dead session as busy.  P4 owns the one-line "
        "fix (release the roster pointer BEFORE the awaited close); P2 "
        "#16c's honest failure is the regression pin; the defect "
        "mechanics are cited in the P4 commit message.  P3 records the "
        "disposition only."
    )

    # ── the envelope deviations of the whole run (recorded, never raised) ──
    recorder = transcript.ViolationRecorder()
    recorder.observe_all(lines.frames)
    result.violations = [str(v) for v in recorder.violations]
    result.observe(f"envelope deviations recorded: {recorder.summary()}")

    return client


# ── the runner ──────────────────────────────────────────────────────────


async def run_phase(
    phase: str,
    work_dir: Path,
    *,
    postgres_user: str = "",
) -> PhaseResult:
    """Run ONE phase of the gate against a freshly booted pair."""
    result = PhaseResult(phase=phase)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    fence_role = tls_harness.resolve_fence_role(postgres_user)
    root = logging.getLogger()
    capture = LogCapture()
    root.addHandler(capture)
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    box = ClientBox()
    client: go_client.GoClient | None = None
    client_pgid: int | None = None
    gw: Any = None
    baseline_listeners: list[str] = []
    stop_report: dict[str, Any] = {}
    try:
        # ── 0. the Go build (offline; nothing lands in the read-only repo) ──
        t0 = time.monotonic()
        artifacts = await asyncio.to_thread(go_client.build_go_artifacts)
        result.timings["go_build_s"] = time.monotonic() - t0
        result.gate(
            "go-build-offline",
            artifacts.client_bin.exists() and artifacts.stub_child_bin.exists(),
            f"client={artifacts.client_bin} stub={artifacts.stub_child_bin}",
        )

        enrollment_token = tls_harness.hex_token()
        listen_token = tls_harness.hex_token()
        listen_addr = f"127.0.0.1:{tls_harness.free_tcp_port()}"

        extra_env = dict(CENTRE_PROFILE)
        extra_env.update(AUTH_PROFILE)
        # BOTH machine ids are enrolled: the probes need one of their own
        # (a second connection for the SAME machine id would supersede the
        # Go client's connection).
        extra_env["MCPS_GATEWAY_ALLOWED_MACHINES"] = f"{MACHINE_ID},{PROBE_MACHINE_ID}"

        t0 = time.monotonic()
        async with tls_harness.boot_tls_gateway(
            work_dir,
            tokens=(enrollment_token,),
            machine_id=MACHINE_ID,
            postgres_user=fence_role,
            extra_env=extra_env,
            heartbeat_interval_ms=int(CENTRE_PROFILE["MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS"]),
            heartbeat_timeout_ms=int(CENTRE_PROFILE["MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS"]),
            client_dead_detect_ms=int(CENTRE_PROFILE["MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS"]),
            command_timeout_ms=int(CENTRE_PROFILE["MCPS_GATEWAY_COMMAND_TIMEOUT_MS"]),
        ) as gw:
            result.timings["boot_s"] = time.monotonic() - t0
            result.evidence.append(f"gateway ws url: {gw.ws_url}")
            baseline_listeners = [
                line
                for line in (getattr(gw, "listener_baseline", []) or [])
                if f":{gw.port} " not in line
            ]
            result.gate(
                "tls-route-registered",
                gw.ws_path in gw.ws_route_paths,
                f"routes={gw.ws_route_paths[:8]}",
            )
            result.gate(
                "engine-is-postgres-fence",
                gw.engine.dialect.name == "postgresql"
                and tls_harness.FENCE_DB_NAME in str(gw.engine.url),
                f"dialect={gw.engine.dialect.name} url={gw.engine.url}",
            )
            result.gate(
                "audit-sink-is-db-repository",
                type(gw.service._audit).__name__ == "McpGatewayAuditRepository",
                f"sink={type(gw.service._audit).__name__}",
            )
            result.gate(
                "auth-is-on",
                getattr(gw.app.state, "auth_config", None) is not None,
                "app.state.auth_config is set — the read lane runs with AUTH ON",
            )

            job_baseline = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queue_items"
            )

            # The ledger is the evidence: every attempt starts from an
            # empty one, so a row of a PREVIOUS attempt can never be
            # mistaken for a row of THIS one.  The fence database is
            # asserted by the helper itself.
            deleted = await asyncio.to_thread(
                tls_harness.reset_audit_table, gw.engine
            )
            result.evidence.append(
                f"fence ledger rows deleted before the attempt: {deleted}"
            )
            pre_flow = await asyncio.to_thread(
                tls_harness.rows_in,
                gw.engine,
                "mcp_gateway_audit",
                "machine_id = :m",
                {"m": MACHINE_ID},
            )
            result.gate(
                "audit-pre-flow-count-zero",
                pre_flow == 0,
                f"count={pre_flow}",
            )

            # ── the read-parity positive control (B12) ──
            # BEFORE the happy path: a read surface that answers 200
            # without a credential would vacate the whole lane.
            control = await asyncio.to_thread(
                https_get, gw.port, "/api/mcps-gateway/audit"
            )
            result.gate(
                "read-surface-401-without-token",
                control["status"] == 401,
                f"status={control['status']} body={control['text'][:120]!r}",
            )
            read_token = await asyncio.to_thread(
                tls_harness.issue_read_bearer,
                gw.engine,
                label="interop-read",
                scopes=("mcp:read",),
                ttl=3600,
            )
            result.gate("read-bearer-minted", bool(read_token), f"length={len(read_token)}")

            if phase == "p1":
                client = await _phase_p1(
                    result,
                    gw=gw,
                    artifacts=artifacts,
                    enrollment_token=enrollment_token,
                    listen_token=listen_token,
                    listen_addr=listen_addr,
                    read_token=read_token,
                    capture=capture,
                    box=box,
                )
            elif phase == "p2":
                client = await _phase_p2(
                    result,
                    gw=gw,
                    artifacts=artifacts,
                    enrollment_token=enrollment_token,
                    listen_token=listen_token,
                    listen_addr=listen_addr,
                    read_token=read_token,
                    capture=capture,
                    box=box,
                )
            else:
                client = await _phase_p3(
                    result,
                    gw=gw,
                    artifacts=artifacts,
                    enrollment_token=enrollment_token,
                    listen_token=listen_token,
                    listen_addr=listen_addr,
                    read_token=read_token,
                    fence_role=fence_role,
                    capture=capture,
                    box=box,
                )
            client = box.client
            if client is not None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    client_pgid = os.getpgid(client.process.pid)

            # ── the hygiene of the lane (R-23) ──
            job_count = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queue_items"
            )
            result.gate(
                "job-queue-items-empty",
                job_count == 0 and job_count == job_baseline,
                f"count={job_count} baseline={job_baseline}",
            )
            plane_ok, plane_detail = await asyncio.to_thread(
                tls_harness.plane_switches_effective
            )
            result.gate("plane-kill-switches-effective", plane_ok, plane_detail)
            result.gate(
                "no-audit-write-failure",
                not capture.contains("audit row could not be written"),
                "no audit write failure was logged",
            )
            result.gate(
                "audit-failures-zero",
                int(getattr(gw.service, "audit_failures", 0)) == 0,
                f"audit_failures={getattr(gw.service, 'audit_failures', None)}",
            )

            result.evidence.append("centre transcript:\n" + gw.transcript.render(400))
            if client is not None:
                result.evidence.append(
                    "client log tail:\n" + "\n".join(client.snapshot()[-60:])
                )

        result.gate(
            "server-task-completed",
            bool(getattr(gw, "server_task_completed", False)),
            "the uvicorn server task finished cleanly"
            if getattr(gw, "server_task_completed", False)
            else (
                "the uvicorn server task did not finish cleanly: "
                f"{getattr(gw, 'server_task_error', '') or 'no exception recorded'}"
            ),
        )
        result.timings["server_shutdown_s"] = float(getattr(gw, "shutdown_seconds", 0.0))
    except Exception as exc:  # noqa: BLE001 — the failure IS the deliverable
        result.gate("gate-run", False, f"{type(exc).__name__}: {exc}")
        import traceback

        result.mismatches.append(traceback.format_exc())
    finally:
        if client is not None:
            stop_report = await asyncio.to_thread(client.stop)
        leftover = await asyncio.to_thread(go_client.stub_child_pids)
        result.gate("no-orphan-stub-child", not leftover, f"pids={leftover}")
        result.gate(
            "client-process-stopped",
            client is None or client.process.poll() is not None,
            f"exit={client.process.poll() if client else None}",
        )
        result.gate(
            "no-orphan-client-process",
            not _process_group_alive(client_pgid),
            f"pgid={client_pgid} pid={client.process.pid if client else None}",
        )
        # nothing may listen
        await asyncio.sleep(0.3)
        remaining = await asyncio.to_thread(tls_harness.listeners_owned_by, os.getpid())
        new_listeners = [
            line for line in remaining if line not in set(baseline_listeners)
        ]
        result.gate(
            "no-listener-left",
            not new_listeners,
            f"remaining={remaining} baseline={baseline_listeners}",
        )
        if gw is not None:
            listening = await asyncio.to_thread(
                harness.port_is_listening, gw.host, gw.port
            )
            result.gate(
                "gateway-port-closed",
                not listening,
                f"port={gw.port} listening={listening}",
            )
        result.evidence.append(
            "lsof listeners after the run:\n" + ("\n".join(remaining) or "(none)")
        )
        result.evidence.append(
            "client stop report: " + json.dumps(stop_report, default=str)
        )
        root.removeHandler(capture)
        root.setLevel(previous_level)

    return result


def render_report(result: PhaseResult) -> str:
    """The human-readable report of one phase."""
    lines = [f"=== Phase D1 {result.phase.upper()} GATE — {result.verdict} ==="]
    for assertion in result.assertions:
        lines.append(assertion.line())
    lines.append("--- gates (lane hygiene) ---")
    for gate in result.gates:
        lines.append(gate.line())
    lines.append("--- timings (s) ---")
    for key, value in result.timings.items():
        lines.append(f"  {key}: {value:.3f}")
    lines.append("--- observations (recorded, never gated) ---")
    for observation in result.observations:
        lines.append(f"  * {observation}")
    if result.violations:
        lines.append("--- envelope deviations ---")
        for violation in result.violations:
            lines.append(f"  {violation}")
    if result.mismatches:
        lines.append("--- mismatch ledger ---")
        for item in result.mismatches:
            lines.append(item)
    if result.evidence:
        lines.append("--- evidence ---")
        lines.extend(result.evidence)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """The runner entry point (its own process)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    phase = "p1"
    work_root = Path("/tmp/mcps-d1/real-pair")
    postgres_user = os.environ.get("POSTGRES_USER", "") or ""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--phase":
            index += 1
            phase = argv[index]
        elif token == "--work-dir":
            index += 1
            work_root = Path(argv[index])
        elif token == "--postgres-user":
            index += 1
            postgres_user = argv[index]
        index += 1
    if phase not in ("p1", "p2", "p3"):
        print(f"unknown phase {phase!r}", file=sys.stderr)
        return 2
    work_dir = work_root / phase
    result = asyncio.run(
        run_phase(phase, work_dir, postgres_user=postgres_user)
    )
    print(render_report(result), flush=True)
    print(f"{RESULT_MARKER} {json.dumps(result.as_dict())}", flush=True)
    return 0 if result.ok else 1


# ── the pytest wrapper ─────────────────────────────────────────────────

import pytest  # noqa: E402  (the wrapper only needs pytest)

pytestmark = pytest.mark.integration

#: The role the FENCE database is reached with — the shell user, never
#: the ambient (production) role.
FENCE_ROLE = tls_harness.resolve_fence_role()

#: The wrapper's own budget (the outer ``timeout 300`` is the second layer).
GATE_TIMEOUT_SECONDS = 280


def _run_phase(phase: str) -> tuple[int, str, dict[str, Any]]:
    """Run ONE phase of the gate in its OWN process and parse its result."""
    env = dict(os.environ)
    env["POSTGRES_USER"] = FENCE_ROLE
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.integration.mcps_gateway.test_real_pair",
            "--phase",
            phase,
            "--work-dir",
            "/tmp/mcps-d1/real-pair",
            "--postgres-user",
            FENCE_ROLE,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=GATE_TIMEOUT_SECONDS,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    payload: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_MARKER):
            with contextlib.suppress(Exception):
                payload = json.loads(line[len(RESULT_MARKER) :])
    return proc.returncode, proc.stdout, payload


@pytest.mark.timeout(GATE_TIMEOUT_SECONDS)
def test_p1_happy_path_real_pair() -> None:
    """P1 — the happy path: assertions 1-6, 9-13, 20-22 against the real pair."""
    code, _stdout, payload = _run_phase("p1")
    assert payload, "the gate produced no machine-readable result"
    failed = [
        f"#{a['number']} {a['name']} — {a['detail']}"
        for a in payload.get("assertions", [])
        if a.get("verdict") == "FAIL"
    ]
    failed += [
        f"gate {g['name']} — {g['detail']}"
        for g in payload.get("gates", [])
        if g.get("verdict") == "FAIL"
    ]
    assert not failed, "P1 FAILED:\n" + "\n".join(failed)
    assert code == 0, f"the P1 gate exited with {code}"
    assert payload.get("verdict") == "PASS"


@pytest.mark.timeout(GATE_TIMEOUT_SECONDS)
def test_p2_negative_paths_real_pair() -> None:
    """P2 — the negative / diagnostic paths: assertions 7, 8, 14, 15, 16, 24, 25, 26."""
    code, _stdout, payload = _run_phase("p2")
    assert payload, "the gate produced no machine-readable result"
    failed = [
        f"#{a['number']} {a['name']} — {a['detail']}"
        for a in payload.get("assertions", [])
        if a.get("verdict") == "FAIL"
    ]
    failed += [
        f"gate {g['name']} — {g['detail']}"
        for g in payload.get("gates", [])
        if g.get("verdict") == "FAIL"
    ]
    assert not failed, "P2 FAILED:\n" + "\n".join(failed)
    assert code == 0, f"the P2 gate exited with {code}"
    assert payload.get("verdict") == "PASS"


@pytest.mark.timeout(GATE_TIMEOUT_SECONDS)
def test_p3_audit_db_proof_and_read_parity() -> None:
    """P3 — the audit DB proof (17-19) + the BE-FE read parity (23)."""
    code, _stdout, payload = _run_phase("p3")
    assert payload, "the gate produced no machine-readable result"
    failed = [
        f"#{a['number']} {a['name']} — {a['detail']}"
        for a in payload.get("assertions", [])
        if a.get("verdict") == "FAIL"
    ]
    failed += [
        f"gate {g['name']} — {g['detail']}"
        for g in payload.get("gates", [])
        if g.get("verdict") == "FAIL"
    ]
    assert not failed, "P3 FAILED:\n" + "\n".join(failed)
    assert code == 0, f"the P3 gate exited with {code}"
    assert payload.get("verdict") == "PASS"


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
