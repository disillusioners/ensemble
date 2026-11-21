"""Phase-3 T7 pack (c) — boot-time seeder lifecycle (T5/T6) + GC-safety.

Covers plan ``phase3-plan.md`` §4 T7 (``seed idempotency (uuid5, seed twice
→ one row) + failure (T5); opt-out flag no-op; startable status; adapter
config shape carries ``schedule=``; GC-safety (enabled row not
collectable, ``repository.py:765``); boot ordering (T6)``) and the
phase-5 G5 gate.

WHAT THIS FILE COVERS THAT THE SIBLING DOES NOT
-----------------------------------------------
``tests/unit/repositories/test_tool_usage_seed_optout.py`` (council C6)
owns the DB-tier opt-out LADDER (row false ⇒ suppressed, malformed ⇒
fail-closed, missing table ⇒ fail-open, env-false ⇒ wins over a DB-true
row). This file owns the seeder's LIFECYCLE half:

* D-4 idempotency — a deterministic uuid5 PK makes seed-twice ONE row,
  on BOTH dialects;
* D-5 operator-intent survival — an EXISTING row is a no-op in ANY state,
  and the get-by-id / get-by-name rows are both dodged (the post-GC
  re-seed window is the only place a row is actually created);
* §3.8 adapter config shape — ``schedule='0 8 * * *'`` (NOT ``when=``),
  timezone/agent/recurrence/instance_mode/project routing;
* D-2 startable status — the row is seeded in the state the compensated
  create-path produces (RUNNING), so ``start_all()`` (which SKIPS
  stopped/cancelled rows) picks it up;
* S6 GC-safety — the enabled seeded row is NOT a GC candidate even when
  aged far past retention;
* T6 boot ordering — the manager's seeder call is BEFORE ``start_all()``
  at the lifespan site, and the call is wrapped so a substrate failure
  can never abort boot;
* the managed-cache tier (the third tier of the C6 ladder) — pinned here
  because the sibling file pins the DB tier with the cache deliberately
  EMPTY (the honest boot shape) and never exercises the populated-cache
  path.

SAFETY FENCE: repo-relative reads only; the PostgreSQL legs use the
shared dev fence (throwaway schema, dropped at teardown).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

from daemon.models.app_settings import AppSetting
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectTagLink,
)
from daemon.repositories.source.models import SourceConfig, SourceStatus
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.repositories.source.seed import (
    DIGEST_MESSAGE,
    SEED_NAME,
    SEED_OPTOUT_KEY,
    SEED_SOURCE_ID,
    SEED_SOURCE_TYPE,
    _seeding_enabled,
    seed_tool_usage_digest_schedule,
)
from daemon.services import metrics_gate
from tests.helpers.tool_usage_t7_pg_fence import pg_throwaway_schema_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_VAR = "LIMITS_TOOL_USAGE_DIGEST_SEEDING_ENABLED"

#: Every table the seeder's own repositories touch (D-1: it builds its own
#: project + source repositories from the caller's engine).
SEEDER_TABLES = [
    SourceConfig.__table__,
    Project.__table__,
    ProjectTagLink.__table__,
    ProjectShortnameLink.__table__,
    ProjectMetadataRecord.__table__,
    AppSetting.__table__,
]


# ── fixtures: dual dialect ─────────────────────────────────────────────────


@pytest.fixture(params=["sqlite", "postgres"])
def seed_engine(request, tmp_path):
    """tmp SQLite + dev-fenced PG (throwaway schema)."""
    if request.param == "sqlite":
        engine = create_engine(
            f"sqlite:///{tmp_path / 'seed_lifecycle.db'}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        SQLModel.metadata.create_all(engine, tables=SEEDER_TABLES)
        try:
            yield engine
        finally:
            engine.dispose()
        return
    with pg_throwaway_schema_engine(SEEDER_TABLES, prefix="t7seed") as engine:
        yield engine


@pytest.fixture(autouse=True)
def _honest_boot_shape(monkeypatch):
    """No env statement, EMPTY managed cache — the real boot call site."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(metrics_gate, "_managed_values", {})


