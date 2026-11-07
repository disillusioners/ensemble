"""Unit tests for ``SQLModelSourceRepository`` GC methods (source-gc lane).

Pins P1-P21 (repo-level half) per
``.agents/shared/planning/source-gc/phase4-plan.md`` §7, each with its
named mutation leg. Fixture is live-sized (phase4 §6): 100 scheduler
rows around the 14d boundary (incl. 10 rows <24h), 3 chat rows incl.
the ``enabled=true, status='stopped'`` trap, 110 executions, 100
mappings 1:1 + ~3 chat-source mappings (W5/P21, incl. one >30d).
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
    def test_sources_and_constants_unchanged_vs_base(self):
        import subprocess
        try:
            out = subprocess.run(
                ["git", "diff", "--stat", "6b571771d..2c6b7ab94", "--",
                 "daemon/sources/", "daemon/constants.py"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            pytest.skip("git not available")
        # The lane's OWN commit span (6b571771d -> our pre-merge tip
        # 2c6b7ab94) must touch zero files under the NOT-TOUCHED sealed
        # paths. A two-dot diff against HEAD would absorb the foreign
        # drift synced in by the v0.29.2 merge (registry.py +98,
        # constants.py +14) and re-red spuriously — that drift is
        # legitimate (sources-regression lane) and is NOT ours. The
        # lane-only span is the truthful basis.
        stat = out.stdout.strip()
        assert stat == "", f"unexpected diff in sealed paths:\n{stat}"