"""``proc_readers`` — cgroup v2 + ``/proc`` readers for the resource
monitor.

Architecture §2.1 (binding):

  * Hot-path sources are cgroup v2 via ``/proc/self/cgroup``
    (``memory.current``, ``memory.stat`` anon/file/slab,
    ``cpu.stat`` usage/throttled, ``memory.peak``) plus raw
    ``/proc/self/{stat,statm,status}`` and per-child
    ``/proc/<pid>/{stat,status}``.
  * ``smaps_rollup`` (PSS) at 1/10 cadence is gated on the
    ``detail_level`` knob (the env-gated knob in the plan);
    P1 emits PSS only when ``detail_level='full'`` or every 10th
    tick under ``detail_level='auto'``.
  * **NO psutil.** Zero new deps.
  * All roots INJECTABLE — ``proc_root`` and ``cgroup_root`` are
    constructor args defaulting to the real paths; tests inject
    synthetic fixture trees (T9 / T10 hermetic + fenced).
  * cgroup v1 / absent ⇒ ``cgroup`` is ``None`` in the payload
    (degrade to daemon-only sampling), NEVER a crash.
  * **PORTABLE HOST FALLBACK** — the daemon's deployment host is
    macOS, which has NO ``/proc`` and NO cgroup v2. On such a host the
    ``/proc`` readers cannot answer at all, so
    :func:`read_daemon_snapshot` falls back to a portable self-snapshot
    (``ps`` + ``resource.getrusage`` + ``/dev/fd``) — see
    :func:`_read_platform_daemon_snapshot`. Without that fallback the
    sampler produced ZERO rows and the flusher wrote ZERO DB rows: the
    whole resource-telemetry tier stayed dark while every counter
    climbed. The fallback engages ONLY when the proc root is ABSENT
    and the host is Darwin, so the ``/proc`` path stays authoritative
    on Linux and the injected-fixture tests stay hermetic.

This module is read-only: it never touches the DB. The flusher
thread (T6) owns persistence; the sampler thread (T5) calls
:func:`read_proc_snapshot` once per tick and writes the result
into the bounded ring.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # pragma: no cover - POSIX-only module (the daemon is macOS/Linux)
    import resource as _resource_mod
except ImportError:  # pragma: no cover - Windows has no ``resource``
    _resource_mod = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ── Daemon + cgroup + per-child data shape ─────────────────────────────────


@dataclass
class CgroupMemory:
    """cgroup v2 memory shape (subset of §2.8 ``cgroup.memory``)."""

    current_mb: int
    peak_mb: int
    anon_mb: int
    file_mb: int
    slab_mb: int


@dataclass
class CgroupCpu:
    """cgroup v2 cpu shape (subset of §2.8 ``cgroup.cpu``)."""

    usage_usec: int
    throttled_usec: int
    nr_throttled: int


@dataclass
class CgroupSnapshot:
    """cgroup v2 snapshot — None when cgroup v2 absent / v1 / unreadable."""

    memory: CgroupMemory
    cpu: CgroupCpu


@dataclass
class DaemonProcSnapshot:
    """``/proc/self/{stat,statm,status}`` snapshot."""

    pid: int
    rss_kb: int
    vsize_kb: int
    anon_kb: int
    hw_peak_kb: int  # VmHWM
    threads: int
    fds: int
    cpu_user_usec: int  # utime from /proc/self/stat
    cpu_sys_usec: int  # stime from /proc/self/stat
    pss_kb: Optional[int]  # smaps_rollup; None when not sampled


@dataclass
class ChildSnapshot:
    """Per-child ``/proc/<pid>/{stat,status}`` snapshot."""

    pid: int
    rss_kb: int
    cpu_user_usec: int  # utime (jiffies)


@dataclass
class ProcSnapshot:
    """One sample's read — fed into the ring."""

    daemon: DaemonProcSnapshot
    cgroup: Optional[CgroupSnapshot]  # None when cgroup v2 absent
    children: list[ChildSnapshot] = field(default_factory=list)


