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


# ══════════════════════════════════════════════════════════════════════════
# P5 Task 5.2 — the random-picker unit tests + the BR-5 DST legs
#
# APPENDED to the P1 file (the P1 legs above are UNTOUCHED — this block is a
# strictly additive diff).  Plan:
# .agents/shared/planning/maintenance-system/phase5-plan.md §Task 5.2
# (lines 62-69).
#
# SAFETY FENCE: pure unit tests — no database, no daemon, no live contact.
# The timezone database is the host's.
#
# The imports below are APPENDED (not hoisted) on purpose: the diff against
# the P1 file stays purely additive, so a reviewer can verify "no existing
# green test was rewritten" by inspection of the diff alone.
# ══════════════════════════════════════════════════════════════════════════

import ast
import pathlib
import sys
import types

import daemon.services.maintenance_schedule_knobs as knobs_module
from daemon.services.maintenance_schedule_knobs import WEEKDAY_NAMES
from daemon.util.tz import anchor_local_to_utc

#: The knobs module under test (the structural pins read its SOURCE).
KNOBS_MODULE_PATH = pathlib.Path(knobs_module.__file__)

#: Uniformity-smoke parameters — FIXED seeds over a FIXED tolerance band.
#:
#: The seeds are fixed (``range(20000)``), so the draw is DETERMINISTIC:
#: the bucket vector is a CONSTANT, not a sample.  The band is therefore a
#: REGRESSION band (it catches a clustered / skewed picker) and not a
#: statistical one — it can never flake.  This is the "bucket-count with a
#: fixed tolerance (or a deterministic seed)" the plan demands; the loose
#: "roughly even" assertion is NOT relied upon here.
#:
#: Observed on this branch: [5104, 5012, 5036, 4848] — mean 5000, band
#: [4800, 5200] (±4% of the mean).
UNIFORMITY_DRAWS = 20_000
UNIFORMITY_BUCKET_MINUTES = 60
UNIFORMITY_TOLERANCE = 0.04

#: A fixed reference instant for the picker (Sunday 00:00 ICT).  Every drawn
#: minute (01:00–04:59) is still in the future ⇒ no "+7d re-anchor" ⇒ the
#: returned ``local_time`` is exactly the drawn minute.
UNIFORMITY_NOW = datetime(2026, 11, 22, 0, 0, tzinfo=ICT)


def _minute_of_day(picked) -> int:
    """The picked minute as minutes since local midnight."""
    return int(picked.local_time[:2]) * 60 + int(picked.local_time[3:])


def _bucket_counts(knobs, *, seeds, now) -> list[int]:
    """Bucket the picker draws into fixed 60-minute buckets.

    The bucket index is derived from the picked LOCAL time, so a picker
    that drifts out of the window is visible as a miscount, not hidden.
    """
    bucket_count = knobs.candidate_minutes // UNIFORMITY_BUCKET_MINUTES
    counts = [0] * bucket_count
    for seed in seeds:
        picked = pick_next_fire_instant(
            knobs, now=now, rng=random.Random(seed)
        )
        index = (_minute_of_day(picked) - knobs.start_minute) // (
            UNIFORMITY_BUCKET_MINUTES
        )
        counts[index] += 1
    return counts


class _RecordingRng:
    """A ``randrange`` stub that RECORDS the bounds it was asked for."""

    def __init__(self, value: int | None = None):
        self.calls: list[tuple[int, int]] = []
        self._value = value

    def randrange(self, start: int, end: int) -> int:
        self.calls.append((start, end))
        return start if self._value is None else self._value


class _CroniterTripwire(types.ModuleType):
    """A fake ``croniter`` module that explodes on ANY attribute use.

    Installed into ``sys.modules`` for the duration of a test: if the
    chain path ever reaches for croniter (top-level import, lazy import
    inside a function, ``from croniter import ...``), the tripwire fires
    instead of the real library — the leg then fails loudly instead of
    passing vacuously.
    """

    def __getattr__(self, name):
        raise AssertionError(
            f"croniter.{name} was touched on the chain path — the picker "
            "must compute the instant directly (no cron expression)"
        )


