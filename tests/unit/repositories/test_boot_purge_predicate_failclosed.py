"""Boot-purge predicate fail-closed proofs (anomalies b0a02363 / 20c13d1f).

Three hermetic, file-backed-SQLite proofs for the deferred env-poison purge:

  P1 — a probe evaluation failure MUST delete NOTHING and complain loudly
       (the base code swallowed the error and degraded to the broader
       running/paused-only predicate).
  P2 — the normal purge deletes EXACTLY the narrow-predicate doomed set:
       FP1 / Event / F-1 arm-3 rows survive; receipt-less backlog and
       orphan messages are deleted.
  P3 — the 42fd1984 shape is impossible: a preserved task keeps its
       message; a PENDING task whose message is actually deleted is
       failed explicitly as orphaned with a clear reason.

No live PostgreSQL is contacted. No HOME-derived paths are resolved.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool
from types import SimpleNamespace
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue import repository as mq_repo_mod
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.timestamps import now_utc_naive


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real file-backed SQLite (NullPool) with the full SQLModel schema."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/boot_purge_failclosed.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def mq_repo(engine: Engine) -> SQLModelMessageQueueRepository:
    return SQLModelMessageQueueRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


def _seed_instance(engine: Engine, iid: str, status: str = "running") -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def _seed_unit(
    engine: Engine,
    *,
    label: str,
    status: str = TaskStatus.PENDING.value,
    receipt: bool = False,
    job: str | None = None,
    arm3: bool = False,
    instance_status: str = "running",
) -> tuple[int, str, str]:
    """Seed Instance + Task + Message (+ receipt / JobItem)."""
    iid = f"inst-{label}"
    mid = f"mq-{label}"
    wid = f"work-{label}"
    _seed_instance(engine, iid, instance_status)
    with Session(engine) as s:
        s.add(
            Task(
                work_id=wid,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                message_id=mid,
                status=status,
                auto_continued_at=now_utc_naive() if arm3 else None,
                created_at=now_utc_naive(),
            )
        )
        s.add(
            MessageQueue(
                message_id=mid,
                instance_id=iid,
                content=label,
                type="human",
                source="api",
                status="ready",
                enqueued_at=now_utc_naive(),
            )
        )
        if receipt:
            s.add(
                Event(
                    instance_id=iid,
                    message_id=mid,
                    kind="message_received",
                    data="{}",
                    created_at=now_utc_naive(),
                )
            )
        if job is not None:
            s.add(
                JobItem(
                    job_id=wid,
                    agent_id="ari",
                    agent_dir="/agents/ari",
                    message=label,
                    source="api",
                    admission_state=job,
                    job_type="message",
                    instance_id=iid,
                    created_at=now_utc_naive().isoformat(),
                )
            )
        s.commit()
        tid = s.exec(select(Task.id).where(Task.message_id == mid)).first()
        assert tid is not None
        return int(tid), mid, wid


def _seed_orphan_message(engine: Engine, label: str) -> str:
    mid = f"mq-{label}"
    iid = f"inst-{label}"
    _seed_instance(engine, iid)
    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id=mid,
                instance_id=iid,
                content=label,
                type="human",
                source="api",
                status="ready",
                enqueued_at=now_utc_naive(),
            )
        )
        s.commit()
    return mid


def _messages(engine: Engine) -> set[str]:
    with Session(engine) as s:
        return set(s.exec(select(MessageQueue.message_id)).all())


def _task_row(engine: Engine, task_id: int) -> Task | None:
    with Session(engine) as s:
        return s.get(Task, task_id)


def _task_ids(engine: Engine) -> set[int]:
    with Session(engine) as s:
        return set(s.exec(select(Task.id)).all())


class _RecordingConn:
    """Fake DBAPI-adjacent connection for compile-level psycopg pins.

    Records each executed statement compiled under the production
    dialect. NEVER connects to PostgreSQL.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, stmt, params=None):
        self.seen.append(
            (
                str(stmt.compile(dialect=postgresql.psycopg.dialect())),
                dict(params or {}),
            )
        )

        class _Result:
            def first(self):
                return (1,)

            def all(self):
                return []

        return _Result()


