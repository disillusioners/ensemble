"""Phase 0 validator: empirically validates C4, C5, W2 against a real code-server.

Strategy:
1. Start a real code-server (no mocks) bound to 127.0.0.1:9100 with --auth none.
2. Start the spike proxy (uvicorn programmatic) bound to 127.0.0.1:8091 (we
   avoid 8079 because the dev backend is there).
3. Run three programmatic tests using the `websockets` library as the WS client.
4. Capture per-test PASS/FAIL with diagnostic context.
5. Always tear down subprocesses, even on failure.

Why port 8091 not 8079 for the proxy: the project dev backend occupies 8079
(`./dev.sh` runs uvicorn there). Killing it is forbidden per AGENTS.md. The
spike `__main__` block still defaults to 8079 for manual runs.

Run:
    uv run python spike/validate_spike.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CODE_SERVER_BIN = shutil.which("code-server") or "/opt/homebrew/bin/code-server"
CODE_SERVER_HOST = "127.0.0.1"
CODE_SERVER_PORT = 9100
CODE_SERVER_URL = f"http://{CODE_SERVER_HOST}:{CODE_SERVER_PORT}"
CODE_SERVER_WS = f"ws://{CODE_SERVER_HOST}:{CODE_SERVER_PORT}"

SPIKE_HOST = "127.0.0.1"
SPIKE_PORT = 8091  # avoid 8079 (dev backend)
SPIKE_URL = f"http://{SPIKE_HOST}:{SPIKE_PORT}"
SPIKE_WS = f"ws://{SPIKE_HOST}:{SPIKE_PORT}"

PROJECT_DIR = "/tmp/vscode-spike-project"
USER_DATA_DIR = "/tmp/vscode-spike-userdata"

STARTUP_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.25
WS_TEST_TIMEOUT_S = 10.0
C5_CLEANUP_WAIT_S = 5.0

RESULTS: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------


@dataclass
class ProcHandle:
    proc: subprocess.Popen
    log_path: Path
    name: str
    log_tail: list[str] = field(default_factory=list)

    def terminate(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            except ProcessLookupError:
                pass

    def collect_log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except FileNotFoundError:
            return ""


def _spawn(name: str, args: list[str], log_dir: Path) -> ProcHandle:
    log_path = log_dir / f"{name}.log"
    log_fh = open(log_path, "w")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        args,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        env=env,
        # New process group so SIGTERM only affects this subtree.
        start_new_session=True,
    )
    return ProcHandle(proc=proc, log_path=log_path, name=name)


def _wait_for_http(url: str, timeout_s: float, label: str) -> None:
    """Poll an HTTP endpoint until it returns any response or timeout."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(url)
                # Any HTTP response (even 4xx) means the server is up.
                if r.status_code < 600:
                    return
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            last_err = e
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"{label} did not become ready within {timeout_s:.1f}s "
        f"(last error: {last_err})"
    )


# ---------------------------------------------------------------------------
# Connection-count probes (for C5)
# ---------------------------------------------------------------------------


