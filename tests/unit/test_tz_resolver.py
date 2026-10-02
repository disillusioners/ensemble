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
        02:00→03:00). The anchor shifts to the FIRST EXISTING post-
        transition local time (per F1, the canonical ``next-valid``
        semantics — NOT naive + gap_seconds) and emits a warning
        containing ``shifted-forward`` (the pinned token callers grep).
        """
        naive = datetime(2026, 3, 8, 2, 30)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny)
        # Shifted to the first valid post-transition local time: 03:00 EDT.
        assert anchored.replace(tzinfo=None).isoformat() == "2026-03-08T03:00:00"
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert "shifted-forward" in warning
        assert "nonexistent local time" in warning

    def test_gap_shifts_to_first_valid_local_one_minute_past(self):
        """F1 pin: 02:01 (1 min into the gap) shifts to 03:00 (gap end), not 03:01.

        Regression for the old ``+gap_seconds`` impl that would have
        landed at 03:01 = 07:01Z. The next-valid semantic lands at the
        gap-end instant (03:00 EDT = 07:00Z) regardless of where the
        naive is inside the gap.
        """
        naive = datetime(2026, 3, 8, 2, 1)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny)
        assert anchored.replace(tzinfo=None).isoformat() == "2026-03-08T03:00:00"
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert "shifted-forward" in warning

    def test_fold_preference_pre_uses_first_occurrence(self):
        """F1 fold pin: ``fold_preference="pre"`` (default, ADR-008) → fold=0 / pre-DST.

        Same input as ``test_fold_uses_first_occurrence_no_warning``
        but exercises the new keyword explicitly. 01:30 NY on Nov 1 →
        EDT (-4h) = 05:30Z.
        """
        naive = datetime(2026, 11, 1, 1, 30)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny, fold_preference="pre")
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert anchored.fold == 0
        assert anchored.replace(tzinfo=None).isoformat() == "2026-11-01T01:30:00"
        assert warning == ""

    def test_fold_preference_post_uses_second_occurrence(self):
        """F2 cron-path pin: ``fold_preference="post"`` → fold=1 / post-DST.

        Same input as above but with the cron path's preference. 01:30
        NY on Nov 1 → EST (-5h) = 06:30Z. The LATER UTC instant keeps
        cron fires continuous across the fall-back (e.g. 06:00 daily
        on Nov 1 → 06:00 EST = 11:00Z, continuous with 11:00Z on Nov 2).
        """
        naive = datetime(2026, 11, 1, 1, 30)
        ny = ZoneInfo("America/New_York")
        anchored, warning = anchor_local_to_utc(naive, ny, fold_preference="post")
        assert anchored.utcoffset().total_seconds() == -5 * 3600
        assert anchored.fold == 1
        assert anchored.replace(tzinfo=None).isoformat() == "2026-11-01T01:30:00"
        assert warning == ""

    def test_fold_preference_invalid_raises_value_error(self):
        """F1 guard pin: ``fold_preference`` outside the closed set raises."""
        with __import__("pytest").raises(ValueError, match="fold_preference"):
            anchor_local_to_utc(
                datetime(2026, 11, 1, 1, 30),
                ZoneInfo("America/New_York"),
                fold_preference="bogus",
            )

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


# ---------------------------------------------------------------------------


class TestComputeNextCronFireMonotonicGuard:
    """Post-review monotonic guard for ``compute_next_cron_fire``.

    Two scenarios live here, both exercising the F4 fix (date source =
    ``after_aware`` for DOW=``*``) and the residual guard at the bottom
    of the reconstruct path:

    * F4 day-advance: a same-date phantom (literal HH:MM has already
      passed on the cursor's date) advances to the NEXT calendar day
      instead of dispatching in the past / being eaten by the guard.
      Closes the post-fire-seed miss reproduced on ``0 23 * * *``
      across 2026-11-01 (Nov 1 23:00 EST fire silently skipped before
      the fix; verified green after).

    * Residual guard (defense-in-depth): when ``anchor_local_to_utc``
      itself returns a non-strictly-later value (theoretical edge —
      not triggered on the F4-fix path in production), the guard still
      signals miss/advance via ``None`` per the function's existing
      convention (``compute_next_cron_fire`` docstring). Pinned via
      a croniter patch that emits a candidate whose date + literal
      HH:MM still lands on/before ``after_aware`` even after the F4
      day-advance (the patch's date math forces the literal back onto
      ``after_aware``'s exact date AFTER the +1d advance).
    """

    def test_same_date_phantom_advances_to_next_day_via_f4_fix(
        self, monkeypatch,
    ):
        """F4 fix: a same-date phantom on a DOW=``*`` expression advances
        to the NEXT calendar day instead of dispatching in the past.

        Repro before F4: ``post-fire-seed`` (cursor = Feb 15 07:00 NY
        just past the prior 06:00 fire) → croniter emitted a same-
        date phantom (Feb 15 06:00 NY = 11:00 UTC) → pre-F4
        reconstruct re-anchored to Feb 15 06:00 EST = 11:00Z (BEFORE
        cursor at 12:00Z) → guard fired → returned ``None`` → next
        cycle compute_next_cron_fire call from ``null`` → cast as
        wall-clock-now → still landed in the past → tight loop (or
        silent skip if the caller treats ``None`` as a miss).

        Post-F4 fix: reconstruct uses ``after_aware``'s local date,
        so a phantom whose literal HH:MM has already passed on the
        cursor's date advances +1 day. The cursor's next fire lands
        on Feb 16 06:00 NY = 11:00 EST = 16:00Z — strictly later
        than the cursor (07:00 EST = 12:00Z), so the monotonic guard
        does NOT fire. The function returns the legitimate next fire
        rather than ``None``.
        """
        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo("America/New_York")

        import croniter as _croniter_module

        call_count = {"i": 0}

        class _PhantomCroniterFake:
            """Same-date phantom that mimics the pre-F4 croniter 6.0.0
            tz-object-identity defect (emits the wrong hour AND the
            wrong date on certain shapes; here we force the same-date
            shape to exercise the F4 day-advance rule).

            First ``get_next`` returns Feb 15 06:00 NY (the phantom
            — wrong hour); roundtrip (call #2) returns Feb 15 14:00 NY
            (different instant → roundtrip != candidate → phantom path
            is taken in the production code).
            """

            def __init__(self, expr, start):
                call_count["i"] += 1
                self._index = call_count["i"]

            def get_next(self, ret_type):
                if self._index == 1:
                    return datetime(2026, 2, 15, 6, 0, tzinfo=ny)
                return datetime(2026, 2, 15, 14, 0, tzinfo=ny)

        monkeypatch.setattr(_croniter_module, "croniter", _PhantomCroniterFake)

        # 07:00 EST on Feb 15 — just past the prior 06:00 fire.
        after_aware = datetime(2026, 2, 15, 7, 0, tzinfo=ny)

        result = compute_next_cron_fire("0 6 * * *", after_aware, ny)

        # F4 fix: phantom advances +1 day → Feb 16 06:00 NY = 16:00Z.
        # Pre-F4: this returned None (guard fired).
        assert result is not None, (
            "F4 fix regression — same-date phantom on DOW=* must "
            "advance to next-day fire, not None (guard)"
        )
        assert result == datetime(2026, 2, 16, 6, 0, tzinfo=ny), (
            f"expected Feb 16 06:00 NY = 11:00Z (F4 next-day advance), "
            f"got {result.isoformat()}"
        )
        assert result.utcoffset().total_seconds() == -5 * 3600
        # Both croniter() invocations happened (main + roundtrip).
        assert call_count["i"] == 2

    def test_residual_guard_fires_when_anchor_returns_non_strictly_later(
        self, monkeypatch, caplog,
    ):
        """Residual guard (defense-in-depth): the monotonic guard at
        the bottom of the reconstruct path still fires when the anchor
        helper itself returns a non-strictly-later value.

        In production the F4 day-advance rule eliminates the scenarios
        which the guard was added to catch (same-date phantom on
        DOW=``*`` no longer lands on/before ``after_aware``). The guard
        remains as defense-in-depth — this test pins that a
        ``recovered <= after_aware`` condition still produces ``None`` +
        a guard warning, by patching ``anchor_local_to_utc`` to return
        the cursor instant (synthetic edge the production flow cannot
        reach but the guard must still handle).
        """
        import logging

        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo("America/New_York")

        import croniter as _croniter_module

        # Force croniter roundtrip-detect a phantom (call #1 != call #2)
        # so the reconstruct path runs.
        call_count = {"i": 0}

        class _PhantomCroniterFake:
            def __init__(self, expr, start):
                call_count["i"] += 1
                self._index = call_count["i"]

            def get_next(self, ret_type):
                if self._index == 1:
                    return datetime(2026, 2, 15, 6, 0, tzinfo=ny)
                return datetime(2026, 2, 15, 14, 0, tzinfo=ny)

        monkeypatch.setattr(_croniter_module, "croniter", _PhantomCroniterFake)

        # Patch anchor_local_to_utc to return exactly ``after_aware`` —
        # F4 advance rule still triggers (Feb 15 06:00 <= Feb 15 07:00
        # → +1 day → Feb 16 06:00 NY), the anchor helper then ignores
        # the input and returns the cursor → recovered <= after_aware
        # → guard fires.
        _anchor_holder = {"after": datetime(2026, 2, 15, 7, 0, tzinfo=ny)}

        def _patched_anchor(naive, tz, **kwargs):
            return _anchor_holder["after"], ""

        monkeypatch.setattr(tz_module, "anchor_local_to_utc", _patched_anchor)

        with caplog.at_level(logging.WARNING, logger="daemon.util.tz"):
            result = compute_next_cron_fire(
                "0 6 * * *", _anchor_holder["after"], ny,
            )

        # Guard fires — returns None (miss/advance signal).
        assert result is None
        guard_warnings = [
            r for r in caplog.records
            if "advancing past phantom fire" in r.getMessage()
        ]
        assert guard_warnings, (
            f"expected monotonic-guard warning, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# User-timezone-setting rung contract (user-timezone-setting feature)
# ---------------------------------------------------------------------------


class TestUserTimezoneRungContract:
    """The user-setting rung is fed in as ``default`` — resolver stays pure.

    The service wrapper (``daemon.services.scheduling_service
    ._resolve_schedule_timezone``) reads the user timezone preference and
    passes it as the preferred ``default``; ``resolve_timezone`` itself
    gains no logic. These pins document the wrapper↔resolver contract:
    a valid ``default`` resolves CLEAN (no warning — deliberate user
    choice, never a fallback), and an explicit param still short-circuits.
    """

    @pytest.fixture(autouse=True)
    def _isolate_host_detection(self, monkeypatch):
        """Same isolation as ``TestResolveTimeZoneChain`` (deterministic host)."""
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS", "0")
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(tz_module, "_read_etc_localtime_target", lambda: None)
        tz_module._cache_clear_for_tests()
        yield
        tz_module._cache_clear_for_tests()

    def test_user_setting_as_default_resolves_clean_no_warning(self):
        """SET + VALID user tz fed as ``default`` → ZoneInfo + EMPTY warning."""
        zone, warning = resolve_timezone(None, default="Asia/Bangkok", for_tool=True)
        assert zone == ZoneInfo("Asia/Bangkok")
        assert warning == ""

    def test_explicit_still_wins_over_user_setting_default(self):
        zone, warning = resolve_timezone(
            "America/New_York", default="Asia/Bangkok", for_tool=True
        )
        assert zone == ZoneInfo("America/New_York")
        assert warning == ""

    def test_invalid_user_setting_default_falls_to_host_then_utc(self):
        """A default that stopped validating between read and use falls
        through the pure chain (host-local is neutralized here → UTC)."""
        zone, warning = resolve_timezone(None, default="Not/ARealZone", for_tool=True)
        assert zone == dt_timezone.utc
        assert "Invalid tz" in warning
