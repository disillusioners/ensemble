"""``tree_reader`` — the injectable ``/proc`` reader for the RAM guard.

Contract: architecture-recommendation.md §4.1 (the FIVE-function reader
contract), §4.1a (the bounded re-arm's inputs), phase2-plan.md T2 / T3 /
T8 / T9.  Every string the contract pins char-for-char is reproduced
here verbatim.

**Import freeze (arch §7 / §15 B7).**  This module imports NOTHING from
``daemon/`` — no ``tools/*``, no ``resource_monitor``, no
``repositories``, no ``event_bus``, no ``main_loop_bridge``, no
``bash_spawn_reaper``.  ``ram_guard`` is stdlib-only.

**The five functions (§4.1, exact):**

    scan_proc(proc_root) -> ProcTable
    resolve_members(table, *, pgid, leader_pid, max_depth, max_nodes) -> MemberSet
    read_tree_rss(proc_root, member_pids) -> TreeReading     # tick thread only
    read_tree_pss(proc_root, member_pids) -> TreeReading     # execution-lane thread only
    exceeds(total_bytes, ceiling_bytes) -> bool              # total >= ceiling (inclusive)

**Units (T9).**  ``/proc`` reports kB (KiB).  The ONLY ``× 1024`` lives
in the parse helpers below; everything downstream sees **bytes**.  A
kB number labelled as bytes is a defect.

**Stat-line format (test-plan §1.1, pinned).**  ``/proc/<pid>/stat`` is
split with ``rfind(")")`` (the LAST ``)`` — ``comm`` may contain spaces
and parentheses.  After the split the tail starts at field 3, so
``tail[0]``=state · ``tail[1]``=ppid · ``tail[2]``=pgrp (field 5) ·
``tail[19]``=starttime (field 22) · ``tail[21]``=rss_pages (field 24).

**Member states (T8).**  READABLE → value · ZERO (readable, no value
line) → 0, still complete · GONE (ENOENT) → 0, still complete ·
UNREADABLE (read error / unparseable) → 0, ``complete=False``, the pid
is recorded.

**Exclusions.**  Zombies (``state`` starting ``Z``) and kernel-thread
lineage (``ppid`` in {0, 2}) are excluded from the table — the same
exclusions ``list_group_members`` applies, reimplemented here (that
helper is not reused: it hardcodes ``/proc`` and is priced per spawn).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# ── Constants ───────────────────────────────────────────────────────────────

#: The real procfs root.  Every reader takes an INJECTABLE ``proc_root``;
#: this is only the default for production use.
PROC_ROOT = Path("/proc")

#: Metric names carried by :class:`TreeReading`.  The trigger metric is
#: the summed RSS; the CONFIRM metric (the deciding value) is the summed
#: PSS.  ``confirmed_pss_bytes`` — never the RSS peak — is what the
#: verdict records (T3).
METRIC_RSS = "summed_rss_bytes"
METRIC_PSS = "summed_pss_bytes"

SOURCE_RSS = "status:VmRSS"
SOURCE_PSS = "smaps_rollup:Pss"

#: Bytes per kB — the ONLY scale factor in the package (T9).
KB = 1024

#: Bounded descendant-closure caps (arch §2 DQ7: "depth/node caps are
#: module constants, not knobs").  The measured fork-without-exec tree
#: is 9 members (E6); 32 levels / 256 nodes leaves ample headroom while
#: keeping the closure bounded against a pathological tree.
RAM_GUARD_MAX_TREE_DEPTH = 32
RAM_GUARD_MAX_TREE_NODES = 256

#: ``/proc/<pid>/stat`` field offsets AFTER the ``rfind(")")`` split
#: (the tail starts at field 3 — test-plan §1.1).
_STAT_FIELD_STATE = 0
_STAT_FIELD_PPID = 1
_STAT_FIELD_PGRP = 2
_STAT_FIELD_STARTTIME = 19
_STAT_FIELD_RSS_PAGES = 21

# ── Member-state vocabulary (T8) ────────────────────────────────────────────
#: READABLE — a value was read and parsed.
MEMBER_STATE_READABLE = "readable"
#: ZERO — the file parsed but carried no value line (empirically
#: zombies / kernel threads): 0, still complete.
MEMBER_STATE_ZERO = "zero"
#: GONE — ENOENT: 0, still complete.
MEMBER_STATE_GONE = "gone"
#: UNREADABLE — read error or unparseable value: 0, ``complete=False``,
#: the pid is recorded.
MEMBER_STATE_UNREADABLE = "unreadable"


# ── Data shapes (§4.1, exact) ───────────────────────────────────────────────


@dataclass(frozen=True)
class ProcEntry:
    """One live process as seen by the shared scan."""

    pid: int
    ppid: int
    pgrp: int
    state: str


@dataclass(frozen=True)
class ProcTable:
    """The ONE shared per-tick scan of ``/proc``.

    ``scan_complete`` is False when the ENUMERATION itself failed (the
    procfs listing could not be read).  A single unreadable pid is not
    an enumeration failure — that pid is simply absent from the table;
    the per-member UNREADABLE state (T8) is a READING-level fact.
    """

    entries: dict[int, ProcEntry]
    by_pgrp: dict[int, tuple[int, ...]]
    children: dict[int, tuple[int, ...]]
    scan_complete: bool
    #: Pids whose stat could not be read (hidepid / exec-race / EACCES).
    #: Non-empty ⇒ the scan is NOT provably complete even when
    #: ``scan_complete`` is True — member emptiness is unprovable.
    unreadable_pids: tuple[int, ...] = ()


@dataclass(frozen=True)
class MemberSet:
    """The resolved membership of one watched tree.

    ``group_pids`` are the members that share the captured pgid — the
    ones a group signal reaches.  ``descendant_only_pids`` are members
    reached through the descendant closure whose pgid differs (the
    ``setsid`` escapee shape): they are MEASURED but counted in
    ``members_unreachable``, never in ``members_killable``.
    """

    pids: tuple[int, ...]
    group_pids: tuple[int, ...]
    descendant_only_pids: tuple[int, ...]
    leader_present: bool
    leader_state: Optional[str]
    closure_truncated: bool
    enum_complete: bool


@dataclass(frozen=True)
class TreeReading:
    """One measurement of a member set."""

    total_bytes: int
    metric: str
    member_count: int
    member_pids: tuple[int, ...]
    complete: bool
    unreadable_pids: tuple[int, ...]
    zero_pids: tuple[int, ...]
    gone_pids: tuple[int, ...]
    source: str


@dataclass(frozen=True)
class StatRead:
    """The outcome of ONE direct ``/proc/<pid>/stat`` read.

    The P5 gate needs the ABSENT vs UNREADABLE split (arch §14.1
    blocker 1): a leader that is gone (ENOENT) re-anchors the identity
    on the surviving group members, while an UNREADABLE leader is
    fail-closed (``refused:leader-unreadable``).  Conflating the two
    would silently widen the kill authority.
    """

    pid: int
    status: str  # "ok" | "absent" | "unreadable"
    state: Optional[str] = None
    ppid: Optional[int] = None
    pgrp: Optional[int] = None
    start_time_jiffies: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def absent(self) -> bool:
        return self.status == "absent"

    @property
    def unreadable(self) -> bool:
        return self.status == "unreadable"


# ── Low-level parse helpers (the stat parse is REIMPLEMENTED here) ─────────


def parse_stat_line(line: str) -> Optional[tuple[str, int, int, Optional[int], Optional[int]]]:
    """Parse one ``/proc/<pid>/stat`` line.

    Returns ``(state, ppid, pgrp, starttime, rss_pages)`` or ``None``
    when the line is unparseable (the UNREADABLE case).  The split uses
    the LAST ``)`` — ``comm`` may contain spaces and parentheses.
    """
    last_paren = line.rfind(")")
    if last_paren == -1:
        return None
    _, _, tail = line.rpartition(")")
    parts = tail.strip().split()
    if len(parts) <= _STAT_FIELD_PGRP:
        return None
    try:
        state = parts[_STAT_FIELD_STATE]
        ppid = int(parts[_STAT_FIELD_PPID])
        pgrp = int(parts[_STAT_FIELD_PGRP])
    except (ValueError, IndexError):
        return None
    starttime: Optional[int] = None
    if len(parts) > _STAT_FIELD_STARTTIME:
        try:
            starttime = int(parts[_STAT_FIELD_STARTTIME])
        except (ValueError, IndexError):
            starttime = None
    rss_pages: Optional[int] = None
    if len(parts) > _STAT_FIELD_RSS_PAGES:
        try:
            rss_pages = int(parts[_STAT_FIELD_RSS_PAGES])
        except (ValueError, IndexError):
            rss_pages = None
    return state, ppid, pgrp, starttime, rss_pages


def classify_proc_stat_error(exc: BaseException) -> str:
    """Classify a ``/proc/<pid>/stat`` read error into ONE label.

    ``FileNotFoundError`` (and its subclasses ``ProcessLookupError``)
    ⇒ ``"absent"`` (the process is gone).  Any other read/parse error
    ⇒ ``"unreadable"`` (fail-closed — identity unverifiable).

    Single source of truth shared by ``read_stat_entry`` and the bash
    lane so the two can never disagree on the absent-vs-unreadable
    split (ENSEMBLE-32 item 4b).
    """
    if isinstance(exc, FileNotFoundError):
        return "absent"
    return "unreadable"


def read_stat_entry(proc_root: Path, pid: int) -> StatRead:
    """Read ONE ``/proc/<pid>/stat`` — the ABSENT/UNREADABLE split.

    ``ENOENT`` ⇒ absent (the process is gone).  Any other read error or
    an unparseable line ⇒ unreadable (fail-closed).  A zombie (state
    ``Z``) is reported as ``ok`` with ``state="Z"`` — the caller
    decides what a zombie means (P5 treats it as a dead leader).
    """
    path = Path(proc_root) / str(pid) / "stat"
    try:
        line = path.read_text(errors="replace")
    except (OSError, ValueError) as exc:
        return StatRead(pid=pid, status=classify_proc_stat_error(exc))
    parsed = parse_stat_line(line)
    if parsed is None:
        return StatRead(pid=pid, status="unreadable")
    state, ppid, pgrp, starttime, _rss_pages = parsed
    return StatRead(
        pid=pid,
        status="ok",
        state=state,
        ppid=ppid,
        pgrp=pgrp,
        start_time_jiffies=starttime,
    )


def read_start_time(proc_root: Path, pid: int) -> Optional[int]:
    """The leader's starttime (stat field 22, in jiffies) or ``None``."""
    return read_stat_entry(proc_root, pid).start_time_jiffies


