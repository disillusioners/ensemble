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

    def test_p2_oracle_pool_exhausted_arms_and_reports_the_availability_trend(
        self,
    ):
        """The EXHAUSTION arm arms (every connection checked out) and
        the availability TREND is reported inside its reason token.

        The pin is deliberately named after the arm it actually
        exercises: §4.4's trend arm is DELIBERATELY SUBSUMED by the
        exhaustion arm (see the ADJUDICATION in
        ``daemon/services/readiness.py::_evaluate_leak`` — reviewer
        finding F1), so NO pin in this suite may claim independent
        trend-arm coverage. What this pin asserts is that the trend is
        OBSERVABLE (it is carried in the reason token) and that the
        exhaustion arm is the one that fires.
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
        # The arm that fired is the EXHAUSTION arm (not an independent
        # trend arm) — asserted on the reason token.
        assert "pool exhausted" in obs.leak_signal["reason"]
        # The trend is reported as part of that verdict.
        assert "availability strictly decreasing" in obs.leak_signal["reason"]
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


# ═══════════════════════════════════════════════════════════════════════
# §4.4-vs-§5(i) — the SUB-CEILING gap and its BOUNDS
# ═══════════════════════════════════════════════════════════════════════
#
# §4.4's trend arm is DELIBERATELY SUBSUMED by the exhaustion arm (see
# the ADJUDICATION in ``daemon/services/readiness.py::_evaluate_leak`` —
# reviewer finding F1). The consequence is a measurable GAP: a
# SUB-CEILING monotonic no-recovery availability decrease (the pool is
# busy and getting busier, but never reaches its ceiling and never
# queues a waiter) does NOT arm the leak class.
#
# The gap is BOUNDED, not open-ended — and this is the pin that proves
# it: the gap can never hold the grace open, because the grace EXPIRES
# at ``first_grace_eligible_observation + GRACE_HORIZON_S`` (120 s,
# §4.3). A daemon that stays in that shape therefore stops being
# grace-held, the composite stays degraded with its reasons intact, and
# the never-green trigger (byte-identical, unconditional — §4.1) still
# fires at cap-reach.


class TestSubCeilingGapIsBounded:
    """The sub-ceiling no-recovery series cannot hold the grace open."""

    def _sub_ceiling_series(self):
        """A SUB-CEILING monotonic no-recovery availability decrease.

        ``pool_size - pool_available`` (the in-use count) rises 2→3→4→5
        but NEVER reaches ``pool_max`` (10), and ``requests_waiting``
        stays 0 (nobody is queued).
        """
        for i, (size, available) in enumerate(
            ((6, 4), (7, 4), (8, 4), (9, 4))
        ):
            yield i, {
                "pool_size": size,
                "pool_max": 10,
                "pool_available": available,
                "requests_waiting": 0,
            }

    def test_sub_ceiling_no_recovery_series_cannot_hold_the_grace_open(self):
        tracker = GraceTracker()
        classes, degraded = _degraded()
        first = None
        last = None
        for i, pool_state in self._sub_ceiling_series():
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg=pool_state,
                now=T0 + timedelta(seconds=i * 10),
            )
            # The TREND path stays FALSE — the sub-ceiling gap.
            assert obs.leak_signal["detected"] is False, (
                "the sub-ceiling series armed the leak class"
            )
            if first is None:
                first = obs
            last = obs
        # The grace is armed on the FIRST observation and the expiry is
        # anchored to it (observation-relative, never extended).
        assert first.grace_active is True
        assert first.grace_expires_at == T0 + timedelta(
            seconds=READINESS_GRACE_HORIZON_S
        )
        assert last.grace_expires_at == first.grace_expires_at, (
            "the expiry moved — the grace was extended"
        )

    def test_the_grace_expires_at_the_horizon_under_the_sub_ceiling_series(self):
        """The bound: the sub-ceiling gap can NOT hold the grace open —
        the grace expires at ``first_eligible_observation + 120 s``."""
        tracker = GraceTracker()
        classes, degraded = _degraded()
        # The first observation is the arming tick.
        armed = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 6,
                "pool_max": 10,
                "pool_available": 4,
                "requests_waiting": 0,
            },
            now=T0,
        )
        assert armed.grace_active is True
        expiry = armed.grace_expires_at
        assert expiry == T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S)

        # Keep the sub-ceiling no-recovery series going for LONGER than
        # the horizon (the burst never settles).
        elapsed = 0
        obs = armed
        while elapsed < READINESS_GRACE_HORIZON_S + 60:
            elapsed += 10
            obs = tracker.observe(
                component_classes=classes,
                degraded_components=degraded,
                pool_state_psycopg={
                    "pool_size": 6 + min(int(elapsed / 10), 3),
                    "pool_max": 10,
                    "pool_available": 4,
                    "requests_waiting": 0,
                },
                now=T0 + timedelta(seconds=elapsed),
            )
            # The leak class stays FALSE (the gap) …
            assert obs.leak_signal["detected"] is False
            # … but the grace EXPIRES at the horizon and is never
            # re-armed by the continuing series.
            if elapsed >= READINESS_GRACE_HORIZON_S:
                assert obs.grace_active is False, (
                    f"the grace was still held {elapsed}s after the first "
                    "eligible observation — the sub-ceiling gap held it "
                    "open"
                )
                assert obs.grace_expires_at == expiry, (
                    "the expiry moved — the grace was extended"
                )

    def test_the_composite_stays_degraded_with_its_reasons_after_expiry(self):
        """The expiry does not make the daemon healthy: the composite is
        still degraded and the component reasons are still present (a
        suppressed reason string is a DEFECT)."""
        from daemon.services.readiness import compute_readiness_composite

        tracker = GraceTracker()
        classes, degraded = _degraded()
        tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 6,
                "pool_max": 10,
                "pool_available": 4,
                "requests_waiting": 0,
            },
            now=T0,
        )
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=degraded,
            pool_state_psycopg={
                "pool_size": 9,
                "pool_max": 10,
                "pool_available": 4,
                "requests_waiting": 0,
            },
            now=T0 + timedelta(seconds=READINESS_GRACE_HORIZON_S + 10),
        )
        assert obs.grace_active is False
        composite = compute_readiness_composite(
            database_ok=False,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=None,
            checked_at=T0,
            component_classes=classes,
            grace_active=obs.grace_active,
            grace_reason=obs.grace_reason,
            grace_expires_at=obs.grace_expires_at,
            grace_scope=obs.grace_scope,
            grace_holds=obs.holds,
            leak_signal=obs.leak_signal,
        )
        payload = composite.to_payload()
        assert payload["status"] == "degraded"
        assert composite.ready is False
        assert payload["detail"]["reasons"], (
            "the reason list was emptied after the grace expired — a DEFECT"
        )
        assert any("database" in r for r in payload["detail"]["reasons"])
        assert payload["detail"]["grace"]["grace_active"] is False
