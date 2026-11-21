"""The engine substrate package (Engine Arc Phase 1 + Phase 2).

Module ownership (plan:
``.agents/shared/planning/engine-arc-phase1/phase1-plan.md`` section 0.3):

* :mod:`daemon.services.engine.spec` — P1.
* :mod:`daemon.services.engine.registry` — P1 (registration half).
* :mod:`daemon.services.engine.estate` — P1.
* :mod:`daemon.services.engine.state` — P2 (honest state machine).
* :mod:`daemon.services.engine.locks` — P2 (single-flight).
* :mod:`daemon.services.engine.dispatch` — P2 (execution classes).
* :mod:`daemon.services.engine.execution` — P2 (tick loop).
* :mod:`daemon.services.engine.engine` — P2 (EngineService).
* :mod:`daemon.services.engine.metrics` — P2 (in-process projection).
* ``guardrails.py`` / ``killswitch.py`` / ``dualpath.py`` — P3.
* ``boot.py`` / ``watchdog.py`` — P4.
* ``projection.py`` — P6.
"""

from __future__ import annotations

from daemon.services.engine.engine import EngineService
from daemon.services.engine.locks import JobLockTable
from daemon.services.engine.metrics import EngineMetrics, project_merged
from daemon.services.engine.state import TerminalStamp, stamp_terminal

__all__ = [
    "EngineMetrics",
    "EngineService",
    "JobLockTable",
    "TerminalStamp",
    "project_merged",
    "stamp_terminal",
]
