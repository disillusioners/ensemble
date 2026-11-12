"""Substrate tier — the verdict record, the breach predicate and the
BOUNDED over-count re-arm (test-plan §3.1, cases S6, S7, S13, S14).

PURE-PREDICATE: no real subprocesses, no real RAM.
"""

from __future__ import annotations

import pytest

from daemon.services.ram_guard.verdict import (
    RAM_GUARD_REARM_MAX_EXTRA_BYTES,
    RAM_GUARD_REARM_MIN_GROWTH_BYTES,
    STATE_BREACHED,
    STATE_KILLED,
    STATE_OVERCOUNTED,
    STATE_REFUSED,
    STATE_RELEASED,
    STATE_WATCHING,
    TRIGGER_PSS_CONFIRM_UNAVAILABLE,
    TRIGGER_PSS_THRESHOLD,
    TRIGGER_RSS_THRESHOLD,
    SpawnRecord,
    breaches,
    compute_rearm_threshold,
    confirm_due,
    exposure_window_seconds,
    total_exposure_bound_bytes,
    under_report_bound_bytes,
)

MIB = 1024 * 1024


def make_record(**overrides) -> SpawnRecord:
    """A record with the pinned defaults."""
    kwargs = dict(
        tracking_id="t-1",
        instance_id="i-1",
        lane="bash",
        pid=100,
        pgid=500,
        start_time_jiffies=4242,
        ceiling_bytes=3072 * MIB,
        poll_interval_ms=200,
        grace_ms=250,
    )
    kwargs.update(overrides)
    return SpawnRecord(**kwargs)


# ── The module constants (NOT knobs) ───────────────────────────────────────


def test_rearm_constants_are_the_pinned_values():
    """§15 B1 — the two constants, exactly."""
    assert RAM_GUARD_REARM_MAX_EXTRA_BYTES == 33554432
    assert RAM_GUARD_REARM_MIN_GROWTH_BYTES == 134217728


def test_rearm_constants_are_absent_from_the_config_field_set():
    """They are MODULE CONSTANTS, not config knobs (arch §2 DQ7).

    ⚠️ **PRE-PHASE-5 PROXY** — the knob surface itself
    (``RamGuardConfig``) is PHASE 5's deliverable (arch §4.6), so this
    leg cannot yet assert "the constant is absent from the config
    class" directly.  It asserts the two properties that ARE
    observable today: the constants live on the MODULE (not on the
    record, and not under a knob-shaped name in any module of the
    package).

    **RE-PIN OBLIGATION:** when ``RamGuardConfig`` lands (Phase 5),
    this leg MUST be replaced by a direct assertion against
    ``RamGuardConfig.model_fields`` — the constants
    ``RAM_GUARD_REARM_MAX_EXTRA_BYTES`` /
    ``RAM_GUARD_REARM_MIN_GROWTH_BYTES`` must NOT appear in it (arch
    §4.6: "Module constants (not knobs): the §4.1a pair, …").
    """
    from daemon.services.ram_guard import ceiling as ceiling_mod
    from daemon.services.ram_guard import ladder as ladder_mod
    from daemon.services.ram_guard import verdict as verdict_mod

    # The constants are MODULE attributes — they are not fields of the
    # record (a config could not carry them) and no module of the
    # package exposes a knob-shaped alias for them.
    for module in (verdict_mod, ladder_mod, ceiling_mod):
        for name in dir(module):
            if name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # A knob would be an UPPER-CASE attribute on a config
                # class; these are module constants by construction.
                assert not name.lower().endswith("_mib")
    assert not hasattr(SpawnRecord, "RAM_GUARD_REARM_MAX_EXTRA_BYTES")
    assert not hasattr(SpawnRecord, "RAM_GUARD_REARM_MIN_GROWTH_BYTES")
    # The values are pinned on the module that owns them.
    assert verdict_mod.RAM_GUARD_REARM_MAX_EXTRA_BYTES == 33554432
    assert verdict_mod.RAM_GUARD_REARM_MIN_GROWTH_BYTES == 134217728


# ── The breach predicate ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("total", "ceiling", "expected"),
    [
        (3072 * MIB - 1, 3072 * MIB, False),
        (3072 * MIB, 3072 * MIB, True),
        (3072 * MIB + 1, 3072 * MIB, True),
    ],
)
def test_breach_boundary_is_inclusive(total, ceiling, expected):
    """The breach fires iff ``total >= ceiling`` (inclusive)."""
    assert breaches(total, ceiling) is expected


# ── S7 — the peak is monotonic ─────────────────────────────────────────────


