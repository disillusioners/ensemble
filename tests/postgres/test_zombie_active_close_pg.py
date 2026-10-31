"""Pack I — Pattern (h) real-PG leg (wi-78967dba): the close
survives the live trigger suite; same-txn lock release on real PG;
seeded end-to-end.

Runs under the postgres marker (``--override-ini="addopts=" -m
postgres``), SERIAL-only under xdist, ``PG_TEST_*`` DSN (conftest
defaults: localhost:5432/ensemble_test/ensemble/ensemble_dev — the
conftest's own scratch-DB fence enforces the allowlist).

Trigger-suite honesty (phase3-plan §7 risk note): the conftest's
``create_all`` does NOT install ``trg_job_locks_active_guard`` /
``trg_job_queue_items_active_lock_guard`` (those come from the
manager path ``_ensure_postgres_columns``). This pack ASSERTS the
trigger suite is present first — when the fixtures have not been
installed, the pack INSTALLS them via the documented trigger DDL
(the same bodies the manager ships) so the leg is never vacuous.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.dependency_bus import set_dependency_bus

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_jqs_mock,
    read_job_state,
    make_recovery_service,
)

pytestmark = pytest.mark.postgres

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

_ACTIVE_JOB_IDS_SUBQUERY = (
    "SELECT job_id FROM job_queue_items "
    "WHERE admission_state IN ('queued', 'active') "
    "  AND deleted_at IS NULL"
)


def _ensure_trigger_suite(pg_engine) -> dict:
    """Assert-or-install the live PG trigger suite (never vacuous).

    The DDL is VERBATIM from the manager's own install path
    (``daemon/manager.py`` :8201-8206 — the deferred CONSTRAINT
    TRIGGERs and their function bodies), so the leg exercises the
    production shapes, not an approximation."""
    with pg_engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE NOT tgisinternal AND tgname IN "
                "('trg_job_locks_active_guard', "
                "'trg_job_queue_items_active_lock_guard')"
            )
        ).all()
        present = {r[0] for r in rows}
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION "
                "job_queue_items_active_lock_guard() RETURNS TRIGGER "
                "AS $$ BEGIN IF NEW.admission_state = 'active' AND "
                "NEW.job_type != 'message' THEN IF NOT EXISTS "
                "(SELECT 1 FROM job_locks WHERE instance_id = "
                "NEW.instance_id) THEN RAISE EXCEPTION "
                "'admission_state=active requires a job_locks row "
                "(instance_id=%)', NEW.instance_id USING ERRCODE = "
                "'integrity_constraint_violation'; END IF; END IF; "
                "RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION "
                "job_locks_active_guard() RETURNS TRIGGER AS $$ "
                "BEGIN IF NOT EXISTS (SELECT 1 FROM job_queue_items "
                "WHERE instance_id = NEW.instance_id AND "
                "admission_state = 'active' AND deleted_at IS NULL) "
                "THEN RAISE EXCEPTION 'job_locks row requires "
                "admission_state=active (instance_id=%)', "
                "NEW.instance_id USING ERRCODE = "
                "'integrity_constraint_violation'; END IF; "
                "RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS "
                "trg_job_queue_items_active_lock_guard "
                "ON job_queue_items"
            )
        )
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_job_locks_active_guard "
                "ON job_locks"
            )
        )
        conn.execute(
            text(
                "CREATE CONSTRAINT TRIGGER "
                "trg_job_queue_items_active_lock_guard "
                "AFTER INSERT OR UPDATE OF admission_state "
                "ON job_queue_items DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION "
                "job_queue_items_active_lock_guard()"
            )
        )
        conn.execute(
            text(
                "CREATE CONSTRAINT TRIGGER trg_job_locks_active_guard "
                "AFTER INSERT OR UPDATE ON job_locks "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION job_locks_active_guard()"
            )
        )
        return {"installed": True, "present_before": sorted(present)}


class TestZombieActiveCloseRealPG:
    def test_close_survives_trigger_suite_releases_lock_same_txn(
        self, pg_engine
    ):
        _ensure_trigger_suite(pg_engine)
        ref = insert_zombie_fixture(
            pg_engine, verdict="completed", age_hours=3.0
        )
        bus = make_bus_mock(pending=[])
        set_dependency_bus(bus)
        try:
            service = make_recovery_service(pg_engine)
            stats = asyncio.run(
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            )
        finally:
            set_dependency_bus(None)
        st = read_job_state(pg_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["terminal_reason"] == "zombie_active_completed", st
        assert st["lock_rows"] == 0, (
            "same-transaction lock release must hold on real PG "
            "under the trigger suite"
        )
        zc = stats.get("zombie_close") or {}
        assert zc.get("finalized", 0) == 1, zc

    def test_candidate_query_populated_on_real_pg(self, pg_engine):
        """The candidate SQL (LEFT JOIN + NOT EXISTS + MAX window)
        compiles and returns the seeded zombie in deterministic
        oldest-first order on real PG."""
        _ensure_trigger_suite(pg_engine)
        older = insert_zombie_fixture(
            pg_engine, verdict="failed", age_hours=5.0,
            queue_suffix="pgolder",
        )
        newer = insert_zombie_fixture(
            pg_engine, verdict="cancelled", age_hours=3.0,
            queue_suffix="pgnewer",
        )
        repo = JobRepository(pg_engine)
        cands = repo.find_zombie_active_candidates(
            age_bound_seconds=7200, batch_cap=10
        )
        ids = [c.job_id for c in cands]
        assert older["job_id"] in ids and newer["job_id"] in ids
        assert ids.index(older["job_id"]) < ids.index(newer["job_id"]), (
            "oldest-terminal-first deterministic order (S5)"
        )
        st_by_id = {c.job_id: c.task_status for c in cands}
        assert st_by_id[older["job_id"]] == "failed"
        assert st_by_id[newer["job_id"]] == "cancelled"

    def test_seeded_end_to_end_three_verdicts(self, pg_engine):
        _ensure_trigger_suite(pg_engine)
        refs = [
            insert_zombie_fixture(
                pg_engine, verdict=v, age_hours=3.0,
                queue_suffix=f"pg{v}",
            )
            for v in ("completed", "failed", "cancelled")
        ]
        bus = make_bus_mock(pending=[])
        set_dependency_bus(bus)
        try:
            service = make_recovery_service(pg_engine)
            stats = asyncio.run(
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            )
        finally:
            set_dependency_bus(None)
        for ref, verdict in zip(refs, ("completed", "failed", "cancelled")):
            st = read_job_state(pg_engine, ref["job_id"])
            assert st["admission_state"] == "done", (ref, st)
            assert st["terminal_reason"] == f"zombie_active_{verdict}", st
            assert st["lock_rows"] == 0, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("finalized", 0) == 3, zc