def _croniter_references(source: str) -> list[str]:
    """Every AST-level reference to croniter in ``source``.

    Docstrings and comments are EXCLUDED by construction (an AST walk
    never sees them as names/imports) — the module's own docstring
    legitimately says "no croniter", so a text grep would false-RED.
    """
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "croniter" or alias.name.startswith("croniter."):
                    hits.append(f"import {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "croniter" or module.startswith("croniter."):
                hits.append(f"from {module} import ... (line {node.lineno})")
            for alias in node.names:
                if alias.name == "croniter":
                    hits.append(
                        f"from {module} import croniter (line {node.lineno})"
                    )
        elif isinstance(node, ast.Name) and node.id == "croniter":
            hits.append(f"name 'croniter' (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr == "croniter":
            hits.append(f"attribute '.croniter' (line {node.lineno})")
    return hits


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 1 — the uniformity smoke (FIXED tolerance / FIXED seeds)
# ══════════════════════════════════════════════════════════════════════════


class TestP5Uniformity:
    def test_the_bucket_counts_stay_within_a_fixed_tolerance(self):
        """Bucket-count with a FIXED band over FIXED seeds.

        The seeds are fixed (``range(20000)``) ⇒ the vector is a
        constant ⇒ the leg can never flake.  The band (±4% of the
        per-bucket mean) is the regression guard: a clustered or skewed
        picker leaves the band immediately.
        """
        knobs = read_maintenance_knobs()
        counts = _bucket_counts(
            knobs, seeds=range(UNIFORMITY_DRAWS), now=UNIFORMITY_NOW
        )
        assert sum(counts) == UNIFORMITY_DRAWS, counts
        expected = UNIFORMITY_DRAWS / len(counts)
        low = expected * (1 - UNIFORMITY_TOLERANCE)
        high = expected * (1 + UNIFORMITY_TOLERANCE)
        for index, count in enumerate(counts):
            assert low <= count <= high, (
                f"bucket {index}={count} left the fixed band "
                f"[{low}, {high}] (counts={counts})"
            )

    def test_the_bucket_counts_are_reproducible_under_the_fixed_seeds(self):
        """The same seeds ⇒ the same vector — twice, independently.

        A picker that leaked hidden state (a module-level RNG, a cached
        knob read) would drift between the two runs.
        """
        knobs = read_maintenance_knobs()
        first = _bucket_counts(knobs, seeds=range(2_000), now=UNIFORMITY_NOW)
        second = _bucket_counts(knobs, seeds=range(2_000), now=UNIFORMITY_NOW)
        assert first == second
        assert len(first) == 4
        assert sum(first) == 2_000

    def test_an_injected_rng_leaves_the_global_rng_untouched(self):
        """The injected RNG is what draws — the global stream is intact."""
        knobs = read_maintenance_knobs()
        saved = random.getstate()
        try:
            random.seed(20261122)
            before = random.getstate()
            picked = pick_next_fire_instant(
                knobs, now=UNIFORMITY_NOW, rng=random.Random(7)
            )
            assert picked.local_time is not None
            assert random.getstate() == before, (
                "the global random stream advanced — the injected RNG was "
                "not the one that drew"
            )
        finally:
            random.setstate(saved)

    def test_the_global_rng_is_the_documented_fallback(self):
        """With no injected RNG the picker falls back to the ``random``
        module — and advances the global stream."""
        knobs = read_maintenance_knobs()
        saved = random.getstate()
        try:
            random.seed(20261122)
            before = random.getstate()
            pick_next_fire_instant(knobs, now=UNIFORMITY_NOW, rng=None)
            assert random.getstate() != before
        finally:
            random.setstate(saved)


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 2 — the [start, end) inclusivity (start reachable, end EXCLUDED)
# ══════════════════════════════════════════════════════════════════════════


class TestP5WindowInclusivity:
    def test_the_default_window_offers_exactly_240_candidate_minutes(self):
        """01:00–05:00 ⇒ ``randrange(60, 300)`` ⇒ 240 candidates.

        The BOUNDS are read off the picker (recorded), not inferred from
        the knob values — a picker that silently changed to an inclusive
        end would show up here as (60, 301).
        """
        knobs = read_maintenance_knobs()
        recorder = _RecordingRng()
        pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=recorder,
        )
        assert recorder.calls == [(60, 300)]
        assert knobs.start_minute == 60
        assert knobs.end_minute == 300
        assert knobs.candidate_minutes == 240
        assert recorder.calls[0][1] - recorder.calls[0][0] == 240

    def test_the_draw_bounds_follow_the_knobs_not_a_hardcoded_default(
        self, monkeypatch
    ):
        """A different window ⇒ different draw bounds (the plumbing)."""
        monkeypatch.setenv(ENV_WINDOW_START, "02:15")
        monkeypatch.setenv(ENV_WINDOW_END, "04:45")
        knobs = read_maintenance_knobs()
        recorder = _RecordingRng()
        pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=recorder,
        )
        assert recorder.calls == [(135, 285)]
        assert knobs.candidate_minutes == 150

    def test_the_exclusive_end_minute_is_never_drawn(self):
        """05:00 itself is NEVER drawn (the end is exclusive)."""
        knobs = read_maintenance_knobs()
        drawn = set()
        for seed in range(2_000):
            picked = pick_next_fire_instant(
                knobs,
                now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
                rng=random.Random(seed),
            )
            minute = _minute_of_day(picked)
            drawn.add(minute)
            assert knobs.start_minute <= minute < knobs.end_minute
        assert knobs.end_minute not in drawn, "the window end must be excluded"
        assert knobs.start_minute in drawn, "the window start must be reachable"


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 3 — the weekday boundary
# ══════════════════════════════════════════════════════════════════════════


