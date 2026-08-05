"""Save-plan model + helpers for resumable blueprint publication.

Addresses C9 (Write Budget Management). A full rebuild creates one core +
N areas, which can exceed the default per-hour rate limit (5/hour).
Rather than silently dropping rate-limited writes (reported as success),
the blueprinter counts its intended writes via
:meth:`BlueprintWriteService.plan_publication`, executes them via
:meth:`BlueprintWriteService.execute_save_plan`, and on a rate-limit hit
persists the plan to project metadata so it can resume after the
cooldown.

This module holds the plain-data structures only — the orchestration
lives in :mod:`daemon.services.blueprint_write_service`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WriteOp:
    """One unit of work in a save plan.

    ``payload`` holds the keyword arguments for the corresponding service
    method (``create_blueprint`` / ``update_blueprint`` /
    ``disable_blueprint``). ``completed`` / ``error`` / ``completed_at``
    are mutated by ``execute_save_plan`` as the plan progresses.
    """

    op: str  # "create" | "update" | "disable"
    blueprint_id: str | None = None  # for update/disable
    payload: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    error: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "blueprint_id": self.blueprint_id,
            "payload": dict(self.payload),
            "completed": self.completed,
            "error": self.error,
            "completed_at": self.completed_at,
        }


@dataclass
class SavePlan:
    """Persisted save plan for a single blueprinter run.

    ``budget_available`` and ``will_rate_limit_at`` are annotated by
    ``plan_publication`` (they are not declared as dataclass fields to
    keep the constructor signature stable; they are set as attributes
    after construction).
    """

    project_id: str
    run_id: str  # UUID; ties the plan to the JobItem
    mode: str  # "rebuild" | "incremental" | "manual"
    operations: list[WriteOp]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None
    total_operations: int = 0
    completed_operations: int = 0
    # Budget annotations set by plan_publication (rev 2 uses attribute
    # assignment to avoid changing the dataclass field set).
    budget_available: int = 0
    will_rate_limit_at: int = 0

    def __post_init__(self) -> None:
        if not self.total_operations:
            self.total_operations = len(self.operations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "operations": [op.to_dict() for op in self.operations],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "total_operations": self.total_operations,
            "completed_operations": self.completed_operations,
            "budget_available": self.budget_available,
            "will_rate_limit_at": self.will_rate_limit_at,
        }


@dataclass
class SavePlanResult:
    """Outcome of executing a :class:`SavePlan`.

    ``status`` is one of:

    * ``"complete"`` — all operations succeeded.
    * ``"partial_rate_limited"`` — some operations completed, the rest
      were blocked by the rate limiter (or errored) and the plan was
      persisted for resume.
    * ``"aborted"`` — the plan was aborted before any write.
    * ``"error"`` — an unexpected error occurred.

    Callers MUST check :attr:`is_complete` before declaring a build
    successful — a ``partial_rate_limited`` result is NOT success.
    """

    status: str
    completed: int
    total: int
    rate_limited_at_index: int | None = None
    cooldown_resume_at: str | None = None  # when to retry
    save_plan: SavePlan | None = None
    message: str = ""

    @property
    def is_complete(self) -> bool:
        """True only when ``status == "complete"``.

        ``partial_rate_limited`` and ``error`` are NOT complete — callers
        must not treat a partial result as a successful build.
        """
        return self.status == "complete"


__all__ = ["WriteOp", "SavePlan", "SavePlanResult"]
