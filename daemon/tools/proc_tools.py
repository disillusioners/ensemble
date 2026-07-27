"""Background process tools for long-running commands.

Provides agents with the ability to start long-running processes (dev
servers, test watchers, file watchers, debug sessions), inspect their
output on demand, and tear them down cleanly. Unlike the ``bash`` tool
which blocks until a command finishes, these tools return a
``process_id`` immediately and let the agent drive the lifecycle.

Architecture
------------
A module-level singleton :class:`BackgroundProcessManager` tracks every
spawned process per instance. Each process gets:

* An async stream-reader task on the merged stdout/stderr pipe that
  feeds the **hybrid** log buffer.
* A **memory ring buffer** capped at ``_MEMORY_BUFFER_LIMIT_BYTES``
  (4 MB). When the cap is hit, the oldest half is flushed to a
  ``tempfile.NamedTemporaryFile`` and the memory buffer retains only
  the newest half — this keeps the in-memory footprint bounded while
  preserving recent context for fast reads.
* A ``tempfile.NamedTemporaryFile`` accumulating older history. As
  memory spills, the file grows. ``proc_logs`` reads the file's tail
  when the memory buffer cannot satisfy the requested line count.

The instance lifecycle code (``daemon/manager.py``) calls
:func:`BackgroundProcessManager.cleanup_instance` on instance
termination so orphaned background processes don't survive the parent.

Security
--------
Process spawns respect ``start_new_session=True`` (Unix) / new process
group so that ``proc_stop`` (SIGTERM, then SIGKILL on ``force=True``)
kills the entire process tree — mirroring :mod:`daemon.tools.bash`'s
kill semantics. The singleton enforces a hard cap of
``MAX_PROCESSES_PER_INSTANCE`` concurrent processes per instance.

Tools are gated through the standard ``tools.allow`` / ``tools.deny``
meta.json filter via the ``proc`` category key registered by
``register_tool_category``. Wire-up (adding ``proc`` to
``CATEGORY_MODULES`` and ``create_instance_tools``) is a separate task.
"""

from __future__ import annotations

import asyncio
import errno
import glob
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from secrets import token_hex
from typing import Any, List, Optional, Union

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Background Processes"
CATEGORY_DOC = """\
Run long-running commands (dev servers, watchers, test runners) in the
background and read their logs on demand. Non-blocking — the agent
controls the lifecycle.

**When to use ``proc_*`` vs ``bash``**:
- ``bash``: short-lived commands (``ls``, ``cat``, ``git``, ``curl``) —
  blocks until the command exits and returns output.
- ``proc_*``: dev servers, watchers, long-running debug sessions —
  returns a ``process_id`` immediately, agent controls start/stop.
"""


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

#: Hard cap on concurrent background processes per instance.
MAX_PROCESSES_PER_INSTANCE: int = 10

#: Maximum bytes retained in the in-memory ring buffer per process.
#: 4 MB ≈ 100k lines at avg 40 chars/line. When memory hits this cap,
#: the oldest half is flushed to disk and memory keeps only the newest
#: half — bounding footprint while preserving recent tail.
_MEMORY_BUFFER_LIMIT_BYTES: int = 4 * 1024 * 1024  # 4 MB

#: When memory buffer exceeds the limit, flush the OLDEST half and keep
#: the newest half. Chosen so a single spill is amortized over many
#: appends — a 4 MB buffer at 1 KB/s stdout fills in ~1 hour.
_MEMORY_SPILL_KEEP_RATIO: float = 0.5

#: Default lines returned by ``proc_logs`` when caller does not specify.
_DEFAULT_LOG_LINES: int = 50

#: Max lines that ``proc_logs`` will materialize in one call. Protects
#: against runaway reads from a misbehaving agent.
_MAX_LOG_LINES: int = 5000

#: Seconds to wait between SIGTERM and SIGKILL during graceful stop.
_STOP_GRACE_SECONDS: float = 5.0

#: Lines of tail output returned by ``proc_stop`` for confirmation.
_STOP_TAIL_LINES: int = 20

#: Number of hex chars in a process_id. ``8`` → ``proc-a3b2c1d4``.
_PROCESS_ID_HEX_LEN: int = 4  # token_hex(4) yields 8 hex chars

#: Environment variable name used to tag spawned subprocesses so that
#: ``proc_stop`` can verify the PID at kill time still belongs to us
#: (defense against PID reuse — see ``_verify_pid_ownership``).
#: The value is the same as the ``process_id`` (e.g. ``proc-a3b2c1d4``).
_TRACKING_ENV_VAR: str = "ENSEMBLE_PROC_TRACKING_ID"


# ---------------------------------------------------------------------------
# ProcessInfo + BackgroundProcessManager
# ---------------------------------------------------------------------------


@dataclass
class ProcessInfo:
    """State for a single background process.

    Attributes:
        process_id: ``proc-{8 hex chars}``.
        instance_id: Owning instance id.
        command: Command string (shell form) or argv list (exec form).
        proc: The asyncio subprocess handle. ``None`` until spawned.
        memory_buffer: In-memory tail of recent output (newest at end).
        file_handle: Append-only temp file accumulating older history
            that spilled from memory. ``None`` until first spill.
        file_path: Absolute path of the spill file (for late reads /
            cleanup diagnostics). ``None`` until first spill.
        started_at: UTC datetime when the process was spawned.
        status: Lifecycle marker — ``"running" | "exited" | "killed"
            | "error"``.
        exit_code: Process exit code once the process has terminated.
            ``None`` while still running.
        reader_task: Background asyncio task draining the merged
            stdout/stderr pipe into the memory + file buffers. Stored
            so the manager can ``cancel()`` it on stop / cleanup to
            release the pipe promptly.
        timeout_task: Background asyncio task that auto-kills the
            process after the user-supplied timeout (0 = disabled).
        timed_out: ``True`` if the process was killed by the timeout
            task (informational; for ``proc_status``).
        user_stopped: ``True`` if the process was killed by an
            explicit ``proc_stop`` call. Like ``timed_out``, this is
            authoritative in :func:`_drain_exit_code` so the user-driven
            stop supersedes any exit-code signal mapping.
        last_error: Last error string surfaced by the reader / killer
            (informational; for ``proc_status``).
        tracking_id: PID-reuse defense tag. Same value as
            ``process_id`` (e.g. ``proc-a3b2c1d4``). Injected into the
            child environment as ``ENSEMBLE_PROC_TRACKING_ID`` at spawn
            time and verified before each kill. Empty string on older
            ``ProcessInfo`` instances (fail-open: skip verification).
    """

    process_id: str
    instance_id: str
    command: Union[str, List[str]]
    proc: Optional[asyncio.subprocess.Process] = None
    memory_buffer: bytearray = field(default_factory=bytearray)
    # ``tempfile.NamedTemporaryFile`` is typed as an overload (it is
    # a factory function in the type stubs, not a class). Use ``Any``
    # for the attribute — the value is guaranteed to be an
    # open file-like with the ``write`` / ``close`` / ``name``
    # attributes we use.
    file_handle: Optional[Any] = None
    file_path: Optional[str] = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    status: str = "running"
    exit_code: Optional[int] = None
    reader_task: Optional[asyncio.Task] = None
    exit_task: Optional[asyncio.Task] = None
    timeout_task: Optional[asyncio.Task] = None
    timed_out: bool = False
    user_stopped: bool = False
    last_error: Optional[str] = None
    # PID-reuse defense: tag written into the child environment as
    # ``ENSEMBLE_PROC_TRACKING_ID=<tracking_id>`` at spawn time. Before
    # any kill we read this back from the live process and refuse to
    # signal if the PID has been recycled to an unrelated process.
    # Defaults to ``""`` for backwards compatibility — old ``ProcessInfo``
    # instances constructed without it (e.g. in tests) skip the
    # ownership verification by design (fail-open).
    tracking_id: str = ""
    # C1 fix: tracks whether the spill file's last byte is ``\n``.
    # Used by ``_get_recent_lines`` to detect the split-line at the
    # memory/file boundary and stitch the partial line correctly.
    # Defaults True so existing tests that never spill pass without
    # any extra branching.
    _file_ends_with_newline: bool = True


def _new_process_id() -> str:
    """Return a fresh ``proc-{8 hex chars}`` id."""
    return f"proc-{token_hex(_PROCESS_ID_HEX_LEN)}"


# ---------------------------------------------------------------------------
# PID ownership verification (Layer 3 of defense-in-depth kill safety)
# ---------------------------------------------------------------------------
# After a tracked subprocess exits, the OS is free to RECYCLE its PID to
# a brand-new, unrelated process. If we then call ``killpg(getpgid(pid),
# SIGKILL)`` we would hit that unrelated process. Defense Layer 3 reads
# back the environment variable we injected at spawn time and aborts the
# kill if it doesn't match.
#
# Return contract for ``_verify_pid_ownership``:
#   True  → env var present and matches ``tracking_id`` — safe to kill.
#   False → env var absent or has a DIFFERENT value — PID was recycled;
#           ABORT the kill (PID does not belong to us).
#   None  → undetermined (unsupported OS, EPERM, ENOENT, command failed,
#           etc.) — fail-open: log a warning and let the kill proceed.
#           Layers 1 (status gate) and 2 (poll) still gate us.


