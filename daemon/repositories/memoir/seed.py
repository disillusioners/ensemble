"""The persona seed — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2).

PHASE 1 OWNS THE CONTENT of this module:

* ``MEMOIR_PERSONA_SEED`` — the persona charter text, BYTE-EQUAL to the
  fenced block in ``design.md`` §11 ("Persona seed text (ships in the
  migration/seed, v1 day one — VERBATIM)"). A byte-identity pin
  compares the constant against the design document;
* ``seed_persona_if_absent(agent_id="ari")`` — an idempotent boot-time
  get-or-create that inserts ONE approved row with ``source="seed"``;
* the failure tolerance: a seed failure logs
  ``WARNING [Memoir] persona seed failed: <exc>`` and **boot continues**
  (the persona is CONTENT, not schema — a content failure never escalates
  to an availability failure).

Design contract refs: ``design.md`` §11 (the verbatim text), §4.3 (the
persona layer); ``phase1-plan.md`` D1.13 (the boot-time get-or-create +
the failure tolerance), T1.4; ``decisions.md`` DD-9 (persona edits land
PENDING; human-lane approval only) and DD-18 (the ``.sql`` is the audit
record — the runner is SQLite-only, so a migration-carried INSERT would
never seed PG).

The seed is KNOB-INDEPENDENT (flag #13): the kill-switch gates the
SURFACES, not the substrate. The seed runs with the knob OFF.
"""

from __future__ import annotations
