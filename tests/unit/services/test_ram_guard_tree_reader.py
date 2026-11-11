"""Substrate tier — the tree reader (test-plan §3.1, cases S1–S5, S9, S15).

PURE-PREDICATE: no real subprocesses, no real RAM, no host ``/proc``
reads beyond the fake trees.  The fixture's stat-line format is pinned
in ``ram_guard_fixtures`` (test-plan §1.1).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daemon.services.ram_guard import tree_reader
from daemon.services.ram_guard.tree_reader import (
    METRIC_PSS,
    METRIC_RSS,
    exceeds,
    parse_stat_line,
    read_tree_pss,
    read_tree_rss,
    resolve_members,
    scan_proc,
)
from tests.unit.services.ram_guard_fixtures import FakeProc, write_proc_tree

PACKAGE_DIR = Path(tree_reader.__file__).resolve().parent


# ── S1 — a single-process tree reading ─────────────────────────────────────


def test_s1_single_process_tree_reading_is_bytes(tmp_path):
    """S1 — one member's VmRSS is reported in BYTES."""
    root = write_proc_tree(
        tmp_path / "proc",
        [FakeProc(pid=100, rss_kb=1024, pss_kb=512)],
    )
    reading = read_tree_rss(root, [100])
    assert reading.total_bytes == 1024 * 1024
    assert reading.metric == METRIC_RSS
    assert reading.member_count == 1
    assert reading.complete is True
    assert reading.unreadable_pids == ()
    assert reading.zero_pids == ()
    assert reading.gone_pids == ()


# ── S2 — a tree of children ────────────────────────────────────────────────


def test_s2_tree_of_children_is_summed(tmp_path):
    """S2 — the sum follows the metric's semantics."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=100, pgrp=500, rss_kb=1000, pss_kb=500),
            FakeProc(pid=101, ppid=100, pgrp=500, rss_kb=2000, pss_kb=1000),
            FakeProc(pid=102, ppid=101, pgrp=500, rss_kb=3000, pss_kb=1500),
        ],
    )
    reading = read_tree_rss(root, [100, 101, 102])
    assert reading.total_bytes == (1000 + 2000 + 3000) * 1024
    assert reading.member_count == 3
    assert reading.complete is True

    pss = read_tree_pss(root, [100, 101, 102])
    assert pss.total_bytes == (500 + 1000 + 1500) * 1024
    assert pss.metric == METRIC_PSS


# ── S3 — the shared-page double-count semantics ────────────────────────────


def test_s3_shared_pages_are_double_counted_by_the_rss_metric(tmp_path):
    """S3 — the over-count shape is pinned: RSS counts a shared page twice.

    The 8.9× over-count on a 9-member fork tree (E6) is exactly this
    shape: the summed RSS metric double-counts shared pages while the
    summed PSS does not.  The test pins the SEMANTICS so they cannot
    drift silently.
    """
    members = 9
    root = write_proc_tree(
        tmp_path / "proc",
        [
            # Each member holds 890 kB of RSS but only 100 kB of PSS —
            # the shared-page shape measured on a 9-member fork tree.
            FakeProc(pid=100 + i, pgrp=500, rss_kb=890, pss_kb=100)
            for i in range(members)
        ],
    )
    rss = read_tree_rss(root, list(range(100, 100 + members)))
    pss = read_tree_pss(root, list(range(100, 100 + members)))
    assert rss.total_bytes == members * 890 * 1024
    assert pss.total_bytes == members * 100 * 1024
    # The RSS metric over-counts the shared total by the measured factor.
    assert rss.total_bytes / pss.total_bytes == pytest.approx(8.9, rel=1e-9)


# ── S4 — an unreadable member ──────────────────────────────────────────────


def test_s4_unreadable_member_is_recorded_and_incomplete(tmp_path):
    """S4 — never a silent 0: the pid is recorded and complete is False."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=100, rss_kb=1000),
            FakeProc(pid=101, status_state="unreadable"),
        ],
    )
    reading = read_tree_rss(root, [100, 101])
    assert reading.unreadable_pids == (101,)
    assert reading.complete is False
    # The unreadable member contributes 0 — but the reading says so.
    assert reading.total_bytes == 1000 * 1024


# ── S5 — the member-state semantics ────────────────────────────────────────


def test_s5_member_states_readable_zero_gone_unreadable(tmp_path):
    """S5 — READABLE / ZERO / GONE / UNREADABLE, each with its own outcome."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=100, rss_kb=1000),                      # READABLE
            FakeProc(pid=101, rss_kb=None),                     # ZERO
            FakeProc(pid=102, status_state="gone"),             # GONE
            FakeProc(pid=103, status_state="unreadable"),       # UNREADABLE
        ],
    )
    reading = read_tree_rss(root, [100, 101, 102, 103])
    assert reading.total_bytes == 1000 * 1024
    assert reading.zero_pids == (101,)
    assert reading.gone_pids == (102,)
    assert reading.unreadable_pids == (103,)
    # ZERO and GONE are still complete; only UNREADABLE breaks it.
    assert reading.complete is False
    assert reading.member_count == 4


def test_s5_zero_and_gone_alone_are_complete(tmp_path):
    """S5 — ZERO and GONE keep the reading COMPLETE."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=101, rss_kb=None),
            FakeProc(pid=102, status_state="gone"),
        ],
    )
    reading = read_tree_rss(root, [101, 102])
    assert reading.complete is True
    assert reading.total_bytes == 0


