"""The memoir repository — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2).

PHASE 1 OWNS THE PERSONA LANE in this module:

* the mirrored transition vocabulary (``VALID_TRANSITIONS`` — the same
  shape as ``daemon/repositories/prompt_version/repository.py``), so
  DD-9's gate is enforced by the data, not by a convention;
* the human-lane-guarded ``approve()`` seam: it raises
  ``HumanLaneRequiredError`` unless ``actor.auth_method == "session"``
  (the house discriminator — ``daemon/services/prompt_version_service.py``
  ``_require_human_lane``). ``submit()`` has NO caller in v1 (the v1
  persona is read-only).

PHASE 2 OWNS THE EPISODE SURFACE in this module (the insert/recall/
pin/unpin writers, the mutability partition, the embedding write-back).

Design contract refs: ``design.md`` §4.1/§4.2/§4.3; ``phase1-plan.md``
D1.11/D1.12, T1.5; ``decisions.md`` DD-9 (the human lane) and DD-2
(syntheses are schema-only); amendments AM-17 (the approve seam carries
the guard) and AM-14/AM-15 (the mutability partition — one named writer
per operational family, no general-purpose update method).
"""

from __future__ import annotations
