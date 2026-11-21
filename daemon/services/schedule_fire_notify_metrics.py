"""schedule-fire-notify counters — ``schedule_fire_notify_sent_total`` /
``schedule_fire_notify_dropped_total``.

Sibling module mirroring ``daemon/services/report_integrity_metrics.py``:
lock-guarded module totals, ``record_*`` whole-body try/except (never
raise), ``attach_sink``, ``get_*`` / ``reset_*``. The two base strings live
HERE (module-level constants) because ``daemon/constants.py`` is
content-SHA256 sealed (see plan fence #1) — no sealed-file edit.

The ``record_*`` functions never raise; the optional sink runs
SYNCHRONOUSLY on the calling thread and must itself be non-blocking (a
slow sink adds latency to the caller — keep it cheap or attach none).

N7: ``record_dropped`` carries a ``reason`` discriminator so intentional
drops (terminal/absent creator, identity skip handled upstream) are
distinguishable from error-path drops (lookup infra error, gate
exception, coroutine exception, missing enqueue hook).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEDULE_FIRE_NOTIFY_SENT_TOTAL: str = "schedule_fire_notify_sent_total"
SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL: str = "schedule_fire_notify_dropped_total"

_total_sent: int = 0
_total_dropped: int = 0
_lock = threading.Lock()

_sink: Callable[[dict[str, Any]], None] | None = None


def attach_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    """Attach (or clear with ``None``) an external metrics sink. Never raises."""
    global _sink
    with _lock:
        _sink = sink


def _record(
    metric_name: str,
    *,
    instance_id: str | None,
    agent_id: str | None,
    reason: str | None,
) -> None:
    """Bump one lock-guarded total, log one line, fan out to the sink.

    Private shared body for :func:`record_sent` / :func:`record_dropped`.
    Whole-body guarded — observability must never break the notify path.
    """
    global _total_sent, _total_dropped
    try:
        with _lock:
            if metric_name == SCHEDULE_FIRE_NOTIFY_SENT_TOTAL:
                _total_sent += 1
                new_total = _total_sent
            else:
                _total_dropped += 1
                new_total = _total_dropped
        logger.info(
            "%s=%d instance_id=%s agent_id=%s reason=%s",
            metric_name,
            new_total,
            (instance_id or "-")[:8],
            agent_id or "-",
            reason or "-",
        )
        sink = _sink
        if sink is not None:
            sink(
                {
                    "metric": metric_name,
                    "value": 1,
                    "total": new_total,
                    "instance_id": instance_id,
                    "agent_id": agent_id,
                    "reason": reason,
                }
            )
        return new_total
    except Exception as e:  # pragma: no cover
        logger.debug("schedule-fire-notify counter failed (non-fatal): %s", e)


def record_sent(
    *,
    instance_id: str | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Record one sent notice (N7 ``reason`` discriminator). Never raises."""
    _record(
        SCHEDULE_FIRE_NOTIFY_SENT_TOTAL,
        instance_id=instance_id,
        agent_id=agent_id,
        reason=reason,
    )


def record_dropped(
    *,
    instance_id: str | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Record one dropped notice (N7 ``reason`` discriminator). Never raises."""
    _record(
        SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL,
        instance_id=instance_id,
        agent_id=agent_id,
        reason=reason,
    )


def get_sent_total() -> int:
    """Current sent-notice total (test/observability read)."""
    with _lock:
        return _total_sent


def get_dropped_total() -> int:
    """Current dropped-notice total (test/observability read)."""
    with _lock:
        return _total_dropped


def reset_sent_total() -> None:
    """Zero the sent counter. Test-scoped helper — not for production use."""
    global _total_sent
    with _lock:
        _total_sent = 0


def reset_dropped_total() -> None:
    """Zero the dropped counter. Test-scoped helper — not for production use."""
    global _total_dropped
    with _lock:
        _total_dropped = 0