def _parse_kb_value(raw: str) -> Optional[int]:
    """Parse a ``<int> kB`` procfs value → bytes.

    The ONLY ``× 1024`` in the package (T9).  ``None`` when the value is
    unparseable (⇒ UNREADABLE).
    """
    parts = raw.split()
    if not parts:
        return None
    try:
        value = int(parts[0])
    except (ValueError, TypeError):
        return None
    return value * KB


# ── The shared scan (T2) ────────────────────────────────────────────────────


def scan_proc(proc_root: Path = PROC_ROOT) -> ProcTable:
    """ONE scan of ``proc_root`` → the per-tick :class:`ProcTable`.

    Called ONCE per tick and serves ALL live spawns: per-spawn scans
    cost 9.49 ms each and overrun the tick (E6, arch §1.1 P9).

    Zombies and kernel-thread lineage (``ppid`` in {0, 2}) are excluded
    — they must never satisfy a membership or liveness gate.
    """
    root = Path(proc_root)
    entries: dict[int, ProcEntry] = {}
    try:
        # The ONE enumeration point: an ENUMERATION FAILURE here is the
        # only thing that clears ``scan_complete`` (a single unreadable
        # pid is NOT an enumeration failure — it is simply absent).
        names = sorted(os.listdir(root))
    except (OSError, ValueError):
        # ENUMERATION FAILURE — the sample is dropped downstream, never
        # a kill on no-evidence (test-plan S6 / N3).
        return ProcTable(entries={}, by_pgrp={}, children={}, scan_complete=False)

    by_pgrp: dict[int, list[int]] = {}
    children: dict[int, list[int]] = {}
    unreadable_pids: list[int] = []
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        stat = read_stat_entry(root, pid)
        if stat.unreadable:
            unreadable_pids.append(pid)
            continue
        if stat.absent:
            continue
        state = stat.state or ""
        ppid = stat.ppid if stat.ppid is not None else -1
        pgrp = stat.pgrp if stat.pgrp is not None else -1
        if state.startswith("Z"):
            continue  # zombie: not a live member
        if ppid in (0, 2):
            continue  # Linux kernel-thread lineage
        entries[pid] = ProcEntry(pid=pid, ppid=ppid, pgrp=pgrp, state=state)
        by_pgrp.setdefault(pgrp, []).append(pid)
        children.setdefault(ppid, []).append(pid)

    return ProcTable(
        entries=entries,
        by_pgrp={pgid: tuple(pids) for pgid, pids in by_pgrp.items()},
        children={ppid: tuple(pids) for ppid, pids in children.items()},
        scan_complete=True,
        unreadable_pids=tuple(unreadable_pids),
    )


