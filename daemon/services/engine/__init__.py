"""The engine substrate package (Engine Arc Phase 1).

Module ownership (plan:
``.agents/shared/planning/engine-arc-phase1/phase1-plan.md`` section 0.3):

* :mod:`daemon.services.engine.spec` — P1. The frozen registration
  contract: ``ServiceMeta``, ``IntervalTrigger`` / ``EventTrigger``,
  ``JobSpec``, ``EngineSpecError``, ``validate_spec``.
* :mod:`daemon.services.engine.registry` — P1 (registration half).
  ``EngineRegistry.register`` / ``aregister``. P3 later edits this module
  to add the in-flight-tick cancellation half.
* :mod:`daemon.services.engine.estate` — P1. The ``SERVICES_*``
  continuity table (DATA only: 12 rows mapping the in-scope estate knobs
  onto continuity job ids, defaults preserved verbatim).
* ``execution.py`` / ``state.py`` / ``locks.py`` / ``dispatch.py`` /
  ``metrics.py`` / ``engine.py`` — P2.
* ``guardrails.py`` / ``killswitch.py`` / ``dualpath.py`` — P3.
* ``boot.py`` / ``watchdog.py`` — P4.
* ``projection.py`` — P6.

**NOTHING EXECUTES IN PHASE 1.** No tick loop, no dispatch, no
``execute_fn`` call path exists here (acceptance gate AC7). The
registration substrate is present and inert by design.
"""

from __future__ import annotations

__all__: list[str] = []
