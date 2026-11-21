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


def _consumers_live(
    spec, bus: Any
) -> tuple[list[dict[str, Any]], bool]:
    """Verify the declared consumers against the live subscriber set.

    Returns ``(entries, verified, any_absent)``. Each entry is
    ``{"consumer": <id>, "live": True|False|None}`` — ``None`` when the
    live set is unavailable (no bus passed / no live source for this
    substrate). A declared consumer that IS absent logs ONE WARNING
    (per kind+consumer, process-lifetime dedup — D5.4/AC6).
    """
    live_source = (spec.metadata or {}).get("live_source")
    declared = list(spec.consumers)
    live_ids = _live_subscriber_ids(bus) if live_source == "event_bus" else None
    entries: list[dict[str, Any]] = []
    any_absent = False
    for consumer in declared:
        if live_ids is None:
            live: bool | None = None
        else:
            live = consumer in live_ids
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
    verified = live_ids is not None
    return entries, verified, any_absent



def snapshot(bus: Any = None) -> dict[str, Any]:
    """The per-kind observability projection (D5.5) — read-only.

    Args:
        bus: the live ``EventBus`` (duck-typed) for consumer-liveness
            verification of ``event_bus`` kinds; ``None`` = liveness
            reported as unverifiable (``live=None``).

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
            consumers_live, _verified, _absent = _consumers_live(spec, bus)
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
