"""NR-3 junk-rate counter — ``report_integrity_junk_report_total``.

Wave-1 no-regret observability (phase2-plan §3.3 NR-3, wc-wake-report-
integrity): counts terminal report fetches whose source history shows the
junk shape — last assistant message with zero tool calls in a short
history (see ``ChildReportsService._is_zero_tool_short_history``).

This repo has NO metrics infra (no Prometheus / statsd / OTel client), so
the counter is a minimal NO-OP-SAFE seam:

* a module-level total readable via :func:`get_junk_report_total`,
* one structured log line per increment (the NR-3 row designates
  ``data/logs/ensemble.log`` as the observation surface — A.4),
* an optional sink (``attach_sink``) where a real metrics backend can
  attach later without touching the increment site.

Observability ONLY — nothing here ever changes report content.

Increment placement contract (§6 adjustment, 2026-08-30): the increment
fires inside ``_get_last_assistant_message_raw`` BEFORE the
``skip_repair`` and ``report_repair.enabled`` short-circuits so ALL
terminal completions count, not only repair-eligible ones.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from daemon.constants import REPORT_INTEGRITY_JUNK_REPORT_TOTAL

logger = logging.getLogger(__name__)

# Module-level total. Guarded by a lock: increments arrive from both the
# event loop and ``asyncio.to_thread`` worker threads (the completion path
# runs DB-sync work off-loop), and ``+=`` is not atomic across threads.
_total: int = 0
_lock = threading.Lock()

# Optional sink: ``Callable[[dict[str, Any]], None]`` receiving
# ``{"metric": <name>, "value": 1, "total": <int>, "instance_id": ...,
# "agent_id": ...}``. Failures are swallowed (debug log) — a broken sink
# must never break the report path.
_sink: Callable[[dict[str, Any]], None] | None = None


def attach_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    """Attach (or clear with ``None``) an external metrics sink.

    The seam for a future Prometheus/OTel backend: the sink receives one
    dict per junk-shape observation. Never raises.
    """
    global _sink
    with _lock:
        _sink = sink


def record_junk_report(*, instance_id: str | None = None, agent_id: str | None = None) -> None:
    """Record one junk-shape terminal report observation.

    No-op-safe by construction: with no sink attached this only bumps the
    module total and emits one structured INFO line. Never raises into the
    caller (the report path must not depend on observability).
    """
    global _total
    try:
        with _lock:
            _total += 1
            total = _total
        logger.info(
            "%s=%d instance_id=%s agent_id=%s",
            REPORT_INTEGRITY_JUNK_REPORT_TOTAL,
            total,
            (instance_id or "-")[:8],
            agent_id or "-",
        )
        sink = _sink
        if sink is not None:
            sink(
                {
                    "metric": REPORT_INTEGRITY_JUNK_REPORT_TOTAL,
                    "value": 1,
                    "total": total,
                    "instance_id": instance_id,
                    "agent_id": agent_id,
                }
            )
    except Exception as e:  # pragma: no cover — defensive; never break the report path
        logger.debug(
            "junk-report counter failed (non-fatal): %s: %s", type(e).__name__, e
        )


def get_junk_report_total() -> int:
    """Current total of junk-shape observations (test/observability read)."""
    with _lock:
        return _total


def reset_junk_report_total() -> None:
    """Zero the counter. Test-scoped helper — not for production use."""
    global _total
    with _lock:
        _total = 0
