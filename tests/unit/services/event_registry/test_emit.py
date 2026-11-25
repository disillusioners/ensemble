"""P5 pack — structured emission (T5.2 + T5.8, AC3 / AC7 / D5.7 / D5.11).

Mutation legs in this file (each asserts the INVARIANT whose violation is
the named mutation, so flipping the code turns the leg RED):

- ``test_record_never_raises_even_when_the_ring_append_raises`` —
  mutation: make ``record`` raise on full/internal error ⇒ RED.
- ``test_full_ring_drops_and_counts`` — mutation: raise on full ⇒ RED.
- ``test_record_refuses_unregistered_kind`` — mutation: accept unknown
  kinds (or drop the refusal) ⇒ RED.
- ``test_fabricated_terminalization_lag_is_refused`` (AC7) — mutation:
  fabricate a lag for an unmeasurable kind ⇒ RED.
"""

from __future__ import annotations

import time

import pytest

from daemon.services import event_registry
from daemon.services.event_registry import (
    RETIRED_EVENT_KINDS,
    RING_CAP,
    counters,
    flush,
    recent,
    record,
    set_event_sink,
    snapshot,
)


# ── AC3 — never raises, never blocks, drop-counted ──────────────────────


def test_record_returns_true_and_lands_in_the_ring():
    assert record("instance_terminalized", instance_id="i1",
                  outcome="completed", fired_count=2) is True
    entries = recent(kind_id="instance_terminalized")
    assert len(entries) == 1
    assert entries[0]["dims"]["fired_count"] == 2


def test_record_swallows_hostile_dims():
    # Objects that explode on dict handling must never break the caller.
    class Hostile:
        def __init__(self):
            self.dict = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    assert record("attestation_epoch", obj=Hostile()) is True


def test_full_ring_drops_and_counts():
    # Fill the ring; the next record must DROP (the oldest evicted) and
    # count — never raise, never block.
    for i in range(RING_CAP):
        record("attestation_epoch", n=i)
    assert counters()["record_dropped_total"] == 0
    assert record("attestation_epoch", n=RING_CAP) is True
    assert counters()["record_dropped_total"] == 1
    assert len(recent(limit=RING_CAP + 10)) == RING_CAP


def test_record_never_raises_even_when_the_ring_append_raises():
    # Mutation simulation: the ring machinery itself explodes → record
    # must return False and count an internal error instead of raising
    # into the emitter (the sampler discipline).
    from daemon.services.event_registry import emit

    class _HostileRing:
        def __len__(self):
            return 0

        def append(self, *_a, **_k):
            raise RuntimeError("ring exploded")

    original = emit._ring
    emit._ring = _HostileRing()
    try:
        assert record("instance_terminalized", x=1) is False
        assert counters()["record_internal_error_total"] == 1
    finally:
        emit._ring = original


