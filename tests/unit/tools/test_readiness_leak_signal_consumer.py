"""The leak-signal CONSUMER (plan Track-1 item 6).

Pins the alerting consumer around the readiness leak-predicate
emission (``GraceTracker._evaluate_leak`` → ``GraceObservation.leak_signal``):

* a firing tick logs ONE warning and increments
  ``checkpoint_leak_signal_total`` exactly once per fire;
* non-firing ticks (no detection / no signal) log nothing and
  increment nothing;
* the consumer is FAIL-OPEN: a consumer failure never propagates to
  the emitter — ``GraceTracker.observe`` still returns the verdict and
  the grace conjunction still sees the leak;
* the counter wiring: ``reset_for_tests()`` zeroes it and
  ``render_metrics()`` renders the name (always, even at 0).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

import daemon.services.readiness as readiness_module
from daemon.checkpoint_metrics import (
    checkpoint_leak_signal_total,
    render_metrics,
    reset_for_tests,
)
from daemon.constants import READINESS_LEAK_TICK_THRESHOLD
from daemon.services.readiness import (
    FAILURE_CLASS_OK,
    FAILURE_CLASS_PROBE_TIMEOUT,
    GraceTracker,
)

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def _degraded() -> tuple[dict, list]:
    classes = {
        name: FAILURE_CLASS_OK for name in readiness_module.READINESS_COMPONENT_NAMES
    }
    classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
    degraded = [n for n, c in classes.items() if c != FAILURE_CLASS_OK]
    return classes, degraded


def _fire_via_emitter() -> object:
    """Drive ``GraceTracker.observe`` past the sustained-waiting arm."""
    tracker = GraceTracker()
    psycopg = {"pool_size": 10, "pool_available": 8, "requests_waiting": 2}
    classes, degraded = _degraded()
    obs = None
    for _ in range(READINESS_LEAK_TICK_THRESHOLD):
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg=psycopg,
            pool_state_sqlalchemy=None,
            now=T0,
        )
    return obs


@pytest.fixture(autouse=True)
def _clean_counter():
    reset_for_tests()
    yield
    reset_for_tests()


class TestLeakSignalConsumerViaEmitter:
    """Fire through the REAL emission path (``GraceTracker.observe``)."""

    def test_a_firing_tick_logs_one_warning_and_increments_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="daemon.services.readiness"):
            obs = _fire_via_emitter()
        assert obs.leak_signal["detected"] is True
        assert checkpoint_leak_signal_total.get() == 1
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "LEAK SIGNAL" in r.getMessage()
        ]
        assert len(warnings) == 1
        # The reason token names the pool identity (the oracle psycopg
        # pool) and the context rides the same line.
        message = warnings[0].getMessage()
        assert "psycopg pool" in message
        assert "checkedout_rising" in message

    def test_a_non_firing_tick_logs_nothing_and_increments_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tracker = GraceTracker()
        psycopg = {"pool_size": 10, "pool_available": 8, "requests_waiting": 0}
        classes, degraded = _degraded()
        with caplog.at_level(logging.WARNING, logger="daemon.services.readiness"):
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg=psycopg,
                pool_state_sqlalchemy=None,
                now=T0,
            )
        assert obs.leak_signal["detected"] is False
        assert checkpoint_leak_signal_total.get() == 0
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "LEAK SIGNAL" in r.getMessage()
        ]

    def test_a_consumer_failure_does_not_propagate_to_the_emitter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BrokenCounter:
            def inc(self, amount: int = 1) -> None:
                raise RuntimeError("counter registry down")

        monkeypatch.setattr(
            readiness_module, "checkpoint_leak_signal_total", _BrokenCounter()
        )
        # The emitter must complete: the verdict is returned, the leak
        # still disengages the grace conjunction, and no exception
        # escapes ``observe``.
        obs = _fire_via_emitter()
        assert obs.leak_signal["detected"] is True


class TestLeakSignalConsumerDirect:
    """The consumer's own gate, exercised directly."""

    def test_none_signal_is_a_no_op(self) -> None:
        readiness_module.consume_leak_signal(None)
        assert checkpoint_leak_signal_total.get() == 0

    def test_non_detected_signal_is_a_no_op(self) -> None:
        readiness_module.consume_leak_signal(
            {"detected": False, "reason": None, "streak": 0}
        )
        assert checkpoint_leak_signal_total.get() == 0

    def test_a_non_dict_signal_is_a_no_op(self) -> None:
        readiness_module.consume_leak_signal("garbage")  # type: ignore[arg-type]
        assert checkpoint_leak_signal_total.get() == 0

    def test_a_direct_consumer_failure_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BrokenCounter:
            def inc(self, amount: int = 1) -> None:
                raise RuntimeError("counter registry down")

        monkeypatch.setattr(
            readiness_module, "checkpoint_leak_signal_total", _BrokenCounter()
        )
        # Must not raise.
        readiness_module.consume_leak_signal(
            {"detected": True, "reason": "psycopg pool exhausted", "streak": 1}
        )


class TestLeakSignalCounterWiring:
    def test_reset_for_tests_zeroes_the_counter(self) -> None:
        checkpoint_leak_signal_total.inc(5)
        assert checkpoint_leak_signal_total.get() == 5
        reset_for_tests()
        assert checkpoint_leak_signal_total.get() == 0

    def test_render_metrics_includes_the_name_even_at_zero(self) -> None:
        reset_for_tests()
        text = render_metrics()
        assert "# TYPE checkpoint_leak_signal_total counter" in text
        assert (
            f"checkpoint_leak_signal_total {checkpoint_leak_signal_total.get()}"
            in text
        )

    def test_each_fire_increments_exactly_once(self) -> None:
        readiness_module.consume_leak_signal({"detected": True, "reason": "a"})
        readiness_module.consume_leak_signal({"detected": True, "reason": "b"})
        readiness_module.consume_leak_signal({"detected": False})
        assert checkpoint_leak_signal_total.get() == 2
