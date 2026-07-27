"""Lifecycle management for a single ``code-server`` process.

The VSCodeServerManager spawns a ``code-server`` binary so users can edit
project files in a browser editor fronted by the daemon's reverse proxy.

Security context (see phase1-plan.md):
    - C1: ``code-server`` MUST bind to ``127.0.0.1`` only. Loopback binding
      is enforced via ``--bind-addr 127.0.0.1:0`` and the
      ``VSCodeConfig.allow_remote`` flag (default ``False``) gates any
      non-loopback intent.
    - W4: The reverse proxy is the SOLE access path. The OS-assigned
      loopback port is never advertised externally; callers must obtain
      it via the daemon API which proxies it.
    - R4: ``--auth none`` disables code-server's own auth. Auth is the
      responsibility of the daemon/reverse proxy layer, not code-server.

Limitation:
    - S3: ``os.killpg`` may not reach child processes that called
      ``setsid`` (language servers, extension hosts). This is documented;
      full cleanup of orphaned detached children is out of scope here.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import signal
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx

from ..config import VSCodeConfig
from ..constants import (
    VSCODE_DEFAULT_USER_DATA_DIR,
    VSCODE_HEALTH_CHECK_INTERVAL_S,
    VSCODE_HEALTH_TIMEOUT_S,
    VSCODE_LOG_BUFFER_LIMIT,
    VSCODE_PID_FILENAME,
    VSCODE_PORT_DETECTION_POLL_S,
    VSCODE_STARTUP_TIMEOUT_S,
    VSCODE_STOP_GRACE_S,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


# Cap on the size of the log tail attached to crash diagnostic messages.
# Bounds exception messages and the resulting 503 JSON response so a single
# pathological multi-MB line from code-server cannot bloat the error payload.
# 16 KB is generous for a "why did it die" diagnostic.
VSCODE_CRASH_LOG_TAIL_MAX_BYTES: int = 16 * 1024  # 16 KB


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class VSCodeServerError(Exception):
    """Base exception for VSCodeServerManager errors."""


class VSCodeServerNotInstalledError(VSCodeServerError):
    """code-server binary not found."""


class VSCodeServerStartError(VSCodeServerError):
    """Failed to spawn code-server."""


class VSCodeServerTimeoutError(VSCodeServerError):
    """Timeout waiting for port detection or health check."""


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class VSCodeServerState:
    """In-memory state for the code-server process."""

    status: Literal["stopped", "starting", "running", "crashed", "stopping"] = (
        "stopped"
    )
    pid: Optional[int] = None
    pgid: Optional[int] = None
    port: Optional[int] = None
    started_at: Optional[datetime] = None
    workdir: Optional[str] = None
    config: Optional[VSCodeConfig] = None
    log_buffer: bytearray = field(default_factory=bytearray)
    log_spill_path: Optional[str] = None
    reader_task: Optional[asyncio.Task] = None
    health_task: Optional[asyncio.Task] = None
    watchdog_task: Optional[asyncio.Task] = None
    last_error: Optional[str] = None
    exit_code: Optional[int] = None
    user_stopped: bool = False


# ---------------------------------------------------------------------------
# VSCodeServerManager
# ---------------------------------------------------------------------------


class VSCodeServerManager:
    """Manages the lifecycle of a single code-server process.

    Security: ALWAYS binds to 127.0.0.1 with ``--auth none`` (C1/W4/R4).
    The reverse proxy is the SOLE access path to the editor.

    Limitation (S3): ``os.killpg`` may not reach detached child processes
    (language servers, extension hosts that called ``setsid``). Best-effort
    cleanup only; orphaned detached children are documented and out of
    scope here.
    """

    # Port detection regex for code-server stdout, e.g.
    # "HTTP server listening on http://127.0.0.1:41293"
    _PORT_RE = re.compile(r"HTTP server listening on http://[\d.]+:(\d+)")

    def __init__(
        self,
        config: VSCodeConfig,
        data_dir: str,
        workdir: Optional[str] = None,
    ) -> None:
        """Initialize the manager.

        Args:
            config: VSCode configuration (binary_path, allow_remote, ...).
            data_dir: Writable directory for PID file and default
                user-data dir.
            workdir: Directory code-server opens as its workspace. If
                ``None``, the daemon's CWD is used at start time.
        """
        self.config = config
        self.data_dir = data_dir
        self.workdir = workdir
        self.state = VSCodeServerState(config=config)
        self.pid_file_path = os.path.join(data_dir, VSCODE_PID_FILENAME)
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    # -- public API --------------------------------------------------------

    async def start(self) -> VSCodeServerState:
        """Spawn code-server if not already running. Idempotent.

        Returns:
            The current state after the start attempt.

        Raises:
            VSCodeServerNotInstalledError: binary not found.
            VSCodeServerStartError: spawn failed or process exited
                during startup before reporting a port.
            VSCodeServerTimeoutError: port detection timed out.
        """
        async with self._lock:
            if self.is_running():
                return self.state

            # Security note (informational): C1/W4 — loopback only.
            if not self.config.allow_remote:
                logger.info(
                    "VSCode allow_remote=false; code-server will bind "
                    "127.0.0.1 only."
                )

            # Resolve binary (raises VSCodeServerNotInstalledError on miss)
            binary_path = self._resolve_binary()

            # Build command — C1/W4: bind 127.0.0.1, R4: --auth none
            user_data_dir = self.config.user_data_dir or os.path.join(
                self.data_dir, VSCODE_DEFAULT_USER_DATA_DIR
            )
            workdir = self.workdir or os.getcwd()

            # Ensure dirs exist
            os.makedirs(user_data_dir, exist_ok=True)
            os.makedirs(self.data_dir, exist_ok=True)

            command = [
                binary_path,
                "--bind-addr", "127.0.0.1:0",  # W4: localhost-only, OS-assigned port
                "--auth", "none",                # R4: proxy is sole access path
                "--disable-workspace-trust",
                "--user-data-dir", user_data_dir,
                workdir,
            ]

            self.state.status = "starting"
            self.state.workdir = workdir
            self.state.config = self.config
            self.state.last_error = None
            self.state.exit_code = None
            self.state.user_stopped = False

            # Spawn — start_new_session puts the child in its own process
            # group so we can later signal the whole tree via killpg.
            subproc_kwargs: dict[str, Any] = {}
            if sys.platform != "win32":
                subproc_kwargs["start_new_session"] = True

            # Build child env. Inheriting the daemon's environment as-is
            # causes ``EADDRINUSE`` on startup: code-server reads ``$PORT``
            # to override ``--bind-addr``'s port, so the daemon's own
            # ``PORT`` (its listen port) makes code-server try to bind to
            # the same address. Strip vars that code-server reads and that
            # could similarly collide; keep everything else (PATH, HOME,
            # etc.) intact.
            child_env = os.environ.copy()
            child_env.pop("PORT", None)
            child_env.pop("CODE_SERVER_CONFIG_FILE", None)
            child_env.pop("CS_DISABLE_FILE_DOWNLOADS", None)
            child_env.pop("CS_DISABLE_GETTING_STARTED_OVERRIDE", None)

            try:
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=workdir,
                    env=child_env,
                    **subproc_kwargs,
                )
            except FileNotFoundError as exc:
                self.state.status = "stopped"
                raise VSCodeServerStartError(
                    f"Failed to spawn code-server: {exc}"
                ) from exc
            except Exception as exc:
                self.state.status = "stopped"
                raise VSCodeServerStartError(
                    f"Failed to spawn code-server: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            self.state.pid = self._process.pid
            try:
                self.state.pgid = (
                    os.getpgid(self._process.pid)
                    if self._process.pid
                    else None
                )
            except OSError:
                self.state.pgid = None

            # Start reader task (log capture + port detection)
            self.state.reader_task = asyncio.create_task(
                self._reader_loop(),
                name="vscode-reader",
            )

            # Wait for port detection (reader parses the port line)
            try:
                await asyncio.wait_for(
                    self._wait_for_port(),
                    timeout=VSCODE_STARTUP_TIMEOUT_S,
                )
            except asyncio.TimeoutError as exc:
                await self._kill_orphan()
                self.state.status = "stopped"
                raise VSCodeServerTimeoutError(
                    f"code-server did not report a port within "
                    f"{VSCODE_STARTUP_TIMEOUT_S}s"
                ) from exc

            # Start health check loop
            self.state.health_task = asyncio.create_task(
                self._health_check_loop(),
                name="vscode-health",
            )

            # Start watchdog (detects unexpected exit)
            self.state.watchdog_task = asyncio.create_task(
                self._watchdog_loop(),
                name="vscode-watchdog",
            )

            # Persist PID file for crash recovery (atomic write)
            self._write_pid_file()

            # Mark running
            self.state.status = "running"
            self.state.started_at = datetime.now(timezone.utc)

            logger.info(
                "code-server started: pid=%d port=%d workdir=%s",
                self.state.pid,
                self.state.port,
                workdir,
            )
            return self.state

    async def stop(self) -> VSCodeServerState:
        """Stop code-server with SIGTERM -> grace -> SIGKILL escalation.

        Idempotent. Mirrors the ``stop_process`` pattern from
        ``proc_tools.py``: SIGTERM the whole process group, wait a grace
        period, then escalate to SIGKILL.

        Returns:
            The state after stop.
        """
        async with self._lock:
            if self.state.status in ("stopped", "stopping"):
                return self.state

            # Mark BEFORE signalling so the watchdog doesn't flip us to
            # "crashed" when the kill takes effect.
            self.state.user_stopped = True
            self.state.status = "stopping"

            # Cancel background tasks first so they don't fight teardown.
            tasks_to_cancel = [
                t
                for t in (
                    self.state.reader_task,
                    self.state.health_task,
                    self.state.watchdog_task,
                )
                if t is not None and not t.done()
            ]
            for task in tasks_to_cancel:
                task.cancel()
            if tasks_to_cancel:
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

            process = self._process
            if process is None or process.returncode is not None:
                self.state.status = "stopped"
                self._remove_pid_file()
                return self.state

            is_unix = sys.platform != "win32"

            # SIGTERM the whole process group.
            # NOTE (S3): killpg may miss detached children that called
            # setsid (language servers, extension hosts). Best-effort.
            #
            # W8: Use the pgid captured at spawn time (state.pgid), NOT
            # ``os.getpgid(process.pid)``. By the time we signal, the PID
            # could have been reused by an unrelated process; re-resolving
            # the pgid at signal time would risk targeting the wrong group.
            if is_unix:
                if self.state.pgid is not None:
                    try:
                        os.killpg(self.state.pgid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        try:
                            process.send_signal(signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                else:
                    # pgid wasn't captured at spawn; fall back to the
                    # subprocess handle's signal API (best-effort).
                    try:
                        process.send_signal(signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            else:
                try:
                    process.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass

            # Wait for graceful exit.
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=VSCODE_STOP_GRACE_S
                )
            except asyncio.TimeoutError:
                # Grace expired — escalate to SIGKILL.
                logger.warning(
                    "code-server did not exit within %ds; sending SIGKILL",
                    VSCODE_STOP_GRACE_S,
                )
                if is_unix:
                    if self.state.pgid is not None:
                        try:
                            os.killpg(self.state.pgid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                    else:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                else:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "code-server did not exit within 2s of SIGKILL"
                    )

            # Finalize
            self.state.exit_code = process.returncode
            self.state.status = "stopped"
            self._remove_pid_file()
            logger.info(
                "code-server stopped: pid=%d exit_code=%s",
                self.state.pid,
                self.state.exit_code,
            )
            return self.state

    async def ensure_running(self) -> VSCodeServerState:
        """Start if not currently running. Idempotent.

        Returns:
            The current state.
        """
        if not self.is_running():
            return await self.start()
        return self.state

    def is_running(self) -> bool:
        """Check if the process is alive and state says running.

        For adopted processes (``self._process is None`` but ``state.status
        == "running"`` after ``attach_existing()``), falls back to a PID
        liveness probe via ``os.kill(pid, 0)`` since we have no subprocess
        handle to inspect (C2).

        Returns:
            ``True`` only if status is running and the OS still knows
            about the PID. If the adopted PID is no longer alive, flips
            ``state.status`` to ``"crashed"`` and records ``last_error``.
        """
        if self.state.status != "running":
            return False
        if self.state.pid is None:
            return False

        if self._process is not None and self._process.returncode is not None:
            return False  # process exited

        if self._process is not None:
            # Live subprocess handle — cross-check with the OS to detect
            # zombie/reaping races.
            try:
                os.kill(self.state.pid, 0)
            except (OSError, ProcessLookupError):
                return False
            return True

        # C2: No subprocess handle (adopted process) — check via PID.
        try:
            os.kill(self.state.pid, 0)
        except (OSError, ProcessLookupError):
            self.state.status = "crashed"
            self.state.last_error = "Adopted process no longer alive"
            return False
        return True

    def get_status(self) -> VSCodeServerState:
        """Return a snapshot of the current state.

        The returned object is a shallow copy of the scalar fields; the
        ``log_buffer`` is shared by reference (read-only intent) and the
        background tasks are intentionally NOT copied (callers should not
        await them).

        Returns:
            A new ``VSCodeServerState`` snapshot.
        """
        return VSCodeServerState(
            status=self.state.status,
            pid=self.state.pid,
            pgid=self.state.pgid,
            port=self.state.port,
            started_at=self.state.started_at,
            workdir=self.state.workdir,
            config=self.state.config,
            log_buffer=self.state.log_buffer,
            log_spill_path=self.state.log_spill_path,
            # Tasks intentionally not propagated.
            last_error=self.state.last_error,
            exit_code=self.state.exit_code,
            user_stopped=self.state.user_stopped,
        )

    def get_port(self) -> Optional[int]:
        """Return the bound port, or None if not yet detected."""
        return self.state.port

    async def attach_existing(self) -> bool:
        """Adopt a running code-server from the PID file (crash recovery).

        Reads the PID file written by a previous ``start()`` call, checks
        that the process is still alive, and adopts it. Stdout cannot be
        reattached after the fact, so live log capture is limited until a
        restart.

        Returns:
            ``True`` if a live process was adopted, ``False`` otherwise
            (no PID file, unreadable, stale PID, etc.).
        """
        if not os.path.exists(self.pid_file_path):
            return False

        try:
            with open(self.pid_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read PID file: %s", exc)
            self._remove_pid_file()
            return False

        pid = data.get("pid")
        if not pid:
            self._remove_pid_file()
            return False

        # Check the process is actually alive.
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            logger.info("Stale PID file for pid %d; removing", pid)
            self._remove_pid_file()
            return False

        # W4: Verify the PID is actually code-server. PID reuse could
        # otherwise cause us to adopt an unrelated process and try to
        # signal its (now-wrong) process group on stop.
        if not self._verify_pid_is_code_server(pid):
            logger.warning(
                "PID %d is not code-server (PID reuse?); "
                "removing stale PID file",
                pid,
            )
            self._remove_pid_file()
            return False

        # Adopt. We cannot reattach stdout, so log capture is limited.
        self.state.pid = pid
        self.state.pgid = data.get("pgid")
        self.state.port = data.get("port")
        self.state.started_at = (
            datetime.fromisoformat(data["started_at"])
            if data.get("started_at")
            else None
        )
        self.state.status = "running"
        logger.warning(
            "Adopted existing code-server pid=%d port=%s; stdout not "
            "reattached. Restart recommended for full log capture.",
            pid,
            self.state.port,
        )
        return True

    async def cleanup(self) -> None:
        """Stop the process if running. Called during daemon shutdown.

        Idempotent: safe to call when already stopped or never started.
        """
        if self.is_running() or self.state.status in ("starting", "stopping"):
            try:
                await self.stop()
            except Exception as exc:
                logger.warning("cleanup stop failed: %s", exc)

    def get_logs(self, tail: int = 100) -> str:
        """Return the last N lines from the in-memory log buffer.

        Args:
            tail: Number of trailing lines to return. ``<= 0`` returns "".

        Returns:
            Newline-joined tail of the captured logs, or "" if empty.
        """
        if tail <= 0:
            return ""
        text = self.state.log_buffer.decode(errors="replace")
        lines = text.splitlines()
        if not lines:
            return ""
        return "\n".join(lines[-tail:])

    # -- private helpers ---------------------------------------------------

    def _resolve_binary(self) -> str:
        """Resolve the code-server binary path.

        Order:
            1. ``config.binary_path`` if set (must exist and be executable).
            2. ``shutil.which("code-server")`` (PATH lookup).
            3. Fallback common install locations (Homebrew, system, user-local).
            4. Raise :class:`VSCodeServerNotInstalledError`.

        Returns:
            Absolute path to the code-server binary.

        Raises:
            VSCodeServerNotInstalledError: binary not found.
        """
        if self.config.binary_path:
            if os.path.isfile(self.config.binary_path) and os.access(
                self.config.binary_path, os.X_OK
            ):
                return self.config.binary_path
            raise VSCodeServerNotInstalledError(
                "Configured code-server binary not found or not "
                f"executable: {self.config.binary_path}"
            )
        found = shutil.which("code-server")
        if found:
            return found
        # Fallback: daemon process PATH may miss common install locations
        # (e.g. Homebrew on Apple Silicon). Probe well-known paths before
        # giving up so a real install is not misreported as missing.
        fallback_paths = [
            "/opt/homebrew/bin/code-server",
            "/usr/local/bin/code-server",
            os.path.expanduser("~/.local/bin/code-server"),
            "/usr/bin/code-server",
        ]
        for path in fallback_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                logger.info("Resolved code-server via fallback path: %s", path)
                return path
        searched = ", ".join(fallback_paths)
        raise VSCodeServerNotInstalledError(
            "code-server not found in PATH or common install locations. "
            f"Searched: {searched}. "
            "Install: curl -fsSL https://code-server.dev/install.sh | sh"
        )

    async def _wait_for_port(self) -> None:
        """Poll until ``state.port`` is set by the reader loop.

        Raises:
            VSCodeServerStartError: if the process exits before a port
                line is seen.
        """
        while self.state.port is None:
            await asyncio.sleep(VSCODE_PORT_DETECTION_POLL_S)
            if (
                self._process is not None
                and self._process.returncode is not None
            ):
                # Record exit_code and last_error BEFORE flipping status
                # so the crash is observable via get_status() / the API,
                # not just via the exception string. Mirrors the
                # watchdog's behavior on unexpected runtime exits. Status
                # stays "stopped" (NOT "crashed") for consistency with the
                # spawn-failure paths.
                self.state.exit_code = self._process.returncode
                self.state.last_error = (
                    f"code-server exited during startup "
                    f"(code={self._process.returncode})"
                )

                # Mark state stopped before raising so callers and the
                # watchdog don't leave the manager stuck in "starting".
                self.state.status = "stopped"

                # Cancel the reader task so it doesn't outlive the
                # manager on this crash path (CancelledError is re-raised
                # in _reader_loop, which is the normal teardown shape).
                if (
                    self.state.reader_task is not None
                    and not self.state.reader_task.done()
                ):
                    self.state.reader_task.cancel()
                    await asyncio.gather(
                        self.state.reader_task, return_exceptions=True
                    )

                # Surface the crash output so the operator can see why
                # code-server failed (e.g. bind error, missing dependency).
                # Decode the in-memory log buffer and take the tail of the
                # last ~50 lines. This buffer is bounded by
                # VSCODE_LOG_BUFFER_LIMIT (with oldest-half spill), so it
                # is always bounded in size.
                log_tail = ""
                if self.state.log_buffer:
                    try:
                        decoded = self.state.log_buffer.decode(
                            "utf-8", errors="replace"
                        )
                        lines = decoded.strip().splitlines()
                        log_tail = "\n".join(lines[-50:])
                    except Exception as exc:
                        log_tail = ""
                        logger.debug(
                            "vscode log buffer decode failed: %s", exc
                        )

                # Cap total tail size to avoid bloating the exception /
                # 503 response with a single pathological multi-MB line.
                # 16 KB is generous for a "why did it die" diagnostic.
                tail_bytes = log_tail.encode("utf-8", errors="replace")
                if len(tail_bytes) > VSCODE_CRASH_LOG_TAIL_MAX_BYTES:
                    log_tail = (
                        tail_bytes[:VSCODE_CRASH_LOG_TAIL_MAX_BYTES]
                        .decode("utf-8", errors="replace")
                        + "\n... [truncated]"
                    )

                logger.error(
                    "code-server exited during startup (code=%s)"
                    "\n--- code-server output (tail) ---\n%s",
                    self._process.returncode,
                    log_tail or "<empty>",
                )

                msg = (
                    f"code-server exited during startup "
                    f"(code={self._process.returncode})"
                )
                if log_tail:
                    msg += (
                        f"\n--- code-server output (tail) ---\n{log_tail}"
                    )
                raise VSCodeServerStartError(msg)

    async def _reader_loop(self) -> None:
        """Read merged stdout+stderr into memory + spill file. Parse port.

        Mirrors the reader pattern from ``proc_tools._capture_output``:
        64KB chunks, in-memory ``log_buffer`` capped at
        ``VSCODE_LOG_BUFFER_LIMIT`` with spill-to-file of the oldest half
        when the cap is exceeded. ``CancelledError`` is re-raised (normal
        teardown path); other exceptions are logged and recorded.
        """
        process = self._process
        if process is None or process.stdout is None:
            return

        _CHUNK_BYTES = 64 * 1024
        try:
            while True:
                chunk = await process.stdout.read(_CHUNK_BYTES)
                if not chunk:
                    # EOF — child closed stdout; watchdog will update status.
                    break

                # 1. Append to in-memory buffer.
                self.state.log_buffer.extend(chunk)

                # 2. Spill oldest half if over the cap.
                if len(self.state.log_buffer) > VSCODE_LOG_BUFFER_LIMIT:
                    await self._spill_oldest_half()

                # 3. Parse for the port line (only until first hit).
                if self.state.port is None:
                    match = self._PORT_RE.search(
                        chunk.decode(errors="replace")
                    )
                    if match:
                        self.state.port = int(match.group(1))
        except asyncio.CancelledError:
            # Normal teardown path (stop / cleanup). Re-raise; do not
            # suppress so the awaiting task observes cancellation.
            raise
        except Exception as exc:
            # Use type name only to avoid leaking secrets in str(exc).
            self.state.last_error = f"reader crashed: {type(exc).__name__}"
            logger.warning("vscode reader crashed: %s", exc)

    async def _spill_oldest_half(self) -> None:
        """Flush the oldest half of the log buffer to a spill file.

        Bounds in-memory log usage to roughly half the cap per spill
        while keeping recent context in RAM for fast ``get_logs`` reads.
        Creates the spill file lazily on first spill.
        """
        split_at = len(self.state.log_buffer) // 2
        if split_at <= 0:
            return
        oldest = bytes(self.state.log_buffer[:split_at])
        del self.state.log_buffer[:split_at]

        try:
            if self.state.log_spill_path is None:
                fd, path = tempfile.mkstemp(prefix="vscode-", suffix=".log")
                self.state.log_spill_path = path
            else:
                fd = os.open(
                    self.state.log_spill_path, os.O_WRONLY | os.O_APPEND
                )
            try:
                os.write(fd, oldest)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("Failed to spill vscode logs: %s", exc)

    async def _health_check_loop(self) -> None:
        """Periodically GET ``/healthz`` to verify code-server is alive.

        Failures are logged but do NOT flip status to ``crashed`` — that
        is the watchdog's job (it observes the actual process exit).
        Health checks just surface lags/unresponsiveness early.
        """
        # Wait briefly for port detection to complete.
        for _ in range(50):
            if self.state.port is not None:
                break
            await asyncio.sleep(0.1)

        if self.state.port is None:
            return

        url = f"http://127.0.0.1:{self.state.port}/healthz"
        try:
            async with httpx.AsyncClient(
                timeout=VSCODE_HEALTH_TIMEOUT_S
            ) as client:
                while True:
                    await asyncio.sleep(VSCODE_HEALTH_CHECK_INTERVAL_S)
                    if self.state.status != "running":
                        break
                    try:
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            logger.warning(
                                "vscode health check returned %d",
                                resp.status_code,
                            )
                    except (httpx.RequestError, httpx.TimeoutException) as exc:
                        # Watchdog handles actual process death; we log.
                        logger.warning("vscode health check failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self) -> None:
        """Monitor process exit; mark ``crashed`` if not user-initiated.

        Polls ``process.returncode`` once per second. On exit, records
        the exit code and either leaves the status as-is (if ``stop()``
        is driving teardown via ``user_stopped``) or marks ``crashed``.
        """
        process = self._process
        if process is None:
            return

        try:
            while True:
                await asyncio.sleep(1.0)
                if process.returncode is not None:
                    self.state.exit_code = process.returncode
                    if self.state.user_stopped:
                        # ``stop()`` is driving teardown and will set the
                        # final status; don't clobber it here.
                        pass
                    else:
                        self.state.status = "crashed"
                        self.state.last_error = (
                            "code-server exited unexpectedly "
                            f"(code={process.returncode})"
                        )
                        logger.warning(
                            "code-server crashed: pid=%d exit_code=%d",
                            self.state.pid,
                            process.returncode,
                        )
                    # Stop health checks once the process is gone.
                    if (
                        self.state.health_task
                        and not self.state.health_task.done()
                    ):
                        self.state.health_task.cancel()
                    break
        except asyncio.CancelledError:
            raise

    async def _kill_orphan(self) -> None:
        """Best-effort SIGKILL when startup fails after spawn.

        Used by ``start()`` to clean up a half-spawned process that never
        reported a port within the startup timeout.
        """
        process = self._process
        if process is None or process.returncode is not None:
            return
        # NOTE (S3): killpg may miss detached children. Best-effort.
        try:
            if sys.platform != "win32":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
            else:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        except Exception as exc:
            logger.warning("Failed to kill orphan vscode: %s", exc)

    def _write_pid_file(self) -> None:
        """Atomically write the PID file.

        Writes to a temp file in the SAME directory as the target (so
        ``os.replace`` is an atomic rename on the same filesystem), then
        renames into place. ``fsync`` is best-effort (not all filesystems
        support it, e.g. some network mounts).
        """
        payload = {
            "pid": self.state.pid,
            "pgid": self.state.pgid,
            "port": self.state.port,
            "started_at": (
                self.state.started_at.isoformat()
                if self.state.started_at
                else None
            ),
        }
        # Temp in same dir so os.replace is atomic (same filesystem).
        fd, tmp_path = tempfile.mkstemp(
            prefix=".vscode-server.pid.",
            dir=self.data_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync unsupported on some filesystems — non-fatal.
                    pass
            os.replace(tmp_path, self.pid_file_path)
        except Exception:
            # Best-effort cleanup of the temp file on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _remove_pid_file(self) -> None:
        """Remove the PID file (best-effort, idempotent)."""
        try:
            os.unlink(self.pid_file_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove PID file: %s", exc)

    def _verify_pid_is_code_server(self, pid: int) -> bool:
        """Verify that ``pid`` is actually a code-server process (W4).

        Guards against PID reuse: a stale PID file could otherwise let us
        adopt an unrelated process and later try to signal its (now-wrong)
        process group on stop.

        On Linux, reads ``/proc/{pid}/cmdline``. On macOS / other Unix,
        shells out to ``ps -p {pid} -o command=``. Returns ``True`` iff
        the command line contains the substring ``code-server``.

        Args:
            pid: Candidate PID to verify.

        Returns:
            ``True`` if ``pid`` resolves to a code-server process.
        """
        try:
            if sys.platform == "linux":
                cmdline_path = f"/proc/{pid}/cmdline"
                try:
                    with open(cmdline_path, "r") as f:
                        cmdline = f.read()
                    return "code-server" in cmdline
                except (OSError, FileNotFoundError):
                    return False
            else:
                # macOS and other Unix: use ps.
                import subprocess

                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return (
                    result.returncode == 0
                    and "code-server" in result.stdout
                )
        except Exception:
            # Any unexpected error => treat as not-ours; caller will
            # remove the stale PID file.
            return False
