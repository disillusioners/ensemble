"""P2 T4 deep-scan reconcile tests (phase2-plan AC2 + R2 pacing).

Gates (AC2, exact):

* node cap ⇒ ``partial_walk=True`` + bounded node count;
* id-memoisation counts shared substructure exactly once;
* 1 instance/tick round-robin pacing.

FENCE DISCIPLINE — the 2026-10-12 live-install-deletion incident class
(project critical note: test scripts must NEVER resolve live-shaped
paths):

* ``FAKE_HOME`` is a pytest ``tmp_path`` directory installed as
  ``$HOME``; nothing under the real HOME is ever read.
* The walk consumes PURELY synthetic in-memory objects (dict trees, a
  tiny compiled langgraph). No /proc, no filesystem, no manager import
  — the instances mapping is injected, so there is no live-path
  resolution surface at all (asserted by an escape-guard test).

Run with ``.venv/bin/python -m pytest tests/test_resource_deep_scan.py -q``.
"""

from __future__ import annotations

import os
import sys

import pytest

from daemon.services.resource_monitor import charge
from daemon.services.resource_monitor.charge import bounded_deep_size
from daemon.services.resource_monitor.deep_scan import (
    SCAN_KIND_DEEP,
    DeepScanResult,
    DeepScanner,
)


# ──────────────────────────────────────────────────────────────────────────
# Fence (session-scoped) — FAKE_HOME + env scrub, mirrors the P1 packs.
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session(tmp_path_factory):
    fake_home = tmp_path_factory.mktemp("fake_home_deep_scan")
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    saved = {
        k: os.environ.get(k)
        for k in list(os.environ)
        if k.startswith("POSTGRES_")
    }
    for k in saved:
        os.environ.pop(k, None)  # ambient PG must never steer tests
    yield
    os.environ["HOME"] = real_home or ""
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _charge_sandbox():
    charge.reset()
    charge.set_enabled(False)
    yield
    charge.reset()
    charge.set_enabled(False)


def _deep_tree(width: int = 200, depth: int = 4) -> dict:
    """Synthetic object tree comfortably over any small node cap."""
    leaf = {"payload": "p" * 64}
    layer = leaf
    for _ in range(depth):
        layer = {f"n{i}": layer for i in range(width)}
    return layer


def _instances(*ids: str) -> dict:
    return {iid: ({"graph_payload": "g" * 32, iid: [1, 2, 3]}, f"/agents/{iid}")
            for iid in ids}


# ──────────────────────────────────────────────────────────────────────────
# AC2 — node cap ⇒ partial_walk=True + bounded node count
# ──────────────────────────────────────────────────────────────────────────


class TestNodeCap:
    def test_cap_trips_partial_and_bounds_nodes(self):
        scanner = DeepScanner(node_cap=25)
        tree = _deep_tree()
        result = scanner.scan_next({"inst-1": (tree, "/agents/inst-1")})
        assert result is not None
        assert result.partial_walk is True
        assert result.nodes_visited <= 25
        assert result.deep_bytes > 0
        assert result.scan_kind == SCAN_KIND_DEEP

    def test_small_tree_does_not_trip_cap(self):
        scanner = DeepScanner(node_cap=10_000)
        tree = {"a": ["b" * 10]}
        result = scanner.scan_next({"inst-1": (tree, "/agents/inst-1")})
        assert result.partial_walk is False
        assert result.nodes_visited <= 10_000
        assert result.deep_bytes == bounded_deep_size(tree)[0]

    def test_depth_cap_marks_partial_on_container_skip(self):
        deep = cur = {}
        for i in range(40):
            cur["next"] = {}
            cur = cur["next"]
        scanner = DeepScanner(node_cap=10_000, depth_cap=5)
        result = scanner.scan_next({"inst-1": (deep, "/agents/inst-1")})
        assert result.partial_walk is True  # containers skipped at depth 5


# ──────────────────────────────────────────────────────────────────────────
# AC2 — memoisation counts shared substructure once
# ──────────────────────────────────────────────────────────────────────────


