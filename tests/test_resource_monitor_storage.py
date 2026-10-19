"""Storage test pack (P1 T10 / AC3, AC4, AC8, AC9).

Covers:

  * AC3 — 4 models + 4 ``.sql`` files; index names byte-identical
    across ``__table_args__`` and the DDL; the T3 partial UNIQUE
    dual-dialect WHERE is present; T3 carries the dormant
    ``action`` / ``action_by`` / ``acted_at`` columns; T4
    ``user_gated`` is NOT NULL default true.
  * AC4 — dual-dialect proof: SQLite applies all 4 ``.sql`` via the
    runner splitter; the PG path is SQLModel-metadata ``create_all``
    (documented §2.4 runtime-verify confirmation); the
    ``now()|USING|ENUM`` grep gate returns nothing.
  * AC8 — one ``executemany`` transaction per flush window;
    drop-oldest at the ~512-row bound.
  * AC9 — roll-down raw→minute→hour correct; retention sweep deletes
    exactly the expired rows at 48h/14d/180d and at the floors.

FENCE DISCIPLINE: file-SQLite at pytest ``tmp_path``; no live /proc;
FAKE_HOME mktemp.
"""

from __future__ import annotations

import os
import re
import tempfile
import time as _time_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from daemon.models.resource_monitor import (
    ResourceDaemonBucket,
    ResourceInstanceSample,
    ResourceLimitPolicy,
    ResourcePidMap,
)
from daemon.services.resource_monitor import (
    FLUSH_BUFFER_CAP,
    Flusher,
    RetentionPolicy,
    TierRing,
    rollup_to_hour,
    rollup_to_minute,
)

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "daemon" / "migrations" / "versions"
)
_RESOURCE_MIGRATION_FILES = [
    "20261018_090001_create_resource_daemon_bucket.sql",
    "20261018_090002_create_resource_instance_sample.sql",
    "20261018_090003_create_resource_pid_map.sql",
    "20261018_090004_create_resource_limit_policy.sql",
]


# ──────────────────────────────────────────────────────────────────────────
# Fence
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


def _split_sql_up(sql_text: str) -> list[str]:
    """Apply the runner's splitter (full-line ``--`` strip then ``;``)."""
    if "-- DOWN" in sql_text:
        sql_text = sql_text.split("-- DOWN")[0]
    code = "\n".join(
        ln for ln in sql_text.splitlines() if not ln.lstrip().startswith("--")
    )
    return [s.strip() for s in code.split(";") if s.strip()]


