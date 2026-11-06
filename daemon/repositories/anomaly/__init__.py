"""The ``anomaly`` repository package (nest-anomaly-report, 2026-11-05).

The nest-level anomaly store behind the ``nest-report`` tool category:
one table per entity (the report row + the cluster identity anchor).
See ``models.py`` for the column contract (R7 / AM-4 / AM-5) and
``repository.py`` for the write + phase-2-ready read surface.
"""

from .models import (
    OUTCOME_ACCEPTED,
    OUTCOME_PERSIST_FAILED,
    OUTCOME_RATE_LIMITED,
    OUTCOME_UNCLUSTERED,
    OUTCOME_VALUES,
    SEVERITY_CRITICAL,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    SEVERITY_VALUES,
    AnomalyCluster,
    AnomalyReport,
)
from .repository import AnomalyRepository

__all__ = [
    "OUTCOME_ACCEPTED",
    "OUTCOME_PERSIST_FAILED",
    "OUTCOME_RATE_LIMITED",
    "OUTCOME_UNCLUSTERED",
    "OUTCOME_VALUES",
    "SEVERITY_CRITICAL",
    "SEVERITY_MAJOR",
    "SEVERITY_MINOR",
    "SEVERITY_VALUES",
    "AnomalyCluster",
    "AnomalyReport",
    "AnomalyRepository",
]