class _FakePgEngine:
    """Engine stand-in whose dialect claims postgresql (compile-level
    only — no driver, no connection, no network)."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.conn = _RecordingConn()

    def connect(self):
        return self.conn



# ---------------------------------------------------------------------------
# P1 — parse/evaluation error: DELETE NOTHING, complain loudly
# ---------------------------------------------------------------------------


def test_p1_probe_error_deletes_nothing_and_complains_loudly(
    engine: Engine,
    mq_repo: SQLModelMessageQueueRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Force the table-existence probe to raise the production error.

    Base behaviour (RED): the exception is swallowed, the arms are dropped,
    and ``delete_doomed_ids`` deletes the row. Fixed behaviour (GREEN): a
    loud FAIL-CLOSED error is raised before any DELETE and the row survives.
    """
    _, doomed_mid, _ = _seed_unit(engine, label="p1-doomed")
    _, preserved_mid, _ = _seed_unit(
        engine, label="p1-event", receipt=True
    )

    real_connect = engine.connect
    state = {"raise": True}

    def _flaky_connect(*args, **kwargs):
        if state["raise"]:
            # ONE-SHOT: only the probe's connection fails. Subsequent
            # Session() connections must work so the base code's
            # swallow-and-degrade path can actually proceed to the DELETE
            # (otherwise the RED would be an incidental Session error).
            state["raise"] = False
            raise ProgrammingError(
                'syntax error at or near ":"',
                {},
                Exception('syntax error at or near ":"'),
            )
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(engine, "connect", _flaky_connect)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception, match="FAIL-CLOSED|predicate|probe"):
            mq_repo.delete_doomed_ids(
                [doomed_mid, preserved_mid], preserve_in_flight=True
            )
    assert "FAIL-CLOSED" in caplog.text, (
        "the probe failure must be loud (ERROR log), not a silent warning"
    )
    assert _messages(engine) == {doomed_mid, preserved_mid}, (
        "a DELETE whose predicate could not be evaluated must delete NOTHING"
    )

    # The CAPTURE_PHASE must fail closed too — it must not hand a broadened
    # doomed list to the deferred DELETE. Re-arm the one-shot probe error.
    state["raise"] = True
    with pytest.raises(Exception, match="FAIL-CLOSED|predicate|probe"):
        mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    assert _messages(engine) == {doomed_mid, preserved_mid}


# ---------------------------------------------------------------------------
# P1b — the probe compiles bound params on the production dialect
# ---------------------------------------------------------------------------


def test_probe_uses_psycopg_bound_params_not_literal_colon() -> None:
    """Hermetic (NO PostgreSQL connection): the probe statement must
    compile to pyformat ``%(tname)s`` for the production dialect.

    The base code used ``exec_driver_sql`` with a raw ``:tname`` string,
    which psycopg sends literally → ``syntax error at or near ":"`` on
    every boot. This pins the fix's cross-dialect mechanism.
    """
    fake_engine = _FakePgEngine()
    fp1_arm, event_arm = mq_repo_mod._receipt_preserve_arms_sql(fake_engine)
    recorded = fake_engine.conn.seen
    assert len(recorded) == 2
    for sql, params in recorded:
        assert "%(tname)s" in sql, f"probe did not compile for psycopg: {sql}"
        assert ":tname" not in sql, f"raw colon placeholder leaked: {sql}"
        assert params["tname"] in {"job_queue_items", "event"}
    assert "jqi.admission_state" in fp1_arm and "task.work_id" in fp1_arm
    assert "message_received" in event_arm and "task.message_id" in event_arm


# ---------------------------------------------------------------------------
# P2 — normal boot: exactly the narrow-predicate doomed set is deleted
# ---------------------------------------------------------------------------


