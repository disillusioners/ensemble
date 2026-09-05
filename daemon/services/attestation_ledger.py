"""Attestation ledger — C3 fail-open wrapper + AttestationLedger protocol.

Phase 3 of the leader completion attestation feature. This module is
the CANONICAL home of the C3 fail-open wrapper for the three ledger
write methods the gate node consumes on
:class:`SQLModelInstanceRepository`:

* :func:`safe_increment` — wraps ``increment_attestation_denied_count``
  (deny path; Phase 3 task 3.3 method (a)).
* :func:`safe_reset` — wraps ``reset_attestation_denied_count`` (attested
  allow + trigger-3 fresh-episode revive; Phase 3 task 3.3 method (b)).
* :func:`safe_set_escalated_and_reset` — wraps
  ``reset_attestation_ledger_with_escalation`` (terminal_after_bound
  atomic write; sets flag + resets counter in one UPDATE).

The former :func:`safe_set_escalated` wrapper (around the bare
``set_completion_gate_escalated`` flag setter) was FOLDED into
:func:`safe_set_escalated_and_reset`: production always pairs the flag
with the counter reset (leader ruling 2 — both columns share the
per-mission lifecycle), so the flag-only write had no production
caller. The repository method itself is retained.

Ledger side-effect surface (which write touches which columns)
--------------------------------------------------------------

* ``increment_attestation_denied_count`` — ``attestation_denied_count``
  +1 (O4 epoch-deduped) AND append to
  ``instance_metadata["attestation:denial_epochs"]`` (one transaction).
* ``reset_attestation_denied_count`` — ``attestation_denied_count = 0``
  AND ``completion_gate_escalated = False`` (one UPDATE).
* ``reset_attestation_ledger_with_escalation`` —
  ``attestation_denied_count = 0`` AND
  ``completion_gate_escalated = True`` (one atomic UPDATE).
* ``set_completion_gate_escalated`` —
  ``completion_gate_escalated = True`` only (counter untouched). No
  production caller remains (the gate always pairs flag + reset);
  retained for tests and postmortem tooling.
* NOT via these methods: the trigger-3 fresh-episode revive clears BOTH
  columns inline in
  ``daemon/services/instance_messaging.py:_prepare_enqueued_message``
  (same-transaction with the status=RUNNING revive write).

Fail-open contract (C3 — widens W4 precedent)
----------------------------------------------

The W4 precedent at ``daemon/graph.py:2663-2688`` uses a narrow
exception set that does NOT cover SQLAlchemy ``OperationalError``
(connection drop, deadlock). This module widens to
``except Exception``: any DB-level error in any of the three methods ⇒

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
expects. The repository's three methods satisfy this protocol directly
(``increment`` / ``reset`` / ``set_escalated_and_reset``); the
``safe_*`` helpers here are the fail-open wrappers that the gate node
calls.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class AttestationLedger(Protocol):
    """Protocol the gate node consumes (Phase 3 task 3.3 method shape).

    The repository's three methods match these names exactly:
    ``SQLModelInstanceRepository.increment_attestation_denied_count``
    → ``ledger.increment(instance_id, denial_epoch)``;
    ``reset_attestation_denied_count`` → ``ledger.reset(instance_id)``;
    ``reset_attestation_ledger_with_escalation`` →
    ``ledger.set_escalated_and_reset(instance_id)``.

    No additional method on the ledger is invoked from the gate node —
    the three writes are the entire side-effect surface (the bare
    ``set_escalated`` flag-only write was folded away; see the module
    docstring's side-effect surface block).
    """

    def increment(self, instance_id: str, denial_epoch: str) -> int: ...

    def reset(self, instance_id: str) -> bool: ...

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


def _safe_ledger_call(
    op_name: str,
    fn: Callable[[], Any],
    instance_id: str | None,
    log_context: dict[str, Any] | None,
) -> Any:
    """Shared C3 fail-open skeleton behind every ``safe_*`` wrapper.

    Contract (identical for every caller):

    * ``fn`` is invoked exactly once inside the ``try`` — its return
      value passes through untouched on success;
    * the structured ``log_context`` is copied and seeded with
      ``instance_id`` BEFORE the call (a failing ledger call must not
      lose its context);
    * on ANY exception: emit the canonical
      ``leader_completion_gate_db_error`` event and return ``None``
      (never re-raised — C3 fail-open).

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    ctx = dict(log_context or {})
    if instance_id is not None:
        ctx.setdefault("instance_id", instance_id)
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — C3 fail-open (widens W4)
        _log_db_error(
            method_name=op_name,
            instance_id=instance_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
            context=ctx,
        )
        return None


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
    return _safe_ledger_call(
        "increment",
        lambda: ledger.increment(instance_id, denial_epoch),
        instance_id,
        log_context,
    )


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
    return _safe_ledger_call(
        "reset",
        lambda: ledger.reset(instance_id),
        instance_id,
        log_context,
    )


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
    resets on ``terminal_after_bound`` finalization. This is ALSO the
    home of the folded flag-only wrapper (the former
    ``safe_set_escalated``): production never sets the flag without
    the paired counter reset.

    Returns:
        ``True`` on success, ``None`` on DB error. The gate treats
        ``None`` as fail-open — the canonical decision log line was
        already emitted by ``evaluate()``.

    Raises:
        Nothing — C3 fail-open: any exception is swallowed, emitted as
            the canonical ``leader_completion_gate_db_error`` event,
            and surfaced as the ``None`` return (never re-raised).
    """
    return _safe_ledger_call(
        "set_escalated_and_reset",
        lambda: ledger.set_escalated_and_reset(instance_id),
        instance_id,
        log_context,
    )


def safe_get_denied_count(ledger_repo: Any, instance_id: str) -> int:
    """Phase 3 wiring helper — read the deny counter via the repo (C3).

    Returns ``0`` on any DB error (fail-open at the read seam mirrors
    the C3 write-seam contract: the gate must NEVER error a leader
    mission on transient DB issues — D2 outage class). The error is
    logged as ``leader_completion_gate_db_error`` so operators can
    detect sustained DB issues without losing leader completions.

    NB: the read seam's error line intentionally does NOT route through
    :func:`_log_db_error` — its field set (``method=get_denied_count``
    literal, no ``context=`` field) is the byte-identical log contract
    this helper has emitted since Phase 3 wiring, and the C3 contract
    freezes emitted log lines.
    """
    if not instance_id:
        return 0
    try:
        return int(ledger_repo.get_attestation_denied_count(instance_id) or 0)
    except Exception as exc:  # noqa: BLE001 — C3 fail-open
        logger.error(
            "event=leader_completion_gate_db_error method=get_denied_count "
            "instance_id=%s error_class=%s error_message=%s "
            "decision=fail_open_allowed",
            instance_id,
            type(exc).__name__,
            exc,
        )
        return 0
