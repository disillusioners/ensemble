"""Skill repository module.

Phase 1 of the Skill Evolution System — six SQLModel tables
for skill lifecycle, lineage, usage tracking, triggers,
embeddings, and A/B tests.

Tables:

* ``skills`` — skill document + counter columns.
* ``skill_lineage`` — parent/child evolution DAG.
* ``skill_usage_records`` — per-task usage events with
  feedback signals.
* ``skill_triggers`` — declarative condition → action rules.
* ``skill_embeddings`` — cached per-skill vector embeddings.
* ``skill_ab_tests`` — A/B test buckets grouping old + new
  variants.

See :mod:`.models` for the SQLModel definitions and
:mod:`.repository` for the six repository classes.
"""

from .models import (
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from .repository import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)

__all__ = [
    # Models
    "Skill",
    "SkillLineage",
    "SkillUsageRecord",
    "SkillTrigger",
    "SkillEmbedding",
    "SkillABTest",
    # Repositories
    "SkillRepository",
    "SkillLineageRepository",
    "SkillUsageRepository",
    "SkillTriggerRepository",
    "SkillEmbeddingRepository",
    "SkillABTestRepository",
]