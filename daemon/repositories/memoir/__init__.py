"""Memoir Store v1 — the repository package (phase 0 skeleton).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` D0.1 — "New
repository package ``daemon/repositories/memoir/``").

Placement rationale (D0.1): the ``bash_spawn`` lane template
(``daemon/repositories/bash_spawn/{__init__,models,repository}.py`` — the
newest small-lane layout, itself citing the board / project-skill-loader
precedents). A ``daemon/models/memoir.py`` placement was rejected: the
models there are manager-owned tables, while this lane ships a repository
family.

Phase 0 ships importable, behavior-free modules. Phase 1 fills them:

* :mod:`daemon.repositories.memoir.models` — the four SQLModel tables
  (``memoir_episodes``, ``memoir_persona``, ``memoir_persona_versions``,
  ``memoir_syntheses``) plus the ``StarsListType`` column adapter.
* :mod:`daemon.repositories.memoir.seed` — ``MEMOIR_PERSONA_SEED`` (the
  design §11 text, byte-equal) and the idempotent boot-time get-or-create.
* :mod:`daemon.repositories.memoir.repository` — the persona lane (the
  mirrored transition vocabulary + the human-lane-guarded ``approve()``).
  The EPISODE surface arrives in phase 2.
"""

from __future__ import annotations
