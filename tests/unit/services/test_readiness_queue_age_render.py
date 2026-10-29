"""Unit tests for the readiness queue-age render (plan Track-1 item 4).

THE BUG: ``queue_max_age_seconds`` is ``None`` for THREE different
facts — the RUNNING set was empty, the queue probe timed out, or the
probe failed — and the payload rendered all three as a bare ``null``
next to a ``queue_freshness: true`` component. Every consumer read
"no age" as "fresh".

THE FIX: the payload now renders
``detail.queue_max_age_seconds_reason`` — the provenance of the age —
so an absent age NEVER reads as an unqualified "fresh".

THE VERDICT IS UNCHANGED: the fix is a RENDER fix. The readiness
verdict (``ready`` / ``components`` / ``reasons``) keeps its existing
semantics — "an empty RUNNING set is fresh" is the daemon's documented
contract (``evaluate_queue_freshness``) and is pinned elsewhere.

Coverage:
    * an absent age renders "empty" (probe answered, nothing to age);
    * a timed-out probe renders "probe_timeout";
    * a FAILED probe renders "unknown" (the age was never determined);
    * a measured age renders "measured";
    * the verdict is untouched by the render.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from daemon.services.readiness import (
    QUEUE_AGE_REASON_EMPTY,
    QUEUE_AGE_REASON_MEASURED,
    QUEUE_AGE_REASON_PROBE_TIMEOUT,
    QUEUE_AGE_REASON_UNKNOWN,
    compute_readiness_composite,
    refresh_readiness_composite,
)


def _composite(age: float | None):
    return compute_readiness_composite(
        database_ok=True,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=age,
        checked_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )


class TestQueueAgeRender:
    """The rendered provenance of the queue age."""

    def test_absent_age_renders_empty_not_fresh(self):
        composite = _composite(None)
        detail = composite.to_payload()["detail"]
        assert detail["queue_max_age_seconds"] is None
        # The LITERAL vocabulary, not the constant: comparing the render
        # against the imported constant only proves the constant equals
        # itself — it constrains nothing. The wire value is pinned here.
        assert detail["queue_max_age_seconds_reason"] == "empty"
        # The label is never "fresh".
        assert detail["queue_max_age_seconds_reason"] != "fresh"

    def test_measured_age_renders_measured(self):
        detail = _composite(12.5).to_payload()["detail"]
        assert detail["queue_max_age_seconds"] == 12.5
        assert detail["queue_max_age_seconds_reason"] == "measured"

    def test_the_verdict_is_untouched_by_the_render(self):
        composite = _composite(None)
        assert composite.ready is True
        assert composite.queue_freshness is True
        assert composite.to_payload()["components"]["queue_freshness"] is True


class TestReasonVocabulary:
    """THE vocabulary — pinned EXACTLY, as literals (F1).

    The four reason labels are a WIRE contract (the payload is read by
    consumers that branch on the string). Asserting the render against
    the imported constant proved only that the constant equals itself,
    so the vocabulary was effectively UNPINNED: renaming a constant
    (and its value) together kept every test green.

    This class pins the literals, and pins that the four are DISTINCT
    (a copy-paste that collapsed two reasons would otherwise be
    invisible).
    """

    def test_the_four_reason_literals(self):
        assert QUEUE_AGE_REASON_MEASURED == "measured"
        assert QUEUE_AGE_REASON_EMPTY == "empty"
        assert QUEUE_AGE_REASON_PROBE_TIMEOUT == "probe_timeout"
        assert QUEUE_AGE_REASON_UNKNOWN == "unknown"

    def test_the_four_reasons_are_distinct(self):
        reasons = {
            QUEUE_AGE_REASON_MEASURED,
            QUEUE_AGE_REASON_EMPTY,
            QUEUE_AGE_REASON_PROBE_TIMEOUT,
            QUEUE_AGE_REASON_UNKNOWN,
        }
        assert len(reasons) == 4
        # The vocabulary is EXACTLY these four — no fifth spelling.
        assert reasons == {"measured", "empty", "probe_timeout", "unknown"}


class TestRefreshProvenance:
    """The refresh path knows WHAT the probe did — the render says so."""

    @pytest.mark.asyncio
    async def test_empty_set_renders_empty(self):
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: None,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
        )
        assert composite.queue_max_age_seconds is None
        assert (
            composite.to_payload()["detail"]["queue_max_age_seconds_reason"]
            == "empty"
        )

    @pytest.mark.asyncio
    async def test_measured_probe_renders_measured(self):
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 12.5,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
        )
        assert composite.queue_max_age_seconds == 12.5
        assert (
            composite.to_payload()["detail"]["queue_max_age_seconds_reason"]
            == "measured"
        )

    @pytest.mark.asyncio
    async def test_failed_probe_renders_unknown(self):
        """The probe RAISED — the age was never determined. The legacy
        empty-set default left the age None next to a "fresh"
        component; the render now says "unknown"."""

        def _boom():
            raise RuntimeError("queue probe exploded")

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=_boom,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
        )
        assert composite.queue_max_age_seconds is None
        assert (
            composite.to_payload()["detail"]["queue_max_age_seconds_reason"]
            == "unknown"
        )

    @pytest.mark.asyncio
    async def test_timed_out_probe_renders_probe_timeout(self):
        def _slow():
            time.sleep(2.6)  # > QUEUE_PROBE_TIMEOUT_S (2.0s)
            return 999.0

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=_slow,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
        )
        assert composite.queue_max_age_seconds is None
        assert (
            composite.to_payload()["detail"]["queue_max_age_seconds_reason"]
            == "probe_timeout"
        )
        # The verdict is unchanged (the timeout still degrades).
        assert composite.queue_freshness is False
