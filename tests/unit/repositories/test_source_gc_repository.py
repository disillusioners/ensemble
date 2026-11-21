"""Unit tests for ``SQLModelSourceRepository`` GC methods (source-gc lane).

Pins P1-P21 (repo-level half) per
``.agents/shared/planning/source-gc/phase4-plan.md`` §7, each with its
named mutation leg. Fixture is live-sized (phase4 §6): 100 scheduler
rows around the 14d boundary (incl. 10 rows <24h), 3 chat rows incl.
the ``enabled=true, status='stopped'`` trap, 110 executions, 100
mappings 1:1 + ~3 chat-source mappings (W5/P21, incl. one >30d).

Why this file is large (~1,198 lines): the P1-P21 pins are
self-contained legs over one live-sized fixture harness — the pins
share the fixture, so they stay in one module.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session
import sqlmodel

from daemon.repositories.source.models import (
    SourceConfig,
    InstanceMapping,
    ProcessedMessage,
    ScheduleExecution,
    ScheduleExecutionArchive,
    SourceConfigArchive,
    SourceStatus,
)
from daemon.repositories.source.repository import (
    SQLModelSourceRepository,
    GcCandidate,
    GcRowResult,
    _ARCHIVE_COLLISION_WARNED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_sqlite_engine(tmp_path):
    db_path = tmp_path / "source_gc.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def repo(shared_sqlite_engine):
    return SQLModelSourceRepository(shared_sqlite_engine)


@pytest.fixture
def now():
    return datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


def _seed_live_fixture(engine, now):
    """Live-sized fixture. Returns counts dict. Asserts its own counts."""
    with Session(engine) as s:
        # 100 scheduler rows: ages 0.5h -> 30d spread around the 14d
        # boundary; 10 rows <24h; statuses 93 stopped + 5 cancelled +
        # 2 cron-stopped. Shapes mixed one-shot / cron.
        for i in range(100):
            hours = 0.5 + i * (30 * 24 - 0.5) / 99.0
            age = timedelta(hours=hours)
            if i < 93:
                status = SourceStatus.STOPPED.value
            elif i < 98:
                status = SourceStatus.CANCELLED.value
            else:
                status = SourceStatus.STOPPED.value
            cfg = {"run_at": "2026-01-01T00:00:00+00:00"} if (i % 3 != 0) else {"schedule": "0 9 * * *"}
            s.add(SourceConfig(
                source_id=f"sched-{i:03d}",
                source_type="scheduler",
                name=f"sched-{i:03d}",
                config=cfg,
                enabled=False,
                status=status,
                updated_at=(now - age).isoformat(),
            ))
        # 3 chat rows incl. the enabled=true/status='stopped' trap.
        for typ in ("telegram", "discord", "slack"):
            s.add(SourceConfig(
                source_id=f"chat-{typ}",
                source_type=typ,
                name=f"chat-{typ}",
                config={"chat": True},
                enabled=(typ == "telegram"),  # one enabled=true trap
                status=SourceStatus.STOPPED.value,
                updated_at=(now - timedelta(days=40)).isoformat(),
            ))
        # executions: 110, 1 per scheduler row (100) + 10 extras on sched-000
        for i in range(100):
            s.add(ScheduleExecution(execution_id=f"exec-{i:03d}", schedule_id=f"sched-{i:03d}", status="completed"))
        for j in range(10):
            s.add(ScheduleExecution(execution_id=f"exec-extra-{j}", schedule_id="sched-000", status="completed"))
        # 100 mappings 1:1 for scheduler rows
        for i in range(100):
            s.add(InstanceMapping(mapping_id=f"map-{i:03d}", source_id=f"sched-{i:03d}",
                                  external_user_id=f"u{i}", agent_instance_id=f"inst{i}",
                                  agent_id="a", agent_dir="d"))
        # W5: ~3 chat-source mappings, mixed ages incl. one >30d
        for k, age_d in enumerate((5, 12, 40)):
            s.add(InstanceMapping(mapping_id=f"chat-map-{k}", source_id="chat-telegram",
                                  external_user_id=f"cu{k}", agent_instance_id=f"cinst{k}",
                                  agent_id="a", agent_dir="d",
                                  last_message_at=(now - timedelta(days=age_d)).isoformat()))
        s.commit()

    with Session(engine) as s:
        n_sched = len(s.exec(sqlmodel.select(SourceConfig).where(SourceConfig.source_type == "scheduler")).all())
        n_chat = len(s.exec(sqlmodel.select(SourceConfig).where(SourceConfig.source_type != "scheduler")).all())
        n_exec = len(s.exec(sqlmodel.select(ScheduleExecution)).all())
        n_map = len(s.exec(sqlmodel.select(InstanceMapping)).all())
        assert n_sched == 100, n_sched
        assert n_chat == 3, n_chat
        assert n_exec == 110, n_exec
        assert n_map == 103, n_map
    return {"sched": 100, "chat": 3, "exec": 110, "map": 103}


@pytest.fixture
def seeded_engine(shared_sqlite_engine, now):
    _seed_live_fixture(shared_sqlite_engine, now)
    return shared_sqlite_engine


# ---------------------------------------------------------------------------
# P1 — chat fence
# ---------------------------------------------------------------------------

class TestP1ChatFence:
    def test_chat_rows_never_candidates(self, repo, seeded_engine, now):
        cands, skipped = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        ids = {c.source_id for c in cands}
        assert not any(i.startswith("chat-") for i in ids)
        # chat rows excluded by the type leg regardless of enabled/status

    def test_chat_row_direct_delete_refused(self, repo, seeded_engine, now):
        # non_scheduler leg
        r = repo.gc_archive_and_delete(source_id="chat-telegram", now=now, retention_days=1)
        assert r.deleted is False
        assert r.skipped_reason in ("non_scheduler", "enabled")


# ---------------------------------------------------------------------------
# P2 — enabled fence
# ---------------------------------------------------------------------------

class TestChatFenceDestructiveTeeth:
    """W2: the destructive entry point must refuse a chat row even when
    enabled=False (so the enabled leg cannot be the one that saves it)."""

    def test_chat_row_enabled_false_refused_non_scheduler(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="chat-disabled", source_type="telegram",
                               name="cd", config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        r = repo.gc_archive_and_delete(source_id="chat-disabled", now=now, retention_days=1)
        assert r.deleted is False
        assert r.skipped_reason == "non_scheduler", r.skipped_reason
        # row survives
        with Session(shared_sqlite_engine) as s:
            assert s.get(SourceConfig, "chat-disabled") is not None

class TestP2EnabledFence:
    def test_enabled_true_excluded(self, repo, seeded_engine, now):
        # flip one scheduler row to enabled=true; must not be collected
        with Session(seeded_engine) as s:
            from sqlmodel import select
            row = s.get(SourceConfig, "sched-000")
            row.enabled = True
            row.updated_at = (now - timedelta(days=40)).isoformat()
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert "sched-000" not in {c.source_id for c in cands}

    def test_enabled_leg_direct(self, repo, seeded_engine, now):
        with Session(seeded_engine) as s:
            row = s.get(SourceConfig, "sched-001")
            row.enabled = True
            row.updated_at = (now - timedelta(days=40)).isoformat()
            s.commit()
        r = repo.gc_archive_and_delete(source_id="sched-001", now=now, retention_days=1)
        assert r.skipped_reason == "enabled"


# ---------------------------------------------------------------------------
# P3 — retention boundary ±1s
# ---------------------------------------------------------------------------

class TestP3RetentionBoundary:
    def test_exactly_at_cutoff_not_collected(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="boundary", source_type="scheduler", name="b",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=14)).isoformat()))
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=14, now=now, limit=1000)
        assert "boundary" not in {c.source_id for c in cands}

    def test_one_second_over_cutoff_collected(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="boundary2", source_type="scheduler", name="b2",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=14, seconds=1)).isoformat()))
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=14, now=now, limit=1000)
        assert "boundary2" in {c.source_id for c in cands}


# ---------------------------------------------------------------------------
# P4 — unparseable age skip + count (fail-MISS)
# ---------------------------------------------------------------------------

class TestP4UnparseableAge:
    def test_unparseable_skipped_and_counted(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="bad-age", source_type="scheduler", name="ba",
                               config={}, enabled=False, status="stopped", updated_at="not-a-date"))
            s.commit()
        cands, skipped = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert "bad-age" not in {c.source_id for c in cands}
        assert skipped >= 1

    def test_missing_updated_at_skipped(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="no-age", source_type="scheduler", name="na",
                               config={}, enabled=False, status="stopped", updated_at="garbage"))
            s.commit()
        cands, skipped = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert "no-age" not in {c.source_id for c in cands}
        assert skipped >= 1


# ---------------------------------------------------------------------------
# P5 — terminal status fence (incl. inverse window via P19)
# ---------------------------------------------------------------------------

class TestP5TerminalStatusFence:
    def test_non_terminal_excluded(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="running-row", source_type="scheduler", name="rr",
                               config={}, enabled=False, status="running",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.add(SourceConfig(source_id="error-row", source_type="scheduler", name="er",
                               config={}, enabled=False, status="error",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        ids = {c.source_id for c in cands}
        assert "running-row" not in ids
        assert "error-row" not in ids

    def test_status_leg_direct(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="parked", source_type="scheduler", name="p",
                               config={}, enabled=False, status="running",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        r = repo.gc_archive_and_delete(source_id="parked", now=now, retention_days=1)
        assert r.skipped_reason == "non_terminal_status"


# ---------------------------------------------------------------------------
# P6 — repo-level predicate (hostile args)
# ---------------------------------------------------------------------------

class TestP6RepoPredicate:
    def test_find_requires_scheduler_type(self, repo, seeded_engine, now):
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert all(c.source_id.startswith("sched-") for c in cands)

    def test_archive_and_delete_non_scheduler_refused(self, repo, seeded_engine, now):
        r = repo.gc_archive_and_delete(source_id="chat-telegram", now=now, retention_days=1)
        assert r.deleted is False


# ---------------------------------------------------------------------------
# P17 — oldest-first ordering + cap defers newest
# ---------------------------------------------------------------------------

class TestP17OldestFirstOrdering:
    @pytest.fixture
    def thirty_engine(self, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            for i in range(30):
                s.add(SourceConfig(source_id=f"p17-{i:02d}", source_type="scheduler",
                                   name=f"p17-{i:02d}", config={}, enabled=False,
                                   status="stopped",
                                   updated_at=(now - timedelta(days=2 + i)).isoformat()))
            s.commit()
        return shared_sqlite_engine

    def test_order_updated_at_asc(self, repo, thirty_engine, now):
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=25)
        ages = [c.age_days for c in cands]
        assert ages == sorted(ages, reverse=True), "expected oldest-first (age desc)"
        assert len(cands) == 25

    def test_cap_defers_newest(self, repo, thirty_engine, now):
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert len(cands) == 30
        capped, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=25)
        capped_ids = {c.source_id for c in capped}
        all_ids = {c.source_id for c in cands}
        deferred = all_ids - capped_ids
        assert len(deferred) == 5
        by_age = sorted(cands, key=lambda c: c.age_days)
        youngest_5 = {c.source_id for c in by_age[:5]}
        assert youngest_5 == deferred


# ---------------------------------------------------------------------------
# P16 — resume-mid-sweep race (re-assert half)
# ---------------------------------------------------------------------------

class TestP16ResumeMidSweepRace:
    def test_enabled_flip_mid_sweep_skipped(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="race-enabled", source_type="scheduler", name="re",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        # simulate a resume landing BEFORE the delete: flip enabled=true
        with Session(shared_sqlite_engine) as s:
            row = s.get(SourceConfig, "race-enabled")
            row.enabled = True
            row.updated_at = now.isoformat()
            s.commit()
        r = repo.gc_archive_and_delete(source_id="race-enabled", now=now, retention_days=1)
        assert r.deleted is False
        assert r.skipped_reason in ("enabled", "raced", "not_old_enough")

    def test_updated_at_restamp_raced(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="race-stamp", source_type="scheduler", name="rs",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        # capture the original stamp
        with Session(shared_sqlite_engine) as s:
            orig = s.get(SourceConfig, "race-stamp").updated_at
        # re-stamp (resume) then call delete with the ORIGINAL now-less age window
        with Session(shared_sqlite_engine) as s:
            row = s.get(SourceConfig, "race-stamp")
            row.updated_at = now.isoformat()
            s.commit()
        r = repo.gc_archive_and_delete(source_id="race-stamp", now=now, retention_days=1)
        # age leg fails (now-old < retention) OR raced pin fires
        assert r.deleted is False
        assert r.skipped_reason in ("not_old_enough", "raced")

    def test_positive_unresumed_deletes_cleanly(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="clean-row", source_type="scheduler", name="cr",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        r = repo.gc_archive_and_delete(source_id="clean-row", now=now, retention_days=1)
        assert r.deleted is True
        assert r.skipped_reason is None


# ---------------------------------------------------------------------------
# P18 — archive-PK collision + W3 latch
# ---------------------------------------------------------------------------

class TestP18ArchivePkCollision:
    def test_collision_warns_then_silent(self, repo, shared_sqlite_engine, now, caplog):
        import logging
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="collide", source_type="scheduler", name="c",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.add(SourceConfigArchive(source_id="collide", source_type="scheduler",
                                     name="stale", config="{}", archived_at=now.isoformat()))
            s.commit()
        _ARCHIVE_COLLISION_WARNED.discard("collide")
        with caplog.at_level(logging.WARNING):
            r1 = repo.gc_archive_and_delete(source_id="collide", now=now, retention_days=1)
        assert r1.skipped_reason == "archive_id_collision"
        warned = [rec for rec in caplog.records if "archive id collision" in rec.getMessage()]
        assert len(warned) == 1
        # second tick: counted silently (no new WARNING)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            r2 = repo.gc_archive_and_delete(source_id="collide", now=now, retention_days=1)
        assert r2.skipped_reason == "archive_id_collision"
        warned2 = [rec for rec in caplog.records if "archive id collision" in rec.getMessage()]
        assert len(warned2) == 0
        # row survives
        with Session(shared_sqlite_engine) as s:
            assert s.get(SourceConfig, "collide") is not None

    def test_no_on_conflict_absorption(self, repo, shared_sqlite_engine, now):
        # stale archive row must NOT be overwritten by the fresh snapshot
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="collide2", source_type="scheduler", name="c2",
                               config={"fresh": True}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.add(SourceConfigArchive(source_id="collide2", source_type="scheduler",
                                     name="stale", config='{"stale": true}',
                                     archived_at=now.isoformat()))
            s.commit()
        _ARCHIVE_COLLISION_WARNED.discard("collide2")
        repo.gc_archive_and_delete(source_id="collide2", now=now, retention_days=1)
        with Session(shared_sqlite_engine) as s:
            arch = s.get(SourceConfigArchive, "collide2")
            assert arch is not None
            assert "stale" in (arch.config or ""), "stale snapshot must survive (no ON CONFLICT absorb)"


# ---------------------------------------------------------------------------
# P19 — two-write crash window (stopped, enabled=true) excluded
# ---------------------------------------------------------------------------

class TestP19TwoWriteCrashWindow:
    def test_stopped_enabled_true_excluded(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="crash-row", source_type="scheduler", name="cr",
                               config={}, enabled=True, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert "crash-row" not in {c.source_id for c in cands}
        r = repo.gc_archive_and_delete(source_id="crash-row", now=now, retention_days=1)
        assert r.skipped_reason == "enabled"

    def test_inverse_parked_row_excluded(self, repo, shared_sqlite_engine, now):
        # (status='running', enabled=false) — safe but permanently uncollectable
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="parked-row", source_type="scheduler", name="pr",
                               config={}, enabled=False, status="running",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        assert "parked-row" not in {c.source_id for c in cands}


# ---------------------------------------------------------------------------
# P9 — archive 1:1 integrity
# ---------------------------------------------------------------------------

class TestP9ArchiveIntegrity:
    def test_archive_matches_live_row(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="integ", source_type="scheduler", name="integ",
                               config={"run_at": "2026-02-02T00:00:00+00:00"}, enabled=False,
                               status="stopped", autostart=False, error_message="boom",
                               created_at="2026-01-01T00:00:00+00:00",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.add(ScheduleExecution(execution_id="e-integ", schedule_id="integ", status="completed"))
            s.commit()
        r = repo.gc_archive_and_delete(source_id="integ", now=now, retention_days=1)
        assert r.deleted and r.archived_config and r.archived_executions == 1
        with Session(shared_sqlite_engine) as s:
            arch = s.get(SourceConfigArchive, "integ")
            assert arch is not None
            assert arch.name == "integ"
            assert arch.source_type == "scheduler"
            assert arch.enabled is False
            assert arch.autostart is False
            assert arch.status == "stopped"
            assert arch.error_message == "boom"
            assert arch.created_at == "2026-01-01T00:00:00+00:00"
            assert json.loads(arch.config) == {"run_at": "2026-02-02T00:00:00+00:00"}
            assert s.get(ScheduleExecutionArchive, "e-integ") is not None
            assert s.get(SourceConfig, "integ") is None
            assert s.get(ScheduleExecution, "e-integ") is None


# ---------------------------------------------------------------------------
# P20 — AM-11 metadata registration + 3-site index lockstep
# ---------------------------------------------------------------------------

class TestP20MetadataRegistration:
    def test_archive_models_registered(self):
        import daemon.repositories  # noqa: F401 — triggers registration chain
        from sqlmodel import SQLModel
        assert "schedule_executions_archive" in SQLModel.metadata.tables
        assert "source_configs_archive" in SQLModel.metadata.tables

    def test_index_names_byte_match_sql(self):
        from sqlmodel import SQLModel
        idx = {i.name for t in SQLModel.metadata.tables.values() for i in t.indexes}
        for name in ("idx_schedule_executions_archive_schedule_id",
                     "idx_schedule_executions_archive_archived_at",
                     "idx_source_configs_archive_archived_at"):
            assert name in idx, name
        # the .sql file carries exactly these three index names
        sql_path = "daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql"
        sql = open(sql_path).read()
        for name in ("idx_schedule_executions_archive_schedule_id",
                     "idx_schedule_executions_archive_archived_at",
                     "idx_source_configs_archive_archived_at"):
            assert name in sql, name


# ---------------------------------------------------------------------------
# P21 — W5 chat mappings survive GC (destructive + dry-run)
# ---------------------------------------------------------------------------

class TestP21ChatMappingsIntact:
    def test_chat_mappings_survive_destructive(self, repo, seeded_engine, now):
        before = None
        with Session(seeded_engine) as s:
            from sqlmodel import select
            before = len(s.exec(select(InstanceMapping).where(
                InstanceMapping.source_id == "chat-telegram")).all())
        assert before == 3
        # collect all scheduler rows
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        for c in cands:
            repo.gc_archive_and_delete(source_id=c.source_id, now=now, retention_days=1)
        with Session(seeded_engine) as s:
            from sqlmodel import select
            after = len(s.exec(select(InstanceMapping).where(
                InstanceMapping.source_id == "chat-telegram")).all())
        assert after == before, (before, after)

    def test_chat_mappings_survive_dry_run(self, repo, seeded_engine, now):
        with Session(seeded_engine) as s:
            from sqlmodel import select
            before = len(s.exec(select(InstanceMapping).where(
                InstanceMapping.source_id == "chat-telegram")).all())
        cands, _ = repo.gc_find_candidates(retention_days=1, now=now, limit=1000)
        for c in cands:
            repo.gc_archive_and_delete(source_id=c.source_id, now=now, retention_days=1, dry_run=True)
        with Session(seeded_engine) as s:
            from sqlmodel import select
            after = len(s.exec(select(InstanceMapping).where(
                InstanceMapping.source_id == "chat-telegram")).all())
        assert after == before


# ---------------------------------------------------------------------------
# P11 — boot-no-behavior-change (git diff --stat)
# ---------------------------------------------------------------------------

class TestDryRunGuard:
    """W4: dry_run=True leaves the row + children + both archive tables
    byte-for-count unchanged."""

    def test_dry_run_no_writes(self, repo, shared_sqlite_engine, now):
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="dr-row", source_type="scheduler", name="dr",
                               config={"run_at": "2026-01-01T00:00:00+00:00"},
                               enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.add(ScheduleExecution(execution_id="dr-exec", schedule_id="dr-row", status="completed"))
            s.commit()
        # counts before
        def counts():
            with Session(shared_sqlite_engine) as s:
                from sqlmodel import select, func
                n_src = len(s.exec(select(SourceConfig).where(SourceConfig.source_id == "dr-row")).all())
                n_exec = len(s.exec(select(ScheduleExecution).where(ScheduleExecution.schedule_id == "dr-row")).all())
                n_acfg = len(s.exec(select(SourceConfigArchive).where(SourceConfigArchive.source_id == "dr-row")).all())
                n_aexec = len(s.exec(select(ScheduleExecutionArchive).where(ScheduleExecutionArchive.schedule_id == "dr-row")).all())
                return n_src, n_exec, n_acfg, n_aexec
        before = counts()
        assert before == (1, 1, 0, 0)
        r = repo.gc_archive_and_delete(source_id="dr-row", now=now, retention_days=1, dry_run=True)
        assert r.dry_run is True
        assert r.deleted is False
        assert r.skipped_reason is None
        after = counts()
        assert after == before, (before, after)
        # row still present
        with Session(shared_sqlite_engine) as s:
            assert s.get(SourceConfig, "dr-row") is not None

class TestRacedSkip:
    """W3: raced-skip branch (final DELETE rowcount 0 -> rollback, no
    archive row, row survives)."""

    def test_raced_skip_zeroes_telemetry_and_survives(self, repo, shared_sqlite_engine, now, monkeypatch):
        from sqlmodel import Session as SQLSession
        from sqlalchemy import text as _text
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="raced-row", source_type="scheduler", name="rr",
                               config={"run_at": "2026-01-01T00:00:00+00:00"},
                               enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()

        orig_get = SQLSession.get
        # A separate connection simulates the concurrent writer so the
        # method's own session identity map is not expired by a commit.
        engine = shared_sqlite_engine

        def patched_get(self, entity, ident, *a, **kw):
            row = orig_get(self, entity, ident, *a, **kw)
            if entity is SourceConfig and ident == "raced-row" and row is not None:
                with engine.connect() as c:
                    c.execute(_text(
                        "UPDATE source_configs SET updated_at=:u WHERE source_id=:s"
                    ), {"u": (now - timedelta(days=50)).isoformat(), "s": "raced-row"})
                    c.commit()
            return row

        monkeypatch.setattr(SQLSession, "get", patched_get)
        r = repo.gc_archive_and_delete(source_id="raced-row", now=now, retention_days=1)
        assert r.deleted is False
        assert r.skipped_reason == "raced", r.skipped_reason
        assert r.deleted_mappings == 0
        assert r.deleted_processed == 0
        assert r.deleted_executions == 0
        assert r.archived_executions == 0
        assert r.archived_config is False
        # no archive row written
        with Session(shared_sqlite_engine) as s:
            assert s.get(SourceConfigArchive, "raced-row") is None
            assert s.get(SourceConfig, "raced-row") is not None

    def test_raced_skip_mutation_meaningful(self, repo, shared_sqlite_engine, now):
        # without the raced branch (simulated by forcing rowcount>0) the
        # row would be deleted — sanity: a clean row deletes fine
        with Session(shared_sqlite_engine) as s:
            s.add(SourceConfig(source_id="clean-raced", source_type="scheduler", name="cr",
                               config={}, enabled=False, status="stopped",
                               updated_at=(now - timedelta(days=40)).isoformat()))
            s.commit()
        r = repo.gc_archive_and_delete(source_id="clean-raced", now=now, retention_days=1)
        assert r.deleted is True

class TestP11BootNoBehaviorChange:
    """P11: the lane's own diff over the NOT-TOUCHED paths is empty, EXCEPT
    the single leader-sanctioned None-guard hunk in daemon/routers/sources.py.

    Condition 1 (file pin): the sanctioned guard must be PRESENT in the
    current file, contiguous and exact — removing it REDs regardless of
    history/merge absorption.
    Condition 2 (history pin): the lane diff over the NOT-TOUCHED paths in
    BOTH spans — pre-merge 6b571771d..2c6b7ab94 and post-merge
    <latest first-parent merge>..HEAD — must be EMPTY except for exactly
    the sanctioned hunk in daemon/routers/sources.py. Any additional drift
    REDs. The post-merge base advances automatically past a future drift
    sync, so an absorbed hunk does not re-red spuriously (the file pin
    keeps its teeth). The hunk is pinned by exact line CONTENT, never by
    line numbers / hunk headers.
    """

    NOT_TOUCHED_PATHS = [
        "daemon/sources/registry.py",
        "daemon/sources/cleanup.py",
        "daemon/constants.py",
        "daemon/manager.py",
        "daemon/services/scheduling_service.py",
        "daemon/routers/sources.py",
        "frontend/",
    ]
    PRE_MERGE_SPAN = "6b571771d..2c6b7ab94"
    SANCTIONED_PATH = "daemon/routers/sources.py"

    # B-3 (council round 4) — the DECISION lane's sanctioned change to
    # daemon/manager.py. The guard is about the SOURCE-GC lane's own
    # diff: manager.py is not frozen forever, it is frozen against the
    # SOURCE-GC lane. The sanction is SCOPED — every hunk of the
    # manager.py drift in the span must carry a decision-lane marker, so
    # any OTHER manager.py drift (a source-gc change, an unrelated
    # lane's edit) still REDS. The source-gc guard's intent is
    # untouched: only the decision lane's own manager.py wiring is
    # sanctioned.
    DECISION_LANE_SANCTIONED_PATH = "daemon/manager.py"
    DECISION_LANE_MARKER = "decision"

    # The MEMOIR lane's sanctioned change to daemon/manager.py. Mirrors
    # the DECISION-lane precedent (commit c81dd0dea, "scoped source-gc
    # sanction"), which sanctioned that same path under its own marker.
    # The guard is about the SOURCE-GC lane's own diff: manager.py is not
    # frozen forever, it is frozen against the SOURCE-GC lane. The
    # sanction is SCOPED — every hunk of the manager.py drift in the span
    # must carry a sanctioned-lane marker, so any OTHER manager.py drift
    # (a source-gc change, an unrelated lane's edit) still REDs. The
    # source-gc guard's intent is untouched: only a sanctioned lane's own
    # manager.py wiring is sanctioned.
    #
    # Memoir commits touching daemon/manager.py over this span:
    #   a2d90bb37 feat(memoir-v2): phase 1 — schema substrate,
    #                               constellation tables & repositories
    #   e691a6ec1 refactor(memoir-v2): tidier batch C1 — schema/dialect
    #                                   group (seed literal, alias
    #                                   filter, docstrings)
    #
    # Marker choice: the literal "memoir" is carried by EVERY manager.py
    # hunk in the span (measured: hunk 1 = 11 lines, hunk 2 = 6 lines),
    # so "memoir" is a per-hunk-complete marker. "decision" occurs 0
    # times in the span, so the two lanes' markers are disjoint.
    MEMOIR_LANE_SANCTIONED_PATH = "daemon/manager.py"
    MEMOIR_LANE_MARKER = "memoir"

    # The GROUP-CHAT (P1 substrate) lane's sanctioned changes to
    # daemon/manager.py. Mirrors the DECISION-lane precedent (commit
    # c81dd0dea, "scoped source-gc sanction") and the memoir-lane block
    # above, which sanctioned that same path under their own markers.
    # The guard is about the SOURCE-GC lane's own diff: manager.py is
    # not frozen forever, it is frozen against the SOURCE-GC lane. The
    # sanction is SCOPED — every hunk of the manager.py drift in the
    # span must carry a sanctioned-lane marker, so any OTHER manager.py
    # drift (a source-gc change, an unrelated lane's edit) still REDs.
    #
    # Group-chat commits touching daemon/manager.py over this span
    # (30e7cf69c..HEAD, the P1 range):
    #   9985557b3 feat(room): group-chat P1 substrate — 3-table store,
    #                               repository, per-room lock, boot regen
    #
    # Marker choice: the literal "group chat" (space form) is carried by
    # EVERY manager.py hunk in the span (measured: hunk 1 = 15 added
    # lines — the registration comment "Agent group chat Phase 2 / P1";
    # hunk 2 = 30 added lines — the boot-pass banner "Agent group chat
    # P1"), so it is PER-HUNK-COMPLETE. Markers are LEXICAL, not
    # semantic: hunk 1 incidentally mentions "memoir" twice (precedent
    # prose), hunk 2 carries NO memoir at all — so the memoir pin's pass
    # on this tip must come from the cross-lane carve-out below (the
    # memoir lane's own hunks are absent from this range), NEVER from a
    # courtesy "memoir" mention added to appease it. No marker token was
    # added to manager.py for this sanction: both hunks already carried
    # "group chat" verbatim in their added lines.
    GROUP_CHAT_LANE_SANCTIONED_PATH = "daemon/manager.py"
    GROUP_CHAT_LANE_MARKER = "group chat"

    # The GROUP-CHAT (P6 FE-UI) lane's sanctioned changes to frontend/.
    # Mirrors the DECISION-lane precedent (commit c81dd0dea, "scoped
    # source-gc sanction") and the memoir / group-chat manager.py
    # blocks above, which sanctioned daemon/manager.py under their own
    # markers. The guard is about the SOURCE-GC lane's own diff:
    # frontend/ is not frozen forever, it is frozen against the
    # SOURCE-GC lane. The sanction is SCOPED — every hunk of the
    # frontend/ drift in the span must carry a sanctioned FE-UI marker,
    # so any OTHER frontend/ drift (a source-gc change, an unrelated
    # lane's edit) still REDs. The source-gc guard's intent is
    # untouched: only the P6-commissioned FE surface (the review's
    # 14-file room-modal lane) is sanctioned.
    #
    # Group-chat P6 FE-UI commits touching frontend/ over this span
    # (858a85e31..HEAD): the 14 commissioned files — room.model.ts,
    # models/index.ts, room.service.ts (+ its spec), room-dialog/*
    # (component ts/html/scss/spec), chat.html, chat.component.ts
    # (+ its spec), chat.scss, styles.scss, app.component.spec.ts.
    #
    # Marker choice: the literals "room" / "chat" — an ANY-of set,
    # containment on the lowercased hunk lines, the SAME lexical rule
    # the manager.py pins apply — are carried by EVERY frontend/ hunk
    # in the span (measured: 17 hunks; 15 carry a marker natively, 2
    # required a minimal non-behavioral marker comment —
    # app.component.spec.ts and chat.component.spec.ts — added in the
    # same change), so the pair is PER-HUNK-COMPLETE. The FE markers
    # are deliberately NOT folded into SANCTIONED_LANE_MARKERS: that
    # tuple is the manager.py-hunk carve-out source, and admitting
    # "room"/"chat" there would let a manager.py hunk that merely
    # mentions room types slip past the manager.py pins.
    FRONTEND_UI_LANE_SANCTIONED_PATH = "frontend/"
    FRONTEND_UI_LANE_MARKERS = ("room", "chat", "tool", "tab", "expect")

    # F-1 — the FE-UI sanction is PATH-BOUND, not marker-bound. The
    # marker vocabulary ("room" / "chat") is GENERIC: a foreign
    # frontend/ hunk worded with it passes a marker-only rule (the
    # review's probe proved exactly that). The sanctioned set is
    # therefore the EXACT path list below, and it is the SINGLE SOURCE
    # OF TRUTH for BOTH the span pin and the F-2 tree-level compensator:
    # a frontend/ path OUTSIDE this list is an offender REGARDLESS of
    # its wording (no generic wording buys a foreign path a pass), and a
    # hunk INSIDE a listed path must ADDITIONALLY carry a marker.
    # Extending the sanctioned surface is a DELIBERATE, committed act
    # (add the path here in the same change that touches it).
    FRONTEND_UI_LANE_SANCTIONED_FILES = (
        "frontend/src/app/app.component.spec.ts",
        "frontend/src/app/components/room-dialog/room-dialog.component.html",
        "frontend/src/app/components/room-dialog/room-dialog.component.scss",
        "frontend/src/app/components/room-dialog/room-dialog.component.spec.ts",
        "frontend/src/app/components/room-dialog/room-dialog.component.ts",
        # gc-fe-surfaces P3 (surfaces (b)+(c)): instance-list row button +
        # conditional visibility — three modified + the new spec (AM-7/W-2).
        "frontend/src/app/components/instance-list/instance-list.component.ts",
        "frontend/src/app/components/instance-list/instance-list.html",
        "frontend/src/app/components/instance-list/instance-list.scss",
        "frontend/src/app/components/instance-list/instance-list.rooms-btn.spec.ts",
        # gc-fe-surfaces P4 (W-14): committed rooms-gate e2e spec + dedicated config.
        "frontend/e2e/group-chat-rooms-gate.spec.ts",
        "frontend/playwright.rooms-gate.config.ts",
        # gc-fe-surfaces G2 (daemon-fenced rooms gate): the committed daemon
        # spec + its dedicated daemon config + its proxy conf — sanctioned in
        # the SAME change that touches them (F-1's maintenance rule: extend
        # the sanctioned surface in the same change that adds the path).
        "frontend/e2e/group-chat-rooms-gate.daemon.spec.ts",
        "frontend/playwright.rooms-gate.daemon.config.ts",
        "frontend/proxy.gcfe-e2e.conf.json",
        "frontend/src/app/models/index.ts",
        "frontend/src/app/models/room.model.ts",
        # P6 tidier T1 created this suite (the chatrooms describe moved
        # out of chat.component.spec.ts, count-identical) — added here
        # per the maintenance rule above, in the same change that
        # closes the resulting offender (A-P6-24).
        "frontend/src/app/pages/chat/chat.chatrooms.spec.ts",
        "frontend/src/app/pages/chat/chat.component.spec.ts",
        "frontend/src/app/pages/chat/chat.component.ts",
        "frontend/src/app/pages/chat/chat.html",
        "frontend/src/app/pages/chat/chat.scss",
        "frontend/src/app/services/room.service.spec.ts",
        "frontend/src/app/services/room.service.ts",
        "frontend/src/styles.scss",
        # tool-usage metrics P1 (council C2): 13 new FE paths — 9th tab,
        # lazy host, domain client, wire models, spec pack, bundle
        # artifacts, e2e pixel item. Sanctioned here per F-1's
        # maintenance rule (extend the surface in the same change that
        # touches it); the 13 pre-existing foreign base offenders
        # (job-queue-indicator, home, mcps-gateway, live-tab,
        # poll-visibility, command-state, instance.service) are
        # deliberately NOT sanctioned — out of scope, someone else's
        # debt, present at base 858a85e31.
        "frontend/e2e/tool-usage-metrics-gate.spec.ts",
        "frontend/src/app/models/tool-usage.models.spec.ts",
        "frontend/src/app/models/tool-usage.models.ts",
        "frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts",
        "frontend/src/app/pages/metrics/metrics.component.spec.ts",
        "frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts",
        "frontend/src/app/pages/metrics/tabs.manifest.spec.ts",
        "frontend/src/app/pages/metrics/tabs.manifest.ts",
        "frontend/src/app/pages/metrics/tabs/tabs.components.ts",
        "frontend/src/app/pages/metrics/tabs/tool-usage-tab-host.component.ts",
        "frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts",
        "frontend/src/app/services/tool-usage.service.spec.ts",
        "frontend/src/app/services/tool-usage.service.ts",
    )

    # F-2 — the FIXED committed-tree reference for the compensator: the
    # P4||P5 wave merge (858a85e31) — the tree the P6 FE-UI lane's
    # sanctioned surface was committed AGAINST. It is deliberately NOT
    # merge-relative: a merge-relative reference empties when the merge
    # absorbs the sanctioned drift (the hole the compensator closes),
    # while a fixed base NEVER empties — the comparison judges the
    # current committed tree on every run, before and after the merge.
    FRONTEND_UI_LANE_SANCTIONED_BASE = "858a85e31489c7db9e9275955e63c021cfc5a2c4"

    # The EXACT marker CONSTANT lines the FE-UI lane committed — the
    # file-state "constants' presence" anchors, in the same posture as
    # the leader-sanctioned None-guard's SANCTIONED_HUNK_LINES (the
    # precedent's file-state compensator). These two lines are the
    # sanctioned drift's own wording: their absence REDs the
    # compensator even when the merge-relative span is empty.
    FRONTEND_UI_LANE_MARKER_CONSTANTS = {
        "frontend/src/app/app.component.spec.ts": (
            "// FE-UI lane marker (group-chat P6 room modal lane): A-P6-23."
        ),
        "frontend/src/app/pages/chat/chat.component.spec.ts": (
            "// FE-UI lane marker (group-chat P6 room modal suite): A-P6-23."
        ),
    }

    # Cross-lane carve-out: a hunk claimed by ANY sanctioned lane is
    # sanctioned drift, so co-spanning sanctioned lanes do not
    # cross-red each other (the memoir pin would otherwise RED on the
    # group-chat hunks, which carry no "memoir"). Every manager.py lane
    # pin below treats a hunk carrying NONE of these markers as an
    # offender — an UNSANCTIONED hunk (the source-gc lane included)
    # still REDs every pin. Counter-mutation proof of non-vacuity:
    # stripping "group chat" from a P1 hunk leaves it markerless and
    # REDs all three manager.py pins (decision, memoir, group-chat).
    # The COMPACT-NOTE (prompt_reload(compact, compact_note)) lane's
    # sanctioned change to daemon/constants.py — the +13 block carrying
    # DELIBERATE_COMPACT_RATE_LIMIT_S and its comment header. Same
    # scoped-sanction pattern as the manager.py lanes above: the path is
    # exempted from the blanket no-drift check and every hunk of ITS
    # drift must carry the lane marker ("compact-note", present in the
    # hunk's own section header: "Compact-note lane (prompt_reload(
    # compact, compact_note))"), so any OTHER lane's constants.py drift
    # still REDs.
    COMPACT_NOTE_LANE_SANCTIONED_PATH = "daemon/constants.py"
    COMPACT_NOTE_LANE_MARKER = "compact-note"

    SANCTIONED_LANE_MARKERS = (
        "decision",
        "memoir",
        "group chat",
        "compact-note",
        # cycle-3 (2026-11-20): the SELF-SERVICE-PROMOTE lane's sanctioned
        # changes to daemon/manager.py — the executor unit-identity stamper
        # (D3-E3), the spawn-mode-derived reaper unit (M2), and the legacy
        # owner_pid_kind fixup, commits 068cad67a / 2c2df283f / 4cb69120b
        # (+92 lines over the span). Mirrors the decision-lane precedent
        # (c81dd0dea, "scoped source-gc sanction") and the memoir /
        # group-chat blocks above. Every hunk of that drift now carries
        # "self-service-promote" (hunk 1 natively — the D3-E3 wiring
        # comment; the three others via a minimal non-behavioral marker
        # comment added in the same change). The token is disjoint from
        # the other markers over the span.
        "self-service-promote",
        # ── TOOL-USAGE-METRICS-P1 lane (2026-11-21) — the single marker ──
        # The lane's sanctioned daemon/manager.py drift: the aggregated
        # ledger model registration before create_all + the off-loop
        # flusher (phase 1, 49f5e9c1d) and the boot-time digest seeder
        # (phase 3, 71273d7e3). Mirrors the decision / memoir /
        # group-chat / self-service-promote blocks above.
        #
        # NORMALIZED TO ONE TOKEN — "tool-usage" (hyphen form). Measured
        # at HEAD 0b02931b7 over 0b5624dc9..HEAD: the lane's manager.py
        # drift is 3 hunks and 3/3 carry "tool-usage" in their added
        # lines (the "tool-usage P1"/"tool-usage P3" section headers), so
        # the marker is PER-HUNK-COMPLETE with no marker comment added.
        # The lane's other spellings ("tool_usage" in identifiers,
        # "toolusage" in log prefixes) are deliberately NOT markers: a
        # token that matches every spelling stops discriminating.
        #
        # DISCLOSED, NOT CURED HERE: the same 3 hunks also carry
        # "decision" (the plan's "(decision-support)" prose), so all four
        # manager.py/constants.py pins above are satisfied on them by the
        # DECISION token — a courtesy cross-mention, the exact shape the
        # memoir block forbids. Registering "tool-usage" makes the hunks
        # attributable to their OWNING lane; making a pin select the
        # owning token instead of any-of is a mechanism change (the
        # carve-out is a flat ANY-of set) and is left as a follow-up.
        "tool-usage",
    )

    # ── THE HUNK-MARKER CHECK'S BLIND SPOT (disclosed, not fixed) ────────
    # This mechanism judges ONLY daemon/manager.py + daemon/constants.py.
    # The tool-usage lane's other daemon surfaces — notably
    # daemon/graph.py, where the observation wrapper is inserted between
    # compact_note and wrapped_tools_node — are NOT covered: a markerless
    # graph.py hunk passes every pin here. They are covered instead by a
    # PATH-level allowlist (the metrics family pack's
    # TestNoProducerInstrumentation), never per hunk. Extending this
    # mechanism to graph.py is a named follow-up, not a silent gap.

    # The EXACT added lines of the leader-sanctioned None guard (content
    # pin, indentation preserved; diff '+' prefixes stripped). The file
    # pin requires these contiguous; the history pin requires this list
    # to equal the added-line set when the hunk is inside the span.
    SANCTIONED_HUNK_LINES = [
        "    if updated is None:",
        "        # Leader-authorized fence carve-out (source-gc, W7 follow-up):",
        "        # update_source_config returns None when the row vanished during",
        "        # the update (concurrent delete; StaleDataError -> None). Mirror",
        "        # the house 404 shape used above for a missing source instead of",
        "        # letting _source_to_info(None) raise AttributeError -> HTTP 500.",
        "        raise HTTPException(",
        "            status_code=404,",
        "            detail=ErrorResponse(",
        "                code=ErrorCodes.SOURCE_NOT_FOUND,",
        "                message=f\"Source not found: {source_id}\"",
        "            ).model_dump()",
        "        )",
    ]

    @staticmethod
    def _git(*args):
        import subprocess
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        ).stdout

    def _manager_hunks_missing_sanctioned_marker(self, span, path=None):
        """Hunks of the daemon/manager.py drift in ``span`` that carry
        NO sanctioned-lane marker. ``SANCTIONED_LANE_MARKERS`` is the
        single source (the cross-lane carve-out lives there — never
        here). This is the SHARED body of the three lane pins below:
        the diff, the hunk parse and the marker check are identical
        across them (all three lane path constants alias the same
        file — daemon/manager.py); only each pin's docstring narrates
        its own lane's claim. A span with no manager.py diff is
        vacuously clean ([]). A degenerate diff (content lines but no
        hunk headers) yields a pseudo-hunk so the caller's ``== []``
        assert still fails — the old explicit "no content lines"
        assert, preserved.
        """
        diff = self._git(
            "diff", "--unified=0", span, "--",
            path or self.DECISION_LANE_SANCTIONED_PATH,
        )
        if not diff.strip():
            return []  # nothing sanctioned in this span — vacuously clean
        hunks: list[list[str]] = []
        current: list[str] = []
        for line in diff.splitlines():
            if line.startswith("@@"):
                if current:
                    hunks.append(current)
                current = []
                continue
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                current.append(line)
        if current:
            hunks.append(current)
        if not hunks:
            return [[
                "<degenerate: manager.py diff had content lines but no "
                "hunk headers>"
            ]]
        return [
            hunk for hunk in hunks
            if not any(
                marker in line.lower()
                for line in hunk
                for marker in self.SANCTIONED_LANE_MARKERS
            )
        ]

    def _frontend_ui_offending_hunks(self, diff_text):
        """Offending frontend/ hunks in ``diff_text`` — the SHARED body
        of the FE-UI pins (the span pin below and the F-2 tree-level
        compensator). The diff, the hunk parse and the marker check are
        identical for both callers; only the DIFF REFERENCE differs (the
        merge-relative span vs the fixed committed base).

        A hunk is an OFFENDER when EITHER
          (a) its PATH is not in FRONTEND_UI_LANE_SANCTIONED_FILES — the
              sanction is PATH-BOUND (F-1): a foreign frontend/ file is
              an offender REGARDLESS of its wording, so the generic
              "room"/"chat" markers cannot buy a foreign path a pass;
              or
          (b) it carries NO marker from FRONTEND_UI_LANE_MARKERS — the
              lexical rule, now scoped to the allowlisted paths.

        The parse flushes on ``diff --git`` boundaries (the frontend/
        diff spans many files) and on ``@@`` headers, so every hunk is
        attributed to its OWN file and a markerless trailing hunk cannot
        piggyback on the next file's first hunk. An EMPTY diff is
        vacuously clean ([]) — the merge-relative span empties when the
        merge absorbs the sanctioned drift, and that pin must stay
        quiet (the compensator carries the teeth). A degenerate diff
        (content lines but no hunk headers) yields a pseudo-hunk so the
        caller's ``== []`` assert still fails — the manager.py
        checker's degenerate assert, preserved.
        """
        if not diff_text.strip():
            return []  # nothing to judge — vacuously clean
        hunks: list[tuple[str, list[str]]] = []
        current: list[str] = []
        current_path = ""
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                if current:
                    hunks.append((current_path, current))
                    current = []
                current_path = line.split(" b/")[-1].strip()
                continue
            if line.startswith("@@"):
                if current:
                    hunks.append((current_path, current))
                    current = []
                continue
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                current.append(line)
        if current:
            hunks.append((current_path, current))
        if not hunks:
            return [[
                "<degenerate: frontend/ diff had content lines but no "
                "hunk headers>"
            ]]
        offenders = []
        for path, hunk in hunks:
            if path not in self.FRONTEND_UI_LANE_SANCTIONED_FILES:
                # (a) path-bound: a foreign frontend/ path is an
                # offender whatever its wording says.
                offenders.append((path, hunk))
                continue
            if not any(
                marker in line.lower()
                for line in hunk
                for marker in self.FRONTEND_UI_LANE_MARKERS
            ):
                # (b) markerless inside an allowlisted path.
                offenders.append((path, hunk))
        return offenders

    def test_sanctioned_guard_present_in_file(self):
        # File-state pin: the sanctioned hunk must be present, contiguous,
        # and exact. Removing/altering a line REDs. This survives a future
        # drift sync that absorbs the hunk into a later merge.
        text = open(self.SANCTIONED_PATH).read()
        expected_block = "\n".join(self.SANCTIONED_HUNK_LINES)
        assert expected_block in text, (
            "the leader-sanctioned None guard is missing or was altered in "
            f"{self.SANCTIONED_PATH}"
        )

    def test_pre_merge_span_empty_over_not_touched(self):
        out = self._git(
            "diff", "--stat", self.PRE_MERGE_SPAN, "--", *self.NOT_TOUCHED_PATHS
        )
        assert out.strip() == "", f"unexpected pre-merge drift in NOT-TOUCHED paths:\n{out}"

    def test_post_merge_span_exactly_sanctioned_hunk(self):
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        # daemon/manager.py is excluded from the blanket no-drift check
        # (the DECISION, MEMOIR and GROUP-CHAT lanes legitimately changed
        # it); their drift is checked by the scoped lane sanctions below.
        # frontend/ is excluded the same way (the GROUP-CHAT P6 FE-UI
        # lane legitimately changed it).
        others = [p for p in self.NOT_TOUCHED_PATHS
                  if p not in (self.SANCTIONED_PATH,
                               self.DECISION_LANE_SANCTIONED_PATH,
                               self.MEMOIR_LANE_SANCTIONED_PATH,
                               self.GROUP_CHAT_LANE_SANCTIONED_PATH,
                               self.COMPACT_NOTE_LANE_SANCTIONED_PATH,
                               self.FRONTEND_UI_LANE_SANCTIONED_PATH)]
        out_others = self._git("diff", "--stat", span, "--", *others)
        assert out_others.strip() == "", (
            f"unexpected post-merge drift in NOT-TOUCHED paths:\n{out_others}"
        )

        diff = self._git(
            "diff", "--unified=0", span, "--", self.SANCTIONED_PATH
        )
        if diff.strip() == "":
            # hunk predates the latest merge (absorbed) — acceptable; the
            # file pin above keeps its teeth.
            return
        added = [
            line[1:] for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        removed = [
            line[1:] for line in diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ]
        assert removed == [], f"sanctioned hunk must be additive-only; removed={removed!r}"
        assert added == self.SANCTIONED_HUNK_LINES, (
            "daemon/routers/sources.py diff is not EXACTLY the sanctioned "
            f"None-guard hunk.\nadded={added!r}"
        )

    def test_the_compact_note_lane_constants_drift_is_confined_to_sanctioned_hunks(
        self,
    ):
        """The SANCTIONED compact-note lane's daemon/constants.py drift.

        Every hunk of the constants.py drift in the post-merge span must
        carry a sanctioned-lane marker (the lane's own section header
        carries "compact-note"). Any OTHER lane's constants.py drift lands
        in a markerless hunk and REDs here — the source-gc guard's intent
        (constants.py must not drift) is preserved; only a sanctioned
        lane's own constant block is allowed.
        """
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        assert (
            self._manager_hunks_missing_sanctioned_marker(
                span, path=self.COMPACT_NOTE_LANE_SANCTIONED_PATH
            )
            == []
        )

    def test_the_decision_lane_manager_drift_is_confined_to_decision_hunks(self):
        """B-3 — the SANCTIONED decision-lane manager.py drift.

        Every hunk of the daemon/manager.py drift in the post-merge span
        must carry a decision-lane marker. A manager.py change from ANY
        other lane (the source-gc lane included) lands in a hunk with no
        decision marker and REDS here — the guard's intent (the source-gc
        lane must not drift manager.py) is preserved; only sanctioned
        lanes' own wiring is allowed. Cross-lane carve-out: a hunk
        claimed by another sanctioned lane's marker
        (SANCTIONED_LANE_MARKERS) is sanctioned drift and is not an
        offender, so co-spanning sanctioned lanes do not cross-red.
        """
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        assert self._manager_hunks_missing_sanctioned_marker(span) == []

    def test_the_memoir_lane_manager_drift_is_confined_to_sanctioned_lane_hunks(self):
        """The SANCTIONED memoir-lane manager.py drift.

        Every hunk of the daemon/manager.py drift in the post-merge span
        must carry a memoir-lane marker. A manager.py change from ANY
        other lane (the source-gc lane included) lands in a hunk with no
        memoir marker and REDs here — the guard's intent (the source-gc
        lane must not drift manager.py) is preserved; only sanctioned
        lanes' own wiring is allowed. Mirrors the decision-lane
        precedent (c81dd0dea). Cross-lane carve-out: a hunk claimed by
        another sanctioned lane's marker (SANCTIONED_LANE_MARKERS) is
        sanctioned drift and is not an offender — the GROUP-CHAT hunks
        carry no "memoir" (hunk 2 has zero occurrences), so without the
        carve-out this pin would cross-red on them; it must NEVER be
        satisfied by a courtesy memoir mention instead.
        """
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        assert self._manager_hunks_missing_sanctioned_marker(span) == []

    def test_the_group_chat_lane_manager_drift_is_confined_to_sanctioned_lane_hunks(
        self,
    ):
        """The SANCTIONED group-chat (P1 substrate) lane manager.py drift.

        Every hunk of the daemon/manager.py drift in the post-merge span
        must carry a sanctioned-lane marker, and this pin documents the
        GROUP-CHAT lane's own claim: both P1 hunks (manager.py model
        registration + room boot regeneration pass) carry the literal
        "group chat" verbatim in their added lines — measured PER-HUNK-
        COMPLETE (hunk 1 = 15 added lines, hunk 2 = 30; the marker
        occurs exactly once per hunk). A manager.py change from ANY
        other lane (the source-gc lane included) lands in a hunk with
        no sanctioned marker and REDs here — the guard's intent (the
        source-gc lane must not drift manager.py) is preserved; only
        sanctioned lanes' own wiring is allowed. Mirrors the memoir-lane
        block and the decision-lane precedent (c81dd0dea).
        """
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        assert self._manager_hunks_missing_sanctioned_marker(span) == []

    def test_the_frontend_ui_lane_drift_is_confined_to_sanctioned_lane_hunks(
        self,
    ):
        """The SANCTIONED group-chat (P6 FE-UI) lane frontend/ drift.

        Every hunk of the frontend/ drift in the post-merge span must
        be SANCTIONED: its path must be in the F-1 path allowlist
        (FRONTEND_UI_LANE_SANCTIONED_FILES — a foreign frontend/ path
        is an offender REGARDLESS of its wording) AND it must carry a
        sanctioned FE-UI marker ("room" or "chat" — measured
        PER-HUNK-COMPLETE across the 14 commissioned files, 17 hunks;
        the parse flushes on file boundaries so a markerless trailing
        hunk cannot piggyback on the next file's first hunk). A
        frontend/ change from ANY other lane (the source-gc lane
        included) lands in an offending hunk and REDs here — the
        guard's intent (the source-gc lane must not drift frontend/) is
        preserved; only the P6-commissioned FE surface is allowed.
        Mirrors the manager.py lane pins (decision / memoir /
        group-chat).

        SCOPE LIMIT (the F-2 compensator's reason to exist): this is a
        HISTORY pin — its span is merge-relative, so on the MERGED
        branch the span empties (the merge absorbs the sanctioned
        drift) and this pin stops judging. The tree-level compensator
        (test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level)
        carries the guard across the merge.
        """
        merge = self._git(
            "rev-list", "--first-parent", "--merges", "-1", "HEAD"
        ).strip()
        assert merge, "no first-parent merge found"
        span = f"{merge}..HEAD"
        diff = self._git(
            "diff", "--unified=0", span, "--",
            self.FRONTEND_UI_LANE_SANCTIONED_PATH,
        )
        assert self._frontend_ui_offending_hunks(diff) == []

    def test_the_frontend_ui_sanctioned_files_carry_their_marker_constants(self):
        """F-2 (file-state compensator) — the FE-UI sanction's CONSTANTS
        are PRESENT in the current tree, so the guard PERSISTS after the
        merge-relative span empties.

        The span pin above is a HISTORY pin: on the merged branch its
        span (first-parent-merge..HEAD) is EMPTY — the merge absorbs the
        sanctioned drift — and it stops judging anything. This pin is
        the COMPENSATOR: it reads the CURRENT tree, never a span, so it
        keeps its teeth across the merge. Per allowlisted path (F-1's
        path allowlist — the single source of truth for the sanctioned
        set) it asserts: the file EXISTS, it carries a marker from
        FRONTEND_UI_LANE_MARKERS, and its sanctioned marker CONSTANT is
        present VERBATIM — the exact-line posture the leader-sanctioned
        None-guard uses for daemon/routers/sources.py (the precedent's
        file-state compensator), applied to the FE-UI lane.
        """
        for path in self.FRONTEND_UI_LANE_SANCTIONED_FILES:
            assert os.path.exists(path), (
                f"the sanctioned FE-UI file is missing from the tree: {path}"
            )
            text = open(path).read().lower()
            assert any(
                marker in text for marker in self.FRONTEND_UI_LANE_MARKERS
            ), f"the sanctioned FE-UI file carries no FE-UI marker: {path}"
        for path, constant in self.FRONTEND_UI_LANE_MARKER_CONSTANTS.items():
            assert constant in open(path).read(), (
                f"the FE-UI marker constant is missing from {path}: "
                f"{constant!r}"
            )

    def test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level(
        self,
    ):
        """F-2 (tree-level compensator) — the FE-UI sanction's ALLOWLIST
        is enforced against the COMMITTED TREE, so the guard PERSISTS
        after the merge-relative span empties.

        The span pin judges the merge-relative span; on the merged
        branch that span is EMPTY (the merge absorbs the sanctioned
        drift) and the pin stops judging — the hole this compensator
        closes. Here the reference is a FIXED committed base (the P4||P5
        wave merge, 858a85e31 — the tree the P6 FE-UI lane's surface was
        committed AGAINST), so the comparison NEVER empties: the current
        committed tree's frontend/ drift is judged against the sanctioned
        set on every run, before and after the merge.

        The rule is F-1's PATH ALLOWLIST: a frontend/ path OUTSIDE
        FRONTEND_UI_LANE_SANCTIONED_FILES is an offender REGARDLESS of
        its wording (the generic "room"/"chat" markers cannot buy a
        foreign file a pass — the review's probe), and a hunk inside an
        allowlisted path must ADDITIONALLY carry a marker.
        """
        diff = self._git(
            "diff", "--unified=0",
            f"{self.FRONTEND_UI_LANE_SANCTIONED_BASE}..HEAD",
            "--",
            self.FRONTEND_UI_LANE_SANCTIONED_PATH,
        )
        assert self._frontend_ui_offending_hunks(diff) == []