def _rows(engine: Engine) -> list[SourceConfig]:
    with Session(engine) as session:
        return list(session.exec(select(SourceConfig)))


def _seeded_row(engine: Engine) -> SourceConfig | None:
    with Session(engine) as session:
        return session.get(SourceConfig, SEED_SOURCE_ID)


# ══════════════════════════════════════════════════════════════════════════
# D-4 / S5 / S9 — uuid5 idempotency
# ══════════════════════════════════════════════════════════════════════════


class TestUuid5Idempotency:
    def test_the_seed_id_is_the_deterministic_uuid5(self):
        import uuid as uuid_mod

        assert SEED_SOURCE_ID == str(uuid_mod.uuid5(uuid_mod.NAMESPACE_DNS, SEED_NAME))
        assert SEED_NAME == "tool-usage-daily-digest"
        assert SEED_SOURCE_TYPE == "scheduler"

    def test_seeding_twice_yields_exactly_one_row(self, seed_engine):
        first = seed_tool_usage_digest_schedule(seed_engine)
        second = seed_tool_usage_digest_schedule(seed_engine)

        assert first is not None and second is not None
        assert first.source_id == second.source_id == SEED_SOURCE_ID
        rows = _rows(seed_engine)
        assert len(rows) == 1
        assert rows[0].source_id == SEED_SOURCE_ID

    def test_a_third_seed_still_leaves_one_row(self, seed_engine):
        for _ in range(3):
            seed_tool_usage_digest_schedule(seed_engine)
        assert len(_rows(seed_engine)) == 1

    def test_the_second_seed_returns_the_existing_row_object(self, seed_engine):
        """The idempotent path RETURNS the existing row (it does not
        return None, which would be indistinguishable from a failure)."""
        first = seed_tool_usage_digest_schedule(seed_engine)
        second = seed_tool_usage_digest_schedule(seed_engine)
        assert second is not None
        assert second.source_id == first.source_id
        assert second.name == SEED_NAME


# ══════════════════════════════════════════════════════════════════════════
# D-5 — operator intent survives a re-seed (no-op in ANY state)
# ══════════════════════════════════════════════════════════════════════════


class TestOperatorIntentSurvivesReseed:
    @pytest.mark.parametrize(
        "status",
        [
            SourceStatus.STOPPED.value,
            SourceStatus.CANCELLED.value,
            SourceStatus.RUNNING.value,
            SourceStatus.ERROR.value,
        ],
    )
    def test_an_existing_row_is_a_noop_in_any_state(self, seed_engine, status):
        """A disabled / stopped / cancelled row the operator parked must
        never be re-seeded back into service."""
        seed_tool_usage_digest_schedule(seed_engine)
        repo = SQLModelSourceRepository(seed_engine)
        repo.update_source_status(SEED_SOURCE_ID, status)
        repo.update_source_config(SEED_SOURCE_ID, enabled=False)

        again = seed_tool_usage_digest_schedule(seed_engine)

        assert again is not None
        assert again.source_id == SEED_SOURCE_ID
        row = _seeded_row(seed_engine)
        assert row is not None
        assert row.status == status
        assert row.enabled is False, "the operator's disable must survive"
        assert len(_rows(seed_engine)) == 1

    def test_a_row_with_the_same_name_but_a_foreign_id_still_dodges(
        self, seed_engine
    ):
        """The get-by-NAME leg: a row carrying the logical key under a
        different source_id is still an existing row (the `name` is a
        logical key, no unique constraint) — re-seeding must not create a
        duplicate logical schedule."""
        repo = SQLModelSourceRepository(seed_engine)
        foreign = repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name=SEED_NAME,
            config={"agent": "ari", "schedule": "0 8 * * *"},
            enabled=False,
            autostart=False,
        )
        assert foreign.source_id != SEED_SOURCE_ID

        result = seed_tool_usage_digest_schedule(seed_engine)

        assert result is not None
        assert result.source_id == foreign.source_id
        assert _seeded_row(seed_engine) is None, (
            "no second row may be minted when the logical key already exists"
        )
        assert len(_rows(seed_engine)) == 1

    def test_a_foreign_named_row_keeps_its_own_config(self, seed_engine):
        """The dodge is a true no-op — it does not overwrite the existing
        row's columns with the seeded defaults."""
        repo = SQLModelSourceRepository(seed_engine)
        repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name=SEED_NAME,
            config={"agent": "someone-else"},
            enabled=False,
        )

        seed_tool_usage_digest_schedule(seed_engine)

        rows = _rows(seed_engine)
        assert len(rows) == 1
        assert rows[0].config["agent"] == "someone-else"
        assert rows[0].enabled is False


