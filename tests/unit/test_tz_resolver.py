"""Unit tests for ``daemon.util.tz`` — the phase-1 tz foundation.

Phase-5 acceptance criterion (phase5-plan.md §Acceptance/Unit tests):

    ``tests/unit/test_tz_resolver.py::TestAnchorLocalToUtc`` (phase-1
    §Task 9) covers 5 cases (aware-trust, fold=0, gap-shift+warning,
    UTC, non-DST-date) — passes in CI.

The file was listed by the plan but NOT landed with phase 1; it lands
here (unit 4) per the commission instruction ("if the tz unit tests are
specified there, include them as a NEW file").

NOTE (skeleton adaptation): the frozen plan skeletons cite the import
path ``daemon.utils.tz``; the landed module is ``daemon.util.tz`` and
the landed env var is ``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`` (field
``default_timezone`` + pydantic ``env_prefix="ENSEMBLE_SCHEDULING_"``
in ``daemon/config.py``). All assertions here use the landed paths.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from daemon.util import tz as tz_module
from daemon.util.tz import anchor_local_to_utc, resolve_timezone


# ---------------------------------------------------------------------------


class TestAnchorLocalToUtc:
    """The 5 pinned cases for the one-shot DST anchor (ADR-008)."""

    def test_aware_input_is_trusted_unchanged(self):
        """Rule 1: an already-aware datetime is trusted — returned as-is, no warning."""
        aware = datetime(2026, 10, 15, 6, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        anchored, warning = anchor_local_to_utc(aware, ZoneInfo("Asia/Tokyo"))
        assert anchored is aware
        assert anchored.utcoffset().total_seconds() == 9 * 3600
        assert warning == ""

    def test_fold_uses_first_occurrence_no_warning(self):
        """Rule 2: ambiguous fall-back local time anchors with fold=0 (first occurrence).

        2026-11-01 01:30 America/New_York exists TWICE (EDT -04:00 then
        EST -05:00). fold=0 pins the PRE-transition (EDT) instant — the
        same semantic croniter applies on the cron path (ADR-008).
        """
        naive = datetime(2026, 11, 1, 1, 30)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny)
        assert anchored.tzinfo is not None
        # First occurrence = EDT = UTC-4.
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert anchored.replace(tzinfo=None).isoformat() == "2026-11-01T01:30:00"
        assert warning == ""

    def test_gap_shifts_forward_with_loud_warning(self):
        """Rule 3: nonexistent spring-forward local time shifts forward + warns.

        2026-03-08 02:30 America/New_York does NOT exist (clocks jump
        02:00→03:00). The anchor shifts to 03:30 EDT and emits a warning
        containing ``shifted-forward`` (the pinned token callers grep).
        """
        naive = datetime(2026, 3, 8, 2, 30)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny)
        # Shifted to the first valid post-transition local time: 03:30 EDT.
        assert anchored.replace(tzinfo=None).isoformat() == "2026-03-08T03:30:00"
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert "shifted-forward" in warning
        assert "nonexistent local time" in warning

    def test_utc_zone_anchor_is_identity(self):
        """UTC has no transitions — naive time anchors 1:1 with no warning."""
        naive = datetime(2026, 7, 4, 12, 0)
        anchored, warning = anchor_local_to_utc(naive, dt_timezone.utc)
        assert anchored.isoformat() == "2026-07-04T12:00:00+00:00"
        assert warning == ""

    def test_non_dst_date_plain_anchor(self):
        """A normal (non-DST-boundary) local time anchors with the zone's standard offset."""
        naive = datetime(2026, 2, 15, 9, 45)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny)
        # February = EST (UTC-5).
        assert anchored.utcoffset().total_seconds() == -5 * 3600
        assert anchored.replace(tzinfo=None).isoformat() == "2026-02-15T09:45:00"
        assert warning == ""


# ---------------------------------------------------------------------------


class TestResolveTimeZoneChain:
    """Complementary chain pins for ``resolve_timezone`` (D2 / ADR-002).

    The full chain (explicit → env default → host-local → UTC) is
    exercised through the adapter in ``tests/test_scheduler_adapter.py::TestTzResolution``;
    these two cases pin the helper-level contract directly: the terminal
    fallback returns ``datetime.timezone.utc`` (NEVER ``ZoneInfo("UTC")``,
    architecture §4.3) and invalid explicit tz falls back to UTC with a
    warning (never raises).
    """

    @pytest.fixture(autouse=True)
    def _isolate_host_detection(self, monkeypatch):
        """Force host-local detection to fail + bypass the module cache.

        Deterministic regardless of the CI host's /etc/localtime; the
        cache-seconds=0 env keeps ``detect_host_local_timezone`` resolving
        fresh on every call (no cross-test cache bleed).
        """
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS", "0")
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(tz_module, "_read_etc_localtime_target", lambda: None)
        tz_module._cache_clear_for_tests()
        yield
        tz_module._cache_clear_for_tests()

    def test_explicit_invalid_tz_falls_back_to_utc_with_warning(self):
        zone, warning = resolve_timezone("Not/ARealZone", for_tool=True)
        assert zone == dt_timezone.utc
        assert "UTC" in warning

    def test_terminal_fallback_is_stdlib_utc_not_zoneinfo(self):
        """Nothing resolvable → ``datetime.timezone.utc`` (C-level constant, architecture §4.3)."""
        zone, warning = resolve_timezone(None, default=None, for_tool=True)
        assert zone == dt_timezone.utc
        # ZoneInfo("UTC") would fail this isinstance (different class hierarchy);
        # the terminal rung must be the stdlib constant (architecture §4.3).
        assert isinstance(zone, dt_timezone)
        assert not isinstance(zone, ZoneInfo)
        assert "UTC" in warning