class TestMemoisation:
    def test_shared_substructure_counted_once(self):
        shared = {"x": "y" * 50}
        obj = {"a": shared, "b": shared}
        scanner = DeepScanner(node_cap=10_000)
        result = scanner.scan_next({"inst-1": (obj, "/agents/inst-1")})
        # root + "a" + "b" + shared + "x" + str = 6 memoised
        # (8 if the shared dict were walked twice)
        assert result.nodes_visited == 6
        expected = (
            sys.getsizeof(obj)
            + sys.getsizeof("a")
            + sys.getsizeof("b")
            + sys.getsizeof(shared)
            + sys.getsizeof("x")
            + sys.getsizeof("y" * 50)
        )
        assert result.deep_bytes == expected

    def test_memoisation_prevents_exponential_rewalk(self):
        # diamond: one object fanned out through 3 levels ⇒ naive walk
        # would visit it 8×, memoised walk visits it once per unique
        # id (root + 3 fans + shared + leaves).
        shared = {"leaf": "l" * 32}
        mid1 = {"m": shared}
        mid2 = {"m": shared}
        obj = {"p": mid1, "q": mid2, "r": shared}
        scanner = DeepScanner(node_cap=10_000)
        result = scanner.scan_next({"inst-1": (obj, "/agents/inst-1")})
        assert result.nodes_visited == bounded_deep_size(obj)[1]

    def test_cycle_terminates(self):
        a: dict = {}
        b = {"a": a}
        a["b"] = b  # cycle
        scanner = DeepScanner(node_cap=10_000)
        result = scanner.scan_next({"inst-1": (a, "/agents/inst-1")})
        assert result is not None
        assert result.nodes_visited <= 10_000


# ──────────────────────────────────────────────────────────────────────────
# AC2 — 1 instance/tick round-robin pacing
# ──────────────────────────────────────────────────────────────────────────


class TestTickPacing:
    def test_one_instance_per_call_in_insertion_order(self):
        scanner = DeepScanner()
        instances = _instances("i1", "i2", "i3")
        seen = [scanner.scan_next(instances).instance_id for _ in range(3)]
        assert seen == ["i1", "i2", "i3"]
        assert scanner.scans_completed == 3

    def test_cursor_wraps(self):
        scanner = DeepScanner()
        instances = _instances("i1", "i2")
        seq = [scanner.scan_next(instances).instance_id for _ in range(4)]
        assert seq == ["i1", "i2", "i1", "i2"]

    def test_many_instances_still_one_per_tick(self):
        scanner = DeepScanner()
        instances = _instances(*[f"inst-{n}" for n in range(70)])
        seen = []
        for _ in range(10):
            result = scanner.scan_next(instances)
            assert result is not None
            seen.append(result.instance_id)
        assert len(set(seen)) == 10  # 10 ticks ⇒ 10 DISTINCT instances
        assert scanner.scans_completed == 10

    def test_empty_mapping_returns_none_and_resets_cursor(self):
        scanner = DeepScanner()
        assert scanner.scan_next(_instances("i1", "i2", "i3")) is not None
        assert scanner.scan_next({}) is None
        # cursor reset ⇒ a fresh population starts from its first key
        fresh = _instances("f1", "f2")
        assert scanner.scan_next(fresh).instance_id == "f1"


class TestW2EvictionRace:
    """N-2 (P2 review): the keyed dict access in ``scan_next`` sat
    OUTSIDE the walk-try — a manager-cache eviction racing the
    sampler raised KeyError and discarded the WHOLE tick's
    attribution. A missing instance must degrade to a skip (None),
    with the cursor already advanced so the scan retries later."""

    def test_missing_instance_degrades_to_skip(self):
        scanner = DeepScanner()

        class _EvictingMapping:
            """keys() includes the id; get() finds nothing — the
            eviction raced between the caller's snapshot and the
            dict access."""

            def __init__(self, inner):
                self._inner = inner

            def keys(self):
                return self._inner.keys()

            def get(self, key, default=None):
                return None

            def __getitem__(self, key):
                return self._inner[key]

        scanner.scan_next(_instances("warm-1"))  # cursor mid-stream
        result = scanner.scan_next(_EvictingMapping(_instances("gone-1")))
        assert result is None, "evicted instance must skip, not raise"
        assert scanner.scans_completed == 1  # unchanged by the skip
        # The scanner survives: the next REAL tick still reconciles.
        follow = scanner.scan_next(_instances("fresh-1"))
        assert follow is not None
        assert scanner.scans_completed == 2


# ──────────────────────────────────────────────────────────────────────────
# Correction lands in the charge registry (T4 output contract)
# ──────────────────────────────────────────────────────────────────────────