def test_s7_peak_never_decreases():
    """A shrinking tree keeps the recorded peak."""
    record = make_record()
    record.record_sample(500 * MIB, now=1.0)
    record.record_sample(400 * MIB, now=2.0)
    record.record_sample(300 * MIB, now=3.0)
    assert record.observed_peak_bytes == 500 * MIB
    assert record.last_total_bytes == 300 * MIB
    assert record.samples == 3


# ── S14 — the honest-peak bound ────────────────────────────────────────────


def test_s14_under_report_bound_is_the_max_positive_delta():
    """The bound is the MAX POSITIVE sample-to-sample delta."""
    totals = [100 * MIB, 130 * MIB, 120 * MIB, 180 * MIB]
    assert under_report_bound_bytes(totals) == 60 * MIB
    # A shrinking tree yields no bound.
    assert under_report_bound_bytes([100 * MIB, 50 * MIB, 10 * MIB]) == 0


def test_s14_total_exposure_window_includes_the_confirm_term():
    """The total exposure window is ``Δt + confirm_deadline + grace``."""
    window = exposure_window_seconds(200, 250, 250)
    assert window == pytest.approx(0.70)
    # Without the confirm term the window would be 0.45 s.
    assert window != pytest.approx(0.45)
    bound = total_exposure_bound_bytes(100 * MIB, 200, 250, 250)
    assert bound == pytest.approx(100 * MIB * (0.70 / 0.20))


# ── S13 — the BOUNDED over-count re-arm ────────────────────────────────────


def test_s13_rearm_threshold_is_bounded_by_the_cap():
    """The threshold is BOUNDED by the 32 MiB cap."""
    ceiling = 3072 * MIB
    huge_extra = 4 * 1024 * MIB  # far beyond the cap
    threshold = compute_rearm_threshold(0, ceiling, huge_extra)
    assert threshold == ceiling + RAM_GUARD_REARM_MAX_EXTRA_BYTES
    # The UNBOUNDED rule is withdrawn — it is what this asserts against.
    assert threshold != ceiling + huge_extra


def test_s13_rearm_threshold_ratchets_and_never_decreases():
    """The threshold is ratcheted (never decreases)."""
    ceiling = 3072 * MIB
    first = compute_rearm_threshold(0, ceiling, 8 * MIB)
    second = compute_rearm_threshold(first, ceiling, 1 * MIB)
    assert second == first
    third = compute_rearm_threshold(first, ceiling, 16 * MIB)
    assert third == ceiling + 16 * MIB
    assert third > first


def test_s13_confirm_due_requires_the_growth_governor():
    """The confirm is gated by BOTH the threshold and the growth."""
    threshold = 3072 * MIB + RAM_GUARD_REARM_MAX_EXTRA_BYTES
    last_confirm = 3072 * MIB
    # Above the threshold but under the growth governor ⇒ not due.
    assert confirm_due(threshold + 1, threshold, last_confirm) is False
    # Above the threshold AND past the 128 MiB growth ⇒ due.
    assert confirm_due(threshold + RAM_GUARD_REARM_MIN_GROWTH_BYTES, threshold, last_confirm) is True
    # Below the threshold ⇒ never due, however large the growth.
    assert confirm_due(threshold - 1, threshold, last_confirm) is False


def test_s13_overcounted_is_sticky_and_idempotent():
    """``OVERCOUNTED → OVERCOUNTED`` is an idempotent in-place update."""
    record = make_record()
    assert record.state == STATE_WATCHING
    assert record.mark_breached(now=1.0) is True
    assert record.state == STATE_BREACHED

    # The first re-arm.
    assert record.mark_overcounted(
        now=2.0, shared_extra_bytes=8 * MIB, summed_rss_bytes=4000 * MIB
    ) is True
    assert record.state == STATE_OVERCOUNTED
    assert record.rearm_threshold_bytes == 3072 * MIB + 8 * MIB
    assert record.last_confirm_rss_bytes == 4000 * MIB
    assert record.rearm_count == 1
    assert record.confirm_count == 1

    # A second re-arm — in place, ratcheted, NO state re-entry and NO
    # BREACHED re-entry.
    breach_at = record.breach_at
    assert record.mark_overcounted(
        now=3.0, shared_extra_bytes=2 * MIB, summed_rss_bytes=4100 * MIB
    ) is True
    assert record.state == STATE_OVERCOUNTED
    assert record.rearm_threshold_bytes == 3072 * MIB + 8 * MIB  # ratcheted
    assert record.rearm_count == 2
    assert record.confirm_count == 2
    assert record.breach_at == breach_at


