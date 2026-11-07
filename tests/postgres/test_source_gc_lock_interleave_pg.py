"""PostgreSQL-gated P16 lock-half leg — source-gc lane (board wi-ed230eb4).

This module exercises the REAL lock seam of
``SQLModelSourceRepository.gc_archive_and_delete`` against PostgreSQL:
``Session.get(SourceConfig, source_id, with_for_update=True)`` renders an
actual ``SELECT ... FOR UPDATE`` on PG (the SQLite unit pack renders no
lock at all — see ``instance/repository.py:1949-1954``), so the
interleave below is the ONLY place the lock can be proven to hold.

Interleave shape (phase4 §7 P16 lock half, W2 re-scope):

* Connection A opens the row lock (``SELECT ... FOR UPDATE``) inside a
  transaction and holds it.
* Connection B attempts the resume write (re-enable the row) → must
  BLOCK on the lock (short ``lock_timeout`` → a lock-timeout error).
* Release A (commit the GC delete) → B retries → the row is gone, so
  the resume degrades to the not-found family (``scheduling_service.py``
  ``_resolve_schedule_ref`` raises ``ValueError("Schedule not found: …")``
  at :989-991 — the honest 404-family outcome; asserted here as row
  absence, which is its precondition).
* Positive control: an un-contended collectable row deletes cleanly
  under the same lock path (``gc_archive_and_delete`` → ``deleted=True``).

Gated behind ``pytest -m postgres`` (deselected by default). Run with a
throwaway PG database only — see the conftest fence. NEVER run against
``ensemble_prod`` / ``ensemble_dev``.

Run command (tester phase):
    PG_TEST_DB=ensemble_test PG_TEST_USER=ensemble PG_TEST_PASSWORD=ensemble_dev \\
      pytest tests/postgres/test_source_gc_lock_interleave_pg.py -m postgres -p no:cacheprovider -o addopts=
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel

# Registering the source models ensures pg_engine's session-scoped
# create_all creates source_configs / instance_mappings /
# processed_external_messages / schedule_executions /
# schedule_executions_archive / source_configs_archive before any test
# runs.
from daemon.repositories.source.models import (  # noqa: F401
    SourceConfig,
    InstanceMapping,
    ProcessedMessage,
    ScheduleExecution,
    ScheduleExecutionArchive,
    SourceConfigArchive,
)
from daemon.repositories.source.repository import SQLModelSourceRepository


ROW_ID = "lock-row"
POSITIVE_ID = "positive-row"
RETENTION_DAYS = 14


def _seed(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM source_configs WHERE source_id IN (:a, :b)"),
                     {"a": ROW_ID, "b": POSITIVE_ID})
        conn.execute(text("DELETE FROM source_configs_archive WHERE source_id IN (:a, :b)"),
                     {"a": ROW_ID, "b": POSITIVE_ID})
        conn.execute(
            text(
                "INSERT INTO source_configs (source_id, source_type, name, config, "
                "credentials, enabled, autostart, status, error_message, "
                "created_at, updated_at) VALUES "
                "(:sid, 'scheduler', :sid, '{}'::jsonb, NULL, false, true, "
                "'stopped', NULL, :now, :upd)"
            ),
            {
                "sid": ROW_ID,
                "now": datetime.now(timezone.utc).isoformat(),
                "upd": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT INTO source_configs (source_id, source_type, name, config, "
                "credentials, enabled, autostart, status, error_message, "
                "created_at, updated_at) VALUES "
                "(:sid, 'scheduler', :sid, '{}'::jsonb, NULL, false, true, "
                "'stopped', NULL, :now, :upd)"
            ),
            {
                "sid": POSITIVE_ID,
                "now": datetime.now(timezone.utc).isoformat(),
                "upd": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
            },
        )


@pytest.fixture(autouse=True)
def _clean(pg_engine):
    _seed(pg_engine)
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM source_configs WHERE source_id IN (:a, :b)"),
                     {"a": ROW_ID, "b": POSITIVE_ID})
        conn.execute(text("DELETE FROM source_configs_archive WHERE source_id IN (:a, :b)"),
                     {"a": ROW_ID, "b": POSITIVE_ID})


@pytest.mark.postgres
def test_p16_lock_interleave_blocks_resume_then_degrades_to_not_found(pg_engine):
    """Connection A holds the row lock; connection B's resume write blocks.

    After A commits the GC delete, B retries and finds the row gone —
    the honest not-found family (scheduling_service.py _resolve_schedule_ref
    raises ValueError('Schedule not found: …') at :989-991).
    """
    now = datetime.now(timezone.utc)
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    a_result: dict = {}

    def worker_a():
        # A: open the lock, hold it, then commit the GC delete.
        with pg_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("SELECT source_id FROM source_configs "
                         "WHERE source_id = :sid FOR UPDATE"),
                    {"sid": ROW_ID},
                )
                lock_acquired.set()
                assert release_lock.wait(timeout=30), "release signal timed out"
                # Commit the GC delete inside A's txn (emulates
                # gc_archive_and_delete's final DELETE under the held lock).
                conn.execute(
                    text(
                        "DELETE FROM source_configs WHERE source_id = :sid "
                        "AND enabled = false AND status IN ('stopped','cancelled')"
                    ),
                    {"sid": ROW_ID},
                )
                a_result["deleted"] = True

    t = threading.Thread(target=worker_a, daemon=True)
    t.start()
    assert lock_acquired.wait(timeout=10), "A never acquired the row lock"

    # B: attempt the resume write (re-enable) → must BLOCK on the lock.
    blocked = False
    block_error = None
    try:
        with pg_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
                conn.execute(
                    text(
                        "UPDATE source_configs SET enabled = true, "
                        "updated_at = :now WHERE source_id = :sid"
                    ),
                    {"now": now.isoformat(), "sid": ROW_ID},
                )
    except Exception as exc:  # noqa: BLE001
        blocked = True
        block_error = str(exc)
    assert blocked, "resume write did not block on the held row lock"
    low = (block_error or "").lower()
    assert ("lock" in low) or ("timeout" in low) or ("canceling statement" in low), (
        f"unexpected block error shape: {block_error!r}"
    )

    # Release A → commit the GC delete.
    release_lock.set()
    t.join(timeout=30)
    assert a_result.get("deleted") is True, "A did not commit the delete"

    # B retries → row is gone → not-found family.
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT source_id FROM source_configs WHERE source_id = :sid"),
            {"sid": ROW_ID},
        ).fetchone()
    assert row is None, "row must be gone after GC commit (resume must degrade to not-found)"

    # Cross-check: the repo method itself reports not_found on the gone row.
    repo = SQLModelSourceRepository(pg_engine)
    res = repo.gc_archive_and_delete(
        source_id=ROW_ID, now=now, retention_days=RETENTION_DAYS
    )
    assert res.deleted is False
    assert res.skipped_reason == "not_found", res.skipped_reason


@pytest.mark.postgres
def test_p16_positive_control_uncontended_deletes_cleanly(pg_engine):
    """Positive control: an un-contended collectable row deletes cleanly
    under the same lock path (gc_archive_and_delete → deleted=True)."""
    now = datetime.now(timezone.utc)
    repo = SQLModelSourceRepository(pg_engine)

    # W1: prove the REAL method emits FOR UPDATE (not a hand-written
    # SELECT ... FOR UPDATE in the test). A before_cursor_execute listener
    # records every statement; at least one must contain "FOR UPDATE".
    # Removing with_for_update=True from gc_archive_and_delete makes this
    # RED (mutation-meaningful).
    emitted: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        emitted.append(statement)

    from sqlalchemy import event
    event.listen(pg_engine, "before_cursor_execute", _listener)
    try:
        res = repo.gc_archive_and_delete(
            source_id=POSITIVE_ID, now=now, retention_days=RETENTION_DAYS
        )
    finally:
        event.remove(pg_engine, "before_cursor_execute", _listener)

    assert any("FOR UPDATE" in (st or "").upper() for st in emitted), (
        "gc_archive_and_delete must emit a FOR UPDATE (with_for_update=True); "
        f"emitted={emitted!r}"
    )
    assert res.deleted is True, res
    assert res.skipped_reason is None, res
    assert res.archived_config is True
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT source_id FROM source_configs WHERE source_id = :sid"),
            {"sid": POSITIVE_ID},
        ).fetchone()
    assert row is None
    # archive row landed
    with pg_engine.connect() as conn:
        arch = conn.execute(
            text("SELECT source_id FROM source_configs_archive WHERE source_id = :sid"),
            {"sid": POSITIVE_ID},
        ).fetchone()
    assert arch is not None

CHILD_FIRST_ID = "child-first-row"


@pytest.fixture(autouse=True)
def _clean_child_first(pg_engine):
    # extend the existing autouse _clean is already defined above; this is a
    # separate autouse for the child-first seed/cleanup so the third node
    # stays self-contained.
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM schedule_executions WHERE schedule_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM schedule_executions_archive WHERE schedule_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM instance_mappings WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM source_configs WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM source_configs_archive WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
    # seed collectable row + children
    now = datetime.now(timezone.utc)
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO source_configs (source_id, source_type, name, config, "
                "credentials, enabled, autostart, status, error_message, "
                "created_at, updated_at) VALUES "
                "(:sid, 'scheduler', :sid, '{}'::jsonb, NULL, false, true, "
                "'stopped', NULL, :now, :upd)"
            ),
            {
                "sid": CHILD_FIRST_ID,
                "now": now.isoformat(),
                "upd": (now - timedelta(days=40)).isoformat(),
            },
        )
        for i in range(3):
            conn.execute(
                text(
                    "INSERT INTO schedule_executions (execution_id, schedule_id, "
                    "triggered_at, instance_id, status, error_message, completed_at) "
                    "VALUES (:eid, :sid, :trig, NULL, 'completed', NULL, :comp)"
                ),
                {
                    "eid": f"cf-exec-{i}",
                    "sid": CHILD_FIRST_ID,
                    "trig": (now - timedelta(days=40)).isoformat(),
                    "comp": (now - timedelta(days=39)).isoformat(),
                },
            )
        conn.execute(
            text(
                "INSERT INTO instance_mappings (mapping_id, source_id, "
                "external_user_id, agent_instance_id, agent_id, agent_dir, "
                "mapping_metadata, last_message_at, created_at) VALUES "
                "(:mid, :sid, 'u1', 'inst1', 'a', 'd', '{}'::jsonb, :lm, :ca)"
            ),
            {
                "mid": "cf-map-1",
                "sid": CHILD_FIRST_ID,
                "lm": (now - timedelta(days=40)).isoformat(),
                "ca": (now - timedelta(days=40)).isoformat(),
            },
        )
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM schedule_executions WHERE schedule_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM schedule_executions_archive WHERE schedule_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM instance_mappings WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM source_configs WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})
        conn.execute(text("DELETE FROM source_configs_archive WHERE source_id = :sid"),
                     {"sid": CHILD_FIRST_ID})


@pytest.mark.postgres
def test_p10_child_first_fk_order_under_real_pg(pg_engine):
    """P10 (retained pin) — child-first delete order under REAL PG FKs.

    Seed a collectable scheduler source with BOTH children
    (``schedule_executions`` x3 + ``instance_mappings`` x1). The live
    models declare these FKs NOT NULL + NO ACTION
    (``daemon/repositories/source/models.py:113`` instance_mappings.source_id
    FK -> source_configs, :168 schedule_executions.schedule_id FK ->
    source_configs; both NOT NULL, NO ACTION). If the delete ran parent
    first, PostgreSQL would raise ForeignKeyViolation and the row would
    survive. A clean ``deleted=True`` completion IS the order proof.

    (Mutation leg lives tester-side per house practice: swapping the
    delete order in ``gc_archive_and_delete`` makes this node RED.)

    Asserts:
      (a) completes deleted=True with correct child counts (3 executions,
          1 mapping) — FK-violation would have aborted the txn;
      (b) both archive rows landed 1:1 (config + 3 executions);
      (c) live children + parent are gone.
    """
    now = datetime.now(timezone.utc)
    repo = SQLModelSourceRepository(pg_engine)
    res = repo.gc_archive_and_delete(
        source_id=CHILD_FIRST_ID, now=now, retention_days=RETENTION_DAYS
    )
    assert res.deleted is True, res
    assert res.skipped_reason is None, res
    assert res.deleted_executions == 3, res
    assert res.deleted_mappings == 1, res
    assert res.archived_config is True
    assert res.archived_executions == 3, res

    with pg_engine.connect() as conn:
        parent = conn.execute(
            text("SELECT source_id FROM source_configs WHERE source_id = :sid"),
            {"sid": CHILD_FIRST_ID},
        ).fetchone()
        assert parent is None, "parent must be gone"
        live_execs = conn.execute(
            text("SELECT count(*) FROM schedule_executions WHERE schedule_id = :sid"),
            {"sid": CHILD_FIRST_ID},
        ).scalar()
        assert live_execs == 0, "live executions must be gone"
        live_maps = conn.execute(
            text("SELECT count(*) FROM instance_mappings WHERE source_id = :sid"),
            {"sid": CHILD_FIRST_ID},
        ).scalar()
        assert live_maps == 0, "live mappings must be gone"
        arch_cfg = conn.execute(
            text("SELECT source_id FROM source_configs_archive WHERE source_id = :sid"),
            {"sid": CHILD_FIRST_ID},
        ).fetchone()
        assert arch_cfg is not None, "config archive row must land"
        arch_execs = conn.execute(
            text("SELECT count(*) FROM schedule_executions_archive WHERE schedule_id = :sid"),
            {"sid": CHILD_FIRST_ID},
        ).scalar()
        assert arch_execs == 3, f"expected 3 archived executions, got {arch_execs}"
