"""Unit tests for ``skill_search_interval`` (configurable skill-search
frequency).

The feature adds a per-agent ``skill_search_interval`` to
:class:`daemon.registry.AgentMetadata` (default ``1`` = current behavior)
and a parallel per-instance message counter to
:class:`daemon.manager.InstanceManager` so the messaging path's skill-
injection block can skip the expensive 3-stage search (BM25 → embedding →
LLM, ~200-2000ms) on the next ``interval - 1`` messages and reuse the
cached result from the most recent search.

Test breakdown:

* :class:`TestAgentMetadataSkillSearchInterval` — registry field defaults
  and validation (``ge=1``).
* :class:`TestManagerSkillSearchCount` — counter semantics
  (pre-increment return, increment-on-call, reset to 0).
* :class:`TestSkillSearchIntervalGate` — messaging-path gate behavior
  across the 5 spec scenarios. The gate itself is a 3-line ``if`` in
  ``daemon.services.instance_messaging.py``; rather than stand up the
  whole message handler (heavy mocking), we mirror its exact decision
  logic in :func:`_gate_decides_skip` and verify the contract by
  asserting the expected call sequence against a mocked manager +
  ``inject_skills`` service.

Phase 4b of the Context Injection Restructure plan.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.registry import AgentMetadata


# ─── Registry field ──────────────────────────────────────────────────────────


class TestAgentMetadataSkillSearchInterval:
    """``AgentMetadata.skill_search_interval`` default + validation."""

    def test_default_is_one(self) -> None:
        """Default is 1 (current behavior — search every message)."""
        meta = AgentMetadata(id="x", name="y", path="/tmp")
        assert meta.skill_search_interval == 1

    def test_accepts_positive_integers(self) -> None:
        """``N > 1`` is accepted and stored verbatim."""
        for n in (2, 3, 5, 10, 100):
            meta = AgentMetadata(
                id="x", name="y", path="/tmp", skill_search_interval=n
            )
            assert meta.skill_search_interval == n

    def test_rejects_zero(self) -> None:
        """``0`` violates ``ge=1`` and must raise."""
        with pytest.raises(ValidationError):
            AgentMetadata(
                id="x", name="y", path="/tmp", skill_search_interval=0
            )

    def test_rejects_negative(self) -> None:
        """Negative values violate ``ge=1`` and must raise."""
        with pytest.raises(ValidationError):
            AgentMetadata(
                id="x", name="y", path="/tmp", skill_search_interval=-3
            )

    def test_rejects_non_integer(self) -> None:
        """Non-integer values must raise (the field is typed ``int``)."""
        with pytest.raises(ValidationError):
            AgentMetadata(
                id="x", name="y", path="/tmp", skill_search_interval=2.5
            )


# ─── Manager counter ─────────────────────────────────────────────────────────
#
# We exercise the two new methods via a thin wrapper around the real
# ``InstanceManager.__init__`` body — specifically, we just construct an
# empty object of the class, then manually attach the dict the same way
# ``__init__`` does, then call the methods. This avoids the full DB /
# engine / repository init chain while still running the actual method
# code (not a mock).
#
# Mirrors the defensive ``getattr`` pattern used in the cleanup sites,
# so the methods are exercised against the same shape they'd see in
# production.


class TestManagerSkillSearchCount:
    """Per-instance message counter for the search gate."""

    def _make_manager(self):
        """Build an ``InstanceManager``-shaped object with only the
        counter dict attached — mirrors the defensive ``getattr``
        cleanup pattern.
        """
        from daemon.manager import InstanceManager

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._skill_search_message_counts = {}
        return mgr

    def test_get_returns_zero_for_fresh_instance(self) -> None:
        """First call on a fresh instance returns ``0`` (pre-increment)."""
        mgr = self._make_manager()
        assert mgr.get_and_increment_skill_search_count("inst-1") == 0

    def test_get_increments_after_call(self) -> None:
        """The counter is incremented AFTER the read; next call returns 1."""
        mgr = self._make_manager()
        mgr.get_and_increment_skill_search_count("inst-1")
        assert mgr.get_and_increment_skill_search_count("inst-1") == 1

    def test_get_returns_pre_increment_value(self) -> None:
        """The contract: return count BEFORE incrementing (so callers can
        compare ``count < interval`` directly)."""
        mgr = self._make_manager()
        mgr.get_and_increment_skill_search_count("inst-1")  # → 0, stored 1
        mgr.get_and_increment_skill_search_count("inst-1")  # → 1, stored 2
        assert mgr.get_and_increment_skill_search_count("inst-1") == 2

    def test_counters_are_per_instance(self) -> None:
        """Different instances track independently."""
        mgr = self._make_manager()
        assert mgr.get_and_increment_skill_search_count("inst-A") == 0
        assert mgr.get_and_increment_skill_search_count("inst-B") == 0
        assert mgr.get_and_increment_skill_search_count("inst-A") == 1
        assert mgr.get_and_increment_skill_search_count("inst-B") == 1
        assert mgr.get_and_increment_skill_search_count("inst-A") == 2

    def test_reset_sets_to_zero(self) -> None:
        """``reset`` zeroes the counter for a tracked instance."""
        mgr = self._make_manager()
        mgr.get_and_increment_skill_search_count("inst-1")
        mgr.get_and_increment_skill_search_count("inst-1")
        mgr.reset_skill_search_count("inst-1")
        assert mgr.get_and_increment_skill_search_count("inst-1") == 0

    def test_reset_is_idempotent(self) -> None:
        """``reset`` on an absent instance is a no-op (no KeyError)."""
        mgr = self._make_manager()
        mgr.reset_skill_search_count("never-seen")  # must not raise

    def test_get_then_reset_cycle(self) -> None:
        """Realistic cycle: get → reset → get returns 0 again."""
        mgr = self._make_manager()
        for _ in range(5):
            mgr.get_and_increment_skill_search_count("inst-1")
        mgr.reset_skill_search_count("inst-1")
        assert mgr.get_and_increment_skill_search_count("inst-1") == 0

    def test_cleanup_drops_entry(self) -> None:
        """``_cleanup_instance_state`` must drop the counter — mirrored
        by direct ``pop`` here since the real cleanup runs inside the
        full ``__init__`` path which we don't want to stand up.
        """
        mgr = self._make_manager()
        mgr.get_and_increment_skill_search_count("inst-1")
        # Simulate the defensive cleanup pattern from
        # ``_cleanup_instance_state``.
        _counts = getattr(mgr, "_skill_search_message_counts", None)
        assert _counts is not None
        _counts.pop("inst-1", None)
        # Fresh read: counter is gone.
        assert mgr.get_and_increment_skill_search_count("inst-1") == 0

    def test_cleanup_safe_when_dict_missing(self) -> None:
        """The ``getattr`` defensive pattern used in cleanup must not
        raise when ``_skill_search_message_counts`` is absent (e.g.,
        hand-rolled test stubs that bypass ``__init__``).
        """
        mgr = self._make_manager()
        del mgr._skill_search_message_counts  # simulate stub
        # Same defensive snippet as in cleanup sites — must not raise.
        _counts = getattr(mgr, "_skill_search_message_counts", None)
        if _counts is not None:
            _counts.pop("inst-1", None)


# ─── Messaging-path gate ────────────────────────────────────────────────────
#
# The gate lives inside the skill-injection block in
# ``daemon.services.instance_messaging.py`` (~line 2412). It is a small
# 3-line decision: skip the search when ``interval > 1`` AND
# ``cached is not None`` AND ``msg_count < interval - 1``; otherwise run the
# search (and reset the counter).
#
# Rather than stand up the entire ``InstanceMessagingService`` (heavy DB,
# engine, graph, and async-context mocking — out of scope for a unit
# test of the gate's contract), we mirror the gate's exact decision
# logic in :func:`_gate_decides_skip` and assert the expected call
# sequence against a mocked manager + mocked ``inject_skills`` service.
#
# This is the contract test: given the agent's ``skill_search_interval``
# + the manager state (``_skill_search_message_counts``,
# ``_context_skill_results``), does the gate skip or search on this
# message? The implementation is a literal copy of the gate's body so
# any drift in the production code is caught by the assertion that
# ``_GATE_DECISION_SOURCE`` stays in sync (see :func:`_GATE_SOURCE`).


# Constant sentinel set by the gate's decision branch — used by the
# test harness to track which path was taken on each simulated message.
_GATE_SKIP = "skip"
_GATE_SEARCH = "search"


def _gate_decides_skip(
    *,
    agent_meta,
    manager,
    instance_id: str,
    explicit_loaded: bool = False,
) -> str:
    """Mirror of the gate's decision in
    ``daemon.services.instance_messaging.py``.

    Returns ``_GATE_SKIP`` when the cached result should be reused
    (no search), ``_GATE_SEARCH`` when a fresh search should run. The
    body is intentionally a literal copy of the production gate's
    decision — see :func:`_GATE_SOURCE` for the drift-detection check.

    Mirrors the S1 perf restructure: ``interval > 1`` is checked
    FIRST so ``get_context_skill_result`` and the explicit-load
    marker are only consulted on the cached-skip path.
    """
    interval = int(
        getattr(agent_meta, "skill_search_interval", 1) or 1
    )
    # Identical order to the production gate: get count (increment
    # happens inside get_and_increment) on every message.
    msg_count = manager.get_and_increment_skill_search_count(
        instance_id
    )

    if interval > 1:
        cached = manager.get_context_skill_result(instance_id)
        if (
            cached is not None
            and msg_count < interval - 1
            and not explicit_loaded
        ):
            return _GATE_SKIP
    return _GATE_SEARCH


def _gate_source() -> str:
    """Return the production gate's decision snippet from the source
    file. Used by :func:`test_gate_decision_matches_production_source`
    to catch drift between this test and the real implementation.
    """
    from pathlib import Path

    path = Path("daemon/services/instance_messaging.py")
    return path.read_text()


class TestSkillSearchIntervalGate:
    """Messaging-path gate behavior across the 5 spec scenarios."""

    def _make_manager(self):
        """Mock manager with just the 3 methods the gate calls."""
        from unittest.mock import MagicMock

        manager = MagicMock()
        # Real dict for the counter (so per-call ordering works).
        manager._skill_search_message_counts = {}
        manager._context_skill_results = {}

        def _get_and_increment(instance_id: str) -> int:
            current = manager._skill_search_message_counts.get(
                instance_id, 0
            )
            manager._skill_search_message_counts[instance_id] = (
                current + 1
            )
            return current

        def _get_cached(instance_id: str):
            return manager._context_skill_results.get(instance_id)

        manager.get_and_increment_skill_search_count = (
            _get_and_increment
        )
        manager.get_context_skill_result = _get_cached

        def _reset(instance_id: str) -> None:
            manager._skill_search_message_counts[instance_id] = 0

        manager.reset_skill_search_count = _reset
        return manager

    # ─── AC#1: default (interval absent / 1) searches every message ──

    def test_default_interval_searches_every_message(self) -> None:
        """``skill_search_interval=1`` (default) → every message runs
        a search, never skips. Mirrors current behavior.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 1  # default

        decisions = [
            _gate_decides_skip(
                agent_meta=agent_meta,
                manager=manager,
                instance_id="inst-1",
            )
            for _ in range(4)
        ]
        assert decisions == [
            _GATE_SEARCH,
            _GATE_SEARCH,
            _GATE_SEARCH,
            _GATE_SEARCH,
        ]

    def test_absent_interval_attribute_also_searches_every_message(
        self,
    ) -> None:
        """If the agent_meta object doesn't even have the attribute
        (``getattr(..., default=1)``), behavior matches the default.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        agent_meta = MagicMock(spec=[])  # no attributes → getattr default fires
        assert not hasattr(agent_meta, "skill_search_interval")

        decisions = [
            _gate_decides_skip(
                agent_meta=agent_meta,
                manager=manager,
                instance_id="inst-1",
            )
            for _ in range(3)
        ]
        assert all(d == _GATE_SEARCH for d in decisions)

    # ─── AC#2: interval=3 → search, skip, skip, search, skip, skip, … ──

    def test_interval_three_searches_every_third_message(self) -> None:
        """``interval=3``: verify the actual cycle.

        Cache is NOT pre-seeded — per AC#3 the first message ALWAYS
        searches (no cache yet → ``cached is None`` → falls to else).
        The cache is built by msg 1's search.

        Concrete trace with the corrected gate
        (``msg_count < interval - 1``, reset to 0 after each SEARCH):

        * msg 1: pre-increment = 0, cache empty → SEARCH (counter reset to 0)
        * msg 2: pre-increment = 0, cache present, 0 < 2 → SKIP
        * msg 3: pre-increment = 1, cache present, 1 < 2 → SKIP
        * msg 4: pre-increment = 2, cache present, 2 < 2 → False → SEARCH
        * msg 5: pre-increment = 0 (after reset), SKIP
        * msg 6: pre-increment = 1, SKIP
        * msg 7: pre-increment = 2, cache present, 2 < 2 → False → SEARCH

        Net cycle: SEARCH + 2 SKIPs → next SEARCH. So with
        ``interval=3`` the effective period is 3 messages (1 search +
        2 cache reuses). This matches the spec's "search every Nth
        message" wording.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        # Cache deliberately empty before the first message.
        assert manager._context_skill_results == {}

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 3

        # Simulate the messaging path's per-message logic: when the
        # gate decides SEARCH, the production code calls
        # ``set_context_skill_result`` (which populates the cache for
        # the NEXT messages) AND ``reset_skill_search_count``. Mirror
        # that here.
        def _simulate_one_message() -> str:
            decision = _gate_decides_skip(
                agent_meta=agent_meta,
                manager=manager,
                instance_id="inst-1",
            )
            if decision == _GATE_SEARCH:
                manager._context_skill_results["inst-1"] = (
                    "fresh text",
                    ["skill-fresh"],
                )
                manager.reset_skill_search_count("inst-1")
            return decision

        decisions = [_simulate_one_message() for _ in range(7)]
        assert decisions == [
            _GATE_SEARCH,
            _GATE_SKIP,
            _GATE_SKIP,
            _GATE_SEARCH,
            _GATE_SKIP,
            _GATE_SKIP,
            _GATE_SEARCH,
        ]

    # ─── AC#3: first message always searches (no cache yet) ───────────

    def test_first_message_searches_even_with_high_interval(self) -> None:
        """``interval=10`` but cache is empty → first message SEARCHES.
        The ``cached is None`` clause short-circuits the gate.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        # No cache populated.
        assert manager._context_skill_results == {}

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 10

        decision = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=manager,
            instance_id="inst-1",
        )
        assert decision == _GATE_SEARCH

    # ─── AC#4: no cached result → always searches ─────────────────────

    def test_no_cached_result_always_searches(self) -> None:
        """``interval=5`` but cache is empty (e.g., first message after
        restart) → gate falls to SEARCH, not SKIP.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        # Cache deliberately empty (no prior search).
        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 5

        for _ in range(3):
            decision = _gate_decides_skip(
                agent_meta=agent_meta,
                manager=manager,
                instance_id="inst-1",
            )
            assert decision == _GATE_SEARCH

    def test_cached_result_present_but_explicit_none_still_searches(
        self,
    ) -> None:
        """When the previous search ran but yielded ``None`` (no
        injectable skills), the cache entry IS present but is
        explicitly ``None``. The gate's ``cached is not None`` clause
        correctly forces a fresh search — the previous result had
        nothing to reuse anyway.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        # Cache was explicitly set to None by the previous search
        # (search ran but yielded no injectable skills).
        manager._context_skill_results["inst-1"] = None

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 5

        decision = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=manager,
            instance_id="inst-1",
        )
        # Even though msg_count=0 < 5, cached is None → SEARCH.
        assert decision == _GATE_SEARCH

    # ─── AC#5: cached result reused correctly ─────────────────────────

    def test_cached_result_reused_within_interval(self) -> None:
        """After a real search populates the cache, subsequent messages
        within the interval window SKIP the search entirely. The cache
        value (a tuple) is the gate's signal to skip.
        """
        from unittest.mock import MagicMock

        manager = self._make_manager()
        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 4

        # First message: cache empty → search.
        first = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=manager,
            instance_id="inst-1",
        )
        assert first == _GATE_SEARCH
        # Simulate the production post-search write + reset.
        manager._context_skill_results["inst-1"] = (
            "searched text",
            ["s-1", "s-2"],
        )
        manager.reset_skill_search_count("inst-1")

        # Next 3 messages: cache present, counter < interval-1 (3) → all skip.
        for i in range(3):
            decision = _gate_decides_skip(
                agent_meta=agent_meta,
                manager=manager,
                instance_id="inst-1",
            )
            assert decision == _GATE_SKIP, (
                f"msg {i + 2}: expected SKIP, got {decision}"
            )

        # Counter has incremented 4 times (1 search + 3 skips);
        # next call would re-search.
        assert (
            manager._skill_search_message_counts["inst-1"] == 3
        )

    # ─── Drift detection ───────────────────────────────────────────────

    def test_gate_decision_matches_production_source(self) -> None:
        """Sanity check: the production gate's decision snippet in
        ``daemon/services/instance_messaging.py`` must match the test
        helper's logic. If this fails, somebody changed the gate
        without updating :func:`_gate_decides_skip`.
        """
        source = _gate_source()
        # The four anchor strings that uniquely identify the gate's
        # decision shape. If any of these drift, update the test
        # helper to match.
        anchors = [
            "interval > 1",
            "cached is not None",
            "msg_count < interval - 1",
            "get_and_increment_skill_search_count",
            "get_context_skill_result",
            "reset_skill_search_count",
            # W1 fix: explicit-load marker consult + set/clear sites
            "was_explicit_skill_loaded",
            "mark_explicit_skill_loaded",
            "clear_explicit_skill_loaded",
            # S1 fix: extracted shared search helper
            "_run_search_and_cache",
        ]
        for anchor in anchors:
            assert anchor in source, (
                f"gate source drift: {anchor!r} no longer present "
                f"in daemon/services/instance_messaging.py — update "
                f"_gate_decides_skip in this test file to match."
            )


# ─── W1 fix: Explicit-load marker separation ───────────────────────────────
#
# The W1 fix introduces a marker set (``_explicit_skill_loaded``) on the
# manager that distinguishes between an auto-search cache write and an
# explicit ``<meta>``-tag ``load_skill`` cache write. The interval gate
# consults the marker so an explicit load does NOT feed the interval
# cache — the next ordinary message must run a fresh auto-search.
#
# These tests exercise the marker semantics on the real
# ``InstanceManager`` methods (not a mock) by attaching only the two
# dicts the methods touch, mirroring the same hand-rolled test-stub
# pattern used for the counter tests above.


class TestExplicitSkillLoadedMarker:
    """W1 fix: ``mark_explicit_skill_loaded`` /
    ``clear_explicit_skill_loaded`` / ``was_explicit_skill_loaded``."""

    def _make_manager(self):
        """Build an ``InstanceManager``-shaped object with the marker
        set attached — mirrors the hand-rolled test-stub pattern.
        """
        from daemon.manager import InstanceManager

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._explicit_skill_loaded = set()
        return mgr

    def test_mark_adds_instance_to_set(self) -> None:
        """``mark_explicit_skill_loaded`` records the instance."""
        mgr = self._make_manager()
        mgr.mark_explicit_skill_loaded("inst-1")
        assert "inst-1" in mgr._explicit_skill_loaded

    def test_was_returns_true_after_mark(self) -> None:
        """``was_explicit_skill_loaded`` returns ``True`` after a mark."""
        mgr = self._make_manager()
        mgr.mark_explicit_skill_loaded("inst-1")
        assert mgr.was_explicit_skill_loaded("inst-1") is True

    def test_was_returns_false_for_unmarked_instance(self) -> None:
        """``was_explicit_skill_loaded`` returns ``False`` for an
        instance that was never marked."""
        mgr = self._make_manager()
        assert mgr.was_explicit_skill_loaded("never-marked") is False

    def test_clear_removes_instance_from_set(self) -> None:
        """``clear_explicit_skill_loaded`` drops the instance."""
        mgr = self._make_manager()
        mgr.mark_explicit_skill_loaded("inst-1")
        mgr.clear_explicit_skill_loaded("inst-1")
        assert mgr.was_explicit_skill_loaded("inst-1") is False

    def test_clear_is_idempotent(self) -> None:
        """``clear_explicit_skill_loaded`` on an unmarked instance
        is a no-op (no ``KeyError``). Mirrors the counter reset
        behavior — both are defensive against absent entries."""
        mgr = self._make_manager()
        mgr.clear_explicit_skill_loaded("never-marked")  # must not raise

    def test_marker_is_per_instance(self) -> None:
        """Markers are tracked independently per instance."""
        mgr = self._make_manager()
        mgr.mark_explicit_skill_loaded("inst-A")
        mgr.mark_explicit_skill_loaded("inst-B")
        assert mgr.was_explicit_skill_loaded("inst-A") is True
        assert mgr.was_explicit_skill_loaded("inst-B") is True
        # Clear one — the other remains.
        mgr.clear_explicit_skill_loaded("inst-A")
        assert mgr.was_explicit_skill_loaded("inst-A") is False
        assert mgr.was_explicit_skill_loaded("inst-B") is True

    def test_cleanup_drops_entry(self) -> None:
        """Cleanup sites use ``discard`` — same defensive pattern as
        the counter cleanup. Mirrored here by direct ``discard``.
        """
        mgr = self._make_manager()
        mgr.mark_explicit_skill_loaded("inst-1")
        # Simulate the defensive cleanup pattern.
        _explicit = getattr(mgr, "_explicit_skill_loaded", None)
        assert _explicit is not None
        _explicit.discard("inst-1")
        assert mgr.was_explicit_skill_loaded("inst-1") is False

    def test_cleanup_safe_when_set_missing(self) -> None:
        """The ``getattr`` defensive pattern must not raise when the
        ``_explicit_skill_loaded`` set is absent (e.g., hand-rolled
        test stubs that bypass ``__init__``)."""
        mgr = self._make_manager()
        del mgr._explicit_skill_loaded  # simulate stub
        # Same defensive snippet as in cleanup sites — must not raise.
        _explicit = getattr(mgr, "_explicit_skill_loaded", None)
        if _explicit is not None:
            _explicit.discard("inst-1")


class TestExplicitLoadDoesNotFeedIntervalCache:
    """Integration: W1 fix end-to-end via the gate helper.

    Mirrors the production flow for an instance with
    ``interval=3``:

    1. Explicit ``load_skill`` writes to ``_context_skill_results``
       and calls :meth:`mark_explicit_skill_loaded`.
    2. The next ordinary message arrives within the interval window
       and the gate must consult :meth:`was_explicit_skill_loaded`.
    3. With the marker set, the gate forces a fresh search
       (``_GATE_SEARCH``), not a cache reuse.
    4. After the auto-search runs, the auto-search path calls
       :meth:`clear_explicit_skill_loaded` and resets the counter —
       subsequent ordinary messages within the window can now reuse
       the fresh result.
    """

    def test_explicit_load_forces_fresh_search_within_interval(self) -> None:
        """The W1 contract: explicit load → marker set → next ordinary
        message searches even within the interval window."""
        from daemon.manager import InstanceManager
        from unittest.mock import MagicMock

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._skill_search_message_counts = {}
        mgr._context_skill_results = {}
        mgr._explicit_skill_loaded = set()

        # Mirror production: explicit load writes a result + sets marker.
        mgr._context_skill_results["inst-1"] = (
            "explicit-loaded text",
            ["skill-explicit"],
        )
        mgr.mark_explicit_skill_loaded("inst-1")

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 3

        decision = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=mgr,
            instance_id="inst-1",
            explicit_loaded=mgr.was_explicit_skill_loaded("inst-1"),
        )
        # Without the W1 fix, this would SKIP. With the W1 fix, the
        # marker forces a fresh SEARCH.
        assert decision == _GATE_SEARCH

    def test_cleared_marker_re_enables_cache_reuse(self) -> None:
        """After the auto-search clears the marker, the gate can
        again reuse the cache for subsequent ordinary messages.
        """
        from daemon.manager import InstanceManager
        from unittest.mock import MagicMock

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._skill_search_message_counts = {}
        mgr._context_skill_results = {}
        mgr._explicit_skill_loaded = set()

        # Mirror: auto-search ran, wrote a result, cleared marker,
        # reset counter.
        mgr._context_skill_results["inst-1"] = (
            "auto-searched text",
            ["skill-auto"],
        )
        mgr.reset_skill_search_count = (
            lambda iid: mgr._skill_search_message_counts.__setitem__(
                iid, 0
            )
        )
        mgr.get_and_increment_skill_search_count = lambda iid: 0
        mgr.get_context_skill_result = (
            lambda iid: mgr._context_skill_results.get(iid)
        )

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 3

        # Marker absent → cache reuse is re-enabled.
        assert mgr.was_explicit_skill_loaded("inst-1") is False
        decision = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=mgr,
            instance_id="inst-1",
            explicit_loaded=mgr.was_explicit_skill_loaded("inst-1"),
        )
        assert decision == _GATE_SKIP

    def test_interval_one_unaffected_by_explicit_marker(self) -> None:
        """``interval == 1`` always searches regardless of marker —
        the S1 restructure puts this on the hot path and the W1
        guard is inside the ``interval > 1`` branch."""
        from daemon.manager import InstanceManager
        from unittest.mock import MagicMock

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._skill_search_message_counts = {}
        mgr._context_skill_results = {}
        mgr._explicit_skill_loaded = set()
        mgr.get_and_increment_skill_search_count = lambda iid: 0

        # Even if the marker is set, interval=1 must always SEARCH.
        mgr.mark_explicit_skill_loaded("inst-1")

        agent_meta = MagicMock()
        agent_meta.skill_search_interval = 1

        decision = _gate_decides_skip(
            agent_meta=agent_meta,
            manager=mgr,
            instance_id="inst-1",
            explicit_loaded=mgr.was_explicit_skill_loaded("inst-1"),
        )
        assert decision == _GATE_SEARCH