def test_s13_rearm_threshold_is_zero_when_unarmed():
    """``rearm_threshold_bytes`` is 0 (never ``None``) when no re-arm was armed."""
    record = make_record()
    assert record.rearm_threshold_bytes == 0
    assert record.rearm_threshold_bytes is not None
    # The effective threshold falls back to the ceiling.
    assert record.effective_threshold_bytes == record.ceiling_bytes


def test_s13_a_later_breach_at_the_rearmed_threshold_is_a_new_episode():
    """After the re-arm the ENFORCED threshold is the re-armed one."""
    record = make_record()
    record.mark_breached(now=1.0)
    record.mark_overcounted(now=2.0, shared_extra_bytes=8 * MIB, summed_rss_bytes=4000 * MIB)
    assert record.effective_threshold_bytes == 3072 * MIB + 8 * MIB
    # The ceiling itself no longer breaches the record.
    assert breaches(3072 * MIB + 1, record.effective_threshold_bytes) is False
    # The re-armed threshold does.
    assert breaches(record.effective_threshold_bytes, record.effective_threshold_bytes) is True


# ── The state machine (first-writer-wins) ──────────────────────────────────


def test_terminal_transitions_are_first_writer_wins():
    """The terminal transitions are first-writer-wins."""
    record = make_record()
    record.mark_breached(now=1.0)
    assert record.mark_killed(now=2.0, reason="killed:ladder") is True
    assert record.state == STATE_KILLED
    assert record.reason == "killed:ladder"
    # A second kill attempt does not overwrite the verdict.
    assert record.mark_killed(now=3.0, reason="killed:term-esrch") is False
    assert record.reason == "killed:ladder"
    assert record.killed_at == 2.0


def test_breach_transition_is_first_writer_wins():
    """WATCHING→BREACHED happens once."""
    record = make_record()
    assert record.mark_breached(now=1.0) is True
    assert record.mark_breached(now=2.0) is False
    assert record.breach_at == 1.0


def test_permanent_refusal_is_terminal_and_transient_rearms_to_watching():
    """§17.2 — a permanent refusal terminates; a transient one re-arms."""
    record = make_record()
    record.mark_breached(now=1.0)
    assert record.mark_refused("refused:group-unknown", permanent=False) is False
    assert record.state == STATE_WATCHING
    assert record.reason == "refused:group-unknown"

    record.mark_breached(now=2.0)
    assert record.mark_refused("refused:pgid-recycled", permanent=True) is True
    assert record.state == STATE_REFUSED
    assert record.is_terminal is True


def test_release_transition_is_first_writer_wins():
    """→ RELEASED happens once."""
    record = make_record()
    record.mark_breached(now=1.0)
    record.mark_killed(now=2.0, reason="killed:ladder")
    assert record.mark_released() is True
    assert record.state == STATE_RELEASED
    assert record.mark_released() is False


def test_trigger_vocabulary_is_the_pinned_triple():
    """The trigger vocabulary is exactly the three values."""
    from daemon.services.ram_guard.verdict import TRIGGER_VALUES

    assert TRIGGER_VALUES == (
        "rss_threshold",
        "pss_threshold",
        "pss_confirm_unavailable",
    )
    assert TRIGGER_RSS_THRESHOLD == "rss_threshold"
    assert TRIGGER_PSS_THRESHOLD == "pss_threshold"
    assert TRIGGER_PSS_CONFIRM_UNAVAILABLE == "pss_confirm_unavailable"


def test_record_carries_every_published_field():
    """§4.4 — the record carries every published field."""
    record = make_record()
    for field in (
        "tracking_id",
        "instance_id",
        "lane",
        "pid",
        "pgid",
        "start_time_jiffies",
        "ceiling_bytes",
        "poll_interval_ms",
        "grace_ms",
        "state",
        "reason",
        "observed_peak_bytes",
        "last_total_bytes",
        "samples",
        "metric",
        "trigger_metric",
        "peak_is_lower_bound",
        "under_report_bound_bytes",
        "confirmed_pss_bytes",
        "tree_complete",
        "members_total",
        "members_killable",
        "members_unreachable",
        "unreadable_pids",
        "gone_pids",
        "enumeration_failures",
        "breach_at",
        "killed_at",
        "last_sample_at",
        "trigger",
        "rearm_threshold_bytes",
        "last_confirm_rss_bytes",
        "rearm_count",
        "confirm_count",
    ):
        assert hasattr(record, field), field
