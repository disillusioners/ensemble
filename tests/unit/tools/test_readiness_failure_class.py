"""Failure-CLASS table + leak predicate + grace state machine.

soak-gate-mcp-warmup · A1 · the normative source is
``.agents/shared/planning/soak-gate-mcp-warmup/requirements.md`` §4.0
(the failure-CLASS table), §4.2 (P2 — eligibility + aggregation) and
§4.4 (P4 — the leak signature).

The pins cover:

* every §4.0 row, on BOTH arms (grace-eligible / never-eligible);
* the leak predicate over a synthetic monotonic ``checkedout()``
  sequence (ADVISORY — it must NOT arm), the P2 oracle signals, the
  streak fallback and its pool-objects-UNAVAILABLE qualification (S9);
* the grace state machine: arm, expiry, no-extension, re-arm-after-lapse
  and disengage-on-leak;
* the tick-by-tick AGGREGATION matrix (all-row-1 / row-1 + row-2 /
  row-1 + leak / all-green) — the conjunction, never a vote.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daemon.constants import (
    READINESS_GRACE_HORIZON_S,
    READINESS_LEAK_TICK_THRESHOLD,
)
from daemon.services.readiness import (
    FAILURE_CLASS_BOOT_INCOMPLETE,
    FAILURE_CLASS_HARD_COMPONENT_RED,
    FAILURE_CLASS_IDENTITY_MISMATCH,
    FAILURE_CLASS_NOT_SERVING,
    FAILURE_CLASS_OK,
    FAILURE_CLASS_POOL_BUSY,
    FAILURE_CLASS_POOL_LEAK,
    FAILURE_CLASS_PROBE_TIMEOUT,
    FAILURE_CLASS_QUEUE_STALE,
    FAILURE_CLASS_READ_TIMEOUT,
    FAILURE_CLASS_UNKNOWN,
    GRACE_ELIGIBLE_FAILURE_CLASSES,
    READINESS_COMPONENT_NAMES,
    GraceTracker,
)

T0 = datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# §4.0 — the failure-CLASS table, both arms
# ═══════════════════════════════════════════════════════════════════════


class TestFailureClassTable:
    """Every row of the §4.0 table, on both arms."""

    @pytest.mark.parametrize(
        "failure_class",
        [
            FAILURE_CLASS_PROBE_TIMEOUT,
            FAILURE_CLASS_POOL_BUSY,
            FAILURE_CLASS_QUEUE_STALE,
            FAILURE_CLASS_READ_TIMEOUT,
        ],
    )
    def test_grace_eligible_rows(self, failure_class):
        assert failure_class in GRACE_ELIGIBLE_FAILURE_CLASSES

    @pytest.mark.parametrize(
        "failure_class",
        [
            FAILURE_CLASS_BOOT_INCOMPLETE,
            FAILURE_CLASS_IDENTITY_MISMATCH,
            FAILURE_CLASS_HARD_COMPONENT_RED,
            FAILURE_CLASS_NOT_SERVING,
            FAILURE_CLASS_POOL_LEAK,
            FAILURE_CLASS_UNKNOWN,
        ],
    )
    def test_never_grace_eligible_rows(self, failure_class):
        assert failure_class not in GRACE_ELIGIBLE_FAILURE_CLASSES

    def test_the_two_sets_are_disjoint_and_cover_the_table(self):
        eligible = set(GRACE_ELIGIBLE_FAILURE_CLASSES)
        never = {
            FAILURE_CLASS_BOOT_INCOMPLETE,
            FAILURE_CLASS_IDENTITY_MISMATCH,
            FAILURE_CLASS_HARD_COMPONENT_RED,
            FAILURE_CLASS_NOT_SERVING,
            FAILURE_CLASS_POOL_LEAK,
            FAILURE_CLASS_UNKNOWN,
        }
        assert not (eligible & never)
        assert eligible | never == {
            FAILURE_CLASS_PROBE_TIMEOUT,
            FAILURE_CLASS_POOL_BUSY,
            FAILURE_CLASS_QUEUE_STALE,
            FAILURE_CLASS_READ_TIMEOUT,
            FAILURE_CLASS_BOOT_INCOMPLETE,
            FAILURE_CLASS_IDENTITY_MISMATCH,
            FAILURE_CLASS_HARD_COMPONENT_RED,
            FAILURE_CLASS_NOT_SERVING,
            FAILURE_CLASS_POOL_LEAK,
            FAILURE_CLASS_UNKNOWN,
        }


# ═══════════════════════════════════════════════════════════════════════
# §4.4 (P4) — the leak predicate
# ═══════════════════════════════════════════════════════════════════════


def _degraded(**overrides) -> tuple[dict, list]:
    classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
    classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
    classes.update(overrides)
    degraded = [n for n, c in classes.items() if c != FAILURE_CLASS_OK]
    return classes, degraded


class TestLeakPredicate:
    """The ORACLE / ADVISORY split + the streak-arm qualification."""

    def test_p1_advisory_checkedout_rise_does_not_arm(self):
        """A MONOTONIC P1 ``checkedout()`` rise is ADVISORY (never arms)."""
        tracker = GraceTracker()
        psycopg = {
            "pool_size": 10,
            "pool_available": 8,
            "requests_waiting": 0,
        }
        for checkedout in (2, 5, 9, 14):
            classes, degraded = _degraded()
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg=psycopg,
                pool_state_sqlalchemy={
                    "checkedout": checkedout,
                    "size": 5,
                    "overflow": 3,
                },
                now=T0,
            )
            assert obs.leak_signal["detected"] is False
            assert obs.grace_active is True
        # The advisory signal IS published (the operator can see it).
        assert obs.leak_signal["checkedout_rising"] is True

    def test_p2_oracle_requests_waiting_sustained_arms(self):
        """``requests_waiting > 0`` sustained ARMS the leak class."""
        tracker = GraceTracker()
        psycopg = {
            "pool_size": 10,
            "pool_available": 8,
            "requests_waiting": 2,
        }
        classes, degraded = _degraded()
        obs = None
        for i in range(READINESS_LEAK_TICK_THRESHOLD):
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg=psycopg,
                now=T0 + timedelta(seconds=i),
            )
        assert obs.leak_signal["detected"] is True
        # A leak signal makes the grace INELIGIBLE (it refuses to engage).
        assert obs.grace_active is False

    def test_p2_oracle_pool_available_decreasing_arms_at_the_ceiling(self):
        """``pool_available`` strictly decreasing over 3 ticks ARMS — the
        TREND-QUALIFIED form (the free capacity is shrinking while the
        pool is consumed TO ITS CEILING, with no recovery tick).

        The qualification is a recorded adjudication: read literally the
        trend fires on ANY sustained ramp, including the healthy-busy
        shape requirements §5(i) mandates NOT be classified as a leak
        (a ramp consumes the free capacity in consecutive ticks whether
        the daemon is healthy or leaking).
        """
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = None
        for i, (size, available) in enumerate(((8, 2), (9, 1), (10, 0))):
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg={
                    "pool_size": size,
                    "pool_max": 10,
                    "pool_available": available,
                    "requests_waiting": 0,
                },
                now=T0 + timedelta(seconds=i),
            )
        assert obs.leak_signal["detected"] is True
        assert obs.grace_active is False

    def test_a_healthy_ramp_below_the_ceiling_does_not_arm(self):
        """A healthy ramp (the free capacity shrinking, but the pool FAR
        from its ceiling) is NOT a leak — the grace stays engaged."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = None
        for i, (size, available) in enumerate(((3, 0), (5, 0), (7, 0))):
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg={
                    "pool_size": size,
                    "pool_max": 10,
                    "pool_available": available,
                    "requests_waiting": 0,
                },
                now=T0 + timedelta(seconds=i),
            )
        assert obs.leak_signal["detected"] is False
        assert obs.grace_active is True

    def test_p2_oracle_pool_exhausted_arms(self):
        """``the pool size exhausted (all connections checked out)``
        ARMS — the IN-USE count reached the pool's ceiling."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 0,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert obs.leak_signal["detected"] is True
        assert obs.grace_active is False

    def test_p2_oracle_a_busy_but_healthy_pool_does_not_arm(self):
        """``pool_available`` is psycopg's IDLE count — it is 0 during
        ANY busy period. The exhausted arm therefore reads the IN-USE
        count against the pool's CEILING, so a healthy burst (busy, but
        far from the ceiling) is NOT classified as a leak."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 7,
                "pool_max": 10,
                "pool_available": 0,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert obs.leak_signal["detected"] is False
        assert obs.grace_active is True

    def test_streak_arm_only_when_pool_objects_unavailable(self):
        """S9: the streak arm fires ONLY when the pool objects are
        UNAVAILABLE — with the pool objects available it MUST NOT fire
        (a sustained busy-but-healthy burst is NOT a leak)."""
        # (a) pool objects AVAILABLE ⇒ the streak must NOT arm.
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = None
        for i in range(READINESS_LEAK_TICK_THRESHOLD + 2):
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg={
                    "pool_size": 10,
                    "pool_max": 10,
                    "pool_available": 8,
                    "requests_waiting": 0,
                },
                now=T0 + timedelta(seconds=i),
            )
        assert obs.leak_signal["streak"] >= READINESS_LEAK_TICK_THRESHOLD
        assert obs.leak_signal["detected"] is False
        assert obs.grace_active is True

        # (b) pool objects UNAVAILABLE ⇒ the streak arm fires (fail-closed).
        tracker2 = GraceTracker()
        obs2 = None
        for i in range(READINESS_LEAK_TICK_THRESHOLD):
            obs2 = tracker2.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg=None,
                now=T0 + timedelta(seconds=i),
            )
        assert obs2.leak_signal["detected"] is True
        assert obs2.grace_active is False


