"""The query-door pagination + bounds pack (registered pack
``metrics_query_pagination``).

The door is BOUNDED: a single response never blows up, and a bounded
response is never SILENTLY truncated — it carries the opaque cursor
that resumes it (AM-6).

* the selector ceiling is 50 (the residual on-loop encode bound);
* a malformed label-aware selector ⇒ ``400 invalid_label_filter``;
* an invalid or EXPIRED cursor ⇒ ``400 invalid_cursor``;
* the byte ceiling bounds the body and mints a cursor (P3-M4);
* ``max_points`` is CLAMPED to the ceiling — the caller may not widen
  the daemon's own budget (P3-M12).

Mutation table: ``P3-M4`` (the pagination ceiling raised above the byte
ceiling) · ``P3-M12`` (``max_points`` default raised above 240, or
``?names=`` accepted above 50).
"""

from __future__ import annotations

import time

import pytest

from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_FAMILIES,
    MAX_NAMES,
    MAX_POINTS_CEILING,
    RESPONSE_BYTE_CEILING,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.envelope import encode_cursor
from tests.helpers.metrics_query_fixtures import (
    RecordingProvider,
    build_client,
    error_code,
)


def _provider(**kwargs) -> RecordingProvider:
    return RecordingProvider(**kwargs)


class TestSelectorCeiling:
    """The selector ceiling bounds the residual on-loop encode."""

    def test_the_ceiling_is_50(self):
        assert MAX_NAMES == 50

    def test_fifty_selectors_are_accepted(self):
        names = ",".join(f"ensemble_p3_metric_{index}" for index in range(50))
        response = build_client(_provider()).get(f"/api/metrics/series?names={names}")
        assert response.status_code == 200, response.text

    def test_more_than_fifty_selectors_are_rejected(self):
        names = ",".join(f"ensemble_p3_metric_{index}" for index in range(51))
        response = build_client(_provider()).get(f"/api/metrics/series?names={names}")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_label_filter"

    def test_the_rejection_is_not_a_silent_truncation(self):
        """A rejected filter carries NO series — never a partial answer."""
        names = ",".join(f"ensemble_p3_metric_{index}" for index in range(51))
        response = build_client(_provider()).get(f"/api/metrics/series?names={names}")
        assert response.status_code == 400
        assert "series" not in response.text