# ── Low-level readers (injectable roots, return S) ──────────────────────────


def _read_text(path: Path) -> Optional[str]:
    """Read a small text file; return ``None`` on any error."""
    try:
        return path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _read_int(path: Path) -> Optional[int]:
    """Read a single integer from a small text file."""
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        return None


def _parse_keyvals(text: str) -> dict[str, int]:
    """Parse ``key value`` / ``key: value`` lines into a dict."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # ``memory.stat`` uses space separation; ``status`` uses colon.
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            key, val = parts[0], parts[1]
        try:
            out[key] = int(val)
        except (ValueError, TypeError):
            continue
    return out


def _self_cgroup_path(proc_root: Path, cgroup_root: Path) -> Optional[Path]:
    """Read ``/proc/self/cgroup`` and resolve the cgroup v2 mount path.

    Returns ``None`` for cgroup v1 / absent / unreadable.
    """
    raw = _read_text(proc_root / "self" / "cgroup")
    if raw is None:
        return None
    # cgroup v2 line format: ``0::<path>`` (hierarchy-id 0, controllers
    # empty, then the cgroup path).
    for line in raw.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            cgroup_path = parts[2].strip()
            if not cgroup_path or cgroup_path == "/":
                # Root cgroup (no slice) — point at the cgroup root.
                return cgroup_root
            return cgroup_root / cgroup_path.lstrip("/")
    return None


def _read_cgroup_memory(cg_dir: Path) -> Optional[CgroupMemory]:
    """Read cgroup v2 memory files. Returns None on any structural failure."""
    current = _read_int(cg_dir / "memory.current")
    if current is None:
        return None
    peak = _read_int(cg_dir / "memory.peak")
    if peak is None:
        peak = current  # kernel may not have memory.peak (very old v2)
    stat_text = _read_text(cg_dir / "memory.stat")
    if stat_text is None:
        return None
    kv = _parse_keyvals(stat_text)
    # Bytes → MB at the boundary (KB_INT * 1024 → bytes / 1048576).
    return CgroupMemory(
        current_mb=current // (1024 * 1024),
        peak_mb=peak // (1024 * 1024),
        anon_mb=kv.get("anon", 0) // (1024 * 1024),
        file_mb=kv.get("file", 0) // (1024 * 1024),
        slab_mb=kv.get("slab", 0) // (1024 * 1024),
    )


def _read_cgroup_cpu(cg_dir: Path) -> Optional[CgroupCpu]:
    """Read cgroup v2 cpu files."""
    stat_text = _read_text(cg_dir / "cpu.stat")
    if stat_text is None:
        return None
    kv = _parse_keyvals(stat_text)
    usage_usec = kv.get("usage_usec", 0)
    throttled_usec = kv.get("throttled_usec", 0)
    nr_throttled = kv.get("nr_throttled", 0)
    return CgroupCpu(
        usage_usec=usage_usec,
        throttled_usec=throttled_usec,
        nr_throttled=nr_throttled,
    )


def read_cgroup_snapshot(proc_root: Path, cgroup_root: Path) -> Optional[CgroupSnapshot]:
    """Top-level cgroup reader. Returns None when v2 absent / unreadable.

    Per architecture §2.1, the sampler degrades to daemon-only on
    missing / cgroup v1 — never a crash.
    """
    cg_dir = _self_cgroup_path(proc_root, cgroup_root)
    if cg_dir is None:
        return None
    if not cg_dir.exists():
        return None
    memory = _read_cgroup_memory(cg_dir)
    cpu = _read_cgroup_cpu(cg_dir)
    if memory is None or cpu is None:
        return None
    return CgroupSnapshot(memory=memory, cpu=cpu)


# /proc/self/stat field positions (1-indexed; field 22 = starttime jiffies)
# https://man7.org/linux/man-pages/man5/proc.5.html
# After parsing the ``(comm)`` field (which may contain spaces and
# parens), the remaining fields live in a list whose element ``i``
# corresponds to overall field ``i+3``. So field N maps to ``parts[N-3]``.
# Note: field 2 (``comm``) may contain spaces and parens, so the parser
#       locates the LAST ``)`` and splits after it.
_STAT_FIELD_UTIME = 14   # user CPU jiffies
_STAT_FIELD_STIME = 15   # system CPU jiffies
_STAT_FIELD_START_TIME = 22
_STAT_FIELD_RSS_PAGES = 24
_STAT_FIELD_VSIZE_PAGES = 23
_STAT_FIELD_THREADS = 19  # num_threads
# Offset from 1-indexed field number to 0-based ``parts`` index,
# accounting for the stripped ``(comm)`` field that sits in slot 2.
_STAT_PART_OFFSET = 3


def _parse_proc_stat(stat: str) -> Optional[dict[str, int]]:
    """Parse a ``/proc/<pid>/stat`` line. Returns None on parse failure.

    Handles the ``(comm)`` field that may contain spaces and parens
    by locating the LAST ``)`` and splitting after it.
    """
    if not stat:
        return None
    last_paren = stat.rfind(")")
    if last_paren == -1:
        return None
    # ``(comm) state ...`` — fields start at the byte AFTER the ``)``.
    head, _, tail = stat.rpartition(")")
    parts = tail.strip().split()
    if len(parts) < _STAT_FIELD_VSIZE_PAGES - _STAT_PART_OFFSET + 1:
        return None
    try:
        return {
            "utime": int(parts[_STAT_FIELD_UTIME - _STAT_PART_OFFSET]),
            "stime": int(parts[_STAT_FIELD_STIME - _STAT_PART_OFFSET]),
            "starttime": int(parts[_STAT_FIELD_START_TIME - _STAT_PART_OFFSET]),
            "rss_pages": int(parts[_STAT_FIELD_RSS_PAGES - _STAT_PART_OFFSET]),
            "vsize_pages": int(parts[_STAT_FIELD_VSIZE_PAGES - _STAT_PART_OFFSET]),
            "threads": int(parts[_STAT_FIELD_THREADS - _STAT_PART_OFFSET]),
        }
    except (ValueError, IndexError):
        return None


def _proc_self_paths(proc_root: Path, pid: int) -> list[Path]:
    """Return the candidate stat paths for the daemon — ``self`` first,
    then the literal pid directory. The /proc contract has ``self`` as
    a symlink to the current pid; tests can either make ``self``
    available (recommended — like real /proc) or create the literal
    pid directory.
    """
    return [proc_root / "self", proc_root / str(pid)]


def _read_proc_stat(proc_root: Path, pid: int) -> Optional[dict[str, int]]:
    """Read /proc/<pid>/stat. For ``pid == os.getpid()``, the daemon
    may use the ``self`` symlink (real /proc contract). For any other
    pid, ONLY the literal pid directory is consulted — children must
    not silently read the daemon's stat under a /proc/self fixture.
    """
    bases: list[Path]
    if pid == os.getpid():
        bases = [proc_root / "self", proc_root / str(pid)]
    else:
        bases = [proc_root / str(pid)]
    for base in bases:
        raw = _read_text(base / "stat")
        if raw is None:
            continue
        first_line = raw.splitlines()[0] if raw else ""
        return _parse_proc_stat(first_line)
    return None


def _parse_status_kv(text: str) -> dict[str, str]:
    """Parse a ``/proc/<pid>/status`` body. ``Key: Value`` lines."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip()
    return out


