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

import pytest

from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
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