class TestLabelAwareSelector:
    """``?series=`` is LABEL-AWARE; ``?names=`` is name-level."""

    def test_a_malformed_label_aware_selector_is_rejected(self):
        response = build_client(_provider()).get(
            "/api/metrics/series?series=ensemble_p3_metric%7Bbroken"
        )
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_label_filter"

    def test_a_label_pair_without_a_value_is_rejected(self):
        response = build_client(_provider()).get(
            "/api/metrics/series?series=ensemble_p3_metric%7Bkey%7D"
        )
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_label_filter"

    def test_a_well_formed_label_aware_selector_is_accepted(self):
        response = build_client(_provider()).get(
            "/api/metrics/series?series=ensemble_p3_metric%7Bop%3Daget%7D"
        )
        assert response.status_code == 200, response.text

    def test_the_label_aware_selector_reaches_the_provider(self):
        captured: dict = {}
        provider = _provider()
        original = provider.history

        def _capture(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        provider.history = _capture  # type: ignore[method-assign]
        response = build_client(provider).get(
            "/api/metrics/series?series=ensemble_p3_metric%7Bop%3Daget%7D"
        )
        assert response.status_code == 200
        assert captured["series"] == ["ensemble_p3_metric{op=aget}"]


class TestCursor:
    """The cursor is OPAQUE and pins the evaluation instant + position."""

    def test_an_undecodable_cursor_is_rejected(self):
        response = build_client(_provider()).get("/api/metrics/series?cursor=not-a-cursor!!")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"

    def test_an_empty_cursor_is_treated_as_absent(self):
        response = build_client(_provider()).get("/api/metrics/series?cursor=")
        assert response.status_code == 200, response.text

    def test_a_cursor_without_an_evaluation_instant_is_rejected(self):
        cursor = encode_cursor({"o": 0})
        response = build_client(_provider()).get(f"/api/metrics/series?cursor={cursor}")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"

    def test_an_expired_cursor_is_rejected(self):
        """A cursor older than the buffer's oldest sample cannot resume."""
        cursor = encode_cursor({"v": 1, "t": 1_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(f"/api/metrics/series?cursor={cursor}")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"

    def test_a_live_cursor_is_accepted(self):
        cursor = encode_cursor({"v": 1, "t": 6_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(f"/api/metrics/series?cursor={cursor}")
        assert response.status_code == 200, response.text

    def test_the_cursor_resumes_at_its_position(self):
        cursor = encode_cursor({"v": 1, "t": 6_000, "o": 1})
        provider = RecordingProvider(
            history=[
                {"name": "a", "labels": {}, "kind": "gauge", "monotonic": False, "points": []},
                {"name": "b", "labels": {}, "kind": "gauge", "monotonic": False, "points": []},
                {"name": "c", "labels": {}, "kind": "gauge", "monotonic": False, "points": []},
            ],
            oldest_sample_timestamp=5_000,
        )
        response = build_client(provider).get(f"/api/metrics/series?cursor={cursor}")
        assert response.status_code == 200
        names = [entry["name"] for entry in response.json()["series"]]
        assert names == ["b", "c"]


class TestByteCeiling:
    """The byte ceiling bounds the body and mints a cursor (P3-M4)."""

    def test_the_ceiling_is_the_frozen_constant(self):
        assert RESPONSE_BYTE_CEILING == 1_048_576

    def test_an_oversized_body_is_bounded_and_carries_a_cursor(self):
        """A body over the ceiling is TRIMMED and carries the cursor."""
        from daemon.routers import metrics as metrics_router

        big = "x" * 4_000
        provider = RecordingProvider(
            history=[
                {
                    "name": "ensemble_p3_bulk",
                    "labels": {"payload": big},
                    "kind": "gauge",
                    "monotonic": False,
                    "points": [[1_700_000_000, 1.0]],
                }
                for _ in range(400)
            ]
        )
        response = build_client(provider).get("/api/metrics/series")
        assert response.status_code == 200
        body = response.json()
        assert body["truncated"] is True
        assert body["next_cursor"], "a bounded body must carry its cursor"
        assert len(body["series"]) < 400
        encoded = len(response.content)
        assert encoded <= RESPONSE_BYTE_CEILING, (
            f"the bounded body is {encoded} bytes — over the ceiling"
        )

    def test_the_cursor_resumes_where_the_trim_stopped(self):
        provider = RecordingProvider(
            history=[
                {
                    "name": "ensemble_p3_bulk",
                    "labels": {"payload": "y" * 4_000},
                    "kind": "gauge",
                    "monotonic": False,
                    "points": [[1_700_000_000, 1.0]],
                }
                for _ in range(400)
            ]
        )
        first = build_client(provider).get("/api/metrics/series").json()
        cursor = first["next_cursor"]
        second = build_client(provider).get(
            f"/api/metrics/series?cursor={cursor}"
        ).json()
        assert second["series"], "the cursor must resume with more series"
        assert second["series"][0]["name"] == "ensemble_p3_bulk"


class TestByteCeilingRunsOffTheEventLoop:
    """W-3 — the byte-ceiling PROBE and the TRIM run off the loop.

    The probe is a FULL JSON dump of the body and the trim is a binary
    search over it (worst case ~9 dumps of ~1 MiB ⇒ 20-30 ms).  Both
    used to run in the route handler — ON THE EVENT LOOP.  They now run
    inside the off-loop page builder.
    """

    def test_the_probe_and_the_trim_run_off_the_event_loop(self, monkeypatch):
        from daemon.routers import metrics as metrics_router

        threads: list[str] = []
        original = metrics_router._encoded_size

        def _record(payload):
            import threading

            threads.append(threading.current_thread().name)
            return original(payload)

        monkeypatch.setattr(metrics_router, "_encoded_size", _record)
        provider = RecordingProvider(
            history=[
                {
                    "name": "ensemble_p3_bulk",
                    "labels": {"payload": "z" * 4_000},
                    "kind": "gauge",
                    "monotonic": False,
                    "points": [[1_700_000_000, 1.0]],
                }
                for _ in range(400)
            ]
        )
        response = build_client(provider).get("/api/metrics/series")
        assert response.status_code == 200
        assert response.json()["truncated"] is True
        assert threads, "the byte-ceiling probe must have run"
        for name in threads:
            assert name.startswith("db-offload"), (
                f"the byte-ceiling probe ran on {name!r} — it must run on the "
                "off-loop worker, never on the event loop"
            )


class TestSnapshotBounds:
    """W-4 — the snapshot route is BOUNDED like the other two.

    The leader's ruling: the ceiling promise is the contract's safety
    property, so the snapshot route is NOT exempt — it carries
    ``?offset=`` / ``?limit=``, the byte ceiling applies, and a bounded
    body carries the continuation cursor.
    """

    def _provider(self, count: int = 40) -> RecordingProvider:
        return RecordingProvider(
            snapshot=[
                {
                    "name": f"ensemble_p3_snapshot_gauge_{index}",
                    "labels": {"index": str(index)},
                    "kind": "gauge",
                    "monotonic": False,
                    "value": float(index),
                    "last_publish_age_s": 0.1,
                }
                for index in range(count)
            ]
        )

    def test_the_limit_bounds_the_page(self):
        response = build_client(self._provider()).get("/api/metrics/snapshot?limit=10")
        assert response.status_code == 200
        body = response.json()
        assert len(body["series"]) == 10
        assert body["truncated"] is True
        assert body.get("next_cursor"), "a bounded snapshot must carry its cursor"

    def test_the_cursor_resumes_where_the_page_stopped(self):
        provider = self._provider()
        first = build_client(provider).get("/api/metrics/snapshot?limit=10").json()
        cursor = first["next_cursor"]
        second = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}"
        ).json()
        assert second["series"], "the cursor must resume with more series"
        assert second["series"][0]["name"] == "ensemble_p3_snapshot_gauge_10"

    def test_a_full_snapshot_is_not_truncated(self):
        response = build_client(self._provider(count=3)).get("/api/metrics/snapshot")
        body = response.json()
        assert len(body["series"]) == 3
        assert body["truncated"] is False
        assert not body.get("next_cursor")

    def test_the_limit_above_the_ceiling_is_clamped(self):
        response = build_client(self._provider()).get(
            "/api/metrics/snapshot?limit=100000"
        )
        assert response.status_code == 200
        assert len(response.json()["series"]) == 40

    def test_a_non_numeric_limit_is_rejected(self):
        response = build_client(self._provider()).get("/api/metrics/snapshot?limit=abc")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_label_filter"

    def test_the_snapshot_body_respects_the_byte_ceiling(self):
        provider = RecordingProvider(
            snapshot=[
                {
                    "name": f"ensemble_p3_snapshot_gauge_{index}",
                    "labels": {"index": str(index), "payload": "w" * 4_000},
                    "kind": "gauge",
                    "monotonic": False,
                    "value": 1.0,
                    "last_publish_age_s": 0.1,
                }
                for index in range(400)
            ]
        )
        response = build_client(provider).get("/api/metrics/snapshot")
        assert response.status_code == 200
        body = response.json()
        assert body["truncated"] is True
        assert body.get("next_cursor")
        assert len(response.content) <= RESPONSE_BYTE_CEILING, (
            f"the bounded snapshot is {len(response.content)} bytes — over the "
            "ceiling"
        )


class TestCursorExpiryProbe:
    """The expiry probe is O(rings) and runs OFF the event loop."""

    def test_the_expiry_probe_never_materialises_the_trend_buffer(self):
        """O(rings), NOT O(points) — the probe must not copy the buffer."""
        from daemon.services.metrics_query import ring as ring_module

        store = ring_module.TrendRingStore()
        ring = ring_module.TrendRing(depth=8)
        ring.append(1_000, 1.0)
        ring.append(1_015, 2.0)
        store._rings["k"] = ring

        def _boom(self):
            raise AssertionError(
                "the cursor's expiry probe must not copy the ring's points"
            )

        original = ring_module.TrendRing.points
        ring_module.TrendRing.points = _boom
        try:
            assert store.oldest_timestamp() == 1_000
            assert store.newest_timestamp() == 1_015
        finally:
            ring_module.TrendRing.points = original

    def test_the_expiry_probe_runs_off_the_event_loop(self):
        cursor = encode_cursor({"v": 1, "t": 6_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(f"/api/metrics/series?cursor={cursor}")
        assert response.status_code == 200, response.text
        assert provider.threads, "the provider must have been consulted"
        for thread_name in provider.threads:
            assert thread_name.startswith("db-offload"), (
                "the cursor's expiry probe must run OFF the event loop — it "
                f"ran on {thread_name!r}"
            )


class TestBounds:
    """``max_points`` and ``window_s`` are clamped."""

    def test_the_max_points_default_is_240(self):
        assert DEFAULT_MAX_POINTS == 240
        assert MAX_POINTS_CEILING == 240

    def test_a_max_points_above_the_ceiling_is_clamped(self):
        captured: dict = {}
        provider = _provider()
        original = provider.history

        def _capture(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        provider.history = _capture  # type: ignore[method-assign]
        response = build_client(provider).get("/api/metrics/series?max_points=100000")
        assert response.status_code == 200
        assert captured["max_points"] == MAX_POINTS_CEILING

    def test_a_max_points_below_the_ceiling_is_honoured(self):
        captured: dict = {}
        provider = _provider()
        original = provider.history

        def _capture(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        provider.history = _capture  # type: ignore[method-assign]
        response = build_client(provider).get("/api/metrics/series?max_points=60")
        assert response.status_code == 200
        assert captured["max_points"] == 60

    def test_a_window_beyond_the_buffer_window_is_clamped(self):
        captured: dict = {}
        provider = _provider()
        original = provider.history

        def _capture(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        provider.history = _capture  # type: ignore[method-assign]
        response = build_client(provider).get(
            f"/api/metrics/series?window_s={WINDOW_SECONDS * 10}"
        )
        assert response.status_code == 200
        assert captured["window_s"] == WINDOW_SECONDS

    def test_a_non_numeric_max_points_is_rejected(self):
        response = build_client(_provider()).get("/api/metrics/series?max_points=abc")
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_label_filter"


class TestCursorWalkTerminates:
    """ND-2 — a cursor walk TERMINATES (the delta-3 bug broke every route).

    The delta-3 change OR-ed the DISPLAY fact ("offset > 0 ⇒ this page
    is a partial view") into the cursor minting.  The consequence on
    every route: the LAST non-empty page minted a cursor, the client
    followed it into an EMPTY page, and the empty page re-minted the
    BYTE-IDENTICAL cursor (the position unchanged) — an endless walk.
    Pre-delta the chains TERMINATED.

    THE FIX (ND-2): the continuation signal is the BUILDER's own
    "more remain" flag plus the byte-ceiling TRIM, captured BEFORE the
    display flag mutates the envelope.  The display flag states a true
    fact about the BODY; it says nothing about whether another page
    exists.

    THE WALK: three pages over ``2 × MAX_FAMILIES + 1`` families — the
    first two are partial (each carries a cursor), the third holds the
    last family and must carry NONE.
    """

    def _provider(self, count: int) -> RecordingProvider:
        return RecordingProvider(
            families=[{"id": f"ensemble_nd2_family_{index:04d}"} for index in range(count)]
        )

    def _client(self, count: int):
        return build_client(self._provider(count))

    def test_a_three_page_walk_terminates(self):
        """The walk visits three pages and then STOPS."""
        client = self._client(2 * MAX_FAMILIES + 1)
        seen: list[str] = []
        pages = 0
        cursor: str | None = None
        while pages < 6:
            url = "/api/metrics/families" + (f"?cursor={cursor}" if cursor else "")
            body = client.get(url).json()
            pages += 1
            cursor = body.get("next_cursor")
            if not cursor:
                break
            assert cursor not in seen, (
                "the walk re-minted a cursor — the client would spin on the "
                "same position forever"
            )
            seen.append(cursor)
        assert pages == 3, f"the walk must terminate after 3 pages; it took {pages}"

    def test_the_last_non_empty_page_mints_no_cursor(self):
        """The LAST page that still holds data carries NO cursor."""
        client = self._client(2 * MAX_FAMILIES + 1)
        first = client.get("/api/metrics/families").json()
        assert first["truncated"] is True
        assert first["next_cursor"], "a partial page must carry its cursor"
        second = client.get(
            f"/api/metrics/families?cursor={first['next_cursor']}"
        ).json()
        assert len(second["families"]) == MAX_FAMILIES
        assert second["truncated"] is True
        assert second["next_cursor"], "a partial page must carry its cursor"
        third = client.get(
            f"/api/metrics/families?cursor={second['next_cursor']}"
        ).json()
        assert len(third["families"]) == 1, "the last page holds the last family"
        assert not third.get("next_cursor"), (
            "the LAST non-empty page must NOT mint a cursor — the walk would "
            "follow it into an empty page"
        )

    def test_an_empty_page_never_re_mints_the_cursor(self):
        """An EMPTY page carries NO cursor (it never re-mints its input)."""
        client = self._client(2 * MAX_FAMILIES + 1)
        stale = encode_cursor(
            {"v": 1, "t": int(time.time()), "o": 2 * MAX_FAMILIES + 1}
        )
        body = client.get(f"/api/metrics/families?cursor={stale}").json()
        assert body["families"] == []
        assert not body.get("next_cursor"), (
            "an empty page must not re-mint a cursor — the walk would spin on "
            "the same position forever"
        )

    def test_the_display_flag_still_states_the_partial_view(self):
        """ND-2 — the display fact is NOT lost: the body still says so."""
        client = self._client(2 * MAX_FAMILIES + 1)
        first = client.get("/api/metrics/families").json()
        third = client.get(
            f"/api/metrics/families?cursor={first['next_cursor']}"
        ).json()
        # …the page reached with a cursor is a partial view of the set,
        # and the BODY says so even when no further page exists.
        last = client.get(
            f"/api/metrics/families?cursor={third['next_cursor']}"
        ).json()
        assert last["truncated"] is True, (
            "the display flag must still mark a partial view — only the "
            "CURSOR minting changed"
        )
        assert not last.get("next_cursor")


class TestSnapshotCursorExpiry:
    """ND-4 — a STALE snapshot cursor expires (400 invalid_cursor).

    The snapshot builder was the only one of the three that did not
    call ``_expire_cursor``, so a stale cursor never expired there and
    the route's ``except _ExpiredCursor → 400`` was DEAD on that route.
    The expiry probe is the same O(rings) read the sibling builders
    make, and it runs OFF the event loop.
    """

    def test_a_stale_snapshot_cursor_is_refused(self):
        # the cursor's pinned instant is OLDER than the buffer's oldest
        # sample (1 000 < 5 000) — it can no longer resume faithfully
        cursor = encode_cursor({"v": 1, "t": 1_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}"
        )
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"

    def test_a_live_snapshot_cursor_is_accepted(self):
        """The probe must not refuse a LIVE cursor (the sibling behaviour)."""
        cursor = encode_cursor({"v": 1, "t": 6_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=1_000)
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}"
        )
        assert response.status_code == 200, response.text

    def test_the_snapshot_expiry_probe_runs_off_the_event_loop(self):
        cursor = encode_cursor({"v": 1, "t": 1_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}"
        )
        assert response.status_code == 400
        assert provider.threads, "the provider must have been consulted"
        for thread_name in provider.threads:
            assert thread_name.startswith("db-offload"), (
                "the expiry probe must run OFF the event loop — it ran on "
                f"{thread_name!r}"
            )


class TestCursorPrecedence:
    """ND-5 — the cursor-expiry probe PRECEDES the unknown-family check.

    ``GET /api/metrics/snapshot`` is the ONLY route whose page builder
    carries a family check at all, and the expiry probe runs FIRST
    there.  THE PINNED PRECEDENCE: an EXPIRED cursor combined with an
    UNKNOWN ``?family=`` answers ``400 invalid_cursor`` — NOT ``404
    unknown_family`` — because the expiry wins.

    THE SOURCE ANCHORS (``daemon/routers/metrics.py``): the probe is
    ``_expire_cursor(provider, cursor)`` at :504 and the family check is
    the block directly below it at :505-508 (``known = {entry.get("id")
    ...}`` / ``raise _UnknownFamily(family)``).

    THE DOC ANCHORS (the published contract — both pages state this
    precedence):

    * ``docs/architecture/metrics-query-contract.md:296`` — "Cursor
      precedence (ND-5)": the probe runs FIRST on all three page
      builders and the ``/snapshot`` unknown-family check sits
      DOWNSTREAM of it;
    * ``docs/architecture/metrics-query-api.md:179`` — the same
      precedence, stated for the API consumer.

    THE THREE LEGS — one load-bearing leg and two controls, so that
    NEITHER mechanism can be dead:

    * an EXPIRED cursor + an UNKNOWN family ⇒ ``400 invalid_cursor``
      (the precedence itself — the leg that reds when the two blocks
      are swapped);
    * a LIVE cursor + an UNKNOWN family ⇒ ``404 unknown_family`` (the
      family check is alive);
    * an EXPIRED cursor + a KNOWN family ⇒ ``400 invalid_cursor`` (the
      expiry probe is alive).

    A cursor is EXPIRED when its pinned instant predates the buffer's
    oldest sample (``_expire_cursor``: ``instant < oldest``); the
    instant rides the cursor payload's ``"t"`` key.
    """

    def test_an_expired_cursor_wins_over_an_unknown_family(self):
        """The precedence — the expiry is answered BEFORE the family."""
        cursor = encode_cursor({"v": 1, "t": 1_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=5_000)
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}&family=no_such_family"
        )
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"
        assert "families" not in provider.reads, (
            "the expiry probe must WIN before the family check is "
            f"consulted — the door read {provider.reads!r}"
        )

    def test_a_live_cursor_still_reports_an_unknown_family(self):
        """The CONTROL — the family check is not dead."""
        cursor = encode_cursor({"v": 1, "t": 6_000, "o": 0})
        provider = _provider(oldest_sample_timestamp=1_000)
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}&family=no_such_family"
        )
        assert response.status_code == 404, response.text
        assert error_code(response) == "unknown_family"
        assert "families" in provider.reads, (
            "the family check must have been consulted — the door read "
            f"{provider.reads!r}"
        )

    def test_an_expired_cursor_is_refused_for_a_known_family(self):
        """The CONTROL — the expiry probe is not dead."""
        cursor = encode_cursor({"v": 1, "t": 1_000, "o": 0})
        provider = _provider(
            families=[{"id": "ensemble_known_family"}],
            oldest_sample_timestamp=5_000,
        )
        response = build_client(provider).get(
            f"/api/metrics/snapshot?cursor={cursor}&family=ensemble_known_family"
        )
        assert response.status_code == 400, response.text
        assert error_code(response) == "invalid_cursor"
