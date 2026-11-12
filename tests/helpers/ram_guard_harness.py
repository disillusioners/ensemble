"""The BOUNDED balloon harness for the RAM-guard real-process packs (phase6 T1).

**Why a harness at all.**  The suite's own failure mode is the incident it
tests: a balloon that OOMs the host.  Every real-process leg therefore
spawns through THIS module, which encodes the four plan constraints
(test-plan.md §1):

1. **Real process trees, tiny ceilings.**  The allocator really touches
   every page (``bytearray`` alone is lazily mapped and never breaches).
2. **The allocation is explicit and bounded in the test source** — the
   caller passes MiB values; the harness refuses an allocation above
   :data:`MAX_ALLOCATION_BYTES` (256 MiB per test tree — the OOM-discipline
   cap from the commission brief).
3. **The host is checked BEFORE it allocates.**  Available memory must
   exceed the planned allocation by :data:`HEADROOM_FACTOR`; otherwise the
   harness *skips* (never breaches).
4. **Every spawned pid is registered for teardown**, and teardown always
   runs.  Registration uses the shared ``tests/tools/conftest`` reap
   registry when it is importable; the harness ALSO keeps its own tracker so
   a caller outside ``tests/tools`` is still covered.

**The stat-line format (test-plan §1.1, pinned).**  The reader parses
``/proc/<pid>/stat`` with the ``rfind(")")`` split; after the split the tail
starts at field 3, so ``tail[0]``=state · ``tail[1]``=ppid · ``tail[2]``=pgrp ·
``tail[19]``=starttime (field 22) · ``tail[21]``=rss_pages (field 24).
:func:`stat_line_shape` documents it and :func:`assert_stat_line_shape`
pins it against the reader's own parser so a fixture author cannot silently
mis-shape a line.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

#: The per-test-tree allocation cap (OOM discipline — the commission brief).
MAX_ALLOCATION_BYTES = 256 * 1024 * 1024

#: Required free-memory multiple of the planned allocation.
HEADROOM_FACTOR = 4

MIB = 1024 * 1024


class HostMemoryShortage(RuntimeError):
    """Raised when the host cannot safely host the planned allocation.

    A caller turns this into ``pytest.skip`` — never into a spawn.
    """


@dataclass
class BalloonSpec:
    """A fully-bounded balloon plan (sizes in BYTES, explicit in the caller)."""

    ceiling_bytes: int
    child_bytes: int
    child_count: int

    @property
    def planned_bytes(self) -> int:
        return self.child_bytes * self.child_count


@dataclass
class BalloonTree:
    """Teardown tracker for one spawned balloon tree."""

    script: Path
    pidfile: Path
    pids: list[int] = field(default_factory=list)

    def register(self, pid: int) -> None:
        if pid > 0 and pid not in self.pids:
            self.pids.append(pid)
            try:  # the house reap registry, when available
                from tests.tools.conftest import _register_pid

                _register_pid(pid, "ram-guard-balloon", str(self.pidfile))
            except Exception:  # noqa: BLE001 — outside tests/tools is fine
                pass

    def read_pids(self) -> list[int]:
        try:
            raw = self.pidfile.read_text()
        except OSError:
            return []
        return [int(ln.strip()) for ln in raw.splitlines() if ln.strip().isdigit()]

    def kill_all(self) -> None:
        """SIGKILL every known pid (the harness's own teardown backstop)."""
        for pid in set(self.pids) | set(self.read_pids()):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


def mem_available_bytes() -> int:
    """The host's MemAvailable, or ``-1`` when it CANNOT be read.

    A ``-1`` (unreadable / unparseable / absent ``MemAvailable:`` line) is
    a DISTINCT value from a genuine ``0`` — the caller treats it as a
    fail-closed condition (:func:`require_allocation_fits`), never as
    "plenty of memory".
    """
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return -1
    return -1


def require_allocation_fits(spec: BalloonSpec) -> None:
    """REFUSE to spawn when the host is short — raise :class:`HostMemoryShortage`.

    The planned allocation must (a) be under the per-tree cap and (b) leave
    :data:`HEADROOM_FACTOR`× free memory on the host.

    **FAIL-CLOSED (D-6):** an UNREADABLE / ZERO ``MemAvailable`` refuses the
    spawn.  A silent pass on an unreadable ``/proc/meminfo`` would let a
    balloon run blind on an unknown-memory host — the exact OOM hazard this
    harness exists to prevent.
    """
    if spec.planned_bytes > MAX_ALLOCATION_BYTES:
        raise HostMemoryShortage(
            f"planned allocation {spec.planned_bytes} bytes exceeds the "
            f"per-tree cap {MAX_ALLOCATION_BYTES} bytes (OOM discipline)"
        )
    needed = spec.planned_bytes * HEADROOM_FACTOR
    available = mem_available_bytes()
    if available <= 0:
        raise HostMemoryShortage(
            "MemAvailable is unreadable or non-positive "
            f"({available} bytes); refusing to spawn blind (fail-closed)"
        )
    if available < needed:
        raise HostMemoryShortage(
            f"host memory short: {available} bytes available, {needed} required "
            f"({HEADROOM_FACTOR}x the {spec.planned_bytes}-byte plan)"
        )


# ── The balloon script (real forks, real touched pages, a pidfile) ─────────

_BALLOON_PY = """
import os, sys, time
size = int(sys.argv[1])
kids = int(sys.argv[2])
pidfile = sys.argv[3]
for _ in range(kids):
    pid = os.fork()
    if pid == 0:
        try:
            fd = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (str(os.getpid()) + "\\n").encode())
            os.close(fd)
            buf = bytearray(size)
            for off in range(0, size, 4096):
                buf[off] = 1
            time.sleep(120)
        finally:
            os._exit(0)
time.sleep(120)
"""

#: The FORK-WITHOUT-EXEC fairness script: a parent allocates ONE buffer and
#: forks N children that only SLEEP (they never exec, so they share the
#: parent's pages copy-on-write).  Summed RSS counts the shared pages once
#: PER PROCESS (the over-count); summed PSS counts them ~once (fair).
_FORK_FAIR_PY = """
import os, sys, time
size = int(sys.argv[1])
kids = int(sys.argv[2])
pidfile = sys.argv[3]
hold = float(sys.argv[4]) if len(sys.argv) > 4 else 120.0
parent_buf = bytearray(size)
for off in range(0, size, 4096):
    parent_buf[off] = 1
for _ in range(kids):
    pid = os.fork()
    if pid == 0:
        try:
            fd = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (str(os.getpid()) + "\\n").encode())
            os.close(fd)
            time.sleep(hold)
        finally:
            os._exit(0)
time.sleep(hold)
"""


def prepare_balloon(tmp_path: Path, *, fair: bool = False) -> BalloonTree:
    """Materialise the balloon script + a pidfile under ``tmp_path``."""
    script = Path(tmp_path) / ("fork_fair.py" if fair else "balloon.py")
    script.write_text(_FORK_FAIR_PY if fair else _BALLOON_PY, encoding="utf-8")
    pidfile = Path(tmp_path) / "pids.txt"
    if pidfile.exists():
        pidfile.unlink()
    return BalloonTree(script=script, pidfile=pidfile)


def wait_for_pids(tree: BalloonTree, count: int, timeout: float = 10.0) -> list[int]:
    """Poll the pidfile until ``count`` pids are recorded (or timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pids = tree.read_pids()
        for pid in pids:
            tree.register(pid)
        if len(pids) >= count:
            return pids
        time.sleep(0.05)
    return tree.read_pids()


def wait_dead(pids: list[int], timeout: float = 15.0) -> bool:
    """True when none of ``pids`` is alive within ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(pid_alive(pid) for pid in pids)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


# ── The stat-line format pin (test-plan §1.1) ──────────────────────────────


def stat_line_shape() -> str:
    """The pinned ``/proc/<pid>/stat`` shape the reader's parse expects."""
    return (
        "<pid> (<comm>) <state> <ppid> <pgrp> <session> <tty_nr> <tpgid> "
        "<flags> … <starttime> <vsize> <rss_pages> …   "
        "| after rfind(')'): tail[0]=state tail[1]=ppid tail[2]=pgrp "
        "tail[19]=starttime(22) tail[21]=rss_pages(24)"
    )


def assert_stat_line_shape() -> None:
    """Pin the documented shape against the reader's own parser.

    A fixture author who mis-shapes a line would silently mis-resolve
    membership; this asserts the documented tail indices agree with the
    reader module's private offsets.
    """
    from daemon.services.ram_guard import tree_reader as reader

    assert reader._STAT_FIELD_STATE == 0
    assert reader._STAT_FIELD_PPID == 1
    assert reader._STAT_FIELD_PGRP == 2
    assert reader._STAT_FIELD_STARTTIME == 19
    assert reader._STAT_FIELD_RSS_PAGES == 21


__all__ = [
    "HEADROOM_FACTOR",
    "MAX_ALLOCATION_BYTES",
    "MIB",
    "BalloonSpec",
    "BalloonTree",
    "HostMemoryShortage",
    "assert_stat_line_shape",
    "mem_available_bytes",
    "pid_alive",
    "prepare_balloon",
    "require_allocation_fits",
    "stat_line_shape",
    "wait_dead",
    "wait_for_pids",
]