# ══════════════════════════════════════════════════════════════════════════
# S10 — the persisted ADAPTER config shape
# ══════════════════════════════════════════════════════════════════════════


class TestAdapterConfigShape:
    def test_the_config_carries_the_cron_schedule_not_when(self, seed_engine):
        """§3.8 / A5: the scheduler adapter reads ``config['schedule']``
        (cron) — a ``when=`` key would be silently ignored at fire time."""
        seed_tool_usage_digest_schedule(seed_engine)
        row = _seeded_row(seed_engine)
        assert row is not None
        config = dict(row.config)

        assert config["schedule"] == "0 8 * * *"
        assert "when" not in config

    def test_every_frozen_config_field_is_persisted(self, seed_engine):
        seed_tool_usage_digest_schedule(seed_engine)
        row = _seeded_row(seed_engine)
        config = dict(row.config)

        assert config["agent"] == "ari"
        assert config["message"] == DIGEST_MESSAGE
        assert config["timezone"] == "Asia/Ho_Chi_Minh"
        assert config["recurrence"] == "daily"
        assert config["local_time"] == "08:00"
        assert config["instance_mode"] == "new_instance"

    def test_the_config_routes_to_the_system_default_project(self, seed_engine):
        """A1/D-1: the seeder derives the project id ITSELF via
        ``ensure_system_default_project()`` (the constant is set later, in
        the api lifespan) so durable job-queue routing works."""
        import uuid as uuid_mod

        from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

        seed_tool_usage_digest_schedule(seed_engine)
        row = _seeded_row(seed_engine)

        expected = str(uuid_mod.uuid5(uuid_mod.NAMESPACE_DNS, SYSTEM_DEFAULT_PROJECT_NAME))
        assert row.config["project_id"] == expected
        with Session(seed_engine) as session:
            project = session.get(Project, expected)
            assert project is not None
            assert project.name == SYSTEM_DEFAULT_PROJECT_NAME

    def test_the_row_is_enabled_and_autostart(self, seed_engine):
        seed_tool_usage_digest_schedule(seed_engine)
        row = _seeded_row(seed_engine)

        assert row.enabled is True
        assert row.autostart is True
        assert row.source_type == SEED_SOURCE_TYPE
        assert row.name == SEED_NAME
        assert row.credentials is None


# ══════════════════════════════════════════════════════════════════════════
# D-2 / S11 — the seeded row is STARTABLE
# ══════════════════════════════════════════════════════════════════════════


class TestStartableStatus:
    def test_the_seeded_row_is_running_not_stopped(self, seed_engine):
        """``create_source_config`` initializes STOPPED and
        ``start_all()`` SKIPS stopped/cancelled rows — the compensation
        to RUNNING is exactly what makes a fresh row startable."""
        row = seed_tool_usage_digest_schedule(seed_engine)
        persisted = _seeded_row(seed_engine)

        assert row is not None and persisted is not None
        assert persisted.status == SourceStatus.RUNNING.value
        assert persisted.status not in {
            SourceStatus.STOPPED.value,
            SourceStatus.CANCELLED.value,
        }

    def test_the_plain_create_path_would_leave_it_stopped(self, seed_engine):
        """The discriminator: the raw repository create initializes
        STOPPED, so the assertion above is not vacuous."""
        repo = SQLModelSourceRepository(seed_engine)
        row = repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name="a-different-logical-name",
            config={},
        )
        assert row.status == SourceStatus.STOPPED.value

    def test_a_reseed_of_the_started_row_changes_nothing(self, seed_engine):
        seed_tool_usage_digest_schedule(seed_engine)
        seed_tool_usage_digest_schedule(seed_engine)
        row = _seeded_row(seed_engine)
        assert row is not None
        assert row.status == SourceStatus.RUNNING.value