# ═══════════════════════════════════════════════════════════════════════
# §4.2 / §4.3 — the grace state machine
# ═══════════════════════════════════════════════════════════════════════


class TestGraceStateMachine:
    """Arming, expiry, no-extension, re-arm-after-lapse, leak-disengage."""

    def test_arms_on_the_first_grace_eligible_observation(self):
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert obs.grace_active is True
        assert obs.grace_reason == FAILURE_CLASS_PROBE_TIMEOUT
        assert obs.grace_scope == "load"
        assert obs.grace_expires_at == T0 + timedelta(
            seconds=READINESS_GRACE_HORIZON_S
        )

    def test_expires_at_the_horizon_and_is_never_extended(self):
        tracker = GraceTracker()
        classes, degraded = _degraded()
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        first_expiry = obs.grace_expires_at
        # A tick INSIDE the horizon keeps the SAME expiry (never extended).
        obs2 = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S - 1),
        )
        assert obs2.grace_active is True
        assert obs2.grace_expires_at == first_expiry
        # A tick AT/AFTER the horizon ⇒ the grace is lapsed. The
        # expiry is NOT moved (never extended) and the grace stays OFF
        # for every later tick until the episode is reset by a healthy
        # observation (A5(iv)(c): "after the absolute ceiling elapses,
        # detail.grace.grace_active == false").
        obs3 = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S),
        )
        assert obs3.grace_active is False
        assert obs3.grace_expires_at == first_expiry
        obs4 = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S + 30),
        )
        assert obs4.grace_active is False
        assert obs4.grace_expires_at == first_expiry

    def test_rearm_requires_the_grace_to_have_lapsed(self):
        """A re-arm starts a NEW window with a NEW expiry — the old one
        is never moved forward."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        healthy = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
        first = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert first.grace_active is True
        # Past the horizon the grace is lapsed.
        lapsed = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S + 1),
        )
        assert lapsed.grace_active is False
        # A healthy observation resets the episode (the daemon
        # recovered) — and ONLY then can a new window be armed.
        tracker.observe(
            component_classes=healthy,
            degraded_components=[],
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S + 2),
        )
        rearm = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S + 3),
        )
        assert rearm.grace_active is True
        assert rearm.grace_expires_at == T0 + timedelta(
            seconds=READINESS_GRACE_HORIZON_S + 3 + READINESS_GRACE_HORIZON_S
        )

    def test_disengages_on_a_leak_signal(self):
        """A leak signal makes the grace INELIGIBLE and DISENGAGES it."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        armed = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert armed.grace_active is True
        leaked = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 0,
                "requests_waiting": 3,
            },
            now=T0 + timedelta(seconds=1),
        )
        assert leaked.leak_signal["detected"] is True
        assert leaked.grace_active is False

    def test_a_never_eligible_class_never_arms(self):
        tracker = GraceTracker()
        classes, degraded = _degraded(database=FAILURE_CLASS_HARD_COMPONENT_RED)
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 10,
                "pool_max": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert obs.grace_active is False
        assert obs.grace_reason == FAILURE_CLASS_HARD_COMPONENT_RED