class TestP5WeekdayBoundary:
    def test_the_next_weekday_d_is_chosen_from_a_non_matching_today(self, monkeypatch):
        """Today is Sunday; the knob says Tuesday ⇒ the NEXT Tuesday."""
        monkeypatch.setenv(ENV_WEEKDAY, "2")
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 6, 0, tzinfo=ICT),  # a Sunday
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.strftime("%A") == "Tuesday"
        assert local.date() == datetime(2026, 11, 24).date()
        assert local.hour == 1 and local.minute == 0

    def test_a_today_past_window_re_anchors_by_exactly_seven_days(self):
        """The re-anchor is a DAY shift of exactly +7 — the wall-clock
        minute is preserved and the weekday name is unchanged."""
        knobs = read_maintenance_knobs()
        now = datetime(2026, 11, 22, 6, 0, tzinfo=ICT)  # a Sunday
        picked = pick_next_fire_instant(knobs, now=now, rng=_FixedRng(60))
        local = picked.instant_utc.astimezone(ICT)
        assert (local.date() - now.date()).days == 7
        assert local.strftime("%A") == "Sunday"
        assert local.hour == 1 and local.minute == 0
        assert local > now

    @pytest.mark.parametrize(
        "knob_weekday,expected_date",
        [
            (0, datetime(2026, 11, 29).date()),  # Sunday
            (1, datetime(2026, 11, 30).date()),  # Monday
            (2, datetime(2026, 11, 24).date()),  # Tuesday
            (3, datetime(2026, 11, 25).date()),  # Wednesday
            (4, datetime(2026, 11, 26).date()),  # Thursday
            (5, datetime(2026, 11, 27).date()),  # Friday
            (6, datetime(2026, 11, 28).date()),  # Saturday
        ],
    )
    def test_the_weekday_conversion_holds_from_a_monday_reference(
        self, monkeypatch, knob_weekday, expected_date
    ):
        """The Sunday-based knob → Python's Monday-based ``weekday()``.

        The reference ``now`` is a MONDAY on purpose: a Sunday-based
        reference cannot distinguish an off-by-one in the ``(D + 6) % 7``
        conversion, a Monday-based one can.
        """
        monkeypatch.setenv(ENV_WEEKDAY, str(knob_weekday))
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 23, 12, 0, tzinfo=ICT),  # a Monday
            rng=_FixedRng(60),
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.date() == expected_date
        assert local.strftime("%A") == WEEKDAY_NAMES[knob_weekday]
        assert local.hour == 1 and local.minute == 0


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 4 — the ICT tz conversion (fixed UTC+7, no DST)
# ══════════════════════════════════════════════════════════════════════════


