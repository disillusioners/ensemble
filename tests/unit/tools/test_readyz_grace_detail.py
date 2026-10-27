"""The /readyz detail payload under an active grace.

soak-gate-mcp-warmup · A1 · A5(iv)(a) — the grace is OBSERVABLE and it
never masks a real fault:

* while the grace is held the composite is still ``degraded`` and the
  component reasons are STILL PRESENT (a suppressed reason string is a
  DEFECT — decisions §4 invariant 2);
* ``detail.grace`` carries ``grace_active`` / ``grace_reason`` /
  ``grace_expires_at`` / ``scope`` / ``holds``;
* when the grace is NOT held the block carries ONLY ``grace_active:
  false`` (no null-padded keys);
* the verdict never changes: ``composite.ready`` stays False while the
  grace is held (the daemon never lies).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daemon.services.readiness import (
    FAILURE_CLASS_PROBE_TIMEOUT,
    READINESS_COMPONENT_NAMES,
    GraceTracker,
    compute_readiness_composite,
)

T0 = datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc)


def _degraded_composite(**kwargs):
    classes = {name: "ok" for name in READINESS_COMPONENT_NAMES}
    classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
    return compute_readiness_composite(
        database_ok=False,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
        checked_at=T0,
        component_classes=classes,
        **kwargs,
    )


class TestReadyzGraceDetail:
    def test_a_held_grace_still_reports_degraded_with_its_reasons(self):
        tracker = GraceTracker()
        classes = {name: "ok" for name in READINESS_COMPONENT_NAMES}
        classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
        obs = tracker.observe(
            component_classes=classes,
            degraded_components=["database"],
            pool_state_psycopg={
                "pool_size": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            now=T0,
        )
        composite = _degraded_composite(
            grace_active=obs.grace_active,
            grace_reason=obs.grace_reason,
            grace_expires_at=obs.grace_expires_at,
            grace_scope=obs.grace_scope,
            grace_holds=obs.holds,
            leak_signal=obs.leak_signal,
        )
        payload = composite.to_payload()
        detail = payload["detail"]
        # The verdict is UNCHANGED — the daemon never lies.
        assert payload["status"] == "degraded"
        assert composite.ready is False
        assert payload["components"]["database"] is False
        # The reasons are PRESENT and name the real component.
        assert detail["reasons"], "the reason list was emptied — a DEFECT"
        assert any("database" in r for r in detail["reasons"])
        # The grace block.
        assert detail["grace"]["grace_active"] is True
        assert detail["grace"]["grace_reason"] == FAILURE_CLASS_PROBE_TIMEOUT
        assert detail["grace"]["scope"] == "load"
        assert detail["grace"]["holds"] >= 1
        expires = detail["grace"]["grace_expires_at"]
        assert expires is not None
        parsed = datetime.fromisoformat(expires)
        assert parsed > T0

    def test_an_inactive_grace_carries_no_null_padded_keys(self):
        composite = _degraded_composite(grace_active=False)
        detail = composite.to_payload()["detail"]
        assert detail["grace"] == {"grace_active": False}
        assert "grace_reason" not in detail["grace"]
        assert "grace_expires_at" not in detail["grace"]
        assert "holds" not in detail["grace"]

    def test_the_pool_state_and_leak_signal_are_published(self):
        composite = _degraded_composite(
            pool_state_sqlalchemy={"checkedout": 3, "size": 5, "overflow": 0},
            pool_state_psycopg={
                "pool_size": 10,
                "pool_available": 8,
                "requests_waiting": 0,
            },
            leak_signal={
                "detected": False,
                "checkedout_rising": False,
                "streak": 0,
            },
        )
        detail = composite.to_payload()["detail"]
        assert detail["pool_state"]["sqlalchemy"]["checkedout"] == 3
        assert detail["pool_state"]["psycopg"]["pool_size"] == 10
        assert detail["leak_signal"]["detected"] is False

    def test_the_component_classes_are_published(self):
        composite = _degraded_composite()
        detail = composite.to_payload()["detail"]
        assert detail["component_classes"]["database"] == FAILURE_CLASS_PROBE_TIMEOUT
        assert detail["component_classes"]["services"] == "ok"

    def test_a_leak_signal_is_published_when_detected(self):
        composite = _degraded_composite(
            leak_signal={
                "detected": True,
                "checkedout_rising": True,
                "streak": 3,
                "reason": "psycopg pool requests_waiting>0 sustained (3 ticks)",
            }
        )
        detail = composite.to_payload()["detail"]
        assert detail["leak_signal"]["detected"] is True
        assert detail["leak_signal"]["streak"] == 3
        # The grace must NOT be active while a leak is detected.
        assert detail["grace"]["grace_active"] is False
