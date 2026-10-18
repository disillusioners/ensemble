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

This module is read-only: it never touches the DB. The flusher
thread (T6) owns persistence; the sampler thread (T5) calls
:func:`read_proc_snapshot` once per tick and writes the result
into the bounded ring.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


def read_daemon_snapshot(
    proc_root: Path,
    *,
    pss_kb: Optional[int] = None,
) -> Optional[DaemonProcSnapshot]:
    """Read ``/proc/self/{stat,statm,status}``. Returns None on failure.

    Args:
        proc_root: The /proc root (injectable for tests).
        pss_kb: Optional PSS override (smaps_rollup is gated by
            ``detail_level`` in the sampler; tests inject directly).
    """
    pid = os.getpid()
    stat = _read_proc_stat(proc_root, pid)
    if stat is None:
        return None

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

    Args:
        proc_root: Injectable /proc root (tests).
        cgroup_root: Injectable cgroup v2 root (tests).
        pss_kb: PSS override (smaps_rollup is gated by the sampler).
        child_pids: Optional list of child PIDs to walk.
    """
    proc_root = Path(proc_root)
    cgroup_root = Path(cgroup_root)

    daemon = read_daemon_snapshot(proc_root, pss_kb=pss_kb)
    if daemon is None:
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
]