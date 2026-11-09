"""Memoir Store v1 — the repository package (phase 1).

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` D0.1).

Placement rationale (D0.1): the ``bash_spawn`` lane template
(``daemon/repositories/bash_spawn/{__init__,models,repository}.py`` — the
newest small-lane layout, itself citing the board / project-skill-loader
precedents). A ``daemon/models/memoir.py`` placement was rejected: the
models there are manager-owned tables, while this lane ships a repository
family.

Contents:

* :mod:`daemon.repositories.memoir.models` — the four SQLModel tables
  (``memoir_episodes``, ``memoir_persona``, ``memoir_persona_versions``,
  ``memoir_syntheses``), the ``StarsListType`` column adapter and the
  persona / synthesis vocabularies.
* :mod:`daemon.repositories.memoir.seed` — ``MEMOIR_PERSONA_SEED`` (the
  design §11 text, byte-equal) and the idempotent boot-time
  get-or-create.
* :mod:`daemon.repositories.memoir.repository` — the persona lane (the
  mirrored transition vocabulary + the human-lane-guarded ``approve()``).
  The EPISODE surface arrives in phase 2.
"""

from __future__ import annotations

from .constellation_repository import MemoirConstellationRepository
from .models import (
    PERSONA_SOURCES,
    PERSONA_STATUSES,
    PERSONA_VALID_TRANSITIONS,
    PIN_ACTORS,
    SYNTHESIS_VERBS,
    MemoirEpisode,
    MemoirJevLedger,
    MemoirLine,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirProposalLedger,
    MemoirReflectionState,
    MemoirStar,
    MemoirStarAlias,
    MemoirSynthesis,
    StarsListType,
)
from .repository import MemoirPersonaRepository, MemoirTransitionError
from .seed import MEMOIR_PERSONA_SEED, MEMOIR_SEED_AGENT_ID, seed_persona_if_absent
from .syntheses_repository import MemoirSynthesesRepository

__all__ = [
    "MEMOIR_PERSONA_SEED",
    "MEMOIR_SEED_AGENT_ID",
    "PERSONA_SOURCES",
    "PERSONA_STATUSES",
    "PERSONA_VALID_TRANSITIONS",
    "PIN_ACTORS",
    "SYNTHESIS_VERBS",
    "MemoirConstellationRepository",
    "MemoirEpisode",
    "MemoirJevLedger",
    "MemoirLine",
    "MemoirPersona",
    "MemoirPersonaRepository",
    "MemoirPersonaVersion",
    "MemoirProposalLedger",
    "MemoirReflectionState",
    "MemoirStar",
    "MemoirStarAlias",
    "MemoirSynthesis",
    "MemoirSynthesesRepository",
    "MemoirTransitionError",
    "StarsListType",
    "seed_persona_if_absent",
]