def test_record_is_sync_and_within_the_coarse_20us_bound():
    # D5.7: record() returns immediately. Coarse bound: the MEDIAN of a
    # 200-call sample under 20µs (robust against GC spikes), plus a bulk
    # wall-clock bound for a 2000-record burst.
    samples = []
    for i in range(200):
        t0 = time.perf_counter()
        record("attestation_epoch", n=i)
        samples.append(time.perf_counter() - t0)
    samples.sort()
    median_us = samples[len(samples) // 2] * 1e6
    assert median_us < 20.0, f"record() median {median_us:.1f}µs exceeds 20µs"

    t0 = time.perf_counter()
    for i in range(2000):
        record("attestation_epoch", n=i)
    assert (time.perf_counter() - t0) < 0.5


def test_flush_folds_the_ring_into_totals():
    record("instance_terminalized", fired_count=1)
    record("instance_terminalized", fired_count=2)
    record("report_injection_obligation", lane="pending_age", observed=5)
    folded = flush()
    assert folded == 3
    assert recent() == []  # ring cleared
    snap = snapshot()
    assert snap["kinds"]["instance_terminalized"]["fired_total"] == 2
    assert snap["kinds"]["report_injection_obligation"]["fired_total"] == 1
    # snapshot() itself flushes (read-through folding), so at least the
    # explicit flush + the snapshot's fold have both run.
    assert snap["counters"]["flush_count_total"] >= 1


def test_snapshot_works_without_any_flush_caller():
    # Zero-wiring observability: snapshot() folds the ring itself.
    record("mission_commission_transition", transition="abandoned")
    snap = snapshot()
    assert snap["kinds"]["mission_commission_transition"]["fired_total"] == 1


def test_snapshot_consuming_contract_is_documented_and_pinned():
    # Rider-5 shape (b) — the CONSUMING READ contract, pinned exactly as
    # snapshot()'s docstring states it: (1) raw entries are visible via
    # recent() until a snapshot; (2) the snapshot's fold DRAINS the ring;
    # (3) totals are cumulative — a re-snapshot never double-counts.
    record("instance_terminalized", instance_id="i1")
    assert len(recent(kind_id="instance_terminalized")) == 1
    snap1 = snapshot()
    assert snap1["kinds"]["instance_terminalized"]["fired_total"] == 1
    assert recent() == [], "the snapshot's fold must drain the ring"
    record("instance_terminalized", instance_id="i2")
    snap2 = snapshot()
    assert (
        snap2["kinds"]["instance_terminalized"]["fired_total"] == 2
    ), "totals are cumulative across snapshots (no double-count)"


# ── Mutation leg: unregistered kind ⇒ RED ───────────────────────────────


def test_record_refuses_unregistered_kind():
    assert record("bogus_kind_no_such_thing", x=1) is False
    assert record("bogus_kind_no_such_thing", x=1) is False
    assert counters()["record_refused_unknown_kind_total"] == 2
    assert recent() == []  # nothing landed


def test_record_refuses_the_deferred_v2_kind():
    # F-18: the dead outbox is a hard anti-target; a deferred kind's
    # substrate does not exist, so a record would fabricate an
    # observation of a mechanism that never fired.
    assert record("turn_transition", x=1) is False
    assert counters()["record_refused_deferred_kind_total"] == 1


# ── Mutation leg: fabricated terminalization lag ⇒ RED (AC7) ────────────


def test_fabricated_terminalization_lag_is_refused():
    # The registry must NEVER relay a lag for a kind whose catalog
    # metadata declares lag unmeasurable — snapshot() must keep
    # lag_seconds None even when a caller passes a fabricated dim.
    assert record("instance_terminalized", instance_id="i1",
                  lag_seconds=5.0) is True  # the event lands...
    snap = snapshot()
    entry = snap["kinds"]["instance_terminalized"]
    assert entry["lag_seconds"] is None            # ...the lag does not
    assert entry["lag_measurable"] is False
    assert counters()["record_lag_refused_total"] == 1


def test_measurable_kinds_relay_the_emitter_provided_lag():
    # State-table kinds: the row-age observed at the seam is honest
    # (D5.12 frame: row created_at vs observation) and IS relayed.
    assert record("report_injection_obligation", lane="pending_age",
                  observed=3, lag_seconds=42.5) is True
    snap = snapshot()
    entry = snap["kinds"]["report_injection_obligation"]
    assert entry["lag_seconds"] == pytest.approx(42.5)
    assert entry["lag_measurable"] is True


# ── T5.8 — the ingest_event sink (D5.11) ────────────────────────────────


def test_sink_invoked_after_record_with_kind_and_payload():
    seen: list[tuple[str, dict]] = []
    set_event_sink(lambda kind, payload: seen.append((kind, payload)))
    record("instance_terminalized", instance_id="i9", outcome="error",
           fired_count=0)
    assert len(seen) == 1
    kind, payload = seen[0]
    assert kind == "instance_terminalized"
    assert payload["dims"]["outcome"] == "error"


def test_unregistered_kind_never_reaches_the_sink():
    seen: list[tuple[str, dict]] = []
    set_event_sink(lambda kind, payload: seen.append((kind, payload)))
    record("not_a_kind", x=1)
    record("turn_transition", x=1)
    assert seen == []


def test_raising_sink_never_breaks_record():
    def boom(kind, payload):
        raise RuntimeError("sink exploded")

    set_event_sink(boom)
    # The observation landed (True) even though the sink exploded — the
    # sink failure is a counted internal error, not a caller-facing
    # exception and not a lost record (sampler discipline).
    assert record("attestation_epoch", n=1) is True
    assert counters()["record_sink_error_total"] == 1
    assert len(recent(kind_id="attestation_epoch")) == 1


def test_sink_can_be_cleared():
    seen: list[tuple[str, dict]] = []
    set_event_sink(lambda kind, payload: seen.append((kind, payload)))
    set_event_sink(None)
    record("attestation_epoch", n=1)
    assert seen == []


# ── F-19 — the bridge adds no second fire / CAS / probe ─────────────────


def test_record_is_a_pure_observation_side_effect_free_call():
    # The registry exposes NO fire/CAS surface at all: nothing in the
    # package's public names can mutate a substrate.
    public = [n for n in dir(event_registry) if not n.startswith("_")]
    forbidden = ("fire", "transition", "cas", "enqueue", "emit_terminal")
    for name in public:
        lowered = name.lower()
        for token in forbidden:
            assert token not in lowered, (name, token)


# ── L-11 retirement: retired kinds are refused as unknown ───────────────


@pytest.mark.parametrize("retired_kind", sorted(RETIRED_EVENT_KINDS))
def test_record_refuses_every_retired_kind(retired_kind):
    # A retired kind must behave like any unregistered kind: record()
    # returns False and the refused-unknown counter increments. This is
    # the silent-revival tripwire — if anyone re-registers a kind
    # without going through the revive procedure (ledger removal +
    # bridge), record() would succeed and this leg REDs.
    assert record(retired_kind, x=1) is False
    assert counters()["record_refused_unknown_kind_total"] == 1
    assert recent() == []
