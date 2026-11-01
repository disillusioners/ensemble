"""Project skill source service package (Phase 1).

The file-backed project skill source: resolver, lazy index,
validation, addresses/pseudo-ids, embedding refresh dispatch into the
dedicated no-FK table. Phase 2 consumes :meth:`ProjectSkillSourceService.corpus_for`;
Phase 3 consumes :meth:`ProjectSkillSourceService.lookup_explicit`.
"""

from daemon.services.project_skill_source.manifest import (  # noqa: F401
    BODY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_NAMESPACES,
    MAX_SKILL_DIRS_PER_NAMESPACE,
    discover_skill_dirs,
    parse_and_validate,
    validate_containment,
)
from daemon.services.project_skill_source.model import (  # noqa: F401
    ProjectFileSkill,
    SkillIndexMeta,
    mint_address,
    mint_pseudo_id,
    parse_pf_address,
)
from daemon.services.project_skill_source.service import (  # noqa: F401
    ExplicitConflictRefused,
    ProjectSkillBodyUnavailable,
    ProjectSkillSourceService,
    Resolution,
    RootScanStats,
)

__all__ = [
    "ProjectSkillSourceService",
    "ProjectFileSkill",
    "SkillIndexMeta",
    "Resolution",
    "RootScanStats",
    "ProjectSkillBodyUnavailable",
    "ExplicitConflictRefused",
    "mint_pseudo_id",
    "mint_address",
    "parse_pf_address",
    "discover_skill_dirs",
    "parse_and_validate",
    "validate_containment",
    "BODY_MAX_BYTES",
    "MANIFEST_MAX_BYTES",
    "MAX_NAMESPACES",
    "MAX_SKILL_DIRS_PER_NAMESPACE",
]
