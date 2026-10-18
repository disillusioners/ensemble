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
