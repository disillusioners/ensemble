"""The TYPE-ONLY exception log helper (council round 4 — B-1).

**THE RULE.** The daemon log is AGENT-READABLE: the ``system-log`` tool
category is granted to ``worker`` and ``maintenancer``. An interpolated
exception is therefore a CONTENT LEAK by another name — exception
messages carry secrets, prompt text and query text, and the ratified
rule (``decisions.md`` §5 item 16(7)) is "exception TYPE only".

**THE SURFACE.** Every decision-module log site that reports a fault
routes through :func:`log_exception`, which logs the exception TYPE and
NOTHING else — never the message, never the exception ``args``, never
the payload. The sites' own context (a seam id, a store key, a metric
name) stays: identifiers are content-free by the design's own rule
(ids/hashes/scores).

The pin that holds this is single-surface: ``log_exception`` is the ONE
place that formats an exception, plus a static AST pin that no
``logger.*`` call in the package may receive an exception object at all.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["log_exception"]


def log_exception(
    logger: logging.Logger,
    level: int,
    message: str,
    exc: BaseException,
    *args: Any,
) -> None:
    """Log a fault with the exception TYPE only.

    Args:
        logger: The logger to write to.
        level: The log level (an ``int``, e.g. ``logging.WARNING``).
        message: The message TEMPLATE. It may carry ``%s``-style
            placeholders for the caller's own context (ids, keys, metric
            names) — never for the exception.
        exc: The exception. ONLY its type is logged.
        *args: The context values for ``message``'s placeholders.

    The rendered line is ``"<message>: <ExceptionType>"``. The message,
    the args and the payload of the exception NEVER reach the log.
    """
    try:
        rendered = message % args if args else message
    except Exception:  # noqa: BLE001 — a bad template must not raise
        rendered = message
    logger.log(level, "%s: %s", rendered, type(exc).__name__)
