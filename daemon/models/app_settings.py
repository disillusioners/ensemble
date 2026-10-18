"""``app_settings`` — DB-backed daemon runtime settings (db-backed-settings P1).

One row per MANAGED key (registry-gated by
:mod:`daemon.services.settings_service` — unregistered keys are
refused everywhere, so this table can never accumulate junk). The v1
(and currently only) managed key is ``limits.llm_concurrency``; see
the registry docstring there for the resolution ladder
(DB > env > yaml literal > field default) and its caveats.

DUAL-DRIVER POSTURE (the ``custom_agent_configs`` /
``20261017_000001_create_secrets_tables`` precedent):

* SQLite — this table's canonical persistent record is the
  companion migration
  ``daemon/migrations/versions/20261018_000001_create_app_settings.sql``
  (idempotent ``CREATE TABLE IF NOT EXISTS``, applied by the .sql
  migration runner on every startup). The runner is a NO-OP on
  PostgreSQL.
* PostgreSQL — the schema comes from ``SQLModel.metadata.create_all``
  with THIS model as the canonical source. Registration into the
  metadata happens via the one-line late-import block in
  ``daemon/manager.py`` (the snapshot / maintenance_runs /
  drift_events precedent), placed BEFORE the ``create_all`` call, so
  brand-new tables arrive on existing PG databases on the next boot
  with zero operator action (``create_all`` is ``checkfirst=True``;
  brand-new tables need no ``_ensure_postgres_columns`` mirror).
* The settings BOOT READ (``apply_db_settings_overrides``) runs
  BEFORE ``create_all`` / the SQLite runner — a missing table is the
  normal first-boot state and reads as "no overrides" (fail-open,
  never a boot-failure mode).

Schema rulings (architecture-recommendation.md Ruling 1 — BINDING):

* TEXT/JSON-string columns; NO native typed columns.
* Per-key type + range validation lives in the managed-key registry
  (``value_type`` + ``ge`` mirroring the pydantic Field constraint
  exactly) — NOT in DB CHECK constraints (dialect parity for zero
  benefit; two truths of validation would drift).
* Zero dialect-specific SQL; the query surface is PK lookups + one
  boot-time full read. No indexes beyond the PRIMARY KEY
  (≤10 rows forever at v1 scope).
* ``updated_by`` is the AuthContext.subject of the last writer; NULL
  when auth is off (never the empty string — nullable-actor
  precedent ``daemon/repositories/infra/models.py:257``).
* Timestamps are ISO-8601 UTC TEXT, application-stamped
  (the cross-driver convention of this table lineage).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    """One operator-set DB override for a managed daemon setting.

    The row is the DB tier of the resolution ladder — the HIGHEST
    tier: when a valid row exists for a registered key, its value
    wins over the env var, the yaml literal, and the field default,
    and is applied onto the live ``Config`` at the next daemon boot
    (v1 semantics are restart-required; there is no live-apply).

    NOTE on ``sa_column``: SQLModel ignores Field-level column
    options (``primary_key`` etc.) when ``sa_column`` is supplied —
    the Column itself carries ``primary_key`` / ``nullable``, which
    is the binding intent of Ruling 1's TEXT-column sketch. All
    string columns are declared ``Text`` so the PG ``create_all``
    schema matches the SQLite migration byte-for-byte in type
    (``drift_event_publisher`` / ``snapshot`` models precedent).
    """

    __tablename__ = "app_settings"

    key: str = Field(
        sa_column=Column("key", Text, primary_key=True),
        description=(
            "Managed-key identifier (registry-gated), e.g. "
            "'limits.llm_concurrency'. Unregistered keys are refused "
            "by the service on every read/write path."
        ),
    )
    value_json: str = Field(
        sa_column=Column("value_json", Text, nullable=False),
        description=(
            "JSON-encoded scalar (int|float|bool|str per value_type). "
            "Encoded/decoded only via the settings service."
        ),
    )
    value_type: str = Field(
        sa_column=Column("value_type", Text, nullable=False),
        description=(
            'Registry-declared type tag: "int" | "float" | "bool" | '
            '"str". FE rendering + coercion target; cross-checked '
            "against the registry on every read."
        ),
    )
    updated_by: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_by", Text, nullable=True),
        description=(
            "AuthContext.subject of the last writer; NULL when auth "
            "is off (never the empty string)."
        ),
    )
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False),
        description="ISO-8601 UTC timestamp, application-stamped.",
    )
