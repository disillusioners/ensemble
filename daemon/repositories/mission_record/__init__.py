"""Mission-record repository package (Proposal B PR1).

The data plane for the mission-record anchor: :class:`MissionRecord`
(one row per mission epoch) + :class:`MissionCommission` (one row per
commissioned worker / work item), with the guarded-state-transition
repository. See :mod:`daemon.services.mission_record_service` for the
single-writer service and the ``ENSEMBLE_MISSION_RECORDS`` mode ladder.

PR1 contract: mints write, NOTHING reads these tables for decisions.
"""

from .models import (
    CommissionLane,
    CommissionState,
    FOUNDING_MSG_NOT_NULL_SQL,
    MissionAttestMethod,
    MissionCommission,
    MissionFoundingSource,
    MissionProvenance,
    MissionRecord,
    MissionStatus,
)
from .repository import MissionRecordError, MissionRecordRepository

__all__ = [
    "MissionRecord",
    "MissionCommission",
    "MissionRecordRepository",
    "MissionRecordError",
    "MissionFoundingSource",
    "MissionStatus",
    "MissionProvenance",
    "MissionAttestMethod",
    "CommissionLane",
    "CommissionState",
    "FOUNDING_MSG_NOT_NULL_SQL",
]