def test_p2_normal_boot_deletes_exact_narrow_doomed_set(
    engine: Engine,
    mq_repo: SQLModelMessageQueueRepository,
    task_repo: TaskRepository,
) -> None:
    """The narrow predicate's three preserve arms hold on a normal boot.

    The FP1 / Event / arm-3 rows are the ones the degraded predicate
    (running/paused only) wrongly deleted under b0a02363.
    """
    _, fp1_mid, _ = _seed_unit(engine, label="p2-fp1", job=AdmissionState.ACTIVE.value)
    _, receipt_mid, _ = _seed_unit(engine, label="p2-receipt", receipt=True)
    _, arm3_mid, _ = _seed_unit(
        engine,
        label="p2-arm3",
        status=TaskStatus.COMPLETED.value,
        arm3=True,
        instance_status="running",
    )
    _, doomed_pending_mid, _ = _seed_unit(engine, label="p2-doomed-pending")
    _, doomed_terminal_mid, _ = _seed_unit(
        engine, label="p2-doomed-terminal", status=TaskStatus.COMPLETED.value
    )
    orphan_mid = _seed_orphan_message(engine, "p2-orphan")

    doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    task_repo.clear_all(preserve_in_flight=True)
    deleted = mq_repo.delete_doomed_ids(doomed, preserve_in_flight=True)

    assert deleted == 3, f"expected exactly 3 doomed rows deleted, got {deleted}"
    assert _messages(engine) == {fp1_mid, receipt_mid, arm3_mid}, (
        "the FP1 / Event / arm-3 preserve arms must keep their messages — "
        "the narrower predicate must actually work on a normal boot"
    )
    assert {doomed_pending_mid, doomed_terminal_mid, orphan_mid}.issubset(set(doomed))


# ---------------------------------------------------------------------------
# P3 — the 42fd1984 shape is impossible
# ---------------------------------------------------------------------------


def test_p3_orphan_pending_task_is_failed_explicitly(
    engine: Engine,
    mq_repo: SQLModelMessageQueueRepository,
) -> None:
    """A PENDING task whose message is actually deleted is failed with a
    clear reason; a preserved PENDING task keeps its message and stays
    claimable. No PENDING task is left against a vanished message.
    """
    orphan_task_id, orphan_mid, _ = _seed_unit(engine, label="p3-orphan")
    preserved_task_id, preserved_mid, _ = _seed_unit(
        engine, label="p3-preserved", receipt=True
    )

    deleted = mq_repo.delete_doomed_ids(
        [orphan_mid, preserved_mid], preserve_in_flight=True
    )
    assert deleted == 1

    failed = _task_row(engine, orphan_task_id)
    assert failed is not None and failed.status == TaskStatus.FAILED.value, (
        "a task whose backing message was deleted must be failed explicitly"
    )
    assert failed.error and "orphan" in failed.error.lower(), (
        f"the failure reason must be explicit, got {failed.error!r}"
    )
    preserved = _task_row(engine, preserved_task_id)
    assert preserved is not None and preserved.status == TaskStatus.PENDING.value
    assert preserved_mid in _messages(engine)

    with Session(engine) as s:
        still_pending = s.exec(
            select(Task.message_id).where(Task.status == TaskStatus.PENDING.value)
        ).all()
        live_messages = set(s.exec(select(MessageQueue.message_id)).all())
    assert all(mid in live_messages for mid in still_pending), (
        "no PENDING task may point at a vanished message_queue row"
    )


# ---------------------------------------------------------------------------
# F1 / T1 — task-side fail-closed at BOTH seams, boot call site survives
# ---------------------------------------------------------------------------