def _verify_pid_ownership(pid: int, tracking_id: str) -> "Optional[bool]":
    """Check whether ``pid`` still belongs to our tracked process.

    Defense Layer 3 — PID reuse guard. The child was spawned with
    ``ENSEMBLE_PROC_TRACKING_ID=<tracking_id>`` in its environment. We
    read that value back from the live process and compare it to the
    ``tracking_id`` we recorded. If the OS has recycled the PID to an
    unrelated process, the env var will be absent or carry a different
    value — we then abort the kill.

    Cross-platform strategy:
      * Linux: read ``/proc/{pid}/environ`` (null-byte-separated
        ``KEY=VALUE`` entries). Cheap, no subprocess, no parsing issues.
      * macOS / BSD: ``ps eww -p <pid>`` (BSD-style — lowercase ``e``,
        ``ww``, ``-p <pid>``) prints the full env block after the
        command; we grep for the marker. Has the usual caveats of
        parsing ps output (locale, whitespace, quoting) but it's the
        only portable way without a C extension. The actual invocation
        in :func:`_verify_pid_ownership_ps` is
        ``["ps", "eww", "-p", str(pid)]``.
      * Windows / unknown: fail-open (``None``). The status gate and
        poll check already cover normal exit races; PID reuse on
        Windows is also much rarer in practice (PIDs are 32-bit and
        the kernel is more conservative about reuse).

    Never raises — every failure mode returns ``None`` and logs a
    warning. The kill path treats ``None`` as "proceed" so a buggy
    verification never blocks a legitimate stop.
    """
    if not tracking_id:
        # No tracking tag was recorded (legacy ProcessInfo, or test
        # stub). Skip verification — fail-open.
        return None

    if sys.platform.startswith("linux"):
        return _verify_pid_ownership_procfs(pid, tracking_id)
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        return _verify_pid_ownership_ps(pid, tracking_id)
    # Windows / other platforms: fail-open.
    logger.warning(
        "PID ownership verification not supported on platform %s; "
        "proceeding with kill (status gate + poll check still apply).",
        sys.platform,
    )
    return None