class TestP5TimezoneConversion:
    def test_ict_is_a_fixed_utc_plus_seven_offset_with_no_dst(self):
        """ICT never moves: +07:00 in January AND in July."""
        january = datetime(2026, 1, 15, 12, 0, tzinfo=ICT)
        july = datetime(2026, 7, 15, 12, 0, tzinfo=ICT)
        assert january.utcoffset() == timedelta(hours=7)
        assert july.utcoffset() == timedelta(hours=7)
        assert january.utcoffset() == july.utcoffset()
        assert january.astimezone(timezone.utc).hour == 5
        assert july.astimezone(timezone.utc).hour == 5

    def test_a_naive_now_is_read_as_local_wall_clock(self):
        """A naive ``now`` is local wall-clock ⇒ 01:00 ICT ⇒ 18:00Z the
        day before."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0),  # NAIVE
            rng=_FixedRng(60),
        )
        assert picked.instant_utc == datetime(
            2026, 11, 21, 18, 0, tzinfo=timezone.utc
        )
        assert picked.run_at_iso == "2026-11-21T18:00:00+00:00"

    def test_the_instant_matches_the_canonical_tz_anchor(self):
        """The picker's instant IS the canonical anchor's instant.

        ``daemon.util.tz.anchor_local_to_utc`` is the house anchor; the
        picker must land on exactly that instant for the same naive
        local wall-clock value (no second, hidden conversion step).
        """
        knobs = read_maintenance_knobs()
        anchored, warning = anchor_local_to_utc(datetime(2026, 11, 22, 1, 0), ICT)
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        assert picked.instant_utc == anchored.astimezone(timezone.utc)
        assert warning == ""  # ICT has no DST — nothing to warn about
        assert picked.warnings == ()


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 5 — the invalid-window fail-closed
# ══════════════════════════════════════════════════════════════════════════


class TestP5FailClosed:
    def test_an_invalid_window_arms_nothing_and_says_so_loudly(self, monkeypatch):
        """start ≥ end ⇒ NO knob set ⇒ NO arm, and the refusal is loud.

        The loud surface on the knob-validation path is the RAISE: an
        operator-facing :class:`MaintenanceKnobError` whose message names
        BOTH offending knobs and states the refusal.  (The knob path does
        not emit a separate log record — only the tz-anchor path logs;
        asserting a log record here would be a false-RED.  The log-record
        surface IS pinned, on the DST legs below.)
        """
        monkeypatch.setenv(ENV_WINDOW_START, "05:00")
        monkeypatch.setenv(ENV_WINDOW_END, "01:00")
        with pytest.raises(MaintenanceKnobError) as exc:
            read_maintenance_knobs()
        message = str(exc.value)
        assert ENV_WINDOW_START in message
        assert ENV_WINDOW_END in message
        assert "refusing to arm" in message
        assert "fail-closed" in message
        assert "midnight" in message

    @pytest.mark.parametrize(
        "raw_start,raw_end",
        [
            ("05:00", "01:00"),  # midnight-crossing
            ("03:00", "03:00"),  # empty window
            ("00:00", "00:00"),  # empty window at midnight
            ("23:00", "00:00"),  # midnight-crossing at the end of the day
        ],
    )
    def test_every_invalid_window_pair_raises_before_any_knob_set(
        self, monkeypatch, raw_start, raw_end
    ):
        """No invalid window yields a knob set ⇒ the picker can never arm.

        The raise is the fail-closed: there is no degraded "best effort"
        knob set to arm with.
        """
        monkeypatch.setenv(ENV_WINDOW_START, raw_start)
        monkeypatch.setenv(ENV_WINDOW_END, raw_end)
        with pytest.raises(MaintenanceKnobError):
            read_maintenance_knobs()


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 6 — NO croniter on the chain path
# ══════════════════════════════════════════════════════════════════════════


class TestP5NoCroniter:
    def test_the_module_source_never_references_croniter(self):
        """AST pin — docstrings/comments excluded by construction.

        The module's own docstring says "no croniter", so a text grep
        would false-RED; the AST sees only imports and name references.
        """
        source = KNOBS_MODULE_PATH.read_text(encoding="utf-8")
        hits = _croniter_references(source)
        assert hits == [], f"croniter references on the chain path: {hits}"

    def test_the_canonical_tz_anchor_is_the_imported_path(self):
        """The positive half of the structural pin: the module imports
        ``anchor_local_to_utc`` from ``daemon.util.tz`` — the canonical
        anchor (ADR-008), not a home-grown offset computation."""
        source = KNOBS_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_from_tz = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "daemon.util.tz":
                imported_from_tz.update(alias.name for alias in node.names)
        assert "anchor_local_to_utc" in imported_from_tz

    def test_no_croniter_is_imported_at_pick_time(self, monkeypatch):
        """Runtime tripwire: a fake ``croniter`` in ``sys.modules`` that
        explodes on ANY attribute use — a lazy import inside the picker
        would fire it."""
        monkeypatch.setitem(sys.modules, "croniter", _CroniterTripwire("croniter"))
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        assert picked.local_time == "01:00"

    def test_the_croniter_tripwire_is_not_vacuous(self, monkeypatch):
        """Control for the leg above: the tripwire really fires."""
        monkeypatch.setitem(sys.modules, "croniter", _CroniterTripwire("croniter"))
        with pytest.raises(AssertionError):
            import croniter  # noqa: PLC0415 — deliberate tripwire probe

            croniter.croniter("* * * * *")

    def test_the_picker_calls_the_anchor_exactly_once_per_pick(self, monkeypatch):
        """The anchor is called ONCE and its ``(aware, warning)`` TUPLE is
        consumed — the warning is surfaced, never discarded (BR-5)."""
        calls: list[tuple] = []
        real = knobs_module.anchor_local_to_utc

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(knobs_module, "anchor_local_to_utc", spy)
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 11, 22, 0, 0, tzinfo=ICT),
            rng=_FixedRng(60),
        )
        assert len(calls) == 1, calls
        # the anchor was handed the NAIVE local wall-clock value
        assert calls[0][0][0] == datetime(2026, 11, 22, 1, 0)
        # the tuple was unpacked (a non-tuple would have raised) and the
        # warning side is what the picker surfaces.
        assert picked.warnings == ()


# ══════════════════════════════════════════════════════════════════════════
# T5.2 leg 7 — BR-5: the DST gap / fold legs (operative)
# ══════════════════════════════════════════════════════════════════════════


class TestP5DstHonesty:
    def test_the_gap_day_instant_is_deterministic_and_the_warning_is_logged(
        self, monkeypatch, caplog
    ):
        """2026-03-08 (US spring-forward) — 02:30 does not exist.

        The anchored instant is DETERMINISTIC (03:00 EDT = 07:00Z), it
        falls INSIDE the [01:00, 05:00) window, and the non-empty
        ``anchor_local_to_utc`` warning is surfaced BOTH on the pick
        (``picked.warnings``) and in the LOG (never swallowed).
        """
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        knobs = read_maintenance_knobs()
        with caplog.at_level("WARNING", logger="daemon.services.maintenance_schedule_knobs"):
            picked = pick_next_fire_instant(
                knobs,
                now=datetime(2026, 3, 1, 12, 0, tzinfo=NYC),  # a Sunday
                rng=_FixedRng(150),  # 02:30 — inside the gap
            )
        assert picked.instant_utc == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)
        assert picked.local_time == "03:00"
        assert picked.warnings, "the anchor warning must never be swallowed"
        assert "shifted-forward" in picked.warnings[0]
        picker_records = [
            record
            for record in caplog.records
            if record.name == "daemon.services.maintenance_schedule_knobs"
        ]
        assert picker_records, "the anchor warning must be surfaced in the log"
        assert any(
            "shifted-forward" in record.getMessage() for record in picker_records
        )

    def test_the_gap_day_matches_the_canonical_anchor(self, monkeypatch):
        """The picker's instant IS the canonical anchor's instant, and the
        warning is MIRRORED (the tuple is consumed, never discarded)."""
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        knobs = read_maintenance_knobs()
        anchored, warning = anchor_local_to_utc(datetime(2026, 3, 8, 2, 30), NYC)
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 3, 1, 12, 0, tzinfo=NYC),
            rng=_FixedRng(150),
        )
        assert warning, "the gap path MUST produce a non-empty warning"
        assert picked.instant_utc == anchored.astimezone(timezone.utc)
        assert picked.warnings == (warning,)

    def test_the_fold_day_instant_is_deterministic_and_inside_the_window(
        self, monkeypatch
    ):
        """2026-11-01 (US fall-back) — 01:30 is AMBIGUOUS.

        ``fold=0`` (the first occurrence, pre-DST) is the canonical
        one-shot rule ⇒ 01:30 EDT = 05:30Z, deterministically.  A FOLD
        needs no shift, so the canonical anchor returns an EMPTY warning
        — the honesty pin is that the picker MIRRORS the anchor's warning
        EXACTLY (consumed, never discarded): it must neither swallow a
        warning the anchor did raise, nor invent one it did not.
        """
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        monkeypatch.setenv(ENV_WINDOW_START, "01:00")
        monkeypatch.setenv(ENV_WINDOW_END, "02:00")
        knobs = read_maintenance_knobs()
        anchored, warning = anchor_local_to_utc(datetime(2026, 11, 1, 1, 30), NYC)
        picked = pick_next_fire_instant(
            knobs,
            now=datetime(2026, 10, 25, 12, 0, tzinfo=NYC),  # a Sunday
            rng=_FixedRng(90),  # 01:30
        )
        assert picked.instant_utc == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
        assert picked.instant_utc == anchored.astimezone(timezone.utc)
        assert picked.local_time == "01:30"
        assert picked.warnings == ((warning,) if warning else ())

    def test_a_window_entirely_inside_the_gap_always_fails_closed(
        self, monkeypatch, caplog
    ):
        """The whole window [02:00, 02:59) is INSIDE the 2026-03-08 gap.

        Every drawn minute is shifted forward to 03:00, which is OUTSIDE
        the window ⇒ NO arm, for EVERY draw (fail-closed, BR-5) — and the
        tz warning is surfaced in the log before the refusal (never
        swallowed).
        """
        monkeypatch.setenv(ENV_TIMEZONE, "America/New_York")
        monkeypatch.setenv(ENV_WINDOW_START, "02:00")
        monkeypatch.setenv(ENV_WINDOW_END, "02:59")
        knobs = read_maintenance_knobs()
        assert knobs.candidate_minutes == 59
        for minute in (120, 140, 178):
            with caplog.at_level(
                "WARNING", logger="daemon.services.maintenance_schedule_knobs"
            ):
                with pytest.raises(MaintenanceKnobError) as exc:
                    pick_next_fire_instant(
                        knobs,
                        now=datetime(2026, 3, 1, 12, 0, tzinfo=NYC),
                        rng=_FixedRng(minute),
                    )
            message = str(exc.value)
            assert "fail-closed" in message
            assert "outside" in message
            assert "refusing to arm" in message
            picker_records = [
                record
                for record in caplog.records
                if record.name == "daemon.services.maintenance_schedule_knobs"
            ]
            assert picker_records, "the anchor warning must be surfaced"
            assert any(
                "shifted-forward" in record.getMessage() for record in picker_records
            )


# ══════════════════════════════════════════════════════════════════════════
# P5B F2 — the weekday is anchored on the LOCAL window date, across a
# UTC-date boundary (the staged-boot proof, 2026-11-22)
# ══════════════════════════════════════════════════════════════════════════


class TestP5bUtcDateBoundaryWeekday:
    """The window is a LOCAL clock window, so the weekday knob must be
    evaluated on the LOCAL calendar date — never on the UTC date of the
    stored ``run_at_utc``. ICT is UTC+7, so every window minute sits
    across a UTC-date boundary: Sunday-local fires land on the PRIOR
    UTC date (Sun 04:15 ICT = Sat 21:15Z), and a boot whose UTC date is
    Sunday is ALREADY Monday locally.

    The staged boot (RESULTS §2, 20:47:13Z Sunday-UTC / Monday-ICT,
    weekday=0) seeded ``run_at_utc=2026-11-28T21:15Z`` = Sunday
    2026-11-29 04:15 ICT — the correct LOCAL-Sunday pick. A picker that
    anchored on the UTC date would have answered ``2026-11-22T21:15Z`` =
    MONDAY 04:15 ICT instead. These pins freeze the local anchoring in
    both boundary directions.
    """

    BOOT_NOW = datetime(2026, 11, 22, 20, 47, 13, tzinfo=timezone.utc)

    def test_boot_crossing_the_utc_date_boundary_picks_the_next_local_sunday(self):
        """The staged-boot shape: the reference instant is a Sunday on
        the UTC calendar but a MONDAY locally (ICT). The next Sunday is
        picked on the LOCAL calendar — 2026-11-29 ICT — whose UTC
        instant lands on the PRIOR UTC date."""
        knobs = read_maintenance_knobs()  # weekday 0 (Sunday), 01:00–05:00 ICT
        picked = pick_next_fire_instant(
            knobs, now=self.BOOT_NOW, allow_today=True, rng=_FixedRng(255)
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.strftime("%A") == "Sunday"
        assert local.date() == datetime(2026, 11, 29).date()
        assert local.hour == 4 and local.minute == 15
        # The stored UTC instant lands on the PRIOR UTC date — the
        # boundary the operator-facing docs call out.
        assert picked.instant_utc.date() == datetime(2026, 11, 28).date()
        assert picked.instant_utc.strftime("%A") == "Saturday"

    def test_the_seed_matches_the_v03134_staged_boot_row_exactly(self):
        """Reproduces the verbatim chain row from the staged boot
        (RESULTS §2): run_at_utc=2026-11-28T21:15:00+00:00 — proving the
        seeded row IS the Sunday-local answer."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs, now=self.BOOT_NOW, allow_today=True, rng=_FixedRng(255)
        )
        assert picked.run_at_iso == "2026-11-28T21:15:00+00:00"
        assert picked.local_time == "04:15"
        assert picked.weekday == 0

    def test_a_utc_date_anchored_weekday_would_answer_monday_local(self):
        """The F2 failure shape, pinned as a NEGATIVE: anchoring the
        weekday on the UTC date of the boot (Sunday 2026-11-22) would
        keep 'today' eligible and answer 2026-11-22T21:15Z — Monday
        04:15 ICT. The local anchor must NOT produce that instant."""
        knobs = read_maintenance_knobs()
        picked = pick_next_fire_instant(
            knobs, now=self.BOOT_NOW, allow_today=True, rng=_FixedRng(255)
        )
        utc_anchored_wrong_answer = datetime(
            2026, 11, 22, 21, 15, tzinfo=timezone.utc
        )
        assert picked.instant_utc != utc_anchored_wrong_answer
        local_wrong = utc_anchored_wrong_answer.astimezone(ICT)
        assert local_wrong.strftime("%A") == "Monday"
        assert picked.instant_utc.astimezone(ICT).strftime("%A") == "Sunday"

    def test_local_sunday_reference_before_the_window_keeps_local_today(self):
        """The boundary in the other direction: Saturday-UTC 17:00Z is
        Sunday 00:00 ICT — today IS the target weekday locally, and the
        drawn minute is still ahead, so allow_today keeps LOCAL today
        (2026-11-29), never rolling a week because the UTC date says
        Saturday."""
        knobs = read_maintenance_knobs()
        now = datetime(2026, 11, 28, 17, 0, tzinfo=timezone.utc)
        picked = pick_next_fire_instant(
            knobs, now=now, allow_today=True, rng=_FixedRng(255)
        )
        local = picked.instant_utc.astimezone(ICT)
        assert local.date() == datetime(2026, 11, 29).date()
        assert local.strftime("%A") == "Sunday"
        assert (local.date() - now.astimezone(ICT).date()).days == 0
        assert picked.instant_utc.date() == datetime(2026, 11, 28).date()