# ══════════════════════════════════════════════════════════════════════════
# S6 — GC-safety: an enabled row is never collectable
# ══════════════════════════════════════════════════════════════════════════


class TestGcSafety:
    def test_the_enabled_seeded_row_is_not_a_gc_candidate(self, seed_engine):
        """The GC predicate requires ``enabled == False`` AND a terminal
        status AND age > retention — an enabled, running row can never be
        collected, however old it is."""
        seed_tool_usage_digest_schedule(seed_engine)
        repo = SQLModelSourceRepository(seed_engine)

        # Age the row far past any retention window by rewriting the
        # persisted stamps (the predicate's age leg reads ``updated_at``).
        ancient = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        with Session(seed_engine) as session:
            row = session.get(SourceConfig, SEED_SOURCE_ID)
            row.created_at = ancient
            row.updated_at = ancient
            session.add(row)
            session.commit()

        candidates, skipped = repo.gc_find_candidates(
            retention_days=14,
            now=datetime.now(timezone.utc),
            limit=100,
        )

        assert [c.source_id for c in candidates] == []
        assert skipped == 0
        assert _seeded_row(seed_engine) is not None, "the row must survive"

    def test_a_disabled_stopped_seeded_row_is_eligible_after_retention(
        self, seed_engine
    ):
        """The negative control: the SAME row, once the operator disables
        and stops it and it ages out, IS collectable — so the leg above is
        not vacuous."""
        seed_tool_usage_digest_schedule(seed_engine)
        repo = SQLModelSourceRepository(seed_engine)
        repo.update_source_status(SEED_SOURCE_ID, SourceStatus.STOPPED.value)
        repo.update_source_config(SEED_SOURCE_ID, enabled=False)

        ancient = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        with Session(seed_engine) as session:
            row = session.get(SourceConfig, SEED_SOURCE_ID)
            row.updated_at = ancient
            session.add(row)
            session.commit()

        candidates, _skipped = repo.gc_find_candidates(
            retention_days=14,
            now=datetime.now(timezone.utc),
            limit=100,
        )
        assert [c.source_id for c in candidates] == [SEED_SOURCE_ID]

    def test_a_within_retention_disabled_row_is_not_yet_eligible(self, seed_engine):
        seed_tool_usage_digest_schedule(seed_engine)
        repo = SQLModelSourceRepository(seed_engine)
        repo.update_source_status(SEED_SOURCE_ID, SourceStatus.STOPPED.value)
        repo.update_source_config(SEED_SOURCE_ID, enabled=False)

        candidates, _skipped = repo.gc_find_candidates(
            retention_days=14,
            now=datetime.now(timezone.utc),
            limit=100,
        )
        assert [c.source_id for c in candidates] == []


# ══════════════════════════════════════════════════════════════════════════
# T6 — boot ordering + failure tolerance
# ══════════════════════════════════════════════════════════════════════════


