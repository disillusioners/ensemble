"""Low-level spawner helpers for the ``service`` tool category (Phase 1.B).

Companion module to :mod:`daemon.services.service_tool_manager` — this
file is the *thin Popen wrapper* the manager calls into. It is the
**only** file in the ``service`` track that touches ``subprocess.Popen``
directly; every other layer (the ``ServiceToolManager``,
``ServiceReconciliationService``, the tools) consumes the helpers
exported here.

Design notes (D1 of ``.agents/shared/planning/service-tool/decisions.md``):

* Spawn primitive is ``subprocess.Popen(start_new_session=True,
  close_fds=True, stdin=DEVNULL, stdout=log_fh, stderr=STDOUT)`` — the
  direct precedent is
  :func:`daemon.tools.upgrade_journal.spawn_executor` (lines 1010-1034
  on the plan-time anchor; SYMBOL-locatable). ``start_new_session=True``
  is the Python equivalent of C's double-fork + ``setsid(2)`` and makes
  the child its own session + process-group leader (pgid == pid), which
  is what :func:`os.killpg` relies on in the kill path (A1).
* stdio is ALWAYS a file (NOT a pipe) — the bash tool documents the
  failure mode (backgrounded child holding a pipe write-end →
  ``communicate()`` hangs forever, ``daemon/tools/bash.py:243-255``).
* ``get_process_start_time`` is cross-platform: Linux parses
  ``/proc/<pid>/stat`` field 22 (starttime jiffies since boot); macOS
  parses ``ps -o lstart`` to epoch seconds. Windows raises
  ``NotImplementedError`` — the platform is not supported (no frozen
  PyInstaller build ships on Windows for the ``service`` track today).
* There is deliberately NO ``stop`` helper here (council Finding 2
  deleted the dead, ownership-unverified one): the manager's inline
  grace loop in ``daemon/services/service_tool_manager.py`` is the
  SOLE kill path for the service track, and every signal site there
  re-verifies ``(pid, start_time)`` ownership before signaling.

NOT registered in any tool registry — this module exposes *helpers*, not
@tool-decorated LangChain tools. The 1.C lane wires
:mod:`daemon.tools.service_tools` into ``CATEGORY_MODULES``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────


#: Root directory for service log files. Resolved via the same env
#: override pattern as the daemon log dir (``daemon/api.py:59`` —
#: ``DAEMON_LOG_DIR`` defaults to ``./data/logs``). The ``services/``
#: subdirectory is a sibling of ``logs/`` so operators have one stable
#: place to find every long-lived process's stdio log.
_LOG_ROOT_ENV_VAR: str = "ENSEMBLE_SERVICE_LOG_DIR"

#: Default log root: relative to ``os.getcwd()`` (matches the daemon's
#: relative-anchor convention used by ``./data/logs/ensemble.log``).
_DEFAULT_LOG_ROOT: str = "./data/services"

#: Bytes cap on a single tail read from a service log — protects
#: ``service_logs`` from runaway reads against a 100 MB log (1.B.7).
MAX_LOG_TAIL_BYTES: int = 10 * 1024 * 1024  # 10 MB

#: Default number of lines returned by ``service_logs`` when the caller
#: does not specify a count.
DEFAULT_TAIL_LINES: int = 200


def kill_log_path(name: str) -> str:
    """Return the absolute log path for a service, creating the directory.

    Args:
        name: Service name (validated to match ``^[a-zA-Z0-9_-]+$`` at
            the caller boundary — this helper does not re-validate).

    Returns:
        Absolute path string to ``<log_root>/<name>.log``. The
        ``<log_root>`` directory is created on first call so the
        ``Popen`` ``stdout=log_fh`` file-handle can open the file
        before the spawn.

    The directory is created via ``os.makedirs(..., exist_ok=True)`` —
    idempotent and racy-safe across concurrent first-calls (the
    OS-level mkdir is atomic on POSIX for a single directory).
    """
    root = os.environ.get(_LOG_ROOT_ENV_VAR, _DEFAULT_LOG_ROOT)
    log_path = os.path.join(root, f"{name}.log")
    # Resolve to absolute so the row's ``log_path`` column is portable
    # across relative-cwd callers (e.g. a tool call invoked from a
    # different daemon cwd via subprocess tests).
    abs_log_path = os.path.abspath(log_path)
    os.makedirs(os.path.dirname(abs_log_path), exist_ok=True)
    return abs_log_path


# ─────────────────────────────────────────────────────────────────────
# Spawn
# ─────────────────────────────────────────────────────────────────────


def spawn(
    argv: list[str],
    log_path: str,
    cwd: Optional[str] = None,
) -> tuple[int, int]:
    """Spawn a detached service process and return ``(pid, start_time)``.

    The child owns its stdio (the log file is the canonical sink; no
    pipe → no ``communicate()`` hang, no parent-side reader task
    overhead). The child is in a brand-new session + process group
    (``start_new_session=True``) so :func:`os.killpg(pid, sig)` reaches
    every descendant.

    Args:
        argv: Command and arguments as a list (NOT a shell string —
            D1 disallows shell expansion to close the injection class).
        log_path: Absolute path to the stdio log file. The file is
            created/truncated by the ``open`` call inside this helper;
            the caller does NOT pre-create it.
        cwd: Working directory for the child. ``None`` inherits the
            daemon cwd. An invalid cwd raises ``FileNotFoundError`` /
            ``NotADirectoryError`` synchronously — the F8 caller
            (in ``ServiceToolManager.start``) catches this and writes
            an EXITED row immediately.

    Returns:
        ``(pid, start_time)`` — ``start_time`` is the kernel starttime
        in the platform-local format (Linux jiffies since boot,
        macOS epoch seconds). The manager stores this value on the
        row for the F1 PID-reuse defense; subsequent
        :func:`get_process_start_time` calls return the SAME value iff
        the PID was NOT recycled.

    Raises:
        OSError: Synchronous spawn failure (binary missing — ENOENT;
            bad cwd — FileNotFoundError; EACCES on the log file; etc.).
            The F8 caller MUST catch and convert to an EXITED row.
        ValueError: ``argv`` is empty.
    """
    if not argv:
        raise ValueError("spawn requires a non-empty argv list")

    # Ensure the log directory exists BEFORE opening the log file —
    # this lets ``spawn`` be called directly (in tests or by an
    # operator-side admin path) without first calling
    # :func:`kill_log_path`. Idempotent and racy-safe (mkdir on a
    # single directory is atomic on POSIX). The manager path still
    # calls ``kill_log_path`` ahead of ``spawn`` so the row's
    # ``log_path`` is the canonical absolute path.
    log_dir = os.path.dirname(os.path.abspath(log_path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Open the log file BEFORE Popen — stdio MUST be a file at the
    # moment the child is created (the bash tool documents the pipe-
    # hang hazard). ``ab`` keeps any prior content for forensic
    # inspection after the service restarts; the child's stdout writer
    # positions at the existing EOF.
    with open(log_path, "ab") as log_fh:
        # ``start_new_session=True`` ≡ C setsid(2) — puts the child in
        # a brand-new session + process group (pgid == pid). On Linux
        # + macOS this is verified for Python 3.10+ (the plan's binding
        # contract). ``close_fds=True`` is the standard defense
        # against FD-leak across fork; the child has no inherited
        # sockets / pipes.
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
        )
        pid = proc.pid

    # Record the start time IMMEDIATELY after spawn — every subsequent
    # ``get_process_start_time(pid)`` call will return this value iff
    # the PID is still owned by us. The window between Popen and this
    # call is microseconds (no I/O); PID reuse is essentially
    # impossible. We DO NOT use ``proc`` after this point (the child's
    # stdio is owned by the OS; we never call ``proc.wait()``).
    start_time = get_process_start_time(pid)
    if start_time is None:
        # The child died between Popen and the start_time read — race
        # that effectively never happens on real hardware but is
        # possible in extreme test environments (PID exhausted). The
        # F8 caller writes an EXITED row; we still return the PID so
        # the caller can killpg a zombie (killpg with no live process
        # is a no-op ESRCH — safe).
        logger.warning(
            "[ServiceSpawner] start_time unresolvable for pid=%s; "
            "child likely exited before start_time could be read",
            pid,
        )
    return pid, int(start_time) if start_time is not None else 0


# ─────────────────────────────────────────────────────────────────────
# get_process_start_time — cross-platform PID-ownership token
# ─────────────────────────────────────────────────────────────────────


def get_process_start_time(pid: int) -> Optional[int]:
    """Return the kernel starttime token for a live PID, or ``None``.

    This is the **PID-ownership token** the F1 PID-reuse defense in
    :mod:`daemon.services.service_tool_manager` compares against the
    stored ``row.start_time``. Equality means the PID is still our
    service; inequality means the kernel recycled the PID onto a
    different (unrelated) process.

    Cross-platform contract:

    * **Linux** — parse ``/proc/<pid>/stat`` field 22 (starttime in
      jiffies since boot). Field 22 is ``comm``-aware: it skips the
      right paren of the (possibly multi-word) ``comm`` field by
      anchoring on the LAST ``)`` before splitting on whitespace
      (precedent: ``proc_tools.py`` start-time liveness check).
    * **macOS** — ``ps -o lstart= -p <pid>`` yields a human timestamp
      like ``Wed Sep 16 03:42:17 2026``; we parse it to epoch seconds
      via ``time.strptime`` + ``timegm``.
    * **Windows** — ``NotImplementedError`` (the daemon is not
      shipped on Windows for the ``service`` track).

    Args:
        pid: PID to introspect.

    Returns:
        Start-time token (Linux jiffies or macOS epoch seconds), or
        ``None`` if the PID is dead (``/proc`` read returns ENOENT on
        Linux, ``ps`` returns empty on macOS).

    Raises:
        NotImplementedError: Platform is Windows.
    """
    if sys.platform == "win32":
        raise NotImplementedError(
            "service_spawner.get_process_start_time is not supported "
            "on Windows (the service track ships on Linux + macOS only)"
        )

    if sys.platform.startswith("linux"):
        return _get_start_time_linux(pid)
    if sys.platform == "darwin":
        return _get_start_time_darwin(pid)

    # Other POSIX (BSD / Solaris): not in scope for the v1 ships; raise
    # explicitly so a future contributor is forced to make a decision.
    raise NotImplementedError(
        f"get_process_start_time is not implemented for platform "
        f"{sys.platform!r}; only Linux and macOS are supported"
    )


def _get_start_time_linux(pid: int) -> Optional[int]:
    """Linux implementation: parse ``/proc/<pid>/stat`` field 22.

    The kernel escapes spaces in ``comm`` (the command name) only for
    display; the raw ``/proc/<pid>/stat`` line uses ``(`` / ``)`` to
    delimit the command field and may contain ANY character inside the
    parens including spaces and ``)`` itself. We anchor on the LAST
    ``)`` to skip past the command field reliably.
    """
    stat_path = f"/proc/{pid}/stat"
    try:
        with open(stat_path, "r", encoding="ascii") as fh:
            line = fh.read()
    except FileNotFoundError:
        # PID is dead (or never existed). Distinguish "dead" from "we
        # have permission issues" — the latter would raise PermissionError
        # above; FileNotFoundError here means ESRCH.
        return None
    except ProcessLookupError:  # pragma: no cover - defensive
        return None

    # Anchor on the LAST ``)`` to skip the (possibly multi-word) comm
    # field. Field 22 is the 22nd whitespace-separated token AFTER the
    # ``)`` — i.e. fields 3..22 are positions 1..20 after the anchor.
    close_paren = line.rfind(")")
    if close_paren == -1:
        return None
    tail = line[close_paren + 1 :].split()
    # tail[0] is field 3 (state), tail[19] is field 22 (starttime).
    # Field 22 is jiffies since boot (CLK_TCK dependent, but the value
    # is opaque — we only compare for EQUALITY, never convert to
    # seconds).
    if len(tail) < 20:
        return None
    try:
        return int(tail[19])
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return None


def _get_start_time_darwin(pid: int) -> Optional[int]:
    """macOS implementation: parse ``ps -o lstart`` to epoch seconds.

    The ``lstart`` column prints like ``Wed Sep 16 03:42:17 2026``
    (the system locale's date format). We use ``time.strptime`` with
    the canonical C locale format string and ``timegm`` to convert
    UTC-naive to epoch seconds. ``ps`` prints in the system's local
    timezone; for the equality-comparison use case the absolute value
    does not need to be UTC-correct (we are comparing tokens, not
    measuring uptime) but converting via ``timegm`` keeps the token
    monotonic and stable across restarts.
    """
    import calendar

    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug(
            "[ServiceSpawner] ps -o lstart failed for pid=%s: %s", pid, exc
        )
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    text = out.stdout.strip()
    # macOS ``ps -o lstart`` prints e.g. ``Wed Sep 16 03:42:17 2026``
    # — the canonical C-locale strptime format.
    try:
        parsed = time.strptime(text, "%a %b %d %H:%M:%S %Y")
        return int(calendar.timegm(parsed))
    except ValueError:
        # Future-proofing for locales that diverge from the C format.
        logger.debug(
            "[ServiceSpawner] lstart parse failed for pid=%s text=%r",
            pid,
            text,
        )
        return None


def is_process_alive(pid: int) -> bool:
    """Return True iff the PID is alive AND not a zombie.

    A zombie's stat file still exists on Linux (``/proc/<pid>/stat``
    readable, ``state`` field = ``Z``) but the process is a corpse —
    ``os.kill(pid, 0)`` returns success for a zombie too. The plan's
    "Zombie-liveness (approver gate)" item requires the liveness check
    to exclude zombies; we use the ``state`` field on Linux and the
    ``ps stat`` column on macOS.

    Args:
        pid: PID to probe.

    Returns:
        ``True`` iff the PID exists AND is not a zombie (state !=
        ``Z``). ``False`` if dead or zombie.

    Raises:
        NotImplementedError: Platform is Windows.
    """
    if sys.platform == "win32":
        raise NotImplementedError(
            "service_spawner.is_process_alive is not supported on Windows"
        )
    if sys.platform.startswith("linux"):
        return _is_alive_linux(pid)
    if sys.platform == "darwin":
        return _is_alive_darwin(pid)
    raise NotImplementedError(
        f"is_process_alive is not implemented for platform {sys.platform!r}"
    )


def _is_alive_linux(pid: int) -> bool:
    """Linux implementation: read state from ``/proc/<pid>/stat``.

    State ``Z`` = zombie; we treat as NOT alive (the plan's zombie-
    liveness approver gate). State ``X`` = dead (transient); treat as
    NOT alive. All other states (``R``, ``S``, ``D``, ``T``, ``I``,
    ``W``) are alive.
    """
    stat_path = f"/proc/{pid}/stat"
    try:
        with open(stat_path, "r", encoding="ascii") as fh:
            line = fh.read()
    except (FileNotFoundError, ProcessLookupError):
        return False
    # Anchor on the LAST ``)`` — same pattern as the start-time reader
    # above. The first whitespace-separated token AFTER ``)`` is field
    # 3 = state.
    close_paren = line.rfind(")")
    if close_paren == -1:
        return False
    tail = line[close_paren + 1 :].lstrip()
    if not tail:
        return False
    state = tail[0]
    # Z = zombie, X = dead — both treated as NOT alive.
    if state in ("Z", "X"):
        return False
    return True


def _is_alive_darwin(pid: int) -> bool:
    """macOS implementation: parse ``ps -o stat`` column.

    ``ps -o stat=`` prints the state char (single letter, sometimes
    followed by ``+`` for foreground). ``Z`` / ``Z+`` = zombie. We
    split on whitespace and check the first token.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if out.returncode != 0 or not out.stdout.strip():
        return False
    state = out.stdout.strip().split()[0]
    # ``Z`` or ``Z+`` is a zombie on macOS — treat as NOT alive.
    return state != "Z" and not state.startswith("Z")


# ─────────────────────────────────────────────────────────────────────
# (no low-level stop helper)
# ─────────────────────────────────────────────────────────────────────
#
# The former ``stop(pid, force, grace_seconds)`` low-level helper was
# DELETED (council Finding 2): it was dead code — the manager
# implements its own grace loop — and its grace loop carried NO
# ``(pid, start_time)`` ownership re-verify, so keeping it exported
# preserved an unsafe kill path behind a stale "verified-call-boundary"
# docstring. The manager's inline loop IS the sole kill path for the
# service track, and EVERY signal site there is ownership-verified
# (pre-signal / per-poll / pre-escalation / lost-race cleanup — see
# the PID-reuse-defense enumeration in
# ``daemon/services/service_tool_manager.py``). Do not re-introduce a
# signal helper here; signals belong behind the manager's ownership
# re-verify.


__all__ = [
    "DEFAULT_TAIL_LINES",
    "MAX_LOG_TAIL_BYTES",
    "get_process_start_time",
    "is_process_alive",
    "kill_log_path",
    "spawn",
]