def _verify_pid_ownership_procfs(
    pid: int, tracking_id: str
) -> "Optional[bool]":
    """Linux backend: read ``/proc/{pid}/environ`` and check the tracker.

    On Linux each running process has a ``/proc/{pid}/environ`` file
    whose contents are the initial environment, encoded as null-byte
    separated ``KEY=VALUE`` entries. The file is readable by the owning
    user (and root). Returns ``None`` if the file is missing, unreadable,
    or the data is malformed.
    """
    try:
        # ``/proc/{pid}/environ`` is a virtual file; ``open()`` works
        # without ``os.path.exists`` checks. If the PID doesn't exist
        # we'll get ``FileNotFoundError`` (or ``ProcessLookupError``).
        with open(f"/proc/{pid}/environ", "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        # Process gone between ``poll()`` and here — raced to exit.
        # Treat as undetermined; the status gate / poll already covers
        # the "already exited" case.
        logger.warning(
            "PID ownership verification: /proc/%d/environ not found "
            "(process may have exited). Proceeding with kill.",
            pid,
        )
        return None
    except PermissionError:
        logger.warning(
            "PID ownership verification: /proc/%d/environ not readable "
            "(EPERM). Proceeding with kill (fail-open).",
            pid,
        )
        return None
    except OSError as exc:
        logger.warning(
            "PID ownership verification: /proc/%d/environ read failed "
            "(%s). Proceeding with kill (fail-open).",
            pid,
            exc,
        )
        return None

    # The environ entries are NUL-separated. Split on NUL and match
    # the exact entry to avoid false positives where the marker
    # ``ENSEMBLE_PROC_TRACKING_ID=<id>`` appears as a substring of
    # another env var's value (e.g. a hypothetical ``SOME_CONFIG=blob
    # ...ENSEMBLE_PROC_TRACKING_ID=proc-...rest`` would otherwise match
    # the needle substring and report "owned" wrongly). Since the
    # tracking_id is generated by ``token_hex`` it contains no ``=`` or
    # NUL, so an exact entry match is safe and unambiguous.
    entries = data.split(b"\x00")
    needle = f"{_TRACKING_ENV_VAR}={tracking_id}".encode("utf-8")
    if needle in entries:
        return True
    # The MARKER key is present with a DIFFERENT value → PID was
    # recycled to a process that happened to have the same env var
    # set (very unlikely with our unique token, but we still check).
    marker_prefix = (_TRACKING_ENV_VAR + "=").encode("utf-8")
    marker_entries = [e for e in entries if e.startswith(marker_prefix)]
    if marker_entries:
        logger.warning(
            "PID ownership verification: marker %s present at PID %d "
            "but value differs (PID likely recycled). Aborting kill.",
            _TRACKING_ENV_VAR,
            pid,
        )
        return False
    # Marker absent entirely. Most likely the PID was recycled to a
    # process that doesn't have our env var at all. ABORT — this is
    # the dangerous case.
    logger.warning(
        "PID ownership verification: %s not found in env of PID %d "
        "(PID may have been recycled). Aborting kill.",
        _TRACKING_ENV_VAR,
        pid,
    )
    return False


def _verify_pid_ownership_ps(
    pid: int, tracking_id: str
) -> "Optional[bool]":
    """macOS / BSD backend: use ``ps eww -p {pid}`` to dump env.

    On macOS and the BSDs, the BSD-style flag ``ps eww -p <pid>`` (no
    leading dash) appends the env block after the command line. The
    ``-ww`` removes column-width limits so long env values aren't
    truncated.

    Important caveat: macOS ``ps(1)`` only shows env vars for SOME
    processes (typically GUI / app-bundle processes where the env
    is captured into the process's debug info). For ordinary CLI
    children (``sleep``, ``sh``, simple Python, etc.) the env block
    is empty in the ``ps`` output. We detect this case and return
    ``None`` (fail-open) — the status gate and poll check still
    provide protection.

    Important: do NOT use the POSIX-style ``-e`` flag. On macOS/BSD
    ``-e`` selects "every process" — it would dump the whole process
    table. The lowercase ``e`` (no dash) is the BSD-style "show env
    after command" flag.

    We only invoke this on a SANE PID (numeric, positive) to avoid
    ``ps`` misinterpreting flags.

    Fail-open vs fail-CLOSED contract (INTENTIONAL — do not soften):

    * **Fail-open (return ``None``)** — when we CANNOT DETERMINE
      ownership: ``ps`` not available, subprocess timeout, ``ps``
      exit non-zero (process likely gone between our checks and
      here), permission denied, or ``ps eww`` simply did not surface
      any env vars at all (the macOS limitation for non-bundled
      CLI children). In all of these cases verification is
      UNDETERMINED, so we let the kill proceed. Layers 1 (status
      gate) and 2 (poll) still gate us on the common
      already-exited race.

    * **Fail-CLOSED (return ``False``)** — when we CAN determine
      ownership and it is NOT ours: env vars ARE visible in the
      ``ps eww`` output (we confirmed via the command-only heuristic
      that env was actually surfaced), but our tracking marker is
      absent or carries a different value. This is PID-recycle
      detection and the kill MUST be aborted — sending a signal to
      an unrelated process would be a safety violation.

    The distinction matters: fail-open is a "we don't know" answer
    (proceed cautiously), fail-CLOSED is a "we checked and it isn't
    ours" answer (abort). Do NOT collapse these into a single
    soft-warn-and-proceed path — that would defeat Layer 3.
    """
    if pid <= 0:
        return None
    needle = f"{_TRACKING_ENV_VAR}={tracking_id}"
    try:
        # BSD-style flags: ``e`` = append env, ``ww`` = unlimited
        # width, ``-p PID`` = restrict to this PID. Order matters —
        # put ``-p`` last so the parser doesn't confuse the digit
        # string with a flag.
        completed = subprocess.run(
            ["ps", "eww", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "PID ownership verification via ps failed for PID %d (%s). "
            "Proceeding with kill (fail-open).",
            pid,
            exc,
        )
        return None

    if completed.returncode != 0:
        # ``ps`` returns non-zero when the PID doesn't exist. That's
        # a "process gone" signal — the kill will hit nothing; abort
        # verification (fail-open to keep the kill path safe).
        logger.warning(
            "PID ownership verification: ps exit=%d for PID %d "
            "(process likely gone). Proceeding with kill.",
            pid,
            completed.returncode,
        )
        return None

    output = completed.stdout

    # Fast path: marker present → owned.
    if needle in output:
        return True

    # Detect the macOS-specific "env vars not shown" case. We do
    # this by comparing the eww output against the command-only
    # output (``ps -p <pid> -o command=``). If the eww output adds
    # nothing beyond the command line, ``ps`` did not surface any
    # env vars — we cannot verify, so we fail-open with a warning.
    try:
        cmd_proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if cmd_proc.returncode == 0:
            cmd = cmd_proc.stdout.strip()
            # Heuristic: does the eww output contain anything
            # beyond the command line itself? If ``cmd`` is empty
            # (race — process exited) we cannot tell; fail-open.
            if cmd and cmd in output:
                trailing = output.split(cmd, 1)[1].strip()
                if not trailing:
                    logger.warning(
                        "PID ownership verification: ps eww did not "
                        "surface env vars for PID %d (macOS limitation "
                        "for non-bundled processes). Proceeding with "
                        "kill (fail-open).",
                        pid,
                    )
                    return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Heuristic failed — fall through to the conservative
        # "aborted" branch. We don't want to silently fail-open
        # if our detection method itself is broken.
        pass

    # Marker absent AND env vars were shown → PID was likely recycled.
    logger.warning(
        "PID ownership verification: %s not found in ps output for "
        "PID %d (PID may have been recycled). Aborting kill.",
        _TRACKING_ENV_VAR,
        pid,
    )
    return False


def _build_child_env(process_id: str) -> dict:
    """Build the child environment with the tracking id injected.

    Always starts from a copy of ``os.environ`` so the child inherits
    PATH, HOME, LANG, etc. (the parent-daemon's environment). The
    tracking id is added — if it is ALREADY in the parent env (e.g. a
    test that pre-sets it), ours overwrites it so the child gets the
    authoritative value tied to ``process_id``.
    """
    env = os.environ.copy()
    env[_TRACKING_ENV_VAR] = process_id
    return env


class BackgroundProcessManager:
    """Module-level singleton tracking background processes per instance.

    Data layout:
        ``dict[instance_id → dict[process_id → ProcessInfo]]``

    Concurrency:
        All mutating operations acquire ``_lock`` so concurrent
        :func:`start_process` calls do not race on the per-instance
        cap and the per-process file handle. Reads that only inspect
        state (e.g. :func:`read_logs`, :func:`get_status`) take the
        lock briefly to take a stable snapshot.
    """

    def __init__(self) -> None:
        self._processes: dict[str, dict[str, ProcessInfo]] = {}
        self._lock = asyncio.Lock()
        # M1 fix: sweep stale spill files left behind by a crashed
        # daemon. Best-effort — never raises. Concurrent daemon
        # instances may also try to sweep so each unlink is guarded
        # by try/except OSError.
        self._sweep_stale_files()

    def _sweep_stale_files(self, max_age_seconds: int = 3600) -> None:
        """Unlink stale ``proc-*.log`` spill files from the system temp dir.

        If the daemon crashed, spill files (whose ``delete=False``
        we own) leak in ``tempfile.gettempdir()`` forever. On
        startup, sweep any that are older than ``max_age_seconds``
        (default 1 hour) so they don't accumulate.

        Safe to call concurrently from multiple daemon instances —
        every unlink is wrapped in ``try/except OSError``. Defensive
        against:
          * File already unlinked by another instance.
          * File in active use by a live process (Windows).
          * Filesystem errors (``EACCES``, etc.).

        Never raises — log warnings on unexpected errors only.
        """
        try:
            tmp_dir = tempfile.gettempdir()
            pattern = os.path.join(tmp_dir, "proc-*.log")
            now = time.time()
            for path in glob.glob(pattern):
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    # File disappeared between glob and stat — fine.
                    continue
                if now - mtime <= max_age_seconds:
                    continue
                try:
                    os.unlink(path)
                except OSError as exc:
                    # Concurrent unlink or file in use — fine. Don't
                    # warn for ``ENOENT`` (race) but DO log others so
                    # we can see permission issues.
                    if exc.errno != errno.ENOENT:
                        logger.warning(
                            "M1 sweep: failed to unlink stale spill "
                            "file %s: %s",
                            path,
                            exc,
                        )
        except Exception as exc:  # pragma: no cover — defensive
            # Never let sweep errors break daemon startup.
            logger.warning(
                "M1 sweep: unexpected error during stale-file sweep: %s",
                exc,
            )

    # -- helpers ------------------------------------------------------------

    def _known_processes(self, instance_id: str) -> dict[str, ProcessInfo]:
        """Return the per-instance dict, creating it if missing."""
        bucket = self._processes.get(instance_id)
        if bucket is None:
            bucket = {}
            self._processes[instance_id] = bucket
        return bucket

    @staticmethod
    def _spill_oldest_half(buffer: bytearray) -> bytes:
        """Return the oldest half of ``buffer`` to flush, retaining the rest.

        Splits ``buffer`` in half (the OLDER half is the bytes that
        came first). The caller appends the returned bytes to the
        spill file and truncates them from memory.
        """
        # Split exactly in half. ``len(buffer) // 2`` keeps the newer
        # half (rounded down). For odd lengths the older half is one
        # byte larger — harmless.
        split_at = len(buffer) // 2
        if split_at <= 0:
            return b""
        oldest = bytes(buffer[:split_at])
        del buffer[:split_at]
        return oldest

    def _ensure_spill_file(self, info: ProcessInfo) -> Any:
        """Return the spill file for ``info``, creating it lazily.

        The file is opened in ``ab`` (append-binary) mode, kept open
        while the process is alive, and unlinked on cleanup. Use
        ``buffering=0`` so ``write()`` writes through; ``tempfile``'s
        default line-buffering would force every append through a
        Python-level buffer that the reader task would have to flush.
        """
        if info.file_handle is not None:
            return info.file_handle
        # ``delete=False`` so we control unlink timing; ``prefix`` and
        # ``dir=None`` default to the system temp dir which is fine —
        # these files only live for the duration of the instance.
        handle = tempfile.NamedTemporaryFile(
            mode="ab",
            buffering=0,
            prefix=f"proc-{info.process_id}-",
            suffix=".log",
            delete=False,
        )
        info.file_handle = handle
        info.file_path = handle.name
        return handle

    async def _close_file_handle(self, info: ProcessInfo) -> None:
        """Atomically close ``info.file_handle`` and null it under ``self._lock``.

        Idempotent — if the handle is already None, returns immediately.
        Sets ``_file_ends_with_newline = True`` as a defensive default (no
        more bytes will land in the spill file once the handle is closed).
        Both ``_drain_exit_code`` and ``cleanup_instance`` route their file
        close through this helper to avoid double-close races.
        """
        async with self._lock:
            fh = info.file_handle
            if fh is None:
                return
            try:
                fh.close()
            except OSError:
                pass
            info.file_handle = None
            info._file_ends_with_newline = True

    # -- output capture (asyncio coroutine running as a background task) -----

    async def _capture_output(self, info: ProcessInfo) -> None:
        """Read merged stdout+stderr into memory + spill file.

        This coroutine is started as a ``reader_task`` by
        :func:`start_process`. It reads from ``info.proc.stdout`` line
        by line (stderr is merged into stdout via
        ``stderr=STDOUT``).

        On EOF (``b''``) it exits cleanly. On unexpected errors it
        records ``info.last_error`` and exits non-fatally — the process
        continues running even if the reader dies. The caller (the
        graph) won't see exceptions; the buffer just freezes.

        Spill:
            After appending each chunk to ``info.memory_buffer``, if
            the buffer exceeds :data:`_MEMORY_BUFFER_LIMIT_BYTES` the
            oldest half is flushed to the spill file. This bounds
            memory to roughly half the cap per process while keeping
            recent context in RAM for fast ``proc_logs`` reads.
        """
        proc = info.proc
        if proc is None or proc.stdout is None:
            return

        # Read larger chunks (64 KB) for throughput but still keep the
        # spill check on a per-chunk cadence — coarse enough to avoid
        # overhead, frequent enough to bound memory.
        _CHUNK_BYTES = 64 * 1024

        try:
            while True:
                chunk = await proc.stdout.read(_CHUNK_BYTES)
                if not chunk:
                    # EOF — child closed stdout. Process likely
                    # exited; ``_drain_exit_code`` task will update
                    # status. Exit cleanly.
                    break

                # 1. Append to memory.
                info.memory_buffer.extend(chunk)

                # 2. Spill if over the cap. Use ``>`` so a chunk
                # exactly at the cap does not trigger a needless
                # spill.
                if len(info.memory_buffer) > _MEMORY_BUFFER_LIMIT_BYTES:
                    async with self._lock:
                        # Re-check under lock — another append could
                        # have already spilled.
                        if (
                            len(info.memory_buffer)
                            > _MEMORY_BUFFER_LIMIT_BYTES
                        ):
                            spilled = self._spill_oldest_half(
                                info.memory_buffer
                            )
                            if spilled:
                                fh = self._ensure_spill_file(info)
                                fh.write(spilled)
                                # No explicit flush — ``buffering=0``
                                # writes through to the OS.
                                # C1 fix: remember whether the LAST byte
                                # of what we just wrote is ``\n`` so the
                                # reader can stitch a partial line that
                                # straddled the memory/file boundary.
                                info._file_ends_with_newline = (
                                    spilled.endswith(b"\n")
                                )
                else:
                    # Still inside the cap. No spill needed.
                    pass
        except asyncio.CancelledError:
            # Cancellation is the normal teardown path (stop / cleanup).
            # Re-raise so the awaiting task sees it; we don't suppress.
            raise
        except Exception as exc:  # pragma: no cover — defensive
            # Use ``type(...).__name__`` to avoid leaking credentials
            # embedded in ``str(exc)`` (see project guideline).
            info.last_error = f"reader crashed: {type(exc).__name__}"
            logger.warning(
                "proc capture reader for %s crashed: %s",
                info.process_id,
                exc,
            )

    async def _drain_exit_code(self, info: ProcessInfo) -> None:
        """Wait for the process to exit and update ``info.status``.

        Runs as a separate task so ``proc_stop`` doesn't have to await
        the OS exit AND the tool's caller doesn't block on it. The
        reader task continues until stdout EOF (which the OS triggers
        once the child exits), so this task mainly updates status and
        exposes ``exit_code`` / ``timed_out`` for ``proc_status``.
        """
        proc = info.proc
        if proc is None:
            return

        try:
            exit_code = await proc.wait()
        except asyncio.CancelledError:
            # We were cancelled (e.g. cleanup). Don't update status;
            # the cancelling code path will set the final state.
            raise
        except Exception as exc:  # pragma: no cover — defensive
            info.last_error = f"wait failed: {type(exc).__name__}"
            info.status = "error"
            logger.warning(
                "proc wait for %s failed: %s",
                info.process_id,
                exc,
            )
            return

        info.exit_code = exit_code
        # Wait for the reader task to drain the remaining pipe data
        # into the spill file BEFORE we close ``info.file_handle``.
        # The reader task exits on stdout EOF (which the OS triggers
        # when the child closes stdout ~at the same time as exit),
        # so it should be done almost immediately. Bounded wait so we
        # never block ``proc_status`` callers indefinitely.
        reader_task = info.reader_task
        if reader_task is not None and not reader_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(reader_task), timeout=2.0
                )
            except asyncio.TimeoutError:
                # Reader is slow / stuck. Close anyway — the buffered
                # data may be lost but stopping a runaway FD is more
                # important than capturing every byte.
                logger.warning(
                    "proc %s reader task did not settle in time",
                    info.process_id,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "proc %s reader await failed: %s",
                    info.process_id,
                    type(exc).__name__,
                )

        # Close + null the spill file handle now that the process is
        # gone and the reader has flushed. The spill file PATH stays
        # valid on disk so ``_read_file_tail`` can still open its
        # own read-only handle via ``open(path, "rb")``. This bounds
        # open FDs to one per process per ``proc_status``/``proc_logs``
        # call instead of one per process for the lifetime of the
        # instance.
        # M2 fix: use the centralized helper so close + null is
        # atomic under the lock (prevents double-close on
        # concurrent natural-exit + cleanup).
        await self._close_file_handle(info)

        # ``timed_out`` and ``user_stopped`` are both authoritative —
        # they represent an intentional kill from the runtime
        # (timeout task or explicit ``proc_stop``) and beat any signal
        # translation below. Without this guard, the drain task could
        # race with ``stop_process`` and overwrite a ``"killed"``
        # verdict with ``"exited"``.
        if info.timed_out:
            info.status = "killed"
        elif info.user_stopped:
            info.status = "killed"
        elif exit_code in (-9, -signal.SIGKILL, -signal.SIGTERM):
            info.status = "killed"
        elif exit_code == 0:
            info.status = "exited"
        else:
            # Non-zero but not killed — treat as "exited" with a
            # non-zero code. ``proc_status`` surfaces the code so the
            # agent can distinguish. We don't use "error" here because
            # an explicit non-zero exit often just means the process
            # did its job and reported a failure (e.g. a test runner).
            info.status = "exited"

    async def _timeout_killer(self, info: ProcessInfo, seconds: float) -> None:
        """Sleep ``seconds``, then kill ``info.proc`` if still running.

        Runs as ``info.timeout_task``. Idempotent: if the process
        already exited, ``proc.kill`` is a no-op on a finished handle.

        Marks ``info.timed_out = True`` so :func:`_drain_exit_code`
        reports ``"killed"`` rather than ``"exited"`` when the timeout
        fires (preserves diagnostic signal for the agent).

        Defense-in-depth: applies the same Layers 1+2+3 checks as
        :func:`stop_process` before sending SIGKILL. If a previous
        owner already exited the process, the timeout fires but the
        killed PID may have been recycled — the verification aborts
        the kill in that case.
        """
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            # The agent called ``proc_stop`` before the timeout fired
            # — cancel our sleep, no kill needed.
            return

        proc = info.proc
        if proc is None:
            return

        # Layer 1: status gate. If the drain task already updated
        # ``info.status`` to a terminal value, there's nothing to kill.
        if info.status != "running":
            return

        # Layer 2: poll. The process may have exited between the
        # status check and now (drain task hasn't caught up yet).
        if proc.returncode is not None:
            return  # already exited naturally

        # Mark the process as timeout-killed BEFORE the kill attempt
        # so ``_drain_exit_code`` (line ~808) reports ``"killed"`` rather
        # than ``"exited"`` when the timeout fires. We roll this back
        # if the helper aborts due to PID recycle (below) — otherwise
        # a recycled PID would be reported as "killed" even though no
        # signal was delivered, the same bug class we fixed in
        # ``stop_process`` via the ``pid_recycled`` guard.
        info.timed_out = True
        try:
            # Route through the centralized helper — it runs Layers
            # 1+2+3 internally and returns the disposition. Layer 3
            # is NOT invoked directly here to avoid the duplicate-call
            # bug we fixed: a direct ``_verify_pid_ownership`` would
            # race the helper's internal check and double-log on
            # recycle. Letting the helper own Layer 3 keeps the
            # kill-path logic in one place.
            sig_result = self._attempt_kill_signal(
                info,
                signal.SIGKILL,
                sys.platform != "win32",
                "SIGKILL (timeout)",
            )
            if sig_result == "aborted_recycled":
                # Layer 3 aborted — PID may have been recycled.
                # Roll back ``timed_out`` so ``_drain_exit_code``
                # doesn't promote ``status="killed"`` for a process
                # that is still alive under a recycled PID. Mirrors
                # the ``pid_recycled`` guard in :func:`stop_process`.
                info.timed_out = False
                logger.warning(
                    "timeout-kill: PID ownership check failed for %s "
                    "(PID %d); ABORTING timeout-kill — PID may have "
                    "been recycled.",
                    info.process_id,
                    proc.pid,
                )
        except ProcessLookupError:
            # Process already reaped between our check and kill —
            # benign race. ``timed_out`` stays True so ``_drain_exit_code``
            # promotes status="killed" (a timeout DID elapse; even if
            # the OS reaped the process naturally after our signal
            # landed, the timeout fired first and that's the
            # user-visible truth).
            pass
        except Exception as exc:  # pragma: no cover — defensive
            info.last_error = f"timeout-kill failed: {type(exc).__name__}"
            logger.warning(
                "proc timeout-kill for %s failed: %s",
                info.process_id,
                exc,
            )

    def _attempt_kill_signal(
        self,
        info: ProcessInfo,
        sig: int,
        is_unix: bool,
        signal_label: str,
    ) -> str:
        """Run defense Layers 1+2+3 and send one signal.

        Centralizes the kill-signal decision so ``stop_process`` and
        ``_timeout_killer`` don't drift apart. Performs these checks
        IN ORDER, returning the FIRST that triggers a skip/abort:

        * **Layer 1 — status gate**: if ``info.status`` is already
          terminal (``exited`` | ``killed`` | ``error``), there's
          nothing to kill. Returns ``"skipped_terminal"``.
        * **Layer 2 — liveness check**: if ``proc.returncode`` is not
          ``None`` (the OS has reaped the process), the process has
          already exited. Update ``info.status`` to the appropriate
          terminal state (do not raise) and return ``"skipped_exited"``
          so the caller proceeds to the post-kill settle.

          Note: ``asyncio.subprocess.Process`` does NOT expose
          ``poll()`` (unlike ``subprocess.Popen``). The equivalent is
          inspecting ``proc.returncode`` — it's ``None`` while
          running and set to the exit code once the OS reaps the
          process. This is the same pattern the existing code uses
          (``if proc.returncode is None: return``).
        * **Layer 3 — ownership**: if the tracking id was set and
          :func:`_verify_pid_ownership` returns ``False``, the PID was
          recycled — ABORT the kill. Returns ``"aborted_recycled"``.
          Verification returning ``None`` (fail-open) → proceed.
        * Else: send the signal via :func:`os.killpg` (Unix) or
          :func:`proc.send_signal` (Windows). Returns ``"sent"``.

        The ``signal_label`` is just for logging — e.g. ``"SIGTERM"``
        or ``"SIGKILL"`` so the CRITICAL warning from Layer 3 reads
        clearly in the log.

        Never raises — the caller's flow must continue regardless of
        what comes back.
        """
        # Layer 1: status gate.
        if info.status != "running":
            return "skipped_terminal"

        proc = info.proc
        if proc is None:
            return "skipped_no_handle"

        # Layer 2: liveness check. ``proc.returncode`` is ``None``
        # while the process is running; set to the exit code once
        # the OS reaps it. This is the asyncio equivalent of
        # ``Popen.poll()``.
        if proc.returncode is not None:
            rc = proc.returncode
            # Update status promptly so concurrent ``proc_status``
            # callers see the real state without waiting for the
            # drain task to wake up.
            info.exit_code = rc
            # Apply the same precedence used in :func:`_drain_exit_code`:
            # user_stopped / timed_out are authoritative; otherwise
            # map signal-style exit codes to "killed".
            if info.user_stopped or info.timed_out:
                info.status = "killed"
            elif rc in (-signal.SIGKILL, -signal.SIGTERM):
                info.status = "killed"
            else:
                info.status = "exited"
            return "skipped_exited"

        # Layer 3: PID ownership verification. Skip if no tracking
        # tag was set (legacy / test ProcessInfo → fail-open).
        if info.tracking_id and proc.pid is not None:
            ownership = _verify_pid_ownership(proc.pid, info.tracking_id)
            if ownership is False:
                logger.warning(
                    "PID ownership check failed for %s (PID %d, "
                    "tracking_id=%s); ABORTING %s — PID may have "
                    "been recycled. THIS IS A SAFETY EVENT; the "
                    "intended kill was not delivered.",
                    info.process_id,
                    proc.pid,
                    info.tracking_id,
                    signal_label,
                )
                return "aborted_recycled"
            # ``True`` or ``None`` (fail-open) → proceed.

        # All gates passed — deliver the signal.
        if is_unix:
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except OSError:
                # ``ProcessLookupError`` is a subclass of ``OSError``.
                # Process group gone — try a direct signal as a
                # fallback. Broaden to ``OSError`` so non-benign
                # failures (e.g. ``PermissionError``) get a WARNING
                # instead of silently passing — the helper's docstring
                # promises "never raises" so we should not lose
                # visibility on unexpected OS errors.
                try:
                    proc.send_signal(sig)
                except OSError as exc:
                    if isinstance(exc, ProcessLookupError):
                        # Already gone — expected benign race.
                        pass
                    else:
                        logger.warning(
                            "killpg+fallback: send_signal(%s) for PID %d "
                            "failed: %s",
                            sig,
                            proc.pid,
                            exc,
                        )
        else:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass
        return "sent"

    # -- public lifecycle API ----------------------------------------------

    async def start_process(
        self,
        instance_id: str,
        command: Union[str, List[str]],
        workdir: Optional[str],
        timeout_seconds: int = 0,
    ) -> tuple[Optional[str], Optional[str]]:
        """Spawn a background subprocess.

        Args:
            instance_id: Owning instance.
            command: Shell command string or argv list (matches
                ``bash`` tool semantics).
            workdir: Working directory for the child. ``None`` means
                inherit parent's CWD. **Not** auto-injected here — the
                tool factory (``create_proc_tools``) passes the
                resolved project workdir explicitly so the boundary
                check is consistent with ``bash``.
            timeout_seconds: ``0`` = no auto-kill. Otherwise schedule
                a background task that SIGKILLs the process after
                ``timeout_seconds`` seconds.

        Returns:
            ``(process_id, error_str)`` tuple. On success
            ``process_id`` is set and ``error_str`` is ``None``; on
            failure ``process_id`` is ``None`` and ``error_str``
            contains a user-facing error message. This dual-return
            convention avoids raising — the caller (a tool function)
            can simply format the message into the tool result.
        """
        async with self._lock:
            bucket = self._known_processes(instance_id)

            # Enforce concurrency cap. Count running+exited+error (any
            # entry we haven't yet reaped) — conservatively, a process
            # stays in the bucket until the next ``start_process`` /
            # ``cleanup_instance`` evicts it. An exited process leaves
            # its process_id in the bucket so the agent can still call
            # ``proc_logs`` / ``proc_status`` after exit, but the cap
            # then forces the agent to clean up before starting more.
            if len(bucket) >= MAX_PROCESSES_PER_INSTANCE:
                running = sum(
                    1 for p in bucket.values() if p.status == "running"
                )
                return None, (
                    f"Error: instance {instance_id} reached the "
                    f"concurrent-process cap "
                    f"({MAX_PROCESSES_PER_INSTANCE}). "
                    f"Currently tracking {len(bucket)} process(es) "
                    f"({running} running). "
                    "Stop an existing process with proc_stop(...) "
                    "or list them with proc_list() before starting more."
                )

            # If the workdir was given, sanity-check that it exists /
            # is a directory. Subprocess would fail anyway but a
            # clear, early error beats an opaque OSError.
            if workdir is not None:
                if not os.path.isdir(workdir):
                    return None, (
                        f"Error: workdir does not exist or is not a "
                        f"directory: {workdir}"
                    )

            process_id = _new_process_id()
            info = ProcessInfo(
                process_id=process_id,
                instance_id=instance_id,
                command=command,
            )
            # Layer 3: tag the process with a tracking token BEFORE
            # spawning so we can later verify the PID still belongs to
            # us before killing. The token is the same as the
            # ``process_id`` (a unique 8-hex-char value) — no need for
            # a separate UUID. Empty-string for legacy / test
            # ``ProcessInfo`` instances → fail-open at verify-time.
            info.tracking_id = process_id
            bucket[process_id] = info

        # Spawn outside the lock — subprocess creation is slow and we
        # don't want to block other operations during it. ``info`` is
        # already registered in ``bucket`` so concurrent readers can
        # find it (status stays ``"running"`` until spawn succeeds).

        # Build subprocess kwargs. On Unix, ``start_new_session=True``
        # creates a new process group so ``proc_stop`` can SIGTERM the
        # entire tree (mirrors bash.py's behavior). Merging stderr →
        # stdout via ``stderr=STDOUT`` simplifies the reader (one
        # pipe) and gives the agent chronological output.
        subproc_kwargs: dict = {}
        if sys.platform != "win32":
            subproc_kwargs["start_new_session"] = True

        # Layer 3: inject the tracking env var into the child so we
        # can later verify the PID wasn't recycled. Inherits the
        # parent env (PATH, HOME, LANG, …) and adds the tracking id.
        # ``_build_child_env`` always overrides any existing value
        # so the child gets the authoritative tag for THIS process_id.
        child_env = _build_child_env(process_id)

        try:
            if isinstance(command, list):
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=workdir,
                    env=child_env,
                    **subproc_kwargs,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=workdir,
                    env=child_env,
                    **subproc_kwargs,
                )
        except FileNotFoundError as exc:
            # Clean up the bucket entry so we don't leak a phantom
            # process_id. Use a second lock acquisition since the
            # first one has already been released.
            async with self._lock:
                bucket.pop(process_id, None)
            return None, f"Error: command not found: {exc}"
        except Exception as exc:
            async with self._lock:
                bucket.pop(process_id, None)
            return None, (
                f"Error: failed to start process: {type(exc).__name__}: "
                f"{exc}"
            )

        info.proc = proc

        # Start the output-capture and exit-watcher tasks. Both run
        # for the lifetime of the process. We hold references so we
        # can cancel them on cleanup.
        info.reader_task = asyncio.create_task(
            self._capture_output(info),
            name=f"proc-capture-{process_id}",
        )
        info.exit_task = asyncio.create_task(  # type: ignore[attr-defined]
            self._drain_exit_code(info),
            name=f"proc-exit-{process_id}",
        )

        # Optionally schedule the timeout killer.
        if timeout_seconds and timeout_seconds > 0:
            info.timeout_task = asyncio.create_task(
                self._timeout_killer(info, float(timeout_seconds)),
                name=f"proc-timeout-{process_id}",
            )

        # C2 fix: re-check that we're still tracked. ``cleanup_instance``
        # could have popped the stub from ``bucket`` during the spawn
        # window (between the first lock release above and now). If so,
        # the just-spawned subprocess + the three tasks we just
        # scheduled are all orphaned — kill the process and cancel the
        # tasks before returning success. The caller sees ``process_id``
        # ``None`` and surfaces an error (the existing ``proc_run``
        # error path already handles this).
        async with self._lock:
            bucket_after = self._processes.get(instance_id, {})
            if process_id not in bucket_after:
                # We were cleaned up during spawn. Kill the orphan.
                # SAFETY NOTE: bypassing ``_attempt_kill_signal`` here.
                # The process was spawned microseconds ago in this same
                # function — PID reuse is essentially impossible. The
                # inline ``proc.kill()`` (the original C2 implementation)
                # is preserved verbatim for two reasons:
                #   1. Uniformity of behavior with the test fixture
                #      ``TestSpawnWindowRace.test_cleanup_during_spawn_kills_orphan``
                #      which mocks ``proc.kill`` to assert the orphan was
                #      killed. The helper routes via ``send_signal``
                #      instead of ``kill`` for portability across signal
                #      types; for SIGKILL-only kill at this microsecond
                #      window the semantics are equivalent.
                #   2. ``info.proc`` is set but the reader_task /
                #      exit_task / timeout_task were JUST scheduled and
                #      are cancelled immediately below — the helper's
                #      Layer 1 (status gate) and Layer 2 (poll) checks
                #      add no value here because we know the state.
                try:
                    if sys.platform != "win32":
                        try:
                            os.killpg(
                                os.getpgid(proc.pid), signal.SIGKILL
                            )
                        except OSError:
                            try:
                                proc.kill()
                            except ProcessLookupError:
                                pass
                    else:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "C2 cleanup: kill failed for orphan %s: %s",
                        process_id,
                        exc,
                    )
                for task in (
                    info.reader_task,
                    info.exit_task,
                    info.timeout_task,
                ):
                    if task is not None and not task.done():
                        task.cancel()
                return None, (
                    f"Error: process {process_id} was cleaned up "
                    f"during start"
                )

        return process_id, None

    async def stop_process(
        self,
        instance_id: str,
        process_id: str,
        force: bool = False,
    ) -> str:
        """Stop a background process.

        Strategy (Unix):
            SIGTERM the process group (or the process directly on
            Windows); await ``proc.wait()`` with
            :data:`_STOP_GRACE_SECONDS` timeout; on expiry escalate
            to SIGKILL. The ``force`` flag skips SIGTERM entirely and
            goes straight to SIGKILL.

        Defense-in-depth (see :meth:`_attempt_kill_signal`):
            Every signal attempt runs through three layers before
            being delivered — status gate (skip if terminal),
            liveness check (skip if already exited), and PID
            ownership verification (ABORT if the PID was recycled
            to an unrelated process). The third layer is fail-open
            on Windows / unsupported OS.

        Returns:
            Human-readable result string. Errors (process not found)
            are surfaced as ``"Error: ..."`` strings. If the process
            is already in a terminal state, returns a success-style
            "already stopped" message (idempotent — does NOT raise).
        """
        async with self._lock:
            bucket = self._processes.get(instance_id, {})
            info = bucket.get(process_id)
        if info is None:
            return (
                f"Error: process {process_id!r} not found for instance "
                f"{instance_id}. Use proc_list() to see active ids."
            )

        # Layer 1 entry-check: idempotent stop. If the process is
        # already in a terminal state, return a success message
        # instead of an error — this matches the spec ("skip the
        # kill entirely and return success/idempotently").
        if info.status != "running":
            return (
                f"Process {process_id} is already stopped "
                f"(status={info.status}); nothing to do."
            )

        proc = info.proc
        if proc is None:
            return f"Error: process {process_id} has no live handle."

        is_unix = sys.platform != "win32"

        try:
            # Mark the process as user-stopped BEFORE sending any
            # signal. Both :func:`_drain_exit_code` and this function
            # await ``proc.wait()`` — when the OS reaps the process,
            # the two coroutines wake up simultaneously and both try
            # to set ``info.status``. The ``user_stopped`` flag is
            # authoritative in the drain branch (it always maps to
            # ``"killed"``), so the final verdict stays consistent
            # regardless of which writer wins the race. Without this
            # flag, a process killed by SIGTERM could end up with
            # ``status == "exited"`` if the drain task's exit_code
            # branch ran last.
            info.user_stopped = True
            info.timed_out = False  # user-initiated, not timeout

            # Track whether the kill was aborted by Layer 3 (PID
            # reuse). We surface this in the response so the agent
            # can investigate. The OS still has no signal attached
            # to our intent in that case.
            pid_recycled = False

            if force:
                # Skip SIGTERM; SIGKILL immediately. The helper
                # runs Layers 1+2+3 before delivering the signal.
                sig_result = self._attempt_kill_signal(
                    info, signal.SIGKILL, is_unix, "SIGKILL (force)"
                )
                if sig_result == "aborted_recycled":
                    pid_recycled = True
                # Whether sent, skipped, or aborted, the OS will
                # have reaped the process by the time we await
                # ``proc.wait()``. Bound the wait so we never block
                # ``proc_stop`` indefinitely.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Even with Layer 3 abort, the process may
                    # still be running (the kill was skipped). If
                    # we sent the signal, SIGKILL should have
                    # finished the job within 2s; if we didn't,
                    # there's nothing more we can do.
                    logger.warning(
                        "proc %s did not exit within 2s of SIGKILL "
                        "(sig_result=%s)",
                        process_id,
                        sig_result,
                    )
            else:
                # Polite stop → SIGTERM → grace → SIGKILL.
                sig_result = self._attempt_kill_signal(
                    info, signal.SIGTERM, is_unix, "SIGTERM"
                )
                if sig_result == "aborted_recycled":
                    pid_recycled = True

                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=_STOP_GRACE_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Grace expired — escalate to SIGKILL via the
                    # helper so Layers 1+2+3 run again (status may
                    # have changed during the grace wait).
                    sig_result = self._attempt_kill_signal(
                        info, signal.SIGKILL, is_unix, "SIGKILL (escalation)"
                    )
                    if sig_result == "aborted_recycled":
                        pid_recycled = True
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "proc %s did not exit within 2s of "
                            "force-kill after grace expiry",
                            process_id,
                        )
        except Exception as exc:
            # Use ``type(...).__name__`` to avoid leaking credentials
            # embedded in ``str(exc)``.
            return (
                f"Error: failed to stop process {process_id}: "
                f"{type(exc).__name__}: {exc}"
            )

        # At this point the OS has reaped the process (or we are
        # giving up and returning best-effort). Wait briefly for the
        # drain task to settle so ``info.status`` and
        # ``info.exit_code`` are populated consistently before we
        # format the return string. The drain task is bounded by the
        # reader task finishing stdout EOF (which the OS triggers
        # right after process exit), so a short timeout is plenty.
        if info.exit_task is not None and not info.exit_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(info.exit_task), timeout=2.0)
            except asyncio.TimeoutError:
                # Drain didn't settle in time. Fall through — we'll
                # surface whatever fields we have.
                logger.warning(
                    "proc %s drain task did not settle in time",
                    process_id,
                )

        # Backfill exit_code if the drain task never finished (rare).
        # ``proc.returncode`` is set once the OS reaps the process,
        # which already happened above.
        if info.exit_code is None:
            info.exit_code = proc.returncode
        # ``info.status`` may still be ``"running"`` if the drain task
        # was cancelled or never ran — promote it now that the
        # process is definitively gone and ``user_stopped`` is True.
        # The ``pid_recycled`` guard prevents the promotion when Layer 3
        # aborted the kill: in that case no signal was sent and the
        # process may still be alive under a recycled PID, so reporting
        # ``"killed"`` would be a lie.
        if info.status == "running" and not pid_recycled:
            info.status = "killed"

        # Return the last ``_STOP_TAIL_LINES`` lines of captured
        # output so the agent can confirm what the process printed
        # before it exited. Falls back gracefully if the file is
        # missing or unreadable.
        tail = await self._get_recent_lines(info, _STOP_TAIL_LINES)
        header = (
            f"Process {process_id} stopped "
            f"(force={force}, status={info.status}, "
            f"exit_code={info.exit_code})."
        )
        if pid_recycled:
            # Layer 3 aborted the kill. Surface this clearly so the
            # agent knows the kill was NOT actually delivered and
            # the PID may now belong to an unrelated process.
            header += (
                "\n\nWARNING: PID ownership verification failed — "
                "the kill was ABORTED because the PID at the time "
                "of the kill no longer carried this process's "
                "tracking marker. The PID may have been recycled "
                "to an unrelated process. No signal was sent."
            )
        if tail:
            return f"{header}\n\nLast {_STOP_TAIL_LINES} lines:\n{tail}"
        return header

    def get_status(self, instance_id: str, process_id: str) -> str:
        """Return a human-readable status line for ``process_id``.

        Combines ``info.status`` with uptime (if running), PID
        (always, when available), and last error (if any). Errors
        return ``"Error: ..."`` strings.
        """
        info = self._processes.get(instance_id, {}).get(process_id)
        if info is None:
            return (
                f"Error: process {process_id!r} not found for instance "
                f"{instance_id}. Use proc_list() to see active ids."
            )

        parts: list[str] = [
            f"process_id: {info.process_id}",
            f"status: {info.status}",
        ]
        if info.proc is not None and info.proc.pid is not None:
            parts.append(f"pid: {info.proc.pid}")
        if info.status == "running":
            uptime = datetime.now(timezone.utc) - info.started_at
            # ``total_seconds()`` is float; format compactly.
            secs = int(uptime.total_seconds())
            parts.append(f"uptime: {secs}s")
            parts.append(f"started_at: {info.started_at.isoformat()}")
        if info.exit_code is not None:
            parts.append(f"exit_code: {info.exit_code}")
        if info.timed_out:
            parts.append("timed_out: true")
        if info.last_error:
            parts.append(f"last_error: {info.last_error}")
        # Buffer info lets the agent know whether to expect a spill
        # file. Helps when troubleshooting empty reads.
        parts.append(
            f"buffer_bytes: {len(info.memory_buffer)} "
            f"(limit={_MEMORY_BUFFER_LIMIT_BYTES})"
        )
        if info.file_path:
            try:
                size = os.path.getsize(info.file_path)
                parts.append(f"spill_file: {info.file_path} ({size} bytes)")
            except OSError:
                parts.append(f"spill_file: {info.file_path} (unreadable)")

        return "\n".join(parts)

    def list_processes(self, instance_id: str) -> str:
        """Return a markdown-table listing of all processes for ``instance_id``.

        Columns: ``process_id | status | command | uptime``. Exited /
        killed processes are listed with uptime frozen at their last
        known value (the agent can read logs via ``proc_logs``).
        """
        bucket = self._processes.get(instance_id, {})
        if not bucket:
            return (
                f"No background processes tracked for instance "
                f"{instance_id}."
            )

        lines = [
            "process_id | status | command | uptime",
            "---|---|---|---",
        ]
        # Stable order so repeated ``proc_list`` calls diff cleanly.
        for pid in sorted(bucket.keys()):
            info = bucket[pid]
            cmd_repr = self._command_repr(info.command)
            elapsed = datetime.now(timezone.utc) - info.started_at
            secs = int(elapsed.total_seconds())
            lines.append(
                f"{info.process_id} | {info.status} | "
                f"{cmd_repr} | {secs}s"
            )
        return "\n".join(lines)

    async def cleanup_instance(self, instance_id: str) -> None:
        """Kill every process for ``instance_id`` and release resources.

        Called from instance-lifecycle code on instance termination /
        error. Idempotent — safe to call multiple times. Cancels the
        reader and exit-watcher tasks, force-kills the subprocess,
        closes / unlinks the spill file, then drops the bucket.
        """
        async with self._lock:
            bucket = self._processes.pop(instance_id, {})

        for pid, info in bucket.items():
            # Cancel background tasks first. They may be blocked on
            # pipe reads; cancellation unblocks them so we can wind
            # down cleanly.
            for task in (info.reader_task, info.exit_task, info.timeout_task):  # type: ignore[attr-defined]
                if task is not None and not task.done():
                    task.cancel()
            # M4 fix: await task cancellation with a short timeout
            # BEFORE unlinking the spill file. On Windows, unlinking
            # an open file fails. On Unix, racing the reader's
            # final write can lose data. A bounded await gives the
            # tasks a chance to flush + close their end of the pipe
            # before we tear down the file.
            tasks_to_await = [
                t
                for t in (
                    info.reader_task,
                    info.exit_task,
                    info.timeout_task,
                )
                if t is not None and not t.done()
            ]
            if tasks_to_await:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *tasks_to_await, return_exceptions=True
                        ),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    # Tasks didn't settle in time. Proceed with
                    # unlink anyway — worst case is a lost final
                    # write or a Windows unlink failure (which we
                    # already guard with try/except OSError).
                    logger.warning(
                        "cleanup: tasks for %s did not settle within "
                        "2s, proceeding with unlink anyway",
                        pid,
                    )

            # Fix: route the kill through the centralized helper so the
            # three defense layers (status gate / liveness check / PID
            # ownership) ALL apply. Previously this block bypassed the
            # helper and went straight to ``killpg``/``proc.kill`` —
            # meaning a recycled PID on a recently-exited process could
            # hit an unrelated process during instance cleanup. The
            # helper short-circuits on terminal status so this is a
            # no-op for already-exited entries.
            is_unix = sys.platform != "win32"
            try:
                self._attempt_kill_signal(
                    info, signal.SIGKILL, is_unix, "SIGKILL (cleanup)"
                )
            except Exception as exc:  # pragma: no cover — defensive
                # The helper promises "never raises"; defense-in-depth.
                logger.warning(
                    "cleanup: force-kill helper raised for %s: %s",
                    pid,
                    exc,
                )
            # Close + unlink the spill file. We created it with
            # ``delete=False`` so this is our responsibility.
            # M2 fix: use the centralized helper so close + null is
            # atomic under the lock (prevents double-close on
            # concurrent natural-exit + cleanup).
            await self._close_file_handle(info)
            if info.file_path:
                try:
                    os.unlink(info.file_path)
                except OSError:
                    pass

    async def cleanup_all(self) -> int:
        """Kill ALL background processes across ALL instances.

        Daemon-shutdown sweep. Idempotent: each ``cleanup_instance`` pops
        its bucket atomically under the per-manager lock, so concurrent
        or repeated calls are safe (the second pop returns an empty
        bucket and short-circuits).

        Known limitations (documented per Phase 1 approver note 4):

        * **Truly-detached orphans.** A child process that called
          ``setsid`` (or otherwise left its original process group)
          sits outside the group that ``cleanup_instance`` kills via
          ``os.killpg``. Such a process will not be reaped by this
          sweep — that is a process-isolation issue, not a manager bug.
        * **Crash-recovery leak.** The in-memory ``self._processes``
          registry is not persisted to disk. If the daemon crashes
          before this sweep runs (no graceful shutdown), the OS
          processes themselves survive but the Python-side bookkeeping
          is gone — a hard daemon restart cannot enumerate them here.
          The OS reaps the subprocesses when the parent Python process
          dies (unless they were ``setsid``-detached, see above).

        Why we snapshot the keys under the lock and release before
        iterating: ``cleanup_instance`` itself acquires ``self._lock``.
        ``asyncio.Lock`` is non-reentrant, so holding it across the
        loop would deadlock. Snapshot → release → iterate is safe
        because ``cleanup_instance`` pops each bucket atomically.

        Returns:
            Number of instance buckets that were cleaned (0 if none).
        """
        # Snapshot instance ids under lock; release before iterating so
        # ``cleanup_instance`` can re-acquire the lock per call.
        async with self._lock:
            instance_ids = list(self._processes.keys())
        cleaned = 0
        for iid in instance_ids:
            try:
                await self.cleanup_instance(iid)
                cleaned += 1
            except Exception as e:
                logger.warning(
                    f"cleanup_all: cleanup_instance failed for {iid[:8]}: "
                    f"{type(e).__name__}: {e}"
                )
        if cleaned:
            logger.info(f"cleanup_all: cleaned {cleaned} instance bucket(s)")
        return cleaned

    # -- log reading --------------------------------------------------------

    async def _get_recent_lines(self, info: ProcessInfo, lines: int) -> str:
        """Return the last ``lines`` of captured output as text.

        Fast path: read the entire memory buffer and split. If memory
        has fewer lines than requested, the slow path (file tail)
        contributes the oldest slice, then memory contributes the rest.

        Note: we read the FULL memory buffer here (bounded to 4 MB by
        the spill mechanism). On the fast path this is a single
        ``bytes.decode`` + ``splitlines`` — cheap enough for the LLM's
        prompt budget. The slow path additionally tails the spill file
        but only when the request really needs history memory can't
        satisfy.

        C1 fix: when the last spill left a partial line in memory
        (i.e. the spill file does not end with ``\n``), the last line
        from the spill file's tail and the first line in the memory
        buffer are two halves of the SAME line. Stitch them before
        merging.
        """
        lines = max(1, min(int(lines), _MAX_LOG_LINES))

        memory_text = info.memory_buffer.decode(errors="replace")
        # ``list(...)`` to copy — we may mutate index 0 below when
        # stitching the split line (C1 fix).
        memory_lines = list(memory_text.splitlines())

        if len(memory_lines) >= lines:
            # Fast path: memory alone has enough lines.
            return "\n".join(memory_lines[-lines:])

        # Slow path: drain historical lines from the spill file and
        # merge with memory. The agent requested more lines than
        # memory holds, so they probably want older context.
        older = await self._read_file_tail(info, lines - len(memory_lines))
        if not older:
            return "\n".join(memory_lines)

        # C1 fix: stitch the split line at the memory/file boundary.
        # If the spill file does NOT end with a newline (last spill
        # was mid-line), then ``older[-1]`` is the partial tail of a
        # line whose head lives in ``memory_lines[0]``. Concatenate
        # them and drop the now-stale older tail.
        if not info._file_ends_with_newline and older and memory_lines:
            memory_lines[0] = older.pop() + memory_lines[0]

        combined = older + memory_lines
        return "\n".join(combined[-lines:])

    async def _read_file_tail(
        self, info: ProcessInfo, want_lines: int
    ) -> list[str]:
        """Async wrapper around :meth:`_read_file_tail_sync`.

        Spill files can grow large; tailing them with ``open()`` +
        ``seek()`` + ``read()`` is synchronous I/O that would block
        the event loop. Offload to a worker thread via
        :func:`asyncio.to_thread`.

        Returns the same ``list[str]`` (oldest→newest) as the sync
        helper — see that method for the stitching algorithm.
        """
        return await asyncio.to_thread(
            self._read_file_tail_sync, info, want_lines
        )

    @staticmethod
    def _read_file_tail_sync(
        info: ProcessInfo, want_lines: int
    ) -> list[str]:
        """Return the most recent ``want_lines`` lines from the spill file.

        Reads the file backwards in 64 KB chunks until it has enough
        complete lines (or hits the start of the file), then returns
        the trailing ``want_lines`` lines in oldest→newest order.

        Stitching:
            Each chunk may start and/or end mid-line. We concatenate
            chunks in chronological order (oldest first) BEFORE
            splitting, so chunk boundaries disappear and ``splitlines``
            correctly identifies complete lines. After splitting, if we
            broke out of the loop early (i.e. there are still older
            bytes we did not read), the FIRST line in our window is a
            partial tail of a line whose HEAD lives in the older part
            of the file — we drop it.
        """
        if info.file_path is None or want_lines <= 0:
            return []
        path = info.file_path
        try:
            file_size = os.path.getsize(path)
        except OSError:
            return []

        if file_size == 0:
            return []

        # Read the tail in chunks, concatenating in chronological order
        # (oldest→newest) into ``collected_bytes``. Stop as soon as we
        # have at least ``want_lines`` newlines (cheap count on bytes)
        # — the +1 covers the edge case where the file's last line has
        # no trailing newline and the partial-first-line discard below.
        collected_bytes = bytearray()
        _CHUNK = 64 * 1024
        offset = file_size
        try:
            with open(path, "rb") as fh:
                while offset > 0:
                    read_size = min(_CHUNK, offset)
                    offset -= read_size
                    fh.seek(offset)
                    chunk = fh.read(read_size)
                    if not chunk:
                        break
                    # Prepend so chronological order is preserved
                    # (newer bytes land at the end of collected_bytes).
                    collected_bytes = bytearray(chunk) + collected_bytes
                    if collected_bytes.count(b"\n") >= want_lines + 1:
                        break
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning(
                "proc tail read failed for %s: %s",
                info.process_id,
                exc,
            )
            return []

        text = collected_bytes.decode(errors="replace")
        lines = text.splitlines()
        if not lines:
            return []

        # If we broke out of the loop early (offset > 0), the first
        # line in our window may be a partial TAIL of a line whose
        # HEAD is in the older, unread portion of the file. Discard
        # it — we have enough additional lines to satisfy ``want_lines``
        # (the loop only breaks after seeing want_lines+1 newlines).
        if offset > 0 and len(lines) > want_lines:
            lines = lines[1:]

        # Return the most recent ``want_lines`` (last N in chronological
        # order = newest in the spill file = closest in time to the
        # memory buffer the caller will merge with).
        return lines[-want_lines:]

    # -- pretty-print helpers -----------------------------------------------

    @staticmethod
    def _command_repr(command: object) -> str:
        """Compact, single-line command representation for ``proc_list``."""
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        text = str(command)
        # Collapse multi-line shell commands to a single line so the
        # table row stays compact. Truncate long commands.
        oneline = " ".join(text.split())
        if len(oneline) > 80:
            return oneline[:77] + "..."
        return oneline


