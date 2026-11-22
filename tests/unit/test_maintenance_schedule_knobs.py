"""Central Maintenance System (P1) — the env-first knobs + the
uniform-random minute picker.

Covers plan ``phase1-plan.md`` Task 1.2 (the picker + BR-5) and the
verification-plan legs the phase names: ``[start, end)`` inclusivity,
the weekday boundary (today-past ⇒ next week), the ICT conversion, the
invalid-window fail-closed leg and the DST gap/fold legs.

SAFETY FENCE: pure unit tests — no database, no daemon, no live
contact.  The timezone database is the host's.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from daemon.services.maintenance_schedule_knobs import (
    DEFAULT_TIMEZONE,
    DEFAULT_WEEKDAY,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    ENV_TIMEZONE,
    ENV_WEEKDAY,
    ENV_WINDOW_END,
    ENV_WINDOW_START,
    MaintenanceKnobError,
    pick_next_fire_instant,
    read_maintenance_knobs,
)

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
NYC = ZoneInfo("America/New_York")


class _FixedRng:
    """A ``randrange`` stub that always returns the same minute."""

    def __init__(self, minute: int):
        self._minute = minute

    def randrange(self, start: int, end: int) -> int:
        assert start <= self._minute < end, (
            f"the picker asked for a minute outside [{start}, {end})"
        )
        return self._minute


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No knob env statements ⇒ the documented defaults are in force."""
    for name in (
        ENV_WINDOW_START,
        ENV_WINDOW_END,
        ENV_WEEKDAY,
        ENV_TIMEZONE,
    ):
        monkeypatch.delenv(name, raising=False)


# ══════════════════════════════════════════════════════════════════════════
# The defaults
# ══════════════════════════════════════════════════════════════════════════


class TestDefaults:
    def test_the_default_knobs_are_the_documented_ones(self):
        knobs = read_maintenance_knobs()
        assert knobs.window_start == DEFAULT_WINDOW_START == "01:00"
        assert knobs.window_end == DEFAULT_WINDOW_END == "05:00"
        assert knobs.weekday == DEFAULT_WEEKDAY == 0
        assert knobs.timezone == DEFAULT_TIMEZONE == "Asia/Ho_Chi_Minh"

    def test_the_default_window_has_exactly_240_candidate_minutes(self):
        """01:00–05:00 ⇒ ``randrange(60, 300)`` ⇒ 240 candidates.

        The count is the DOCUMENTED contract (start inclusive, end
        exclusive) — a silent off-by-one here is a missed week.
        """
        knobs = read_maintenance_knobs()
        assert knobs.start_minute == 60
        assert knobs.end_minute == 300
        assert knobs.candidate_minutes == 240

    def test_the_knobs_are_read_at_every_call(self, monkeypatch):
        """Env-first, never cached — an operator edit applies at the next
        arm/re-arm without a daemon restart."""
        first = read_maintenance_knobs()
        assert first.window_start == "01:00"
        monkeypatch.setenv(ENV_WINDOW_START, "02:00")
        monkeypatch.setenv(ENV_WINDOW_END, "03:00")
        second = read_maintenance_knobs()
        assert second.window_start == "02:00"
        assert second.end_minute == 180
        assert second.candidate_minutes == 60


# ══════════════════════════════════════════════════════════════════════════
# [start, end) inclusivity
# ══════════════════════════════════════════════════════════════════════════


class TestWindowInclusivity:
    def test_the_start_minute_is_reachable(self):
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        assert picked.local_time == "01:00"

    def test_the_end_minute_is_NOT_reachable(self):
        """The end is EXCLUSIVE — 05:00 is outside the window."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(299),
        )
        assert picked.local_time == "04:59"

    def test_every_drawed_minute_is_inside_the_window(self):
        knobs = read_maintenance_knobs()
        seen = set()
        for seed in range(400):
            picked = pick_next_fire_instant(
                knobs,
                now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
                rng=random.Random(seed),
            )
            minute = int(picked.local_time[:2]) * 60 + int(picked.local_time[3:])
            assert knobs.start_minute <= minute < knobs.end_minute
            seen.add(minute)
        # The smoke: the draw really spans the window (not one constant).
        assert len(seen) > 100

    def test_the_distribution_is_roughly_uniform(self):
        """A loose uniformity smoke — the draw is uniform over the
        window, not clustered at one end."""
        knobs = read_maintenance_knobs()
        buckets = [0] * 4
        for seed in range(4000):
            picked = pick_next_fire_instant(
                knobs,
                now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
                rng=random.Random(seed),
            )
            minute = int(picked.local_time[:2]) * 60 + int(picked.local_time[3:])
            buckets[(minute - knobs.start_minute) // 60] += 1
        for count in buckets:
            assert 700 < count < 1300, f"buckets={buckets} are not uniform"


# ══════════════════════════════════════════════════════════════════════════
# The weekday boundary
# ══════════════════════════════════════════════════════════════════════════


class TestWeekdayBoundary:
    def test_today_past_rolls_to_next_week(self):
        """Sunday 06:00 local, window 01:00–05:00 ⇒ today's minute is
        already past ⇒ the pick lands on the NEXT Sunday."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 6, 0, tzinfo=ICT),  # a Sunday
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.strftime("%A") == "Sunday"
        assert local.date() == datetime(2026, 11, 29).date()
        assert local.hour == 1 and local.minute == 0

    def test_today_future_keeps_today(self):
        """Sunday 00:30 local ⇒ the 01:00 slot is still ahead ⇒ today."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 30, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.date() == datetime(2026, 11, 22).date()
        assert local.hour == 1

    @pytest.mark.parametrize(
        "knob_weekday,expected_name",
        [
            (0, "Sunday"),
            (1, "Monday"),
            (2, "Tuesday"),
            (3, "Wednesday"),
            (4, "Thursday"),
            (5, "Friday"),
            (6, "Saturday"),
        ],
    )
    def test_the_weekday_knob_maps_to_the_right_day(
        self, monkeypatch, knob_weekday, expected_name
    ):
        """Sunday-based knob (0=Sun..6=Sat) → Python's Monday-based
        ``weekday()`` via ``(D + 6) % 7``."""
        monkeypatch.setenv(ENV_WEEKDAY, str(knob_weekday))
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 6, 0, tzinfo=ICT),  # a Sunday
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.strftime("%A") == expected_name

    def test_the_next_occurrence_is_within_seven_days(self):
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 6, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert timedelta(0) < local - datetime(2026, 11, 22, 6, 0, tzinfo=ICT)
        assert local - datetime(2026, 11, 22, 6, 0, tzinfo=ICT) <= timedelta(days=7)

    def test_allow_today_false_never_selects_today(self):
        """The RE-ARM path: the next slot is the next weekday-D counted
        from the re-arm — never the day the chain just fired."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 3, 17, tzinfo=ICT),  # a Sunday
            allow_today=False,
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.date() == datetime(2026, 11, 29).date()
        assert local.strftime("%A") == "Sunday"


