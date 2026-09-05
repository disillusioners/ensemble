"""Attestation ledger — C3 fail-open wrapper + AttestationLedger protocol.

Phase 3 of the leader completion attestation feature. This module is
the CANONICAL home of the C3 fail-open wrapper for the four ledger
write methods on :class:`SQLModelInstanceRepository`:

* :func:`safe_increment` — wraps ``increment_attestation_denied_count``
  (deny path; Phase 3 task 3.3 method (a)).
* :func:`safe_reset` — wraps ``reset_attestation_denied_count`` (attested
  allow + trigger-3 fresh-episode revive; Phase 3 task 3.3 method (b)).
* :func:`safe_set_escalated` — wraps ``set_completion_gate_escalated``
  (terminal_after_bound flag setter; Phase 3 task 3.3 method (c)).
* :func:`safe_set_escalated_and_reset` — wraps
  ``reset_attestation_ledger_with_escalation`` (terminal_after_bound
  atomic write; sets flag + resets counter in one UPDATE).

Fail-open contract (C3 — widens W4 precedent)
----------------------------------------------

The W4 precedent at ``daemon/graph.py:2663-2688`` uses a narrow
exception set that does NOT cover SQLAlchemy ``OperationalError``
(connection drop, deadlock). This module widens to
``except Exception``: any DB-level error in any of the four methods ⇒

* the gate's deny/terminal outcome degrades to ``allow`` (the gate's
  ``Decision.ALLOWED`` path is the natural fallback — see Phase 3
  task 3.6);
* a structured error log ``event=leader_completion_gate_db_error`` is
  emitted with ``instance_id``, ``error_class``, and ``error_message``;
* the leader mission does NOT error (D2 outage class — bounded by the
  log volume);
* ``KeyboardInterrupt`` / ``SystemExit`` are BaseException and
  propagate (fail-closed on interpreter shutdown) because every
  handler here is ``except Exception``.

The fail-open at this seam is in ADDITION to the fail-open around the
scanner/gate itself (Phase 2 task 2.3 ``evaluate()``). The ordering
is: DB write is wrapped first; on success the deny proceeds normally;
on failure the deny becomes ``allow`` with the error log. This
guarantees one scanner bug OR one transient DB error does NOT error
every leader mission.

Protocol
--------

:class:`AttestationLedger` is the duck-typed interface the gate node
expects. The repository's four methods satisfy this protocol directly
(``increment`` / ``reset`` / ``set_escalated`` /
``set_escalated_and_reset``); the ``safe_*`` helpers here are the
fail-open wrappers that the gate node calls.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AttestationLedger(Protocol):
    """Protocol the gate node consumes (Phase 3 task 3.3 method shape).

    The repository's four methods match these names exactly:
    ``SQLModelInstanceRepository.increment_attestation_denied_count``
    → ``ledger.increment(instance_id, denial_epoch)``;
    ``reset_attestation_denied_count`` → ``ledger.reset(instance_id)``;
    ``set_completion_gate_escalated`` → ``ledger.set_escalated(instance_id)``;
    ``reset_attestation_ledger_with_escalation`` →
    ``ledger.set_escalated_and_reset(instance_id)``.

    No additional method on the ledger is invoked from the gate node —
    the four writes are the entire side-effect surface.
    """

    def increment(self, instance_id: str, denial_epoch: str) -> int: ...

    def reset(self, instance_id: str) -> bool: ...

    def set_escalated(self, instance_id: str) -> bool: ...

    def set_escalated_and_reset(self, instance_id: str) -> bool: ...


def _log_db_error(
    *,
    method_name: str,
    instance_id: str | None,
    error_class: str,
    error_message: str,
    context: dict[str, Any],
) -> None:
    """Emit the canonical C3 fail-open structured error log.

    The event name ``leader_completion_gate_db_error`` is the contract
    name observed by :file:`tests/unit/test_attestation_gate.py` and
    the Phase 5 fail-open integration test (per AC-6.6).
    """
    # NB: structured log via ``extra=`` for production log aggregators
    # plus a human-readable message for tail-readers.
    logger.error(
        "event=leader_completion_gate_db_error method=%s instance_id=%s "
        "error_class=%s error_message=%s context=%s decision=fail_open_allowed",
        method_name,
        instance_id,
        error_class,
        error_message,
        context,
    )


def safe_increment(
    ledger: Any,
    instance_id: str | None,
    denial_epoch: str,
    *,
    log_context: dict[str, Any] | None = None,
) -> int | None:
    """C3 fail-open wrapper around ``ledger.increment``.

    Returns:
        The post-increment counter value, or ``None`` on DB error
        (the gate treats ``None`` as fail-open — ``evaluate()`` already
        carries the original ``denied_count`` for the decision).

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    ctx = dict(log_context or {})
    if instance_id is not None:
        ctx.setdefault("instance_id", instance_id)
    try:
        return ledger.increment(instance_id, denial_epoch)
    except Exception as exc:  # noqa: BLE001 — C3 fail-open (widens W4)
        _log_db_error(
            method_name="increment",
            instance_id=instance_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
            context=ctx,
        )
        return None