# ── S9 — the breach boundary ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("total", "ceiling", "expected"),
    [
        (1023, 1024, False),
        (1024, 1024, True),
        (1025, 1024, True),
    ],
)
def test_s9_breach_boundary_is_inclusive(total, ceiling, expected):
    """S9 — the breach fires iff ``total >= ceiling`` (inclusive)."""
    assert exceeds(total, ceiling) is expected


# ── S15 — the unit discipline ──────────────────────────────────────────────


def test_s15_kb_is_converted_to_bytes_exactly_once(tmp_path):
    """S15 — ``1024 kB`` ⇒ ``1 048 576 bytes``; downstream sees BYTES."""
    root = write_proc_tree(tmp_path / "proc", [FakeProc(pid=100, rss_kb=1024)])
    reading = read_tree_rss(root, [100])
    assert reading.total_bytes == 1048576
    # The metric name says bytes; the value is bytes.
    assert reading.metric == METRIC_RSS
    assert reading.total_bytes % 1024 == 0


# ── The stat-line format (test-plan §1.1) ─────────────────────────────────


def test_stat_line_split_uses_the_last_paren(tmp_path):
    """A comm with spaces AND parentheses must still parse."""
    proc = FakeProc(pid=100, comm="weird (name) here", pgrp=500, starttime=4242)
    root = write_proc_tree(tmp_path / "proc", [proc])
    parsed = parse_stat_line((root / "100" / "stat").read_text())
    assert parsed is not None
    state, ppid, pgrp, starttime, rss_pages = parsed
    assert state == "S"
    assert ppid == proc.ppid
    assert pgrp == 500
    assert starttime == 4242
    assert rss_pages == proc.rss_pages


def test_scan_excludes_zombies_and_kernel_threads(tmp_path):
    """Zombies and kernel-thread lineage are excluded from the table."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=100, pgrp=500),
            FakeProc(pid=101, pgrp=500, state="Z"),        # zombie
            FakeProc(pid=102, pgrp=500, ppid=2),            # kernel thread
            FakeProc(pid=103, pgrp=500, ppid=0),            # kernel thread
        ],
    )
    table = scan_proc(root)
    assert set(table.entries) == {100}
    assert table.by_pgrp[500] == (100,)


def test_scan_enumeration_failure_is_not_complete(tmp_path):
    """A missing procfs root ⇒ ``scan_complete=False`` (the sample is dropped)."""
    table = scan_proc(tmp_path / "no-such-proc")
    assert table.scan_complete is False
    assert table.entries == {}


# ── Membership resolution ──────────────────────────────────────────────────


def test_resolve_members_is_group_union_bounded_closure(tmp_path):
    """Membership = the group relation ∪ the bounded descendant closure."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=100, pgrp=500),                 # the leader
            FakeProc(pid=101, ppid=100, pgrp=500),       # in the group
            FakeProc(pid=102, ppid=101, pgrp=500),       # in the group
            FakeProc(pid=103, ppid=102, pgrp=900),       # a setsid escapee
            FakeProc(pid=200, pgrp=700),                 # unrelated
        ],
    )
    table = scan_proc(root)
    members = resolve_members(table, pgid=500, leader_pid=100, max_depth=8, max_nodes=64)
    assert set(members.pids) == {100, 101, 102, 103}
    assert set(members.group_pids) == {100, 101, 102}
    assert set(members.descendant_only_pids) == {103}
    assert members.leader_present is True
    assert members.closure_truncated is False


def test_resolve_members_marks_a_truncated_closure(tmp_path):
    """Hitting the node cap marks the closure truncated (tree_complete False)."""
    root = write_proc_tree(
        tmp_path / "proc",
        [FakeProc(pid=100, pgrp=500)]
        + [FakeProc(pid=200 + i, ppid=100 + i - 1, pgrp=500) for i in range(1, 6)],
    )
    table = scan_proc(root)
    members = resolve_members(table, pgid=500, leader_pid=100, max_depth=8, max_nodes=3)
    assert members.closure_truncated is True
    assert len(members.pids) == 3


# ── The import freeze (arch §7 / §15 B7) ───────────────────────────────────


def test_package_imports_nothing_from_daemon():
    """``ram_guard`` imports NOTHING from ``daemon/`` — asserted on the AST.

    The assertion target is the import-freeze SENTENCE, not
    ``daemon/tools/*`` alone: no ``tools``, no ``resource_monitor``, no
    ``repositories``, no ``event_bus``, no ``main_loop_bridge``, no
    ``bash_spawn_reaper`` — and nothing else from ``daemon/`` either.
    """
    forbidden = {
        "tools",
        "resource_monitor",
        "repositories",
        "event_bus",
        "main_loop_bridge",
        "bash_spawn_reaper",
    }
    offenders: list[str] = []
    for source in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # ``from .x import y`` ⇒ the relative module x; an
                # absolute ``from daemon...`` ⇒ the daemon package.
                if node.level == 0 and node.module:
                    names = [node.module]
            for name in names:
                head = name.split(".")[0]
                if head == "daemon":
                    offenders.append(f"{source.name}: {name}")
                if name.split(".")[0] in forbidden:
                    offenders.append(f"{source.name}: {name}")
    assert offenders == [], f"import-freeze violations: {offenders}"


def test_package_module_decomposition_is_frozen():
    """The §7 decomposition — the seven in-package modules, no more."""
    names = {path.stem for path in PACKAGE_DIR.glob("*.py")}
    assert names == {
        "__init__",
        "ceiling",
        "errors",
        "events",
        "guard",
        "ladder",
        "tree_reader",
        "verdict",
    }
    # The emitter implementation lives OUTSIDE the package.
    outside = PACKAGE_DIR.parent / "ram_guard_events.py"
    assert outside.exists()
