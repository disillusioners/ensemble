"""The four Memoir Store tables — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2 — module
skeletons: "empty-but-importable packages/modules with docstrings stating
the design contract refs").

PHASE 1 OWNS THE CONTENT of this module:

* the four SQLModel tables — ``memoir_episodes`` (layer 1 + the keepsake
  flag of layer 2), ``memoir_persona`` + ``memoir_persona_versions``
  (layer 3), ``memoir_syntheses`` (layer 4, schema-only and EMPTY);
* the ``StarsListType`` column adapter (``impl = JSON``,
  ``cache_ok = True``, PG → ``ARRAY(Text())`` / SQLite → ``JSON()``,
  identity ``process_bind_param`` / ``process_result_value`` returning a
  Python list on BOTH drivers, nullable, never a mutable default);
* the MODEL-bound CHECK constraints (``__table_args__ = (CheckConstraint(
  ...),)``) — the PG tables are built by ``create_all`` FROM THE MODELS,
  so a constraint that lives only in the ``.sql`` would not exist on PG;
* the dual-dialect partial indexes (``sqlite_where`` /
  ``postgresql_where``).

Design contract refs: ``design.md`` §3 (storage kinds), §4.1 (episodes),
§4.2 (keepsakes), §4.3 (persona), §4.4 (syntheses); ``phase1-plan.md``
§3 (the exact column list), D1.2/D1.4/D1.8/D1.9/D1.14/D1.15.

Phase 1 ALSO adds the PG-only ``tsv`` generated column — which is in NO
model (it is added by the ``_ensure_postgres_columns`` ensure, because
SQLite has no ``to_tsvector``).
"""

from __future__ import annotations