# ── Membership resolution (T2) ─────────────────────────────────────────────


def resolve_members(
    table: ProcTable,
    *,
    pgid: int,
    leader_pid: int,
    max_depth: int = RAM_GUARD_MAX_TREE_DEPTH,
    max_nodes: int = RAM_GUARD_MAX_TREE_NODES,
) -> MemberSet:
    """Resolve the watched tree's membership from ONE scan.

    Membership = **group relation ∪ bounded descendant closure from the
    leader** (arch §2 DQ3).  The closure is bounded by ``max_depth``
    (levels) and ``max_nodes`` (members); hitting a bound sets
    ``closure_truncated`` (which makes ``tree_complete`` false).

    The CARRY-FORWARD rule (§4.1) is applied by the GUARD, not here —
    this function keeps its §4.1 signature (the reader contract is not
    re-shaped).
    """
    group_pids = tuple(table.by_pgrp.get(pgid, ()))
    members: list[int] = []
    seen: set[int] = set()
    truncated = False

    # The group relation comes first — the cap bounds the TOTAL
    # membership (the closure is the unbounded part, but the group is
    # bounded by the same cap).
    for pid in group_pids:
        if len(members) >= max_nodes:
            truncated = True
            break
        seen.add(pid)
        members.append(pid)

    frontier = [leader_pid]
    expanded: set[int] = set()
    depth = 0
    while frontier and depth < max_depth and not truncated:
        next_frontier: list[int] = []
        for parent in frontier:
            if parent in expanded:
                continue
            expanded.add(parent)
            for child in table.children.get(parent, ()):
                if child in seen:
                    # Already a member — but it is still part of the
                    # tree, so the closure continues THROUGH it.
                    next_frontier.append(child)
                    continue
                if len(members) >= max_nodes:
                    truncated = True
                    break
                seen.add(child)
                members.append(child)
                next_frontier.append(child)
            if truncated:
                break
        frontier = next_frontier
        depth += 1
    if not truncated and frontier and depth >= max_depth:
        truncated = True

    group_set = set(group_pids)
    descendant_only = tuple(pid for pid in members if pid not in group_set)
    leader_entry = table.entries.get(leader_pid)
    return MemberSet(
        pids=tuple(members),
        group_pids=group_pids,
        descendant_only_pids=descendant_only,
        leader_present=leader_entry is not None,
        leader_state=leader_entry.state if leader_entry is not None else None,
        closure_truncated=truncated,
        enum_complete=table.scan_complete,
    )


