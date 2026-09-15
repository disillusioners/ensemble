"""Repository layer for the ``service`` tool category (Phase 1.A store track).

Stores the ``service_tracking`` SQLModel table (D2 schema, amended per
A11 / A12 / A13) plus the synchronous ``ServiceRepo`` facade consumed
by the Phase 1.B ``ServiceToolManager`` and the Phase 1.C
``ServiceReconciliationService`` (skeleton + Phase 2 sweep).

Phase 1.A land-first deliverables — see
``.agents/shared/planning/service-tool/phase1-plan.md`` tasks 1.A.0
through 1.A.7. Phase 1.B / 1.C / Phase 2 may NOT modify the
:mod:`daemon.repositories.service_tool` public surface — the frozen
``ServiceRepo`` interface (1.A.0) is the contract.
"""