class TestCorrectionWrites:
    def test_deep_bytes_written_into_charge_record(self):
        charge.set_enabled(True)
        charge.note_message_append("inst-1", {"m": "x" * 100})
        scanner = DeepScanner(node_cap=10_000)
        tree = {"graph": "g" * 500}
        result = scanner.scan_next({"inst-1": (tree, "/agents/inst-1")})
        snap = charge.snapshot_instance("inst-1")
        assert snap["deep_bytes"] == result.deep_bytes
        assert snap["deep_partial"] == result.partial_walk
        assert snap["deep_scans"] == 1
        # the reconcile does NOT blend into charged_bytes (D13: the
        # sampler owns the coverage math)
        charged_before = snap["charged_bytes"]
        scanner.scan_next({"inst-1": (tree, "/agents/inst-1")})
        snap2 = charge.snapshot_instance("inst-1")
        assert snap2["charged_bytes"] == charged_before
        assert snap2["deep_scans"] == 2

    def test_scan_works_with_charge_disabled(self):
        # registry disabled ⇒ the write is a no-op, but the sampler
        # still gets its result object (layering: scan ≠ charge).
        scanner = DeepScanner(node_cap=10_000)
        result = scanner.scan_next({"inst-1": ({"a": "b" * 10}, "/agents/x")})
        assert result is not None
        assert charge.snapshot_all() == {}

    def test_result_shape_scalar_only_d14(self):
        scanner = DeepScanner()
        result = scanner.scan_next(_instances("inst-1")["inst-1"] and _instances("inst-1"))
        assert isinstance(result, DeepScanResult)
        assert set(result.__dict__) <= {
            "instance_id",
            "deep_bytes",
            "nodes_visited",
            "partial_walk",
            "agent_dir",
            "scan_kind",
        }
        assert result.agent_dir == "/agents/inst-1"


# ──────────────────────────────────────────────────────────────────────────
# Realism + safety
# ──────────────────────────────────────────────────────────────────────────


class TestRealismAndSafety:
    def test_real_compiled_langgraph_walk_is_bounded(self):
        from langgraph.graph import END, StateGraph

        builder = StateGraph(dict)
        builder.add_node("echo", lambda state: {"echoed": True})
        builder.add_edge("echo", END)
        builder.set_entry_point("echo")
        graph = builder.compile()
        scanner = DeepScanner()  # production caps
        result = scanner.scan_next({"inst-real": (graph, "/agents/inst-real")})
        assert result is not None
        assert result.deep_bytes > 0
        assert result.nodes_visited <= scanner.node_cap

    def test_hostile_object_never_raises(self):
        class _HostileDict(dict):
            def keys(self):
                raise RuntimeError("mutated during iteration")

        scanner = DeepScanner(node_cap=100)
        # must not raise — walker marks partial and continues
        result = scanner.scan_next(
            {"inst-1": (_HostileDict({"a": 1}), "/agents/inst-1")}
        )
        assert result is None or result.partial_walk is True

    def test_walk_failure_degrades_to_partial_not_raise(self):
        class _HostileObj:
            @property
            def __dict__(self):
                raise RuntimeError("no dict for you")

        scanner = DeepScanner(node_cap=10)
        result = scanner.scan_next({"inst-1": (_HostileObj(), "/x")})
        # the shell object is measured; the hostile attribute dict is
        # skipped ⇒ graceful partial result, never a raise into the
        # sampler tick (R2)
        assert result is not None
        assert result.partial_walk is True
        assert result.deep_bytes > 0

    def test_walk_crash_returns_none_not_raise(self):
        class _ExplodingType:
            """A type whose getsizeof itself blows up — the only path
            that turns into a scan_next()``None`` (retried next tick)."""

            def __sizeof__(self):
                raise RuntimeError("sizeof boom")

        scanner = DeepScanner(node_cap=10)
        result = scanner.scan_next({"inst-1": (_ExplodingType(), "/x")})
        assert result is None  # retried on a later tick, never raised

    def test_no_live_path_surface_escape_guard(self):
        """Escape-guard (2026-10-12 class): the deep-scan module must
        hold NO live-path resolution surface — no /proc, no HOME
        expansion, no file reads, no manager import. The instances
        mapping is injected; nothing is ever read from disk."""
        import inspect

        from daemon.services.resource_monitor import deep_scan

        src = inspect.getsource(deep_scan)
        assert "/proc" not in src
        assert "expanduser" not in src
        assert "open(" not in src
        assert "from daemon.manager" not in src
        assert "import manager" not in src