# ---------------------------------------------------------------------------
# Module-level singleton
#
# The instance lifecycle code (daemon/manager.py) imports this to drive
# cleanup_instance() on instance termination. Tests can monkeypatch it
# or instantiate their own BackgroundProcessManager().
# ---------------------------------------------------------------------------

_background_process_manager = BackgroundProcessManager()


def get_background_process_manager() -> BackgroundProcessManager:
    """Return the module-level :class:`BackgroundProcessManager` singleton.

    Exposed as a function (not a bare global) so tests can patch the
    accessor when they want to inject a stub manager without touching
    module globals.
    """
    return _background_process_manager


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def _format_command_repr(command: object) -> str:
    """Return a compact, human-readable version of ``command`` for tool output."""
    if isinstance(command, list):
        # Join argv with single spaces, quote parts that contain
        # whitespace — for tool STARTUP output (not for the table).
        return " ".join(repr(str(part)) if " " in str(part) else str(part) for part in command)
    oneline = " ".join(str(command).split())
    if len(oneline) > 120:
        return oneline[:117] + "..."
    return oneline


def create_proc_tools(current_instance_id: str = "") -> list:
    """Create background-process tools scoped to ``current_instance_id``.

    The tools share :data:`_background_process_manager` (a module-level
    singleton) so processes survive across tool calls within an
    instance. The ``current_instance_id`` is captured by closure so
    callers do not need to provide it — agents operate against their
    own instance's process bucket.

    Tools created:
        * :func:`proc_run`
        * :func:`proc_logs`
        * :func:`proc_status`
        * :func:`proc_stop`
        * :func:`proc_list`

    Args:
        current_instance_id: Owning instance id. Empty string disables
            the tools (returns no tools), matching the convention used
            by other factories that don't have a context yet.

    Returns:
        List of LangChain tool functions. Empty list when no instance.
    """
    if not current_instance_id:
        # No instance context — return an empty list rather than a
        # half-configured toolset. Matches the safety pattern used by
        # other factories.
        return []

    # Capture into the closure so each tool reuses the same
    # manager / instance pair. Async tools (``proc_run``, ``proc_stop``)
    # are real ``async def``; sync tools (``proc_logs``, ``proc_status``,
    # ``proc_list``) are plain ``def`` that talk to the manager's
    # in-memory state without awaiting anything — keeping them sync is
    # fine because the captured state IS thread-safe under asyncio
    # (only ``_lock``-protected paths mutate; readers see a stable
    # snapshot).

    manager = _background_process_manager

    # Closure-captured id. Re-rebinds on each factory call which is
    # what we want — different instance, different toolset.
    _instance_id: str = current_instance_id

    # -----------------------------------------------------------------
    # proc_run — start a background process and return its id
    # -----------------------------------------------------------------
    @register_tool_category("proc")
    @tool
    async def proc_run(
        command: str,
        workdir: str | None = None,
        timeout: int = 0,
    ) -> str:
        """Start a command in the background. Returns process_id. Use tool_help('proc_run') for details.

        Args:
            command: The command to execute. Strings run via shell;
                pass ``["ls", "-la"]`` style lists (not supported
                here — use bash for argv form) — this tool always
                uses shell semantics because background processes
                typically need shell features (&, pipes, redirects).
            workdir: Working directory for the command. ``None``
                inherits the parent process's current directory.
                ``""`` is treated the same as ``None``.
            timeout: Optional auto-kill timeout in seconds
                (``0`` = no timeout, never auto-killed). When set,
                the process is SIGKILLed after ``timeout`` seconds.

        Returns:
            On success: ``"Started process: proc-a3b2c1d4 ..."`` with
            pid, command, and workdir for confirmation.
            On failure: ``"Error: ..."`` string describing why
            (process cap reached, command not found, etc.).
        """
        # Defensive: timeout validation (matches ``bash`` semantics
        # of accepting 0 = no-timeout). No explicit upper bound here
        # because the user explicitly asked for a long-running
        # process — 1800s would be too restrictive.
        if timeout is not None and timeout < 0:
            return f"Error: timeout must be >= 0 seconds. Got: {timeout}s"

        # Normalize null-ish workdir to None.
        if workdir is not None and str(workdir).strip().lower() in (
            "",
            "null",
            "none",
        ):
            workdir = None

        process_id, err = await manager.start_process(
            instance_id=_instance_id,
            command=command,
            workdir=workdir,
            timeout_seconds=timeout if timeout else 0,
        )
        if err is not None:
            return err
        # ``process_id`` is guaranteed non-None when ``err`` is None.
        assert process_id is not None  # for type checkers

        # Look up the freshly-started process to surface the PID.
        info = manager._processes.get(_instance_id, {}).get(process_id)
        pid_str = f", pid={info.proc.pid}" if info and info.proc else ""
        timeout_note = (
            f" (auto-kill after {timeout}s)"
            if timeout and timeout > 0
            else "none"
        )
        return (
            f"Started process: {process_id}{pid_str}\n"
            f"command: {_format_command_repr(command)}\n"
            f"workdir: {workdir or '<inherited>'}\n"
            f"timeout: {timeout_note}\n"
            f"Use proc_logs('{process_id}', lines=50) to read output, "
            f"proc_status('{process_id}') for state, "
            f"proc_stop('{process_id}') to terminate."
        )

    proc_run._full_doc_ = """\
Start a long-running command in the background and return its
process_id immediately.

Unlike ``bash`` (which blocks until the command exits), ``proc_run``
returns a ``process_id`` so the agent can read logs, check status,
and stop the process on demand. Typical use cases: dev servers
(uvicorn, vite, webpack), test watchers (pytest-watch), file
watchers, debug REPLs.

**Capabilities**:
- Captures merged stdout+stderr asynchronously into a hybrid
  (memory + temp file) log buffer.
- Enforces a per-instance concurrent-process cap
  (``proc`` category registry: see ``MAX_PROCESSES_PER_INSTANCE``).
- Optional ``timeout`` parameter auto-kills the process.
- Graceful stop (``proc_stop`` default → SIGTERM → SIGKILL after 5s
  grace). Use ``force=True`` to skip SIGTERM and SIGKILL immediately.

**Limits**:
- 10 concurrent processes per instance.
- 4 MB memory buffer per process; older half spills to a temp file.
- No stdin interaction.

Args:
    command: Shell command string.
    workdir: Working directory. ``None`` inherits parent CWD.
    timeout: Auto-kill timeout in seconds (``0`` = no timeout).

Returns:
    On success: confirmation string including the ``process_id``.
    On failure: ``"Error: ..."`` string.
"""

    # -----------------------------------------------------------------
    # proc_logs — read recent captured output
    # -----------------------------------------------------------------
    @register_tool_category("proc")
    @tool
    async def proc_logs(process_id: str, lines: int = 50) -> str:
        """Read the last N lines of captured output. Use tool_help('proc_logs') for details.

        Args:
            process_id: The id returned by ``proc_run``.
            lines: How many recent lines to return (default ``50``,
                max ``5000``). Output is oldest→newest.

        Returns:
            Text of recent logs, or ``"Error: ..."`` on failure.
        """
        info = manager._processes.get(_instance_id, {}).get(process_id)
        if info is None:
            return (
                f"Error: process {process_id!r} not found for this "
                f"instance. Use proc_list() to see active ids."
            )

        # Sanitize ``lines``.
        try:
            n = int(lines)
        except (TypeError, ValueError):
            return f"Error: lines must be an integer, got {lines!r}"
        if n <= 0:
            return f"Error: lines must be > 0, got {n}"

        # Empty buffer fast path — guard so the caller doesn't get a
        # confusing empty string with no status hint.
        if not info.memory_buffer and info.file_path is None:
            status_hint = info.status
            return (
                f"(no output captured yet; status={status_hint})"
            )

        # M3 fix: ``_get_recent_lines`` is now async because it may
        # tail a large spill file via ``asyncio.to_thread`` — large
        # spill files would otherwise stall the event loop.
        body = await manager._get_recent_lines(info, n)
        if not body:
            return (
                f"(no output captured yet; status={info.status})"
            )

        # Annotate with status so the agent can decide whether to
        # wait or stop.
        header = f"--- proc_logs {process_id} (last {n} lines, status={info.status}) ---"
        return f"{header}\n{body}"

    proc_logs._full_doc_ = """\
Read the last N lines of captured stdout+stderr from a background
process.

Output is stored in a hybrid (memory + spill file) buffer with a
4 MB per-process memory cap; older output spills to a tempfile on
overflow. ``proc_logs`` reads memory first (fast path) and tails
the spill file only when the requested line count exceeds what
memory holds.

Args:
    process_id: ``process_id`` returned by ``proc_run``.
    lines: Number of lines to return (default 50, max 5000).
        Output ordering is oldest → newest.

Returns:
    The requested lines as text, prefixed with a status header.
    Empty buffer returns a friendly hint instead of an empty string.
"""

    # -----------------------------------------------------------------
    # proc_status — report process status + diagnostics
    # -----------------------------------------------------------------
    @register_tool_category("proc")
    @tool
    def proc_status(process_id: str) -> str:
        """Report process status, pid, uptime, exit code, buffer size. Use tool_help('proc_status') for details.

        Args:
            process_id: The id returned by ``proc_run``.

        Returns:
            Multi-line status string, or ``"Error: ..."`` if the
            process is unknown to this instance.
        """
        return manager.get_status(_instance_id, process_id)

    proc_status._full_doc_ = """\
Get a process's current state (running, exited, killed, error),
PID, uptime, exit code, buffer size, and any tracked error.

Args:
    process_id: ``process_id`` returned by ``proc_run``.

Returns:
    Multi-line status. ``status: running`` → still active;
    ``status: exited`` → exited cleanly (exit_code); ``status:
    killed`` → killed by SIGTERM/SIGKILL or user-requested stop;
    ``status: error`` → spawn / wait failed.
"""

    # -----------------------------------------------------------------
    # proc_stop — terminate a background process
    # -----------------------------------------------------------------
    @register_tool_category("proc")
    @tool
    async def proc_stop(process_id: str, force: bool = False) -> str:
        """Stop a background process. SIGTERM by default; SIGKILL when force=True. Use tool_help('proc_stop') for details.

        Args:
            process_id: The id returned by ``proc_run``.
            force: ``True`` skips SIGTERM and goes straight to SIGKILL.
                Use this for unresponsive processes.

        Returns:
            Confirmation string including the last ~20 lines of
            captured output (so the agent can see what the process
            printed before exiting). ``"Error: ..."`` on failure.
        """
        return await manager.stop_process(
            instance_id=_instance_id,
            process_id=process_id,
            force=force,
        )

    proc_stop._full_doc_ = """\
Stop a background process.

Strategy: ``SIGTERM`` → wait up to 5 seconds for graceful exit →
``SIGKILL``. With ``force=True``, ``SIGTERM`` is skipped and
``SIGKILL`` is sent immediately. Kills the entire process group
on Unix so backgrounded children (e.g. ``nohup ... &``) are
cleaned up too — mirrors the ``bash`` tool's kill semantics.

Args:
    process_id: ``process_id`` returned by ``proc_run``.
    force: ``True`` to skip SIGTERM and SIGKILL immediately.

Returns:
    Confirmation string with the last ~20 lines of captured output.
"""

    # -----------------------------------------------------------------
    # proc_list — list this instance's processes
    # -----------------------------------------------------------------
    @register_tool_category("proc")
    @tool
    def proc_list() -> str:
        """List all background processes owned by this instance. Use tool_help('proc_list') for details.

        Returns:
            Markdown table of ``process_id | status | command |
            uptime`` rows, or a friendly empty-state message.
        """
        return manager.list_processes(_instance_id)

    proc_list._full_doc_ = """\
List every background process owned by the current instance.

Args: (none)

Returns:
    Markdown table of processes with status, command (truncated to
    ~80 chars), and uptime in seconds. Stable ordering for diff-friendly
    re-reads.
"""

    return [proc_run, proc_logs, proc_status, proc_stop, proc_list]