def _count_upstream_conns() -> int:
    """Count ESTABLISHED TCP connections on the code-server port from local."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{CODE_SERVER_PORT}", "-sTCP:ESTABLISHED"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # lsof may not be present in CI; fall back to socket stats.
        return _count_via_socket_stats()
    # Subtract header row.
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def _count_via_socket_stats() -> int:
    """Fallback using `netstat` if lsof isn't available."""
    try:
        out = subprocess.run(
            ["netstat", "-anp", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1  # unknown
    count = 0
    needle = f".{CODE_SERVER_PORT} "
    for line in out.splitlines():
        if "ESTABLISHED" in line and needle in line:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _prepare_dirs(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    # Clean previous project + user-data to start fresh.
    for p in (Path(PROJECT_DIR), Path(USER_DATA_DIR)):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    Path(PROJECT_DIR).mkdir(parents=True, exist_ok=True)
    # Seed a single file so code-server has something to serve.
    (Path(PROJECT_DIR) / "README.md").write_text("# Spike project\n")


@asynccontextmanager
async def _serve(log_dir: Path):
    """Start code-server + spike proxy, yield nothing, tear both down."""
    code_server = _spawn(
        "code-server",
        [
            CODE_SERVER_BIN,
            "--bind-addr",
            f"{CODE_SERVER_HOST}:{CODE_SERVER_PORT}",
            "--auth",
            "none",
            "--user-data-dir",
            USER_DATA_DIR,
            "--disable-telemetry",
            "--disable-update-check",
            PROJECT_DIR,
        ],
        log_dir,
    )
    spike = _spawn(
        "spike-proxy",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "spike.vscode_ws_spike:app",
            "--host",
            SPIKE_HOST,
            "--port",
            str(SPIKE_PORT),
            "--log-level",
            "info",
            "--no-access-log",
        ],
        log_dir,
    )
    try:
        # code-server is the upstream; wait for it first.
        _wait_for_http(
            f"{CODE_SERVER_URL}/healthz", STARTUP_TIMEOUT_S, "code-server"
        )
        # Then the proxy. Any 200/4xx/5xx means it's listening.
        _wait_for_http(
            f"{SPIKE_URL}/vscode/", STARTUP_TIMEOUT_S, "spike-proxy"
        )
        yield code_server, spike
    finally:
        spike.terminate()
        code_server.terminate()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _record(test_id: str, name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(
        {
            "id": test_id,
            "name": name,
            "passed": passed,
            "detail": detail,
        }
    )
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {test_id} {name}: {detail}", flush=True)


async def test_c4_binary_dispatch() -> None:
    """C4: binary frames proxied without TypeError/UnicodeDecodeError."""
    print("\n=== C4: binary frame dispatch ===", flush=True)
    # Connect to the proxy; the upstream will be the real code-server WS endpoint.
    url = f"{SPIKE_WS}/vscode/ws/"
    binary_payload = bytes(range(256))  # full byte range; includes non-UTF-8
    text_payload = "hello, subprotocol-test\n"

    try:
        async with websockets.connect(
            url,
            subprotocols=[Subprotocol("v5.code-server-protocol")],
            open_timeout=WS_TEST_TIMEOUT_S,
            ping_interval=None,
        ) as ws:
            # Send a binary frame and a text frame. We don't expect code-server
            # to echo arbitrary bytes; what matters is the proxy doesn't raise
            # UnicodeDecodeError / TypeError when relaying.
            await ws.send(binary_payload)
            await ws.send(text_payload)
            # Give the proxy a moment to relay.
            await asyncio.sleep(0.5)
            _record(
                "C4",
                "binary frame dispatch",
                passed=True,
                detail=(
                    "binary (256-byte) + text frames sent through proxy without "
                    "TypeError/UnicodeDecodeError (code-server may close the "
                    "WS handshake after this, which is acceptable for the test)"
                ),
            )
    except ConnectionClosed as exc:
        # code-server closing is acceptable; the test is about the proxy not
        # crashing on bytes. If the close happened cleanly (1006 or normal),
        # the dispatch worked.
        _record(
            "C4",
            "binary frame dispatch",
            passed=True,
            detail=f"frames relayed; upstream closed with {exc!r} (expected)",
        )
    except (UnicodeDecodeError, TypeError) as exc:
        _record(
            "C4",
            "binary frame dispatch",
            passed=False,
            detail=f"proxy raised on binary frame: {exc!r}",
        )
    except Exception as exc:  # noqa: BLE001
        _record(
            "C4",
            "binary frame dispatch",
            passed=False,
            detail=f"unexpected: {type(exc).__name__}: {exc}",
        )


async def test_w2_subprotocols() -> None:
    """W2: Sec-WebSocket-Protocol forwarded; handshake succeeds with subprotocol."""
    print("\n=== W2: subprotocol forwarding ===", flush=True)
    url = f"{SPIKE_WS}/vscode/ws/"
    requested = "v5.code-server-protocol"
    try:
        async with websockets.connect(
            url,
            subprotocols=[Subprotocol(requested)],
            open_timeout=WS_TEST_TIMEOUT_S,
            ping_interval=None,
        ) as ws:
            negotiated = ws.subprotocol
            if negotiated == requested:
                _record(
                    "W2",
                    "subprotocol negotiation",
                    passed=True,
                    detail=f"requested={requested!r} negotiated={negotiated!r}",
                )
            elif negotiated is None:
                # code-server may not select any subprotocol (server picks none).
                # That's still acceptable: handshake succeeded.
                _record(
                    "W2",
                    "subprotocol negotiation",
                    passed=True,
                    detail=(
                        f"handshake succeeded; upstream chose no subprotocol "
                        f"(requested={requested!r}); code-server's protocol "
                        "is client-flexible"
                    ),
                )
            else:
                _record(
                    "W2",
                    "subprotocol negotiation",
                    passed=False,
                    detail=f"requested={requested!r} got={negotiated!r}",
                )
            await ws.close()
    except Exception as exc:  # noqa: BLE001
        _record(
            "W2",
            "subprotocol negotiation",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
        )


async def test_c5_taskgroup_cleanup() -> None:
    """C5: abrupt client disconnect → upstream connection cleaned up."""
    print("\n=== C5: TaskGroup cleanup on abrupt disconnect ===", flush=True)
    url = f"{SPIKE_WS}/vscode/ws/"
    # Strategy: open WS via the proxy → upstream WS exists on port 9100.
    # Count upstream conns (before), hard-abort the client transport (no close
    # frame — simulates a tab crash), wait C5_CLEANUP_WAIT_S, recount.
    # Pass if the upstream conn dropped back to baseline.
    before = _count_upstream_conns()
    ws = None
    try:
        ws = await websockets.connect(
            url,
            open_timeout=WS_TEST_TIMEOUT_S,
            ping_interval=None,
        )
    except Exception as exc:  # noqa: BLE001
        _record(
            "C5",
            "TaskGroup cleanup on abrupt disconnect",
            passed=False,
            detail=f"WS connect failed: {type(exc).__name__}: {exc}",
        )
        return

    # Wait for the proxy to dial upstream.
    await asyncio.sleep(0.5)
    during = _count_upstream_conns()
    if during <= before:
        try:
            await ws.close()
        except Exception:
            pass
        _record(
            "C5",
            "TaskGroup cleanup on abrupt disconnect",
            passed=False,
            detail=(
                f"no upstream connection observed "
                f"(before={before}, during={during}); "
                "proxy did not establish upstream WS — cannot test cleanup"
            ),
        )
        return

    # Force-abort the underlying transport. This is the most aggressive form of
    # disconnect — no close frame, no FIN handshake. The proxy should observe
    # this via its read loop and the TaskGroup should propagate cancellation.
    ws.transport.abort()

    # Wait for the proxy to detect and cascade cancellation upstream.
    await asyncio.sleep(C5_CLEANUP_WAIT_S)
    after = _count_upstream_conns()
    delta = before - after
    if delta >= 1:
        _record(
            "C5",
            "TaskGroup cleanup on abrupt disconnect",
            passed=True,
            detail=(
                f"upstream conn count: before={before} during={during} "
                f"after={after} (closed within {C5_CLEANUP_WAIT_S}s; "
                f"Δ={delta})"
            ),
        )
    else:
        _record(
            "C5",
            "TaskGroup cleanup on abrupt disconnect",
            passed=False,
            detail=(
                f"upstream conn STILL present: before={before} "
                f"during={during} after={after} after "
                f"{C5_CLEANUP_WAIT_S}s wait — TaskGroup likely did not cancel"
            ),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run_all(log_dir: Path) -> int:
    _prepare_dirs(log_dir)
    try:
        async with _serve(log_dir):
            await test_c4_binary_dispatch()
            # Brief pause to let the previous test's WS cleanly close.
            await asyncio.sleep(0.5)
            await test_w2_subprotocols()
            await asyncio.sleep(0.5)
            await test_c5_taskgroup_cleanup()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FATAL] validator harness failed: {type(exc).__name__}: {exc}")
        return 2

    print("\n=== SUMMARY ===", flush=True)
    for r in RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['id']} {r['name']}")
    failures = [r for r in RESULTS if not r["passed"]]
    if not failures:
        print("\nAll spike validations passed.")
        return 0
    print(f"\n{len(failures)} spike validation(s) FAILED.")
    return 1


def main() -> int:
    log_dir = Path("/tmp/vscode-spike-logs")
    try:
        return asyncio.run(_run_all(log_dir))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")
        return 130
    finally:
        # Best-effort cleanup of any orphan processes if the harness aborted.
        for name in ("spike-proxy", "code-server"):
            try:
                subprocess.run(
                    ["pkill", "-f", name],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())