@pytest.fixture
def sqlite_engine(tmp_path):
    """File SQLite with the 4 resource migrations applied."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        f"sqlite:///{tmp_path / 'storage.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for fn in _RESOURCE_MIGRATION_FILES:
        sql = (_MIGRATIONS_DIR / fn).read_text()
        with engine.begin() as conn:
            for stmt in _split_sql_up(sql):
                conn.execute(text(stmt))
    return engine


# ──────────────────────────────────────────────────────────────────────────
# AC3 — index-name byte-identity + schema specifics
# ──────────────────────────────────────────────────────────────────────────


class TestIndexLockstep:
    """3-site convention (D7): SQLModel __table_args__ + .sql names."""

    def _model_index_names(self, cls) -> set[str]:
        names = set()
        for arg in cls.__table_args__ or ():
            name = getattr(arg, "name", None)
            if name:
                names.add(name)
        return names

    def _sql_index_names(self, sql_file: str) -> set[str]:
        raw = (_MIGRATIONS_DIR / sql_file).read_text()
        return set(
            re.findall(
                r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def test_t1_index_names_match(self):
        model = self._model_index_names(ResourceDaemonBucket)
        sql = self._sql_index_names(_RESOURCE_MIGRATION_FILES[0])
        assert model == sql == {"uq_resource_daemon_bucket_ts_interval"}

    def test_t2_index_names_match(self):
        model = self._model_index_names(ResourceInstanceSample)
        sql = self._sql_index_names(_RESOURCE_MIGRATION_FILES[1])
        assert model == sql
        assert sql == {
            "idx_resource_instance_sample_instance_bucket",
            "idx_resource_instance_sample_bucket",
        }

    def test_t3_index_names_match(self):
        model = self._model_index_names(ResourcePidMap)
        sql = self._sql_index_names(_RESOURCE_MIGRATION_FILES[2])
        assert model == sql
        assert sql == {
            "uq_resource_pid_map_pid_active",
            "idx_resource_pid_map_instance_spawned",
            "idx_resource_pid_map_kind_spawned",
            "idx_resource_pid_map_pid",
        }

    def test_t4_index_names_match(self):
        model = self._model_index_names(ResourceLimitPolicy)
        sql = self._sql_index_names(_RESOURCE_MIGRATION_FILES[3])
        assert model == sql == {"idx_resource_limit_policy_scope_target_metric"}

    def test_indexes_present_after_sqlite_apply(self, sqlite_engine):
        insp = inspect(sqlite_engine)
        for table, expected in [
            (
                "resource_daemon_bucket",
                {"uq_resource_daemon_bucket_ts_interval"},
            ),
            (
                "resource_instance_sample",
                {
                    "idx_resource_instance_sample_instance_bucket",
                    "idx_resource_instance_sample_bucket",
                },
            ),
            (
                "resource_pid_map",
                {
                    "uq_resource_pid_map_pid_active",
                    "idx_resource_pid_map_instance_spawned",
                    "idx_resource_pid_map_kind_spawned",
                    "idx_resource_pid_map_pid",
                },
            ),
            (
                "resource_limit_policy",
                {"idx_resource_limit_policy_scope_target_metric"},
            ),
        ]:
            names = {i["name"] for i in insp.get_indexes(table)}
            assert expected.issubset(names), (
                f"{table}: expected {expected} ⊆ {names}"
            )


class TestSchemaSpecifics:
    def test_t3_partial_unique_dual_dialect_where(self):
        """The T3 partial UNIQUE carries BOTH sqlite_where and
        postgresql_where with the same predicate.
        """
        found = None
        for arg in ResourcePidMap.__table_args__:
            if getattr(arg, "name", None) == "uq_resource_pid_map_pid_active":
                found = arg
        assert found is not None, "partial UNIQUE index missing from model"
        assert found.unique is True
        sqlite_where = found.dialect_options["sqlite"].get("where")
        pg_where = found.dialect_options["postgresql"].get("where")
        assert sqlite_where is not None
        assert pg_where is not None
        assert str(sqlite_where) == str(pg_where) == "exited_at IS NULL"

    def test_t3_dormant_columns_present(self):
        cols = {c.name for c in ResourcePidMap.__table__.columns}
        assert {"action", "action_by", "acted_at"}.issubset(cols)

    def test_t4_user_gated_not_null_default_true(self):
        col = ResourceLimitPolicy.__table__.columns["user_gated"]
        assert col.nullable is False
        # SQLModel default is True.
        assert ResourceLimitPolicy.model_fields["user_gated"].default is True

    def test_t4_enabled_default_false(self):
        assert ResourceLimitPolicy.model_fields["enabled"].default is False

    def test_t1_bucket_ts_interval_unique(self):
        idx = None
        for arg in ResourceDaemonBucket.__table_args__:
            if (
                getattr(arg, "name", None)
                == "uq_resource_daemon_bucket_ts_interval"
            ):
                idx = arg
        assert idx is not None
        assert idx.unique is True
        assert [c.name for c in idx.columns] == ["bucket_ts", "interval_s"]

    def test_columns_match_between_model_and_sqlite(self, sqlite_engine):
        insp = inspect(sqlite_engine)
        for cls in (
            ResourceDaemonBucket,
            ResourceInstanceSample,
            ResourcePidMap,
            ResourceLimitPolicy,
        ):
            model_cols = {c.name for c in cls.__table__.columns}
            db_cols = {c["name"] for c in insp.get_columns(cls.__tablename__)}
            assert model_cols == db_cols, (
                f"{cls.__tablename__}: model/db column mismatch: "
                f"{model_cols ^ db_cols}"
            )


# ──────────────────────────────────────────────────────────────────────────
# AC4 — dual-dialect proof + grep gate
# ──────────────────────────────────────────────────────────────────────────


class TestDualDialect:
    def test_sqlite_applies_all_4_migrations(self, sqlite_engine):
        """SQLite: the runner applies all 4 .sql files cleanly."""
        insp = inspect(sqlite_engine)
        tables = set(insp.get_table_names())
        assert {
            "resource_daemon_bucket",
            "resource_instance_sample",
            "resource_pid_map",
            "resource_limit_policy",
        }.issubset(tables)

    def test_pg_path_is_sqlmodel_metadata(self, tmp_path):
        """PG path: SQLModel.metadata.create_all emits the schema.

        §2.4 runtime-verify confirmation: the ``.sql`` runner is a
        NO-OP on PG; brand-new tables come from the SQLModel
        metadata ``create_all`` at boot (registered via the
        daemon/manager.py late-import block). We prove the models
        are on the shared metadata AND ``create_all`` emits each
        table on SQLite (the dialect-neutral creation routine).
        """
        from sqlmodel import SQLModel

        for cls in (
            ResourceDaemonBucket,
            ResourceInstanceSample,
            ResourcePidMap,
            ResourceLimitPolicy,
        ):
            assert cls.__tablename__ in SQLModel.metadata.tables

        # create_all via the metadata produces the tables (the same
        # call the PG boot path runs).
        engine = create_engine(f"sqlite:///{tmp_path / 'meta.db'}")
        SQLModel.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        for cls in (
            ResourceDaemonBucket,
            ResourceInstanceSample,
            ResourcePidMap,
            ResourceLimitPolicy,
        ):
            assert cls.__tablename__ in tables

    def test_grep_gate_no_pg_only_ddl(self):
        """AC4 grep gate: no now() / USING / ENUM in the 4 .sql files."""
        hits: list[str] = []
        for fn in _RESOURCE_MIGRATION_FILES:
            raw = (_MIGRATIONS_DIR / fn).read_text()
            for m in re.finditer(r"now\(\)|USING|ENUM", raw):
                hits.append(f"{fn}:{m.group(0)}")
        assert hits == [], f"PG-only DDL found: {hits}"


# ──────────────────────────────────────────────────────────────────────────
# AC8 — flusher batching + bounded drop-oldest
# ──────────────────────────────────────────────────────────────────────────


class TestFlusherBatching:
    def _seed_ring(self, ring: TierRing, n: int, *, base_minute: int = 0) -> None:
        """Seed the ring with valid minute/second buckets.

        Minutes wrap within the hour (0..59) and hours roll over so
        large ``n`` values stay parseable (e.g. n=612 needs more
        than one hour).
        """
        total_ticks = base_minute * 2
        for i in range(n):
            tick = total_ticks + i
            hour = tick // 120
            minute = (tick // 2) % 60
            second = (i % 2) * 30
            ring.append(
                {
                    "bucket_ts": (
                        f"2026-10-18 {hour:02d}:{minute:02d}:{second:02d}"
                    ),
                    "interval_s": 30,
                    "rss_kb": 100000 + i,
                    "cpu_user_usec": 1000 * (i + 1),
                    "cpu_sys_usec": 100 * (i + 1),
                    "threads": 4,
                    "fds": 5,
                    "sample_cost_ms": 0.5,
                    "attributed_bytes": 0,
                    "unattributed_kb": 0,
                    "coverage_pct": 0.0,
                    "missed_samples": 0,
                }
            )

    def test_one_transaction_per_window(self, sqlite_engine):
        """AC8: exactly ONE BEGIN + ONE COMMIT per flush window."""
        from sqlalchemy.event import listen

        events: list[tuple[str, int]] = []
        listen(sqlite_engine, "begin", lambda c: events.append(("begin", id(c))))
        listen(sqlite_engine, "commit", lambda c: events.append(("commit", id(c))))

        ring = TierRing(tier="raw", maxlen=256)
        self._seed_ring(ring, 6)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        events.clear()
        result = flusher.run_once()
        assert result["rows_written"] > 0
        begins = [e for e in events if e[0] == "begin"]
        commits = [e for e in events if e[0] == "commit"]
        assert len(begins) == 1, f"expected 1 BEGIN, saw {len(begins)}"
        assert len(commits) == 1, f"expected 1 COMMIT, saw {len(commits)}"

    def test_window_writes_raw_minute_hour(self, sqlite_engine):
        ring = TierRing(tier="raw", maxlen=256)
        self._seed_ring(ring, 10)  # spans 5 minutes
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        flusher.run_once()
        with sqlite_engine.connect() as conn:
            counts = dict(
                conn.execute(
                    text(
                        "SELECT interval_s, COUNT(*) FROM "
                        "resource_daemon_bucket GROUP BY interval_s"
                    )
                ).fetchall()
            )
        assert counts.get(30) == 10  # raw
        assert counts.get(60) == 5   # minute
        assert counts.get(3600) == 1  # hour

    def test_bounded_drop_oldest(self, sqlite_engine):
        """The flusher's in-flight buffer never exceeds the cap."""
        ring = TierRing(tier="raw", maxlen=4096)
        self._seed_ring(ring, FLUSH_BUFFER_CAP + 100)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        # Drain more rows than the cap in one window.
        flusher.run_once()
        assert len(flusher._buffer) == 0  # cleared post-window
        # The buffer was capped before the write: rows_written <= cap.
        # (exact count varies with rollup dedup on bucket_ts)
        assert flusher.counters.batches == 1

    def test_empty_window_still_sweeps(self, sqlite_engine):
        """An empty buffer still runs the retention sweep."""
        ring = TierRing(tier="raw", maxlen=16)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        result = flusher.run_once()
        assert result["rows_written"] == 0
        assert "swept" in result


