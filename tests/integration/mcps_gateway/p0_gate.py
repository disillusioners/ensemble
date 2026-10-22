"""The Phase-D1 P0 GATE — the REAL Go client against the REAL Python gateway.

WHAT THIS IS
------------
One runnable gate that proves the whole P0 chain end to end:

    TLS upgrade  →  hello / hello_ack  →  heartbeat  →  one tools/call
    round-trip  →  a REAL audit row in a REAL database  →  a clean teardown.

The peer is the SHIPPED Go client (``mcps_client``, built into ``/tmp`` from
the READ-ONLY repository) — not a transcription.  The centre is the SHIPPED
Python gateway, booted from the REAL ``create_app()`` with the REAL lifespan
(so the DB-backed audit sink and the ``mcp_gateway_audit`` table exist) and
served over uvicorn-native TLS (the client refuses ``ws://``).

WHY A SCRIPT AND NOT ONLY A PYTEST
-----------------------------------
The gateway knobs and the database target are read at IMPORT time
(``MCPS_GATEWAY_ENABLED`` gates the ROUTE REGISTRATION, and the ensemble
config is resolved when ``daemon.api`` is imported).  A pytest process that
has already imported ``daemon.api`` — e.g. next to the fake-client suite —
can no longer change either.  The gate therefore runs in its OWN process
(``python tests/integration/mcps_gateway/p0_gate.py``), and the pytest
wrapper (``test_p0_gate.py``) shells out to it, so the ordering is always
the shipped ordering.

THE ASSERTIONS
--------------
Every check is recorded with its evidence; a FAILED check makes the verdict
FAIL and the mismatch ledger is written to the evidence file.  A precise
failure is the deliverable when the pair cannot meet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tests.integration.mcps_gateway import go_client, tls_harness
else:  # pragma: no cover - module invocation
    from . import go_client, tls_harness

#: Where the evidence log is written.
EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "shared"
    / "planning"
    / "mcps-gateway-interop"
    / "p0-gate-evidence.md"
)

#: The machine id of the interop peer.
MACHINE_ID = "mach_interop"

#: The agent identity that drives the centre side (the agent-facing seam).
AGENT_ID = "ari"

#: The arguments of the ONE forwarded command (ASCII only — the args hash
#: must be recomputable byte for byte).
COMMAND_ARGS: dict[str, Any] = {"command": "uptime", "timeout_ms": 5000}

#: The tool the stub child serves successfully.
SUCCESS_TOOL = "secret_tool"


@dataclass
class Check:
    """One assertion of the gate."""

    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}" + (
            f" — {self.detail}" if self.detail else ""
        )


@dataclass
class GateResult:
    """The outcome of one gate attempt."""

    attempt: int
    checks: list[Check] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    mismatch: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name=name, ok=bool(ok), detail=detail))
        if not ok:
            self.mismatch.append(f"{name}: {detail}")
        return bool(ok)


class LogCapture(logging.Handler):
    """Capture the daemon's log records (the audit-failure line is asserted)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    def contains(self, needle: str) -> bool:
        return any(needle in message for message in self.messages())