# ═══════════════════════════════════════════════════════════════════════
# §4.2 (W-2) — the aggregation matrix (a CONJUNCTION, never a vote)
# ═══════════════════════════════════════════════════════════════════════


class TestAggregationMatrix:
    """The tick-by-tick matrix: all-row-1 / row-1+row-2 / row-1+leak /
    all-green."""

    def _observe(self, tracker, classes, psycopg=None, now=T0):
        degraded = [n for n, c in classes.items() if c != FAILURE_CLASS_OK]
        return tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg=psycopg,
            now=now,
        )

    def test_all_row_1_grace_is_active(self):
        tracker = GraceTracker()
        classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
        classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
        classes["checkpoint_saver"] = FAILURE_CLASS_POOL_BUSY
        obs = self._observe(tracker, classes, psycopg={
            "pool_size": 10, "pool_max": 10, "pool_available": 8, "requests_waiting": 0,
        })
        assert obs.grace_active is True

    def test_row_1_plus_row_2_grace_is_off(self):
        """One never-eligible component among several timeouts KILLS the
        grace for that tick (a conjunction, not a vote)."""
        tracker = GraceTracker()
        classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
        classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
        classes["queue_freshness"] = FAILURE_CLASS_QUEUE_STALE
        classes["checkpoint_saver"] = FAILURE_CLASS_HARD_COMPONENT_RED
        obs = self._observe(tracker, classes, psycopg={
            "pool_size": 10, "pool_max": 10, "pool_available": 8, "requests_waiting": 0,
        })
        assert obs.grace_active is False

    def test_row_1_plus_leak_grace_is_off(self):
        tracker = GraceTracker()
        classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
        classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
        obs = self._observe(tracker, classes, psycopg={
            "pool_size": 10, "pool_max": 10, "pool_available": 0, "requests_waiting": 1,
        })
        assert obs.grace_active is False

    def test_all_green_grace_is_inactive(self):
        tracker = GraceTracker()
        classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
        obs = self._observe(tracker, classes, psycopg={
            "pool_size": 10, "pool_max": 10, "pool_available": 8, "requests_waiting": 0,
        })
        assert obs.grace_active is False
        assert obs.grace_expires_at is None