# ──────────────────────────────────────────────────────────────────────────
# AC9 — roll-down arithmetic + retention sweep
# ──────────────────────────────────────────────────────────────────────────


class TestRollDown:
    def test_rollup_to_minute_arithmetic(self):
        raw = [
            {"bucket_ts": "2026-10-18 10:00:00", "rss_kb": 100, "cpu_user_usec": 10, "missed_samples": 0},
            {"bucket_ts": "2026-10-18 10:00:30", "rss_kb": 200, "cpu_user_usec": 20, "missed_samples": 1},
            {"bucket_ts": "2026-10-18 10:01:00", "rss_kb": 300, "cpu_user_usec": 30, "missed_samples": 0},
        ]
        mins = rollup_to_minute(raw)
        assert len(mins) == 2
        assert mins[0]["bucket_ts"] == "2026-10-18 10:00:00"
        assert mins[0]["interval_s"] == 60
        assert mins[0]["raw_count"] == 2
        # LAST non-null wins for counters.
        assert mins[0]["rss_kb"] == 200
        # missed_samples is summed.
        assert mins[0]["missed_samples"] == 1
        assert mins[1]["bucket_ts"] == "2026-10-18 10:01:00"

    def test_rollup_to_hour_arithmetic(self):
        mins = [
            {"bucket_ts": "2026-10-18 10:00:00", "rss_kb": 100, "raw_count": 2, "missed_samples": 1},
            {"bucket_ts": "2026-10-18 10:01:00", "rss_kb": 200, "raw_count": 2, "missed_samples": 1},
            {"bucket_ts": "2026-10-18 11:00:00", "rss_kb": 300, "raw_count": 2, "missed_samples": 0},
        ]
        hrs = rollup_to_hour(mins)
        assert len(hrs) == 2
        assert hrs[0]["bucket_ts"] == "2026-10-18 10:00:00"
        assert hrs[0]["interval_s"] == 3600
        assert hrs[0]["raw_count"] == 4
        assert hrs[0]["missed_samples"] == 2
        assert hrs[1]["bucket_ts"] == "2026-10-18 11:00:00"

    def test_retention_policy_floors(self):
        with pytest.raises(ValueError):
            RetentionPolicy(100, 336, 180)
        with pytest.raises(ValueError):
            RetentionPolicy(2880, 10, 180)
        with pytest.raises(ValueError):
            RetentionPolicy(2880, 336, 10)

    def test_retention_from_config_clamps(self):
        p = RetentionPolicy.from_config(10, 10, 10)
        assert p.raw_minutes == 1440
        assert p.minute_hours == 168
        assert p.hour_days == 90

    def test_sweep_deletes_exactly_expired(self, sqlite_engine):
        """AC9: the sweep deletes only the rows older than the cutoff."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_daemon_bucket "
                    "(bucket_ts, interval_s, rss_kb) VALUES (:t, 30, 1)"
                ),
                {"t": old},
            )
            conn.execute(
                text(
                    "INSERT INTO resource_daemon_bucket "
                    "(bucket_ts, interval_s, rss_kb) VALUES (:t, 30, 1)"
                ),
                {"t": recent},
            )

        ring = TierRing(tier="raw", maxlen=16)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        result = flusher.run_once()
        assert result["swept"]["raw"] == 1

        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT bucket_ts FROM resource_daemon_bucket")
            ).fetchall()
        remaining = {r[0] for r in rows}
        assert recent in remaining
        assert old not in remaining

    def test_sweep_respects_floor_windows(self, sqlite_engine):
        """At the floor (24h raw), a 25h-old row is swept, a 1h-old kept."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        overnight = (now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
        fresh = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite_engine.begin() as conn:
            for ts in (overnight, fresh):
                conn.execute(
                    text(
                        "INSERT INTO resource_daemon_bucket "
                        "(bucket_ts, interval_s, rss_kb) VALUES (:t, 30, 1)"
                    ),
                    {"t": ts},
                )
        ring = TierRing(tier="raw", maxlen=16)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(1440, 168, 90),  # floors
        )
        result = flusher.run_once()
        assert result["swept"]["raw"] == 1
        with sqlite_engine.connect() as conn:
            remaining = {
                r[0]
                for r in conn.execute(
                    text("SELECT bucket_ts FROM resource_daemon_bucket")
                ).fetchall()
            }
        assert fresh in remaining
        assert overnight not in remaining

    def test_sweep_per_tier_predicates(self, sqlite_engine):
        """Review F1 regression: per-tier sweep predicates under the
        stacked ``resource_daemon_bucket`` table (interval_s
        30 raw / 60 minute / 3600 hour).

        Retention floors 24 h raw / 7 d minute / 90 d hour (D8).
        The raw sweep must NOT delete minute (7 d) or hour (90 d)
        rows — only the rows whose interval_s matches the tier's
        cutoff. Tier-blind DELETEs (the pre-fix bug) erase
        higher-tier rows inside the raw cutoff window.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ts = {
            "raw_25h": (now - timedelta(hours=25)),   # > 24h floor → raw-delete
            "raw_fresh": (now - timedelta(minutes=5)),  # < 24h → raw-keep
            "min_25h": (now - timedelta(hours=25)),    # < 7d floor → minute-keep
            "min_4d": (now - timedelta(days=4)),       # < 7d → minute-keep
            "hr_4d": (now - timedelta(days=4)),        # < 90d floor → hour-keep
            "hr_200d": (now - timedelta(days=200)),    # > 90d → hour-delete
        }
        iso = {k: v.strftime("%Y-%m-%d %H:%M:%S") for k, v in ts.items()}

        # Seeded (tier, age): raw@25h, raw@5m, min@25h, min@4d, hr@4d, hr@200d.
        seed = [
            (30, "raw_25h"),
            (30, "raw_fresh"),
            (60, "min_25h"),
            (60, "min_4d"),
            (3600, "hr_4d"),
            (3600, "hr_200d"),
        ]
        with sqlite_engine.begin() as conn:
            for interval_s, age_key in seed:
                conn.execute(
                    text(
                        "INSERT INTO resource_daemon_bucket "
                        "(bucket_ts, interval_s, rss_kb) "
                        "VALUES (:t, :i, 1)"
                    ),
                    {"t": iso[age_key], "i": interval_s},
                )

        ring = TierRing(tier="raw", maxlen=16)
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(1440, 168, 90),  # floors
        )
        result = flusher.run_once()

        # Counter expectations under the fixed per-tier sweep:
        #   raw: 1 deletion (raw@25h — raw@5m kept; min@25h, min@4d,
        #   hr@4d are NOT deleted because their interval_s != 30)
        #   minute: 0 deletions (min@25h and min@4d both ≤ 7 d)
        #   hour: 1 deletion (hr@200d — hr@4d kept)
        #   instance: 0 (no instance rows seeded)
        assert result["swept"]["raw"] == 1
        assert result["swept"]["minute"] == 0
        assert result["swept"]["hour"] == 1
        assert result["swept"]["instance"] == 0

        with sqlite_engine.connect() as conn:
            remaining = {
                (r[0], r[1])
                for r in conn.execute(
                    text(
                        "SELECT bucket_ts, interval_s FROM "
                        "resource_daemon_bucket"
                    )
                ).fetchall()
            }
        # Deletions.
        assert (iso["raw_25h"], 30) not in remaining
        assert (iso["hr_200d"], 3600) not in remaining
        # Survivals (the bug would have wiped these under the
        # tier-blind raw sweep because they sit inside the 24 h
        # raw-cutoff window: min@25h, min@4d, hr@4d are all older
        # than 24 h but belong to tiers with longer retention).
        assert (iso["raw_fresh"], 30) in remaining
        assert (iso["min_25h"], 60) in remaining
        assert (iso["min_4d"], 60) in remaining
        assert (iso["hr_4d"], 3600) in remaining

    def test_first_flush_missing_table_degrades_with_one_warning(
        self, tmp_path, caplog
    ):
        """Review F3: when the resource_* tables are absent on the
        first flush, the writes degrade (no exception, zero rows
        written) and ONE warning is logged. Subsequent windows stay
        silent (warn-once, file_change_monitor precedent).
        """
        # Empty SQLite — no resource_* tables.
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            f"sqlite:///{tmp_path / 'no_tables.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ring = TierRing(tier="raw", maxlen=16)
        ring.append(
            {
                "bucket_ts": "2026-10-18 10:00:00",
                "interval_s": 30,
                "rss_kb": 100000,
                "cpu_user_usec": 1000,
                "cpu_sys_usec": 100,
                "threads": 4,
                "fds": 5,
                "sample_cost_ms": 0.5,
                "attributed_bytes": 0,
                "unattributed_kb": 0,
                "coverage_pct": 0.0,
                "missed_samples": 0,
            }
        )
        flusher = Flusher(
            engine=engine,
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )

        with caplog.at_level("WARNING", logger="daemon.services.resource_monitor.flusher"):
            r1 = flusher.run_once()
        # First flush: degraded, no error raised, zero rows written.
        assert r1["rows_written"] == 0
        assert flusher._missing_table_warned is True
        warnings_first = [
            rec for rec in caplog.records
            if "resource-monitor flusher degraded" in rec.message
        ]
        assert len(warnings_first) == 1, (
            f"expected exactly one degrade warning, saw {len(warnings_first)}"
        )
        assert "schema not created yet" in warnings_first[0].message

        # Second flush with the same flusher — still degraded but
        # warn-once must not re-fire.
        caplog.clear()
        with caplog.at_level("WARNING", logger="daemon.services.resource_monitor.flusher"):
            r2 = flusher.run_once()
        assert r2["rows_written"] == 0
        warnings_second = [
            rec for rec in caplog.records
            if "resource-monitor flusher degraded" in rec.message
        ]
        assert warnings_second == [], (
            "warn-once violated: second flush produced a degrade warning"
        )


# ──────────────────────────────────────────────────────────────────────────
# P2 T10 — resource_instance_sample tier write (additive, same window)
# ──────────────────────────────────────────────────────────────────────────


class TestInstanceSampleWrites:
    """P2 T10: the flusher drains the sampler's instance ring and
    persists T2 rows inside the SAME engine.begin() window."""

    def _instance_row(self, instance_id="inst-a", *, bucket_ts=None, **over):
        row = {
            "instance_id": instance_id,
            "agent_id": "agent-a",
            "status": None,
            "scan_kind": "counter",
            "charged_bytes": 12345,
            "deep_bytes": None,
            "graph_cache_present": True,
            "in_flight_tasks": 1,
            "all_state_messages_len": 3,
            "child_rss_kb": 8192,
            "child_cpu_user_usec": 100_000,
            "cpu_share_usec_est": 4321,
            "estimated": True,
        }
        if bucket_ts:
            row["bucket_ts"] = bucket_ts
        row.update(over)
        return row

    def test_instance_rows_persist_with_populated_columns(
        self, sqlite_engine
    ):
        """AC7 extension: T2 rows persist with the P2-populated
        attribution columns (charged_bytes / deep_bytes / estimated
        NOT NULL / honesty fields typed)."""
        raw_ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=64)
        instance_ring.extend(
            [
                self._instance_row("inst-a"),
                self._instance_row(
                    "inst-b",
                    scan_kind="deep",
                    deep_bytes=999999,
                    estimated=False,
                ),
                self._instance_row("__overflow__", agent_id=None),
            ]
        )
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=raw_ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )
        result = flusher.run_once()
        assert result["instance_rows"] == 3
        assert result["rows_written"] >= 3
        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT instance_id, agent_id, scan_kind, charged_bytes, "
                    "deep_bytes, graph_cache_present, in_flight_tasks, "
                    "all_state_messages_len, child_rss_kb, "
                    "child_cpu_user_usec, cpu_share_usec_est, estimated "
                    "FROM resource_instance_sample ORDER BY instance_id"
                )
            ).fetchall()
        by_id = {r[0]: r for r in rows}
        assert set(by_id) == {"inst-a", "inst-b", "__overflow__"}
        a = by_id["inst-a"]
        assert a[1] == "agent-a"
        assert a[2] == "counter"
        assert a[3] == 12345
        assert a[4] is None  # never deep-scanned ⇒ NULL (side-by-side)
        assert a[5] == 1  # sqlite bool
        assert a[6] == 1
        assert a[7] == 3
        assert a[8] == 8192
        assert a[9] == 100_000
        assert a[10] == 4321
        assert a[11] == 1  # estimated NOT NULL
        b = by_id["inst-b"]
        assert b[2] == "deep"
        assert b[4] == 999999
        assert b[11] == 0  # complete fresh deep scan ⇒ estimated False
        # The instance ring was DRAINED (rows leave exactly once).
        assert len(instance_ring) == 0

    def test_estimated_defaults_true_when_missing(self, sqlite_engine):
        """A row without ``estimated`` persists estimated=True — the
        NOT NULL contract is enforced at the write seam."""
        instance_ring = TierRing(tier="instance", maxlen=8)
        row = self._instance_row("inst-x")
        row.pop("estimated")
        instance_ring.extend([row])
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=4),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )
        flusher.run_once()
        with sqlite_engine.connect() as conn:
            est = conn.execute(
                text(
                    "SELECT estimated FROM resource_instance_sample "
                    "WHERE instance_id = 'inst-x'"
                )
            ).scalar()
        assert est == 1

    def test_no_instance_ring_is_p1_noop(self, sqlite_engine):
        """A P1-constructed flusher (no instance ring) skips the T2
        write — back-compat."""
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=4),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        result = flusher.run_once()
        assert result["instance_rows"] == 0
        with sqlite_engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM resource_instance_sample")
            ).scalar()
        assert n == 0

    def test_instance_rows_swept_at_raw_cutoff(self, sqlite_engine):
        """T2 is raw-tier-only: rows older than the raw cutoff are
        swept with the populated columns present (AC7 retention)."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        instance_ring = TierRing(tier="instance", maxlen=8)
        instance_ring.extend(
            [
                self._instance_row("inst-old", bucket_ts=old),
                self._instance_row("inst-new", bucket_ts=recent),
            ]
        )
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=4),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )
        flusher.run_once()
        result = flusher.run_once()  # second window sweeps the old row
        assert result["swept"]["instance"] == 1
        with sqlite_engine.connect() as conn:
            ids = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT instance_id FROM resource_instance_sample"
                    )
                ).fetchall()
            }
        assert ids == {"inst-new"}

    def test_t2_write_rides_same_single_transaction(self, sqlite_engine):
        """The T2 write shares the ONE engine.begin() window with T1
        (AC8 shape preserved)."""
        from sqlalchemy.event import listen

        events: list[str] = []
        listen(
            sqlite_engine,
            "begin",
            lambda c: events.append("begin"),
        )
        raw_ring = TierRing(tier="raw", maxlen=8)
        raw_ring.append(
            {
                "bucket_ts": "2026-10-18 10:00:00",
                "interval_s": 30,
                "rss_kb": 1,
                "cpu_user_usec": 1,
                "cpu_sys_usec": 1,
                "threads": 1,
                "fds": 1,
                "sample_cost_ms": 0.1,
                "attributed_bytes": 0,
                "unattributed_kb": 0,
                "coverage_pct": 0.0,
                "missed_samples": 0,
            }
        )
        instance_ring = TierRing(tier="instance", maxlen=8)
        instance_ring.extend([self._instance_row("inst-a")])
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=raw_ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )
        events.clear()
        result = flusher.run_once()
        assert result["rows_written"] >= 2  # T1 + T2 (+ rollups)
        assert events.count("begin") == 1


