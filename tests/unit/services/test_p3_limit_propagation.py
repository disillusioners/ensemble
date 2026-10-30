"""P3 pins (wi-3f5bea95): limit propagation + degraded absence."""

import inspect

from daemon.services.job_queue_service import JobQueueService


def test_limit_signature_on_jobqueueservice():
    """(c) JobQueueService.list_work accepts limit/ensure_job_ids (the class
    this fix actually extended; WorkResolverService.list_work predates it)."""
    sig = inspect.signature(JobQueueService.list_work)
    assert "limit" in sig.parameters
    assert "ensure_job_ids" in sig.parameters

# --- DEGRADED-ABSENCE DISCLOSURE (opportunistic item, cycle 3) ---
# Full behavioural pin (degraded=True -> EMPTY absence set + per-row fallback
# taken) requires resolver+DB fixtures (out of this scope). The contract is
# verified by the existing W-1 pin in
# tests/unit/services/test_fix_c_read_model_split.py (degraded path). A
# vacuous `assert True` was removed; this disclosure replaces it.
# -----------------------------------------------------------------