# ── The two measurement readers (T2 / T3 / T8) ──────────────────────────────


def read_tree_rss(proc_root: Path, member_pids: Iterable[int]) -> TreeReading:
    """The TRIGGER metric — summed ``VmRSS`` over the member set.

    TICK THREAD ONLY (arch §4.1 / §15 B17: no PSS read on the tick
    path).  ``VmRSS`` is kB in procfs ⇒ converted to bytes here, the
    one place the scale factor lives.
    """
    return _read_tree(
        proc_root,
        member_pids,
        metric=METRIC_RSS,
        source=SOURCE_RSS,
        value_of=_read_status_rss_bytes,
    )


def read_tree_pss(proc_root: Path, member_pids: Iterable[int]) -> TreeReading:
    """The CONFIRM metric — summed ``Pss`` over the member set.

    EXECUTION-LANE THREAD ONLY — this is the deciding value, read once
    at breach under the 250 ms deadline (fail-open ⇒ kill).  It runs
    ONLY on the kill path, never on the tick path.
    """
    return _read_tree(
        proc_root,
        member_pids,
        metric=METRIC_PSS,
        source=SOURCE_PSS,
        value_of=_read_smaps_rollup_pss_bytes,
    )


def _read_tree(
    proc_root: Path,
    member_pids: Iterable[int],
    *,
    metric: str,
    source: str,
    value_of,
) -> TreeReading:
    """Shared reader body — the member-state semantics (T8)."""
    root = Path(proc_root)
    pids = tuple(member_pids)
    total = 0
    unreadable: list[int] = []
    zero: list[int] = []
    gone: list[int] = []
    for pid in pids:
        try:
            value, state = value_of(root, pid)
        except _Unreadable:
            unreadable.append(pid)
            continue
        except FileNotFoundError:
            gone.append(pid)
            continue
        if state == "zero":
            zero.append(pid)
            continue
        total += value
    return TreeReading(
        total_bytes=total,
        metric=metric,
        member_count=len(pids),
        member_pids=pids,
        complete=not unreadable,
        unreadable_pids=tuple(unreadable),
        zero_pids=tuple(zero),
        gone_pids=tuple(gone),
        source=source,
    )