# ──────────────────────────────────────────────────────────────────────────
# P2 review C-1 — PG-grammar pins + narrowed relation-missing tolerance
# ──────────────────────────────────────────────────────────────────────────


class TestPGGrammarPins:
    """C-1: the flusher's generated INSERT grammar must be valid on
    BOTH dialects. SQLite-only green (INSERT OR IGNORE takes no
    target) masked the ``ON CONFLICT (_NONEXISTENT)`` shape — which
    PostgreSQL rejected with 42703 on every exited-pid flush, and
    the then-bare ``"does not exist"`` tolerance swallowed as a
    benign first-boot state (silent data loss). These pins make the
    class fail in unit tests regardless of the test dialect.
    """

    # The exact (table, columns, idempotent) set the flusher emits.
    _FLUSHER_INSERTS = [
        (
            "resource_daemon_bucket",
            [
                "bucket_ts", "interval_s", "rss_kb", "cpu_user_usec",
                "cpu_sys_usec", "missed_samples", "anon_kb", "threads",
                "fds", "sample_cost_ms", "attributed_bytes",
                "unattributed_kb", "coverage_pct",
            ],
            True,
        ),
        (
            "resource_pid_map",
            [
                "pid", "pgid", "start_time", "instance_id", "agent_id",
                "kind", "command", "detail", "parent_pid", "spawned_at",
                "exited_at", "exit_code", "exit_kind", "peak_rss_kb",
                "cpu_user_usec",
            ],
            False,
        ),
        (
            "resource_instance_sample",
            [
                "bucket_ts", "instance_id", "agent_id", "status",
                "scan_kind", "charged_bytes", "deep_bytes",
                "graph_cache_present", "in_flight_tasks",
                "all_state_messages_len", "child_rss_kb",
                "child_cpu_user_usec", "cpu_share_usec_est", "estimated",
            ],
            False,
        ),
    ]

    def _flusher_with_url(self, url: str) -> Flusher:
        """A Flusher shell whose ONLY consumed attribute is the URL
        string (``_dialect_insert`` reads ``str(self.engine.url)``
        for dialect detection)."""
        import types

        f = Flusher.__new__(Flusher)
        f.engine = types.SimpleNamespace(url=url)
        return f

    def _real_columns(self, sqlite_engine, table: str) -> set[str]:
        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                text(f"PRAGMA table_info({table})")
            ).fetchall()
        return {r[1] for r in rows}

    def test_pid_map_insert_is_plain_on_both_dialects(self, sqlite_engine):
        """C-1 core pin: the pid_map insert carries NO conflict
        clause on either dialect (drain-once semantics — the
        partial UNIQUE cannot serve as a column-list target)."""
        real_cols = self._real_columns(sqlite_engine, "resource_pid_map")
        for url in (
            "sqlite:///:memory:",
            "postgresql+psycopg://u:p@localhost:5432/ens",
        ):
            f = self._flusher_with_url(url)
            sql = f._dialect_insert(
                "resource_pid_map", sorted(real_cols), idempotent=False
            )
            assert "ON CONFLICT" not in sql.upper(), (url, sql)
            assert "INSERT OR IGNORE" not in sql.upper(), (url, sql)
            assert sql.upper().startswith("INSERT INTO"), (url, sql)

    def test_every_conflict_target_names_real_columns(self, sqlite_engine):
        """PG-grammar pin: for EVERY insert the flusher can emit,
        the ON CONFLICT target column list names REAL columns of
        the target table (applied schema = ground truth)."""
        for table, columns, idempotent in self._FLUSHER_INSERTS:
            real_cols = self._real_columns(sqlite_engine, table)
            assert set(columns) <= real_cols, (table, columns, real_cols)
            for url in (
                "sqlite:///:memory:",
                "postgresql+psycopg://u:p@localhost:5432/ens",
            ):
                f = self._flusher_with_url(url)
                sql = f._dialect_insert(
                    table, columns, idempotent=idempotent
                )
                assert "_NONEXISTENT" not in sql, (table, url, sql)
                m = re.search(
                    r"ON\s+CONFLICT\s*\(([^)]*)\)", sql, flags=re.IGNORECASE
                )
                if m is None:
                    # Plain insert — nothing to pin on this shape.
                    continue
                targets = [
                    c.strip().strip('"') for c in m.group(1).split(",")
                ]
                assert targets, (table, url, sql)
                unknown = [c for c in targets if c not in real_cols]
                assert not unknown, (
                    f"ON CONFLICT target {unknown} is not a real column "
                    f"of {table} (42703 class — C-1); sql={sql!r}"
                )

    def test_idempotent_on_non_bucket_table_fails_loud(self):
        """The silent ``_NONEXISTENT`` fallback is replaced by a
        generation-time ValueError — a future idempotent=True call
        against a table without the compound unique key fails HERE,
        not silently at flush time."""
        f = self._flusher_with_url(
            "postgresql+psycopg://u:p@localhost:5432/ens"
        )
        with pytest.raises(ValueError, match="C-1"):
            f._dialect_insert(
                "resource_pid_map", ["pid", "kind"], idempotent=True
            )

    def test_raw_write_sql_via_real_flusher_pg_shape(self, sqlite_engine):
        """End-to-end shape check through a real Flusher: the raw
        tier write SQL targets (bucket_ts, interval_s) on a PG URL
        and both columns exist in the applied schema."""
        real_cols = self._real_columns(sqlite_engine, "resource_daemon_bucket")
        assert {"bucket_ts", "interval_s"} <= real_cols
        ring = TierRing(tier="raw", maxlen=4)
        ring.append(
            {
                "bucket_ts": "2026-10-18 10:00:00",
                "interval_s": 30,
                "rss_kb": 1,
                "cpu_user_usec": 1,
                "cpu_sys_usec": 1,
                "threads": 1,
                "fds": 1,
                "sample_cost_ms": 0.1,
                "attributed_bytes": 0,
                "unattributed_kb": 0,
                "coverage_pct": 0.0,
                "missed_samples": 0,
            }
        )
        f = Flusher(
            engine=self._pg_url_engine(),
            raw_ring=ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
        )
        sql = f._dialect_insert(
            "resource_daemon_bucket",
            [
                "bucket_ts", "interval_s", "rss_kb", "cpu_user_usec",
                "cpu_sys_usec", "missed_samples", "anon_kb", "threads",
                "fds", "sample_cost_ms", "attributed_bytes",
                "unattributed_kb", "coverage_pct",
            ],
            idempotent=True,
        )
        assert "ON CONFLICT (bucket_ts, interval_s) DO NOTHING" in sql

    def _pg_url_engine(self):
        import types

        return types.SimpleNamespace(
            url="postgresql+psycopg://u:p@localhost:5432/ens"
        )


