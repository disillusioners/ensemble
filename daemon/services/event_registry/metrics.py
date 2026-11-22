"""Event registry v1 — the per-kind observability projection (T5.7).

D5.5: ``snapshot()`` is ENGINE-LOCAL FIRST — it returns per-kind
``{fired_total, last_fired_at, consumers, consumers_live, lag_seconds,
posture}`` and registers NOTHING in the metrics registry (F-1 — the census
is closed; see emit.py's docstring for the full rationale).

D5.4: consumer visibility is DECLARED in the catalog and VERIFIED against
the live subscriber sets — for ``event_bus`` kinds the live set is the
``EventBus._global_subscribers`` keys (duck-typed attribute read; the bus
is passed in, never imported). A declared consumer absent from the live
set appears in ``consumers_live`` as ``live=False`` and logs ONE WARNING
(D5.4: "a declared list that is verified against the live subscriber set
... detects a dead consumer").

THE SECOND LIVE SOURCE (D5.4: "the dependency bus's lane names") is
implemented by :func:`_live_dependency_bus_lanes` — the same duck-typed,
INJECTED pattern as the event-bus extractor (the bus object is passed in;
the registry never imports ``daemon.services.dependency_bus``). A
consumer id is namespaced ``<source>:<lane>``; the namespace selects the
live source, so a consumer whose substrate has no implemented extractor
stays ``live=None`` ("cannot verify" ≠ "absent"). The implemented
dispatch set is exactly :data:`LIVE_SOURCES` — a THIRD source would be
unimplemented and therefore unverifiable (the fence keeps guarding the
frontier).

D5.12 lag honesty (AC7): ``lag_seconds`` is populated ONLY for kinds whose
catalog metadata declares lag measurable (``event_bus`` + state-table
kinds) AND only from an emitter-provided ``lag_seconds`` dim observed at
the seam. Terminalization lag stays ``None`` with its documented reason —
NEVER fabricated from ``now − created_at`` (the wrong-clock-frame trap the
study warns against, F12's ``ensemble_sweep_cycle_seconds`` note).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Mapping

from . import emit
from .catalog import (
    BRIDGE_PENDING,
    CATALOG,
    DECLARED_SILENT_KINDS,
    KIND_INDEX,
    FailurePosture,
)

logger = logging.getLogger(__name__)

_warn_lock = threading.Lock()
_warned_absent: set[tuple[str, str]] = set()


def _live_subscriber_ids(bus: Any) -> set[str] | None:
    """The live subscriber set, duck-typed (D5.4's named source).

    ``EventBus._global_subscribers`` is a ``dict[str, asyncio.Queue]`` —
    the keys are the subscriber ids. The bus is NEVER imported here; the
    caller passes it (boot wiring is P4/P6's concern). Any object without
    the attribute (a stub, an unwired boot) yields ``None`` = "cannot
    verify" — reported honestly, not as absence.
    """
    if bus is None:
        return None
    subscribers = getattr(bus, "_global_subscribers", None)
    if not isinstance(subscribers, Mapping):
        return None
    return set(subscribers.keys())


def _live_dependency_bus_lanes(bus: Any) -> set[str] | None:
    """The dependency bus's live LANE names (D5.4's SECOND live source).

    Duck-typed on the INJECTED bus object, exactly like
    :func:`_live_subscriber_ids` does for the event bus: the registry
    never imports ``daemon.services.dependency_bus`` (no hard import —
    the injection pattern avoids it).

    WHAT A LANE NAME IS AT THIS TREE: the dependency bus's arm lanes.
    Its own module docstring names the two production arm lanes — the
    spawn / ``send_message`` lane (``daemon/tools/instance.py``
    ``_register_child_completion_watcher``) and the nudge lane
    (``daemon/tools/instance_ops.py``
    ``_register_nudge_completion_watcher``) — and each armed watcher
    carries its arm lane as the machine-readable ``metadata["kind"]``
    tag (``"child_complete"`` / ``"nudge_complete"``).

    THE LIVE SET is the set of arm lanes that currently hold an ARMED
    watcher (the bus's in-RAM ``_pending`` cache — the same RAM-only
    read the event-bus extractor does). It answers D5.4's operator
    question truthfully: "who is listening to this kind RIGHT NOW". A
    lane with no armed waiter reads ABSENT (live=False + the one
    WARNING) — which is a fact about the daemon's state, not a verdict
    on the code.

    Returns ``None`` when the injected object exposes no lane set (an
    unwired boot, a stub) — "cannot verify", never "absent".
    """
    if bus is None:
        return None
    # Prefer an explicit lane-name surface if a live bus exposes one.
    lanes = getattr(bus, "_lane_names", None)
    if lanes is None:
        lanes = getattr(bus, "lane_names", None)
        if callable(lanes):
            try:
                lanes = lanes()
            except Exception:  # noqa: BLE001 — a broken accessor ≠ absence
                return None
    if lanes is not None:
        if isinstance(lanes, Mapping):
            return {str(k) for k in lanes.keys()}
        if isinstance(lanes, (set, frozenset, list, tuple)):
            return {str(item) for item in lanes}
        return None
    # Derive the live lane names from the bus's ARMED watchers: each
    # armed FollowUp carries its arm lane as metadata["kind"].
    pending = getattr(bus, "_pending", None)
    if not isinstance(pending, Mapping):
        return None
    derived: set[str] = set()
    for follow_ups in pending.values():
        for follow_up in follow_ups or ():
            metadata = getattr(follow_up, "metadata", None)
            if not isinstance(metadata, Mapping):
                continue
            lane = metadata.get("kind")
            if isinstance(lane, str) and lane:
                derived.add(lane)
    return derived


#: The IMPLEMENTED live-source dispatch set (D5.4). Every entry is a
#: real extractor over an INJECTED object — a dispatch branch without an
#: extractor would be a LIE, so the set is exactly what the code can
#: verify. A THIRD source is NOT implemented and therefore unverifiable
#: (the fence keeps guarding the frontier, not the status quo).
LIVE_SOURCES: dict[str, Any] = {
    "event_bus": _live_subscriber_ids,
    "dependency_bus": _live_dependency_bus_lanes,
}


def _consumer_lane(consumer: str, default_source: str | None) -> tuple[str | None, str]:
    """Resolve ONE declared consumer to ``(source, lane_id)``.

    A namespaced consumer id (``<source>:<lane>``) is verified by the
    extractor of ITS OWN source — so a kind may declare consumers on
    different substrates (e.g. ``instance_terminalized`` declares
    dependency-bus lanes AND a report-lane consumer), and each is
    verified (or honestly left ``None``) on its own substrate. An
    UNNAMESPACED consumer falls back to the kind's declared
    ``live_source`` and is compared by its full id.
    """
    if ":" in consumer:
        source, _, lane = consumer.partition(":")
        return (source if source in LIVE_SOURCES else None), lane
    return (default_source if default_source in LIVE_SOURCES else None), consumer


def _consumers_live(
    spec, bus: Any, dependency_bus: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """Verify the declared consumers against the live subscriber sets.

    Returns ``(entries, verified, any_absent)``. Each entry is
    ``{"consumer": <id>, "live": True|False|None}`` — ``None`` when the
    live set is unavailable (no bus passed / no live source for this
    consumer's substrate). A declared consumer that IS absent logs ONE
    WARNING (per kind+consumer, process-lifetime dedup — D5.4/AC6).

    A KIND whose declared ``live_source`` is NOT implemented is
    UNVERIFIABLE as a whole (every consumer reads ``None``): a source
    the registry cannot extract must never be answered by another
    source's live set.
    """
    declared_source = (spec.metadata or {}).get("live_source")
    if declared_source is not None and declared_source not in LIVE_SOURCES:
        return (
            [{"consumer": c, "live": None} for c in spec.consumers],
            False,
            False,
        )
    entries: list[dict[str, Any]] = []
    any_absent = False
    verified_any = False
    for consumer in list(spec.consumers):
        source, lane = _consumer_lane(consumer, declared_source)
        if source is None:
            # No implemented extractor for this consumer's substrate ⇒
            # "cannot verify" (None), never "absent" (False).
            entries.append({"consumer": consumer, "live": None})
            continue
        extractor = LIVE_SOURCES[source]
        if source == "dependency_bus":
            live_ids = extractor(dependency_bus)
        else:
            live_ids = extractor(bus)
        if live_ids is None:
            entries.append({"consumer": consumer, "live": None})
            continue
        verified_any = True
        live = lane in live_ids
        if not live:
            any_absent = True
            key = (spec.kind_id, consumer)
            with _warn_lock:
                first = key not in _warned_absent
                if first:
                    _warned_absent.add(key)
            if first:
                logger.warning(
                    "event_registry: declared consumer %r of kind %r "
                    "is ABSENT from the live subscriber set (dead "
                    "consumer? — D5.4 verification)",
                    consumer,
                    spec.kind_id,
                )
        entries.append({"consumer": consumer, "live": live})
    return entries, verified_any, any_absent



def snapshot(bus: Any = None, dependency_bus: Any = None) -> dict[str, Any]:
    """The per-kind observability projection (D5.5).

    **CONSUMING READ CONTRACT (rider-5, stage review):** this is NOT a
    pure read — it FOLDS the hot ring into the per-kind totals and the
    fold drains the ring (``emit.flush()``). Consequences, both pinned
    by the unit pack:

    * totals are cumulative across calls (an entry is folded exactly
      once — a second ``snapshot()`` never double-counts);
    * the raw ring is EMPTY after a snapshot, so ``recent()`` must be
      called BEFORE ``snapshot()`` if raw per-entry dims are wanted
      (the durable substrates — event rows, state tables, the ledger —
      remain the system's truth; the ring is a bounded debug buffer).

    Args:
        bus: the live ``EventBus`` (duck-typed) for consumer-liveness
            verification of ``event_bus`` kinds; ``None`` = liveness
            reported as unverifiable (``live=None``).
        dependency_bus: the live ``DependencyBus`` (duck-typed) for the
            SECOND live source (D5.4: "the dependency bus's lane
            names"); ``None`` = its consumers stay unverifiable.

    Returns::

        {
          "kinds": {
            <kind_id>: {
              "class": <EventClass name>,
              "service_tag": ..., "posture": ...,
              "fired_total": int, "last_fired_at": iso|None,
              "consumers": [...], "consumers_live": [{"consumer", "live"}],
              "lag_seconds": float|None, "lag_reason": str,
              "bridge_pending": <reason|None>,
              "deferred_v2": bool,
            }, ...
          },
          "counters": {...global registry counters...},
          "generated_at": iso,
        }

    Never raises; never blocks beyond the registry lock.
    """
    try:
        emit.flush()  # fold the hot ring so totals are current
        totals = {k: dict(v) for k, v in emit._totals.items()}
        kinds_out: dict[str, Any] = {}
        for spec in CATALOG:
            agg = totals.get(spec.kind_id, {})
            lag_meta = spec.metadata.get("lag") or {}
            measurable = bool(lag_meta.get("measurable"))
            lag_latest = agg.get("lag_latest")
            if measurable and lag_latest is not None:
                lag_seconds: float | None = float(lag_latest)
                lag_reason = (
                    "emitter-provided row-age observed at the seam "
                    "(D5.12 state-table/event_bus frame)"
                )
            else:
                lag_seconds = None
                lag_reason = str(lag_meta.get("reason") or "not measurable")
            consumers_live, _verified, _absent = _consumers_live(
                spec, bus, dependency_bus
            )
            kinds_out[spec.kind_id] = {
                "class": spec.klass.name,
                "service_tag": spec.service_tag,
                "posture": spec.posture.value
                if isinstance(spec.posture, FailurePosture)
                else str(spec.posture),
                "fired_total": int(agg.get("fired_total", 0)),
                "last_fired_at": agg.get("last_fired_at"),
                "consumers": list(spec.consumers),
                "consumers_live": consumers_live,
                "lag_seconds": lag_seconds,
                "lag_reason": lag_reason,
                "lag_measurable": measurable,
                "bridge_pending": BRIDGE_PENDING.get(spec.kind_id),
                "declared_silent": spec.kind_id in DECLARED_SILENT_KINDS,
                "deferred_v2": bool(spec.metadata.get("deferred_v2")),
            }
        return {
            "kinds": kinds_out,
            "counters": emit.counters(),
            "generated_at": _now_iso(time.time()),
        }
    except Exception:  # noqa: BLE001 — a snapshot never kills a caller
        logger.exception("event_registry.snapshot failed")
        return {"kinds": {}, "counters": {}, "generated_at": None}


def _now_iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def reset_warning_state_for_tests() -> None:
    """Clear the one-WARNING dedup set — TEST-ONLY."""
    with _warn_lock:
        _warned_absent.clear()