def canonical_args_hash(arguments: Any) -> str:
    """The SHA-256 of the canonicalised arguments — computed INDEPENDENTLY.

    The shape is the contract of ``daemon/services/mcps_gateway/protocol.py``
    (``canonical_json``) and ``audit.py`` (``hash_args``): sorted keys, tight
    separators, ``default=str``, over the arguments ONLY (never the tool
    name), and ``None`` becomes ``{}``.
    """
    payload = json.dumps(
        arguments if arguments is not None else {},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _process_group_of(client: go_client.GoClient | None) -> int | None:
    """The process GROUP of the client (it is started with ``start_new_session=True``).

    The group is the honest "is anything of this client left?" probe: the
    client process AND any survivor of its group (the stub child) keep
    the group alive.
    """
    if client is None:
        return None
    try:
        return os.getpgid(client.process.pid)
    except (ProcessLookupError, PermissionError, OSError):
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


def write_client_yaml(
    artifacts: go_client.GoArtifacts,
    *,
    center_url: str,
    enrollment_token: str,
    ca_pem: Path,
    listen_addr: str,
    listen_token: str,
    work_dir: Path,
) -> Path:
    """Write the client configuration and run the ``-check-config`` preflight."""
    config_path = work_dir / "client-config.yaml"
    go_client.write_client_config(
        config_path,
        center_url=center_url,
        enrollment_token=enrollment_token,
        machine_id=MACHINE_ID,
        ca_pem=ca_pem,
        listen_addr=listen_addr,
        listen_token=listen_token,
        stub_child_bin=artifacts.stub_child_bin,
        audit_path=work_dir / "client-audit.jsonl",
        child_record_path=work_dir / "child.jsonl",
    )
    return config_path


async def run_gate(
    work_dir: Path,
    *,
    attempt: int = 1,
    postgres_user: str = "",
    boot_timeout_hint: str = "",
) -> GateResult:
    """Run ONE attempt of the gate."""
    result = GateResult(attempt=attempt)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # The fence role is resolved ONCE here (the harness resolves it the
    # same way) so that the independent engine below connects as the
    # fence OWNER — never as the ambient (production) role.
    fence_role = tls_harness.resolve_fence_role(postgres_user)

    root = logging.getLogger()
    capture = LogCapture()
    root.addHandler(capture)
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    client: go_client.GoClient | None = None
    client_pgid: int | None = None
    gw: Any = None
    stop_report: dict[str, Any] = {}
    baseline_listeners: list[str] = []
    try:
        # ── 0. the Go build (offline; nothing lands in the read-only repo) ──
        t0 = time.monotonic()
        artifacts = await asyncio.to_thread(go_client.build_go_artifacts)
        result.timings["go_build_s"] = time.monotonic() - t0
        result.add(
            "go-build-offline",
            artifacts.client_bin.exists() and artifacts.stub_child_bin.exists(),
            f"client={artifacts.client_bin} stub={artifacts.stub_child_bin}",
        )

        # ── 1. the TLS boot of the REAL app ──
        enrollment_token = tls_harness.hex_token()
        listen_token = tls_harness.hex_token()
        listen_port = tls_harness.free_tcp_port()
        listen_addr = f"127.0.0.1:{listen_port}"

        t0 = time.monotonic()
        async with tls_harness.boot_tls_gateway(
            work_dir,
            tokens=(enrollment_token,),
            machine_id=MACHINE_ID,
            postgres_user=fence_role,
        ) as gw:
            result.timings["boot_s"] = time.monotonic() - t0
            result.evidence.append(f"gateway ws url: {gw.ws_url}")
            result.evidence.append(
                f"gateway route registered: {gw.ws_path in gw.ws_route_paths}"
            )
            # The listener baseline was captured by the harness BEFORE the
            # server bound its socket.  A baseline taken after the bind
            # contains the gateway's OWN listener and would mask a leaked
            # gateway listener as "pre-existing" — the gateway's port is
            # subtracted explicitly as well (belt and braces).
            baseline_listeners = [
                line
                for line in (getattr(gw, "listener_baseline", []) or [])
                if f":{gw.port} " not in line
            ]
            result.add(
                "listener-baseline-excludes-gateway-port",
                not any(f":{gw.port} " in line for line in baseline_listeners),
                f"baseline={baseline_listeners} gateway_port={gw.port}",
            )
            result.add(
                "tls-route-registered",
                gw.ws_path in gw.ws_route_paths,
                f"routes={gw.ws_route_paths[:6]}",
            )
            result.add(
                "tls-cert-has-ip-san",
                # The SAN probe shells out to ``openssl x509`` — a BLOCKING
                # call.  It must not run inside the live event loop.
                await asyncio.to_thread(tls_harness.cert_has_ip_san, gw.cert_path),
                str(gw.cert_path),
            )
            result.add(
                "engine-is-postgres",
                gw.engine.dialect.name == "postgresql",
                f"dialect={gw.engine.dialect.name} url={gw.engine.url}",
            )
            result.add(
                "engine-targets-fence-db",
                tls_harness.FENCE_DB_NAME in str(gw.engine.url),
                f"url={gw.engine.url}",
            )

            # ── 2. the audit canary gate (B11) — BEFORE the Go client boots ──
            from daemon.repositories.factory import PG_SESSION_CONNECT_ARGS
            from daemon.services.mcps_gateway.audit import AuditRecord
            from sqlalchemy import create_engine

            # The ledger is the evidence: every attempt starts empty.
            deleted = await asyncio.to_thread(tls_harness.reset_audit_table, gw.engine)
            result.evidence.append(f"fence ledger rows deleted before the attempt: {deleted}")

            # The daemon's EVENT LOOP responsiveness (a blocked loop answers
            # nothing — this is the measurement of the boot-blocking window).
            status, latency, body = await tls_harness.https_probe_async(
                gw.port, timeout=10.0
            )
            result.timings["boot_probe_s"] = latency
            result.evidence.append(
                f"boot probe: GET /api/health -> status={status} in {latency:.3f}s ({body[:120]})"
            )
            result.add(
                "daemon-event-loop-responsive-after-boot",
                status is not None and latency < 5.0,
                f"status={status} latency={latency:.3f}s body={body[:120]!r}",
            )

            # The job-queue baseline (the daemon creates its default QUEUE
            # rows at boot — the assertion is that NO JOB is created and
            # that no queue row appears DURING the run).
            queue_baseline = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queues"
            )
            job_baseline = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queue_items"
            )

            service = gw.service
            result.add(
                "audit-sink-is-db-repository",
                type(service._audit).__name__ == "McpGatewayAuditRepository",
                f"sink={type(service._audit).__name__}",
            )
            result.add(
                "audit-sink-engine-is-manager-engine",
                getattr(service._audit, "_engine", None) is gw.manager.engine,
                f"sink_engine={getattr(service._audit, '_engine', None)}",
            )
            result.add(
                "audit-enabled",
                # IDENTITY on the bool — the documented claim is that the
                # audit sink is ON, not merely truthy.
                getattr(gw.config, "audit_enabled", None) is True,
                f"audit_enabled={getattr(gw.config, 'audit_enabled', None)!r}",
            )

            canary_args = {"probe": "p0-canary"}
            canary_hash = canonical_args_hash(canary_args)
            canary_machine = f"mach_canary_a{attempt}"
            canary = AuditRecord(
                agent_id="ari-canary",
                machine_id=canary_machine,
                tool="canary_tool",
                args_hash=canary_hash,
                outcome="ok",
                session_id="sess_canary",
                detail="p0 canary row",
            )
            await asyncio.to_thread(service._audit.record, canary)

            independent_engine = create_engine(
                tls_harness.fence_sync_dsn(fence_role),
                connect_args=dict(PG_SESSION_CONNECT_ARGS),
            )
            try:
                canary_rows = await asyncio.to_thread(
                    tls_harness.fetch_rows,
                    independent_engine,
                    "SELECT * FROM mcp_gateway_audit WHERE machine_id = :m",
                    {"m": canary_machine},
                )
            finally:
                await asyncio.to_thread(independent_engine.dispose)
            result.add(
                "audit-canary-visible-on-independent-engine",
                len(canary_rows) == 1,
                f"rows={len(canary_rows)}",
            )
            if canary_rows:
                row = canary_rows[0]
                result.add(
                    "audit-canary-hash-exact",
                    row.get("args_hash") == canary_hash,
                    f"stored={row.get('args_hash')} recomputed={canary_hash}",
                )
                result.evidence.append(
                    "canary row: " + json.dumps(row, sort_keys=True, default=str)
                )
            else:
                # ALWAYS recorded — the check COUNT must not depend on the
                # outcome of the previous check (an unstable tally makes
                # the "N/N checks green" claim meaningless).
                result.add(
                    "audit-canary-hash-exact",
                    False,
                    "the canary row is absent: there is no stored hash to compare",
                )

            pre_flow_count = await asyncio.to_thread(
                tls_harness.rows_in,
                gw.engine,
                "mcp_gateway_audit",
                "machine_id = :m",
                {"m": MACHINE_ID},
            )
            result.add("audit-pre-flow-count-zero", pre_flow_count == 0, f"count={pre_flow_count}")

            # ── 3. the Go client ──
            config_path = write_client_yaml(
                artifacts,
                center_url=gw.ws_url,
                enrollment_token=enrollment_token,
                ca_pem=gw.cert_path,
                listen_addr=listen_addr,
                listen_token=listen_token,
                work_dir=work_dir,
            )
            code, out, err = await asyncio.to_thread(
                go_client.run_check_config, artifacts.client_bin, config_path
            )
            result.add(
                "client-check-config-exit-0",
                code == 0,
                f"exit={code} stdout={out.strip()[:200]} stderr={err.strip()[:400]}",
            )
            result.evidence.append(f"client config: {config_path}")

            # NOTE: the listener baseline is NOT captured here — the
            # gateway's own listener is already open at this point, so a
            # baseline taken now would mask a leaked gateway listener as
            # "pre-existing".  The harness captured it BEFORE the bind.

            t0 = time.monotonic()
            client = go_client.start_client(
                artifacts.client_bin, config_path, log_path=work_dir / "client-stderr.log"
            )
            client_pgid = _process_group_of(client)
            result.timings["client_start_s"] = time.monotonic() - t0

            child_ready = await client.wait_for_message_async(
                go_client.CHILD_READY_LINE, timeout=90.0
            )
            result.add(
                "client-child-started",
                child_ready,
                "the stub child reported 'child started'"
                if child_ready
                else "the stub child did not report 'child started' within 90s",
            )
            handshake = await client.wait_for_message_async(
                go_client.HANDSHAKE_LINE, timeout=90.0
            )
            result.add(
                "client-handshake-accepted",
                handshake,
                "the client reported 'the center accepted the handshake'"
                if handshake
                else "the client did not report 'the center accepted the handshake' within 90s",
            )
            result.timings["handshake_s"] = time.monotonic() - t0
            if not handshake:
                result.evidence.append(
                    "client log tail:\n" + "\n".join(client.snapshot()[-40:])
                )
                result.evidence.append(
                    "centre transcript:\n" + gw.transcript.render()
                )

            # ── 4. the centre-side transcript: hello / hello_ack ──
            hello_frames = gw.transcript.find(direction="recv", method="hello")
            result.add("centre-saw-hello", len(hello_frames) == 1, f"frames={len(hello_frames)}")
            ack_frames = [
                frame
                for frame in gw.transcript.find(direction="send", type="response")
                if frame.parsed and frame.parsed.get("result", {}).get("protocol_version")
            ]
            result.add(
                "centre-sent-hello-ack",
                len(ack_frames) >= 1,
                f"frames={len(ack_frames)}",
            )
            if hello_frames and ack_frames:
                hello_id = (hello_frames[0].parsed or {}).get("id")
                ack = ack_frames[0].parsed or {}
                result.add(
                    "hello-ack-echoes-id-verbatim",
                    ack.get("id") == hello_id,
                    f"hello_id={hello_id!r} ack_id={ack.get('id')!r}",
                )
                result_body = ack.get("result") or {}
                result.add(
                    "hello-ack-protocol-version-0.1",
                    result_body.get("protocol_version") == "0.1",
                    f"protocol_version={result_body.get('protocol_version')!r}",
                )
                timings = {
                    key: result_body.get(key)
                    for key in (
                        "heartbeat_interval_ms",
                        "heartbeat_timeout_ms",
                        "client_dead_detect_ms",
                    )
                }
                result.add(
                    "hello-ack-advertises-three-timings",
                    all(isinstance(value, int) for value in timings.values()),
                    json.dumps(timings),
                )
                result.evidence.append(
                    "hello: "
                    + (hello_frames[0].parsed or {}).get("params", {}).get("protocol_version", "")
                    + " "
                    + json.dumps(
                        {
                            "id": (hello_frames[0].parsed or {}).get("id"),
                            "machine_id": (hello_frames[0].parsed or {})
                            .get("params", {})
                            .get("machine_id"),
                            "capabilities": (hello_frames[0].parsed or {})
                            .get("params", {})
                            .get("capabilities"),
                        },
                        sort_keys=True,
                    )
                )
                result.evidence.append("hello_ack: " + json.dumps(ack, sort_keys=True))

            # ── 5. the heartbeat (centre-side, <= 20 s) ──
            t0 = time.monotonic()
            heartbeat = await gw.transcript.wait_for(
                lambda frame: frame.direction == "recv"
                and (frame.parsed or {}).get("method") == "heartbeat",
                timeout=20.0,
            )
            result.timings["heartbeat_s"] = time.monotonic() - t0
            result.add(
                "centre-saw-heartbeat",
                heartbeat is not None,
                "a heartbeat notification arrived centre-side"
                if heartbeat is not None
                else "no heartbeat notification arrived centre-side within 20s",
            )
            if heartbeat is not None:
                result.evidence.append("heartbeat: " + heartbeat.raw)

            # ── 6. the command round-trip (the shipped service seam) ──
            t0 = time.monotonic()
            session: dict[str, Any] = {}
            command_result: dict[str, Any] = {}
            end_result: dict[str, Any] = {}
            try:
                session = await gw.service.connect(
                    machine_id=MACHINE_ID,
                    agent_id=AGENT_ID,
                    reason="p0-gate",
                )
                result.add(
                    "session-begin-answered",
                    bool(session.get("session_id")),
                    json.dumps(session, sort_keys=True, default=str)[:300],
                )
                result.evidence.append("session.begin result: " + json.dumps(session, sort_keys=True, default=str)[:600])
                command_result = await gw.service.command(
                    agent_id=AGENT_ID,
                    tool=SUCCESS_TOOL,
                    arguments=COMMAND_ARGS,
                )
                result.add(
                    "tools-call-round-trip",
                    "the secret tool ran" in json.dumps(command_result, default=str),
                    json.dumps(command_result, sort_keys=True, default=str)[:300],
                )
                result.evidence.append("tools/call result: " + json.dumps(command_result, sort_keys=True, default=str)[:600])
                end_result = await gw.service.disconnect(agent_id=AGENT_ID)
                result.add(
                    "session-end-acknowledged",
                    bool(end_result.get("ended")),
                    json.dumps(end_result, sort_keys=True, default=str)[:200],
                )
            except Exception as exc:  # noqa: BLE001 — the failure IS the deliverable
                result.add(
                    "session-round-trip",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            result.timings["round_trip_s"] = time.monotonic() - t0

            # ── 7. the audit row of the REAL flow ──
            expected_hash = canonical_args_hash(COMMAND_ARGS)
            flow_rows = await asyncio.to_thread(
                tls_harness.fetch_rows,
                gw.engine,
                "SELECT * FROM mcp_gateway_audit WHERE machine_id = :m ORDER BY created_at DESC",
                {"m": MACHINE_ID},
            )
            result.add(
                "audit-flow-row-exists",
                len(flow_rows) >= 1,
                f"rows={len(flow_rows)}",
            )
            if flow_rows:
                row = flow_rows[0]
                session_id = (session or {}).get("session_id")
                result.add(
                    "audit-row-session-id-matches",
                    row.get("session_id") == session_id,
                    f"row={row.get('session_id')!r} observed={session_id!r}",
                )
                result.add(
                    "audit-row-tool-matches",
                    row.get("tool") == SUCCESS_TOOL,
                    f"row={row.get('tool')!r}",
                )
                result.add(
                    "audit-row-outcome-ok",
                    row.get("outcome") == "ok",
                    f"row={row.get('outcome')!r}",
                )
                result.add(
                    "audit-row-args-hash-exact",
                    row.get("args_hash") == expected_hash,
                    f"stored={row.get('args_hash')!r} recomputed={expected_hash}",
                )
                result.add(
                    "audit-row-no-raw-arguments",
                    "uptime" not in json.dumps(row, default=str),
                    "the row carries the raw arguments",
                )
                result.evidence.append("audit row: " + json.dumps(row, sort_keys=True, default=str))

            result.add(
                "audit-no-write-failure-log",
                not capture.contains("audit row could not be written"),
                "no audit write failure was logged"
                if not capture.contains("audit row could not be written")
                else "the daemon logged an audit write failure",
            )
            result.add(
                "audit-failures-zero",
                int(getattr(gw.service, "audit_failures", 0)) == 0,
                f"audit_failures={getattr(gw.service, 'audit_failures', None)}",
            )

            # ── 8. the REAL kill switches (R-23), read back on the booted app ──
            # ``DRY_RUN`` / ``DESTRUCTIVE`` have NO daemon-wide consumer
            # (verified by grep — only the two Plane switches are
            # honoured), so the R-23 mitigation is real ONLY for these
            # two.  Their EFFECT is therefore read back here, on the
            # booted application, instead of being assumed.
            from daemon.mcp.builtin_servers import plane as plane_builtin
            from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
            from daemon.services.plane_sync_service import (
                PlaneSyncService,
                plane_sync_enabled,
            )

            sync_enabled = plane_sync_enabled()
            sync_reason = PlaneSyncService.unavailable_reason()
            result.add(
                "plane-sync-kill-switch-effective",
                sync_enabled is False
                and sync_reason == "disabled by kill-switch (PLANE_SYNC_ENABLED=false)",
                f"enabled={sync_enabled} reason={sync_reason!r}",
            )
            try:
                plane_rows = await asyncio.to_thread(
                    tls_harness.fetch_rows,
                    gw.engine,
                    "SELECT name, is_active FROM mcp_servers WHERE name = :n",
                    {"n": "plane"},
                )
            except Exception as exc:  # noqa: BLE001 — the query IS the check
                plane_rows = []
                plane_error = f"{type(exc).__name__}: {exc}"
            else:
                plane_error = ""
            # The switch itself is read back through the daemon's OWN reader
            # (a private module function — the public ``is_available()``
            # also returns False when the URL/key are missing, so it
            # cannot isolate the switch).
            mcp_switch_off = plane_builtin._mcp_kill_switch_enabled() is False
            mcp_available = PlaneServerDefinition.is_available()
            result.add(
                "plane-mcp-kill-switch-effective",
                mcp_switch_off
                and mcp_available is False
                and not any(bool(row.get("is_active")) for row in plane_rows),
                f"switch_off={mcp_switch_off} available={mcp_available} "
                f"rows={plane_rows} error={plane_error}",
            )

            # ── 9. the job queue stays empty (R-23) ──
            job_count = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queue_items"
            )
            queue_count = await asyncio.to_thread(
                tls_harness.rows_in, gw.engine, "job_queues"
            )
            result.add(
                "job-queue-items-empty",
                job_count == 0 and job_count == job_baseline,
                f"count={job_count} baseline={job_baseline}",
            )
            result.add(
                "job-queues-unchanged",
                queue_count == queue_baseline,
                f"count={queue_count} baseline={queue_baseline}",
            )

            result.evidence.append("centre transcript:\n" + gw.transcript.render())
            result.evidence.append(
                "client log tail:\n" + "\n".join(client.snapshot()[-40:])
            )
            result.timings["server_shutdown_s"] = 0.0

        result.add(
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
        result.add("gate-run", False, f"{type(exc).__name__}: {exc}")
        import traceback

        result.mismatch.append(traceback.format_exc())
    finally:
        if client is not None:
            stop_report = await asyncio.to_thread(client.stop)
        # the stub child must be gone
        leftover = await asyncio.to_thread(go_client.stub_child_pids)
        result.add(
            "no-orphan-stub-child",
            not leftover,
            f"pids={leftover}",
        )
        result.add(
            "client-process-stopped",
            client is None or client.process.poll() is not None,
            f"exit={client.process.poll() if client else None}",
        )
        # DISTINCT from ``client-process-stopped``: the client's process
        # GROUP (the client was started with ``start_new_session=True``)
        # must have NO member left — the stub child lives in that group
        # too, so this catches a survivor that the process check misses.
        group_alive = _process_group_alive(client_pgid)
        result.add(
            "no-orphan-client-process",
            not group_alive,
            f"pgid={client_pgid} group_alive={group_alive} "
            f"pid={client.process.pid if client else None}",
        )
        # nothing may listen
        await asyncio.sleep(0.3)
        remaining = await asyncio.to_thread(tls_harness.listeners_owned_by, os.getpid())
        new_listeners = [
            line for line in remaining if line not in set(baseline_listeners)
        ]
        result.add(
            "no-listener-left",
            not new_listeners,
            f"remaining={remaining} baseline={baseline_listeners}",
        )
        result.evidence.append(
            "lsof listeners after the run:\n" + ("\n".join(remaining) or "(none)")
        )
        result.evidence.append("client stop report: " + json.dumps(stop_report, default=str))
        root.removeHandler(capture)
        root.setLevel(previous_level)

    return result


def render_report(result: GateResult) -> str:
    """The human-readable report of one attempt."""
    lines = [f"=== Phase-D1 P0 GATE — attempt {result.attempt} — {result.verdict} ==="]
    for check in result.checks:
        lines.append(check.line())
    lines.append("--- timings (s) ---")
    for key, value in result.timings.items():
        lines.append(f"  {key}: {value:.3f}")
    if result.mismatch:
        lines.append("--- mismatch ledger ---")
        for item in result.mismatch:
            lines.append(item)
    lines.append("--- evidence ---")
    lines.extend(result.evidence)
    return "\n".join(lines)


def write_evidence(result: GateResult, path: Path = EVIDENCE_PATH) -> Path:
    """Append the attempt to the evidence log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## Attempt {result.attempt} — {result.verdict} ({stamp})\n\n")
        handle.write("```\n")
        handle.write(render_report(result))
        handle.write("\n```\n")
    return path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    attempts = 1
    work_root = Path("/tmp/mcps-d1/gate")
    postgres_user = os.environ.get("POSTGRES_USER", "") or ""
    if argv:
        attempts = int(argv[0])
    if len(argv) > 1:
        work_root = Path(argv[1])
    if len(argv) > 2:
        postgres_user = argv[2]

    final: GateResult | None = None
    for attempt in range(1, attempts + 1):
        work_dir = work_root / f"attempt-{attempt}"
        result = asyncio.run(run_gate(work_dir, attempt=attempt, postgres_user=postgres_user))
        final = result
        print(render_report(result), flush=True)
        write_evidence(result)
        if result.ok:
            break
    assert final is not None
    print(f"\nP0 GATE VERDICT: {final.verdict} (attempts: {final.attempt})")
    return 0 if final.ok else 1


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
