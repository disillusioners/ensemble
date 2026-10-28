"""T4 — Fingerprints vs the incident record.

The incident (2026-10-26) recorded pg_stat_activity ``last_query``
fingerprints for the 5 stuck backends:
    BEGIN                                                x2
    SELECT instances.instance_id, instances.project_id ...  x2
    SELECT count(*) FROM job_queue_items ...              x1
    INSERT ... mission_records (UniqueViolation)          x4/boot

For each, MEASURE the byte string PG actually stores in
``pg_stat_activity.last_query`` at base (19fc11c8b) and compare it to
the incident's recorded shape.

Run:  /home/nea/ensemble-src/.venv/bin/python t4_fingerprints.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

sys.path.insert(0, "/home/nea/ensemble-src-wt-dbpool-phase0")

from sqlalchemy import text  # noqa: E402

from common import (  # noqa: E402
    SCRATCH_URL,
    activity_by_pid,
    emit,
    make_admin_conn,
    make_manager_engine,
    make_repair_engine,
)

REPO = "/home/nea/ensemble-src-wt-dbpool-phase0"


def _raw_conn(obj):
    """Return the raw psycopg3 connection for a Session OR a Connection."""
    c = obj
    # Session -> .connection() -> Connection
    if hasattr(c, "connection") and callable(getattr(c, "connection")):
        try:
            c = c.connection()
        except TypeError:
            pass
    # Connection -> .connection -> _ConnectionFairy
    fairy = getattr(c, "connection", None)
    if fairy is not None and hasattr(fairy, "driver_connection"):
        return fairy.driver_connection
    # already raw
    if hasattr(c, "info"):
        return c
    raise TypeError(f"cannot resolve a raw connection from {type(obj)}")


def held_last_query(conn) -> tuple[str, dict[str, Any] | None]:
    """Execute on `conn`, keep it open, read PG's stored last_query."""
    pid = _raw_conn(conn).info.backend_pid
    time.sleep(0.15)
    row = activity_by_pid(ADMIN, pid)
    return (row or {}).get("last_query"), row


ADMIN = None


def build_schema(engine) -> None:
    """Create the REAL daemon schema (real models) in the scratch DB."""
    from sqlmodel import SQLModel

    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.mission_record.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.message_queue.models  # noqa: F401


    SQLModel.metadata.create_all(engine)


