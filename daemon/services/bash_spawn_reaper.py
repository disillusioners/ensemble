"""Bash-spawn durable registry reaper service.

Bash-tool orphan reaper — ``daemon/services/bash_spawn_reaper.py``.

Phase 1 shipped the WRITE-SIDE + DISCRIMINATORS: the boot id, the
kill-switch parse, the hard guards + D8/D9 start-time gate helpers, and
the platform env read-backends ported from ``daemon/tools/proc_tools.py``.

Phase 2 (this state) completes ``verify_and_kill`` with the FULL
verification predicate + attribution + the TERM→grace→KILL ladder
(plan task 2.1), and adds the shared per-row sweep executor
(:func:`sweep_rows`), the per-instance lane (:func:`sweep_instance`,
driven by ``BashProcessRegistry.cleanup_instance``), and the shutdown
lane (:func:`sweep_open_rows_for_shutdown`, driven by the
``manager.shutdown`` Phase-3 block). The one-shot boot reconcile
(:func:`reconcile_stale_bash_spawns`) remains Phase 3.

KNOBS (boot-time env, O1 convention; deliberately NOT app_settings —
this runs before config load and is boot-lane hygiene, not a managed
runtime key):

* ``ENSEMBLE_BASH_ORPHAN_REAPER`` — MASTER switch. **Default ON in ALL
  lanes when absent (F6 leader ruling — the 2026-10-31 incident ran in
  the dev lane; the row-scoped positive discriminator does not inherit
  the boot sweep's dev-OFF over-broad-candidates rationale).** Explicit
  ``0/false/no/off`` (case-insensitive) = OFF everywhere; explicit
  anything else (including empty/whitespace) = ON everywhere. OFF is a
  COMPLETE no-op: no stamps, no row writes, no sweeps, AND ZERO DB
  QUERIES at every cleanup point (m3 — disabled sweeps never consult
  the repository), one INFO line at boot. Parsing mirrors
  ``daemon/services/boot_subprocess_sweep.py:kill_switch_from_env`` with
  the lane default FLIPPED.
* ``ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP`` — leg knob (Phase 3): set
  ``0`` disables ONLY the boot reconcile even when master is ON.
* ``ENSEMBLE_BASH_REAPER_TERM_GRACE_SECONDS`` — TERM→KILL grace window
  (Phase 2); default 5.0, clamped to [0.5, 30.0].
* ``ENSEMBLE_BASH_REAPER_START_TOLERANCE_SECONDS`` — D9 hard start-time
  gate tolerance (default 10.0 s; covers shell fork/exec latency).

Restart-lane exclusion is STRUCTURAL (plan D1): the bash seam is the
ONLY writer of ``bash_spawn_registry``; ``spawn_executor`` deliberately
skips every registry (``daemon/tools/upgrade_journal.py:2913-2915``) and
the executor env allowlist strips ``ENSEMBLE_BASH_TRACKING_ID``
(``upgrade_journal.py:2157-2188``), so restart-lane processes can never
carry a row. Pinned by ``tests/unit/tools/test_agent_restart.py``.

ACTIVATION (restart-class, constraint 5): this feature activates on a
daemon rebuild + restart. The ``bash_spawn_registry`` table lands via
``SQLModel.metadata.create_all`` (manager init) on the first boot after
activation, so that first boot has NO rows from a previous boot and the
boot reconcile is a no-op. The reconcile only sweeps pre-restart rows on
the SECOND restart (once rows from the post-activation daemon exist),
which is the intended behavior — rows are written durable at spawn time
before any detach opportunity, and the boot sweep reads them on the next
boot.

Concurrency: this module holds NO locks; the repository is sync and
callers bridge with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4
from daemon.services.timestamps import now_utc_iso

logger = logging.getLogger(__name__)

# ── Boot id (D8) — one per daemon process, minted at import ──────────────────
CURRENT_BOOT_ID: str = uuid4().hex

# ── Env var names ────────────────────────────────────────────────────────────
ENV_KILL_SWITCH = "ENSEMBLE_BASH_ORPHAN_REAPER"
ENV_BOOT_SWEEP = "ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP"
ENV_TERM_GRACE = "ENSEMBLE_BASH_REAPER_TERM_GRACE_SECONDS"
ENV_START_TOLERANCE = "ENSEMBLE_BASH_REAPER_START_TOLERANCE_SECONDS"

# The secondary discriminator injected into the spawned child's env.
BASH_TRACKING_ENV_VAR: str = "ENSEMBLE_BASH_TRACKING_ID"

_DISABLE_VALUES = {"0", "false", "no", "off"}

DEFAULT_TERM_GRACE_SECONDS = 5.0
MIN_TERM_GRACE_SECONDS = 0.5
MAX_TERM_GRACE_SECONDS = 30.0

DEFAULT_START_TOLERANCE_SECONDS = 10.0


# ── Kill-switch parse (F6: lane default FLIPPED to ON) ───────────────────────
def kill_switch_from_env(environ: Optional[dict] = None, *, is_prod: Optional[bool] = None) -> bool:
    """Resolve the master switch.

    Explicit-override semantics (mirroring
    ``boot_subprocess_sweep.kill_switch_from_env``) with the lane default
    FLIPPED to ON in ALL lanes (F6 leader ruling). ``is_prod`` is accepted
    for signature parity but does NOT change the absent-default (always ON).
    """
    env = os.environ if environ is None else environ
    val = env.get(ENV_KILL_SWITCH)
    if val is not None:
        raw = val.strip().lower()
        if not raw:
            return True  # operator set the var to empty/whitespace: ON
        return raw not in _DISABLE_VALUES
    # Absent ⇒ ON in ALL lanes (F6).
    return True


def boot_sweep_leg_from_env(environ: Optional[dict] = None) -> bool:
    """Phase 3 leg knob: master ON + this leg OFF ⇒ boot reconcile no-op."""
    env = os.environ if environ is None else environ
    val = env.get(ENV_BOOT_SWEEP)
    if val is None:
        return True  # absent ⇒ leg follows master
    raw = val.strip().lower()
    if not raw:
        return True
    return raw not in _DISABLE_VALUES


def term_grace_seconds_from_env(environ: Optional[dict] = None) -> float:
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_TERM_GRACE) or "").strip()
    if not raw:
        return DEFAULT_TERM_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TERM_GRACE_SECONDS
    return max(MIN_TERM_GRACE_SECONDS, min(MAX_TERM_GRACE_SECONDS, value))


def start_tolerance_seconds_from_env(environ: Optional[dict] = None) -> float:
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_START_TOLERANCE) or "").strip()
    if not raw:
        return DEFAULT_START_TOLERANCE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_START_TOLERANCE_SECONDS
    if value < 0:
        return DEFAULT_START_TOLERANCE_SECONDS
    return value


# ── Linux jiffies reader (ported from daemon/tools/bash.py) ──────────────────
def _read_proc_start_time(pid: int) -> Optional[int]:
    if sys.platform == "win32":
        return None
    try:
        with open(f"/proc/{pid}/stat", "r") as h:
            line = h.readline()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    last_paren = line.rfind(")")
    if last_paren == -1:
        return None
    _, _, tail = line.rpartition(")")
    parts = tail.strip().split()
    try:
        return int(parts[19])
    except (ValueError, IndexError):
        return None


# ── D9: process start wall-clock ─────────────────────────────────────────────
def process_start_wallclock(pid: int) -> Optional[datetime]:
    """Return the process's wall-clock start time (UTC) or None.

    Linux: /proc/<pid>/stat field-22 jiffies + /proc/uptime wall conversion.
    macOS/BSD: ``ps -o lstart= -p <pid>`` absolute start.
    Else / unparseable / missing: None (fail-closed — the D9 gate refuses).
    """
    if pid is None or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        jiffies = _read_proc_start_time(pid)
        if jiffies is None:
            return None
        try:
            with open("/proc/uptime", "r") as h:
                uptime_s = float(h.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        try:
            clk_tck = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            clk_tck = 100
        if not clk_tck:
            clk_tck = 100
        boot_time = datetime.now(timezone.utc) - timedelta(seconds=uptime_s)
        return boot_time + timedelta(seconds=jiffies / clk_tck)
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        try:
            completed = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if completed.returncode != 0:
            return None
        raw = completed.stdout.strip()
        if not raw:
            return None
        for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b  %d %H:%M:%S %Y"):
            try:
                dt = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            # ``ps -o lstart=`` emits LOCAL wall time — interpret the
            # naive parse as local and convert to UTC. Stamping it
            # ``tzinfo=utc`` directly shifts by the local UTC offset
            # (e.g. +07) and makes every start read as a FUTURE
            # instant — defeating BOTH the D8 live-sibling skip
            # (mis-classified as a recycled holder ⇒ proceed) and the
            # D9 hard gate (members read too young ⇒ spurious refusals).
            return dt.astimezone(timezone.utc)
        return None
    # Other platforms: fail-closed (no start time determinable).
    return None


# ── Group member enumeration ──────────────────────────────────────────────────
def _proc_state_and_ppid(pid: int) -> Optional[tuple[str, Optional[int]]]:
    """Return (state, ppid) for a pid, or None when unreadable.

    Linux: /proc/<pid>/stat fields 3 (state) + 4 (ppid) — the parts
    after the ``comm`` paren: index 0 = state, index 1 = ppid.
    macOS/BSD: ``ps -o state=,ppid= -p <pid>``.
    """
    if pid is None or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "r") as h:
                line = h.readline()
        except (OSError, FileNotFoundError, PermissionError):
            return None
        last_paren = line.rfind(")")
        if last_paren == -1:
            return None
        _, _, tail = line.rpartition(")")
        parts = tail.strip().split()
        try:
            state = parts[0]
            ppid = int(parts[1])
        except (ValueError, IndexError):
            return None
        return (state, ppid)
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        try:
            completed = subprocess.run(
                ["ps", "-o", "state=,ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if completed.returncode != 0:
            return None
        parts = completed.stdout.strip().split()
        if len(parts) < 1:
            return None
        state = parts[0]
        try:
            ppid = int(parts[1]) if len(parts) > 1 else None
        except ValueError:
            ppid = None
        return (state, ppid)
    return None


def _is_excluded_member(pid: int) -> bool:
    """Hard guard 4: zombies and kernel threads are NEVER members.

    A zombie cannot be killed (it is already dead, awaiting reap) and
    must not satisfy the D9 start-time gate; kernel threads (Linux
    ppid 0/2 — kthreadd lineage) are not userspace processes and can
    never belong to a bash spawn group. Heuristic filter, fail-safe
    direction: excluding a real member only tightens the gate.
    """
    read = _proc_state_and_ppid(pid)
    if read is None:
        return False  # unreadable ⇒ not provably excluded; caller decides
    state, ppid = read
    if state and state.startswith("Z"):
        return True
    if sys.platform.startswith("linux") and ppid in (0, 2):
        return True
    return False


def list_group_members(pgid: int) -> Optional[list[int]]:
    """Return live pids whose process group == ``pgid``.

    Linux: scan /proc/*/stat field 5 (pgrp). macOS/BSD: ``ps -axo pid=,pgid=,state=``.
    Zombies and Linux kernel threads are EXCLUDED (hard guard 4 —
    they must never satisfy the D9 start-time gate).

    Returns ``None`` when enumeration itself FAILED (ps broken /
    /proc unreadable) — callers must distinguish "no members" (the
    group is dead) from "could not enumerate" (fail-closed: never
    GC-close a row on a blind read).
    """
    if pgid is None or pgid <= 0:
        return []
    if sys.platform.startswith("linux"):
        out: list[int] = []
        try:
            for name in os.listdir("/proc"):
                if not name.isdigit():
                    continue
                pid = int(name)
                try:
                    with open(f"/proc/{pid}/stat", "r") as h:
                        line = h.readline()
                except (OSError, FileNotFoundError, PermissionError):
                    continue
                last_paren = line.rfind(")")
                if last_paren == -1:
                    continue
                _, _, tail = line.rpartition(")")
                parts = tail.strip().split()
                # state, ppid, pgrp → parts[0], [1], [2]
                try:
                    state = parts[0]
                    ppid = int(parts[1])
                    pgrp = int(parts[2])
                except (ValueError, IndexError):
                    continue
                if pgrp != pgid:
                    continue
                if state.startswith("Z"):
                    continue  # zombie: not a killable member
                if ppid in (0, 2):
                    continue  # Linux kernel-thread lineage
                out.append(pid)
        except OSError:
            return None  # enumeration failed — not an empty group
        return out
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        try:
            completed = subprocess.run(
                ["ps", "-axo", "pid=,pgid=,state="],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None  # enumeration failed — not an empty group
        if completed.returncode != 0:
            return None
        out: list[int] = []
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
                pg = int(parts[1])
            except ValueError:
                continue
            if pg != pgid:
                continue
            state = parts[2] if len(parts) > 2 else ""
            if state.startswith("Z"):
                continue  # zombie: not a killable member
            out.append(pid)
        return out
    return None


# ── Env read-backends (ported from daemon/tools/proc_tools.py) ────────────────
def verify_member_env(pid: int, tracking_id: str) -> Optional[bool]:
    """Verify that ``pid``'s env carries ``BASH_TRACKING_ENV_VAR=tracking_id``.

    Linux: /proc/<pid>/environ exact-entry match (fail-closed on readable
    mismatch; fail-open on unreadable/missing).
    macOS/BSD: ``ps eww`` positive-only (unreadable ⇒ None fail-open).
    Else: None (fail-open, with WARNING).

    Returns True/False/None per the proc_tools contract.
    """
    if pid is None or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _verify_member_env_procfs(pid, tracking_id)
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        return _verify_member_env_ps(pid, tracking_id)
    logger.warning(
        "PID env verification not supported on platform %s; proceeding "
        "(fail-open).",
        sys.platform,
    )
    return None


def _verify_member_env_procfs(pid: int, tracking_id: str) -> Optional[bool]:
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        logger.warning(
            "env verify: /proc/%d/environ not found (process may have "
            "exited). Proceeding (fail-open).",
            pid,
        )
        return None
    except PermissionError:
        logger.warning(
            "env verify: /proc/%d/environ not readable (EPERM). "
            "Proceeding (fail-open).",
            pid,
        )
        return None
    except OSError as exc:
        logger.warning(
            "env verify: /proc/%d/environ read failed (%s). Proceeding "
            "(fail-open).",
            pid,
            exc,
        )
        return None

    entries = data.split(b"\x00")
    needle = f"{BASH_TRACKING_ENV_VAR}={tracking_id}".encode("utf-8")
    if needle in entries:
        return True
    marker_prefix = (BASH_TRACKING_ENV_VAR + "=").encode("utf-8")
    marker_entries = [e for e in entries if e.startswith(marker_prefix)]
    if marker_entries:
        logger.warning(
            "env verify: marker %s present at PID %d but value differs "
            "(PID likely recycled). Aborting.",
            BASH_TRACKING_ENV_VAR,
            pid,
        )
        return False
    logger.warning(
        "env verify: %s not found in env of PID %d (PID may have been "
        "recycled). Aborting.",
        BASH_TRACKING_ENV_VAR,
        pid,
    )
    return False


def _verify_member_env_ps(pid: int, tracking_id: str) -> Optional[bool]:
    if pid <= 0:
        return None
    needle = f"{BASH_TRACKING_ENV_VAR}={tracking_id}"
    try:
        completed = subprocess.run(
            ["ps", "eww", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "env verify via ps failed for PID %d (%s). Proceeding "
            "(fail-open).",
            pid,
            exc,
        )
        return None

    if completed.returncode != 0:
        logger.warning(
            "env verify: ps exit=%d for PID %d (process likely gone). "
            "Proceeding (fail-open).",
            completed.returncode,
            pid,
        )
        return None

    output = completed.stdout

    if needle in output:
        return True

    # Detect the macOS "env vars not shown" case.
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
            if cmd and cmd in output:
                trailing = output.split(cmd, 1)[1].strip()
                if not trailing:
                    logger.warning(
                        "env verify: ps eww did not surface env vars for "
                        "PID %d (macOS limitation). Proceeding (fail-open).",
                        pid,
                    )
                    return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    logger.warning(
        "env verify: %s not found in ps output for PID %d (PID may have "
        "been recycled). Aborting.",
        BASH_TRACKING_ENV_VAR,
        pid,
    )
    return False


# ── Helpers ──────────────────────────────────────────────────────────────────
def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_get(row: Any, attr: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(attr, default)
    try:
        return getattr(row, attr, default)
    except Exception:
        return default


# ── verify_and_kill: full predicate + attribution + kill ladder (P2) ─────────
def _clamped_grace(value: Optional[float]) -> float:
    """Clamp a TERM→KILL grace to [MIN, MAX] (env-parse convention)."""
    if value is None:
        return term_grace_seconds_from_env()
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TERM_GRACE_SECONDS
    return max(MIN_TERM_GRACE_SECONDS, min(MAX_TERM_GRACE_SECONDS, value))


def _kill_ladder(pgid: int, grace_seconds: float) -> tuple[str, str]:
    """TERM the group → bounded grace → KILL the group (plan rule 5).

    POSIX-gated (``sys.platform != "win32"`` — constraint: every new
    kill leg). ESRCH (``ProcessLookupError``) is swallowed at every
    site (kill-site exemption precedent,
    ``tests/integration/test_service_tool_kill_site_exemption.py``).
    A TERM that lands ESRCH skips the grace + KILL legs entirely —
    the group died between verification and signaling.

    Returns ``(status, reason)`` with status ``"reaped"``.
    """
    if sys.platform == "win32":
        return ("refused", "refused:platform-win32")
    term_esrch = False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        term_esrch = True
    except OSError as exc:
        logger.warning(
            "bash reaper: killpg TERM failed for pgid %s: %s: %s",
            pgid, type(exc).__name__, exc,
        )
    if term_esrch:
        # Group died between verification and the TERM — nothing left
        # to signal; skip the grace + KILL legs.
        return ("reaped", "reaped:term-esrch")
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    # Zombie-aware KILL skip: when the group's only remaining members
    # are unreaped ZOMBIES, a SIGKILL against the group returns EPERM
    # on BSD/macOS (macOS refuses SIGKILL to a zombie; killpg aborts
    # on the first non-signalable member). The group is already dead —
    # skip the KILL leg. Enumeration-available platforms only; an
    # empty enumeration elsewhere falls through to the KILL (ESRCH is
    # swallowed either way).
    if sys.platform.startswith("linux") or sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        try:
            remaining = list_group_members(pgid)
        except Exception:
            remaining = None
        if remaining is not None and not remaining:
            return ("reaped", "reaped:group-dead-after-grace")
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # ESRCH: group exited during the grace window
    except OSError as exc:
        logger.warning(
            "bash reaper: killpg KILL failed for pgid %s: %s: %s",
            pgid, type(exc).__name__, exc,
        )
    return ("reaped", "reaped:ladder")


def verify_and_kill(
    row: Any,
    *,
    members: Optional[list] = None,
    my_pid: Optional[int] = None,
    tolerance_seconds: Optional[float] = None,
    term_grace_seconds: Optional[float] = None,
    reaped_by: Optional[str] = None,
) -> tuple[str, str]:
    """Verify a durable spawn row and, when safe, reap its process group.

    Returns ``(status, reason)`` where status ∈ {``"refused"``,
    ``"skip"``, ``"reaped"``, ``"proceed"``}:

    * ``("refused", "refused:<guard>")`` — a hard guard, the D9
      start-time gate, or a readable-env mismatch vetoed the kill.
      The row is CLOSED by the caller (``reap_reason=<reason>``) —
      an ambiguous row is never retried (task 3.5 convention).
    * ``("skip", "skip:...")`` — D8 live-sibling guard: a live
      foreign daemon owns this row (its bash children are LIVE, not
      leaked). The row stays OPEN.
    * ``("reaped", "reaped:<how>")`` — the ladder executed (TERM →
      grace → KILL; ESRCH swallowed).
    * ``("proceed", "proceed:group-dead-gc")`` — the group is already
      dead; no signal. The caller GC-closes the row
      (``reaped_by='gc@<pid>'``, ``reap_reason='gc'``).

    Verification predicate (plan-overview §Verification, rules 1-5):

      1. Hard guards: ``pgid > 1``; ``pgid != os.getpgid(os.getpid())``;
         ``pid != self``.
      2. D8 daemon_pid live-sibling guard — skip when a live process
         OTHER than this daemon holds ``row.daemon_pid`` with a
         process-start-time ≤ ``row.created_at``; a holder started
         after the row is a recycled pid → proceed; **self rows
         (``daemon_pid == os.getpid()``) ALWAYS proceed** (MAJOR-1).
      3. Group reachability: ``os.kill(row.pid, 0)`` (direct child;
         on Linux additionally its jiffies must equal
         ``row.start_time`` — PID-recycle defense) OR a member scan
         confirms ≥1 live process with ``pgid == row.pgid`` AND the
         D9 HARD start-time gate: ≥1 member started at or before
         ``row.created_at + tolerance`` (default 10 s — a recycled
         group's members cannot). Fail-closed on unparseable starts.
      4. Attribution: env-stamp exact match on ≥1 member — readable-
         but-mismatched env VETOES (refuse) regardless of the gate;
         env unreadable ⇒ pgid-only attribution IS permitted (F1
         unified rule, any boot scope) but ONLY in combination with
         the D9 gate of rule 3 (two-signal; logged
         ``attribution=pgid-only``).
      5. Kill ladder: SIGTERM group → bounded grace → SIGKILL group.

    Members may be passed in (``(pid, start_dt)`` tuples / dicts) to
    keep the predicate hermetic; otherwise the group is enumerated
    fresh via :func:`list_group_members` + :func:`process_start_wallclock`.
    """
    if my_pid is None:
        my_pid = os.getpid()
    if tolerance_seconds is None:
        tolerance_seconds = start_tolerance_seconds_from_env()

    pgid = _row_get(row, "pgid")
    pid = _row_get(row, "pid")
    daemon_pid = _row_get(row, "daemon_pid")
    created_at = _row_get(row, "created_at")
    tracking_id = _row_get(row, "tracking_id")

    # 1. Hard guards.
    if pgid is None:
        return ("refused", "refused:pgid-missing")
    try:
        pgid_i = int(pgid)
    except (TypeError, ValueError):
        return ("refused", "refused:pgid-invalid")
    if pgid_i <= 1:
        return ("refused", "refused:pgid<=1")
    try:
        daemon_group = os.getpgid(os.getpid())
    except OSError:
        daemon_group = None
    if daemon_group is not None and pgid_i == daemon_group:
        return ("refused", "refused:daemon-group")
    if pid is None:
        return ("refused", "refused:pid-missing")
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return ("refused", "refused:pid-invalid")
    if pid_i == os.getpid():
        # self-pid guard: never target self
        return ("refused", "refused:self-pid")

    # POSIX gating (constraint: every new kill leg is
    # ``sys.platform != "win32"``-gated). Windows keeps today's
    # taskkill fallback; the reaper never ladders there — the row is
    # closed with this refusal so it is never retried.
    if sys.platform == "win32":
        return ("refused", "refused:platform-win32")

    # 2. D8 daemon_pid live-sibling guard (self rows always proceed).
    skip, reason = _daemon_pid_guard(daemon_pid, my_pid, created_at)
    if skip:
        return ("skip", reason)

    row_created = _parse_dt(created_at)

    # Direct child liveness.
    direct_alive = False
    direct_unprobeable = False
    try:
        os.kill(pid_i, 0)
        direct_alive = True
    except ProcessLookupError:
        direct_alive = False
    except PermissionError:
        # EPERM ⇒ the pid exists (we lack permission to signal it).
        direct_alive = True
    except Exception:
        # Unprobeable ⇒ fail-closed (refuse), never "not alive".
        direct_unprobeable = True

    jiffies_ok = True
    if sys.platform.startswith("linux"):
        st = _row_get(row, "start_time")
        if st is not None:
            actual = _read_proc_start_time(pid_i)
            if actual is None or actual != st:
                jiffies_ok = False

    # Unprobeable direct-child probe ⇒ refuse (fail-closed).
    if direct_unprobeable:
        return ("refused", "refused:direct-child-unprobeable")

    # Member list (provided or enumerated).
    if members is None:
        try:
            raw_members = list_group_members(pgid_i)
        except Exception:
            raw_members = None
        if raw_members is None:
            # Enumeration failed (ps broken / /proc unreadable):
            # fail-closed — never treat a blind read as "group dead",
            # never kill unverified. Leave the row OPEN (transient;
            # the next cleanup point retries).
            logger.warning(
                "bash reaper: group enumeration unavailable for pgid "
                "%s; skipping this row (row left open)",
                pgid_i,
            )
            return ("skip", "skip:enum-unavailable")
        members = [(p, process_start_wallclock(p)) for p in raw_members]

    member_ok = False
    unparseable = False
    gate_failed_direct = False
    had_member = False
    member_pids: list[int] = []
    for entry in members or []:
        # entry may be (pid, start) or a dict
        if isinstance(entry, (tuple, list)):
            mpid, mstart = (entry[0], entry[1]) if len(entry) >= 2 else (entry[0], None)
        elif isinstance(entry, dict):
            mpid = entry.get("pid")
            mstart = entry.get("start")
            if mstart is None and "start_time" in entry:
                mstart = entry.get("start_time")
        else:
            mpid = _row_get(entry, "pid")
            mstart = _row_get(entry, "start")
            if mstart is None:
                mstart = _row_get(entry, "start_time")
        if mpid is None:
            continue
        try:
            mpid_i = int(mpid)
        except (TypeError, ValueError):
            continue
        if mpid_i == os.getpid():
            continue
        if _is_excluded_member(mpid_i):
            continue  # zombies / kernel threads are never members (guard 4)
        had_member = True
        member_pids.append(mpid_i)
        mstart_dt = mstart if isinstance(mstart, datetime) else _parse_dt(mstart)
        if mstart is None:
            unparseable = True
            continue
        if mstart_dt is None:
            unparseable = True
            continue
        if row_created is not None and mstart_dt <= row_created + timedelta(
            seconds=float(tolerance_seconds)
        ):
            member_ok = True

    # Direct-child path: the D9 HARD start-time gate applies to the
    # direct child too — on Linux the jiffies exact-match is the
    # PID-recycle defense; on darwin/BSD the absolute wall-clock start
    # (``ps -o lstart=``, P3 leg; fail-closed on unparseable/None)
    # plays the same role: a genuine direct child spawned BEFORE the
    # row write auto-satisfies it; a recycled pid's group (started
    # after the row) CANNOT.
    direct_ok = False
    if direct_alive:
        if sys.platform.startswith("linux") and _row_get(row, "start_time") is not None:
            if jiffies_ok:
                direct_ok = True
            else:
                gate_failed_direct = True
        else:
            wc = process_start_wallclock(pid_i)
            if wc is None or row_created is None:
                unparseable = True  # fail-closed (P3)
            elif wc <= row_created + timedelta(seconds=float(tolerance_seconds)):
                direct_ok = True
            else:
                gate_failed_direct = True

    # Group already dead (no live members, direct child gone) ⇒ gc path.
    if not direct_alive and not had_member:
        return ("proceed", "proceed:group-dead-gc")

    # 3/4. Attribution (plan rule 3/F1): env-stamp exact match on ≥1
    # candidate; readable-but-mismatched env VETOES regardless of the
    # start-time gate; unreadable env everywhere ⇒ pgid-only permitted
    # (two-signal: the caller only reaches here when the D9 gate of
    # rule 2/3 held or is about to be enforced below).
    candidates: list[int] = list(member_pids)
    if direct_alive and pid_i not in candidates and not _is_excluded_member(pid_i):
        # zombies/kthreads are never attribution candidates either —
        # an unreaped (zombie) direct child has no readable env and
        # counts as unreadable (pgid-only), not as a mismatch.
        candidates.append(pid_i)
    matched = False
    mismatch = False
    if tracking_id:
        for cpid in candidates:
            try:
                verdict = verify_member_env(cpid, str(tracking_id))
            except Exception:
                verdict = None
            if verdict is True:
                matched = True
                break
            if verdict is False:
                mismatch = True
    if matched:
        attribution = "env-match"
    elif mismatch:
        # readable env disagrees on every candidate: fail-closed veto
        # regardless of the start-time gate (PID-recycle defense).
        return ("refused", "refused:env-mismatch")
    else:
        attribution = "pgid-only"

    # Reachability must hold (rule 2/3) — with pgid-only attribution it
    # is the SECOND signal that makes the kill two-signal.
    if direct_ok or member_ok:
        grace = _clamped_grace(term_grace_seconds)
        if attribution == "pgid-only":
            logger.info(
                "bash reaper: killpg pgid=%s attribution=pgid-only "
                "(env unreadable; two-signal via D9 start-time gate) "
                "row=%s reaped_by=%s",
                pgid_i, _row_get(row, "id"), reaped_by or "unspecified",
            )
        status, ladder_reason = _kill_ladder(pgid_i, grace)
        if status == "reaped" and ladder_reason == "reaped:ladder":
            # carry the attribution in the outcome reason (diagnostic)
            return ("reaped", f"reaped:{attribution}")
        return (status, ladder_reason)
    if unparseable:
        return ("refused", "refused:start-time-unparseable")
    return ("refused", "refused:start-time-gate")


def _daemon_pid_guard(daemon_pid: Any, my_pid: int, created_at: Any) -> tuple[bool, str]:
    """D8 live-sibling guard.

    Returns (skip, reason). skip=True means a live sibling daemon holds
    daemon_pid (started at/before the row) → skip this row. Self rows
    (daemon_pid == my_pid == os.getpid()) always proceed.
    """
    if daemon_pid is None:
        return (False, "")
    try:
        dp = int(daemon_pid)
    except (TypeError, ValueError):
        return (False, "")
    if dp == os.getpid() or dp == my_pid:
        # Self rows always proceed (MAJOR-1).
        return (False, "")
    # Is a live process holding dp?
    try:
        os.kill(dp, 0)
    except ProcessLookupError:
        # Holder dead → writer presumed dead → proceed.
        return (False, "")
    except PermissionError:
        # EPERM ⇒ the pid exists → live sibling candidate.
        pass
    except Exception:
        # Unprobeable ⇒ conservative skip (fail-closed: never kill
        # unverified).
        return (True, "skip:live-sibling-daemon (unprobeable holder probe)")
    # Holder alive and not us → live sibling candidate; apply start-time gate.
    holder_start = process_start_wallclock(dp)
    row_created = _parse_dt(created_at)
    if holder_start is None:
        # Cannot determine holder start → conservative skip.
        return (True, "skip:live-sibling-daemon (unparseable holder start)")
    if row_created is not None and holder_start <= row_created:
        return (True, "skip:live-sibling-daemon")
    # Holder started after the row → recycled pid → writer dead → proceed.
    return (False, "")


# ════════════════════════════════════════════════════════════════════════════
# Phase 2 sweeps (tasks 2.2 / 2.4): shared per-row executor + the two
# event-driven lanes. The one-shot boot reconcile (reconcile_stale_bash_spawns)
# is Phase 3. EVERY lane: master switch OFF ⇒ complete no-op BEFORE any
# repository access (m3 — disabled sweeps never consult the repository).
# ════════════════════════════════════════════════════════════════════════════

GC_REAPED_BY = "gc"
RAM_DEDUP_REASON = "ram-tier-dedup"


async def _mark_reaped(repo: Any, row_id: Any, reaped_by: str, reason: Optional[str]) -> None:
    """Bridge the sync ``mark_reaped`` into the event loop (best-effort)."""
    await asyncio.to_thread(repo.mark_reaped, row_id, reaped_by, reason)


async def sweep_rows(
    rows: list,
    *,
    repo: Any,
    reaped_by: str,
    ram_pgids: Optional[set] = None,
    my_pid: Optional[int] = None,
    term_grace_seconds: Optional[float] = None,
) -> int:
    """Shared per-row sweep executor: verify-and-kill + row bookkeeping.

    Per-row outcomes (plan task 2.2):

    * ``skip`` (D8 live sibling) — row LEFT OPEN; logged with both the
      writer daemon pid and the sweeping daemon pid (adjudication 1).
    * ``refused`` — row CLOSED with ``reap_reason=<refused:...>``
      (an ambiguous row is never retried; task 3.5 convention).
    * ``proceed:group-dead-gc`` — row GC-closed
      (``reaped_by='gc@<pid>'``, ``reap_reason='gc'``, idempotent).
    * ``reaped`` — row closed ``reaped_by='<lane>@<pid>'``.
    * pgid already killed by this sweep's RAM tier (``ram_pgids``) —
      NO second signal (dedup'd by pgid: the group was just SIGKILLed
      synchronously one statement earlier; a redundant killpg could
      hit a recycled pgid). Row closed with
      ``reap_reason='ram-tier-dedup'``.

    Returns the number of rows closed (refused + gc + killed + dedup).
    """
    if my_pid is None:
        my_pid = os.getpid()
    actions = 0
    for row in rows:
        row_id = _row_get(row, "id")
        try:
            row_pgid = _row_get(row, "pgid")
            row_pgid_i = int(row_pgid) if row_pgid is not None else None
        except (TypeError, ValueError):
            row_pgid_i = None

        if ram_pgids and row_pgid_i is not None and row_pgid_i in ram_pgids:
            # The RAM tier of THIS sweep just SIGKILLed this group —
            # close the tombstone without a redundant signal.
            logger.info(
                "bash reaper: row %s (pgid=%s) dedup'd against the RAM "
                "killpg tier; closing (writer daemon_pid=%s swept by %s)",
                row_id, row_pgid_i, _row_get(row, "daemon_pid"), my_pid,
            )
            if repo is not None and row_id is not None:
                try:
                    await _mark_reaped(repo, row_id, f"{reaped_by}@{my_pid}", RAM_DEDUP_REASON)
                    try:
                        row.reaped_at = now_utc_iso() if hasattr(row, "reaped_at") else row.reaped_at
                    except Exception:
                        pass
                except Exception as exc:
                    logger.warning(
                        "bash reaper: mark_reaped failed for row %s: %s: %s",
                        row_id, type(exc).__name__, exc,
                    )
            actions += 1
            continue

        try:
            status, reason = await asyncio.to_thread(
                verify_and_kill,
                row,
                my_pid=my_pid,
                term_grace_seconds=term_grace_seconds,
                reaped_by=reaped_by,
            )
        except Exception as exc:
            logger.warning(
                "bash reaper: verify_and_kill failed for row %s: %s: %s",
                row_id, type(exc).__name__, exc,
            )
            continue

        if status == "skip":
            logger.info(
                "bash reaper: skipped row %s (instance=%.8s writer "
                "daemon_pid=%s) — %s (swept by daemon pid %s)",
                row_id, str(_row_get(row, "instance_id") or ""),
                _row_get(row, "daemon_pid"), reason, my_pid,
            )
            continue  # live sibling owns this row — leave OPEN

        if repo is None or row_id is None:
            actions += 1
            continue

        try:
            if status == "refused":
                logger.warning(
                    "bash reaper: refused row %s (writer daemon_pid=%s) — "
                    "%s (swept by daemon pid %s)",
                    row_id, _row_get(row, "daemon_pid"), reason, my_pid,
                )
                await _mark_reaped(repo, row_id, f"{reaped_by}@{my_pid}", reason)
                try:
                    row.reaped_at = now_utc_iso()
                except Exception:
                    pass
                actions += 1
            elif status == "proceed" and str(reason).startswith("proceed:group-dead-gc"):
                await _mark_reaped(repo, row_id, f"{GC_REAPED_BY}@{my_pid}", "gc")
                try:
                    row.reaped_at = now_utc_iso()
                except Exception:
                    pass
                actions += 1
            elif status == "reaped":
                await _mark_reaped(repo, row_id, f"{reaped_by}@{my_pid}", None)
                try:
                    row.reaped_at = now_utc_iso()
                except Exception:
                    pass
                actions += 1
            else:
                logger.warning(
                    "bash reaper: unexpected verify_and_kill status %r for "
                    "row %s (%s); row left untouched",
                    status, row_id, reason,
                )
        except Exception as exc:
            logger.warning(
                "bash reaper: mark_reaped failed for row %s: %s: %s",
                row_id, type(exc).__name__, exc,
            )
    return actions


async def sweep_instance(
    repo: Any,
    instance_id: str,
    *,
    reaped_by: str = "terminal-tier",
    ram_pgids: Optional[set] = None,
    my_pid: Optional[int] = None,
    term_grace_seconds: Optional[float] = None,
) -> int:
    """Consult open durable rows for ONE instance and reap them (task 2.2).

    Master switch OFF ⇒ complete no-op with ZERO repository access
    (m3). Driven by ``BashProcessRegistry.cleanup_instance`` (terminal
    tiers / terminate / chart inherit the reach with zero call-site
    changes) and ``cleanup_all`` (the shutdown lane passes
    ``reaped_by='shutdown'``).
    """
    if not kill_switch_from_env():
        return 0
    if repo is None:
        return 0
    rows = await asyncio.to_thread(repo.list_open_by_instance, instance_id)
    if not rows:
        return 0
    closed = await sweep_rows(
        rows,
        repo=repo,
        reaped_by=reaped_by,
        ram_pgids=ram_pgids,
        my_pid=my_pid,
        term_grace_seconds=term_grace_seconds,
    )
    # Phase 4 enumeration: reach setsid/job-control escapees whose env
    # carries one of our open rows' tracking ids. Event-driven, same
    # sweep site (no new kill site).
    closed += await _enumerated_sweep(
        rows, repo=repo, reaped_by=reaped_by,
        my_pid=my_pid, term_grace_seconds=term_grace_seconds,
    )
    return closed


async def sweep_open_rows_for_shutdown(
    repo: Any,
    *,
    my_pid: Optional[int] = None,
    term_grace_seconds: Optional[float] = None,
) -> int:
    """Shutdown lane (plan task 2.4): sweep ALL open durable rows whose
    writer daemon is dead OR is us.

    The D8 live-sibling guard + self-exemption apply exactly as in
    every other lane: a live FOREIGN daemon holding ``row.daemon_pid``
    (holder start ≤ ``row.created_at``) ⇒ the row is skipped — its
    bash children are LIVE, not leaked; self rows
    (``daemon_pid == os.getpid()``) always proceed (MAJOR-1); a holder
    that started after the row is a recycled pid ⇒ proceed.

    Master switch OFF ⇒ returns 0 with ZERO repository access (m3).
    Returns the number of rows closed.
    """
    if not kill_switch_from_env():
        logger.info(
            "bash reaper: master switch OFF — shutdown durable sweep "
            "is a no-op (no repository access)"
        )
        return 0
    if repo is None:
        return 0
    rows = await asyncio.to_thread(repo.list_open_all)
    if not rows:
        return 0
    closed = await sweep_rows(
        rows,
        repo=repo,
        reaped_by="shutdown",
        ram_pgids=None,
        my_pid=my_pid,
        term_grace_seconds=term_grace_seconds,
    )
    closed += await _enumerated_sweep(
        rows, repo=repo, reaped_by="shutdown",
        my_pid=my_pid, term_grace_seconds=term_grace_seconds,
    )
    if closed:
        logger.info(
            "bash reaper: shutdown durable sweep closed %d row(s) "
            "(sweeping daemon pid %s)",
            closed, my_pid if my_pid is not None else os.getpid(),
        )
    return closed


async def reconcile_stale_bash_spawns(
    repo: Any = None,
    *,
    current_boot_id: Optional[str] = None,
    my_pid: Optional[int] = None,
    term_grace_seconds: Optional[float] = None,
    tolerance_seconds: Optional[float] = None,
) -> int:
    """One-shot boot reconcile (plan task 3.1 / D6).

    Lists open rows whose ``boot_id`` is NULL/empty/foreign (i.e. from a
    previous daemon boot — D3 adjudication 2), applies the D8 daemon_pid
    live-sibling guard + D9 start-time gate inside :func:`verify_and_kill`,
    reaps the survivors with ``reaped_by='boot-reconcile@<pid>'``, and
    GC-closes rows whose group is already dead (``reap_reason='gc'``).

    Master switch OFF or the boot leg knob OFF ⇒ complete no-op with
    ZERO repository access (m3) — the switches are checked BEFORE any
    repository touch. Non-fatal: callers wrap in try/except (the
    ``recover_stale_job_locks`` shape in ``daemon/api.py``).
    """
    if not kill_switch_from_env():
        logger.info(
            "bash reaper: master switch OFF — boot reconcile is a no-op "
            "(no repository access)"
        )
        return 0
    if not boot_sweep_leg_from_env():
        logger.info(
            "bash reaper: boot-sweep leg OFF — boot reconcile is a no-op "
            "(no repository access)"
        )
        return 0

    if repo is None:
        try:
            from daemon.tools.bash import get_bash_process_registry

            reg = get_bash_process_registry()
            repo = getattr(reg, "durable_writer", None) or getattr(reg, "get_durable_writer", None)
            if callable(repo) and not hasattr(repo, "list_open_previous_boot"):
                repo = repo()
        except Exception:  # noqa: BLE001
            repo = None
    if repo is None:
        return 0

    if current_boot_id is None:
        current_boot_id = CURRENT_BOOT_ID
    if my_pid is None:
        my_pid = os.getpid()
    if tolerance_seconds is None:
        tolerance_seconds = start_tolerance_seconds_from_env()

    try:
        rows = await asyncio.to_thread(
            repo.list_open_previous_boot, current_boot_id, my_pid
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "bash reaper: boot reconcile list_open_previous_boot failed: "
            "%s: %s", type(exc).__name__, exc,
        )
        raise
    if not rows:
        return 0

    closed = await sweep_rows(
        rows,
        repo=repo,
        reaped_by="boot-reconcile",
        ram_pgids=None,
        my_pid=my_pid,
        term_grace_seconds=term_grace_seconds,
    )
    closed += await _enumerated_sweep(
        rows, repo=repo, reaped_by="boot-reconcile",
        my_pid=my_pid, term_grace_seconds=term_grace_seconds,
    )
    if closed:
        logger.info(
            "bash reaper: boot reconcile closed %d row(s) "
            "(sweeping daemon pid %s)",
            closed, my_pid,
        )
    return closed


# ════════════════════════════════════════════════════════════════════════════
# Phase 4 — Enumerated stamp sweep (Paths B/C/D + timeout-race survivors).
# One fresh process-table scan per sweep; candidate pre-filter BEFORE any env
# read; per-candidate attribution via the proc_tools backends; TERM→grace→KILL
# per attributed pid. Rides the master switch (no separate enumeration switch).
# ════════════════════════════════════════════════════════════════════════════

def _parse_etime(value: Any) -> Optional[int]:
    """Parse a ``ps`` etime token (``[[dd-]hh:]mm:ss``) into seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        dpart, text = text.split("-", 1)
        try:
            days = int(dpart)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = parts
            return days * 86400 + int(h) * 3600 + int(m) * 60 + int(sec)
        if len(parts) == 2:
            m, sec = parts
            return days * 86400 + int(m) * 60 + int(sec)
        if len(parts) == 1:
            return days * 86400 + int(parts[0])
    except ValueError:
        return None
    return None


def _scan_process_table() -> list:
    """ONE fresh full-process-table scan (POSIX only).

    Returns a list of dicts: ``{pid, ppid, pgid, etime, start, command}``.
    Win32 / scan failure ⇒ ``[]`` (fail-open-to-skip with WARNING). The
    scan command matches the plan's ``ps -axo pid,ppid,pgid,etime,command``
    so callers can monkeypatch ``reaper.subprocess.run`` (or this helper)
    and assert a single scan per sweep.
    """
    if sys.platform == "win32":
        return []
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid,ppid,pgid,etime,command"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "bash reaper: process-table scan failed (%s: %s); "
            "skipping enumeration",
            type(exc).__name__, exc,
        )
        return []
    if completed.returncode != 0:
        logger.warning(
            "bash reaper: process-table scan exited %d; skipping enumeration",
            completed.returncode,
        )
        return []
    now = datetime.now(timezone.utc)
    entries: list = []
    lines = (completed.stdout or "").splitlines()
    # Skip the header line (if present).
    for line in lines[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pgid = int(parts[2])
        except ValueError:
            continue
        etime_s = _parse_etime(parts[3])
        if etime_s is None:
            logger.warning(
                "bash reaper: unparseable etime %r for pid %d; skipping "
                "(fail-open-to-skip)",
                parts[3], pid,
            )
            continue
        entries.append({
            "pid": pid,
            "ppid": ppid,
            "pgid": pgid,
            "etime": etime_s,
            "start": now - timedelta(seconds=etime_s),
            "command": parts[4],
        })
    return entries


def _enumerate_stamped_member_details(open_rows: Any) -> list:
    """Enumerate process-table members carrying one of ``open_rows``'
    tracking ids.

    Returns a list of ``(pid, tracking_id)`` tuples selected for killing
    (exact env match + D9 start-time gate). Refused/skipped candidates
    are logged (WARNING) and excluded. Master switch OFF ⇒ ``[]`` with
    zero scan. Win32 ⇒ ``[]``.

    Candidate pre-filter (m6) runs BEFORE any env read: self/daemon/
    kernel-thread/zombie exclusion, pgid>1, daemon-group exclusion, D8
    sibling-guarded pgids, and a D9 eligibility pre-filter (a candidate
    too young for EVERY open row cannot belong to any of them).
    """
    if not kill_switch_from_env():
        return []
    if sys.platform == "win32":
        return []
    rows = list(open_rows or [])
    if not rows:
        return []

    # Tracking ids from open rows.
    tids: list = []
    for r in rows:
        tid = _row_get(r, "tracking_id")
        if tid is not None and str(tid) not in tids:
            tids.append(str(tid))
    if not tids:
        return []

    row_by_tid: dict = {}
    for r in rows:
        tid = _row_get(r, "tracking_id")
        if tid is not None:
            row_by_tid.setdefault(str(tid), r)

    # D8 sibling-guarded pgids + daemon pids (rows skipped by the guard).
    skipped_pgids: set = set()
    skipped_daemon_pids: set = set()
    for r in rows:
        skip, _ = _daemon_pid_guard(
            _row_get(r, "daemon_pid"), os.getpid(), _row_get(r, "created_at")
        )
        if skip:
            try:
                skipped_pgids.add(int(_row_get(r, "pgid")))
            except (TypeError, ValueError):
                pass
            try:
                skipped_daemon_pids.add(int(_row_get(r, "daemon_pid")))
            except (TypeError, ValueError):
                pass

    created_dts = [
        d for d in (_parse_dt(_row_get(r, "created_at")) for r in rows) if d is not None
    ]
    max_created = max(created_dts) if created_dts else None
    tolerance = start_tolerance_seconds_from_env()

    daemon_pgid = None
    try:
        daemon_pgid = os.getpgid(os.getpid())
    except OSError:
        daemon_pgid = None

    scan = _scan_process_table()

    # Pre-filter BEFORE any env read.
    surviving: list = []
    for cand in scan:
        pid = cand["pid"]
        pgid = cand["pgid"]
        ppid = cand["ppid"]
        if pid <= 0 or pid == os.getpid():
            continue
        if pgid is None or pgid <= 1:
            continue
        if daemon_pgid is not None and pgid == daemon_pgid:
            continue
        if sys.platform.startswith("linux") and ppid in (0, 2):
            continue
        if "defunct" in (cand.get("command") or "").lower():
            continue
        if pgid in skipped_pgids:
            continue
        if pid in skipped_daemon_pids:
            continue
        # D9 eligibility pre-filter: candidate must be able to satisfy
        # the gate for at least one open row.
        if max_created is not None and cand.get("start") is not None:
            if cand["start"] > max_created + timedelta(seconds=tolerance):
                continue
        surviving.append(cand)

    # Attribution.
    results: list = []
    for cand in surviving:
        pid = cand["pid"]
        matched_tid = None
        any_none = False
        readable_mismatch = False
        for tid in tids:
            try:
                v = verify_member_env(pid, tid)
            except Exception:  # noqa: BLE001
                v = None
            if v is True:
                matched_tid = tid
                break
            if v is None:
                any_none = True
            else:
                readable_mismatch = True
        if matched_tid is None:
            if any_none:
                logger.warning(
                    "bash reaper: enumeration skipped pid=%d (env unreadable) "
                    "— never kill-unverified",
                    pid,
                )
                continue
            logger.warning(
                "bash reaper: enumeration refused pid=%d (env mismatch)", pid,
            )
            continue
        # D9 gate on the matched row.
        mrow = row_by_tid.get(matched_tid)
        mcreated = _parse_dt(_row_get(mrow, "created_at")) if mrow is not None else None
        if mcreated is None:
            # Fail-closed (mirrors the row-level verify_and_kill
            # refusal ``refused:start-time-unparseable``): a row whose
            # created_at is unparseable/missing must not have its
            # candidates selected on env-match alone. Reachable only
            # via a corrupted row (the writer always stamps
            # now_utc_iso).
            logger.warning(
                "bash reaper: enumeration refused pid=%d (row %s "
                "created_at unparseable) — fail-closed",
                pid, _row_get(mrow, "id") if mrow is not None else None,
            )
            continue
        if cand.get("start") is not None:
            if cand["start"] > mcreated + timedelta(seconds=tolerance):
                logger.warning(
                    "bash reaper: enumeration refused pid=%d (start-time gate)",
                    pid,
                )
                continue
        results.append((pid, matched_tid))
        logger.info(
            "bash reaper: enumeration selected pid=%d tracking_id=%s",
            pid, matched_tid,
        )
    return results


def _enumerate_stamped_members(open_rows: Any) -> list:
    """Public-ish enumeration entry point (plan task 4.1).

    Returns the list of pids selected for killing (exact env match +
    D9 gate). Refused/skipped candidates are logged and excluded. The
    underlying details (pid, tracking_id) are available via
    :func:`_enumerate_stamped_member_details`.
    """
    details = _enumerate_stamped_member_details(open_rows)
    return [pid for pid, _ in details]


def _kill_enumerated_pid(pid: int, grace_seconds: float) -> None:
    """TERM → bounded grace → KILL a single enumerated pid (ESRCH swallowed)."""
    if sys.platform == "win32":
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        logger.warning(
            "bash reaper: enumerated TERM failed for pid %s: %s: %s",
            pid, type(exc).__name__, exc,
        )
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        logger.warning(
            "bash reaper: enumerated KILL failed for pid %s: %s: %s",
            pid, type(exc).__name__, exc,
        )


async def _enumerated_sweep(
    open_rows: Any,
    *,
    repo: Any,
    reaped_by: str,
    my_pid: Optional[int] = None,
    term_grace_seconds: Optional[float] = None,
) -> int:
    """Phase 4 lane: enumerate stamped members, kill them, close rows."""
    if not kill_switch_from_env():
        return 0
    if my_pid is None:
        my_pid = os.getpid()
    # Only consider rows that are still open (sweep_rows closes rows
    # in place; refused/gc/reaped rows must not drive enumeration or
    # D8 os.kill probes).
    open_now = [
        r for r in (open_rows or [])
        if _row_get(r, "reaped_at") is None
    ]
    if not open_now:
        return 0
    survivors = await asyncio.to_thread(_enumerate_stamped_member_details, open_now)
    if not survivors:
        return 0
    grace = _clamped_grace(term_grace_seconds)
    # Kill all survivors in a worker thread (TERM grace sleeps there).
    def _run_kills():
        for pid, _tid in survivors:
            _kill_enumerated_pid(pid, grace)
    await asyncio.to_thread(_run_kills)

    # Close the associated rows.
    row_by_tid: dict = {}
    for r in open_now:
        tid = _row_get(r, "tracking_id")
        if tid is not None:
            row_by_tid.setdefault(str(tid), r)

    closed = 0
    for _pid, tid in survivors:
        mrow = row_by_tid.get(str(tid))
        if mrow is None or repo is None:
            continue
        rid = _row_get(mrow, "id")
        if rid is None:
            continue
        try:
            await _mark_reaped(
                repo, rid, f"{reaped_by}@{my_pid}", "enumerated"
            )
            closed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bash reaper: mark_reaped failed for enumerated row %s: "
                "%s: %s", rid, type(exc).__name__, exc,
            )
    return closed
