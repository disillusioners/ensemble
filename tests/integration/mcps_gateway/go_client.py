"""Driver for the SHIPPED Go client (``mcps_client``) — the interop peer.

This module holds NO protocol knowledge and imports NOTHING from the
daemon: it builds the shipped binaries into ``/tmp`` (the ``mcps_client``
repository is READ-ONLY — no build output lands inside it), writes the
client configuration, runs the ``-check-config`` preflight and supervises
the client process.

The client is a FOREGROUND long-runner with JSON logs on stderr
(``slog.NewJSONHandler(os.Stderr, ...)``), and a fatal remote error does
NOT exit the process — so the readiness and the failure detection are both
driven by the LOG LINES, never by the exit code.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The repository of the shipped client.  READ-ONLY: nothing is written
#: inside it, ever.
GO_REPO = Path("/Users/nguyenminhkha/All/Code/opensource-projects/mcps_client")

#: Where the build outputs land (NOT inside the read-only repository).
BUILD_DIR = Path("/tmp/mcps-d1")

#: The log line the supervisor prints when the child is ready.
CHILD_READY_LINE = "child started"

#: The log line the remote hop prints when the handshake succeeded.
HANDSHAKE_LINE = "the center accepted the handshake"


class GoBuildError(RuntimeError):
    """The Go build failed (or wanted to fetch from the network)."""


@dataclass
class GoArtifacts:
    """The built artefacts of the shipped client."""

    client_bin: Path
    stub_child_bin: Path
    build_log: list[str] = field(default_factory=list)


def build_go_artifacts(
    repo: Path = GO_REPO, build_dir: Path = BUILD_DIR
) -> GoArtifacts:
    """Build the client and the adversarial stub child into ``build_dir``.

    ``GOPROXY=off`` + ``GOFLAGS=-mod=readonly`` make the build FAIL FAST
    when the module cache would need a network fetch (the lane must not
    depend on the network), and ``-mod=readonly`` keeps the read-only
    repository untouched (``go.mod``/``go.sum`` are never rewritten).
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    client_bin = build_dir / "mcps-client"
    stub_bin = build_dir / "adversarial.test"
    log: list[str] = []
    env = dict(os.environ)
    env["GOPROXY"] = "off"
    env["GOFLAGS"] = "-mod=readonly"

    steps = [
        (["go", "build", "-trimpath", "-o", str(client_bin), "./cmd/mcps-client"], "client"),
        (["go", "test", "-c", "-o", str(stub_bin), "./internal/adversarial"], "stub child"),
    ]
    for argv, label in steps:
        proc = subprocess.run(
            argv,
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
        )
        log.append(f"$ {' '.join(argv)}  ->  exit {proc.returncode}")
        if proc.stdout.strip():
            log.append(proc.stdout.strip())
        if proc.stderr.strip():
            log.append(proc.stderr.strip())
        if proc.returncode != 0:
            raise GoBuildError(
                f"the {label} build failed (exit {proc.returncode}):\n"
                + "\n".join(log)
            )
    if not client_bin.exists() or not stub_bin.exists():
        raise GoBuildError("the build produced no binaries:\n" + "\n".join(log))
    return GoArtifacts(client_bin=client_bin, stub_child_bin=stub_bin, build_log=log)


def write_client_config(
    path: Path,
    *,
    center_url: str,
    enrollment_token: str,
    machine_id: str,
    ca_pem: Path,
    listen_addr: str,
    listen_token: str,
    stub_child_bin: Path,
    audit_path: Path,
    child_record_path: Path,
    heartbeat_interval_ms: int = 15000,
    heartbeat_timeout_ms: int = 45000,
    client_dead_detect_ms: int = 45000,
    reconnect_initial_backoff_ms: int = 1000,
    reconnect_max_backoff_ms: int = 60000,
    tool_call_timeout_ms: int = 30000,
    max_frame_bytes: int = 8388608,
) -> Path:
    """Write the client configuration.

    The shape is EXACT for this build: an unknown YAML key is a hard error
    at load time (``internal/config/config.go``), and ``child.command`` is
    a SCALAR — a YAML sequence is refused by the strict decoder.

    THE TIMING KNOBS ARE PARAMETERS (the defaults reproduce the previous
    file byte for byte, so the P0 gate is unaffected): the compressed
    profile of the P1/P2 lane needs a client whose ``tool_call_timeout``
    is ABOVE the centre's command deadline (the centre's arm must win the
    timeout race, §9.3) and whose reconnect backoff is short.

    The heartbeat values written here are the FALLBACKS of the client
    (``internal/config/config.go``): the client REFUSES a configuration
    whose ``heartbeat_interval_ms`` is outside 5000-60000 (SPEC §3.3), so
    the compressed values of the CENTRE cannot be written here — the
    client ADOPTS the advertised ones at runtime instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""listen:
  addr: "{listen_addr}"
  token: "{listen_token}"
  path: "/mcp"
child:
  command: "{stub_child_bin}"
  args: ["-test.run=^$"]
  env:
    - "MCPS_ADVERSARIAL_CHILD_MODE=1"
    - "MCPS_ADVERSARIAL_CHILD_RECORD={child_record_path}"
  dir: ""
  terminate_grace_ms: 5000
restart:
  enabled: true
  initial_backoff_ms: 1000
  max_backoff_ms: 60000
  max_attempts: 0
  multiplier: 2.0
  jitter: 0.2
policy:
  enabled: true
  deny_tools: []
audit:
  enabled: false
  path: "{audit_path}"
remote:
  enabled: true
  center_url: "{center_url}"
  enrollment_token: "{enrollment_token}"
  machine_id: "{machine_id}"
  ca_pem: "{ca_pem}"
  server_pin_sha256: ""
  heartbeat_interval_ms: {heartbeat_interval_ms}
  heartbeat_timeout_ms: {heartbeat_timeout_ms}
  client_dead_detect_ms: {client_dead_detect_ms}
  reconnect_initial_backoff_ms: {reconnect_initial_backoff_ms}
  reconnect_max_backoff_ms: {reconnect_max_backoff_ms}
  tool_call_timeout_ms: {tool_call_timeout_ms}
  max_frame_bytes: {max_frame_bytes}
"""
    path.write_text(text, encoding="utf-8")
    return path