def _parse_status_kb(text: str) -> dict[str, int]:
    """Parse the int-kb fields from a ``/proc/<pid>/status`` body.

    Values are either ``<int> kB`` (VmRSS, VmHWM, etc.) or just
    ``<int>`` (Threads). We extract the leading integer.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        parts = val.split()
        if not parts:
            continue
        try:
            out[key] = int(parts[0])
        except (ValueError, TypeError):
            continue
    return out


# ── Portable (non-/proc) host fallback ─────────────────────────────────────
#
# The daemon's deployment host is macOS: there is NO ``/proc`` and NO
# cgroup v2 tree. The ``/proc`` readers above are the AUTHORITATIVE
# source on Linux, but on a host without ``/proc`` they cannot produce
# a snapshot at all — ``read_proc_snapshot`` returned ``None``, the
# sampler wrote NO ring row, and therefore the flusher wrote NO DB
# row: the entire resource-telemetry tier stayed dark while every
# counter still climbed (``samples`` up, ``flush.errors == 0``).
#
# The fallback is deliberately NARROW:
#
#   * it engages ONLY when the proc root is ABSENT — an EXISTING but
#     unreadable proc root keeps the historical ``None`` contract, so
#     the injected-fixture tests stay hermetic (they build a real
#     directory tree under ``tmp_path``);
#   * it engages ONLY on Darwin — on Linux ``/proc`` is present and
#     remains authoritative; a missing ``/proc`` there is a genuinely
#     broken host and must NOT be papered over;
#   * it NEVER changes the ``/proc`` code path when ``/proc`` exists.
#
# Data sources (the module's "NO psutil — zero new deps" rule holds):
#
#   * RSS / VSZ  — ``ps -o rss=,vsz= -p <pid>`` (Darwin reports KB).
#   * threads    — ``ps -M -p <pid>`` (one line per thread + header).
#   * fds        — ``/dev/fd`` (Darwin exposes the process's own
#     descriptor list there).
#   * CPU        — ``resource.getrusage(RUSAGE_SELF)`` (utime/stime).
#   * peak RSS   — ``resource.getrusage().ru_maxrss`` (BYTES on Darwin).
#
# Cost: two short ``ps`` forks per sampler tick. The tick
# interval is 15-60 s and the sampler self-cost budget is 1 % of that
# interval (>= 150 ms), so the fallback stays far inside the budget.
# The cost is stated as a RATIO, never as a settled millisecond
# measurement: on the macOS deployment host the fallback measures in
# the tens of milliseconds against a 30 s interval — well under 0.1 %
# of the interval, i.e. >10x headroom against the self-cost budget.
# The absolute value tracks host load and is NOT a contract.
#
# The per-call ``ps`` timeout is ``_PS_TIMEOUT_S`` (1 s) — the tick
# budget is ~300 ms, so a hung ``ps`` must not block the sampler for
# seconds; two hung calls would exceed the whole tick budget.

_PS_TIMEOUT_S = 1.0

#: The ``ps`` binary is resolved ONCE at import time. A PATH that is
#: broken at boot used to surface as a ``FileNotFoundError`` swallowed
#: by :func:`_run_ps` — the SAME silent ``None`` as every other
#: failure, so the original macOS dark-tier bug could reappear with NO
#: operator-visible cause. Resolving once (and warning once) makes the
#: "the binary is not on PATH" case loud at boot instead of silent at
#: tick time. ``None`` ⇒ the portable fallback cannot read memory.
_PS_PATH: Optional[str] = shutil.which("ps")

if _PS_PATH is None:  # pragma: no cover - depends on the host PATH
    logger.warning(
        "resource-monitor proc_readers: the `ps` binary was NOT found on "
        "PATH — the portable (no-/proc) host fallback cannot read RSS/VSZ "
        "or the thread count, so every sampler tick on such a host will "
        "be a DEGRADED (no-sample) tick",
    )


# ── Read-failure reason vocabulary ─────────────────────────────────────────
#
# The sampler's degradation warning must name a CAUSE, not just
# "(platform read failed)" — a missing binary, a non-zero exit, a
# timeout and a parse failure are four different operator problems.
# The vocabulary below is a FIXED, CLOSED set of tokens: it NEVER
# carries ``ps`` stdout (that would leak process command lines into
# the log) and never carries an exception repr.
#
# The reason is recorded in a module-level slot rather than returned
# through the read seam because ``_run_ps`` is a MONKEYPATCHED seam in
# the test corpus (the real ``ps`` is never spawned) — changing its
# signature would break every caller for no diagnostic gain. The slot
# is written by the sampler thread only (one reader thread), so the
# plain assignment is safe.

REASON_PS_BINARY_MISSING = "ps-binary-missing"
REASON_PS_SPAWN_FAILED = "ps-spawn-failed"
REASON_PS_TIMEOUT = "ps-timeout"
REASON_PS_NONZERO_EXIT = "ps-nonzero-exit"
REASON_PS_EMPTY_OUTPUT = "ps-empty-output"
REASON_PS_UNPARSABLE = "ps-unparsable-output"
REASON_PROC_UNREADABLE = "proc-root-unreadable"
REASON_DARWIN_FALLBACK_FAILED = "darwin-fallback-failed"

_LAST_READ_REASON: Optional[str] = None


def reset_read_reason() -> None:
    """Clear the recorded read-failure reason (called before a read)."""
    global _LAST_READ_REASON
    _LAST_READ_REASON = None


def last_read_reason() -> Optional[str]:
    """The reason token for the MOST RECENT failed platform read.

    ``None`` when the last read SUCCEEDED (or when the read seam was
    replaced by a test double that cannot report a cause).
    """
    return _LAST_READ_REASON


def _set_read_reason(reason: str) -> None:
    global _LAST_READ_REASON
    _LAST_READ_REASON = reason


def _run_ps(args: list[str]) -> Optional[str]:
    """Run ``ps`` with the given args. Returns stripped stdout or None.

    Fail-soft by contract: a missing ``ps`` binary, a non-zero exit
    code, an empty stdout, or a timeout all return ``None`` — the
    caller degrades instead of raising into the sampler tick.

    Each failure mode records a DISTINCT reason token (see
    :func:`last_read_reason`) so the sampler can name the cause. The
    ``ps`` stdout is NEVER recorded, logged or returned in the reason.
    """
    if _PS_PATH is None:
        _set_read_reason(REASON_PS_BINARY_MISSING)
        return None
    try:
        completed = subprocess.run(
            [_PS_PATH, *args],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _set_read_reason(REASON_PS_TIMEOUT)
        return None
    except (OSError, subprocess.SubprocessError):
        _set_read_reason(REASON_PS_SPAWN_FAILED)
        return None
    if completed.returncode != 0:
        _set_read_reason(REASON_PS_NONZERO_EXIT)
        return None
    out = (completed.stdout or "").strip()
    if not out:
        _set_read_reason(REASON_PS_EMPTY_OUTPUT)
        return None
    return out


def _darwin_memory_kb(pid: int) -> Optional[tuple[int, int]]:
    """``(rss_kb, vsize_kb)`` from ``ps`` — None when unavailable."""
    out = _run_ps(["-o", "rss=,vsz=", "-p", str(pid)])
    if out is None:
        return None
    parts = out.split()
    if len(parts) < 2:
        _set_read_reason(REASON_PS_UNPARSABLE)
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        _set_read_reason(REASON_PS_UNPARSABLE)
        return None


def _darwin_thread_count(pid: int) -> int:
    """Thread count from ``ps -M`` (one line per thread + a header).

    Falls back to ``threading.active_count()`` (the Python-visible
    thread count) when ``ps`` is unavailable — an approximation is
    better than a zero for a monitoring column.
    """
    out = _run_ps(["-M", "-p", str(pid)])
    if out is None:
        return threading.active_count()
    lines = [line for line in out.splitlines() if line.strip()]
    if len(lines) <= 1:
        # Header only — ``ps`` saw no thread rows. Fall back to the
        # Python-visible count rather than reporting zero.
        return max(1, threading.active_count())
    return len(lines) - 1


def _darwin_fd_count() -> int:
    """Open-descriptor count from ``/dev/fd``.

    ``/dev/fd`` lists the CURRENT process's descriptors. The listing
    itself holds one descriptor open while it is read, so one is
    subtracted — the count is approximate by exactly that one.
    """
    try:
        return max(0, len(os.listdir("/dev/fd")) - 1)
    except OSError:
        return 0


def _read_darwin_daemon_snapshot(
    pid: int,
    *,
    pss_kb: Optional[int] = None,
) -> Optional[DaemonProcSnapshot]:
    """Self-snapshot for a macOS host (no ``/proc``).

    Returns ``None`` when the required sources are unavailable — the
    sampler then treats the tick as a structural read failure (and
    counts it, so the blind spot is visible instead of silent).
    """
    memory = _darwin_memory_kb(pid)
    if memory is None:
        if last_read_reason() is None:
            # The ``ps`` seam did not report a cause (e.g. a test
            # double replaced it) — name the fallback instead of
            # staying silent.
            _set_read_reason(REASON_DARWIN_FALLBACK_FAILED)
        return None
    rss_kb, vsize_kb = memory

    if _resource_mod is None:  # pragma: no cover - POSIX-only host
        return None
    usage = _resource_mod.getrusage(_resource_mod.RUSAGE_SELF)
    cpu_user_usec = int(usage.ru_utime * 1_000_000)
    cpu_sys_usec = int(usage.ru_stime * 1_000_000)
    # Darwin's ``ru_maxrss`` is in BYTES (Linux reports KB).
    hw_peak_kb = int(usage.ru_maxrss / 1024)

    return DaemonProcSnapshot(
        pid=pid,
        rss_kb=rss_kb,
        vsize_kb=vsize_kb,
        # Darwin exposes no private-anonymous counter; the Linux reader
        # degrades the same way (``anon_kb = VmData or rss_kb``).
        anon_kb=rss_kb,
        hw_peak_kb=hw_peak_kb,
        threads=_darwin_thread_count(pid),
        fds=_darwin_fd_count(),
        cpu_user_usec=cpu_user_usec,
        cpu_sys_usec=cpu_sys_usec,
        # Darwin has no PSS (no ``smaps_rollup``).
        pss_kb=pss_kb,
    )


def _read_platform_daemon_snapshot(
    proc_root: Path,
    pid: int,
    *,
    pss_kb: Optional[int] = None,
) -> Optional[DaemonProcSnapshot]:
    """Fallback for hosts WITHOUT ``/proc``. None when not applicable.

    Two guards, both load-bearing:

    * ``proc_root.exists()`` — an existing-but-unreadable proc root
      keeps the historical ``None`` contract. That is what the
      injected-fixture tests rely on (they build a synthetic tree, so
      the fallback must never reach for the REAL host process).
    * ``sys.platform != "darwin"`` — on Linux ``/proc`` is
      authoritative; its absence is a broken host, not a platform
      difference, and must stay visible.
    """
    if proc_root.exists():
        return None
    if sys.platform != "darwin":
        return None
    return _read_darwin_daemon_snapshot(pid, pss_kb=pss_kb)


def read_daemon_snapshot(
    proc_root: Path,
    *,
    pss_kb: Optional[int] = None,
) -> Optional[DaemonProcSnapshot]:
    """Read ``/proc/self/{stat,statm,status}``. Returns None on failure.

    On a host with NO ``/proc`` (macOS — the daemon's deployment host)
    the ``/proc`` readers cannot answer; the portable fallback
    (:func:`_read_platform_daemon_snapshot`) supplies the snapshot so
    the sampler keeps producing rows. Without it the whole
    resource-telemetry tier is dark (no ring row ⇒ no DB row).

    Args:
        proc_root: The /proc root (injectable for tests).
        pss_kb: Optional PSS override (smaps_rollup is gated by
            ``detail_level`` in the sampler; tests inject directly).
    """
    pid = os.getpid()
    stat = _read_proc_stat(proc_root, pid)
    if stat is None:
        return _read_platform_daemon_snapshot(proc_root, pid, pss_kb=pss_kb)

    # Find the daemon's proc entry — ``self`` symlink preferred, fall
    # back to literal pid dir. Children are NEVER read from ``self``
    # (see _read_proc_stat).
    daemon_dir: Optional[Path] = None
    for candidate in (proc_root / "self", proc_root / str(pid)):
        if (candidate / "stat").exists():
            daemon_dir = candidate
            break
    if daemon_dir is None:
        daemon_dir = proc_root / "self"

    # /proc/self/status gives VmRSS (kB), VmHWM (kB), VmSize (bytes),
    # VmData (kB), Threads (count).
    status_text = _read_text(daemon_dir / "status")
    status_kb: dict[str, int] = {}
    if status_text is not None:
        status_kb = _parse_status_kb(status_text)

    # /proc/self/statm: size resident shared text lib data dt
    # (in pages). rss is field 2.
    statm_raw = _read_text(daemon_dir / "statm")
    rss_pages = stat["rss_pages"]
    vsize_pages = stat["vsize_pages"]
    if statm_raw is not None:
        parts = statm_raw.split()
        if len(parts) >= 2:
            try:
                rss_pages = int(parts[1])
            except (ValueError, TypeError):
                pass
        if len(parts) >= 1:
            try:
                vsize_pages = int(parts[0])
            except (ValueError, TypeError):
                pass

    # rss in kB (pages * 4 on x86_64; PAGE_SIZE not importable cross-
    # platform — use 4K which is the only page size Linux supports for
    # /proc/<pid>/statm). status.VmRSS in kB is the authority when
    # present.
    rss_kb = status_kb.get("VmRSS")
    if rss_kb is None:
        rss_kb = rss_pages * 4
    vsize_kb = vsize_pages * 4

    # Threads: status is authoritative; fall back to /proc/self/stat.
    threads = status_kb.get("Threads")
    if threads is None:
        threads = stat["threads"]

    # FDs: count entries in /proc/self/fd (or 0 if absent). Bounded
    # by the kernel max-fds which is small (default 1024) so iterating
    # is cheap.
    fd_dir = daemon_dir / "fd"
    try:
        fds = sum(1 for _ in fd_dir.iterdir())
    except (FileNotFoundError, PermissionError, OSError, NotADirectoryError):
        fds = 0

    # VmHWM (high-water RSS) — peak kB. Optional.
    hw_peak_kb = status_kb.get("VmHWM") or 0

    # anon_kb: prefer status.VmData as a proxy (private data) when no
    # /proc/self/smaps_rollup is sampled. Falls back to rss_kb for the
    # degraded-but-not-crashing daemon-only path. PSS path overrides
    # anon_kb downstream.
    anon_kb = status_kb.get("VmData") or rss_kb

    # CPU jiffies → usec (USER_HZ = 100 on most Linux; the proc(5)
    # contract is ``sysconf(_SC_CLK_TCK)``). Hard-coded 100 — the
    # only supported host runs USER_HZ=100, and reading ``os.sysconf``
    # here would add a syscall per tick.
    cpu_user_usec = stat["utime"] * 10_000
    cpu_sys_usec = stat["stime"] * 10_000

    return DaemonProcSnapshot(
        pid=pid,
        rss_kb=rss_kb,
        vsize_kb=vsize_kb,
        anon_kb=anon_kb,
        hw_peak_kb=hw_peak_kb,
        threads=threads,
        fds=fds,
        cpu_user_usec=cpu_user_usec,
        cpu_sys_usec=cpu_sys_usec,
        pss_kb=pss_kb,
    )


def read_child_snapshot(proc_root: Path, pid: int) -> Optional[ChildSnapshot]:
    """Read a single child process's ``/proc/<pid>/{stat,status}``.

    Used for the registry children that the sampler walks each tick.
    Returns None when the pid has exited (the registry will catch
    that on the next reconciler pass).
    """
    stat = _read_proc_stat(proc_root, pid)
    if stat is None:
        return None
    status_text = _read_text(proc_root / str(pid) / "status")
    status_kb = (
        _parse_status_kb(status_text) if status_text is not None else {}
    )
    rss_kb = status_kb.get("VmRSS")
    if rss_kb is None:
        rss_kb = stat["rss_pages"] * 4
    return ChildSnapshot(
        pid=pid,
        rss_kb=rss_kb,
        cpu_user_usec=stat["utime"] * 10_000,
    )


def read_pss_rollup(proc_root: Path) -> Optional[int]:
    """Read ``/proc/self/smaps_rollup`` for PSS in kB.

    Gated by ``detail_level`` in the sampler (1/10 cadence under
    ``deep`` / ``full``). Routed through the module's ``_read_text``
    so the fenced tests can instrument the read (escape guard).
    Returns None on any read failure — never crashes the sampler.
    """
    text = _read_text(proc_root / "self" / "smaps_rollup")
    if text is None:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Pss:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except (ValueError, TypeError):
                    return None
    return None


# ── Top-level entry ────────────────────────────────────────────────────────


def read_proc_snapshot(
    proc_root: Path | str = "/proc",
    cgroup_root: Path | str = "/sys/fs/cgroup",
    *,
    pss_kb: Optional[int] = None,
    child_pids: Optional[list[int]] = None,
) -> Optional[ProcSnapshot]:
    """ONE snapshot of the daemon + cgroup + registry children.

    Used by the sampler thread (T5) once per tick. The sampler never
    reads more than one tick at a time; ``child_pids`` is the
    caller-supplied registry list (P2 will populate it from the
    resource_pid_map table; P1 leaves it empty / caller-supplied).

    Returns ``None`` on a structurally-broken /proc (the sampler
    treats that as a skip — never crashes).

    A failed read records a REASON token (see :func:`last_read_reason`)
    so the sampler can name the cause in its degradation warning
    instead of the opaque "(platform read failed)".

    Args:
        proc_root: Injectable /proc root (tests).
        cgroup_root: Injectable cgroup v2 root (tests).
        pss_kb: PSS override (smaps_rollup is gated by the sampler).
        child_pids: Optional list of child PIDs to walk.
    """
    proc_root = Path(proc_root)
    cgroup_root = Path(cgroup_root)

    reset_read_reason()
    daemon = read_daemon_snapshot(proc_root, pss_kb=pss_kb)
    if daemon is None:
        if last_read_reason() is None:
            # The /proc path (Linux) has no reason vocabulary yet —
            # name the structural fact instead of staying silent.
            _set_read_reason(REASON_PROC_UNREADABLE)
        return None
    cgroup = read_cgroup_snapshot(proc_root, cgroup_root)

    children: list[ChildSnapshot] = []
    for pid in (child_pids or []):
        child = read_child_snapshot(proc_root, pid)
        if child is not None:
            children.append(child)

    return ProcSnapshot(daemon=daemon, cgroup=cgroup, children=children)


__all__ = [
    "CgroupMemory",
    "CgroupCpu",
    "CgroupSnapshot",
    "DaemonProcSnapshot",
    "ChildSnapshot",
    "ProcSnapshot",
    "read_cgroup_snapshot",
    "read_daemon_snapshot",
    "read_child_snapshot",
    "read_proc_snapshot",
    "read_pss_rollup",
    "reset_read_reason",
    "last_read_reason",
]