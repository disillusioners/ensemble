"""Shared fixtures for the RAM-guard substrate unit pack.

THE STAT-LINE FORMAT IS PINNED HERE (test-plan §1.1)
----------------------------------------------------

The reader parses ``/proc/<pid>/stat`` with the ``rfind(")")`` split.
A fixture author who mis-shapes a line silently gets wrong membership,
so the format is documented ONCE, here, and the builder below is the
only way a fake procfs tree is constructed in this pack:

    <pid> (<comm>) <state> <ppid> <pgrp> <session> <tty_nr> <tpgid> <flags> … <starttime> <vsize> <rss_pages> …

After the ``rfind(")")`` split the tail starts at **field 3**, so:
``tail[0]``=state · ``tail[1]``=ppid · ``tail[2]``=pgrp (field 5) ·
``tail[19]``=**starttime** (field 22) · ``tail[21]``=**rss_pages**
(field 24).  ``comm`` may contain spaces and parentheses ⇒ the split
must use the **LAST** ``)``.

The MEMBER-STATE fixtures are equally pinned (phase2-plan T8):

* **READABLE** — the status file carries a ``VmRSS:`` line.
* **ZERO** — the status file parses but carries NO ``VmRSS:`` line.
* **GONE** — the status file does not exist (ENOENT).
* **UNREADABLE** — the status path is a DIRECTORY: reading it raises
  ``IsADirectoryError`` (an ``OSError`` that is NOT
  ``FileNotFoundError``) ⇒ the reader's UNREADABLE branch, regardless
  of the uid the pack runs under (a chmod-based fixture would be
  bypassed by root).

The pack is PURE-PREDICATE: no real subprocesses, no real RAM, and no
host ``/proc`` reads beyond the fake trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: The procfs ``/proc/<pid>/status`` VmRSS unit is kB (KiB).
KB = 1024


@dataclass
class FakeProc:
    """One fake process inside the fake procfs tree."""

    pid: int
    comm: str = "proc"
    state: str = "S"
    ppid: int = 1
    pgrp: int = 1
    session: int = 1
    starttime: int = 1000
    rss_pages: int = 256
    #: The ``VmRSS:`` value in kB — ``None`` ⇒ no ``VmRSS:`` line (ZERO).
    rss_kb: int | None = 1024
    #: The ``Pss:`` value in kB — ``None`` ⇒ no ``Pss:`` line (ZERO).
    pss_kb: int | None = 512
    #: ``"gone"`` ⇒ no status file · ``"unreadable"`` ⇒ the status path
    #: is a directory · ``"ok"`` ⇒ a regular file.
    status_state: str = "ok"
    #: The same tri-state for the ``smaps_rollup`` file (the CONFIRM
    #: metric's source).
    pss_state: str = "ok"
    #: The ownership-stamp environ bytes (or ``None`` ⇒ no environ file).
    environ: bytes | None = None
    #: The extra ``stat`` tail fields — the builder fills the rest.
    extra: dict[str, int] = field(default_factory=dict)


def stat_line(proc: FakeProc) -> str:
    """Build ONE correctly-shaped ``/proc/<pid>/stat`` line."""
    # Fields 1–21 (pid … itrealvalue) then 22=starttime, 23=vsize,
    # 24=rss_pages.  Everything after the comm is the "tail".
    tail = [
        proc.state,      # 3
        str(proc.ppid),  # 4
        str(proc.pgrp),  # 5
        str(proc.session),  # 6
        "0",             # 7 tty_nr
        "-1",            # 8 tpgid
        "4194560",       # 9 flags
        "1",             # 10 minflt
        "0",             # 11 cminflt
        "0",             # 12 majflt
        "0",             # 13 cmajflt
        "0",             # 14 utime
        "0",             # 15 stime
        "0",             # 16 cutime
        "0",             # 17 cstime
        "20",            # 18 priority
        "0",             # 19 nice
        "1",             # 20 num_threads
        "0",             # 21 itrealvalue
        str(proc.starttime),  # 22 starttime
        "0",             # 23 vsize
        str(proc.rss_pages),  # 24 rss_pages
    ]
    tail.extend(str(value) for value in proc.extra.values())
    return f"{proc.pid} ({proc.comm}) " + " ".join(tail)


def write_proc_tree(
    root: Path,
    procs: list[FakeProc],
    *,
    mem_total_kb: int | None = None,
    cgroup_lines: list[str] | None = None,
) -> Path:
    """Materialise a fake ``/proc`` tree under ``root``.

    ``mem_total_kb`` writes a ``meminfo``; ``cgroup_lines`` writes
    ``self/cgroup`` (the cgroup detector's input).
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for proc in procs:
        pid_dir = root / str(proc.pid)
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "stat").write_text(stat_line(proc) + "\n")

        if proc.status_state == "gone":
            pass  # no status file ⇒ ENOENT ⇒ GONE
        elif proc.status_state == "unreadable":
            # A DIRECTORY: reading it raises IsADirectoryError (an
            # OSError that is NOT FileNotFoundError) ⇒ UNREADABLE.
            (pid_dir / "status").mkdir(exist_ok=True)
        else:
            lines = ["Name:\t%s" % proc.comm, "State:\t%s" % proc.state]
            if proc.rss_kb is not None:
                lines.append("VmRSS:\t%d kB" % proc.rss_kb)
            lines.append("VmHWM:\t%d kB" % (proc.rss_kb or 0))
            (pid_dir / "status").write_text("\n".join(lines) + "\n")

        if proc.pss_state == "unreadable":
            # A DIRECTORY ⇒ reading it raises IsADirectoryError ⇒ the
            # confirm's UNREADABLE branch.
            (pid_dir / "smaps_rollup").mkdir(exist_ok=True)
        elif proc.pss_state != "gone" and proc.pss_kb is not None:
            (pid_dir / "smaps_rollup").write_text(
                "Rss:\t%d kB\nPss:\t%d kB\n" % (proc.rss_kb or 0, proc.pss_kb)
            )
        if proc.environ is not None:
            (pid_dir / "environ").write_bytes(proc.environ)

    if mem_total_kb is not None:
        (root / "meminfo").write_text(
            "MemTotal:       %d kB\nMemFree:         1024 kB\n" % mem_total_kb
        )
    if cgroup_lines is not None:
        (root / "self").mkdir(exist_ok=True)
        (root / "self" / "cgroup").write_text("\n".join(cgroup_lines) + "\n")
    return root


def kill_tree(root: Path, pids: list[int]) -> None:
    """Make the fake members GONE — the alive→gone transition at a KILL.

    The P8 post-kill probe re-scans the tree; the census alone cannot
    make the probe observe a dead tree (test-plan §1.2).
    """
    for pid in pids:
        pid_dir = Path(root) / str(pid)
        for name in ("stat", "status", "smaps_rollup", "environ"):
            target = pid_dir / name
            if target.exists():
                if target.is_dir():
                    continue
                target.unlink()
