"""Trigger pack — the three wedge predicates (T29 / AC42 / D2 / D3 / D4b / D7 / D13).

WHAT THIS PACK PROVES
---------------------
* every threshold on BOTH sides (the CPU, lag and concentration lines);
* the **WALL-TIME** sustain window (D4b): the same series yields the same
  verdict at a 1 s and a 5 s tick cadence, and a drop below the threshold
  RESETS the window;
* the **cooldown / episode de-dup** (D13): a sustained wedge produces
  exactly ONE decision per cooldown window per reason, and two DIFFERENT
  reasons in one window produce TWO decisions — and (W1) the cooldown is
  stamped ONLY on the ACTUAL capture (the capture path's caller-ack),
  so a decision the watchdog could not service stays armed for the
  next tick;
* the **concentration absolute floor** (D7 / F-06): a high ratio over a
  near-idle process does NOT fire, and the single-sample window case
  (no elapsed span) uses that tick's own percentage rather than an
  epsilon-division that would make the floor trivially true;
* **A1**: an unset / ``initializing`` / ``stopped`` / ``unknown`` probe is
  NEVER consumed — a number that does not exist never becomes a breach.

The pack drives the PURE functions (``evaluate`` / ``TriggerEngine``)
directly: no threads, no I/O, no clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.services.wedge_capture.config import WedgeCaptureConfig
from daemon.services.wedge_capture.lag_probe import (
    PROBE_INITIALIZING,
    PROBE_OK,
    PROBE_STOPPED,
    PROBE_UNKNOWN,
)
from daemon.services.wedge_capture.triggers import (
    CANONICAL_REASONS,
    CONCENTRATION_ABSOLUTE_FLOOR_PCT,
    REASON_MANUAL,
    REASON_SUSTAINED_CPU,
    REASON_SUSTAINED_LOOP_LAG,
    REASON_THREAD_CONCENTRATION,
    TriggerEngine,
    TriggerSample,
    evaluate,
)


# ── builders ───────────────────────────────────────────────────────────────


def _config(tmp_path: Path, **overrides) -> WedgeCaptureConfig:
    """A config with the production defaults, rooted at ``tmp_path``."""
    return WedgeCaptureConfig(capture_dir=tmp_path, **overrides)


def _cpu_series(
    *,
    pct: float,
    seconds: float,
    cadence: float = 1.0,
    delta_s: float = 0.9,
) -> list[TriggerSample]:
    """A CPU-only series at ``cadence``, spanning ``seconds``."""
    ticks = max(1, int(round(seconds / cadence)) + 1)
    return [
        TriggerSample(
            t=index * cadence,
            process_cpu_pct=pct,
            process_cpu_delta_s=delta_s * cadence,
        )
        for index in range(ticks)
    ]


def _lag_series(
    *,
    lag_s: float,
    seconds: float,
    cadence: float = 1.0,
    state: str = PROBE_OK,
    lag_value: float | None = None,
) -> list[TriggerSample]:
    """A lag-only series (CPU idle) at ``cadence``, spanning ``seconds``."""
    ticks = max(1, int(round(seconds / cadence)) + 1)
    return [
        TriggerSample(
            t=index * cadence,
            process_cpu_pct=1.0,
            process_cpu_delta_s=0.01 * cadence,
            lag_s=lag_s if lag_value is None else lag_value,
            lag_state=state,
        )
        for index in range(ticks)
    ]


def _concentration_series(
    *,
    seconds: float,
    share_pct: float = 95.0,
    process_delta_s: float = 0.9,
    native_id: int = 4242,
    comm: str = "MainThread",
    cadence: float = 1.0,
) -> list[TriggerSample]:
    """A series where ONE thread holds ``share_pct`` of the process CPU."""
    ticks = max(1, int(round(seconds / cadence)) + 1)
    return [
        TriggerSample(
            t=index * cadence,
            process_cpu_pct=95.0,
            process_cpu_delta_s=process_delta_s * cadence,
            top_thread_delta_s=process_delta_s * cadence * (share_pct / 100.0),
            top_thread_share_pct=share_pct,
            top_thread_native_id=native_id,
            top_thread_comm=comm,
        )
        for index in range(ticks)
    ]


def _reasons(decisions) -> list[str]:
    return [decision.reason for decision in decisions]


# ── AC42: the thresholds, both sides ───────────────────────────────────────


class TestCpuThreshold:
    def test_fires_at_the_threshold_sustained(self, tmp_path: Path) -> None:
        config = _config(tmp_path)  # 95% / 30s
        decisions = evaluate(_cpu_series(pct=95.0, seconds=30.0), config)
        assert _reasons(decisions) == [REASON_SUSTAINED_CPU]
        observed = decisions[0].observed
        assert observed["sustained_s"] == pytest.approx(30.0)
        assert observed["threshold_pct"] == pytest.approx(95.0)

    def test_does_not_fire_below_the_threshold(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        decisions = evaluate(_cpu_series(pct=94.9, seconds=60.0), config)
        assert decisions == []

    def test_does_not_fire_before_the_sustain_window(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        decisions = evaluate(_cpu_series(pct=99.0, seconds=10.0), config)
        assert decisions == []

    def test_the_since_is_the_start_of_the_over_threshold_run(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        samples = _cpu_series(pct=99.0, seconds=30.0)
        decisions = evaluate(samples, config)
        assert decisions[0].since == pytest.approx(samples[0].t)


class TestLagThreshold:
    def test_fires_at_the_threshold_sustained(self, tmp_path: Path) -> None:
        config = _config(tmp_path)  # lag 5.0s / 15s
        decisions = evaluate(_lag_series(lag_s=5.0, seconds=15.0), config)
        assert _reasons(decisions) == [REASON_SUSTAINED_LOOP_LAG]
        assert decisions[0].observed["lag_s"] == pytest.approx(5.0)

    def test_does_not_fire_below_the_threshold(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        decisions = evaluate(_lag_series(lag_s=4.99, seconds=60.0), config)
        assert decisions == []

    def test_does_not_fire_before_the_sustain_window(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        decisions = evaluate(_lag_series(lag_s=30.0, seconds=5.0), config)
        assert decisions == []

    def test_a_lag_episode_does_not_fire_the_cpu_trigger(
        self, tmp_path: Path
    ) -> None:
        """The H2 shape: a stall with NO CPU peg must be caught by lag
        ALONE. This is the negative half of the H1/H2 discriminator."""
        config = _config(tmp_path)
        decisions = evaluate(_lag_series(lag_s=9.0, seconds=20.0), config)
        assert _reasons(decisions) == [REASON_SUSTAINED_LOOP_LAG]


class TestConcentrationThreshold:
    def test_fires_when_one_thread_holds_the_process(self, tmp_path: Path) -> None:
        config = _config(tmp_path)  # 85% / 30s
        decisions = evaluate(
            _concentration_series(seconds=30.0, share_pct=85.0), config
        )
        assert REASON_THREAD_CONCENTRATION in _reasons(decisions)
        payload = next(
            d for d in decisions if d.reason == REASON_THREAD_CONCENTRATION
        ).observed
        assert payload["native_id"] == 4242
        assert payload["comm"] == "MainThread"
        assert payload["share_pct"] == pytest.approx(85.0)
        assert payload["absolute_floor_pct"] == pytest.approx(
            CONCENTRATION_ABSOLUTE_FLOOR_PCT
        )

    def test_does_not_fire_below_the_ratio(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        decisions = evaluate(
            _concentration_series(seconds=60.0, share_pct=84.0), config
        )
        assert REASON_THREAD_CONCENTRATION not in _reasons(decisions)

    def test_does_not_fire_below_the_absolute_cpu_floor(
        self, tmp_path: Path
    ) -> None:
        """F-06: a ~100% ratio on a NEAR-IDLE process must stay silent.

        The process burns 0.001 s of CPU per second (0.1% of one core)
        while one helper thread holds all of it. The ratio is 1.0 and
        the share is 100% — and the trigger must NOT fire, because
        there is no CPU for concentration to be the explanation of.
        """
        config = _config(tmp_path)
        decisions = evaluate(
            _concentration_series(seconds=30.0, share_pct=100.0, process_delta_s=0.001),
            config,
        )
        assert REASON_THREAD_CONCENTRATION not in _reasons(decisions)

    def test_a_single_sample_window_uses_the_tick_percentage(
        self, tmp_path: Path
    ) -> None:
        """A single-sample window has NO elapsed span.

        The floor must be judged against that tick's own percentage —
        never against an epsilon division (which would fabricate an
        astronomical number and make the floor trivially true).
        """
        config = _config(tmp_path, cpu_sustain_s=0.0, thread_concentration_pct=85.0)

        hot = [
            TriggerSample(
                t=10.0,
                process_cpu_pct=120.0,
                process_cpu_delta_s=1.2,
                top_thread_delta_s=1.2,
                top_thread_share_pct=100.0,
                top_thread_native_id=7,
                top_thread_comm="burner",
            )
        ]
        assert REASON_THREAD_CONCENTRATION in _reasons(evaluate(hot, config))

        idle = [
            TriggerSample(
                t=10.0,
                process_cpu_pct=0.05,
                process_cpu_delta_s=0.0005,
                top_thread_delta_s=0.0005,
                top_thread_share_pct=100.0,
                top_thread_native_id=7,
                top_thread_comm="burner",
            )
        ]
        assert REASON_THREAD_CONCENTRATION not in _reasons(evaluate(idle, config))


# ── AC42: the sustain RULE ─────────────────────────────────────────────────


class TestSustainRule:
    def test_a_drop_below_the_threshold_resets_the_window(
        self, tmp_path: Path
    ) -> None:
        """20 s over the line, 1 s under, 20 s over ⇒ NO fire.

        The trailing run is the last 20 s only; the sustain window is
        measured from the FIRST sample of the CONSECUTIVE run.
        """
        config = _config(tmp_path)
        samples = (
            _cpu_series(pct=99.0, seconds=20.0)
            + [TriggerSample(t=20.5, process_cpu_pct=10.0, process_cpu_delta_s=0.1)]
            + [
                TriggerSample(t=21.5 + index, process_cpu_pct=99.0, process_cpu_delta_s=0.9)
                for index in range(20)
            ]
        )
        assert evaluate(samples, config) == []

    def test_the_verdict_is_cadence_independent(self, tmp_path: Path) -> None:
        """D4b: the same WALL-TIME series yields the same verdict at a
        1 s and a 5 s cadence."""
        config = _config(tmp_path)
        fast = evaluate(_cpu_series(pct=99.0, seconds=30.0, cadence=1.0), config)
        slow = evaluate(_cpu_series(pct=99.0, seconds=30.0, cadence=5.0), config)
        assert _reasons(fast) == _reasons(slow) == [REASON_SUSTAINED_CPU]

        fast_short = evaluate(_cpu_series(pct=99.0, seconds=20.0, cadence=1.0), config)
        slow_short = evaluate(_cpu_series(pct=99.0, seconds=20.0, cadence=5.0), config)
        assert fast_short == slow_short == []


# ── A1: probe states the trigger never consumes ───────────────────────────


class TestProbeStates:
    @pytest.mark.parametrize(
        "state", [PROBE_INITIALIZING, PROBE_UNKNOWN, PROBE_STOPPED]
    )
    def test_a_non_ok_probe_state_never_fires_the_lag_trigger(
        self, tmp_path: Path, state: str
    ) -> None:
        config = _config(tmp_path)
        # A numeric lag IS present — but the probe state says the number
        # is not trustworthy, so it must not be consumed.
        samples = _lag_series(lag_s=999.0, seconds=60.0, state=state)
        decisions = evaluate(samples, config)
        assert REASON_SUSTAINED_LOOP_LAG not in _reasons(decisions)

    def test_an_unset_lag_slot_never_fires(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        samples = _lag_series(
            lag_s=0.0, seconds=60.0, state=PROBE_OK, lag_value=None
        )
        assert evaluate(samples, config) == []

    def test_an_empty_series_fires_nothing(self, tmp_path: Path) -> None:
        assert evaluate([], _config(tmp_path)) == []


# ── D13: cooldown + episode de-dup ────────────────────────────────────────


class TestCooldown:
    @staticmethod
    def _tick_and_capture(engine: TriggerEngine, sample: TriggerSample) -> list:
        """The watchdog's per-tick shape: observe, capture the FIRST
        decision only, and ack it from the capture path (W1)."""
        decisions = engine.observe(sample)
        if decisions:
            engine.note_capture_started(reason=decisions[0].reason)
        return decisions

    def test_ten_trigger_ticks_inside_one_window_yield_one_decision(
        self, tmp_path: Path
    ) -> None:
        """SC-6's unit-level core: a sustained wedge must NOT spam.

        The loop mirrors ``run_once``: every tick observes, captures
        the first decision and acks it (W1 — the cooldown is stamped by
        the capture path, never by ``observe``).
        """
        config = _config(tmp_path, cpu_sustain_s=0.0, cooldown_s=300.0)
        engine = TriggerEngine(config)
        fired = []
        for index in range(10):
            sample = TriggerSample(
                t=float(index), process_cpu_pct=99.0, process_cpu_delta_s=0.9
            )
            fired.extend(self._tick_and_capture(engine, sample))
        assert len(fired) == 1
        assert fired[0].reason == REASON_SUSTAINED_CPU
        assert len(engine.window()) == 10

    def test_it_fires_again_after_the_window_elapses(self, tmp_path: Path) -> None:
        config = _config(tmp_path, cpu_sustain_s=0.0, cooldown_s=300.0)
        engine = TriggerEngine(config)
        first = engine.observe(
            TriggerSample(t=0.0, process_cpu_pct=99.0, process_cpu_delta_s=0.9)
        )
        assert len(first) == 1
        # The actual capture acks the reason (W1) — on the sample's clock.
        engine.note_capture_started(0.0, reason=first[0].reason)
        still_cooling = engine.observe(
            TriggerSample(t=299.0, process_cpu_pct=99.0, process_cpu_delta_s=0.9)
        )
        assert still_cooling == []
        reopened = engine.observe(
            TriggerSample(t=301.0, process_cpu_pct=99.0, process_cpu_delta_s=0.9)
        )
        assert len(reopened) == 1

    def test_two_different_reasons_in_one_window_produce_two_decisions(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path, cpu_sustain_s=0.0, lag_sustain_s=0.0, cooldown_s=300.0)
        engine = TriggerEngine(config)
        sample = TriggerSample(
            t=0.0,
            process_cpu_pct=99.0,
            process_cpu_delta_s=0.9,
            lag_s=9.0,
            lag_state=PROBE_OK,
        )
        fired = engine.observe(sample)
        assert sorted(_reasons(fired)) == sorted(
            [REASON_SUSTAINED_CPU, REASON_SUSTAINED_LOOP_LAG]
        )

    def test_an_unserviced_reason_stays_armed_for_the_next_tick(
        self, tmp_path: Path
    ) -> None:
        """W1 (the D13 seam): the cooldown is stamped ONLY on the
        ACTUAL capture — never on the ALLOWED decision.

        Two reasons mature on ONE tick, but the watchdog services only
        the FIRST (one capture per tick). The unserviced reason must be
        returned again on the NEXT tick — under the pre-fix shape it
        was suppressed for the WHOLE 300 s cooldown without ever being
        captured.
        """
        config = _config(
            tmp_path, cpu_sustain_s=0.0, lag_sustain_s=0.0, cooldown_s=300.0
        )
        engine = TriggerEngine(config)

        def _both_sample(t: float) -> TriggerSample:
            return TriggerSample(
                t=t,
                process_cpu_pct=99.0,
                process_cpu_delta_s=0.9,
                lag_s=9.0,
                lag_state=PROBE_OK,
            )

        fired = engine.observe(_both_sample(0.0))
        assert sorted(_reasons(fired)) == sorted(
            [REASON_SUSTAINED_CPU, REASON_SUSTAINED_LOOP_LAG]
        )
        # The watchdog captured ONLY fired[0] (one capture per tick);
        # the capture path acks THAT reason alone — on the sample's
        # clock:
        engine.note_capture_started(0.0, reason=fired[0].reason)

        next_tick = engine.observe(_both_sample(1.0))
        assert _reasons(next_tick) == [REASON_SUSTAINED_LOOP_LAG], (
            "the unserviced reason must stay armed for the next tick — "
            "only the CAPTURED reason is cooling down"
        )

    def test_the_cooldown_is_per_reason_fingerprint(self, tmp_path: Path) -> None:
        config = _config(
            tmp_path, cpu_sustain_s=0.0, lag_sustain_s=0.0, cooldown_s=300.0
        )
        engine = TriggerEngine(config)
        # Tick 1: CPU only — the capture acks CPU (W1) and CPU's cooldown
        # starts.
        first = engine.observe(
            TriggerSample(t=0.0, process_cpu_pct=99.0, process_cpu_delta_s=0.9)
        )
        engine.note_capture_started(reason=first[0].reason)
        # Tick 2: the lag crosses the line INSIDE the CPU cooldown — the
        # lag reason has its own fingerprint and MUST fire.
        fired = engine.observe(
            TriggerSample(
                t=1.0, process_cpu_pct=1.0, process_cpu_delta_s=0.01,
                lag_s=9.0, lag_state=PROBE_OK,
            )
        )
        assert _reasons(fired) == [REASON_SUSTAINED_LOOP_LAG]

    def test_note_capture_started_records_the_reference_point(
        self, tmp_path: Path
    ) -> None:
        engine = TriggerEngine(_config(tmp_path))
        assert engine.last_capture_started is None
        engine.note_capture_started(123.0)
        assert engine.last_capture_started == pytest.approx(123.0)
        # The reason-bearing ack stamps the per-reason fingerprint (W1).
        engine.note_capture_started(124.0, reason=REASON_SUSTAINED_CPU)
        assert engine.cooldown_state()[REASON_SUSTAINED_CPU] == pytest.approx(124.0)


# ── the canonical vocabulary ───────────────────────────────────────────────


def test_the_canonical_reasons_are_exactly_the_four() -> None:
    assert CANONICAL_REASONS == (
        REASON_SUSTAINED_CPU,
        REASON_SUSTAINED_LOOP_LAG,
        REASON_THREAD_CONCENTRATION,
        REASON_MANUAL,
    )


def test_the_decision_payload_is_json_safe(tmp_path: Path) -> None:
    decisions = evaluate(_cpu_series(pct=99.0, seconds=30.0), _config(tmp_path))
    payload = decisions[0].as_dict()
    assert payload["reason"] == REASON_SUSTAINED_CPU
    assert isinstance(payload["detail"], str)
    assert isinstance(payload["observed"], dict)
    assert "since" in payload

