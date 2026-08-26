"""Usage-limit episode schedule derivation + anchor helpers.

docs/plans/usage-limit-deferral-path.md W5/W6: the stateless wake
schedule (3m → 5m → 10m → 15m cap), the jitter past-now clamp, the
beyond-last-slot contract edge, and the instance-metadata anchor
read/write/clear soft-fail contract.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from daemon.services.usage_limit_schedule import (
    DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS,
    USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
    clear_usage_limit_first_seen,
    live_usage_limit_first_seen,
    next_usage_limit_retry_at,
    parse_usage_limit_first_seen,
    read_usage_limit_first_seen,
    usage_limit_in_window,
    write_usage_limit_first_seen,
)
from tests.helpers.fake_instance_repo import FakeInstanceMetadataRepo

UTC = timezone.utc

DELAYS = (180.0, 300.0, 600.0, 900.0)
CUMSUM = [180.0, 480.0, 1080.0, 1980.0]


def _dt(seconds: float) -> datetime:
    return datetime(2026, 8, 27, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


class TestSlotDerivation:
    """Elapsed-based slot selection across the 6h window."""

    @pytest.mark.parametrize(
        "elapsed,expected_cumsum",
        [
            (0.0, 180.0),      # first sighting → +3 min
            (179.9, 180.0),
            (180.0, 480.0),    # exactly on the first slot → NEXT slot (strictly greater)
            (200.0, 480.0),    # early wake re-selects slot 1 (monotonic)
            (481.0, 1080.0),
            (1079.9, 1080.0),
            (1080.0, 1980.0),
            (1979.0, 1980.0),
        ],
    )
    def test_slot_selection_no_jitter(self, elapsed, expected_cumsum):
        first_seen = _dt(0)
        now = _dt(elapsed)
        wake = next_usage_limit_retry_at(
            first_seen, now, delays=DELAYS, jitter_fraction=0.0, floor_seconds=0.0
        )
        assert wake == first_seen + timedelta(seconds=expected_cumsum)

    def test_default_floor_lifts_imminent_slots(self):
        """Near-slot wakes are clamped to now + floor — never an
        immediate re-attempt (the clamp is unconditional, not
        jitter-only)."""
        first_seen = _dt(0)
        wake = next_usage_limit_retry_at(
            first_seen, _dt(179.9), delays=DELAYS, jitter_fraction=0.0
        )
        assert wake == _dt(179.9 + 30.0)

    def test_beyond_last_slot_extends_by_final_delay(self):
        """Contract edge: past the last listed cumsum (1980), the
        schedule extends by the 900s cap indefinitely until the
        deadline check ends the episode."""
        first_seen = _dt(0)
        # elapsed = 2000 → slots: 1980, 2880 → next is 2880
        wake = next_usage_limit_retry_at(
            first_seen, _dt(2000), delays=DELAYS, jitter_fraction=0.0
        )
        assert wake == first_seen + timedelta(seconds=2880)
        # elapsed = 2900 → 2880, 3780 → next is 3780
        wake = next_usage_limit_retry_at(
            first_seen, _dt(2900), delays=DELAYS, jitter_fraction=0.0
        )
        assert wake == first_seen + timedelta(seconds=3780)
        # exactly on an extension slot → strictly-greater next
        wake = next_usage_limit_retry_at(
            first_seen, _dt(2880), delays=DELAYS, jitter_fraction=0.0
        )
        assert wake == first_seen + timedelta(seconds=3780)

    def test_restart_resumes_window_not_fresh(self):
        """Crash-safety: derivation is from the PERSISTED anchor — a
        restart mid-episode (long elapsed) selects the next absolute
        cumsum slot, never a fresh +180."""
        first_seen = _dt(0)
        wake = next_usage_limit_retry_at(
            first_seen, _dt(5000), delays=DELAYS, jitter_fraction=0.0
        )
        # slots: ..., 4680, 5580 → elapsed 5000 → 5580
        assert wake == first_seen + timedelta(seconds=5580)

    def test_default_delays_match_plan(self):
        assert DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS == (180.0, 300.0, 600.0, 900.0)

    def test_rejects_empty_or_nonpositive_delays(self):
        first_seen = _dt(0)
        with pytest.raises(ValueError):
            next_usage_limit_retry_at(first_seen, _dt(0), delays=[])
        with pytest.raises(ValueError):
            next_usage_limit_retry_at(first_seen, _dt(0), delays=(100.0, 0.0))


class TestJitterClamp:
    """rev3 §3.2 — no roll may schedule into the past."""

    def test_fuzz_no_past_scheduling_across_full_episode(self):
        """Fuzz jitter across a full 6h episode: the result must NEVER
        be ≤ now (clamped to at least now + floor)."""
        first_seen = _dt(0)
        rng = random.Random(20260827)
        for elapsed in range(0, 21601, 37):  # coarse sweep over the window
            now = _dt(elapsed)
            for _ in range(20):  # jitter rolls per point
                wake = next_usage_limit_retry_at(
                    first_seen, now, delays=DELAYS, jitter_fraction=0.1, rng=rng
                )
                assert wake > now, f"past-scheduling at elapsed={elapsed}"

    def test_early_jittered_wake_re_rolls_stay_future(self):
        """The exact rev3 §3.2 scenario: an early-jittered wake at
        elapsed ~ inside slot k re-selects slot k; a negative jitter
        roll must not land before now."""
        first_seen = _dt(0)
        rng = random.Random(7)
        worst = None
        for _ in range(500):
            # elapsed just before the slot boundary — early-wake region
            now = _dt(179.0)
            wake = next_usage_limit_retry_at(
                first_seen, now, delays=DELAYS, jitter_fraction=0.1, rng=rng
            )
            assert wake > now
            worst = wake if worst is None else min(worst, wake)
        # all wakes are anchored at first_seen+180 minus at most 10% of
        # 180s = 18s → the clamp lifts them to >= now + floor anyway.
        assert worst > _dt(179.0)

    def test_jitter_bounds_are_symmetric_fraction(self):
        first_seen = _dt(0)
        rng = random.Random(3)
        lo, hi = None, None
        for _ in range(200):
            wake = next_usage_limit_retry_at(
                first_seen,
                _dt(0),
                delays=DELAYS,
                jitter_fraction=0.1,
                floor_seconds=0.0,  # expose raw jitter bounds
                rng=rng,
            )
            delta = (wake - first_seen).total_seconds()
            lo = delta if lo is None else min(lo, delta)
            hi = delta if hi is None else max(hi, delta)
        # ±10% of the first step (180s) = ±18s
        assert lo >= 162.0
        assert hi <= 198.0


class TestAnchorHelpers:
    """W6 — parse/read/write/clear over instance metadata (soft-fail)."""

    _Repo = FakeInstanceMetadataRepo

    def test_parse_variants(self):
        assert parse_usage_limit_first_seen(None) is None
        assert parse_usage_limit_first_seen("") is None
        assert parse_usage_limit_first_seen("garbage") is None
        parsed = parse_usage_limit_first_seen("2026-08-27T01:02:03Z")
        assert parsed == datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC)
        naive = parse_usage_limit_first_seen("2026-08-27T01:02:03")
        assert naive is not None and naive.tzinfo == UTC

    def test_read_write_clear_roundtrip(self):
        repo = self._Repo()
        iid = "inst-1"
        assert read_usage_limit_first_seen(repo, iid) is None

        stamp = datetime(2026, 8, 27, tzinfo=UTC)
        assert write_usage_limit_first_seen(repo, iid, stamp) is True
        assert read_usage_limit_first_seen(repo, iid) == stamp

        assert clear_usage_limit_first_seen(repo, iid) is True
        assert read_usage_limit_first_seen(repo, iid) is None

    def test_read_uses_targeted_key_accessor(self):
        """Perf contract: the anchor read goes through
        ``get_metadata_value`` (single-key) when available, not the
        full-row ``get``."""
        repo = self._Repo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: datetime.now(UTC).isoformat()}
        )
        read_usage_limit_first_seen(repo, "i")
        assert repo.get_metadata_value_calls == 1

    def test_clear_uses_conditional_delete(self):
        """Perf contract: the anchor clear goes through
        ``delete_metadata_if_present`` (zero-row no-op when absent)."""
        repo = self._Repo()
        clear_usage_limit_first_seen(repo, "i")
        assert repo.delete_if_present_calls == 1

    def test_fallback_path_without_targeted_methods(self):
        """Duck-typed repos without the targeted variants still work
        through the plain get/delete fallbacks."""
        from types import SimpleNamespace

        class _Minimal:
            def __init__(self):
                self._metadata = {}
                self.deleted = []

            def get(self, instance_id):
                return SimpleNamespace(instance_metadata=dict(self._metadata))

            def set_metadata(self, instance_id, key, value):
                self._metadata[key] = value

            def delete_metadata(self, instance_id, key):
                self.deleted.append(key)

        repo = _Minimal()
        assert read_usage_limit_first_seen(repo, "i") is None
        write_usage_limit_first_seen(repo, "i", datetime.now(UTC))
        assert read_usage_limit_first_seen(repo, "i") is not None
        clear_usage_limit_first_seen(repo, "i")
        assert repo.deleted == [USAGE_LIMIT_FIRST_SEEN_METADATA_KEY]

    def test_all_helpers_soft_fail(self):
        repo = self._Repo(fail=True)
        assert read_usage_limit_first_seen(repo, "i") is None
        assert write_usage_limit_first_seen(repo, "i", datetime.now(UTC)) is False
        assert clear_usage_limit_first_seen(repo, "i") is False
        # None repo / None id → no-op, no raise
        assert read_usage_limit_first_seen(None, "i") is None
        assert write_usage_limit_first_seen(None, "i", datetime.now(UTC)) is False
        assert clear_usage_limit_first_seen(None, "i") is False

    def test_live_anchor_gating(self):
        repo = self._Repo(
            {USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: datetime.now(UTC).isoformat()}
        )
        assert live_usage_limit_first_seen(repo, "i") is not None

        # past-deadline anchor → not live (stale episode; default path)
        old = (datetime.now(UTC) - timedelta(seconds=21601)).isoformat()
        stale_repo = self._Repo({USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: old})
        assert live_usage_limit_first_seen(stale_repo, "i") is None

        # absent anchor → not live
        assert live_usage_limit_first_seen(self._Repo(), "i") is None


class TestSharedWindowPredicate:
    """The single boundary predicate used by worker seam + liveness."""

    def test_boundary_is_exclusive_of_deadline(self):
        first_seen = _dt(0)
        assert usage_limit_in_window(first_seen, _dt(0), 21600)
        assert usage_limit_in_window(first_seen, _dt(21599.9), 21600)
        # now == deadline → OUT of window (episode terminalizes)
        assert not usage_limit_in_window(first_seen, _dt(21600), 21600)
        assert not usage_limit_in_window(first_seen, _dt(21601), 21600)


class TestEpisodeConfigValidation:
    """ServicesConfig validation — a loadable typo must not strand
    quota-hit tasks RUNNING forever (local-review finding)."""

    def test_empty_delays_rejected(self):
        from daemon.config import ServicesConfig

        with pytest.raises(ValueError, match="non-empty"):
            ServicesConfig(usage_limit_retry_delays_seconds=[])

    def test_non_positive_delays_rejected(self):
        from daemon.config import ServicesConfig

        with pytest.raises(ValueError, match="positive"):
            ServicesConfig(usage_limit_retry_delays_seconds=[180, 0, -5])

    def test_defaults_and_valid_lists_accepted(self):
        from daemon.config import ServicesConfig

        assert ServicesConfig().usage_limit_retry_delays_seconds == [
            180, 300, 600, 900,
        ]
        assert ServicesConfig(
            usage_limit_retry_delays_seconds=[60]
        ).usage_limit_retry_delays_seconds == [60]