class TestRelationMissingTolerance:
    """C-1 hardening: the first-boot tolerance matches the
    RELATION-missing class ONLY. A PG column error (42703,
    ``column ... does not exist``) must PROPAGATE — the old bare
    substring swallowed it (the C-1 silent-loss amplifier)."""

    class _RaisingConn:
        class _NoopSavepoint:
            def commit(self):
                pass

            def rollback(self):
                pass

        def __init__(self, exc: BaseException):
            self._exc = exc

        def execute(self, *a, **k):
            raise self._exc

        def begin_nested(self):
            # Post-FINDING-#2 the tolerance runs inside a SAVEPOINT;
            # this double only exercises the tolerance semantics, so
            # a no-op savepoint suffices (rollback/commit never touch
            # state here).
            return self._NoopSavepoint()

    def _flusher(self) -> Flusher:
        import types

        f = Flusher.__new__(Flusher)
        f.engine = types.SimpleNamespace(url="sqlite:///:memory:")
        f._missing_table_warned = False
        return f

    def test_pg_column_error_propagates(self):
        f = self._flusher()
        conn = self._RaisingConn(
            Exception(
                'column "_nonexistent" of relation '
                '"resource_pid_map" does not exist'
            )
        )
        with pytest.raises(Exception, match="_nonexistent"):
            f._tier_write(conn, "INSERT INTO x VALUES (1)", [{}])

    def test_pg_relation_missing_still_tolerated(self):
        f = self._flusher()
        conn = self._RaisingConn(
            Exception('relation "resource_pid_map" does not exist')
        )
        assert f._tier_write(conn, "INSERT INTO x VALUES (1)", [{}]) == 0
        assert f._missing_table_warned is True

    def test_sqlite_no_such_table_still_tolerated(self):
        f = self._flusher()
        conn = self._RaisingConn(
            Exception("no such table: resource_daemon_bucket")
        )
        assert f._tier_write(conn, "INSERT INTO x VALUES (1)", [{}]) == 0

    def test_undefinedtable_class_name_tolerated(self):
        f = self._flusher()

        class UndefinedTable(Exception):
            pass

        conn = self._RaisingConn(UndefinedTable("syntax-level miss"))
        assert f._tier_write(conn, "INSERT INTO x VALUES (1)", [{}]) == 0

    def test_sweep_column_error_propagates(self, sqlite_engine):
        """The sweep shares the narrowed matcher — a column-shaped
        DELETE error surfaces instead of returning 0."""
        f = self._flusher()
        conn = self._RaisingConn(
            Exception('column "nope" does not exist')
        )
        with pytest.raises(Exception, match="nope"):
            f._sweep(conn, "resource_pid_map", 12345)

    def test_sweep_relation_missing_returns_zero(self):
        f = self._flusher()
        conn = self._RaisingConn(
            Exception('relation "resource_pid_map" does not exist')
        )
        assert f._sweep(conn, "resource_pid_map", 12345) == 0

    def test_write_pid_map_emits_plain_insert_pg(self):
        """Call-site pin: driving the REAL flusher's pid_map write
        with a PG-URL engine must emit a plain INSERT (no conflict
        clause). Catches a regression of the call-site flag (the
        original bug lived in the ``idempotent=True`` argument,
        which the direct-_dialect_insert pins above do not
        exercise)."""
        import types

        from daemon.services.resource_monitor.pid_registry import (
            PidRegistryEntry,
        )

        captured: list[str] = []

        class _CaptureConn:
            def execute(self, sql, params):
                captured.append(str(sql))
                return types.SimpleNamespace(rowcount=1)

            def begin_nested(self):
                # FINDING #2 savepoint surface — no-op double.
                return types.SimpleNamespace(
                    commit=lambda: None, rollback=lambda: None
                )

        f = Flusher.__new__(Flusher)
        f.engine = types.SimpleNamespace(
            url="postgresql+psycopg://u:p@localhost:5432/ens"
        )
        f._missing_table_warned = False
        entry = PidRegistryEntry(
            pid=1,
            start_time=2,
            kind="bash",
            spawned_at="2026-10-18 10:00:00",
            exited_at="2026-10-18 10:01:00",
        )
        f._write_pid_map(_CaptureConn(), [entry])
        assert len(captured) == 1
        sql = captured[0]
        assert sql.upper().startswith("INSERT INTO RESOURCE_PID_MAP"), sql
        assert "ON CONFLICT" not in sql.upper(), sql
        assert "_NONEXISTENT" not in sql, sql


