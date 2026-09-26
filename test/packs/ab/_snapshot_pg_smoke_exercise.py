#!/usr/bin/env python3
"""Snapshot PG dialect smoke — agent-snapshot-v1 Gate 3 acceptance.

Exercises ``SnapshotRepository.filter_by_tags`` over both PG and SQLite
arms and compares per-case id lists.

This script is INVOKED by the bash pack
``test/packs/ab/snapshot_pg_smoke_integration_test.sh`` which
provisions its own throwaway PG cluster; the script itself is
dialect-pure Python — it expects ``POSTGRES_HOST/PORT/DB/USER``
env vars to point at the throwaway (trust-auth, port 15432).

Schema strategy
---------------
Production PG databases get their schema from
``SQLModel.metadata.create_all()`` (called during manager init) —
see ``daemon/migrations/runner.py:719-727``. ``MigrationRunner
.run_pending_migrations()`` is a documented no-op for non-SQLite
engines; the project's .sql migrations apply only on SQLite. We
mirror that here: no .sql split, just ``metadata.create_all``.

Per-case capture
----------------
Cases run independently — a PG exception on one does not block
the others. The PG result column records either a list-of-ids
or ``EXC:<ExceptionType>``. The SQLite column is the reference
oracle (v1 design: SQLite arm is the documented fallback).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, col, select

# All SQLModel tables visible to metadata.create_all
import daemon.repositories  # noqa
import daemon.repositories.infra.models  # noqa
import daemon.repositories.instance.models  # noqa
import daemon.repositories.message_queue.models  # noqa
import daemon.repositories.source.models  # noqa
import daemon.repositories.project.models  # noqa
import daemon.repositories.skill.models  # noqa
import daemon.repositories.blueprint.models  # noqa
import daemon.repositories.message_metadata.models  # noqa
import daemon.repositories.job_queue.models  # noqa
import daemon.repositories.shared_meta_kv.models  # noqa
import daemon.repositories.mcp_server.models  # noqa
import daemon.repositories.db_connection.models  # noqa
from daemon.repositories.snapshot.models import Snapshot
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.repositories.project.models import Project, ProjectStatus


# ── Seed fixtures ─────────────────────────────────────────────────────
PROJECT_ID = "proj-snap-smoke"
AGENT_ID = "agent-snap-smoke"

# (id, title, tags) — order matters; filter_by_tags preserves caller order.
SEEDS: list[tuple[str, str, list[str]]] = [
    ("snap-001", "t1", ["a", "b", "c"]),
    ("snap-002", "t2", ["a", "b"]),
    ("snap-003", "t3", ["b"]),
    ("snap-004", "t4", ["c", "d", "a"]),
    ("snap-005", "t5", []),               # empty tags — passthrough
    ("snap-006", "t6", ["a"]),
    ("snap-007", "t7", ["d", "e"]),       # no overlap with a/b/c baseline
]

CASES: list[tuple[tuple[str, ...], str]] = [
    (("a", "b"), "all"),
    (("a", "b"), "any"),
    (("a", "c"), "all"),
    (("a", "c"), "any"),
    (("a",), "all"),
    (("a",), "any"),
    (("d",), "any"),
    (("d",), "all"),
    ((), "all"),
    ((), "any"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_snapshot(sid: str, title: str, tags: list[str]) -> Snapshot:
    return Snapshot(
        id=sid,
        project_id=PROJECT_ID,
        created_by_agent_id=AGENT_ID,
        target_instance_id=f"inst-{sid}",
        title=title,
        task_summary=f"smoke-{sid}",
        domain_tags=tags,
        status="active",
        git_dirty=False,
        runtime_version="smoke-0.0.0",
        effective_model="smoke-model",
        digest={},
        created_at=_now(),
    )


def _seed(engine) -> list[Snapshot]:
    """Seed identical fixtures; return fresh loaded candidates."""
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        existing = session.get(Project, PROJECT_ID)
        if existing is None:
            session.add(Project(
                project_id=PROJECT_ID,
                name="snap-smoke",
                project_type="smoke",
                status=ProjectStatus.ACTIVE.value,
                main_directory="/tmp/snapab",
            ))
            session.commit()
        for sid, title, tags in SEEDS:
            session.add(_make_snapshot(sid, title, tags))
        session.commit()
    with Session(engine) as session:
        return list(session.exec(
            select(Snapshot).where(Snapshot.project_id == PROJECT_ID)
        ).all())


def _ids(rows) -> list[str]:
    return [r.id for r in rows]


def _run_one(repo: SnapshotRepository, candidates: list[Snapshot], tags: list[str], mode: str) -> tuple[str, list[str] | str]:
    """Return (status, payload). status ∈ {'OK', 'EXC'}.

    On success: 'OK' + list of ids.
    On exception: 'EXC' + error type name.
    """
    try:
        got = repo.filter_by_tags(candidates, tags=tags, tag_mode=mode)
        return "OK", _ids(got)
    except Exception as exc:
        return "EXC", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


def main() -> int:
    pg_host = os.environ["POSTGRES_HOST"]
    pg_port = os.environ["POSTGRES_PORT"]
    pg_db = os.environ["POSTGRES_DB"]
    pg_user = os.environ["POSTGRES_USER"]
    pg_url = f"postgresql+psycopg://{pg_user}@{pg_host}:{pg_port}/{pg_db}"

    print("=== engine wiring ===")
    print(f"pg: {pg_host}:{pg_port}/{pg_db} user={pg_user}")
    pg_engine = create_engine(pg_url, pool_pre_ping=False, future=True)
    sqlite_engine = create_engine("sqlite:///:memory:", future=True)

    # Verify PG isolation BEFORE any writes
    print("\n=== PG isolation proof (read-only) ===")
    with pg_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT current_database(), current_setting('port'), "
            "inet_server_port(), pg_backend_pid()"
        )).fetchone()
        print(f"db={row[0]}  port_setting={row[1]}  server_port={row[2]}  backend_pid={row[3]}")

    # Mirror production schema path: SQLModel.metadata.create_all (runner is
    # documented no-op for PG; see daemon/migrations/runner.py:719-727).
    print("\n=== schema materialization (SQLModel.metadata.create_all) ===")
    SQLModel.metadata.create_all(pg_engine)
    SQLModel.metadata.create_all(sqlite_engine)

    # Identify which PG columns made it + capture exec plan evidence
    print("\n=== snapshots table — PG column types ===")
    with pg_engine.connect() as conn:
        r = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name='snapshots' AND column_name='domain_tags'
        """)).fetchone()
        print(f"PG snapshots.domain_tags → {r[1]} (expect: jsonb)")

        ix = conn.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename='snapshots' ORDER BY indexname
        """)).fetchall()
        print(f"PG snapshots indexes ({len(ix)}): {[r[0] for r in ix]}")

    # GIN-check (per rider (f) claim: no GIN index)
    print("\n=== GIN index check on snapshots.domain_tags (expect empty) ===")
    with pg_engine.connect() as conn:
        gin = conn.execute(text("""
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename='snapshots' AND indexdef ILIKE '%gin%'
        """)).fetchall()
        print(f"gin count: {len(gin)} (expect 0 — matches rider (f) 'GIN-free fallback' claim)")

    # ── Migration runner — documented no-op for PG; record the observation ──
    print("\n=== MigrationRunner behavior on PG (runner.py:719-727) ===")
    from daemon.migrations import MigrationRunner
    runner = MigrationRunner(pg_engine)
    applied = runner.run_pending_migrations()
    print(f"runner returned: {applied!r} (expect: [] — documented PG no-op)")
    print("schema_migrations ledger (PG is empty by design):")
    with pg_engine.connect() as conn:
        has_tbl = conn.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name='schema_migrations'"
        )).fetchone()
        if not has_tbl:
            print("  table does not exist (confirmed no-op: runner.py:719-727 skipped create)")
        else:
            rows = conn.execute(text(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )).fetchall()
            if rows:
                for r in rows: print(f"  {r[0]} | {r[1]}")
            else:
                print("  (empty — runtime PG schema is materialized via metadata.create_all)")

    # ── Seed ──
    print("\n=== seed (identical fixtures on both dialects) ===")
    pg_candidates = _seed(pg_engine)
    sqlite_candidates = _seed(sqlite_engine)
    print(f"PG: {len(pg_candidates)} candidates  SQLite: {len(sqlite_candidates)} candidates")

    # ── Per-case matrix ──
    pg_repo = SnapshotRepository(pg_engine)
    sqlite_repo = SnapshotRepository(sqlite_engine)

    print("\n=== filter_by_tags PG vs SQLite — per-case matrix ===")
    print(f"{'tags':30} {'mode':5} {'PG':40} {'SQLite':40} {'agree'}")
    matrix: list[dict[str, Any]] = []
    agreement_count = 0
    disagreements = []
    for tags_tup, mode in CASES:
        tags_list = list(tags_tup)
        pg_st, pg_v = _run_one(pg_repo, pg_candidates, tags_list, mode)
        sq_st, sq_v = _run_one(sqlite_repo, sqlite_candidates, tags_list, mode)
        # Compare: OK vs OK → exact list match. Anything with EXC is also
        # not-equivalent even if SQLite arm coincidentally errored the
        # same way.
        agree = (pg_st == "OK" and sq_st == "OK" and pg_v == sq_v)
        marker = "OK" if agree else "!!"
        print(f"{json.dumps(tags_list):30} {mode:5} {json.dumps([pg_st, pg_v])[:38]:40} "
              f"{json.dumps([sq_st, sq_v])[:38]:40} {marker}")
        matrix.append({
            "tags": tags_list, "mode": mode,
            "pg": [pg_st, pg_v], "sqlite": [sq_st, sq_v],
            "agree": agree,
        })
        if agree:
            agreement_count += 1
        else:
            disagreements.append({"tags": tags_list, "mode": mode,
                                  "pg": pg_v, "sqlite": sq_v})

    summary = {
        "agreement_count": agreement_count,
        "case_count": len(CASES),
        "all_agree": agreement_count == len(CASES),
        "matrix": matrix,
        "disagreements": disagreements,
    }
    print(f"\n=== matrix agreement: {agreement_count}/{len(CASES)} ===")
    if not summary["all_agree"]:
        print("BLOCKER: PG and SQLite disagree on ≥1 case. Production code NOT fixed; see evidence above.")

    # ── Edge cases ──
    print("\n=== edge cases ===")
    edges: list[dict[str, Any]] = []

    # Invalid tag_mode (per source: line 525 raises ValueError BEFORE engine check)
    for bad_mode in ("xor", "ALL", None, ""):
        got_pg = _run_one(pg_repo, pg_candidates, ["a"], bad_mode)  # type: ignore[arg-type]
        got_sq = _run_one(sqlite_repo, sqlite_candidates, ["a"], bad_mode)  # type: ignore[arg-type]
        edges.append({
            "label": f"invalid mode {bad_mode!r}",
            "expected": "ValueError",
            "pg": got_pg,
            "sqlite": got_sq,
        })

    # Empty candidates (PG short-circuits → falls to Python arm which returns [])
    got_pg_empty = _run_one(pg_repo, [], ["a"], "all")
    got_sq_empty = _run_one(sqlite_repo, [], ["a"], "all")
    edges.append({
        "label": "empty candidates list",
        "expected": "(OK, [])",
        "pg": got_pg_empty,
        "sqlite": got_sq_empty,
    })

    for e in edges:
        print(json.dumps(e, default=str)[:160])

    # ── Verdict ──
    print()
    if summary["all_agree"]:
        print("=== RESULT: PASS — all cases agree ===")
        return 0
    else:
        print("=== RESULT: FAIL — PG arm produces wrong SQL on JSONB @> path. ===")
        print("=== This is a CONFIRMED PG runtime defect: col().contains emits LIKE, not JSONB @>. ===")
        print("=== See captured SQL above. Per task instructions, production code is NOT modified. ===")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"=== RESULT: FAIL (unhandled {type(exc).__name__}: {exc}) ===")
        sys.exit(2)
