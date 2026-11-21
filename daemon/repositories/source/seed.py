"""Boot-time seeder for the tool-usage daily digest schedule (Phase 3).

Idempotent get-or-create keyed on a deterministic ``source_id`` (uuid5)
so a second seed is a PK-collision no-op (D-4). Honors the managed key
``limits.tool_usage_digest_seeding_enabled`` (D-5): OFF ⇒ no-op. Never
raises: a failure logs WARNING and boot continues (memoir seed pattern).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import Engine
from sqlmodel import Session

from daemon.repositories.source.models import SourceStatus
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.repositories.factory import create_project_repository, create_source_repository

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

SEED_NAME = "tool-usage-daily-digest"
SEED_SOURCE_TYPE = "scheduler"
SEED_SOURCE_ID = str(uuid.uuid5(uuid.uuid5.__class__.NAMESPACE_DNS if False else uuid.NAMESPACE_DNS, "tool-usage-daily-digest"))
# above is just uuid5(NAMESPACE_DNS, ...); keep simple:
SEED_SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "tool-usage-daily-digest"))

DIGEST_MESSAGE = "Call tool_usage_digest for the previous day and write the durable digest."


def _seeding_enabled() -> bool:
    """Resolve the opt-out managed key (D-5), fail-open to default ON."""
    env = os.environ.get("LIMITS_TOOL_USAGE_DIGEST_SEEDING_ENABLED")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on", "t", "y"}
    try:
        from daemon.services.metrics_gate import live_managed_value

        value = live_managed_value("limits.tool_usage_digest_seeding_enabled", True)
    except Exception:  # pragma: no cover
        return True
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "t", "y"}
    return bool(value)


def seed_tool_usage_digest_schedule(engine: "Engine") -> Any:
    """Idempotently seed the daily 08:00 ICT digest schedule.

    Returns the seeded/existing SourceConfig or None on failure/no-op.
    Never raises.
    """
    try:
        if not _seeding_enabled():
            logger.info("[ToolUsageDigest] seeding disabled by managed key; skipping")
            return None

        project_repo = create_project_repository(engine=engine, create_tables=False)
        source_repo = create_source_repository(engine=engine, create_tables=False)

        project_id = project_repo.ensure_system_default_project()

        # Idempotent: get-by-id first, then by name.
        existing = source_repo.get_source_config(SEED_SOURCE_ID)
        if existing is None:
            existing = source_repo.get_source_config_by_name(SEED_NAME)
        if existing is not None:
            # No-op in ANY state (D-5): preserve operator intent.
            return existing

        config = {
            "agent": "ari",
            "message": DIGEST_MESSAGE,
            "timezone": "Asia/Ho_Chi_Minh",
            "recurrence": "daily",
            "local_time": "08:00",
            "schedule": "0 8 * * *",
            "instance_mode": "new_instance",
            "project_id": project_id,
        }

        row = source_repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name=SEED_NAME,
            config=config,
            credentials=None,
            enabled=True,
            autostart=True,
            source_id=SEED_SOURCE_ID,
        )
        # Status compensation: create initializes STOPPED; start_all skips
        # stopped/cancelled rows. Promote to RUNNING so start_all picks it up.
        try:
            source_repo.update_source_status(row.source_id, SourceStatus.RUNNING.value)
        except Exception:  # noqa: BLE001
            pass
        logger.info("[ToolUsageDigest] seeded schedule %s", row.source_id)
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ToolUsageDigest] seed failed: %s", exc)
        return None


__all__ = ["seed_tool_usage_digest_schedule", "SEED_SOURCE_ID", "SEED_NAME"]