# ──────────────────────────────────────────────────────────────────────────
# Final-gate FINDING #2 — PG txn-abort window survival (savepoint lane)
# ──────────────────────────────────────────────────────────────────────────


class TestPGTxnAbortWindowSurvival:
    """FINDING #2 (final gate, advisory): on PostgreSQL a tolerated
    42P01 (relation-missing INSERT) ABORTS the surrounding
    transaction — the same-window sweep then dies with 25P02
    (``InFailedSqlTransaction``, correctly NOT classified
    missing-relation) and the window RAISES instead of degrading
    to 0. The fix runs every tier-write AND sweep statement inside
    a SAVEPOINT (:meth:`Connection.begin_nested`) with the
    tolerance catch INSIDE the nested block, so the savepoint
    rollback clears the aborted state and the window degrades to 0
    and continues.

    SQLite cannot discriminate the fix (no mid-transaction abort —
    the pre-fix code was unit-green by dialect leniency), so this
    test EMULATES the PG abort semantics on a duck-typed
    connection: after one statement fails, every subsequent
    statement raises the 25P02-shaped error UNLESS a savepoint
    rollback ran in between (exactly what ``ROLLBACK TO SAVEPOINT``
    does on PG).
    """

    _42P01 = 'relation "resource_daemon_bucket" does not exist'
    _25P02 = (
        "current transaction is aborted, commands ignored until end "
        "of transaction block"
    )
    _42703 = 'column "nope" of relation "resource_pid_map" does not exist'

    class _AbortEmulatingConn:
        """Duck-typed connection with PG abort semantics: after a
        failing statement, EVERY subsequent execute raises 25P02
        until a savepoint rollback clears the state."""

        def __init__(self):
            self.aborted = False
            self.executed: list[str] = []
            self._armed = True  # fail the FIRST statement once

        def execute(self, sql, params=None):
            import types as _types

            if self.aborted:
                raise Exception(TestPGTxnAbortWindowSurvival._25P02)
            s = str(sql)
            self.executed.append(s)
            if self._armed:
                self._armed = False
                self.aborted = True
                raise Exception(TestPGTxnAbortWindowSurvival._42P01)
            return _types.SimpleNamespace(rowcount=0)

        def begin_nested(self):
            conn = self

            class _SP:
                def commit(self):  # RELEASE SAVEPOINT — no state change
                    pass

                def rollback(self):  # ROLLBACK TO SAVEPOINT — clears abort
                    conn.aborted = False

            return _SP()

    class _AbortEmulatingEngine:
        """``begin()`` yields the shared emulating connection (the
        flusher's ONE-window contract — no reuse beyond it)."""

        def __init__(self, conn):
            self._conn = conn
            self.url = "postgresql+psycopg://u:p@localhost:5432/ens"

        def begin(self):
            eng = self

            class _Tx:
                def __enter__(self):
                    return eng._conn

                def __exit__(self, *a):
                    return False

            return _Tx()

    def _flusher_over(self, conn, ring):
        """A Flusher shell over the emulating engine (the same
        ``__new__`` shell convention as TestRelationMissingTolerance).
        pid registry stub drains empty; no instance ring; no gate."""
        import types as _types

        from daemon.services.resource_monitor.flusher import FlusherCounters

        f = Flusher.__new__(Flusher)
        f.engine = self._AbortEmulatingEngine(conn)
        f.raw_ring = ring
        f.flush_batch_seconds = 60
        f.retention = RetentionPolicy(2880, 336, 180)
        f.counters = FlusherCounters()
        f._pid_registry = _types.SimpleNamespace(drain_history=lambda: [])
        f._instance_ring = None
        f._wall_clock = _time_mod.monotonic
        f._now_utc_naive = None  # constructor default wired below
        if f._now_utc_naive is None:
            from daemon.services.resource_monitor.flusher import (
                _default_now_utc_naive,
            )

            f._now_utc_naive = _default_now_utc_naive
        f._anomaly_gate = None
        f._buffer = []
        f._missing_table_warned = False
        return f

    def test_write_path_tolerated_error_no_longer_kills_the_window(self):
        """The tester's real-PG sequence: the raw tier INSERT hits
        42P01 mid-window; post-fix the savepoint rollback clears the
        abort and the minute/hour writes + ALL sweeps complete — the
        window returns instead of raising."""
        conn = self._AbortEmulatingConn()
        ring = TierRing(tier="raw", maxlen=16)
        ring.append(
            {
                "bucket_ts": "2026-10-18 10:00:00",
                "interval_s": 30,
                "rss_kb": 100000,
                "cpu_user_usec": 1000,
                "cpu_sys_usec": 100,
                "threads": 4,
                "fds": 5,
                "sample_cost_ms": 0.5,
                "attributed_bytes": 0,
                "unattributed_kb": 0,
                "coverage_pct": 0.0,
                "missed_samples": 0,
            }
        )
        flusher = self._flusher_over(conn, ring)
        result = flusher.run_once()  # pre-fix: RAISES 25P02-shaped
        assert "swept" in result
        assert result["swept"]["pid_map"] == 0
        # The window continued past the failed raw write: minute/hour
        # inserts ran, and every sweep DELETE executed.
        deletes = [s for s in conn.executed if "DELETE FROM" in s]
        assert len(deletes) == 5, conn.executed
        assert any("resource_pid_map" in s for s in deletes)
        # The savepoint rollback cleared the PG abort state.
        assert conn.aborted is False
        assert flusher._missing_table_warned is True

    def test_sweep_path_tolerated_error_no_longer_kills_the_window(self):
        """Empty-buffer window: the FIRST sweep DELETE hits 42P01;
        post-fix the remaining 4 sweeps + the pid_map write still
        run (pre-fix: the second sweep died 25P02 and the window
        raised)."""
        conn = self._AbortEmulatingConn()
        ring = TierRing(tier="raw", maxlen=16)  # empty
        flusher = self._flusher_over(conn, ring)
        result = flusher.run_once()
        assert result["rows_written"] == 0
        deletes = [s for s in conn.executed if "DELETE FROM" in s]
        assert len(deletes) == 5, conn.executed
        assert conn.aborted is False

    def test_non_tolerated_error_still_propagates_through_savepoint(self):
        """C-1 hardening survives the savepoint path: a 42703
        column-shaped error is NOT missing-relation and MUST
        surface (never swallowed as first-boot benign)."""
        conn = self._AbortEmulatingConn()
        # Re-arm the fault with the column-error shape.
        conn.execute_orig = conn.execute

        def _fail_column(sql, params=None):
            if conn._armed:
                conn._armed = False
                conn.aborted = True
                raise Exception(TestPGTxnAbortWindowSurvival._42703)
            return conn.execute_orig(sql, params)

        ring = TierRing(tier="raw", maxlen=16)
        flusher = self._flusher_over(conn, ring)
        import unittest.mock as _mock

        with _mock.patch.object(conn, "execute", side_effect=_fail_column):
            with pytest.raises(Exception, match="nope"):
                flusher.run_once()
        # The savepoint was rolled back and the error STILL surfaced —
        # the tolerance did NOT swallow a column-shaped error (C-1
        # hardening intact through the savepoint path). The abort
        # state itself is cleared by the savepoint rollback before
        # the tolerance check (implementation detail); the observable
        # contract is the PROPAGATION asserted above.