class _Unreadable(Exception):
    """Internal marker — the member's value could not be read/parsed."""


def _read_status_rss_bytes(proc_root: Path, pid: int) -> tuple[int, str]:
    """One member's ``VmRSS`` → bytes.

    READABLE → the value · ZERO → the file parsed but carries no
    ``VmRSS:`` line (empirically zombies / kernel threads) · GONE →
    ENOENT · UNREADABLE → read error or unparseable value.
    """
    path = proc_root / str(pid) / "status"
    try:
        text = path.read_text(errors="replace")
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise _Unreadable(str(exc)) from exc
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            raw = line.split(":", 1)[1].strip()
            value = _parse_kb_value(raw)
            if value is None:
                raise _Unreadable(f"unparseable VmRSS for pid {pid}")
            return value, "readable"
    return 0, "zero"


def _read_smaps_rollup_pss_bytes(proc_root: Path, pid: int) -> tuple[int, str]:
    """One member's ``smaps_rollup`` ``Pss:`` → bytes."""
    path = proc_root / str(pid) / "smaps_rollup"
    try:
        text = path.read_text(errors="replace")
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise _Unreadable(str(exc)) from exc
    for line in text.splitlines():
        if line.startswith("Pss:"):
            raw = line.split(":", 1)[1].strip()
            value = _parse_kb_value(raw)
            if value is None:
                raise _Unreadable(f"unparseable Pss for pid {pid}")
            return value, "readable"
    return 0, "zero"


# ── The breach comparison (T5 / T9) ─────────────────────────────────────────


def exceeds(total_bytes: int, ceiling_bytes: int) -> bool:
    """``total >= ceiling`` — INCLUSIVE (arch §4.1).

    The trigger fires on the CURRENT sample, not on the peak.
    """
    return total_bytes >= ceiling_bytes


__all__ = [
    "KB",
    "MEMBER_STATE_GONE",
    "MEMBER_STATE_READABLE",
    "MEMBER_STATE_UNREADABLE",
    "MEMBER_STATE_ZERO",
    "METRIC_PSS",
    "METRIC_RSS",
    "PROC_ROOT",
    "RAM_GUARD_MAX_TREE_DEPTH",
    "RAM_GUARD_MAX_TREE_NODES",
    "MemberSet",
    "ProcEntry",
    "ProcTable",
    "SOURCE_PSS",
    "SOURCE_RSS",
    "StatRead",
    "TreeReading",
    "exceeds",
    "parse_stat_line",
    "read_start_time",
    "read_stat_entry",
    "read_tree_pss",
    "read_tree_rss",
    "resolve_members",
    "scan_proc",
]
