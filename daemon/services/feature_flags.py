"""Process-local feature flags for the turn-reconciler migration.

These flags gate the Phase 4a/4b/4c rollout of the named-transition
chokepoint migration (see
``.agents/shared/planning/turn-reconciler-migration/increment3-plan.md``
§6b / §9 Phase 4).

Design notes:

* **Module-level constants** — simple booleans the wrapper code can read
  without indirection. Mutable at runtime via ``monkeypatch.setattr`` in
  tests; the process-local persistence keeps the surface small.
* **OFF by default** — every flag here defaults to ``False`` so the
  shipped code path is the conservative one. Production canaries opt in
  explicitly per Phase 4b-step-N.
* **Single responsibility** — these flags are about the named-transition
  chokepoint migration. Existing project-wide feature flags (e.g.
  ``USE_LEGACY_WAITING_FOR_CASCADE``) live in ``daemon/config.yaml``;
  do NOT migrate those here.
"""
from __future__ import annotations

TURN_RECONCILER_DIRECT_WRITE_PARITY: bool = False
"""Phase 4b rollout gate (C9).

When ``True``, ``complete_task`` / ``cancel_task`` / ``fail_task`` execute
BOTH the new named-transition wrapper path AND a snapshot of the legacy
direct-UPDATE result, logging any divergence. The new path remains
authoritative regardless of the flag's value — the flag only controls
the parallel shadow-traffic and divergence logging, not the routing.

Default: ``False`` (Phase 4a state — wrappers ship without parallel
legacy execution). Promote to ``True`` in production canary only after
the chokepoint wrappers have soaked in production for ≥7 days.
See increment3-plan.md §6b.

When this flag is **removed** (Phase 4c), the wrappers are the sole
codepath and the C7 ``_status_write_guard`` is permanently enabled.
"""