def safe_reset(
    ledger: Any,
    instance_id: str | None,
    *,
    log_context: dict[str, Any] | None = None,
) -> bool | None:
    """C3 fail-open wrapper around ``ledger.reset``.

    Returns:
        ``True`` on success, ``None`` on DB error. The gate treats
        ``None`` as fail-open — the canonical decision log line was
        already emitted by ``evaluate()`` so the operator still sees
        the gate decision; the missed counter reset is documented as a
        bounded known risk (next allow will reset it again).

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    ctx = dict(log_context or {})
    if instance_id is not None:
        ctx.setdefault("instance_id", instance_id)
    try:
        return ledger.reset(instance_id)
    except Exception as exc:  # noqa: BLE001 — C3 fail-open (widens W4)
        _log_db_error(
            method_name="reset",
            instance_id=instance_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
            context=ctx,
        )
        return None


def safe_set_escalated(
    ledger: Any,
    instance_id: str | None,
    *,
    log_context: dict[str, Any] | None = None,
) -> bool | None:
    """C3 fail-open wrapper around ``ledger.set_escalated``.

    Returns:
        ``True`` on success, ``None`` on DB error. The gate treats
        ``None`` as fail-open — the canonical decision log line was
        already emitted by ``evaluate()``.

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    ctx = dict(log_context or {})
    if instance_id is not None:
        ctx.setdefault("instance_id", instance_id)
    try:
        return ledger.set_escalated(instance_id)
    except Exception as exc:  # noqa: BLE001 — C3 fail-open (widens W4)
        _log_db_error(
            method_name="set_escalated",
            instance_id=instance_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
            context=ctx,
        )
        return None


def safe_set_escalated_and_reset(
    ledger: Any,
    instance_id: str | None,
    *,
    log_context: dict[str, Any] | None = None,
) -> bool | None:
    """C3 fail-open wrapper around ``ledger.set_escalated_and_reset``.

    Used on the ``terminal_after_bound`` path: the SAME single atomic
    UPDATE that sets ``completion_gate_escalated=True`` also clears
    the counter to ``0``. Per leader ruling 2, both columns share the
    per-mission lifecycle; per ruling 1 (trigger 2), the counter
    resets on ``terminal_after_bound`` finalization.

    Returns:
        ``True`` on success, ``None`` on DB error. The gate treats
        ``None`` as fail-open — the canonical decision log line was
        already emitted by ``evaluate()``.

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    ctx = dict(log_context or {})
    if instance_id is not None:
        ctx.setdefault("instance_id", instance_id)
    try:
        return ledger.set_escalated_and_reset(instance_id)
    except Exception as exc:  # noqa: BLE001 — C3 fail-open (widens W4)
        _log_db_error(
            method_name="set_escalated_and_reset",
            instance_id=instance_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
            context=ctx,
        )
        return None
