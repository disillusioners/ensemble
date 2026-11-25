"""Engine substrate repositories (Engine Arc Phase 1).

Owner map (plan:
``.agents/shared/planning/engine-arc-phase1/phase1-plan.md`` section 0.3):
P1 owns ``models.py`` and ``repository.py``; P2+ consume them and add
nothing here.

Importing this package registers both engine models on
``SQLModel.metadata`` — the manager's late-import block relies on that
(the ``repositories/__init__.py`` 8/8 sibling new-table convention).
"""

from daemon.repositories.engine.models import (  # noqa: F401
    CONCURRENCY_CLASS_DB_OFFLOAD,
    CONCURRENCY_CLASS_DEDICATED_EXECUTOR,
    CONCURRENCY_CLASS_MAIN_LOOP,
    IDX_ENGINE_JOBS_CATEGORY,
    IDX_ENGINE_JOBS_TRIGGER,
    IDX_ENGINE_RUNS_JOB_STARTED,
    IDX_ENGINE_RUNS_OUTCOME,
    OUTCOME_FAILED_BACKOFF,
    OUTCOME_SKIPPED_PREFIX,
    OUTCOME_SUCCEEDED,
    OUTCOME_TIMED_OUT,
    TIMEOUT_POLICY_CANCEL,
    TIMEOUT_POLICY_EXTEND,
    TRIGGER_KIND_EVENT,
    TRIGGER_KIND_INTERVAL,
    UQ_ENGINE_JOBS_SERVICE_TAG,
    EngineJob,
    EngineRun,
)
from daemon.repositories.engine.repository import EngineRepository

__all__ = [
    "CONCURRENCY_CLASS_DB_OFFLOAD",
    "CONCURRENCY_CLASS_DEDICATED_EXECUTOR",
    "CONCURRENCY_CLASS_MAIN_LOOP",
    "EngineJob",
    "EngineRepository",
    "EngineRun",
    "IDX_ENGINE_JOBS_CATEGORY",
    "IDX_ENGINE_JOBS_TRIGGER",
    "IDX_ENGINE_RUNS_JOB_STARTED",
    "IDX_ENGINE_RUNS_OUTCOME",
    "OUTCOME_FAILED_BACKOFF",
    "OUTCOME_SKIPPED_PREFIX",
    "OUTCOME_SUCCEEDED",
    "OUTCOME_TIMED_OUT",
    "TIMEOUT_POLICY_CANCEL",
    "TIMEOUT_POLICY_EXTEND",
    "TRIGGER_KIND_EVENT",
    "TRIGGER_KIND_INTERVAL",
    "UQ_ENGINE_JOBS_SERVICE_TAG",
]
