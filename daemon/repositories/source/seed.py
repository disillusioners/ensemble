"""Boot-time seeder for the tool-usage daily digest schedule (Phase 3).

Idempotent get-or-create keyed on a deterministic ``source_id`` (uuid5)
so a second seed is a PK-collision no-op (D-4). Honors the managed key
``limits.tool_usage_digest_seeding_enabled`` (D-5): OFF ⇒ no-op. Never
raises: a failure logs WARNING and boot continues (memoir seed pattern).

Council C6: this seeder runs inside ``InstanceManager.__init__`` BEFORE
the api lifespan wires the managed-values provider — so at this boot
call site the ``live_managed_value`` cache is EMPTY and a DB override
used to be silently ignored (operator-intent reversal: disable → 14d
GC reap → reboot re-seeds). The DB tier is now read DIRECTLY at seed
time: one PK lookup on ``app_settings`` through the SAME engine the
seeder already builds its repositories from — no throwaway engine, no
api.py boot-order change. Tiering: explicit env first (existing path),
then the DB row, then the managed cache / default.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session

from daemon.models.app_settings import AppSetting
from daemon.repositories.source.models import SourceStatus
from daemon.repositories.factory import create_project_repository, create_source_repository

logger = logging.getLogger(__name__)

SEED_NAME = "tool-usage-daily-digest"
SEED_SOURCE_TYPE = "scheduler"
SEED_SOURCE_ID = str(uuid.uuid5(uuid.uuid5.__class__.NAMESPACE_DNS if False else uuid.NAMESPACE_DNS, "tool-usage-daily-digest"))
# above is just uuid5(NAMESPACE_DNS, ...); keep simple:
SEED_SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "tool-usage-daily-digest"))

DIGEST_MESSAGE = "Call tool_usage_digest for the previous day and write the durable digest."

#: The D-5 opt-out managed key (registry: settings_service MANAGED_KEYS;
#: DB/ENV-only — ``config_path=None`` — so the DB row IS the operator
#: tier this boot-time call site must honor).
SEED_OPTOUT_KEY = "limits.tool_usage_digest_seeding_enabled"


def _db_tier_seeding_enabled(engine: "Engine") -> "bool | None":
    """Read the D-5 opt-out row DIRECTLY from ``app_settings`` (C6).

    One PK lookup through the CALLER'S engine (the sanctioned query
    surface of this table lineage — the model docstring names "PK
    lookups + one boot-time full read"). Returns:

    * ``True`` / ``False`` — the row exists and ``value_json`` decodes
      to a JSON bool: the operator's DB-tier decision.
    * ``None`` — no row, OR the read failed (a missing table is the
      NORMAL first-boot state — the ``app_settings`` model's documented
      fail-open posture; lower tiers stay active).
    * ``False`` — row present but MALFORMED (undecodable ``value_json``
      or a decoded non-bool): FAIL-CLOSED. An unreadable row is not a
      statement of "on", and seeding is the default action this gate
      exists to suppress — the disable-survival promise (D-5) is the
      whole point of the key.
    """
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, SEED_OPTOUT_KEY)
    except Exception as exc:  # noqa: BLE001 — a tier read never aborts boot
        logger.info(
            "[ToolUsageDigest] app_settings DB tier unreadable (%s) — "
            "lower tiers active",
            exc,
        )
        return None
    if row is None:
        return None
    try:
        decoded = json.loads(row.value_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "[ToolUsageDigest] app_settings row %r malformed (failed parse: "
            "%s) — fail-closed, seeding suppressed",
            SEED_OPTOUT_KEY,
            exc,
        )
        return False
    if isinstance(decoded, bool):
        return decoded
    logger.warning(
        "[ToolUsageDigest] app_settings row %r malformed (decoded %s, not "
        "a bool) — fail-closed, seeding suppressed",
        SEED_OPTOUT_KEY,
        type(decoded).__name__,
    )
    return False


def _seeding_enabled(engine: "Engine | None" = None) -> bool:
    """Resolve the opt-out managed key (D-5), tiered.

    Explicit env tier first (existing path), then the DB override row
    read DIRECTLY at seed time (council C6 — the managed-values
    provider wires only in the api lifespan, so the cache below can
    never carry the row at this boot-time call site), then the
    managed-value cache / default (fail-open ON).
    """
    env = os.environ.get("LIMITS_TOOL_USAGE_DIGEST_SEEDING_ENABLED")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on", "t", "y"}
    if engine is not None:
        db_value = _db_tier_seeding_enabled(engine)
        if db_value is not None:
            return db_value
    try:
        from daemon.services.metrics_gate import live_managed_value

        value = live_managed_value(SEED_OPTOUT_KEY, True)
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
        if not _seeding_enabled(engine):
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