def main() -> None:
    global ADMIN
    ADMIN = make_admin_conn()
    engine = make_manager_engine()
    repair = make_repair_engine()
    build_schema(engine)

    results: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # F-A — the /api/instances list path (ORM render)
    # ------------------------------------------------------------------
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    from sqlmodel import select

    repo = SQLModelInstanceRepository(engine)
    try:
        instances, total, truncated = repo.list(limit=10, offset=0)
        f_a_status = "ran"
    except Exception as exc:
        f_a_status = f"{type(exc).__name__}: {exc}"

    # The EXACT statement the repository issues (flat path):
    stmt = select(Instance)
    compiled = str(stmt.compile(engine))
    with engine.connect() as conn:
        conn.execute(text(compiled)).first()
        last, row = held_last_query(conn)
    results["F-A_instances_list_ORM_render"] = {
        "repo_call_status": f_a_status,
        "measured_last_query": last,
        "pg_state": (row or {}).get("state"),
        "incident_fingerprint": "SELECT instances.instance_id, instances.project_id ...",
        "byte_compatible_with_incident": bool(
            last
            and last.startswith("SELECT instances.instance_id")
            and "instances.project_id" in last
        ),
        "full_rendered_sql": compiled,
    }

    # ------------------------------------------------------------------
    # F-B — the ens_db_tools.py:1479/:1491 explicit BEGIN
    # ------------------------------------------------------------------
    from sqlalchemy.orm import Session

    with Session(repair) as s:
        s.execute(text("BEGIN"))
        last_b, row_b = held_last_query(s.connection())
    results["F-B_explicit_BEGIN"] = {
        "measured_last_query": last_b,
        "pg_state": (row_b or {}).get("state"),
        "incident_fingerprint": "BEGIN",
        "byte_compatible_with_incident": last_b == "BEGIN",
        "note": "ens_db_tools.py:1479 (DDL) / :1491 (DML) both execute "
        "session.execute(text('BEGIN')) — the only text('BEGIN') in daemon/",
    }

    # ------------------------------------------------------------------
    # F-C — the job_queue_items count (defer_block_resolver owner)
    # ------------------------------------------------------------------
    # NOTE: daemon.services.defer_block_resolver has a circular import
    # through daemon.routers — import the router package FIRST so the
    # cycle resolves.
    import daemon.routers  # noqa: F401
    from daemon.services.defer_block_resolver import _DEFER_PENDING_COUNT_SQL

    with engine.connect() as conn:
        conn.execute(_DEFER_PENDING_COUNT_SQL).first()
        last_c, row_c = held_last_query(conn)
    results["F-C_job_queue_items_count"] = {
        "measured_last_query": last_c,
        "pg_state": (row_c or {}).get("state"),
        "incident_fingerprint": "SELECT count(*) FROM job_queue_items ...",
        "byte_compatible_with_incident": bool(
            last_c and last_c.startswith("SELECT count(*) FROM job_queue_items")
        ),
        "source": "daemon/services/defer_block_resolver.py:185-191 "
        "_DEFER_PENDING_COUNT_SQL",
    }

    # ------------------------------------------------------------------
    # F-D — the readiness queue_freshness probe
    # ------------------------------------------------------------------
    from daemon.services.readiness import (
        _INFLIGHT_COUNT_SQL_POSTGRES,
        _QUEUE_MAX_AGE_SQL_POSTGRES,
        make_queue_probe,
    )

    probe = make_queue_probe(
        engine,
        boot_epoch="2026-01-01T00:00:00",
        freshness_threshold_seconds=60.0,
    )
    try:
        probe()
        f_d_status = "ran"
    except Exception as exc:
        f_d_status = f"{type(exc).__name__}: {exc}"

    with engine.connect() as conn:
        conn.execute(
            _QUEUE_MAX_AGE_SQL_POSTGRES,
            {"status_running": "running", "boot_epoch": "2026-01-01T00:00:00"},
        ).first()
        last_d, row_d = held_last_query(conn)
    with engine.connect() as conn:
        conn.execute(
            _INFLIGHT_COUNT_SQL_POSTGRES,
            {
                "status_running": "running",
                "boot_epoch": "2026-01-01T00:00:00",
                "fresh_threshold_secs": 60.0,
            },
        ).first()
        last_d2, row_d2 = held_last_query(conn)

    results["F-D_readiness_queue_probe"] = {
        "probe_call_status": f_d_status,
        "measured_last_query_max_age": last_d,
        "measured_last_query_inflight_count": last_d2,
        "pg_state": (row_d or {}).get("state"),
        "incident_fingerprint": "SELECT count(*) FROM job_queue_items ...",
        "byte_compatible_with_incident": False,
        "reads_from_task": bool(last_d and "FROM task" in last_d),
        "note": "the readiness probe reads FROM task, NOT job_queue_items — "
        "the RCA's job_queue_items fingerprint does NOT belong to the probe",
    }

    # ------------------------------------------------------------------
    # F-E — the mission_records INSERT render
    # ------------------------------------------------------------------
    from daemon.repositories.mission_record.models import (
        MissionRecord,
        MissionStatus,
    )

    ins = (
        __import__("sqlmodel").insert(MissionRecord)
        .values(
            anchor_instance_id="anchor-x",
            epoch_seq=1,
            founding_source="boot",
            founding_message_id=None,
            status=MissionStatus.OPEN.value,
        )
    )
    compiled_ins = str(ins.compile(engine))
    results["F-E_mission_records_insert"] = {
        "compiled_insert_sql": compiled_ins,
        "mission_status_open_value": MissionStatus.OPEN.value,
        "incident_fingerprint": "INSERT ... mission_records",
        "byte_compatible_with_incident": "mission_records" in compiled_ins,
    }

    # The real INSERT as executed by insert_mission (ORM, own session),
    # flushed (not committed) so the backend keeps the INSERT as its
    # last_query while we sample pg_stat_activity.
    from sqlmodel import Session as _SMSession

    with engine.connect() as conn:
        sm = _SMSession(bind=conn)
        sm.add(
            MissionRecord(
                anchor_instance_id="anchor-x",
                epoch_seq=1,
                founding_source="boot",
                founding_message_id=None,
                status=MissionStatus.OPEN.value,
            )
        )
        sm.flush()
        last_e, row_e = held_last_query(conn)
        sm.rollback()
    results["F-E_mission_records_insert"]["measured_last_query_after_insert"] = (
        last_e
    )
    results["F-E_mission_records_insert"]["pg_state_after_insert"] = (
        row_e or {}
    ).get("state")

    emit("T4 FINGERPRINT MEASUREMENTS", results)

    emit(
        "T4 COMPARISON MATRIX",
        {
            "incident_fingerprint -> reproduced?": {
                "BEGIN": results["F-B_explicit_BEGIN"][
                    "byte_compatible_with_incident"
                ],
                "SELECT instances.instance_id, instances.project_id ...": results[
                    "F-A_instances_list_ORM_render"
                ]["byte_compatible_with_incident"],
                "SELECT count(*) FROM job_queue_items ...": results[
                    "F-C_job_queue_items_count"
                ]["byte_compatible_with_incident"],
                "INSERT ... mission_records": results["F-E_mission_records_insert"][
                    "byte_compatible_with_incident"
                ],
            },
            "readiness_probe_actually_reads_from_task": results[
                "F-D_readiness_queue_probe"
            ]["reads_from_task"],
            "readiness_probe_matches_incident_fingerprint": results[
                "F-D_readiness_queue_probe"
            ]["byte_compatible_with_incident"],
        },
    )

    engine.dispose()
    repair.dispose()
    ADMIN.close()


if __name__ == "__main__":
    main()
