"""Report-injection repository module.

DB-backed, queued, persistent store for child→parent completion
reports, delivering them to a parent's *live* graph turn as injected
``HumanMessage`` objects (the "report injection" path) instead of
waiting for the parent's turn to end.

This is a SEPARATE mechanism from the RAM-only user-message injection
slot (``InstanceManager._pending_injections`` / ``set_injection``),
which is intentionally left untouched. The report path needs a queue
(multiple workers can complete near-simultaneously) and persistence
(survives crashes), neither of which the single-slot RAM store
provides.

The existing ``PROCESS_REPORT`` task path is retained as the
fallback / turn-starter for the case where no parent turn is live.
Exactly-once delivery between the two paths is enforced by the
atomic ``state = PENDING → terminal`` claim in
:meth:`ReportInjectionRepository.claim_for_injection` /
:meth:`ReportInjectionRepository.claim_for_task_delivery`.
"""

from .models import ReportInjection, ReportInjectionState
from .repository import ReportInjectionRepository, TaskDeliveryClaim

__all__ = [
    "ReportInjection",
    "ReportInjectionState",
    "ReportInjectionRepository",
    "TaskDeliveryClaim",
]