def run_check_config(client_bin: Path, config_path: Path) -> tuple[int, str, str]:
    """The ``-check-config`` preflight: exit 0 means the YAML is valid."""
    proc = subprocess.run(
        [str(client_bin), "-config", str(config_path), "-check-config"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


class GoClient:
    """The running client process and its JSON log stream."""

    def __init__(self, process: subprocess.Popen, log_path: Path) -> None:
        self.process = process
        self.log_path = log_path
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self._log_file = open(log_path, "ab")
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self.process.stderr is not None
        for raw in self.process.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            with self._lock:
                self.lines.append(line)
            try:
                self._log_file.write(line.encode("utf-8") + b"\n")
                self._log_file.flush()
            except Exception:  # noqa: BLE001 — the log file is a convenience
                pass
        try:
            self._log_file.close()
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.lines)

    def records(self) -> list[dict[str, Any]]:
        """The parsed JSON log records (a non-JSON line is skipped)."""
        out: list[dict[str, Any]] = []
        for line in self.snapshot():
            try:
                payload = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(payload, dict):
                out.append(payload)
        return out

    def messages(self) -> list[str]:
        return [
            str(record.get("msg", ""))
            for record in self.records()
            if record.get("msg")
        ]

    def wait_for_message(self, needle: str, *, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in message for message in self.messages()):
                return True
            if self.process.poll() is not None:
                # The process died: one last look at the log.
                return any(needle in message for message in self.messages())
            time.sleep(0.05)
        return False

    async def wait_for_message_async(self, needle: str, *, timeout: float = 30.0) -> bool:
        """The same wait, but it NEVER parks the event loop.

        A blocking wait inside the event loop stops the daemon under test
        from answering (its own socket work needs the very loop that is
        parked) — the client's upgrade would then time out for a reason
        that has nothing to do with the daemon.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in message for message in self.messages()):
                return True
            if self.process.poll() is not None:
                return any(needle in message for message in self.messages())
            await asyncio.sleep(0.05)
        return False

    def wait_for_exit(self, timeout: float = 5.0) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self, *, grace: float = 10.0) -> dict[str, Any]:
        """Stop the client (and its stub child) and prove both are gone."""
        report: dict[str, Any] = {"pid": self.process.pid, "signalled": None}
        if self.process.poll() is None:
            report["signalled"] = "SIGTERM"
            try:
                self.process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                report["signalled"] = "gone"
            exit_code = self.wait_for_exit(timeout=grace)
            if exit_code is None:
                report["signalled"] = "SIGKILL"
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                exit_code = self.wait_for_exit(timeout=5.0)
            report["exit_code"] = exit_code
        else:
            report["exit_code"] = self.process.returncode
        # The stub child is in the client's process group (the client was
        # started with ``start_new_session=True``).  A child that survived
        # the client's own shutdown is killed with the GROUP, so no orphan
        # is left behind.
        leftover = stub_child_pids()
        if leftover:
            report["group_kill"] = True
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                for pid in leftover:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            time.sleep(0.5)
        report["stub_children"] = stub_child_pids()
        return report


def stub_child_pids() -> list[int]:
    """The PIDs of the running stub children (the adversarial test binary)."""
    proc = subprocess.run(
        ["pgrep", "-f", "adversarial.test"],
        capture_output=True,
        text=True,
    )
    pids = []
    for line in proc.stdout.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def start_client(
    client_bin: Path, config_path: Path, *, log_path: Path
) -> GoClient:
    """Start the client as a foreground subprocess with JSON logs on stderr."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(client_bin), "-config", str(config_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=1,
        start_new_session=True,
    )
    return GoClient(process, log_path)


__all__ = [
    "BUILD_DIR",
    "CHILD_READY_LINE",
    "GoArtifacts",
    "GoBuildError",
    "GoClient",
    "GO_REPO",
    "HANDSHAKE_LINE",
    "build_go_artifacts",
    "run_check_config",
    "start_client",
    "stub_child_pids",
    "write_client_config",
]