class TestBootOrderingAndFailureTolerance:
    def test_the_manager_seeds_before_start_all(self):
        """T6: the seeder call sits in ``__init__`` (source: before the
        lifespan's ``start_all()``), so the enabled row exists when
        adapters load."""
        source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
        seed_call = source.index("seed_tool_usage_digest_schedule(self._engine)")
        start_all = source.index("await self.source_registry.start_all()")
        assert seed_call < start_all, (
            "the seeder must run BEFORE start_all() — a row minted after "
            "adapter loading would never start"
        )

    def test_the_manager_wraps_the_seed_call_so_boot_cannot_abort(self):
        source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
        idx = source.index("seed_tool_usage_digest_schedule(self._engine)")
        window = source[max(0, idx - 400) : idx + 400]
        assert "try:" in window
        assert "except Exception" in window
        assert "logger.warning" in window

    def test_a_failing_engine_returns_none_and_never_raises(self, caplog):
        """S11/§3.11: a substrate failure logs a WARNING and returns None;
        the boot continues (a measurement substrate is never an
        availability failure)."""

        class _BoomEngine:
            def __getattr__(self, name):  # noqa: ANN001
                raise RuntimeError("substrate gone")

            def connect(self):  # noqa: ANN201
                raise RuntimeError("substrate gone")

        with caplog.at_level(logging.WARNING):
            result = seed_tool_usage_digest_schedule(_BoomEngine())

        assert result is None
        assert any(
            "ToolUsageDigest" in rec.message or "ToolUsageDigest" in rec.name
            for rec in caplog.records
        )

    def test_a_partial_schema_engine_returns_none_and_never_raises(self, tmp_path):
        """A REAL engine missing the source_configs table (the pre-migration
        first-boot shape) is the same contract: no raise, no row."""
        bare = create_engine(
            f"sqlite:///{tmp_path / 'bare.db'}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        try:
            assert seed_tool_usage_digest_schedule(bare) is None
        finally:
            bare.dispose()


# ══════════════════════════════════════════════════════════════════════════
# D-5 — the managed-cache tier (third rung of the C6 ladder)
# ══════════════════════════════════════════════════════════════════════════


class TestManagedCacheTier:
    def test_a_populated_cache_false_suppresses_seeding(self, seed_engine, monkeypatch):
        monkeypatch.setattr(metrics_gate, "_managed_values", {SEED_OPTOUT_KEY: False})

        assert _seeding_enabled(seed_engine) is False
        assert seed_tool_usage_digest_schedule(seed_engine) is None
        assert _rows(seed_engine) == []

    def test_a_populated_cache_true_permits_seeding(self, seed_engine, monkeypatch):
        monkeypatch.setattr(metrics_gate, "_managed_values", {SEED_OPTOUT_KEY: True})

        assert _seeding_enabled(seed_engine) is True
        assert seed_tool_usage_digest_schedule(seed_engine) is not None
        assert len(_rows(seed_engine)) == 1

    def test_the_db_row_outranks_the_managed_cache(self, seed_engine, monkeypatch):
        """C6 tier order: env → DB row → managed cache/default. A DB FALSE
        wins over a cache TRUE (the boot-time read is the authority), and
        the seeder therefore NO-OPS — the D-5 operator-disable promise."""
        monkeypatch.setattr(metrics_gate, "_managed_values", {SEED_OPTOUT_KEY: True})
        with Session(seed_engine) as session:
            session.add(
                AppSetting(
                    key=SEED_OPTOUT_KEY,
                    value_json="false",
                    value_type="bool",
                    updated_by=None,
                    updated_at="2026-11-21T00:00:00+00:00",
                )
            )
            session.commit()

        assert _seeding_enabled(seed_engine) is False
        assert seed_tool_usage_digest_schedule(seed_engine) is None
        assert _rows(seed_engine) == []

    def test_the_env_tier_outranks_both(self, seed_engine, monkeypatch):
        monkeypatch.setattr(metrics_gate, "_managed_values", {SEED_OPTOUT_KEY: True})
        monkeypatch.setenv(ENV_VAR, "0")

        assert _seeding_enabled(seed_engine) is False

    @pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on", "t", "y"])
    def test_the_env_tier_accepts_the_truthy_spellings(self, seed_engine, monkeypatch, truthy):
        monkeypatch.setenv(ENV_VAR, truthy)
        assert _seeding_enabled(seed_engine) is True

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "n", "f", ""])
    def test_the_env_tier_treats_other_spellings_as_off(self, seed_engine, monkeypatch, falsy):
        """An EXPLICIT env statement is never ignored: anything that is not
        a truthy spelling means OFF (fail-closed on the opt-out key)."""
        monkeypatch.setenv(ENV_VAR, falsy)
        assert _seeding_enabled(seed_engine) is False
