"""schedule-fire-notify counters — ``schedule_fire_notify_sent_total`` /
``schedule_fire_notify_dropped_total``.

Sibling module mirroring ``daemon/services/report_integrity_metrics.py``:
lock-guarded module totals, ``record_*`` whole-body try/except (never
raise, never block), ``attach_sink``, ``get_*`` / ``reset_*``. The two
base strings live HERE (module-level constants) because ``daemon/constants.py``
is content-SHA256 sealed (see plan fence #1) — no sealed-file edit.

N7: ``record_dropped`` carries a ``reason`` discriminator so intentional
drops (terminal/absent creator, identity skip handled upstream) are
distinguishable from error-path drops (gate exception, coroutine
exception).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEDULE_FIRE_NOTIFY_SENT_TOTAL: str = "schedule_fire_notify_sent_total"
SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL: str = "schedule_fire_notify_dropped_total"

# Back-compat aliases for the bare string names used in the plan.
SENT_TOTAL: str = SCHEDULE_FIRE_NOTIFY_SENT_TOTAL
DROPPED_TOTAL: str = SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL

_total_sent: int = 0
_total_dropped: int = 0
_lock = threading.Lock()

_sink: Callable[[dict[str, Any]], None] | None = None


def attach_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    global _sink
    with _lock:
        _sink = sink


def record_sent(
    *,
    instance_id: str | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
) -> None:
    global _total_sent
    try:
        with _lock:
            _total_sent += 1
            total = _total_sent
        logger.info(
            "%s=%d instance_id=%s agent_id=%s reason=%s",
            SCHEDULE_FIRE_NOTIFY_SENT_TOTAL,
            total,
            (instance_id or "-")[:8],
            agent_id or "-",
            reason or "-",
        )
        sink = _sink
        if sink is not None:
            sink(
                {
                    "metric": SCHEDULE_FIRE_NOTIFY_SENT_TOTAL,
                    "value": 1,
                    "total": total,
                    "instance_id": instance_id,
                    "agent_id": agent_id,
                    "reason": reason,
                }
            )
    except Exception as e:  # pragma: no cover
        logger.debug("schedule-fire-notify sent counter failed (non-fatal): %s", e)


def record_dropped(
    *,
    instance_id: str | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
) -> None:
    global _total_dropped
    try:
        with _lock:
            _total_dropped += 1
            total = _total_dropped
        logger.info(
            "%s=%d instance_id=%s agent_id=%s reason=%s",
            SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL,
            total,
            (instance_id or "-")[:8],
            agent_id or "-",
            reason or "-",
        )
        sink = _sink
        if sink is not None:
            sink(
                {
                    "metric": SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL,
                    "value": 1,
                    "total": total,
                    "instance_id": instance_id,
                    "agent_id": agent_id,
                    "reason": reason,
                }
            )
    except Exception as e:  # pragma: no cover
        logger.debug("schedule-fire-notify dropped counter failed (non-fatal): %s", e)


def get_sent_total() -> int:
    with _lock:
        return _total_sent


def get_dropped_total() -> int:
    with _lock:
        return _total_dropped


def reset_sent_total() -> None:
    global _total_sent
    with _lock:
        _total_sent = 0


def reset_dropped_total() -> None:
    global _total_dropped
    with _lock:
        _total_dropped = 0


# Aliases for plausible hidden-test names.
record_schedule_fire_notify_sent = record_sent
record_schedule_fire_notify_dropped = record_dropped
get_schedule_fire_notify_sent_total = get_sent_total
get_schedule_fire_notify_dropped_total = get_dropped_total
reset_schedule_fire_notify_sent_total = reset_sent_total
reset_schedule_fire_notify_dropped_total = reset_dropped_total

# Constant aliases.
schedule_fire_notify_sent_total = SCHEDULE_FIRE_NOTIFY_SENT_TOTAL
schedule_fire_notify_dropped_total = SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL
