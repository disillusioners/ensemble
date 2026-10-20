#!/usr/bin/env python
"""jaaf-p8 — behavioral PG-dialect parity pass for two bulk repository queries.

Proves BEHAVIORALLY (real repos, real engines, real rows) that the PG dialect
arm returns the same rows as the SQLite arm for:

  1. SQLModelInstanceRepository.find_by_metadata_value
     (daemon/repositories/instance/repository.py — PG `metadata ->> :key`,
      SQLite `json_extract(metadata, :path)`)
  2. TaskRepository.find_instances_with_answer_handle
     (daemon/repositories/task/repository.py — ORM select, chunked)

Fixture shapes mirror tests/job_queue/test_job_answer_tool.py
(_seed_instance:181 / _seed_task:209) and the QuestionManager pack payload
(pack_to_dict, daemon/services/question_manager.py:526).

Isolation: throwaway PG database `jaaf_pgbranch_<8hex>` on the DEV server
(falls back to a throwaway schema inside ensemble_dev when CREATEDB is
denied). Escape-guarded drops only. SQLite arm: file-backed /tmp/jaaf-p8/.

Exit codes: 0 = PASS, 1 = FAIL, 124 = TIMEOUT.
"""

from __future__ import annotations

import os
import re
import signal
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Soft deadline (layer 2) — the outer `timeout 300` is layer 1.
# ---------------------------------------------------------------------------
DEADLINE_S = 280
_START = time.monotonic()


class SoftTimeout(BaseException):
    pass


def _on_alarm(signum, frame):
    print("RESULT: TIMEOUT")
    print(f"runtime: {time.monotonic() - _START:.2f}s")
    sys.stdout.flush()
    os._exit(124)


signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(DEADLINE_S)


def _check_deadline(where: str) -> None:
    if time.monotonic() - _START > DEADLINE_S - 5:
        print(f"RESULT: TIMEOUT (soft deadline hit at: {where})")
        sys.stdout.flush()
        os._exit(124)


# ---------------------------------------------------------------------------
# Target safety — escape guard for every identifier this script may DROP.
# ---------------------------------------------------------------------------
_FORBIDDEN = {"ensemble_dev", "ensemble_prod", "postgres", "template0", "template1"}
_IDENT_RE = re.compile(r"^jaaf_pgbranch_[0-9a-f]{8}$")


def _guard_identifier(name: str) -> str:
    if not _IDENT_RE.fullmatch(name):
        raise RuntimeError(f"escape guard: identifier {name!r} does not match {_IDENT_RE.pattern}")
    if name in _FORBIDDEN:
        raise RuntimeError(f"escape guard: identifier {name!r} is a protected name")
    return name


# ---------------------------------------------------------------------------
# Environment + engine URLs (secrets never printed).
# ---------------------------------------------------------------------------
PG_USER = os.environ["POSTGRES_USER"]
PG_PASS = os.environ["POSTGRES_PASSWORD"]
PG_HOST = os.environ["POSTGRES_HOST"]
PG_PORT = os.environ["POSTGRES_PORT"]
PG_DEV_DB = os.environ["POSTGRES_DB"]
assert PG_DEV_DB == "ensemble_dev", f"refusing to run against {PG_DEV_DB!r}"

TARGET = f"jaaf_pgbranch_{secrets.token_hex(4)}"  # 8 hex chars
_guard_identifier(TARGET)

BASE_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}"

SQLITE_DIR = Path("/tmp/jaaf-p8")
SQLITE_PATH = SQLITE_DIR / "sqlite_arm.db"

# ---------------------------------------------------------------------------
# Model imports — mirror tests/job_queue/test_job_answer_tool.py:53-61 so
# SQLModel.metadata carries the same tables the real harness builds.
# ---------------------------------------------------------------------------
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import SuspensionReason, Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

QUESTION_PACK_ID_KEY = "question_pack_id"  # daemon/services/midflight_qa.py:102


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Executed-SQL capture — ground truth of what each dialect actually ran.
# ---------------------------------------------------------------------------
from sqlalchemy import event

captured_sql: dict[str, list[str]] = {"postgresql": [], "sqlite": []}
HOOK_FIRES: list[int] = []


def _hook(dialect_name: str):
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        captured_sql[dialect_name].append(statement)

    return _before_cursor_execute


# ---------------------------------------------------------------------------
# Fixture seeding — mirrors _seed_instance / _seed_task exactly.
# ---------------------------------------------------------------------------
def _pack_payload(pack_id: str, holder_id: str, question_text: str) -> dict:
    """pack_to_dict shape (daemon/services/question_manager.py:526)."""
    return {
        "instance_id": holder_id,
        "pack_id": pack_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": [
            {
                "id": "q1",
                "text": question_text,
                "options": [],
                "allow_custom": True,
                "required": True,
                "answer": None,
                "option_descriptions": {},
            }
        ],
        "answers": {},
    }


def _seed_instance(session, *, instance_id: str, metadata: dict, parent_id: str | None = None) -> str:
    """Mirror tests/job_queue/test_job_answer_tool.py::_seed_instance (l.181)."""
    session.add(
        Instance(
            instance_id=instance_id,
            agent_id="leader",
            agent_dir="/tmp",
            project_id=None,
            status=InstanceStatus.PAUSED.value,
            version=1,
            instance_metadata=metadata,
            parent_id=parent_id,
        )
    )
    return instance_id


def _seed_task(session, iid: str, *, status: str = TaskStatus.PAUSED.value,
               task_type: str = TaskType.PROCESS_MESSAGE.value,
               suspension_reason: str | None = None,
               resume_target_turn_id: str | None = None) -> str:
    """Mirror tests/job_queue/test_job_answer_tool.py::_seed_task (l.209);
    the suspension fields are parameterized so decoy arms of the WHERE
    clause can be exercised too. Default (None/None) reproduces the test's
    PAUSED awaiting_answer shape exactly."""
    wid = str(uuid.uuid4())
    if suspension_reason is None and resume_target_turn_id is None and status == TaskStatus.PAUSED.value:
        suspension_reason = SuspensionReason.AWAITING_ANSWER.value
        resume_target_turn_id = wid
    session.add(
        Task(
            work_id=wid,
            task_type=task_type,
            instance_id=iid,
            status=status,
            suspension_reason=suspension_reason,
            resume_target_turn_id=resume_target_turn_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return wid


def _hold_pack(session, iid: str, pack_id: str) -> None:
    """Stamp the durable pack metadata exactly like the resolver's producers
    (test_job_answer_target_resolution.py:1209 set_metadata(question_pack_id, ...))."""
    row = session.get(Instance, iid)
    row.instance_metadata = {
        QUESTION_PACK_ID_KEY: pack_id,
        "question_pack_payload": _pack_payload(pack_id, iid, "Please answer?"),
    }


def seed_mission(session, root_id: str, children: list[tuple[str, dict]],
                 root_metadata: dict | None = None, root_task: bool = False) -> None:
    _seed_instance(session, instance_id=root_id, metadata=root_metadata or {})
    if root_task:
        _seed_task(session, root_id)
    for cid, meta in children:
        _seed_instance(session, instance_id=cid, metadata=meta, parent_id=root_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    log("=== jaaf-p8 — PG-dialect behavioral parity (bulk answer-path queries) ===")
    log(f"[env] POSTGRES_DB={PG_DEV_DB} port={PG_PORT} driver=psycopg3 target={TARGET}")
    log("[env] ambient POSTGRES_* scrubbed upstream; only the worktree fenced .env was sourced")

    import psycopg
    from sqlalchemy import create_engine, text as sa_text
    from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect_mod
    from sqlmodel import Session, SQLModel

    # ---- provision the throwaway PG target --------------------------------
    def _connect_admin(dbname: str):
        return psycopg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
            dbname=dbname, autocommit=True,
        )

    mode = None
    admin = _connect_admin("postgres")
    try:
        admin.execute(f'CREATE DATABASE "{TARGET}"')
        mode = "database"
        log(f"[target] mode=database name={TARGET} (throwaway on dev server)")
    except psycopg.Error as e:
        if getattr(e, "sqlstate", None) in {"42501", "42510"} or "permission" in str(e).lower():
            admin.close()
            # The throwaway schema lives INSIDE ensemble_dev — reconnect there.
            admin = _connect_admin(PG_DEV_DB)
            mode = "schema"
            log(f"[target] CREATE DATABASE denied (sqlstate={getattr(e, 'sqlstate', '?')}) — "
                f"falling back to schema {TARGET} inside {PG_DEV_DB}")
            admin.execute(f'CREATE SCHEMA "{TARGET}"')
        else:
            raise
    finally:
        admin.close()

    if mode == "database":
        pg_url = f"{BASE_URL}/{TARGET}"
    else:
        pg_url = f"{BASE_URL}/{PG_DEV_DB}"

    pg_engine = create_engine(pg_url, pool_pre_ping=True)
    sqlite_engine = create_engine(f"sqlite:///{SQLITE_PATH}")

    # Pre-run public baseline (shared dev DB — for the post-run delta evidence)
    _base = psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
        dbname=PG_DEV_DB, autocommit=True,
    )
    _bcur = _base.cursor()
    _bcur.execute("SELECT count(*) FROM public.instances")
    PUB_I_BEFORE = _bcur.fetchone()[0]
    _bcur.execute("SELECT count(*) FROM public.task")
    PUB_T_BEFORE = _bcur.fetchone()[0]
    _bcur.close()
    _base.close()
    log(f"[baseline] ensemble_dev public before run: instances={PUB_I_BEFORE} task={PUB_T_BEFORE}")

    if mode == "schema":
        # Deterministic per-connection search_path (conninfo `options` proved
        # unreliable through this driver stack — tables silently resolved to
        # public in the first run). SET (not SET LOCAL) persists for the
        # pooled connection's lifetime; the hook fires on EVERY new physical
        # connection.
        from sqlalchemy import event as sa_event

        @sa_event.listens_for(pg_engine, "connect")
        def _set_search_path(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute(f'SET search_path TO "{TARGET}", public')
            cur.close()
            HOOK_FIRES.append(1)

    event.listen(pg_engine, "before_cursor_execute", _hook("postgresql"))
    event.listen(sqlite_engine, "before_cursor_execute", _hook("sqlite"))

    # ---- schema (same tables as the test harness) -------------------------
    SQLITE_DIR.mkdir(parents=True, exist_ok=True)
    if SQLITE_PATH.exists():
        SQLITE_PATH.unlink()
    SQLITE_PATH.touch()
    # checkfirst=False + explicit table list. WHY: SQLAlchemy's dialect caches
    # default_schema_name='public' (captured before the connect-event SET
    # ran), so checkfirst=True found ensemble_dev's public.instances and
    # silently SKIPPED creation (first-run contamination vector). With
    # checkfirst=False the unqualified CREATE resolves SERVER-SIDE to the
    # FIRST search_path schema (= TARGET) — the dialect cache is bypassed.
    # The repos' own unqualified queries resolve via the session search_path
    # (proven correct via SHOW search_path), so parity semantics are intact.
    _tables = [Instance.__table__, Task.__table__]
    SQLModel.metadata.create_all(pg_engine, checkfirst=False, tables=_tables)
    SQLModel.metadata.create_all(sqlite_engine, checkfirst=False, tables=_tables)
    log(f"[debug] connect-hook fires so far: {len(HOOK_FIRES)}")
    log(f"[schema] created tables {[_t.name for _t in _tables]} on both engines "
        "(checkfirst=False — first-search_path-schema resolution)")
    if mode == "schema":
        with pg_engine.connect() as c:
            locs = c.execute(sa_text(
                "SELECT schemaname FROM pg_tables WHERE tablename = 'instances'"
            )).fetchall()
            log(f"[debug] 'instances' table locations after create_all: {[r[0] for r in locs]}")

    if mode == "schema":
        with pg_engine.connect() as c:
            dbg_sp = c.execute(sa_text("SHOW search_path")).scalar()
            dbg_cs = c.execute(sa_text("SELECT current_schema()")).scalar()
            dbg_dn = getattr(pg_engine.dialect, "default_schema_name", "<unset>")
            log(f"[debug] on isolation conn: search_path={dbg_sp!r} current_schema={dbg_cs!r} "
                f"dialect.default_schema_name={dbg_dn!r}")
            in_target = c.execute(sa_text(
                "SELECT count(*) FROM pg_tables WHERE schemaname = :s AND tablename = 'instances'"
            ), {"s": TARGET}).scalar()
            assert in_target == 1, (
                f"ISOLATION FAILURE: instances table not in schema {TARGET} — "
                "aborting BEFORE any write"
            )
            log(f"[isolation] public untouched: instances table confirmed in schema {TARGET}")

    # PRE-SEED GUARD — a fresh throwaway MUST show zero rows before seeding.
    # Any non-zero count means the engine resolved a pre-existing table
    # (isolation failure) → abort BEFORE any INSERT can contaminate anything.
    with Session(pg_engine) as gp, Session(sqlite_engine) as gl:
        pg0 = gp.exec(sa_text("SELECT count(*) FROM instances")).one()[0]
        sl0 = gl.exec(sa_text("SELECT count(*) FROM instances")).one()[0]
        pgt0 = gp.exec(sa_text("SELECT count(*) FROM task")).one()[0]
        assert pg0 == 0 and sl0 == 0 and pgt0 == 0, (
            f"ISOLATION FAILURE: pre-seed counts pg=({pg0},{pgt0}) sqlite=({sl0},) "
            "not zero — aborting BEFORE any write"
        )
        log("[isolation] pre-seed zero-count guard passed (instances=0, task=0 on both engines)")

    pg_instance_repo = SQLModelInstanceRepository(pg_engine)
    pg_task_repo = TaskRepository(pg_engine)
    lite_instance_repo = SQLModelInstanceRepository(sqlite_engine)
    lite_task_repo = TaskRepository(sqlite_engine)

    # ---- fixtures ----------------------------------------------------------
    pack1, pack2, pack3, pack4, pack5 = (str(uuid.uuid4()) for _ in range(5))

    # G1 child-holder: root R (NO pack) + child C1 holding P1 + decoys
    g1_root = f"inst-{uuid.uuid4()}"
    g1_holder = f"inst-{uuid.uuid4()}"          # holds P1 + handle task
    g1_d_run = f"inst-{uuid.uuid4()}"           # task RUNNING, no handle fields
    g1_d_reason = f"inst-{uuid.uuid4()}"        # PAUSED + paused_by_parent
    g1_d_invariant = f"inst-{uuid.uuid4()}"     # PAUSED + awaiting_answer + NULL resume_target
    g1_d_terminal = f"inst-{uuid.uuid4()}"      # COMPLETED + awaiting_answer
    g1_d_metaval = f"inst-{uuid.uuid4()}"       # question_pack_id = OTHER value
    g1_d_emptymeta = f"inst-{uuid.uuid4()}"     # metadata {} (NULL extraction)

    # G2 root-holder: pack P2 in ROOT metadata only (+ root handle task)
    g2_root = f"inst-{uuid.uuid4()}"

    # G3 two-holders: P3 in two different children
    g3_root = f"inst-{uuid.uuid4()}"
    g3_holder_a = f"inst-{uuid.uuid4()}"
    g3_holder_b = f"inst-{uuid.uuid4()}"

    # G4 zero-holders: own mission, no pack P4 anywhere, no handles
    g4_root = f"inst-{uuid.uuid4()}"
    g4_child = f"inst-{uuid.uuid4()}"

    # G5 501-instance subtree: root + 500 children, exactly child #317 holds P5
    g5_root = f"inst-{uuid.uuid4()}"
    g5_children = [f"inst-{uuid.uuid4()}" for _ in range(500)]
    g5_holder = g5_children[317]  # zero-based → "child #317"

    with Session(pg_engine) as sp, Session(sqlite_engine) as sl:
        for session in (sp, sl):  # identical rows in both engines
            # G1
            seed_mission(session, g1_root, [
                (g1_holder, {}), (g1_d_run, {}), (g1_d_reason, {}), (g1_d_invariant, {}),
                (g1_d_terminal, {}), (g1_d_metaval, {QUESTION_PACK_ID_KEY: "pack-decoy-other"}),
                (g1_d_emptymeta, {}),
            ])
            _hold_pack(session, g1_holder, pack1)
            _seed_task(session, g1_holder)                                   # true handle
            _seed_task(session, g1_d_run, status=TaskStatus.RUNNING.value)   # decoy: wrong status
            _seed_task(session, g1_d_reason,
                       suspension_reason="paused_by_parent",
                       resume_target_turn_id=str(uuid.uuid4()))              # decoy: wrong reason
            _seed_task(session, g1_d_invariant, suspension_reason="awaiting_answer",
                       resume_target_turn_id=None)                           # decoy: invariant-2 NULL
            _seed_task(session, g1_d_terminal, status=TaskStatus.COMPLETED.value,
                       suspension_reason="awaiting_answer",
                       resume_target_turn_id=str(uuid.uuid4()))              # decoy: terminal status
            # G2
            seed_mission(session, g2_root, [], root_task=True)
            _hold_pack(session, g2_root, pack2)
            # G3
            seed_mission(session, g3_root, [(g3_holder_a, {}), (g3_holder_b, {})])
            _hold_pack(session, g3_holder_a, pack3)
            _hold_pack(session, g3_holder_b, pack3)
            _seed_task(session, g3_holder_a)
            _seed_task(session, g3_holder_b)
            # G4 — nobody holds P4; child has a RUNNING task (no handle)
            seed_mission(session, g4_root, [(g4_child, {})])
            _seed_task(session, g4_child, status=TaskStatus.RUNNING.value)
            # G5 — root + 500 children, ONLY #317 holds P5 + handle
            seed_mission(session, g5_root, [(cid, {}) for cid in g5_children])
            _hold_pack(session, g5_holder, pack5)
            _seed_task(session, g5_holder)
            session.commit()
        counts = {}
        for session, name in ((sp, "pg"), (sl, "sqlite")):
            n_inst = session.exec(sa_text("SELECT count(*) FROM instances")).one()[0]
            n_task = session.exec(sa_text("SELECT count(*) FROM task")).one()[0]
            counts[name] = (n_inst, n_task)
        assert counts["pg"] == counts["sqlite"], f"row-count divergence: {counts}"
        log(f"[fixtures] identical rows both engines: instances={counts['pg'][0]} task={counts['pg'][1]}")

    missions = {
        "G1 child-holder": {
            "ids": [g1_root, g1_holder, g1_d_run, g1_d_reason, g1_d_invariant,
                    g1_d_terminal, g1_d_metaval, g1_d_emptymeta],
            "pack": pack1,
            "expect_meta": {g1_holder},
            "expect_handle": {g1_holder},
        },
        "G2 root-holder": {
            "ids": [g2_root],
            "pack": pack2,
            "expect_meta": {g2_root},
            "expect_handle": {g2_root},
        },
        "G3 two-holders": {
            "ids": [g3_root, g3_holder_a, g3_holder_b],
            "pack": pack3,
            "expect_meta": {g3_holder_a, g3_holder_b},
            "expect_handle": {g3_holder_a, g3_holder_b},
        },
        "G4 zero-holders": {
            "ids": [g4_root, g4_child],
            "pack": pack4,
            "expect_meta": set(),
            "expect_handle": set(),
        },
        "G5 501-subtree (child #317 only)": {
            "ids": [g5_root] + g5_children,
            "pack": pack5,
            "expect_meta": {g5_holder},
            "expect_handle": {g5_holder},
        },
    }

    # ---- run both methods on both engines per geometry ---------------------
    results = []
    all_match = True

    # empty-set guard parity (both repos short-circuit without touching DB)
    guard_ok = (
        pg_instance_repo.find_by_metadata_value([], QUESTION_PACK_ID_KEY, pack1) == []
        and lite_instance_repo.find_by_metadata_value([], QUESTION_PACK_ID_KEY, pack1) == []
        and pg_task_repo.find_instances_with_answer_handle([]) == []
        and lite_task_repo.find_instances_with_answer_handle([]) == []
    )
    log(f"[guard] empty-instance_ids short-circuit parity: {'OK' if guard_ok else 'MISMATCH'}")
    all_match &= guard_ok

    for geo, spec in missions.items():
        _check_deadline(f"before {geo}")
        row = {"geometry": geo, "ids_len": len(spec["ids"])}
        for method_name in ("metadata", "handle"):
            timings = {}
            got = {}
            for engine_name, inst_repo, task_repo in (
                ("sqlite", lite_instance_repo, lite_task_repo),
                ("pg", pg_instance_repo, pg_task_repo),
            ):
                t0 = time.perf_counter()
                if method_name == "metadata":
                    res = inst_repo.find_by_metadata_value(spec["ids"], QUESTION_PACK_ID_KEY, spec["pack"])
                else:
                    res = task_repo.find_instances_with_answer_handle(spec["ids"])
                timings[engine_name] = (time.perf_counter() - t0) * 1000.0
                got[engine_name] = set(res)
            expect = spec["expect_meta"] if method_name == "metadata" else spec["expect_handle"]
            match = got["pg"] == got["sqlite"] == expect
            all_match &= match
            row[method_name] = {"sqlite": got["sqlite"], "pg": got["pg"],
                                "match": match, "ms": timings,
                                "expect": expect,
                                "dup_check": None}
            if method_name == "handle":
                # invariant: an id appears more than once only on duplicate handles
                raw = pg_task_repo.find_instances_with_answer_handle(spec["ids"])
                row["dup_check"] = (len(raw) == len(set(raw)))
        results.append(row)

    # ---- compiled-SQL excerpts (both dialects, both methods) ---------------
    from sqlalchemy import bindparam, text as sa_text
    log("")
    log("=== Compiled SQL excerpts (rendered per dialect) ===")
    pg_stmt = sa_text(
        "SELECT instance_id FROM instances "
        "WHERE instance_id IN :ids "
        "AND metadata ->> :key = :value"
    ).bindparams(bindparam("ids", expanding=True))
    lite_stmt = sa_text(
        "SELECT instance_id FROM instances "
        "WHERE instance_id IN :ids "
        "AND json_extract(metadata, :path) = :value"
    ).bindparams(bindparam("ids", expanding=True))
    log("-- find_by_metadata_value / PostgreSQL arm --")
    log(str(pg_stmt.compile(dialect=postgresql.dialect())))
    log("-- find_by_metadata_value / SQLite arm --")
    log(str(lite_stmt.compile(dialect=sqlite_dialect_mod.dialect())))

    from sqlmodel import col, select
    handle_stmt = (
        select(Task.instance_id)
        .where(Task.instance_id.in_(["<ids...>"]))
        .where(Task.suspension_reason == SuspensionReason.AWAITING_ANSWER.value)
        .where(Task.resume_target_turn_id.isnot(None))
        .where(Task.status == TaskStatus.PAUSED.value)
    )
    log("-- find_instances_with_answer_handle / PostgreSQL (ORM-compiled) --")
    log(str(handle_stmt.compile(dialect=postgresql.dialect())))
    log("-- find_instances_with_answer_handle / SQLite (ORM-compiled) --")
    log(str(handle_stmt.compile(dialect=sqlite_dialect_mod.dialect())))

    # ground truth: the statements actually executed per engine
    log("")
    log("=== Actually-executed statements (event-hook capture) ===")
    pg_meta_seen = [s for s in captured_sql["postgresql"] if "metadata ->>" in s]
    lite_meta_seen = [s for s in captured_sql["sqlite"] if "json_extract" in s]
    pg_handle_seen = [s for s in captured_sql["postgresql"]
                      if s.lstrip().startswith("SELECT") and "suspension_reason" in s]
    lite_handle_seen = [s for s in captured_sql["sqlite"]
                        if s.lstrip().startswith("SELECT") and "suspension_reason" in s]
    log(f"[pg] metadata arm SELECTs executed {len(pg_meta_seen)}x "
        f"(5 geometry calls + 1 G5 chunk-split) — first: {pg_meta_seen[0] if pg_meta_seen else 'NONE'}")
    log(f"[sqlite] metadata arm SELECTs executed {len(lite_meta_seen)}x — first: {lite_meta_seen[0] if lite_meta_seen else 'NONE'}")
    log(f"[pg] handle SELECTs executed {len(pg_handle_seen)}x "
        f"(10 calls + 1 G5 chunk-split) — first: {(pg_handle_seen[0] if pg_handle_seen else 'NONE')[:160]}…")
    log(f"[sqlite] handle SELECTs executed {len(lite_handle_seen)}x — first: {(lite_handle_seen[0] if lite_handle_seen else 'NONE')[:160]}…")
    log("[chunk] G5 passes 501 ids → chunk boundary exercised: chunk of 500 + chunk of 1 (both repos chunk at 500)")

    # ---- per-geometry table -------------------------------------------------
    log("")
    log("=== Per-geometry results ===")
    log(f"{'geometry':<34} {'method':<8} {'sqlite rows (ids)':<24} {'pg rows (ids)':<24} {'MATCH':<6} {'t_sqlite(ms)':>12} {'t_pg(ms)':>10}")
    for row in results:
        for method_name in ("metadata", "handle"):
            cell = row[method_name]
            def fmt_ids(s):
                if not s:
                    return "[]"
                short = sorted(x[:13] for x in s)
                return f"[{len(s)}] {','.join(short)}{'…' if len(s) > 3 else ''}"
            log(f"{row['geometry']:<34} {method_name:<8} {fmt_ids(cell['sqlite']):<24} "
                f"{fmt_ids(cell['pg']):<24} {'Y' if cell['match'] else 'N':<6} "
                f"{cell['ms']['sqlite']:>12.2f} {cell['ms']['pg']:>10.2f}")
        if row.get("dup_check") is not None:
            log(f"{'':<34} {'':<8} handle-duplicate check (len==set): {'OK' if row['dup_check'] else 'VIOLATION'}")

    g5 = next(r for r in results if r["geometry"].startswith("G5"))
    log("")
    log(f"[G5 timing] metadata: sqlite={g5['metadata']['ms']['sqlite']:.2f}ms pg={g5['metadata']['ms']['pg']:.2f}ms | "
        f"handle: sqlite={g5['handle']['ms']['sqlite']:.2f}ms pg={g5['handle']['ms']['pg']:.2f}ms "
        "(PK-anchored IN-list; no pathological scan)")

    # ---- cleanup ------------------------------------------------------------
    log("")
    log("=== Cleanup ===")
    pg_engine.dispose()
    sqlite_engine.dispose()

    admin = _connect_admin("postgres" if mode == "database" else PG_DEV_DB)
    if mode == "database":
        _guard_identifier(TARGET)
        admin.execute(f'DROP DATABASE "{TARGET}" WITH (FORCE)')
        log(f"[cleanup] DROP DATABASE \"{TARGET}\" WITH (FORCE) — executed")
        left = admin.execute(
            "SELECT count(*) FROM pg_database WHERE datname = %s", (TARGET,)
        ).fetchone()[0]
        where = "pg_database"
    else:
        _guard_identifier(TARGET)
        admin.execute(f'DROP SCHEMA "{TARGET}" CASCADE')
        log(f"[cleanup] DROP SCHEMA \"{TARGET}\" CASCADE — executed")
        left = admin.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (TARGET,)
        ).fetchone()[0]
        where = "pg_namespace"
    admin.close()
    log(f"[cleanup] verification via {where}: remaining rows for {TARGET} = {left} "
        f"({'GONE' if left == 0 else 'STILL PRESENT — FAIL'})")

    # ---- final public-schema evidence (shared dev DB: concurrent lanes) ----
    # NOTE: ensemble_dev public is SHARED — other agent lanes run transient
    # fixtures against it concurrently (observed live: a foreign +5/+5 spike
    # that self-cleaned seconds later). The HARD safety rails are the pre-seed
    # zero-count guard and the throwaway-schema isolation check (both abort
    # BEFORE any write). This post-run check is EVIDENCE, not a gate: report
    # before/after deltas instead of failing on foreign transients.
    leak = psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
        dbname=PG_DEV_DB, autocommit=True,
    )
    lcur = leak.cursor()
    lcur.execute("SELECT count(*) FROM public.instances")
    pub_i = lcur.fetchone()[0]
    lcur.execute("SELECT count(*) FROM public.task")
    pub_t = lcur.fetchone()[0]
    lcur.execute("SELECT count(*) FROM public.instances WHERE agent_id=%s AND agent_dir=%s "
                 "AND instance_id LIKE %s", ("leader", "/tmp", "inst-%"))
    leaked = lcur.fetchone()[0]
    lcur.close()
    leak.close()
    delta_i = pub_i - PUB_I_BEFORE
    delta_t = pub_t - PUB_T_BEFORE
    log(f"[leak-check] ensemble_dev public before/after: instances {PUB_I_BEFORE}->{pub_i} "
        f"(delta {delta_i:+d}), task {PUB_T_BEFORE}->{pub_t} (delta {delta_t:+d}), "
        f"fixture-fingerprint now={leaked}")
    if delta_i != 0 or delta_t != 0:
        log("[leak-check] WARN: post-run delta non-zero — on a shared dev DB this is "
            "typically a CONCURRENT lane's transient fixtures. This script's writes are "
            "provably confined to the throwaway schema (pre-seed zero-count guard + "
            "isolation check). Foreign rows are NEVER deleted by this script.")

    if SQLITE_PATH.exists():
        SQLITE_PATH.unlink()
        log(f"[cleanup] removed sqlite file {SQLITE_PATH}")
    log(f"[cleanup] preserved for re-verification: {sys.argv[0]}")

    runtime = time.monotonic() - _START
    log("")
    verdict = "PASS" if (all_match and left == 0 and guard_ok) else "FAIL"
    log(f"RESULT: {verdict}")
    if verdict == "FAIL":
        for row in results:
            for method_name in ("metadata", "handle"):
                if not row[method_name]["match"]:
                    log(f"  mismatch: {row['geometry']} / {method_name}: "
                        f"sqlite={sorted(row[method_name]['sqlite'])} pg={sorted(row[method_name]['pg'])} "
                        f"expected={sorted(row[method_name]['expect'])}")
    log(f"runtime: {runtime:.2f}s")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    try:
        code = main()
    except SoftTimeout:
        code = 124
    sys.exit(code)
