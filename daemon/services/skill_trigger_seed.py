"""Default skill trigger rules.

Phase 4 of the Skill Evolution System. Five baseline rules
ship with the daemon so a fresh install has trigger coverage
out of the box. Per-project customizations layer on top via
``SkillTriggerRepository.create``.

Trigger catalogue
----------------

The default rules map to the ``condition_type`` discriminators
the :class:`~daemon.services.skill_trigger_engine.SkillTriggerEngine`
knows how to evaluate. The ``condition_json`` body is the
type-specific parameter bag (threshold, min_selections, etc.).

* ``low_completion_rate`` — flag skills that are being selected
  but the tasks that apply them rarely succeed. Suggests the
  skill content is misleading or incomplete.
* ``high_fallback_rate`` — flag skills that get applied but the
  agent falls back to a non-skill path most of the time.
  Suggests the skill is hard to apply or low-value.
* ``consecutive_failures`` — flag skills that have racked up a
  streak of consecutive task failures touching them.
* ``task_count_scan`` — periodic health check at N selections
  (independent of outcome). Catches "looks fine but never
  inspected" skills.
* ``periodic_scan`` — weekly freshness check regardless of
  counters. Catches stale skills that haven't been touched.

The ``min_selections`` floor on rate-based triggers avoids
flapping on a single early data point — a skill with one
selection and one failure has a 100% fallback rate but isn't
actionable yet.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Default trigger catalogue
# ============================================================


# Each entry maps to a row in ``skill_triggers``. ``name`` is
# the human-readable label; ``condition_type`` is the engine
# discriminator; ``condition_json`` is the type-specific
# parameter bag; ``action`` is the free-form action string the
# engine emits (``"analyze"`` for Tier 2 LLM analysis,
# ``"evolve_fix"`` for direct evolution).
DEFAULT_TRIGGERS: list[dict[str, Any]] = [
    {
        "name": "low_completion_rate",
        "condition_type": "low_completion_rate",
        "condition_json": {"threshold": 0.3, "min_selections": 5},
        "action": "analyze",
    },
    {
        "name": "high_fallback_rate",
        "condition_type": "high_fallback_rate",
        "condition_json": {"threshold": 0.5, "min_selections": 5},
        "action": "analyze",
    },
    {
        "name": "consecutive_failures",
        "condition_type": "consecutive_failures",
        "condition_json": {"threshold": 3},
        # Route through Tier 2 analysis first so the LLM can decide
        # if the skill is genuinely broken or just unlucky. The LLM
        # may skip evolution if failures are spurious.
        "action": "analyze",
    },
    {
        "name": "periodic_scan",
        "condition_type": "periodic_scan",
        "condition_json": {"interval_days": 7},
        "action": "analyze",
    },
    {
        "name": "task_count_scan",
        "condition_type": "task_count_scan",
        "condition_json": {"threshold": 20},
        "action": "analyze",
    },
]


# ============================================================
# Migration helper
# ============================================================


def _update_consecutive_failures_action(trigger_repo: Any) -> int:
    """Update existing ``consecutive_failures`` trigger rows from
    ``"evolve_fix"`` to ``"analyze"``.

    Idempotent guard: only updates rows where ``action`` is currently
    ``"evolve_fix"``. This is a one-time migration for databases
    that already have the old seed.
    """
    updated = 0
    try:
        triggers = trigger_repo.list(enabled_only=False)
        for t in triggers:
            if (
                getattr(t, "condition_type", None) == "consecutive_failures"
                and getattr(t, "action", None) == "evolve_fix"
            ):
                trigger_repo.update(t.id, action="analyze")
                updated += 1
                logger.info(
                    "Updated consecutive_failures trigger action: evolve_fix → analyze"
                )
    except Exception as exc:
        logger.warning(f"Failed to migrate consecutive_failures trigger action: {exc}")
    return updated


# ============================================================
# Seeding
# ============================================================


async def seed_default_triggers(
    trigger_repo: Any,
    project_id: Optional[str],
) -> int:
    """Seed ``DEFAULT_TRIGGERS`` for a project if not already present.

    Idempotent: existing rows (matched by ``name``) are skipped,
    so re-running the seeder after schema resets or upgrades is
    safe. Called from the daemon startup hook after Phase 1
    schema init.

    ``project_id=None`` seeds the GLOBAL set (rows with
    ``project_id IS NULL``); a string seeds per-project rules.

    Sync repo methods are dispatched via
    ``asyncio.to_thread`` so the call site can ``await`` from
    an async startup hook without blocking the event loop.

    Args:
        trigger_repo: :class:`SkillTriggerRepository` bound to
            the project's SQLAlchemy engine.
        project_id: Project ID to seed for, or ``None`` for the
            global set.

    Returns:
        Number of new trigger rows inserted (0 when every
        default already exists).
    """
    import asyncio

    def _seed() -> int:
        # ``enabled_only=False`` so we can match disabled
        # rows too — a row that exists but is disabled
        # shouldn't be re-created.
        existing = trigger_repo.list(
            project_id=project_id, enabled_only=False
        )
        existing_names = {t.name for t in existing}
        inserted = 0
        for trigger_def in DEFAULT_TRIGGERS:
            if trigger_def["name"] in existing_names:
                continue
            trigger_repo.create(
                name=trigger_def["name"],
                condition_type=trigger_def["condition_type"],
                condition_json=trigger_def["condition_json"],
                action=trigger_def["action"],
                project_id=project_id,
            )
            inserted += 1
        return inserted

    inserted = await asyncio.to_thread(_seed)
    if inserted:
        logger.info(
            f"Seeded default skill triggers: project_id={project_id}, "
            f"inserted={inserted}"
        )

    # Migration (W4): update existing consecutive_failures rows to 'analyze' action
    await asyncio.to_thread(_update_consecutive_failures_action, trigger_repo)

    return inserted