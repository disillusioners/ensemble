"""The ``notify_audit`` repository package (ari-voice, 2026-10-25).

The audit ledger + rate-limit budget source behind the ``notify_user``
privileged tool (category ``notify-user``). See ``models.py`` for the
lifecycle and ``repository.py`` for the C3 commit-before-count contract.
"""

from .models import (
    ACCEPTED_CLASS_OUTCOMES,
    NotifyAudit,
    OUTCOME_ACCEPTED,
    OUTCOME_DELIVERY_FAILED,
    OUTCOME_DELIVERY_UNKNOWN,
    OUTCOME_DISPATCHED,
    OUTCOME_REFUSED,
    OUTCOME_RATE_LIMITED,
)
from .repository import NotifyAuditRepository

__all__ = [
    "ACCEPTED_CLASS_OUTCOMES",
    "NotifyAudit",
    "NotifyAuditRepository",
    "OUTCOME_ACCEPTED",
    "OUTCOME_DELIVERY_FAILED",
    "OUTCOME_DELIVERY_UNKNOWN",
    "OUTCOME_DISPATCHED",
    "OUTCOME_REFUSED",
    "OUTCOME_RATE_LIMITED",
]