def test_t1_task_side_predicate_failure_is_fail_closed_and_boot_survives(
    engine: Engine,
    task_repo: TaskRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Force the SHARED predicate to fail on the TASK side; assert the
    repository seam raises fail-closed (zero rows deleted, loud ERROR)
    AND the boot-path caller survives (F1 regression pin).

    RED on base, two independent ways:
      * base's ``TaskRepository.clear_all`` has no probe, so the
        one-shot connect failure surfaces as a RAW ProgrammingError
        (not ``BootWipePredicateError``) — the exception-type
        assertion below fails;
      * base has no ``daemon.manager._boot_task_wipe_fail_closed``,
        so the boot call site is UNGUARDED — the reviewer verified
        manually that this kills the boot.
    """
    task_id, _mid, _wid = _seed_unit(engine, label="t1-task")
    tasks_before = _task_ids(engine)

    real_connect = engine.connect
    state = {"raise": True}

    def _flaky_connect(*args, **kwargs):
        if state["raise"]:
            state["raise"] = False
            raise ProgrammingError(
                'syntax error at or near ":"',
                {},
                Exception('syntax error at or near ":"'),
            )
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(engine, "connect", _flaky_connect)

    # --- seam 1: the repository must RAISE fail-closed ---
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception) as excinfo:
            task_repo.clear_all(preserve_in_flight=True)
    assert excinfo.type.__name__ == "BootWipePredicateError", (
        f"expected the fail-closed BootWipePredicateError, got "
        f"{excinfo.type.__module__}.{excinfo.type.__name__} (a raw error "
        f"escaping the wipe seam is the boot-abort / fail-open defect)"
    )
    assert "FAIL-CLOSED" in caplog.text
    assert _task_ids(engine) == tasks_before, "no task row may be deleted"

    # --- seam 2: the BOOT CALL SITE must survive the raise ---
    import daemon.manager as manager_mod

    boot_guard = getattr(manager_mod, "_boot_task_wipe_fail_closed", None)
    assert boot_guard is not None, (
        "F1 regression: the task-side boot call site is UNGUARDED — "
        "a predicate failure must not abort daemon startup"
    )
    state["raise"] = True  # re-arm the one-shot probe failure
    with caplog.at_level(logging.ERROR):
        deleted = boot_guard(task_repo, boot_epoch=None)
    assert deleted == 0
    assert "FAIL-CLOSED" in caplog.text, (
        "the boot-call-site guard must complain loudly (ERROR), not silently"
    )
    assert "auto_continued_at" in caplog.text, (
        "the ERROR log must name which predicate/arm set failed"
    )
    assert _task_ids(engine) == tasks_before, (
        "the guarded boot path must delete NOTHING on a predicate failure"
    )


# ---------------------------------------------------------------------------
# F3 / T2 — cross-side agreement over a full cycle
# ---------------------------------------------------------------------------


def test_t2_task_and_message_wipes_agree_over_full_cycle(
    engine: Engine,
    mq_repo: SQLModelMessageQueueRepository,
    task_repo: TaskRepository,
) -> None:
    """capture → task wipe → message DELETE over a mixed fixture; the
    task-side deleted set and the message-side doomed set must AGREE
    (one shared keep-set), and no PENDING task may point at a vanished
    message.

    NOTE (stated plainly): this test is GREEN on the unfixed base —
    SQLite accepted the raw ``:tname`` probe, so the base already
    agreed on SQLite. Its value is pinning the AGREEMENT itself (the
    psycopg revert is detected by test_f4 and by P1, not here).
    """
    survivors = {}
    for label, kwargs in (
        ("t2-fp1", {"job": AdmissionState.ACTIVE.value}),
        ("t2-receipt", {"receipt": True}),
        (
            "t2-arm3",
            {
                "status": TaskStatus.COMPLETED.value,
                "arm3": True,
                "instance_status": "running",
            },
        ),
    ):
        _tid, mid, _wid = _seed_unit(engine, label=label, **kwargs)
        survivors[mid] = _tid
    doomed: dict[str, int | None] = {}
    _tid, mid, _wid = _seed_unit(engine, label="t2-doomed-pending")
    doomed[mid] = _tid
    _tid, mid, _wid = _seed_unit(
        engine, label="t2-doomed-terminal", status=TaskStatus.COMPLETED.value
    )
    doomed[mid] = _tid
    orphan_mid = _seed_orphan_message(engine, "t2-orphan")
    doomed[orphan_mid] = None

    doomed_ids = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    task_count = task_repo.clear_all(preserve_in_flight=True)
    deleted = mq_repo.delete_doomed_ids(doomed_ids, preserve_in_flight=True)

    assert deleted == len(doomed) == 3
    assert task_count == 2  # the two doomed tasks; the orphan has none
    assert set(doomed_ids) == set(doomed), (
        "the message-side doomed set must be exactly the narrow set"
    )
    for mid, tid in {**{m: t for m, t in survivors.items()}, **doomed}.items():
        task_alive = tid is not None and _task_row(engine, tid) is not None
        message_alive = mid in _messages(engine)
        assert task_alive == message_alive, (
            f"task/message disagreement for {mid}: task_alive={task_alive} "
            f"message_alive={message_alive} (the shared keep-set must hold "
            f"on BOTH sides of the wipe)"
        )
    with Session(engine) as s:
        still_pending = s.exec(
            select(Task.message_id).where(Task.status == TaskStatus.PENDING.value)
        ).all()
        live_messages = set(s.exec(select(MessageQueue.message_id)).all())
    assert all(m in live_messages for m in still_pending)


# ---------------------------------------------------------------------------
# F4 — psycopg compile-level pin of the FULL doomed predicate
# ---------------------------------------------------------------------------


def test_f4_full_doomed_predicate_compiles_on_psycopg_dialect() -> None:
    """The FULL shared doomed predicate (all arms) must compile under
    the production psycopg dialect with NO literal-colon leak.

    RED on base: ``boot_wipe_preserve_predicate_sql`` does not exist
    there (AttributeError) — this is the assertion that detects a
    psycopg-side revert, which P2 (SQLite-only) cannot.
    """
    pred = mq_repo_mod.boot_wipe_preserve_predicate_sql(
        _FakePgEngine(), arm3_active=True
    )
    assert ":tname" not in pred and ":id_" not in pred
    # All four arms present:
    assert "task.status IN ('running', 'paused')" in pred
    assert "jqi.admission_state" in pred  # FP1
    assert "message_received" in pred  # Event receipt
    assert "auto_continued_at" in pred  # F-1 arm 3
    compiled = str(
        text(f"SELECT 1 FROM task WHERE NOT {pred}").compile(
            dialect=postgresql.psycopg.dialect()
        )
    )
    assert "NOT (" in compiled and compiled.count("(") == compiled.count(")")

    # The chunked DELETE shape the boot DELETE phase actually runs must
    # also compile — bound params become pyformat, no raw colons.
    del_sql = (
        "DELETE FROM message_queue WHERE message_id IN (:id_0) "
        f"AND message_id NOT IN (SELECT message_id FROM task WHERE {pred})"
    )
    compiled_del = str(text(del_sql).compile(dialect=postgresql.psycopg.dialect()))
    assert "%(id_0)s" in compiled_del, compiled_del
    assert ":id_0" not in compiled_del, compiled_del


# ---------------------------------------------------------------------------
# F2 — schema-qualified, visible column probe
# ---------------------------------------------------------------------------


def test_f2_column_probe_full_schema_true_and_minimal_schema_loud_false(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """The orphan belt must ARM on the real schema and SKIP LOUDLY on a
    legacy minimal schema (WARNING names schema + columns found)."""
    full = engine_probe_full(tmp_path)
    try:
        assert mq_repo_mod._orphan_error_column_available(full)
    finally:
        full.dispose()

    minimal = create_engine(
        f"sqlite:///{tmp_path}/minimal.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    with minimal.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE task (task_id VARCHAR PRIMARY KEY, "
            "instance_id VARCHAR, status VARCHAR, auto_continued_at TIMESTAMP, "
            "message_id VARCHAR)"
        )
    with caplog.at_level(logging.WARNING):
        armed = mq_repo_mod._orphan_error_column_available(minimal)
    minimal.dispose()
    assert armed is False
    assert "DISARMED" in caplog.text, (
        "a legacy schema that disarms the orphan belt must be visible"
    )
    assert "main" in caplog.text and "error" in caplog.text


def engine_probe_full(tmp_path) -> Engine:
    """Full-schema engine (same shape as the module fixture)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/probe_full.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def test_f2_pg_column_probe_sql_is_schema_qualified() -> None:
    """The PostgreSQL column probe must qualify by schema
    (``table_schema = current_schema()``) so another schema's ``task``
    table cannot arm the orphan UPDATE.

    RED on base: ``_orphan_column_probe_sql`` does not exist there.
    """
    probe, params, schema_label = mq_repo_mod._orphan_column_probe_sql("postgresql")
    compiled = str(probe.compile(dialect=postgresql.psycopg.dialect()))
    assert "table_schema = current_schema()" in compiled, compiled
    assert "%(tname)s" in compiled and ":tname" not in compiled, compiled
    assert params == {"tname": "task"}
    assert schema_label == "current_schema()"
    # The SQLite path stays covered by PRAGMA (information_schema does
    # not exist there) — exercised by the full/minimal test above.
    sqlite_probe, sqlite_params, sqlite_label = (
        mq_repo_mod._orphan_column_probe_sql("sqlite")
    )
    assert "PRAGMA table_info(task)" in str(sqlite_probe)
    assert sqlite_params == {} and sqlite_label == "main"