# ══════════════════════════════════════════════════════════════════════════
# The ICT conversion
# ══════════════════════════════════════════════════════════════════════════


class TestTimezoneConversion:
    def test_the_instant_is_anchored_to_ict(self):
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.hour == 1 and local.minute == 0
        # ICT is a fixed UTC+7 offset (no DST).
        assert picked.instant_utc == datetime(
            2026, 11, 21, 18, 0, tzinfo=timezone.utc
        )

    def test_the_run_at_echo_is_an_iso_utc_string(self):
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        assert picked.run_at_iso.startswith("2026-11-21T18:00:00")
        assert picked.run_at_iso.endswith("+00:00")

    def test_a_non_ict_zone_is_honoured(self, monkeypatch):
        monkeypatch.setenv(ENV_TIMEZONE, "UTC")
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc),
            rng=_FixedRng(60),
        )
        assert picked.instant_utc == datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# The fail-closed legs
# ══════════════════════════════════════════════════════════════════════════


class TestFailClosed:
    def test_an_invalid_window_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_WINDOW_START, "05:00")
        monkeypatch.setenv(ENV_WINDOW_END, "01:00")
        with pytest.raises(MaintenanceKnobError) as exc:
            read_maintenance_knobs()
        assert "midnight" in str(exc.value)

    def test_an_equal_window_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_WINDOW_START, "03:00")
        monkeypatch.setenv(ENV_WINDOW_END, "03:00")
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()

    def test_a_malformed_window_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_WINDOW_START, "1am")
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()

    def test_an_out_of_range_weekday_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_WEEKDAY, "7")
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()

    def test_a_non_numeric_weekday_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_WEEKDAY, "sunday")
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()

    def test_an_unresolvable_timezone_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_TIMEZONE, "Mars/Olympus_Mons")
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()


# ══════════════════════════════════════════════════════════════════════════
# BR-5 — the DST gap / fold legs
# ══════════════════════════════════════════════════════════════════════════


class TestDstHonesty:
    def test_a_gap_that_leaves_the_window_fails_closed(self, monkeypatch):
        """2026-03-08 02:30 does not exist in New York — the anchor
        shifts it forward to 03:00, which is OUTSIDE the [02:00, 03:00)
        window ⇒ no arm (BR-5 fail-closed)."""
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        monkeypatch.setenv(ENV_WINDOW_START, "02:00")
        monkeypatch.setenv(ENV_WINDOW_END, "03:00")
        knobs = read_maintenance_knobs()
        with pytest.raises(MaintenanceKnobError) as exc:
            pick_next_fire_instant(
                knobs,
                now=datetime(2026, 3, 1, 12, 0, tzinfo=NYC),
                rng=_FixedRng(150),  # 02:30 — inside the gap
            )
        assert "fail-closed" in str(exc.value)

    def test_a_gap_that_stays_inside_the_window_is_armed_with_a_warning(
        self, monkeypatch, caplog
    ):
        """A wider window absorbs the forward shift — the pick is armed
        and the anchor's warning is SURFACED, never swallowed."""
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        monkeypatch.setenv(ENV_WINDOW_START, "01:00")
        monkeypatch.setenv(ENV_WINDOW_END, "05:00")
        knobs = read_maintenance_knobs()
        with caplog.at_level("WARNING"):
            picked = pick_next_fire_instant(
                knobs,
                now=datetime(2026, 3, 1, 12, 0, tzinfo=NYC),
                rng=_FixedRng(150),  # 02:30 — inside the gap
            )
        assert picked.warnings, "the anchor warning must never be swallowed"
        assert "shifted-forward" in picked.warnings[0]
        assert picked.local_time == "03:00"

    def test_a_fold_uses_fold_zero(self, monkeypatch):
        """2026-11-01 01:30 is ambiguous in New York — ``fold=0``
        (the first occurrence, pre-DST) is the canonical one-shot rule."""
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        monkeypatch.setenv(ENV_WINDOW_START, "01:00")
        monkeypatch.setenv(ENV_WINDOW_END, "02:00")
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 10, 25, 12, 0, tzinfo=NYC),
            rng=_FixedRng(90),  # 01:30
        )
        # 01:30 EDT (UTC-4) ⇒ 05:30Z — the FIRST occurrence.
        assert picked.instant_utc == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
        assert picked.local_time == "01:30"
